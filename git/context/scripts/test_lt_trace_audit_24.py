"""Driver rules of Sprint 24 on synthetic rows (no artifacts, no model): chain steps, tables, the loop's tool text."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lt_trace_audit_24 as s24  # noqa: E402

ARMS = {"v-lt": "LT", "v-o": "O", "v-s": "S", "v-r0": "R0"}


def trace(cell, q, oid="v-lt", cond="s1-follow"):
    return {"cell_id": cell, "tier": "t2", "variant_id": oid, "question_id": q, "condition": cond, "replicate": 1,
            "identity_ok": True, "terminal": "answer", "replay": "verified", "final_context": "verified",
            "zero_follow": False, "compaction_retained_reads": []}


def follow(cell, q, doc, call, *, cls="added", first=True, label="supported", span="none", ranges=((1, 5),),
           status="accepted", oid="v-lt"):
    return {"cell_id": cell, "tier": "t2", "variant_id": oid, "question_id": q, "condition": "s1-follow",
            "replicate": 1, "call": call, "action": "follow", "status": status, "doc_id": doc, "edge_class": cls,
            "first_page_text_exposure": first, "label": label, "span_exposure_this_call": span,
            "returned_ranges": [list(r) for r in ranges], "menu_edges": 4, "menu_added_edges": 1,
            "menu_unknown_edges": 0}


def cite(cell, q, doc, a, b):
    return {"cell_id": cell, "tier": "t2", "variant_id": "v-lt", "question_id": q, "condition": "s1-follow",
            "replicate": 1, "kind": "answer", "doc_id": doc, "start_line": a, "end_line": b,
            "first_exposing_follow_edge_classes": ["added"], "first_exposure": "follow_only"}


class ChainTests(unittest.TestCase):
    def setUp(self):
        self.traces = [trace("c1", "q1"), trace("c2", "q2"), trace("c3", "q3"), trace("c4", "q4", cond="s1-no-follow")]
        self.actions = [
            follow("c1", "q1", "d1", 3, span="full"),                    # full chain, cited lines from this follow
            follow("c1", "q1", "d1", 5, first=False),                    # repeat visit: stops at step 2
            follow("c2", "q2", "d2", 2, label="never_judged"),           # unjudged destination stops at step 3
            follow("c3", "q3", "d3", 2, cls="mapped_baseline"),          # mapped edge: step 1 only
            follow("c3", "q3", "d4", 4, status="rejected"),              # failed follow is not counted
        ]
        self.citations = [cite("c1", "q1", "d1", 4, 8)]

    def test_chain_steps_are_separate_and_success_qualified(self):
        out = s24.chain(self.traces, self.actions, self.citations, ARMS)["LT"]
        self.assertEqual(out["traces"], {"0_planned_traces": 3, "1_any_accepted_follow": 3, "2_added_edge_follow": 2,
                                         "3_first_page_text_exposure": 2, "4_destination_supported": 1,
                                         "5_span_in_returned_lines": 1, "6_destination_cited": 1,
                                         "7_cited_lines_from_this_follow": 1})
        self.assertEqual(out["unique_question_destination_pairs"]["2_added_edge_follow"], 2)

    def test_citation_outside_returned_lines_stops_at_step_six(self):
        out = s24.chain(self.traces, self.actions, [cite("c1", "q1", "d1", 20, 22)], ARMS)["LT"]
        self.assertEqual(out["traces"].get("6_destination_cited"), 1)
        self.assertNotIn("7_cited_lines_from_this_follow", out["traces"])

    def test_comparability_view_restricts_questions(self):
        out = s24.chain(self.traces, self.actions, self.citations, ARMS, keep={"q2"})["LT"]
        self.assertEqual(out["traces"]["0_planned_traces"], 1)
        self.assertNotIn("4_destination_supported", out["traces"])

    def test_tables_count_rejections_and_opportunity(self):
        t = s24.coverage_tables(self.traces, self.actions, [], ARMS)["t2/LT/s1-follow"]
        self.assertEqual(t["actions"], {"follow:accepted": 4, "follow:rejected": 1})
        self.assertEqual(t["follow_accepted"]["edge_class=added"], 3)
        self.assertEqual(t["opportunity_summary"]["mean_menu_added_share"], 0.25)
        self.assertEqual(t["opportunity_summary"]["added_share_of_accepted_follows"], 0.75)
        self.assertEqual(t["unique_q_dest"]["pairs"], 3)

    def test_tool_schema_is_read_from_the_pinned_loop_without_import(self):
        self.assertTrue(s24.tool_schema_functions().startswith("Call exactly one of the provided functions"))
        self.assertNotIn("complex_task_agent_loop", sys.modules)


if __name__ == "__main__":
    unittest.main()

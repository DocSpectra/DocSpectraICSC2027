"""Offline tests for Sprint 15b (toy traces only; no containers, no network, no model)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wc_ragas_15b as wr  # noqa: E402
from retrieval_arena.errors import ValidationError  # noqa: E402


def lines(start, texts):
    return [{"line": start + i, "text": t, "truncated_line": False} for i, t in enumerate(texts)]


TRACE = {
    "schema_version": "docspectra.icsc.complex_task_agent_trace.v2", "terminal": "answer",
    "answer": {"text": "Enable it, then check.", "citations": [{"doc_id": "a"}]},
    "steps": [
        {"call": 1, "action": {"action": "search", "query": "x"}, "result": {"hits": []}},
        {"call": 2, "action": {"action": "read", "doc_id": "a"}, "result": {"doc_id": "a", "lines": lines(1, ["A1", "A2", "A3"])}},
        # Overlapping re-read: only lines 4-5 are new.
        {"call": 3, "action": {"action": "read", "doc_id": "a"}, "result": {"doc_id": "a", "lines": lines(2, ["A2", "A3", "A4", "A5"])}},
        {"call": 4, "action": {"action": "follow", "source_doc_id": "a", "doc_id": "b"},
         "result": {"doc_id": "b", "lines": lines(10, ["B10", "B11"])}},
        {"call": 5, "action": {"action": "read", "doc_id": "c"}, "result": {"error": "not admitted"}},
        # Re-read with a gap: lines 1 and 3 seen, 7 new -> one new run.
        {"call": 6, "action": {"action": "read", "doc_id": "a"}, "result": {"doc_id": "a", "lines": lines(7, ["A7"])}},
    ],
}

QUESTION = {"question_id": "q1", "question": "How do I enable and check it?", "core_elements": [
    {"element_id": "C2", "acceptance": "Says to check status.", "requirement": "r", "page_id": "a"},
    {"element_id": "C1", "acceptance": "Says to enable the feature.", "requirement": "r", "page_id": "a"}]}


def unit(**over):
    ctx = wr.trace_contexts(TRACE)
    base = {"qid": "q1", "question": QUESTION["question"], "reference": wr.tier1_reference(QUESTION),
            "condition": "trace-1", "run": "toy", "text": wr.answer_text(TRACE), "contexts": ctx,
            "source_sha": "0" * 64, "record_id": "trace-1.json"}
    base.update(over)
    return base


PROVENANCE = {"authoring_method": "toy", "reviewer_id": "toy", "frozen_at": "2026-10-11", "independent_of_predictions": True}


class TraceInputs(unittest.TestCase):
    def test_contexts_in_order_deduplicated_and_split(self):
        ctx = wr.trace_contexts(TRACE)
        self.assertEqual([(c["document_id"], c["chunk_id"].split("#")[0], c["text"]) for c in ctx],
                         [("a", "L1-3", "A1\nA2\nA3"), ("a", "L4-5", "A4\nA5"), ("b", "L10-11", "B10\nB11"), ("a", "L7-7", "A7")])
        for c in ctx:
            self.assertEqual(c["sha256"], wr.rs.text_hash(c["text"]))

    def test_answer_text_and_missing_answer(self):
        self.assertEqual(wr.answer_text(TRACE), "Enable it, then check.")
        self.assertIsNone(wr.answer_text({**TRACE, "terminal": "invalid_action_limit"}))

    def test_tier1_reference_in_element_order(self):
        self.assertEqual(wr.tier1_reference(QUESTION), "Says to enable the feature.\nSays to check status.")

    def test_assemble_validates_against_ra(self):
        batch, rubrics = wr.assemble([unit(), unit(condition="trace-2")], PROVENANCE)
        self.assertEqual(rubrics["schema_version"], "retrieval_arena.ragas_rubrics.v2")
        self.assertEqual(rubrics["questions"][0]["elements"], [])
        self.assertEqual(len(batch["corpus"]["chunks"]), 4)  # shared chunks once
        cfg = wr.config(wr.SETS["real-tier1"])
        wr.rs.validate_pairing({q["question_id"]: q for q in rubrics["questions"]}, cfg)
        # Context-stage text is not used; answer rows carry the agent answer unchanged.
        self.assertEqual(batch["predictions"][0]["text"], "Enable it, then check.")

    def test_assemble_refuses_conflicting_reference(self):
        with self.assertRaises(AssertionError):
            wr.assemble([unit(), unit(condition="trace-2", reference="other")], PROVENANCE)

    def test_missing_reference_refused_by_ra(self):
        batch, rubrics = wr.assemble([unit(reference=None)], PROVENANCE)
        with self.assertRaises(ValidationError):
            wr.rs.validate_pairing({q["question_id"]: q for q in rubrics["questions"]}, wr.config(wr.SETS["real-tier1"]))

    def test_configs_are_published_defaults(self):
        cfg = wr.config(wr.SETS["controls-response"])
        self.assertEqual(cfg["factual_correctness"], {"mode": "f1", "beta": 1.0, "atomicity": "low", "coverage": "low"})
        self.assertFalse(cfg["context_recall"])
        self.assertIsNone(wr.config(wr.SETS["controls-reference"])["factual_correctness"])
        self.assertLessEqual(cfg["max_input_tokens"] + cfg["max_output_tokens"] + 1024, 40960)


class Controls(unittest.TestCase):
    def test_support_windows_pick_matching_lines_within_cap(self):
        pages = [("p1", ["intro", "noise", "create docker account", "more", "x", "y", "z", "unrelated"]),
                 ("p2", ["docker account settings", "tail"])]
        ctx = wr.support_windows("Create a Docker account in settings.", pages, cap=10_000)
        self.assertEqual([(c["document_id"], c["chunk_id"].split("#")[0]) for c in ctx],
                         [("p1", "L1-5"), ("p2", "L1-2")])
        small = wr.support_windows("Create a Docker account in settings.", pages, cap=30)
        self.assertLessEqual(sum(len(c["text"].encode()) + 1 for c in small), 30)

    def test_separation_and_agreement_count_failures(self):
        values = {("a", "positive"): ("ok", 1.0), ("a", "negative"): ("ok", 0.0),
                  ("b", "positive"): ("ok", 0.5), ("b", "negative"): ("ok", 0.5),
                  ("c", "positive"): ("parse_error", None), ("c", "negative"): ("ok", 0.0)}
        self.assertEqual(wr.separation(values), {"pairs": 3, "positive_above_negative": 1, "share": 1 / 3})
        other = dict(values)
        other[("a", "positive")] = ("ok", 0.85)
        agree = wr.agreement(values, other)
        self.assertEqual((agree["units"], agree["both_scored"], agree["within_0_1"]), (6, 5, 4))
        self.assertEqual(wr.parse_rate(values)["rate"], 5 / 6)


if __name__ == "__main__":
    unittest.main()

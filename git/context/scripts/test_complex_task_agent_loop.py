"""Focused contract checks for the documentation-only agent loop."""

import importlib.util
import json
import shutil
import unittest
import uuid
from pathlib import Path


SPEC = importlib.util.spec_from_file_location("complex_task_agent_loop", Path(__file__).with_name("complex_task_agent_loop.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ScriptedModel:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.histories = []

    def complete(self, messages, allowed_actions):
        self.histories.append(list(messages))
        return next(self.actions), {"model": "scripted"}


class AgentLoopTest(unittest.TestCase):
    def setUp(self):
        workspace_artifacts = Path(__file__).resolve().parents[3] / "artifacts"
        root = workspace_artifacts / ("agent-loop-test-" + uuid.uuid4().hex)
        root.mkdir()
        self.root = root
        (root / "corpus").mkdir()
        (root / "corpus" / "a.md").write_text("# Pump\nCheck the pressure.\n", encoding="utf-8")
        (root / "corpus" / "b.md").write_text("# Valve\nOpen the bypass.\n", encoding="utf-8")
        (root / "graph_edges.csv").write_text("source,target\na,b\n", encoding="utf-8")
        self.docs = MODULE.Documentation(root)

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_link_trace_keeps_prior_evidence_and_cites_observed_lines(self):
        model = ScriptedModel([
            {"action": "search", "query": "Pump"},
            {"action": "read", "doc_id": "a", "start_line": 1, "line_count": 2},
            {"action": "follow", "source_doc_id": "a", "doc_id": "b", "start_line": 1, "line_count": 2},
            {"action": "answer", "text": "Check pressure, then open bypass.",
             "citations": [{"doc_id": "a", "start_line": 2, "end_line": 2},
                           {"doc_id": "b", "start_line": 2, "end_line": 2}]},
        ])
        trace = MODULE.AgentLoop(self.docs, model, "Prompt", follow_links=True).run("q", "Pump issue")
        self.assertEqual(trace["terminal"], "answer")
        self.assertEqual(trace["usage"]["reads"], 2)
        self.assertEqual(trace["steps"][2]["result"]["route"], "follow")
        self.assertTrue(any("Check the pressure" in m["content"] for m in model.histories[-1]))
        self.assertTrue(any("Open the bypass" in m["content"] for m in model.histories[-1]))

    def test_unread_citation_rejected(self):
        model = ScriptedModel([
            {"action": "search", "query": "Pump"},
            {"action": "read", "doc_id": "a", "start_line": 1, "line_count": 1},
            {"action": "answer", "text": "Claim", "citations": [{"doc_id": "b", "start_line": 2, "end_line": 2}]},
        ])
        trace = MODULE.AgentLoop(self.docs, model, "Prompt", follow_links=False, call_budget=3).run("q", "Pump issue")
        self.assertEqual(trace["terminal"], "model_call_budget_exhausted")
        self.assertEqual(trace["steps"][-1]["result"]["error"], "citation-not-observed")

    def test_disabled_follow_refused(self):
        model = ScriptedModel([
            {"action": "search", "query": "Pump"},
            {"action": "read", "doc_id": "a", "start_line": 1, "line_count": 1},
            {"action": "follow", "source_doc_id": "a", "doc_id": "b", "start_line": 1, "line_count": 1},
        ])
        trace = MODULE.AgentLoop(self.docs, model, "Prompt", follow_links=False, call_budget=3).run("q", "Pump issue")
        self.assertEqual(trace["steps"][-1]["result"]["error"], "action-not-allowed")

    def test_zero_hit_then_recover(self):
        model = ScriptedModel([
            {"action": "search", "query": "absent"},
            {"action": "search", "query": "Pump"},
            {"action": "read", "doc_id": "a", "start_line": 1, "line_count": 2},
            {"action": "answer", "text": "Check pressure.",
             "citations": [{"doc_id": "a", "start_line": 2, "end_line": 2}]},
        ])
        trace = MODULE.AgentLoop(self.docs, model, "Prompt", follow_links=False).run("q", "Pump")
        self.assertEqual(trace["terminal"], "answer")
        self.assertEqual(trace["steps"][0]["result"]["hits"], [])

    def test_invalid_range_and_budget_terminal(self):
        model = ScriptedModel([
            {"action": "search", "query": "Pump"},
            {"action": "read", "doc_id": "a", "start_line": 1, "line_count": 100},
        ])
        trace = MODULE.AgentLoop(self.docs, model, "Prompt", follow_links=False, call_budget=2).run("q", "Pump")
        self.assertEqual(trace["terminal"], "model_call_budget_exhausted")
        self.assertEqual(trace["steps"][-1]["result"]["error"], "invalid-read-range")

    def test_model_error_and_history_budget_are_terminal(self):
        class FailingModel:
            def complete(self, messages, allowed):
                raise RuntimeError('agent-model-response-incomplete-or-wrong-identity:{"done_reason": "length"}')
        trace = MODULE.AgentLoop(self.docs, FailingModel(), "Prompt", follow_links=False).run("q", "Pump")
        self.assertEqual(trace["terminal"], "model_length")
        trace = MODULE.AgentLoop(self.docs, ScriptedModel([]), "Prompt", follow_links=False,
                                 max_history_bytes=1).run("q", "Pump")
        self.assertEqual(trace["terminal"], "history_budget_exceeded")

    def test_compacted_evidence_is_identical_for_native_and_function_history(self):
        long_line = "Pressure reading remains stable. " * 36
        (self.root / "corpus" / "a.md").write_text("# Pump\n" + long_line + "\n", encoding="utf-8")
        docs = MODULE.Documentation(self.root)
        actions = [{"action": "search", "query": "Pump"},
                   {"action": "read", "doc_id": "a", "start_line": 2, "line_count": 1},
                   {"action": "read", "doc_id": "a", "start_line": 2, "line_count": 1},
                   {"action": "answer", "text": "Pressure is stable.",
                    "citations": [{"doc_id": "a", "start_line": 2, "end_line": 2}]}]

        class FunctionModel(ScriptedModel):
            uses_functions = True

            def complete(self, messages, allowed_actions):
                self.histories.append(list(messages))
                return next(self.actions), {"model": "scripted", "call_id": f"call_{len(self.histories)}"}

        snapshots = []
        for model in (ScriptedModel(actions), FunctionModel(actions)):
            events = []
            trace = MODULE.AgentLoop(docs, model, "Prompt", follow_links=False,
                                     max_history_bytes=3500, checkpoint=events.append).run("q", "Pump issue")
            self.assertEqual(trace["terminal"], "answer")
            compacted = [m["content"] for m in model.histories[-1]
                         if m["content"].startswith("Session snapshot: ")]
            self.assertEqual(len(compacted), 1)
            self.assertLessEqual(len(MODULE.canonical(model.histories[-1])), 3500)
            snapshot = json.loads(compacted[0].removeprefix("Session snapshot: "))
            self.assertEqual(MODULE.latest_observation(model.histories[-1]), snapshot["observation"])
            self.assertEqual(snapshot["evidence"][0]["lines"][0]["text"], long_line)
            self.assertEqual(len(snapshot["evidence"][0]["lines"]), 1)
            self.assertTrue(any(e["event"] == "history_compaction" for e in events))
            snapshots.append(snapshot)
        self.assertEqual([a["result"] for a in snapshots[0]["actions"]],
                         [a["result"] for a in snapshots[1]["actions"]])
        self.assertEqual([a["call_id"] for a in snapshots[1]["actions"]],
                         ["call_1", "call_2", "call_3"])

    def test_masked_key_fragment_is_not_saved_in_trace(self):
        class FailingModel:
            def complete(self, messages, allowed):
                raise RuntimeError("Incorrect API key provided: sk-proj-********abcd. invalid_api_key")
        trace = MODULE.AgentLoop(self.docs, FailingModel(), "Prompt", follow_links=False).run("q", "Pump")
        self.assertNotIn("sk-proj-", str(trace))
        self.assertIn("[REDACTED_API_KEY]", trace["steps"][0]["result"]["error"])

    def test_token_budget_evicts_old_lines_but_keeps_cited_notes_and_allows_reread(self):
        class CountedModel(ScriptedModel):
            max_input_tokens = 8000

            def count_input_tokens(self, messages, allowed_actions):
                snapshots = [m["content"] for m in messages if m["content"].startswith("Session snapshot: ")]
                if snapshots:
                    if len(messages) > 3:
                        return 3000 + 1000 * len(messages)
                    snapshot = json.loads(snapshots[-1].removeprefix("Session snapshot: "))
                    return 3000 + 3000 * len(snapshot["evidence"])
                return 1000 * len(messages)

        citation = {"doc_id": "a", "start_line": 2, "end_line": 2}
        model = CountedModel([
            {"action": "search", "query": "Pump Valve"},
            {"action": "read", "doc_id": "a", "start_line": 1, "line_count": 2},
            {"action": "note", "text": "Check the pressure.", "citations": [citation]},
            {"action": "read", "doc_id": "b", "start_line": 1, "line_count": 2},
            {"action": "answer", "text": "Check the pressure.", "citations": [citation]},
            {"action": "read", "doc_id": "a", "start_line": 2, "line_count": 1},
            {"action": "answer", "text": "Check the pressure.", "citations": [citation]},
        ])
        trace = MODULE.AgentLoop(self.docs, model, "Prompt", follow_links=False,
                                 call_budget=7).run("q", "Pump and Valve")
        self.assertEqual(trace["terminal"], "answer")
        self.assertEqual(trace["usage"]["notes"], 1)
        self.assertEqual(trace["steps"][4]["result"]["error"], "citation-not-observed")
        self.assertEqual(trace["history_compaction_policy"], "working_evidence_v1")
        snapshot = next(json.loads(m["content"].removeprefix("Session snapshot: "))
                        for m in model.histories[4] if m["content"].startswith("Session snapshot: "))
        self.assertEqual(snapshot["observation"]["notes"][0]["text"], "Check the pressure.")
        self.assertEqual(set(snapshot["observation"]["read_ranges"]), {"b"})

    def test_search_and_read_budget_exhaustion(self):
        model = ScriptedModel([
            {"action": "search", "query": "Pump"},
            {"action": "search", "query": "Pump"},
            {"action": "read", "doc_id": "a", "start_line": 1, "line_count": 1},
            {"action": "read", "doc_id": "a", "start_line": 2, "line_count": 1},
            {"action": "search", "query": "Pump"},
        ])
        trace = MODULE.AgentLoop(self.docs, model, "Prompt", follow_links=False,
                                 search_budget=2, read_budget=2, call_budget=5).run("q", "Pump")
        self.assertEqual(trace["terminal"], "model_call_budget_exhausted")
        self.assertEqual(trace["steps"][-1]["result"]["error"], "action-not-allowed")


if __name__ == "__main__":
    unittest.main()

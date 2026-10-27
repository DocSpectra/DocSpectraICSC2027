"""Sprint 11f configurable scorer call and run gate, with a fake Ollama transport (no model calls)."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import answer_quality_scorer as aqs  # noqa: E402
import answer_quality_scorer_config as aqsc  # noqa: E402
import scoring_pilot_11f as p11f  # noqa: E402
from test_answer_quality_scorer import fixture, reply  # noqa: E402

CONFIG = aqsc.ScorerConfig(aqs.MODEL, aqs.EXPECTED_DIGEST, True,
                           {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "seed": 11, "num_ctx": 32768,
                            "num_predict": 16384})


class FakeThinkingOllama:
    """Each reply is (content, thinking, done_reason); thinking None omits the field."""

    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls = []

    def digest(self, model):
        return aqs.EXPECTED_DIGEST

    def chat(self, body):
        self.calls.append(body)
        content, thinking, done = self.replies.pop(0) if self.replies else (reply(), "I weigh the notes.", "stop")
        message = {"role": "assistant", "content": content if isinstance(content, str) else json.dumps(content)}
        if thinking is not None:
            message["thinking"] = thinking
        return json.dumps({"message": message, "prompt_eval_count": 100, "eval_count": 900, "total_duration": 5,
                           "done_reason": done}).encode()


class Tmp(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root)

    def write(self, seed=7, notes_seed=None, config=CONFIG):
        answers, questions = fixture()
        packets, key = aqs.build_packets(answers, questions, seed, notes_seed)
        return aqsc.write_packets(self.root, packets, key, seed, config, notes_seed)

    def records(self):
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.root.glob("calls/*/attempt-01.json"))]


class ConfigCallTest(Tmp):
    def test_think_and_options_reach_the_request(self):
        receipt = self.write()
        self.assertTrue(receipt["think"])
        self.assertEqual(receipt["options"], CONFIG.options)
        fake = FakeThinkingOllama()
        aqsc.run(self.root, CONFIG, fake, log=lambda _: None)
        for body in fake.calls:
            self.assertIs(body["think"], True)
            self.assertEqual(body["options"], CONFIG.options)
            self.assertEqual(body["model"], aqs.MODEL)
            self.assertEqual(list(body["format"]["properties"]), list(aqs.FIELD_ORDER))

    def test_trace_saved_and_hashed(self):
        self.write()
        aqsc.run(self.root, CONFIG, FakeThinkingOllama(), log=lambda _: None)
        for rec in self.records():
            trace = self.root / "calls" / rec["packet_id"] / "attempt-01.thinking.txt"
            self.assertEqual(trace.read_text(encoding="utf-8"), "I weigh the notes.")
            self.assertEqual(rec["thinking_sha256"], aqs.sha(trace))
            self.assertTrue(rec["thinking_present"])
            self.assertEqual(rec["thinking_chars"], len("I weigh the notes."))
            self.assertTrue(rec["valid"])
        s = aqsc.thinking_stats(self.root)
        self.assertEqual((s["attempts"], s["thinking_present"], s["done_reason"]), (3, 3, {"stop": 3}))

    def test_missing_thinking_tolerated_and_recorded(self):
        self.write()
        aqsc.run(self.root, CONFIG, FakeThinkingOllama([(reply(), None, "stop")]), limit=1, log=lambda _: None)
        rec = self.records()[0]
        self.assertTrue(rec["valid"])
        self.assertFalse(rec["thinking_present"])
        self.assertIsNone(rec["thinking_sha256"])
        self.assertEqual(aqsc.thinking_stats(self.root)["thinking_missing"], [rec["packet_id"]])

    def test_done_reason_length_is_a_failure_then_retry(self):
        self.write()
        fake = FakeThinkingOllama([(reply(), "loop loop", "length"), (reply(), "t", "stop"), (reply(), "t", "stop"),
                                   (reply(), "t", "stop")])
        res = aqsc.run(self.root, CONFIG, fake, log=lambda _: None)
        self.assertEqual([r["status"] for r in res], ["failed", "valid", "valid", "valid"])
        failed = [r for r in self.records() if not r["valid"]][0]
        self.assertEqual(failed["validation_error"], "ValueError:done_reason:length")
        self.assertTrue((self.root / "calls" / failed["packet_id"] / "attempt-01.thinking.txt").is_file())
        s = aqs.summarize(self.root, write=False)
        self.assertEqual((s["usage"]["first_attempt_valid"], s["usage"]["max_attempts"]), (2, 2))

    def test_config_drift_refused(self):
        self.write()
        other = aqsc.ScorerConfig(aqs.MODEL, aqs.EXPECTED_DIGEST, True, {**CONFIG.options, "temperature": 0})
        with self.assertRaisesRegex(RuntimeError, "scorer-config-drift"):
            aqsc.run(self.root, other, FakeThinkingOllama(), log=lambda _: None)

    def test_packets_blind_and_identical_to_11e_layout(self):
        """Same seed: same labels, order, packet bytes and manifest as the 11e writer."""
        self.write(seed=20261015, notes_seed=None)
        other = Path(tempfile.mkdtemp())
        try:
            answers, questions = fixture()
            packets, key = aqs.build_packets(answers, questions, 20261015)
            aqs.write_packets(other, packets, key, 20261015, None)
            self.assertEqual(aqs.sha(self.root / "packet-manifest.json"), aqs.sha(other / "packet-manifest.json"))
            self.assertEqual(aqs.sha(self.root / "host-private/answer-key.json"),
                             aqs.sha(other / "host-private/answer-key.json"))
        finally:
            shutil.rmtree(other)
        forbidden = {a["answer_id"] for a in answers} | {a["arm"] for a in answers}
        for p in self.root.glob("packets/*.json"):
            aqs.check_blind(json.loads(p.read_text(encoding="utf-8")), forbidden)


class GateTest(Tmp):
    def summary(self, replies):
        self.write()
        aqsc.run(self.root, CONFIG, FakeThinkingOllama([(r, "t", "stop") for r in replies]), log=lambda _: None)
        return aqs.summarize(self.root, write=False)

    def test_gate_stops_after_failed_spread(self):
        s = self.summary([reply(), reply(), reply((5, 4, 5, 5, 5))])  # 2/3 all-5 > 0.50
        g = {"spread_A": p11f.spread(s)}
        self.assertEqual(g["spread_A"]["status"], "fail")
        self.assertEqual(p11f.may_run("A", g), (True, None))
        for run in ("A2", "B"):
            ok, why = p11f.may_run(run, g)
            self.assertFalse(ok)
            self.assertIn("skipped-by-gate", why)

    def test_gate_passes_and_requires_evaluation(self):
        s = self.summary([reply((4, 5, 5, 5, 5)), reply((5, 3, 5, 5, 5)), reply()])  # 1/3 all-5
        g = {"spread_A": p11f.spread(s)}
        self.assertEqual(g["spread_A"]["status"], "pass")
        self.assertEqual(p11f.may_run("B", g), (True, None))
        self.assertFalse(p11f.may_run("A2", None)[0])

    def test_unavailable_is_not_all_5(self):
        s = self.summary([reply(), "bad", reply(), "bad"])
        self.assertEqual(p11f.spread(s)["all_5"], 2)
        self.assertEqual(p11f.spread(s)["answers"], 3)

    def test_run_seeds(self):
        self.assertEqual(p11f.config("A").options["seed"], p11f.config("B").options["seed"])
        self.assertNotEqual(p11f.config("A").options["seed"], p11f.config("A2").options["seed"])
        self.assertEqual(p11f.SEED, 20261015)
        self.assertTrue(all(p11f.config(r).think for r in p11f.RUNS))


if __name__ == "__main__":
    unittest.main()

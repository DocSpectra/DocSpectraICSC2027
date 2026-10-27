"""Sprint 11h Luna scorer call and pilot stages, with a fake OpenAI client (no API calls)."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import answer_quality_scorer as aqs  # noqa: E402
import answer_quality_scorer_luna as aql  # noqa: E402
import restructuring_pilot_11b as p11b  # noqa: E402
import rubric_quote_match as rqm  # noqa: E402
import scoring_pilot_11e as p11e  # noqa: E402
import scoring_pilot_11h as p11h  # noqa: E402
from test_answer_quality_scorer import fixture, reply  # noqa: E402

CONFIG = aql.LunaConfig(authorization="test-label")
ANSWER_NO = re.compile(r"answer (\d+)")


class _Resp:
    def __init__(self, data):
        self.data = data

    def model_dump(self):
        return self.data


class FakeClient:
    """`responses.create`; overrides are (content or None, status, refusal) consumed per call."""

    def __init__(self, overrides=None, scores=None):
        self.overrides = list(overrides or [])
        self.scores = scores or (lambda prompt: (5, 5, 5, 5, 5))
        self.bodies = []
        self.responses = SimpleNamespace(create=self.create)

    def create(self, **body):
        self.bodies.append(body)
        prompt = body["input"][0]["content"]
        content, status, refusal = json.dumps(reply(self.scores(prompt))), "completed", False
        if self.overrides:
            o = self.overrides.pop(0)
            content = o[0] if o[0] is not None else content
            status, refusal = o[1], o[2]
        part = {"type": "refusal", "refusal": "no"} if refusal else {"type": "output_text", "text": content}
        return _Resp({"id": f"resp_{len(self.bodies)}", "model": "gpt-6-luna-2026-09-01", "status": status,
                      "service_tier": "default",
                      "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
                      "output": [{"type": "reasoning", "summary": []},
                                 {"type": "message", "content": [part]}],
                      "usage": {"input_tokens": 1500, "input_tokens_details": {"cached_tokens": 0},
                                "output_tokens": 600, "output_tokens_details": {"reasoning_tokens": 250}}})


class Tmp(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root)

    def write(self, seed=7, notes_seed=None, config=CONFIG, root=None):
        answers, questions = fixture()
        packets, key = aqs.build_packets(answers, questions, seed, notes_seed)
        return aql.write_packets(root or self.root, packets, key, seed, config, notes_seed)

    def transport(self, client, config=CONFIG):
        return aql.LunaTransport(config, self.root / "ledger.jsonl", client)

    def records(self):
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.root.glob("calls/*/attempt-*.json"))
                if p.stem.removeprefix("attempt-").isdigit()]


class LunaCallTest(Tmp):
    def test_request_body(self):
        receipt = self.write()
        self.assertEqual(receipt["model"], "gpt-6-luna")
        fake = FakeClient()
        aql.run(self.root, self.transport(fake), log=lambda _: None)
        self.assertEqual(len(fake.bodies), 3)
        for body in fake.bodies:
            self.assertEqual(body["model"], "gpt-6-luna")
            fmt = body["text"]["format"]
            self.assertEqual((fmt["type"], fmt["strict"]), ("json_schema", True))
            self.assertEqual(fmt["schema"], json.loads(aqs.SCHEMA.read_text(encoding="utf-8")))  # schema v2 as is
            self.assertEqual(body["reasoning"], {"effort": "medium"})
            self.assertEqual((body["max_output_tokens"], body["store"], body["service_tier"]), (4000, False, "default"))
            self.assertNotIn("<<QUESTION>>", body["input"][0]["content"][:50])
        rec = self.records()[0]
        self.assertTrue(rec["valid"] and rec["field_order_ok"])
        self.assertEqual((rec["model_reported"], rec["response_status"]), ("gpt-6-luna-2026-09-01", "completed"))
        self.assertEqual(rec["usage"]["reasoning_tokens"], 250)
        self.assertAlmostEqual(rec["cost_reported_usd"], 1500 * 0.10e-6 + 600 * 0.50e-6)
        self.assertAlmostEqual(rec["cost_reserved_usd"], aql.reservation(CONFIG))

    def test_ledger_reserves_first_and_stops_at_the_limit(self):
        tight = aql.LunaConfig(authorization="test-label", limit_usd=0.007)  # two reservations of 0.003 fit
        self.write(config=tight)
        fake = FakeClient()
        with self.assertRaisesRegex(RuntimeError, "estimated-charge-stop"):
            aql.run(self.root, self.transport(fake, tight), log=lambda _: None)
        self.assertEqual(len(fake.bodies), 2)
        self.assertEqual(len(self.records()), 2)  # the stopped call leaves no attempt
        events = [json.loads(x)["event"] for x in (self.root / "ledger.jsonl").read_text().splitlines()]
        self.assertEqual(events, ["reserved_before_request", "reported_usage"] * 2)
        # a new transport on the same ledger still sees the reservations
        with self.assertRaisesRegex(RuntimeError, "estimated-charge-stop"):
            aql.run(self.root, self.transport(FakeClient(), tight), log=lambda _: None)

    def test_api_failure_keeps_its_reservation(self):
        self.write()

        class Boom(FakeClient):
            def create(self, **body):
                raise ConnectionError("down")
        boom = Boom()
        aql.run(self.root, self.transport(boom), limit=1, retry_pass=False, log=lambda _: None)
        rec = self.records()[0]
        self.assertFalse(rec["valid"])
        self.assertIn("ConnectionError", rec["validation_error"])
        events = [json.loads(x)["event"] for x in (self.root / "ledger.jsonl").read_text().splitlines()]
        self.assertEqual(events, ["reserved_before_request", "failed_possible_billable"])

    def test_incomplete_refusal_and_invalid_are_failures_then_one_retry(self):
        self.write()
        fake = FakeClient([(None, "incomplete", False), (None, "completed", True),
                           (json.dumps(reply((6, 5, 5, 5, 5))), "completed", False)])
        res = aql.run(self.root, self.transport(fake), log=lambda _: None)
        self.assertEqual([r["status"] for r in res], ["failed"] * 3 + ["valid"] * 3)
        errors = sorted(r["validation_error"] for r in self.records() if not r["valid"])
        self.assertTrue(any("status:incomplete:max_output_tokens" in e for e in errors))
        self.assertTrue(any("refusal" in e for e in errors))
        self.assertTrue(any("ValidationError" in e for e in errors))
        s = aqs.summarize(self.root, write=False)
        self.assertEqual((s["usage"]["first_attempt_valid"], s["usage"]["max_attempts"], s["usage"]["packets_valid"]),
                         (0, 2, 3))

    def test_summarize_reads_the_layout(self):
        self.write()
        aql.run(self.root, self.transport(FakeClient(scores=lambda p: (4, 3, 5, 5, 5))), log=lambda _: None)
        s = aqs.summarize(self.root, write=False)
        self.assertEqual(len(s["scores"]), 3)
        self.assertTrue(all(v == {"correctness": 4, "fit": 3, "completeness": 5, "caveats": 5, "clarity": 5}
                            for v in s["scores"].values()))
        self.assertEqual((s["usage"]["prompt_eval_count"], s["usage"]["eval_count"]), (4500, 1800))
        self.assertEqual(aql.cost_stats(self.root)["models_reported"], ["gpt-6-luna-2026-09-01"])

    def test_packets_identical_to_11e_writer(self):
        self.write(seed=20261015, notes_seed=20261015)
        other = Path(tempfile.mkdtemp())
        try:
            answers, questions = fixture()
            packets, key = aqs.build_packets(answers, questions, 20261015, 20261015)
            aqs.write_packets(other, packets, key, 20261015, 20261015)
            for f in ("packet-manifest.json", "host-private/answer-key.json"):
                self.assertEqual(aqs.sha(self.root / f), aqs.sha(other / f))
        finally:
            shutil.rmtree(other)

    def test_config_drift_and_missing_label_refused(self):
        self.write()
        other = aql.LunaConfig(authorization="test-label", reasoning_effort="high")
        with self.assertRaisesRegex(RuntimeError, "scorer-config-drift"):
            aql.run(self.root, self.transport(FakeClient(), other), log=lambda _: None)
        with self.assertRaisesRegex(RuntimeError, "authorization-label-required"):
            aql.LunaTransport(aql.LunaConfig(), self.root / "l.jsonl", FakeClient())


class CriteriaTest(unittest.TestCase):
    def analysis(self, o=5, s=5, within=0.95, same=0.85, all5=0.3, first=180, max_att=1):
        usage = {"packets": 60, "first_attempt_valid": first // 3, "max_attempts": max_att}
        return {"by_pair_type": {"A": {"O-S": {"base_wins": o, "other_wins": s, "ties": 20 - o - s,
                                               "unavailable": 0}}},
                "stability_A_vs_B": {"within_1_share": within, "same_ranking_share": same},
                "nondeterminism_A_vs_A2": {"within_1_share": 1, "identical_score_vectors": 50, "same_ranking_share": 1},
                "spread_A": {"all_5_share": all5}, "usage": {"A": usage, "A2": usage, "B": usage}}

    def test_pass_and_failures(self):
        crit, d = p11h.evaluate_criteria(self.analysis(o=7, s=3, within=0.90, same=0.80, all5=0.50, first=171))
        self.assertEqual(d, "qualified_for_triage")
        self.assertEqual(crit["human_agreement"]["status"], "not_collected")
        self.assertEqual(p11h.evaluate_criteria(self.analysis(all5=0.51))[1], "needs_revision")
        self.assertEqual(p11h.evaluate_criteria(self.analysis(first=168))[1], "needs_revision")
        self.assertEqual(p11h.evaluate_criteria(self.analysis(max_att=3))[1], "needs_revision")
        self.assertEqual(p11h.evaluate_criteria(self.analysis(o=8, s=3, same=0.79))[1], "not_usable")
        self.assertEqual(p11h.evaluate_criteria(self.analysis(o=8, s=3))[1], "needs_revision")


class StagesTest(Tmp):
    """smoke -> freeze -> score A, A2, B -> analyse -> evaluate on temporary roots."""

    def setUp(self):
        super().setUp()
        t = self.root
        answers, questions = fixture()
        self.answers = answers
        files = []
        (t / "answers").mkdir()
        for a in answers:
            f = t / "answers" / a["answer_id"]
            f.write_text(json.dumps({"answer": a["text"]}), encoding="utf-8")
            files.append(f)
        digest = hashlib.sha256("".join(f"{p.name} {aqs.sha(p)}\n" for p in files).encode()).hexdigest()
        for name in ("questions.json", "rubric.json"):
            (t / name).write_text("{}", encoding="utf-8")
        (t / "judge").mkdir()
        (t / "judge/aliases.json").write_text("{}", encoding="utf-8")
        plan_11e = {"inputs": {"answers_dir": "x", "answer_files": 3, "answers_scored": 3, "answer_files_digest": digest},
                    "ranking": {"rule": aqs.RANK_RULE}, "seeds": {"notes_rule": "per question"},
                    "criteria": {"sham_balance": {}, "stability": {}, "spread": {}, "schema_validity": {},
                                 "human_agreement": {}}, "reports": ["r"]}
        (t / "plan11e.json").write_text(json.dumps(plan_11e), encoding="utf-8")
        out11e = t / "out11e"
        for r, ns in (("A", None), ("A2", None), ("B", p11e.NOTES_SEED)):
            packets, key = aqs.build_packets(answers, questions, p11e.SEED, ns)
            aqs.write_packets(out11e / f"run-{r}", packets, key, p11e.SEED, ns)

        def scores(prompt):
            i = int(ANSWER_NO.search(prompt).group(1))
            return [(5, 5, 5, 5, 5), (5, 4, 5, 5, 5), (4, 5, 5, 5, 5)][i]
        self.fake = FakeClient(scores=scores)
        evidence = t / "evidence"
        patches = {
            (p11h, "OUT"): t / "out", (p11h, "SMOKE"): t / "smoke", (p11h, "EVIDENCE"): evidence,
            (p11h, "PLAN"): evidence / "plan.json", (p11h, "LEDGER"): t / "out/cost-ledger.jsonl",
            (p11h, "OUT_11E"): out11e, (p11h, "PLAN_11E"): t / "plan11e.json", (p11h, "CLIENT"): self.fake,
            (p11h, "ANALYSIS_11E"): t / "none.json", (p11h, "ANALYSIS_11F"): t / "none.json",
            (p11e, "inputs"): lambda: ([{k: v for k, v in a.items() if k != "arm"} for a in answers], questions,
                                       set()),
            (p11e, "HUMAN"): t / "human",
            (p11b, "answer_files"): lambda: sorted(files), (p11b, "QUESTIONS"): t / "questions.json",
            (p11b, "JUDGE"): t / "judge", (p11b, "RUBRIC_SCORES"): t / "rubric.json",
            (p11b, "variant_ids"): lambda: {"O": "v-aaa", "S": "v-bbb", "R0": "v-ccc"},
            (rqm, "score_judge_root"): lambda *a, **k: {},
        }
        self.patches = [mock.patch.object(m, n, v) for (m, n), v in patches.items()]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        super().tearDown()

    def quiet(self, fn, *a):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*a)

    def test_end_to_end(self):
        s = self.quiet(p11h.smoke, None)
        self.assertEqual(s["calls"][0]["valid"], True)
        self.assertNotIn("scores", json.dumps(s))
        args = SimpleNamespace(rule_confirmation="r", time_confirmation="t")
        self.quiet(p11h.freeze, args)
        plan = aqs.read_json(p11h.PLAN)
        self.assertEqual(plan["human_agreement"]["status"], "not_collected")
        self.assertNotIn("human_agreement", plan["criteria"])
        self.assertAlmostEqual(plan["cost"]["worst_case_reserved_usd"], 1.08)
        with self.assertRaisesRegex(SystemExit, "plan-already-written"):
            self.quiet(p11h.freeze, args)
        for r in ("A", "A2", "B"):
            out = self.quiet(p11h.score, SimpleNamespace(run=r, limit=None))
            self.assertEqual(out["usage"]["first_attempt_valid"], 3)
        self.assertEqual(len(self.fake.bodies), 1 + 9)
        ledger = [json.loads(x) for x in p11h.LEDGER.read_text().splitlines()]
        self.assertEqual(sum(e["event"] == "reserved_before_request" for e in ledger), 10)  # smoke shares the ledger
        path = self.quiet(p11h.analyse, None)
        a = aqs.read_json(path)
        self.assertEqual(a["by_pair_type_A"]["O-S"]["base_wins"], 1)  # O (all 5) beats S (fit 4)
        self.assertEqual(a["by_pair_type_A"]["O-R0"]["base_wins"], 1)
        self.assertEqual(a["nondeterminism_A_vs_A2"]["identical_score_vectors"], 3)
        self.assertEqual(a["spread_A"]["all_5"], 1)
        d = aqs.read_json(self.quiet(p11h.evaluate, SimpleNamespace(analysis=str(path))))
        self.assertEqual(d["decision"], "qualified_for_triage")
        self.assertFalse(d["provisional"])
        with self.assertRaises(FileExistsError):
            self.quiet(p11h.analyse, None)

    def test_freeze_refuses_without_smoke_or_after_a_run(self):
        args = SimpleNamespace(rule_confirmation="r", time_confirmation="t")
        with self.assertRaisesRegex(SystemExit, "smoke-not-run"):
            self.quiet(p11h.freeze, args)
        (p11h.OUT / "run-A").mkdir(parents=True)
        with self.assertRaisesRegex(SystemExit, "scorer-run-exists"):
            self.quiet(p11h.freeze, args)

    def test_score_refuses_packets_that_differ_from_11e(self):
        self.quiet(p11h.smoke, None)
        self.quiet(p11h.freeze, SimpleNamespace(rule_confirmation="r", time_confirmation="t"))
        shutil.rmtree(p11h.OUT_11E / "run-A")
        answers, questions = fixture()
        packets, key = aqs.build_packets(answers, questions, 99)
        aqs.write_packets(p11h.OUT_11E / "run-A", packets, key, 99)
        with self.assertRaisesRegex(SystemExit, "packets-differ-from-11e"):
            self.quiet(p11h.score, SimpleNamespace(run="A", limit=None))
        with self.assertRaisesRegex(SystemExit, "packets-differ-from-11e"):  # also on a rerun
            self.quiet(p11h.score, SimpleNamespace(run="A", limit=None))
        self.assertEqual(len(self.fake.bodies), 1)  # no call after the mismatch


if __name__ == "__main__":
    unittest.main()

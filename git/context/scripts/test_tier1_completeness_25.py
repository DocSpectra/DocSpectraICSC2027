"""Offline fixture tests for the Sprint 25 legacy adapter (no model, no real artifacts)."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "RetrievalArena"))
import rubric_quote_match as rqm  # noqa: E402
import tier1_completeness_25 as t25  # noqa: E402
from retrieval_arena import answer_element_comparison as aec  # noqa: E402


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class AdapterTests(unittest.TestCase):
    def test_parse_stem(self):
        self.assertEqual(t25.parse_stem("v-6337600cdc97-42f436b5b230-s6-no-follow-r2"),
                         ("v-6337600cdc97", "42f436b5b230", "s6-no-follow", 2))

    def test_rubric_reader_keeps_aliases_attempts_and_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            verdicts = [{"element_id": "C1", "verdict": "satisfied", "answer_span": "x", "reason": "r"}]
            elements = [{"element_id": "C1", "requirement": "q", "acceptance": "a"}]
            for uid in ("u1", "u2"):
                write(root / "packets" / f"{uid}.json",
                      {"units": [{"unit_id": uid, "question": "Q?", "answer": "x y", "elements": elements}]})
            raw = {"message": {"content": json.dumps({"units": [{"unit_id": "u1", "verdicts": verdicts}]})}}
            norm = {"units": [{"unit_id": "u1", "verdicts": [dict(verdicts[0], verdict="not_satisfied")]}]}
            write(root / "local-qwen-v4/u1/attempt-02.raw.json", raw)
            write(root / "local-qwen-v4/u1/attempt-02.normalized.json", norm)
            write(root / "local-qwen-v4/u1/attempt-01.raw.json", {"message": {"content": "{}"}})
            sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
            write(root / "local-qwen-v4/u1/completion.json",
                  {"valid": True, "attempt": 2, "raw_response_sha256": sha(root / "local-qwen-v4/u1/attempt-02.raw.json"),
                   "normalized_response_sha256": sha(root / "local-qwen-v4/u1/attempt-02.normalized.json")})
            write(root / "local-qwen-v4/u2/attempt-01.json", {"valid": False, "validation_error": "TimeoutError:timed out"})
            write(root / "aliases.json", {"u1": ["a.json", "b.json"], "u2": ["c.json"]})
            write(root / "packet-manifest.json", [{"unit_id": u, "sha256": sha(root / "packets" / f"{u}.json")} for u in ("u1", "u2")])
            old = t25.RUBRIC
            t25.RUBRIC = root
            try:
                issues = []
                out = t25.load_rubric(issues)
            finally:
                t25.RUBRIC = old
            self.assertEqual(issues, [])
            self.assertEqual(out["u1"]["answer_ids"], ["a", "b"])
            rec = out["u1"]["record"]
            self.assertEqual((rec["status"], rec["selected_attempt"]), ("valid", 2))
            self.assertEqual(rec["raw_verdicts"][0]["verdict"], "satisfied")
            self.assertEqual(rec["strict_verdicts"][0]["verdict"], "not_satisfied")
            self.assertEqual(out["u2"]["record"]["status"], "no_completion:TimeoutError:timed out")

    def test_ra_relaxed_rule_equals_context_rule(self):
        answer = "Run **docker compose up** [p lines 1-3] and then “check” the logs carefully today."
        spans = ["docker compose up", "Run docker compose up and then check", "Run ... the logs",
                 "logs ... Run", "Run docker compose up and then check the logs carefully now", "nothing alike at all",
                 None, "null", "check\" the logs"]
        for s in spans:
            self.assertEqual(aec.relaxed_match(s, answer), rqm.relaxed_match(s, answer), s)


if __name__ == "__main__":
    unittest.main()

"""Pairwise ranker and relaxed quote-match checks with a fake Ollama transport (no model calls)."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import answer_pairwise_ranker as apr  # noqa: E402
import rubric_quote_match as rqm  # noqa: E402

GOOD = {"errors_a": [], "errors_b": ["wrong flag"], "decisive_criterion": 1, "preference": "A",
        "reason": "B uses a wrong flag."}


class FakeOllama:
    """Answers /api/tags and /api/chat; `replies` is consumed per chat call (dicts are JSON-encoded)."""

    def __init__(self, replies=None, digests=None):
        self.replies = list(replies or [])
        self.digests = list(digests or [])
        self.calls = []

    def digest(self, model):
        return self.digests.pop(0) if self.digests else apr.EXPECTED_DIGEST

    def chat(self, body):
        self.calls.append(body)
        reply = self.replies.pop(0) if self.replies else GOOD
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return json.dumps({"message": {"content": content}, "prompt_eval_count": 100, "eval_count": 20,
                           "total_duration": 5, "done_reason": "stop"}).encode()


def fixture(arms=("v-aaa", "v-bbb", "v-ccc"), cells=("s1-follow",)):
    answers = [{"answer_id": f"{a}-q1-{c}.json", "question_id": "q1", "cell": c, "arm": a,
                "text": f"answer {i} in {j}"} for i, a in enumerate(arms) for j, c in enumerate(cells)]
    questions = {"q1": {"question": "How do I build?", "core_elements": [
        {"element_id": "C1", "requirement": "Use buildx", "acceptance": "names buildx", "page_id": "p1",
         "exact_quote": "secret quote"}]}}
    return answers, questions


class CombineTest(unittest.TestCase):
    def test_cases(self):
        self.assertEqual(apr.combine("A", "B"), {"outcome": "x", "kind": "consistent_win", "position_flip": False})
        self.assertEqual(apr.combine("B", "A")["outcome"], "y")
        self.assertEqual(apr.combine("tie", "tie"), {"outcome": "tie", "kind": "both_tie", "position_flip": False})
        self.assertEqual(apr.combine("A", "A"), {"outcome": "tie", "kind": "flip", "position_flip": True})
        self.assertEqual(apr.combine("B", "B")["kind"], "flip")
        self.assertEqual(apr.combine("A", "tie"), {"outcome": "tie", "kind": "half_tie", "position_flip": False})

    def test_cycle_detected_not_resolved(self):
        cyc = apr.rank_cell(["x", "y", "z"], [("x", "y", "x"), ("y", "z", "x"), ("x", "z", "y")])
        self.assertEqual(cyc["cycles"], [["x", "y", "z"]])
        self.assertIsNone(cyc["ranking"])
        ok = apr.rank_cell(["x", "y", "z"], [("x", "y", "x"), ("y", "z", "tie"), ("x", "z", "x")])
        self.assertEqual(ok["cycles"], [])
        self.assertEqual(ok["ranking"], [["x"], ["y", "z"]])


class PacketTest(unittest.TestCase):
    def test_blinding_and_labels(self):
        answers, questions = fixture()
        packets, key = apr.build_pairs(answers, questions, ["v-aaa", "v-bbb", "v-ccc"], seed=7)
        self.assertEqual(len(packets), 6)  # 3 pairs x 2 orders
        forbidden = {a["answer_id"] for a in answers} | {a["arm"] for a in answers} | {"boolean-unit-id"}
        for p in packets:
            apr.check_blind(p, forbidden)
            self.assertEqual(set(p), apr.PACKET_KEYS)
            self.assertNotIn("secret quote", json.dumps(p))
            self.assertNotIn("element_id", json.dumps(p))
        by_pair = {}
        for pid, k in key.items():
            by_pair.setdefault(k["pair_id"], {})[k["order"]] = pid
        for orders in by_pair.values():
            xy, yx = (next(p for p in packets if p["packet_id"] == orders[o]) for o in ("xy", "yx"))
            self.assertNotEqual(xy["packet_id"], yx["packet_id"])
            self.assertEqual((xy["answer_a"], xy["answer_b"]), (yx["answer_b"], yx["answer_a"]))
        labels = set(key) | {k["pair_id"] for k in key.values()}
        self.assertEqual(len(labels), 9)  # all fresh
        self.assertTrue(all(label.startswith("rk-") for label in labels))
        _, key2 = apr.build_pairs(answers, questions, ["v-aaa", "v-bbb", "v-ccc"], seed=8)
        self.assertFalse(set(key) & set(key2))  # another seed (channel) gets other labels
        bad = dict(packets[0], arm="O")
        with self.assertRaisesRegex(ValueError, "packet-keys"):
            apr.check_blind(bad)
        leak = dict(packets[0], answer_a="see v-aaa-q1-s1-follow.json")
        with self.assertRaisesRegex(ValueError, "forbidden-string"):
            apr.check_blind(leak, forbidden)
        nested = dict(packets[0], reference_notes=[{"requirement": "r", "acceptance": "a", "verdict": "satisfied"}])
        with self.assertRaisesRegex(ValueError, "reference-note-keys"):
            apr.check_blind(nested)

    def test_render_does_not_substitute_inside_answers(self):
        answers, questions = fixture()
        answers[0]["text"] = "literal <<ANSWER_B>> marker"
        packets, _ = apr.build_pairs(answers, questions, ["v-aaa", "v-bbb"], seed=1)
        text = apr.render(next(p for p in packets if p["answer_a"].startswith("literal")))
        self.assertIn("literal <<ANSWER_B>> marker", text)
        self.assertIn("Use buildx (Accept if: names buildx)", text)


class RunTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()) / "ranker"
        answers, questions = fixture(arms=("v-aaa", "v-bbb"), cells=("s1", "s6"))
        packets, key = apr.build_pairs(answers, questions, ["v-aaa", "v-bbb"], seed=3)
        apr.write_packets(self.root, packets, key, 3, {a["answer_id"] for a in answers})
        self.manifest = apr.read_json(self.root / "packet-manifest.json")

    def tearDown(self):
        shutil.rmtree(self.root.parent)

    def run_quiet(self, transport, **kw):
        return apr.run(self.root, transport=transport, log=lambda _: None, **kw)

    def test_receipt_and_request(self):
        receipt = apr.read_json(self.root / "receipt.json")
        self.assertEqual(receipt["combine_rule"], "pairwise_combine_v1")
        self.assertEqual(receipt["prompt_sha256"], apr.sha(apr.PROMPT))
        fake = FakeOllama()
        self.run_quiet(fake, limit=1)
        body = fake.calls[0]
        self.assertIs(body["think"], False)
        self.assertEqual(body["options"], {"temperature": 0, "num_ctx": 32768})
        self.assertEqual(body["format"], json.loads(apr.SCHEMA.read_text(encoding="utf-8")))

    def test_schema_invalid_is_failed_attempt_then_retry(self):
        bad = dict(GOOD, preference="maybe")
        fake = FakeOllama(replies=[bad, "not json"])
        r = self.run_quiet(fake, limit=2)
        self.assertEqual([x["status"] for x in r], ["failed", "failed"])
        first = self.root / "calls" / self.manifest[0]["packet_id"]
        rec = apr.read_json(first / "attempt-01.json")
        self.assertFalse(rec["valid"])
        self.assertIn("ValidationError", rec["validation_error"])
        self.assertTrue((first / "attempt-01.raw.json").exists())
        self.assertFalse((first / "completion.json").exists())
        self.assertEqual(self.run_quiet(FakeOllama(), limit=1)[0]["status"], "previous_failure")
        self.assertEqual(self.run_quiet(FakeOllama(), limit=1, retry=True)[0]["status"], "valid")
        self.assertEqual(apr.read_json(first / "completion.json")["attempt"], 2)

    def test_resume_after_partial_run(self):
        first = FakeOllama()
        self.run_quiet(first, limit=2)
        self.assertEqual(len(first.calls), 2)
        second = FakeOllama()
        r = self.run_quiet(second)
        self.assertEqual([x["status"] for x in r], ["already_valid"] * 2 + ["valid"] * 2)
        self.assertEqual(len(second.calls), 2)
        s = apr.summarize(self.root, write=False)
        self.assertEqual(s["usage"]["packets_valid"], 4)
        self.assertEqual(s["position_flips"], 2)  # GOOD always prefers A: every pair flips
        self.assertTrue(all(r["kind"] == "flip" for r in s["pairs"]))

    def test_digest_drift_stops(self):
        with self.assertRaisesRegex(RuntimeError, "digest-drift-before"):
            self.run_quiet(FakeOllama(digests=["other"]))
        with self.assertRaisesRegex(RuntimeError, "digest-drift-after"):
            self.run_quiet(FakeOllama(digests=[apr.EXPECTED_DIGEST, "other"]))
        self.assertFalse(list(self.root.glob("calls/*/completion.json")))

    def test_packet_tamper_stops(self):
        path = self.root / "packets" / self.manifest[0]["packet"]
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "packet-hash-drift"):
            self.run_quiet(FakeOllama())


class RelaxedQuoteTest(unittest.TestCase):
    """Fixtures are excerpts of real 11b judge spans and answers (one per demotion category)."""

    def check(self, span, answer, rule):
        self.assertNotIn(span, answer)  # the strict check rejects it
        ok, got, detail = rqm.relaxed_match(span, answer)
        self.assertEqual((ok, got), (rule is not None, rule), detail)

    def test_code_block_indentation(self):
        answer = ("2. Add the source:\n\n   ```yaml\n   rag:\n     codebase:\n       docs: [./src, ./pkg]   "
                  "# use paths covering your code\n       strategies:\n         - type: semantic-embeddings\n   ```")
        span = ("rag:\n  codebase:\n    docs: [./src, ./pkg]   # use paths covering your code\n    strategies:\n"
                "      - type: semantic-embeddings")
        self.check(span, answer, "exact_normalized")

    def test_code_lines_skipped_without_ellipsis(self):
        answer = ("default allow := false\n\n# Permit the local build context.\nallow if input.local\n\n"
                  "# Replace/add only approved registry hostnames.\nallowed_registries := [\"docker.io\", \"ghcr.io\"]\n"
                  "allow if {\n  input.image\n  input.image.host in allowed_registries\n}\n\ndecision := {\"allow\": allow}\n```")
        span = "default allow := false\n\n# Permit the local build context.\nallow if input.local\n\ndecision := {\"allow\": allow}"
        self.check(span, answer, "word_order_90")

    def test_markdown(self):
        answer = ("Customize the mirrored FIPS image.** In Docker Hub, go to **Hardened Images > Manage > Mirrored "
                  "Images**, choose the mirrored Python repository, then **Customize**.")
        span = ("Customize the mirrored FIPS image. In Docker Hub, go to **Hardened Images > Manage > Mirrored "
                "Images**, choose the mirrored Python repository, then **Customize**.")
        self.check(span, answer, "exact_normalized")
        self.check("filter for FIPS, open the Python repository, and inspect Images. Or run `docker dhi catalog list`",
                   "filter for FIPS, open the Python repository, and inspect **Images**. Or run `docker dhi catalog list`, then",
                   "exact_normalized")

    def test_ellipsis(self):
        answer = ("Prepare the company CA as an OCI artifact.** The documented pattern uses a build stage. "
                  "[Certificate artifact example](content_manuals_dhi:299-318)\n\n4. **Create the customization "
                  "from the mirrored Python FIPS variant.**")
        span = "Prepare the company CA as an OCI artifact. ... Create the customization from the mirrored Python FIPS variant."
        self.check(span, answer, "ellipsis_pieces")
        self.check(span.replace("...", "…"), answer, "ellipsis_pieces")
        self.check("Create the customization from the mirrored Python FIPS variant. ... Prepare the company CA",
                   answer, None)  # pieces out of order

    def test_missing_paren(self):
        answer = ("Hub automated builds require a Pro, Team, or Business subscription) [Automated testing, lines "
                  "12–22; Troubleshooting, lines 11–14].\n\n1. **Prepare Docker Hub")
        span = ("Hub automated builds require a Pro, Team, or Business subscription [Automated testing, lines "
                "12–22; Troubleshooting, lines 11–14].")
        self.check(span, answer, "word_order_90")

    def test_paraphrase_rejected(self):
        answer = ("3. **Prepare the company CA as an OCI artifact.** The documented pattern builds a certificate into "
                  "an updated CA bundle using a multi-stage build. Build and push the artifact to a repository in "
                  "the same organization namespace as the mirrored DHI. Add the OCI artifact during customization.")
        span = "add the OCI artifact for the company certificate and states the owner-first mirroring dependency"
        self.check(span, answer, None)
        ok, _, detail = rqm.relaxed_match(span, answer)
        self.assertLess(detail["words_in_order"] / detail["words"], 0.9)

    def test_three_scores(self):
        answer = "Use **buildx** to build. Push with docker push."
        raw = [{"element_id": "C1", "verdict": "satisfied", "answer_span": "Use buildx to build."},
               {"element_id": "C2", "verdict": "satisfied", "answer_span": "Push with docker push."},
               {"element_id": "C3", "verdict": "satisfied", "answer_span": "deploy to the moon"},
               {"element_id": "C4", "verdict": "missing", "answer_span": None},
               {"element_id": "C5", "verdict": "satisfied", "answer_span": None}]
        strict = [dict(v) for v in raw]
        for v in strict:
            if v["verdict"] == "satisfied" and (not v["answer_span"] or v["answer_span"] not in answer):
                v["verdict"] = "uncertain"
        s = rqm.score_verdicts(raw, strict, answer, 5)
        self.assertEqual((s["rubric_v1_strict"], s["rubric_v1_relaxed"], s["rubric_v1_raw"]), (0.2, 0.4, 0.8))
        self.assertEqual([(x["element_id"], x["admitted"], x["rule"]) for x in s["relaxed_log"]],
                         [("C1", True, "exact_normalized"), ("C3", False, None), ("C5", False, None)])


if __name__ == "__main__":
    unittest.main()

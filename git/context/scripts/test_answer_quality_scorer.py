"""Pointwise quality scorer v2 checks with a fake Ollama transport (no model calls)."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import answer_quality_scorer as aqs  # noqa: E402


def reply(scores=(5, 5, 5, 5, 5), errors=()):
    out = {"errors": list(errors)}
    for m, s in zip(aqs.MEASURES, scores):
        out[m] = {"note": f"{m} evidence", "score": s}
    return out


class FakeOllama:
    """Answers /api/tags and /api/chat; `replies` is consumed per chat call (dicts are JSON-encoded)."""

    def __init__(self, replies=None, digests=None):
        self.replies = list(replies or [])
        self.digests = list(digests or [])
        self.calls = []

    def digest(self, model):
        return self.digests.pop(0) if self.digests else aqs.EXPECTED_DIGEST

    def chat(self, body):
        self.calls.append(body)
        r = self.replies.pop(0) if self.replies else reply()
        content = r if isinstance(r, str) else json.dumps(r)
        return json.dumps({"message": {"content": content}, "prompt_eval_count": 100, "eval_count": 20,
                           "total_duration": 5, "done_reason": "stop"}).encode()


def fixture(arms=("v-aaa", "v-bbb", "v-ccc")):
    answers = [{"answer_id": f"{a}-q1-s1-follow.json", "question_id": "q1", "cell": "s1-follow", "arm": a,
                "text": f"answer {i} <<QUESTION>>"} for i, a in enumerate(arms)]
    elements = [{"element_id": f"C{i}", "requirement": f"req {i}", "acceptance": f"acc {i}", "page_id": "p1",
                 "exact_quote": "secret quote"} for i in range(5)]
    questions = {"q1": {"question": "How do I build?", "core_elements": elements}}
    return answers, questions


class Tmp(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root)

    def write(self, seed=7, notes_seed=None):
        answers, questions = fixture()
        packets, key = aqs.build_packets(answers, questions, seed, notes_seed)
        aqs.write_packets(self.root, packets, key, seed, notes_seed)
        return packets, key


class PacketTest(Tmp):
    def test_blinding(self):
        answers, questions = fixture()
        packets, key = aqs.build_packets(answers, questions, seed=7)
        self.assertEqual(len(packets), 3)
        forbidden = {a["answer_id"] for a in answers} | {a["arm"] for a in answers}
        for p in packets:
            aqs.check_blind(p, forbidden)
            self.assertEqual(set(p), aqs.PACKET_KEYS)
            text = json.dumps(p)
            self.assertNotIn("secret quote", text)
            self.assertNotIn("element_id", text)
            self.assertTrue(p["packet_id"].startswith("aq-"))
            others = [a["text"] for a in answers if a["text"] != p["answer"]]
            self.assertFalse(any(o in text for o in others))  # one answer per packet
        self.assertEqual({k["answer_id"] for k in key.values()}, {a["answer_id"] for a in answers})
        with self.assertRaisesRegex(ValueError, "packet-keys"):
            aqs.check_blind(dict(packets[0], arm="O"))
        with self.assertRaisesRegex(ValueError, "packet-forbidden-string"):
            aqs.check_blind(dict(packets[0], answer="see v-aaa"), {"v-aaa"})

    def test_same_seed_same_labels_and_order(self):
        answers, questions = fixture()
        a, ka = aqs.build_packets(answers, questions, 7)
        b, kb = aqs.build_packets(answers, questions, 7, notes_seed=99)
        self.assertEqual([p["packet_id"] for p in a], [p["packet_id"] for p in b])
        self.assertEqual({k: v["answer_id"] for k, v in ka.items()}, {k: v["answer_id"] for k, v in kb.items()})

    def test_shuffled_notes_deterministic(self):
        answers, questions = fixture()
        o1 = aqs.notes_order("q1", 5, 20261015)
        self.assertEqual(o1, aqs.notes_order("q1", 5, 20261015))
        self.assertNotEqual(o1, list(range(5)))
        self.assertEqual(sorted(o1), list(range(5)))
        self.assertEqual(aqs.notes_order("q1", 5, None), list(range(5)))
        p1, _ = aqs.build_packets(answers, questions, 7, notes_seed=20261015)
        p2, _ = aqs.build_packets(answers, questions, 7, notes_seed=20261015)
        self.assertEqual([aqs.render(p) for p in p1], [aqs.render(p) for p in p2])
        base, _ = aqs.build_packets(answers, questions, 7)
        self.assertNotEqual(aqs.render(base[0]), aqs.render(p1[0]))
        self.assertEqual([n["requirement"] for n in p1[0]["reference_notes"]], [f"req {i}" for i in o1])

    def test_render_single_pass(self):
        answers, questions = fixture()
        packets, _ = aqs.build_packets(answers, questions, 7)
        text = aqs.render(packets[0])
        self.assertIn("<<QUESTION>>", text)  # inside the answer, left alone
        self.assertEqual(text.count("How do I build?"), 1)
        self.assertNotIn("<<ANSWER>>", text)
        self.assertNotIn("<<REFERENCE_NOTES>>", text)

    def test_refuses_overwrite(self):
        self.write()
        with self.assertRaises(SystemExit):
            self.write()


class CallTest(Tmp):
    def test_valid_and_resume(self):
        self.write()
        fake = FakeOllama()
        res = aqs.run(self.root, fake, log=lambda _: None)
        self.assertEqual([r["status"] for r in res], ["valid"] * 3)
        body = fake.calls[0]
        self.assertFalse(body["think"])
        self.assertEqual(body["options"]["temperature"], 0)
        self.assertEqual(list(body["format"]["properties"]), list(aqs.FIELD_ORDER))
        res = aqs.run(self.root, fake, log=lambda _: None)
        self.assertEqual([r["status"] for r in res], ["already_valid"] * 3)
        self.assertEqual(len(fake.calls), 3)
        s = aqs.summarize(self.root, write=False)
        self.assertEqual(s["usage"]["field_order_ok"], 3)

    def test_schema_rejection_then_one_retry(self):
        self.write()
        bad_score = reply((6, 5, 5, 5, 5))
        missing = {k: v for k, v in reply().items() if k != "clarity"}
        fake = FakeOllama([bad_score, "not json", missing, reply(), reply(), "still not json"])
        res = aqs.run(self.root, fake, log=lambda _: None)
        # 3 failures in the first pass; the retry pass recovers two and the third stays unavailable
        self.assertEqual([r["status"] for r in res], ["failed"] * 3 + ["valid", "valid", "failed"])
        s = aqs.summarize(self.root, write=False)
        self.assertEqual(s["usage"]["first_attempt_valid"], 0)
        self.assertEqual(s["usage"]["max_attempts"], 2)
        self.assertEqual(len(s["unavailable"]), 1)
        self.assertIsNone(s["cells"][0]["ranking"])
        self.assertEqual(sum(p["outcome"] is None for p in s["cells"][0]["pairs"]), 2)
        res = aqs.run(self.root, fake, log=lambda _: None)  # no third attempt
        self.assertIn("unavailable_after_retry", [r["status"] for r in res])
        self.assertEqual(len(fake.calls), 6)

    def test_field_order_reported(self):
        self.write()
        r = reply()
        swapped = json.dumps({"correctness": r["correctness"], "errors": [],
                              **{m: r[m] for m in aqs.MEASURES[1:]}})
        aqs.run(self.root, FakeOllama([swapped]), limit=1, log=lambda _: None)
        rec = next(self.root.glob("calls/*/attempt-01.json"))
        rec = json.loads(rec.read_text(encoding="utf-8"))
        self.assertTrue(rec["valid"])
        self.assertFalse(rec["field_order_ok"])

    def test_digest_drift(self):
        self.write()
        with self.assertRaisesRegex(RuntimeError, "model-digest-drift-before"):
            aqs.run(self.root, FakeOllama(digests=["other"]), log=lambda _: None)
        with self.assertRaisesRegex(RuntimeError, "model-digest-drift-after"):
            aqs.run(self.root, FakeOllama(digests=[aqs.EXPECTED_DIGEST, "other"]), log=lambda _: None)

    def test_packet_drift(self):
        self.write()
        p = next(self.root.glob("packets/*.json"))
        p.write_text(p.read_text(encoding="utf-8").replace("answer", "ANSWER"), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "packet-hash-drift"):
            aqs.run(self.root, FakeOllama(), log=lambda _: None)


def s(*v):
    return dict(zip(aqs.MEASURES, v))


class RankTest(unittest.TestCase):
    def test_margin_decides(self):
        self.assertEqual(aqs.compare(s(5, 3, 5, 5, 5), s(5, 4, 1, 1, 1)), ("b", "fit"))
        self.assertEqual(aqs.compare(s(4, 1, 1, 1, 1), s(3, 5, 5, 5, 5)), ("a", "correctness"))

    def test_sub_margin_falls_through(self):
        self.assertEqual(aqs.compare(s(4.5, 3, 5, 5, 5), s(4.0, 3, 4, 5, 5)), ("a", "completeness"))
        self.assertEqual(aqs.compare(s(4.5, 3, 5, 5, 5), s(4.0, 3, 5, 5, 5)), ("tie", None))

    def test_all_equal_tie(self):
        self.assertEqual(aqs.compare(s(3, 3, 3, 3, 3), s(3, 3, 3, 3, 3)), ("tie", None))

    def test_cell_ranking(self):
        r = aqs.rank_cell_lexicographic(["x", "y", "z"], {"x": s(5, 5, 5, 5, 5), "y": s(5, 5, 4, 5, 5),
                                                          "z": s(5, 5, 4, 5, 5)})
        self.assertEqual(r["ranking"], [["x"], ["y", "z"]])
        self.assertEqual(r["cycles"], [])
        self.assertEqual([p["deciding_measure"] for p in r["pairs"]], ["completeness", "completeness", None])


if __name__ == "__main__":
    unittest.main()

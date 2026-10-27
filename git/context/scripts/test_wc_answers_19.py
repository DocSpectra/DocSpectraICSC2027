"""Offline tests for the Sprint 19 driver additions (toy inputs only; no containers, no network, no model)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wc_answers_19 as w  # noqa: E402

sys.path.insert(0, str(w.rr.RA))
from retrieval_arena.ranked_scoring import validate_ranked_inputs  # noqa: E402


def ledger(rows):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    f.write("\n".join(json.dumps(r) for r in rows) + "\n")
    f.close()
    return Path(f.name)


class CellStem(unittest.TestCase):
    def test_tier2_keeps_the_full_question_id(self):
        a = w.cell_stem(2, "v-x", "docker_docs_faq_0001", "s1-follow", 1)
        b = w.cell_stem(2, "v-x", "docker_docs_faq_0002", "s1-follow", 1)
        self.assertNotEqual(a, b)
        self.assertIn("docker_docs_faq_0001", a)

    def test_tier1_keeps_the_12b_stem(self):
        qid = "3b5890ab4c4510d13ecab1e2c24e6dc8847922084aad04dc217832486693700a"
        self.assertEqual(w.cell_stem(1, "v-x", qid, "s6-follow", 2), w.sf.cell_stem("v-x", qid, "s6-follow", 2))

    def test_every_planned_cell_has_a_unique_name(self):
        for led in w.load_plan()["ledgers"]:
            stems = {w.cell_stem(led["tier"], c["variant"], c["question_id"], c["condition"], c["replicate"])
                     for c in led["cells"]}
            self.assertEqual(len(stems), len(led["cells"]), led["id"])


class LedgerCharge(unittest.TestCase):
    def test_agent_scorer_and_ra_rows(self):
        p = ledger([
            {"event": "reported_usage", "usage": {"input": 1_000_000, "output": 0}},           # agent: $0.125
            {"event": "reported_usage", "usage": {"input_tokens": 0, "output_tokens": 1_000_000}},  # scorer: $0.50
            {"event": "failed_possible_billable", "projected_usd": 0.01},
            {"event": "reserved", "reservation": 1, "projected_usd": 0.002},
            {"event": "reported", "reservation": 1, "cost_usd": 0.001},
            {"event": "reserved", "reservation": 2, "projected_usd": 0.004},
            {"event": "failed", "reservation": 2, "error": "APIConnectionError"},               # counts its reservation
        ])
        self.assertAlmostEqual(w.ledger_charge(p), 0.125 + 0.5 + 0.01 + 0.001 + 0.004)


class RankedPredictions(unittest.TestCase):
    def test_order_kept_duplicates_collapsed_and_ra_accepts(self):
        rows = [{"question_id": "q1", "retrieved_context": [
            {"doc_id": "b", "rank": "2", "score": "5"}, {"doc_id": "a", "rank": "1", "score": "1"},
            {"doc_id": "b", "rank": "3", "score": "9"}, {"doc_id": "c", "rank": "4", "score": "9"}]}]
        p = ledger(rows)
        pred = w.ranked_predictions(p, ["q1"], 2)[0]
        self.assertEqual([i["support_target_id"] for i in pred["items"]], ["a", "b", "c"])
        self.assertEqual(pred["duplicate_collapse_count"], 1)
        self.assertFalse(pred["retrieval_exhausted"])
        dataset = {"schema_version": "retrieval_arena.ranked_dataset.v2", "dataset_id": "d", "corpus_id": "c",
                   "query_set_id": "q", "support_surface_id": "s", "corpus_snapshot_id": "x", "graph_snapshot_id": "g",
                   "ranked_item_granularity": "support_target", "qrels_scope": "pooled", "relevance_scale": "binary",
                   "judgment_policy_id": "j", "judgment_provenance_sha256": "0" * 64, "ordered_query_ids": ["q1"],
                   "support_group_by_query": {"q1": "q1"}, "qrels_sha256": "0" * 64, "dataset_manifest_sha256": "0" * 64}
        qrels = [{"query_id": "q1", "support_target_id": "b", "relevance": 1, "judgment_id": "j1",
                  "judgment_provenance_sha256": "0" * 64}]
        validate_ranked_inputs(dataset, qrels, [pred], w.ranked_policy(2))


if __name__ == "__main__":
    unittest.main()

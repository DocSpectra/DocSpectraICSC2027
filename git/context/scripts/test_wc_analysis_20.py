"""Offline tests for the Sprint 20 analysis helpers (no run outputs needed)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wc_analysis_20 as w  # noqa: E402


def res(est, lo, hi):
    return {"estimate": est, "ci95": [lo, hi], "n": 1, "clusters": 1}


class Clusters(unittest.TestCase):
    def test_duplicate_pair_is_one_cluster_and_absent_members_drop(self):
        cl = w.clusters_for(["a", "b", "c", "d"], [["a", "c"], ["d", "z"]])
        self.assertEqual(cl, [("a", "c"), ("b",), ("d",)])

    def test_bootstrap_estimate_weighs_questions_and_is_deterministic(self):
        diffs = {"a": 1.0, "c": 1.0, "b": -2.0}
        cl = w.clusters_for(["a", "b", "c"], [["a", "c"]])
        r1, r2 = w.cluster_bootstrap(diffs, cl), w.cluster_bootstrap(diffs, cl)
        self.assertEqual(r1, r2)
        self.assertAlmostEqual(r1["estimate"], 0.0)
        self.assertEqual((r1["n"], r1["clusters"]), (3, 2))
        lo, hi = r1["ci95"]
        self.assertTrue(-2.0 <= lo <= 0.0 <= hi <= 1.0)

    def test_empty(self):
        self.assertEqual(w.cluster_bootstrap({}, [("a",)])["n"], 0)


class Pairing(unittest.TestCase):
    def test_contrast_uses_only_questions_with_both_values(self):
        vals = {"O": {"q1": 0.0, "q2": 1.0, "q3": None}, "S": {"q1": 0.0, "q2": 1.0, "q3": 1.0},
                "LT": {"q1": 1.0, "q2": None, "q3": 1.0}, "R0": {"q1": 0.0, "q2": 0.0, "q3": 0.0}}
        t = w.contrast_table(vals, ["q1", "q2", "q3"], [("q1",), ("q2",), ("q3",)])
        self.assertEqual(t["LT - O"]["n"], 1)
        self.assertEqual(t["LT - O"]["estimate"], 1.0)
        self.assertEqual(t["LT - R0"]["n"], 2)
        self.assertEqual(t["S - O"]["estimate"], 0.0)

    def test_per_question_averages_replicates_then_conditions(self):
        cells = {("O", "q", "s1"): [1.0, 0.0], ("O", "q", "s6"): [1.0], ("O", "r", "s1"): [None]}
        out = w.per_question(cells)
        self.assertAlmostEqual(out["O"]["q"], 0.75)
        self.assertNotIn("r", out["O"])
        self.assertEqual(w.per_question(cells, {"s6"})["O"]["q"], 1.0)


class Ranking(unittest.TestCase):
    def test_ndcg_unavailable_when_an_unjudged_page_is_in_the_top_10(self):
        ranking = ["x", "g", "u"]
        judged = {"x", "g"}
        self.assertIsNone(w.score_ranking(ranking, {"g"}, None, judged)["ndcg@10"])
        full = w.score_ranking(ranking, {"g"}, None, judged | {"u"})
        self.assertAlmostEqual(full["rr"], 0.5)
        self.assertEqual(full["recall@5"], 1.0)

    def test_hits_bound_treats_undetermined_as_relevant(self):
        self.assertEqual(w.score_ranking(["u", "g"], {"g", "u"}, None, None)["rr"], 1.0)


class Rules(unittest.TestCase):
    def rows(self, primary, hits, o1, surv):
        blank = {"retrieval": {}, "navigation": {}}
        t = lambda r: {"retrieval": {"lexical/rr": {"LT - O": r}}, "navigation": {}}
        return {"primary": t(primary), "sa5_hits": t(hits), "original_101": t(primary), "strict_111": t(primary),
                "lenient_o1_111": t(o1), "o1_surviving_111": t(surv), "sa7_without_6": blank, "historical_top5": blank}

    def test_sa5_needs_the_same_sign(self):
        r = w.robustness(self.rows(res(-0.1, -0.2, -0.01), res(0.02, -0.1, 0.1), res(-0.1, -0.2, -0.1), res(-0.1, -0.2, -0.1)))
        self.assertFalse(r["sa5"]["retrieval:lexical/rr:LT - O"]["robust"])
        self.assertTrue(r["sa6"]["retrieval:lexical/rr:LT - O"]["robust"])       # all four bracket rows negative
        r = w.robustness(self.rows(res(-0.1, -0.2, -0.01), res(-0.1, -0.2, -0.01), res(0.01, -0.1, 0.1), res(0.0, -0.1, 0.1)))
        self.assertFalse(r["sa6"]["retrieval:lexical/rr:LT - O"]["robust"])      # lenient row flips the sign

    def test_o1_derived_credit_only_if_it_disappears_or_reverses(self):
        r = w.robustness(self.rows(res(-0.1, -0.2, -0.01), res(-0.1, -0.2, -0.01), res(0.06, 0.01, 0.1), res(-0.04, -0.08, -0.01)))
        self.assertTrue(r["o1_derived"]["retrieval:lexical/rr:LT - O"]["derived_credit_driven"])
        r = w.robustness(self.rows(res(-0.1, -0.2, -0.01), res(-0.1, -0.2, -0.01), res(0.06, 0.01, 0.1), res(0.05, 0.01, 0.09)))
        self.assertFalse(r["o1_derived"]["retrieval:lexical/rr:LT - O"]["derived_credit_driven"])


if __name__ == "__main__":
    unittest.main()

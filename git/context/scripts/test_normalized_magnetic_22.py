"""Sprint 22 scientific joins: view rules, pairing, pooling and the inherited sign-flip rule.

Run from git/context/scripts: python -B -m unittest test_normalized_magnetic_22
"""
import json
import unittest

import normalized_magnetic_22 as nm


def receipt(value, status="qualified"):
    return {"normalized": {"0.25": {"status": status, "lambda1": value}}, "selection": {"nodes": 10}}


class Joins(unittest.TestCase):
    def test_sprint18_test_replays_through_the_new_pairing_and_pooling(self):
        """Fed the saved unnormalized per-area values, area_analogue must give Sprint 18's frozen result exactly."""
        import wc_refinement_15 as wr
        hist = nm.read_json(nm.EVIDENCE / "wc-spectra-18-v1-2026-10-19.json")
        per_area = {}
        for path in sorted((nm.WC_ANALYSIS / "subgraph").glob("*__A*.json")):
            arm, area = path.stem.split("__")
            r = json.loads(path.read_bytes())
            per_area.setdefault(area, {})[arm] = {"d": r["d"]["magnetic_lambda1_q0.25"],
                                                  "nulls": [n["magnetic_lambda1_q0.25"] for n in r["nulls"]]}
        out = nm.area_analogue(per_area, wr.sign_flip_test)
        self.assertEqual(out["n"], hist["primary_test"]["n"])
        self.assertEqual(out["p"], hist["primary_test"]["p"])
        self.assertAlmostEqual(out["statistic_mean"], hist["primary_test"]["statistic_mean"], places=12)
        self.assertEqual(sorted(out["differences"]), hist["primary_test"]["areas_in_test"])
        self.assertEqual(out["areas_one_arm_only"], hist["primary_test"]["areas_one_arm_only"])
        self.assertAlmostEqual(out["pooled_null_sd"], hist["pooled_null_sd"]["magnetic_lambda1_q0.25"], places=14)
        sa1 = nm.area_analogue(per_area, wr.sign_flip_test, drop={"A10", "A12", "A21"})
        self.assertEqual((sa1["n"], sa1["p"]), (hist["SA1"]["n"], hist["SA1"]["p"]))

    def test_area_inputs_pair_each_graph_with_its_own_mapped_baseline(self):
        rows = {"area/LT/A02/O-prime#lwcc_in_S": receipt(.5), "area/LT/A02/v#lwcc_in_S": receipt(.7),
                "area/LT/A02/ln-1#lwcc_in_S": receipt(.6), "area/LT/A02/ln-2#lwcc_in_S": receipt(.4),
                "area/LT/A02/v#fixed_S": receipt(9.0), "area/LT/A02/O-prime#fixed_S": receipt(1.0)}
        built = nm.area_inputs(rows, "lwcc_in_S")
        self.assertAlmostEqual(built["A02"]["LT"]["d"], .2)
        self.assertEqual([round(x, 12) for x in built["A02"]["LT"]["nulls"]], [.1, -.1])
        fixed = nm.area_inputs(rows, "fixed_S")
        self.assertAlmostEqual(fixed["A02"]["LT"]["d"], 8.0)
        self.assertEqual(fixed["A02"]["LT"]["nulls"], [])

    def test_unqualified_values_never_enter_statistics(self):
        self.assertIsNone(nm.lam(receipt(.3, "failed"), .25))
        rows = {"area/R0/A01/O-prime#lwcc_in_S": receipt(.5), "area/R0/A01/v#lwcc_in_S": receipt(.7, "failed"),
                "area/R0/A01/ln-1#lwcc_in_S": receipt(.6), "area/R0/A01/ln-2#lwcc_in_S": receipt(.4)}
        built = nm.area_inputs(rows, "lwcc_in_S")
        self.assertIsNone(built["A01"]["R0"]["d"])
        out = nm.area_analogue({"A01": {"R0": built["A01"]["R0"], "LT": {"d": .1, "nulls": [0.0, .2]}}},
                               lambda d: {"n": len(d), "p": None})
        self.assertEqual(out["not_computable"], {"A01": {"R0": None, "LT": out["z"]["A01"]["LT"]}})
        self.assertEqual(out["n"], 0)

    def test_panel_view_rules(self):
        anchor = {"graph_id": "anchor/x", "bundle": "bundles/x.json", "provenance": {}}
        jobs = nm.panel_jobs("anchors", anchor)
        self.assertEqual([j["view"] for j in jobs], [{"kind": "induced", "node_set": "historical_lwcc"}, {"kind": "full"}])
        self.assertEqual(jobs[0]["normalized_q"], nm.GRID)
        self.assertEqual(jobs[1]["normalized_q"], nm.ENDPOINTS)
        missing = dict(anchor, provenance={"full_view": "unavailable"})
        self.assertEqual(len(nm.panel_jobs("anchors", missing)), 1)
        area = nm.panel_jobs("area", {"graph_id": "area/LT/A02/v", "bundle": "b", "provenance": {}})
        self.assertEqual([(j["job_id"].split("#")[1], j["view"]) for j in area],
                         [("fixed_S", {"kind": "full"}), ("lwcc_in_S", {"kind": "lwcc"})])
        self.assertTrue(all(j["normalized_q"] == nm.ENDPOINTS == j["unnormalized_q"] for j in area))
        wc = nm.panel_jobs("wc", {"graph_id": "wc/O", "bundle": "b", "provenance": {}})
        self.assertEqual([j["view"]["kind"] for j in wc], ["lwcc", "full"])

    def test_pooled_sd_ignores_incomplete_rows(self):
        self.assertAlmostEqual(nm.pooled_null_sd([[1.0, 3.0], [2.0, None], [5.0]]), 2 ** .5)
        self.assertIsNone(nm.pooled_null_sd([[1.0]]))


if __name__ == "__main__":
    unittest.main()

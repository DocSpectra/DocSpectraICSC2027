"""Offline tests for Sprint 13 (toy graphs only; no endpoint data, no network, no model)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lt_locality_13 as ln  # noqa: E402
import structure_forcing_12 as sf  # noqa: E402
from test_structure_forcing_12 import toy_graph  # noqa: E402


class LocalityNull(unittest.TestCase):
    def setUp(self):
        self.nodes, self.o = toy_graph(n=60, m=140, seed=3)
        self.T = self.nodes[:5]
        self.v = sorted(set(self.o) | {("n00", "n30"), ("n01", "n31")})  # v links two far pages into the region
        self.K = ln.locality_region(self.T, self.o, self.v, added=[])

    def run_ln(self, a, r, f, seed=ln.ln_seed("LT-r01", 1)):
        return ln.locality_null(self.o, self.T, self.K, a, r, f, seed)

    def test_counts_reciprocity_and_every_addition_in_region(self):
        K, T = set(self.K), set(self.T)
        for a, r, f in ((6, 2, 0.0), (6, 2, 0.5), (5, 0, 1.0), (0, 2, 0.0)):
            edges, rec = self.run_ln(a, r, f)
            es, os_ = set(edges), set(self.o)
            added, removed = es - os_, os_ - es
            self.assertEqual((len(added), len(removed)), (a, r))
            self.assertTrue(all(s in K and t in K for s, t in added))
            self.assertTrue(all(s in T or t in T for s, t in added))  # RN's "at least one endpoint in T" kept
            W = T | sf.neighbors(self.o, T) if rec["remove_pool"]["level"] else T
            self.assertTrue(all(s in W and t in W for s, t in removed))
            self.assertEqual(rec["locality"]["added_inside_region"], a)
            self.assertEqual(rec["rule"], "sf13_locality_null_v1")
            if a:
                self.assertLessEqual(abs(sum((t, s) in es for s, t in added) - round(f * a)), 1)
        self.assertLess(len(self.K), len(self.nodes))  # the region is a proper restriction here

    def test_deterministic_and_salted(self):
        e1, _ = self.run_ln(6, 2, 0.5, ln.ln_seed("LT-r01", 1))
        e2, _ = self.run_ln(6, 2, 0.5, ln.ln_seed("LT-r01", 1))
        e3, _ = self.run_ln(6, 2, 0.5, ln.ln_seed("LT-r01", 2))
        self.assertEqual(e1, e2)
        self.assertNotEqual(e1, e3)
        self.assertNotEqual(ln.ln_seed("LT-r01", 1), sf.null_seed("LT-r01", 1))

    def test_full_node_set_and_o_edges_kept(self):
        edges, _ = self.run_ln(4, 0, 0.0)
        self.assertTrue(set(self.o) <= set(edges))
        self.assertTrue(any(s not in self.K or t not in self.K for s, t in edges))  # graph is not cut to K_v

    def test_widening_recorded_and_stays_in_region(self):
        T = ["n00"]
        K = ln.locality_region(T, self.o, self.o, added=[])
        level0 = sum(1 for x in K if x != "n00" for p in (("n00", x), (x, "n00")) if p not in set(self.o))
        a = level0 + 1
        edges, rec = ln.locality_null(self.o, T, K, a, 0, 0.0, 5)
        self.assertEqual(rec["widened"], [{"stage": "add", "need": a}])
        self.assertTrue(all(s in K and t in K for s, t in set(edges) - set(self.o)))
        _, rec0 = self.run_ln(2, 0, 0.0)
        self.assertEqual(rec0["widened"], [])

    def test_refuses_when_region_pool_too_small(self):
        with self.assertRaises(SystemExit):
            ln.locality_null(self.o, self.T, self.K, len(self.K) ** 2, 0, 0.0, 1)
        with self.assertRaises(SystemExit):
            ln.locality_null(self.o, self.T, self.T[:2], 1, 0, 0.0, 1)  # touched set must lie inside K_v


class StratifiedPermutation(unittest.TestCase):
    def setUp(self):
        self.strata = [a for a in ("A1", "A2", "A3", "A4") for _ in range(3)]
        self.arms = ["LT", "MT", "MR"] * 4

    def test_enumerates_81(self):
        masks = list(ln.stratified_assignments(self.strata, {a: 1 for a in ("A1", "A2", "A3", "A4")}))
        self.assertEqual(len(masks), 81)
        self.assertEqual(len(set(masks)), 81)
        for m in masks:
            for a in ("A1", "A2", "A3", "A4"):
                self.assertEqual(sum(g for g, s in zip(m, self.strata) if s == a), 1)

    def test_perfect_separation_gives_one_over_81(self):
        Z = [[10.0 + i, -5.0] if arm == "LT" else [0.1 * i, 0.0] for i, arm in enumerate(self.arms)]
        res = ln.stratified_exact_p(Z, self.arms, self.strata)
        self.assertEqual(res["assignments"], 81)
        self.assertAlmostEqual(res["p"], 1 / 81)
        self.assertEqual(res["at_least_observed"], 1)

    def test_no_signal_gives_p_one(self):
        res = ln.stratified_exact_p([[1.0, 2.0]] * 12, self.arms, self.strata)
        self.assertEqual(res["p"], 1.0)

    def test_area_effect_alone_is_not_significant(self):
        level = {"A1": 0.0, "A2": 1.0, "A3": 30.0, "A4": 0.5}
        noise = [0.02, 0.01, 0.03, 0.03, 0.02, 0.01, 0.01, 0.03, 0.02, 0.02, 0.03, 0.01]  # LT mid, top, bottom, mid
        Z = [[level[s] + e] for s, e in zip(self.strata, noise)]  # large area shifts, small arm-free noise
        res = ln.stratified_exact_p(Z, self.arms, self.strata)
        self.assertGreater(res["p"], 0.05)

    def test_outcome_rows(self):
        keep = {"magnetic_lambda1_q0.25": 1, "magnetic_lambda2_q0.25": 1, "outdegree_gini": -1, "sources": -1,
                "sinks": -1}
        flip = dict(keep, sinks=1)
        self.assertEqual(ln.outcome_row(0.0123, 0.01, keep), "R1")
        self.assertEqual(ln.outcome_row(0.0123, 0.01, flip), "R2")
        self.assertEqual(ln.outcome_row(0.3, 0.0123, keep), "R3")
        self.assertEqual(ln.outcome_row(0.3, 0.3, keep), "R4")
        self.assertEqual(ln.outcome_row(None, 0.01, keep), "R0")


class HashGate(unittest.TestCase):
    def test_refuses_changed_prereg_or_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inp = root / "input.txt"
            inp.write_text("frozen", encoding="utf-8")
            pre = root / "prereg.json"
            pre.write_text(json.dumps({"hashes": {"input": {"path": "input.txt", "sha256": sf.sha(inp)}}}),
                           encoding="utf-8")
            lock = pre.with_suffix(".sha256")
            with self.assertRaises(SystemExit):
                ln.check_prereg(pre, root)  # no lock yet
            lock.write_text(f"{sf.sha(pre)}  prereg.json\n", encoding="ascii")
            self.assertIn("hashes", ln.check_prereg(pre, root))
            inp.write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "frozen-input-changed:input"):
                ln.check_prereg(pre, root)
            inp.write_text("frozen", encoding="utf-8")
            pre.write_text(pre.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "preregistration-hash-changed"):
                ln.check_prereg(pre, root)

    def test_prereg_freezes_code_and_12b_inputs(self):
        self.assertEqual(set(ln.INPUTS), {"plan_12b_v3", "admission_12b", "spectra_12b", "driver_sf12",
                                          "driver_sf12b", "code_sprint13"})
        self.assertEqual(ln.INPUTS["code_sprint13"], Path(ln.__file__).resolve())


if __name__ == "__main__":
    unittest.main()

"""Tests for the Sprint 23 driver: verdict and caveat rules, job mapping, and selection replay from saved bundles."""
import json
import unittest

import magnetic_attribution_23 as d


def row(job, q=0.25, resolved=True, passed=True, rank=1, support=0.5, perm=None):
    side = {"cluster_rank": rank, "boundary_qualified": True, "effective_support_fraction": support,
            "numerics": "qualified"}
    return {"job_id": job, "q": q, "resolved": resolved, "controls_passed": passed, "a": dict(side), "b": dict(side),
            "permutation_check": perm}


class VerdictTests(unittest.TestCase):
    def test_outcomes(self):
        self.assertEqual(d.verdict([row("x"), row("y", resolved=False)])["outcome"], "resolved_matched_response")
        self.assertEqual(d.verdict([row("x", resolved=False)])["outcome"], "no_resolved_matched_response")
        caveated = d.verdict([row("x", rank=2), row("y", support=0.01)])
        self.assertEqual(caveated["outcome"], "resolved_with_localization_caveat")
        self.assertEqual(caveated["caveats"]["x"], ["a_clustered", "b_clustered"])
        failed = d.verdict([row("x", passed=False), row("y", resolved=False)])
        self.assertEqual(failed["outcome"], "no_resolved_matched_response")
        self.assertEqual(failed["failed_controls_or_qualification"], ["x"])

    def test_other_q_rows_do_not_enter_the_primary_outcome(self):
        v = d.verdict([row("x", q=0.10), row("x", resolved=False), row("x", q=0.0, resolved=False)])
        self.assertEqual(v["outcome"], "no_resolved_matched_response")
        self.assertEqual(v["resolved_at_alias_q"], ["x"])
        self.assertTrue(v["q0_all_unresolved"])

    def test_permutation_failure_is_a_caveat(self):
        self.assertIn("permutation_failed", d.caveat(row("x", perm={"passed": False})))


class PlanReplayTests(unittest.TestCase):
    def test_selection_replays_from_saved_bundles_and_jobs_match_plan(self):
        if not d.PLAN.exists():
            self.skipTest("plan not written")
        ma, _ = d.ra()
        plan = d.read_json(d.PLAN)
        jobs = {j["job_id"]: j for j in d.jobs_for("anchors")}
        for a in plan["anchors"]:
            cid = a["corpus_id"]
            b = json.loads((d.INPUTS / "anchors/bundles" / f"anchor23__{cid}__baseline.json").read_bytes())
            replay = ma.nonreciprocal_cycles(b["nodes"], [tuple(e) for e in b["edges"]], d.CYCLE_LENGTHS,
                                             d.MAX_CYCLES, d.BUDGET)
            self.assertEqual(replay, a["selection"])
            self.assertEqual(b["canonical_graph_sha256"], a["baseline_canonical_graph_sha256"])
            for v in a["variants"]:
                job = jobs[f"{cid}#cycle{v['index'] + 1}"]
                self.assertEqual(job["changed_arcs"], v["reversed_arcs"])
                self.assertEqual(job["selection_ref"], {"job_id": f"{cid}#selection", "index": v["index"]})
                self.assertEqual(job["control"], "matched_cycle_reversal")
            self.assertEqual("permutation_seed" in jobs[f"{cid}#global_reversal"], cid == d.PERMUTATION["anchor"])


if __name__ == "__main__":
    unittest.main()

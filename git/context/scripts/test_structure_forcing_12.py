"""Offline tests for Sprint 12 (no network, no container, no model)."""
from __future__ import annotations

import copy
import inspect
import json
import random
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import structure_forcing_12 as sf  # noqa: E402


def toy_graph(n=40, m=90, seed=1):
    rng = random.Random(seed)
    nodes = [f"n{i:02d}" for i in range(n)]
    edges = set()
    while len(edges) < m:
        s, t = rng.sample(nodes, 2)
        edges.add((s, t))
    return nodes, sorted(edges)


class NullRule(unittest.TestCase):
    def setUp(self):
        self.nodes, self.o = toy_graph()
        self.T = self.nodes[:8]

    def check(self, a, r, f, seed=7):
        edges, rec = sf.random_null(self.nodes, self.o, self.T, a, r, f, seed)
        es, os_ = set(edges), set(self.o)
        self.assertEqual(len(es - os_), a)
        self.assertEqual(len(os_ - es), r)
        self.assertTrue({x for e in edges for x in e} <= set(self.nodes))
        added = sorted(es - os_)
        if a:
            recip = sum((t, s) in es for s, t in added)
            self.assertLessEqual(abs(recip - round(f * a)), 1)
        return edges, rec

    def test_counts_reciprocity_and_node_set(self):
        for a, r, f in ((6, 2, 0.0), (6, 2, 0.5), (5, 0, 1.0), (0, 3, 0.0)):
            self.check(a, r, f)

    def test_deterministic_per_seed(self):
        e1, _ = self.check(6, 2, 0.5, seed=sf.null_seed("LT-r01", 1))
        e2, _ = self.check(6, 2, 0.5, seed=sf.null_seed("LT-r01", 1))
        e3, _ = self.check(6, 2, 0.5, seed=sf.null_seed("LT-r01", 2))
        self.assertEqual(e1, e2)
        self.assertNotEqual(e1, e3)

    def test_widening_is_recorded(self):
        inside = sum(1 for s, t in self.o if s in self.T and t in self.T)
        _, rec = self.check(4, inside + 3, 0.0)
        self.assertEqual(rec["remove_pool"]["level"], 1)
        self.assertEqual(rec["widened"][0]["stage"], "remove")
        _, rec0 = self.check(4, 0, 0.0)
        self.assertEqual(rec0["widened"], [])

    def test_refuses_instead_of_dropping(self):
        with self.assertRaises(SystemExit):
            sf.random_null(self.nodes, self.o, self.T, 0, len(self.o) + 1, 0.0, 1)

    def test_added_page_is_a_node(self):
        nodes = self.nodes + ["new_page"]
        edges, _ = sf.random_null(nodes, self.o, self.T + ["new_page"], 8, 0, 0.0, 3)
        self.assertEqual(len(set(edges) - set(self.o)), 8)


class SubgraphRule(unittest.TestCase):
    def test_survivors_added_and_neighbours(self):
        o_edges = [("a", "b"), ("b", "c"), ("c", "d"), ("x", "y"), ("m", "a")]
        receipt = {"mapping": {"m": "a"}, "removed": ["m"], "changed": ["b"], "added": ["new"]}
        v_nodes = ["a", "b", "c", "d", "new", "x", "y", "z"]
        om = sf.mapped_edges(o_edges, receipt["mapping"], v_nodes)
        self.assertNotIn(("a", "a"), om)  # self-loop from the merge dropped
        T = sf.touched_set(receipt)
        self.assertEqual(T, ["a", "b", "new"])
        v_edges = om + [("new", "z"), ("b", "new")]
        S = sf.subgraph_nodes(T, om, v_edges, receipt["added"])
        self.assertEqual(S, ["a", "b", "c", "new", "z"])  # survivor a, added new with its v-neighbour z; not d, x, y
        # the same node set serves v and O' restricted
        self.assertEqual({n for e in sf.induced(om, S) for n in e} <= set(S), True)
        self.assertEqual(sf.subgraph_nodes(T, om, v_edges, receipt["added"]), S)

    def test_v2_node_set_holds_every_edit_endpoint(self):
        om = [("a", "b"), ("b", "c"), ("c", "d"), ("x", "y")]
        ve = om + [("a", "c")]
        null = [e for e in om if e != ("c", "d")] + [("a", "far")]
        S1 = sf.subgraph_nodes(["a"], om, ve, [])
        S2 = sf.subgraph_nodes_v2(["a"], om, ve, [], [null])
        self.assertEqual(S1, ["a", "b"])
        self.assertEqual(S2, ["a", "b", "c", "d", "far"])
        for g in (ve, null):  # every changed edge of v and of the null is inside the common node set
            self.assertTrue(all(s in S2 and t in S2 for s, t in set(g) ^ set(om)))

    def test_edit_magnitude(self):
        om = [("a", "b"), ("b", "c")]
        ve = [("a", "b"), ("c", "b"), ("b", "c"), ("c", "a")]
        mag = sf.edit_magnitude(om, ve, ["a", "c"])
        self.assertEqual((mag["edges_added"], mag["edges_removed"]), (2, 0))
        self.assertEqual(mag["reciprocal_fraction_added"], 0.5)


class Bidirectional(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.o9, cls.mp = sf.rr.load_spectral()

    def test_cycle(self):
        nodes = ["a", "b", "c", "d", "e"]
        cycle = [("a", "b"), ("b", "c"), ("c", "d"), ("d", "e"), ("e", "a")]
        full = sf.bd_identity(self.mp, nodes, sf.mirror(cycle), sf.rr.Q_SWEEP)
        self.assertTrue(full["passes"], full)
        self.assertLess(abs(full["magnetic_lambda1_q0.25"]), 1e-9)
        one_way = [e for e in sf.mirror(cycle) if e != ("b", "a")]
        partial = sf.bd_identity(self.mp, nodes, one_way, sf.rr.Q_SWEEP)
        self.assertFalse(partial["passes"])
        self.assertGreater(partial["worst"], 1e-3)


class PrimaryStatistic(unittest.TestCase):
    def test_null_is_about_uniform_and_shift_is_small(self):
        import numpy as np
        rng = np.random.default_rng(5)
        labels = ["LT"] * 4 + ["MT"] * 4 + ["MR"] * 4
        ps = [sf.permutation_p(rng.normal(size=(12, 6)), labels, n=200, seed=i)["p"] for i in range(200)]
        self.assertAlmostEqual(float(np.mean(ps)), 0.5, delta=0.07)
        self.assertLess(float(np.mean(np.array(ps) < 0.05)), 0.10)
        shifted = rng.normal(size=(12, 6))
        shifted[:4] += 3
        shifted[8:] -= 3
        self.assertLess(sf.permutation_p(shifted, labels, n=500, seed=1)["p"], 0.01)

    def test_drops_undefined_measures(self):
        eps = [{"variant_id": f"v{i}", "arm": a, "z": {m: (None if m == "sinks" and i == 0 else float(i % 3))
                                                       for m in sf.MEASURES}}
               for i, a in enumerate(["LT"] * 2 + ["MT"] * 2 + ["MR"] * 2)]
        res = sf.primary_statistic(eps)
        self.assertEqual(res["measures_dropped"], ["sinks"])
        self.assertIn("p", res)

    def test_standardize_uses_pooled_sd(self):
        d = {m: 1.0 for m in sf.MEASURES}
        nulls = [{m: (0.0 if m == "sinks" else float(k)) for m in sf.MEASURES} for k in range(5)]
        pooled = {m: 2.0 for m in sf.MEASURES}
        pooled["sources"] = 0.0
        z, rule = sf.standardize(d, nulls, pooled)
        self.assertEqual(rule["sinks"], "pooled")
        self.assertEqual(z["sinks"], 0.5)
        self.assertEqual(z["outdegree_gini"], (1.0 - 2.0) / 2.0)  # own null mean, pooled sd
        self.assertIsNone(z["sources"])
        self.assertEqual(rule["sources"], "zero_sd")
        p = sf.pooled_null_sd([nulls, nulls])
        self.assertAlmostEqual(p["outdegree_gini"], (2.5) ** 0.5)
        self.assertEqual(p["sinks"], 0.0)


class Schedule(unittest.TestCase):
    def test_560_cells_unique_stems_seeded(self):
        qs = [f"{i:x}" * 64 for i in range(1, 6)]
        conds = ["s1-no-follow", "s1-follow", "s6-no-follow", "s6-follow"]
        cells = sf.schedule(qs, conds)
        self.assertEqual(len(cells), 560)
        variant = {s: f"v-{i:012x}" for i, s in enumerate(sf.slots())}
        stems = {sf.cell_stem(variant[c["slot"]], c["question_id"], c["condition"], c["replicate"]) for c in cells}
        self.assertEqual(len(stems), 560)
        self.assertEqual(cells, sf.schedule(qs, conds))
        rep1 = [c for c in cells if c["replicate"] == 1]
        self.assertEqual(cells[:280], rep1)
        ordered = sorted(rep1, key=lambda c: (c["slot"], c["question_id"], c["condition"]))
        self.assertNotEqual(rep1, ordered)
        self.assertEqual(len(sf.slots()), 14)

    def test_cell_command_matches_11b_flags(self):
        import restructuring_pilot_11b as p11b
        flags_11b = set(re.findall(r'"(--[a-z-]+)"', inspect.getsource(p11b.answer)))
        a = sf.answering_block()
        q = {"question_id": "q" * 64, "question": "?"}
        cond = {"id": "s1-follow", "searches": 1, "min_distinct_searches": 1, "follow_links": True}
        cmd = sf.cell_command(a, dataset="d", question=q, dest="x.json", ledger="l", future_calls=0,
                              campaign_calls=5600, stop_usd=27.0, condition=cond)
        self.assertEqual({x for x in cmd if x.startswith("--")}, flags_11b)


class Prompts(unittest.TestCase):
    def test_one_sentence_and_role_line_differ(self):
        r0 = sf.R0_V4.read_bytes()
        for arm in sf.STRUCTURE_ARMS:
            text = sf.arm_prompt(r0, arm).decode()
            self.assertIn(sf.ARM_TARGETS[arm], text)
            self.assertNotIn(sf.R0_MORPHOLOGY, text)
            for keep in ("60 minutes", "150 page paths", "there is no checkpoint step",
                         "No analysis tool and no structural or quality feedback is available"):
                self.assertIn(keep, text)
            a, b = r0.decode().split("\n"), text.split("\n")
            self.assertEqual(len(a), len(b))
            self.assertEqual(sum(x != y for x, y in zip(a, b)), 2)  # role line and the one paragraph


class HashGate(unittest.TestCase):
    def test_refuses_modified_plan(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            manifest, templates = d / "m.json", d / "t.json"
            manifest.write_text("{}", encoding="utf-8")
            templates.write_text("{}", encoding="utf-8")
            plan = d / "plan.json"
            plan.write_text(json.dumps({"run_manifest": {"path": str(manifest), "sha256": sf.sha(manifest)},
                                        "arm_templates": {"path": str(templates), "sha256": sf.sha(templates)}}),
                            encoding="utf-8")
            plan.with_suffix(".sha256").write_text(sf.sha(plan) + "  plan.json\n", encoding="ascii")
            sf.check_frozen(plan)
            plan.write_text(plan.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaises(SystemExit):
                sf.check_frozen(plan)
            plan.with_suffix(".sha256").write_text(sf.sha(plan) + "  plan.json\n", encoding="ascii")
            manifest.write_text("{ }", encoding="utf-8")
            with self.assertRaises(SystemExit):
                sf.check_frozen(plan)


class AnswerTables(unittest.TestCase):
    def test_sham_and_arm_rows(self):
        qs = ["q1", "q2", "q3"]
        arms = {"vO": "O", "vS": "S", "v1": "LT", "v2": "LT"}
        cells = {}
        for v in arms:
            for q in qs:
                for c in ("c1", "c2"):
                    for rep in (1, 2):
                        cells[(v, q, c, rep)] = {"correctness": 4.0 + (v == "v1")}
        t = sf.answer_tables(cells, arms, qs, ["correctness"], boot={"replicates": 50, "seed": 1})["correctness"]
        self.assertEqual(t["S - O"]["estimate"], 0.0)
        self.assertAlmostEqual(t["LT - O"]["estimate"], 0.5)


# ------------------------------------------------------------------ Sprint 12b (run sf-12-v2, plan v3)

import structure_forcing_12b as b12  # noqa: E402

V1_RUN_ROOT = sf.run_root()


class AdmissionV2Rules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        vm = sf.rr.read_json(sf.V6)
        sf.dcv.pinned(vm)  # puts the pinned importer (route keys) on sys.path
        cls.route_key = staticmethod(b12.link_helpers()[1])

    def test_protected_lines_set_rule(self):
        base = ["# A\n\n```bash\ndocker run --rm hello\n```\n", "# B\n\n    docker run --rm hello\nWARNING: keep\n"]
        consolidated = ["# A\n\n```bash\ndocker run --rm hello\n```\nSee shared.\n", "# B\nSee shared.\nWARNING: keep\n"]
        self.assertEqual(b12.protected_missing_v3(base, consolidated), [])  # duplicate collapsed: passes
        deleted = ["# A\n\nRun the image.\n", "# B\nWARNING: keep\n"]
        self.assertIn("docker run --rm hello", b12.protected_missing_v3(base, deleted))  # true deletion: fails

    def test_unresolved_link_target_rule(self):
        rk = self.route_key
        baseline = [{"page": "content/a/x.md", "target": "/reference/cli/dockerd/#daemon-socket-option"},
                    {"page": "content/a/x.md", "target": "../missing.md"}]
        moved = [{"page": "content/b/shared.md", "target": "/reference/cli/dockerd/#daemon-socket-option"},
                 {"page": "content/a/z.md", "target": "../missing.md"},
                 {"page": "content/deep/er/y.md", "target": "../../missing.md"}]
        self.assertEqual(b12.introduced_unresolved_v3(moved, baseline, rk), [])  # moved, not introduced
        broken = [{"page": "content/a/x.md", "target": "/manuals/engine/nowhere/"},
                  {"page": "content/b/y.md", "target": "/reference/cli/dockerd/#another-anchor"}]
        self.assertEqual(len(b12.introduced_unresolved_v3(broken, baseline, rk)), 2)  # newly broken: fails

    @unittest.skipUnless((V1_RUN_ROOT / "LT-r01/exports/preservation-review/review.json").exists(),
                         "Sprint 12 artifacts not present")
    def test_sprint12_lt_packets_pass_v2_and_fail_v1(self):
        vm = sf.rr.read_json(sf.V6)
        base = b12.Baseline(sf.dcv.pinned(vm), vm)
        for name in ("LT-r01", "LT-r02", "LT-r03", "LT-r04"):
            ns = V1_RUN_ROOT / name
            review = sf.rr.read_json(ns / "exports/preservation-review/review.json")
            tree = Path(sf.rr.read_json(ns / "host/run-state.json")["terminal_tree"])
            pages, _, _ = sf.rr.tree_graph(base.gm, tree, sf.dcv.scope_of(vm))
            self.assertEqual(review["baseline_unresolved_internal_link_count"], len(base.unresolved))
            self.assertGreater(len(review["introduced_unresolved_internal_links"]), 0)  # failed v1
            self.assertEqual(b12.introduced_unresolved_v3(review["unresolved_internal_links"], base.unresolved,
                                                          self.route_key), [])
            self.assertEqual(b12.protected_missing_v3(base.texts, [p.text for p in pages]), [])


class ResidualTerminal(unittest.TestCase):
    def test_terminal_source(self):
        self.assertEqual(b12.terminal_source({"status": "budget_stop", "budget_stop_residual": "abc", "flags": []}),
                         "residual")
        self.assertIsNone(b12.terminal_source({"status": "failed", "residual_checkpoint": "abc",
                                               "flags": ["infrastructure_stop:usage_limit"]}))
        self.assertIsNone(b12.terminal_source({"status": "budget_stop", "budget_stop_residual": "abc",
                                               "flags": ["infrastructure_stop:usage_limit"]}))
        self.assertEqual(b12.terminal_source({"status": "completed", "terminal_tree": "/t", "flags": []}),
                         "run_state")
        self.assertIsNone(b12.terminal_source({"status": "budget_stop", "flags": []}))

    def test_runtime_counter_has_no_path_limit(self):
        sys.path.insert(0, str(sf.CONTEXT / "sandbox"))
        import codex_events
        self.assertIsNone(b12.LIMITS["edits"])
        self.assertEqual(b12.LIMITS["tokens"], 30_000_000)
        counter = codex_events.EventCounter(dict(b12.LIMITS))
        counter.check(edits=10 ** 6, elapsed=10)  # no edit cap: never raises
        counter.tokens = 30_000_001
        with self.assertRaises(codex_events.LimitReached):
            counter.check()

    @unittest.skipUnless(b12.manifest_file().exists(), "base manifest not prepared yet")
    def test_prepared_manifest_has_no_path_limit(self):
        m = sf.rr.read_json(b12.manifest_file())
        self.assertIsNone(m["limits"]["edits"])
        self.assertEqual(m["limits"]["tokens"], 30_000_000)
        self.assertTrue(m["stopping"]["budget_stop_terminal_eligible_for_grading"])


class ProfileSwitch(unittest.TestCase):
    def test_v1_paths_unchanged_v2_separate(self):
        self.assertEqual(sf.RUN_ID, "sf-12-v1")
        self.assertTrue(sf.PLAN.name.startswith("structure-forcing-12-plan-v2-"))
        self.assertEqual(sf.run_root().name, "sf-12-v1")
        edited = sf.rr.EDITED
        with b12.profile():
            self.assertEqual(sf.RUN_ID, "sf-12-v2")
            self.assertEqual(sf.run_root().name, "sf-12-v2")
            self.assertEqual(sf.PLAN, b12.PLAN)
            self.assertNotEqual(sf.ANSWERS, sf.rr.ROOT / "artifacts/sf-12-v1-answers")
            self.assertIn("AS", sf.rr.EDITED)
        self.assertEqual(sf.RUN_ID, "sf-12-v1")
        self.assertEqual(sf.run_root().name, "sf-12-v1")
        self.assertEqual(sf.ANSWERS, sf.rr.ROOT / "artifacts/sf-12-v1-answers")
        self.assertEqual(sf.rr.EDITED, edited)
        self.assertNotEqual(b12.PLAN, sf.PLAN)
        self.assertNotEqual(b12.ANSWERS, sf.ANSWERS)


class PromptsV2(unittest.TestCase):
    PATHS = ["content/manuals/engine/network/", "content/manuals/engine/daemon/proxy.md"]

    def test_arm_prompts_differ_only_as_planned(self):
        for arm in sf.STRUCTURE_ARMS:
            v1 = b12.v1_prompt_path(arm).read_bytes()
            for area in b12.AREAS:
                text = b12.arm_prompt_v2(v1, arm, area, self.PATHS).decode()
                self.assertNotIn("150", text)
                self.assertNotIn("page paths", text)
                self.assertIn(b12.V2_LIMITS, text)
                paragraphs = text.rstrip("\n").split("\n\n")
                self.assertEqual(paragraphs[3], b12.AREA_PARAGRAPH.format(paths=", ".join(self.PATHS)))
                record = b12.record_paragraph(b12.arm_record_template(arm, area, self.PATHS), b12.ARM_RECORD_NOTE)
                self.assertTrue(text.endswith(record + "\n"))
                rest = "\n\n".join(paragraphs[:3] + paragraphs[4:]) + "\n"
                rest = rest.replace("\n\n" + record + "\n", "\n").replace(b12.V2_LIMITS, b12.V1_LIMITS)
                if arm == "MT":
                    self.assertIn(b12.MT_V2, text)
                    rest = rest.replace(b12.MT_V2, b12.MT_V1)
                self.assertEqual(rest.encode(), v1)  # nothing else changed
                self.assertIn(f'"arm": "{arm}"', text)
                self.assertIn(f'"area_id": "{area}"', text)

    def test_selection_prompt_is_arm_and_corpus_neutral(self):
        text = b12.selection_prompt().decode()
        self.assertIn(b12.SELECTION_TASK, text)
        self.assertIn(b12.V2_LIMITS, text)
        for banned in ["Docker", "docker", "content/", "manuals", "LT", "MT", "MR", "leaf", "shared", "table of",
                       "related-page", "hub", "150"] + list(sf.ARM_TARGETS.values()):
            self.assertNotIn(banned, text, banned)
        self.assertNotRegex(text, r"\blinks?\b")


class Records(unittest.TestCase):
    def setUp(self):
        self.schemas = b12.record_schemas()
        self.session = {"schema": "edit_session_record.v1", "arm": "LT", "area_id": "A2",
                        "area_paths": ["content/x/"], "pages_in_area": 3, "pages_edited": ["/work/content/x/a.md"],
                        "pages_created": ["content/x/hub.md"], "pages_removed": [], "done": True,
                        "not_done_reason": None, "edits": [{"pages": ["content/x/a.md"], "reason": "r",
                                                            "moved_from": []}],
                        "preservation_concerns": [], "elapsed_minutes_at_stop": 31.5}
        self.selection = {"schema": "area_selection.v1", "excluded_candidates": [], "method": "m", "areas": [
            {"area_id": f"A{i}", "paths": [f"content/a{i}/"], "page_count": 2, "rationale": "r",
             "navigation_problem": "n", "fits_one_session": True} for i in range(1, 5)]}

    def wrap(self, record):
        return "Done.\n\n```json\n" + json.dumps(record) + "\n```\n"

    def test_valid_records_pass(self):
        self.assertEqual(b12.extract_record(self.wrap(self.session), "edit_session_record.v1", self.schemas)[0],
                         "valid")
        self.assertEqual(b12.extract_record(self.wrap(self.selection), "area_selection.v1", self.schemas)[0], "valid")

    def test_invalid_records(self):
        cases = {"missing-message": None, "no-block": "Done, no record.",
                 "two-blocks": self.wrap(self.session) + self.wrap(self.session),
                 "malformed": "```json\n{\"schema\": \"edit_session_record.v1\",}\n```",
                 "wrong-schema": self.wrap(dict(self.session, schema="area_selection.v1")),
                 "missing-field": self.wrap({k: v for k, v in self.session.items() if k != "done"}),
                 "extra-field": self.wrap(dict(self.session, note="x"))}
        for label, message in cases.items():
            status, _, why = b12.extract_record(message, "edit_session_record.v1", self.schemas)
            self.assertEqual(status, "record_invalid", label)
            self.assertTrue(why, label)
        three = dict(self.selection, areas=self.selection["areas"][:3])
        self.assertEqual(b12.extract_record(self.wrap(three), "area_selection.v1", self.schemas)[0], "record_invalid")

    def test_transcript_final_message(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "transcript.jsonl"
            rows = [{"codex_event": json.dumps({"type": "item.completed", "item": {"type": "agent_message",
                                                                                  "text": "first"}})},
                    {"tool": "x"}, {"codex_event": "not json"},
                    {"codex_event": json.dumps({"type": "item.completed", "item": {"type": "agent_message",
                                                                                  "text": "last"}})}]
            t.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            self.assertEqual(b12.final_message(t), "last")
            self.assertIsNone(b12.final_message(Path(d) / "absent.jsonl"))

    def test_diff_check_flags_undeclared_and_outside_area(self):
        review = {"changed": ["content/x/a.md", "content/y/b.md"], "added": ["content/x/hub.md", "content/z/n.md"],
                  "removed": []}
        out = b12.session_record_check(self.session, review, ["content/x/"])
        self.assertEqual(out["pages_edited"]["undeclared"], ["content/y/b.md"])  # /work/ prefix normalized
        self.assertEqual(out["pages_created"]["undeclared"], ["content/z/n.md"])
        self.assertEqual(out["outside_area"]["changed"], ["content/y/b.md"])
        self.assertEqual(out["outside_area"]["added_outside_area_paths"], ["content/z/n.md"])
        self.assertIsNone(b12.session_record_check(None, review, ["content/x/"])["declared"])

    def test_selection_host_checks(self):
        rels = ["content/a1/p.md", "content/a2/p.md", "content/a3/p.md", "content/a4/p.md", "content/a4/q.md"]
        ok = b12.selection_host_checks(self.selection, rels)
        self.assertTrue(ok["passes"])
        self.assertEqual(ok["host_page_count"]["A4"], 2)
        bad = copy.deepcopy(self.selection)
        bad["areas"][1]["paths"] = ["content/a1/"]  # A2 duplicates A1: overlap, and content/a2 is in no area
        out = b12.selection_host_checks(bad, rels)
        self.assertFalse(out["passes"])
        self.assertTrue(any(p.startswith("overlapping-pages") for p in out["problems"]))
        empty = copy.deepcopy(self.selection)
        empty["areas"][3]["paths"] = ["content/none/"]
        self.assertIn("empty-area:A4", b12.selection_host_checks(empty, rels)["problems"])


class HashGateV3(unittest.TestCase):
    def test_refuses_any_changed_frozen_file(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            files = {k: d / f"{k}.json" for k in ("base", "A1", "templates", "bind", "schemas", "prereg")}
            for f in files.values():
                f.write_text("{}", encoding="utf-8")
            plan = d / "plan.json"
            plan.write_text(json.dumps({
                "run_manifests": {k: {"path": str(files[k]), "sha256": sf.sha(files[k])} for k in ("base", "A1")},
                "arm_templates": {"path": str(files["templates"]), "sha256": sf.sha(files["templates"]),
                                  "bindings": {"A1": {"path": str(files["bind"]), "sha256": sf.sha(files["bind"])}}},
                "record_schemas": {"path": str(files["schemas"]), "sha256": sf.sha(files["schemas"])},
                "preregistration": {"path": str(files["prereg"]), "sha256": sf.sha(files["prereg"])}}),
                encoding="utf-8")
            plan.with_suffix(".sha256").write_text(sf.sha(plan) + "  plan.json\n", encoding="ascii")
            b12.check_frozen(plan)
            for f in files.values():
                f.write_text("{ }", encoding="utf-8")
                with self.assertRaises(SystemExit):
                    b12.check_frozen(plan)
                f.write_text("{}", encoding="utf-8")
            b12.check_frozen(plan)
            plan.write_text(plan.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaises(SystemExit):
                b12.check_frozen(plan)


class SessionLog(unittest.TestCase):
    def test_chains_ignore_end_rows_and_follow_reruns(self):
        plan = {"edit_order": {"order": []}}
        sessions = [{"namespace": "LT-r02", "arm": "LT", "replicate": 2, "area": "A2", "role": "realization",
                     "of": None, "event": "launch"},
                    {"namespace": "LT-r02", "event": "end", "status": "failed"},
                    {"namespace": "LT-r06", "arm": "LT", "replicate": 6, "area": "A2", "role": "rerun",
                     "of": "LT-r02", "event": "launch"},
                    {"namespace": "LT-r06", "event": "end", "status": "completed"}]
        rows = b12.chains(plan, Path("."), sessions)["LT"]
        self.assertEqual(rows[1], ("realization", "A2", ["LT-r02", "LT-r06"]))
        self.assertEqual(len(rows), 4)


if __name__ == "__main__":
    unittest.main()

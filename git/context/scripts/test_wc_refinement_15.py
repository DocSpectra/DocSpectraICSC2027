"""Offline tests for Sprint 15 (toy data only; no containers, no network, no model)."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wc_refinement_15 as wc  # noqa: E402
import structure_forcing_12 as sf  # noqa: E402
import lt_locality_13 as ln  # noqa: E402
from test_structure_forcing_12 import toy_graph  # noqa: E402

BASE = b"Role: test arm prompt.\n\nTarget paragraph.\n"


def page(doc_id, rel, text):
    return SimpleNamespace(doc_id=doc_id, rel_path=rel, text=text)


class Prompts(unittest.TestCase):
    def test_r0_never_sees_trunk_and_lt_needs_it(self):
        with self.assertRaises(wc.Refused):
            wc.session_prompt(BASE, "R0", "pass", "A01", ["a/"], trunk_inventory=[{"x": 1}])
        with self.assertRaises(wc.Refused):
            wc.session_prompt(BASE, "LT", "pass", "A01", ["a/"])
        r0 = wc.session_prompt(BASE, "R0", "pass", "A01", ["a/"]).decode()
        self.assertNotIn("trunk inventory", r0.lower())

    def test_kinds_differ_only_by_their_paragraph(self):
        inv = [{"trunk_id": "T01", "topic": "t", "pages": ["a/x.md"], "proposed_page": "a/t.md"}]
        p = wc.session_prompt(BASE, "LT", "pass", "A01", ["a/"], inv).decode()
        r = wc.session_prompt(BASE, "LT", "resume", "A01", ["a/"], inv).decode()
        self.assertTrue(p.startswith(BASE.decode().rstrip("\n")))
        self.assertIn(wc.RESUME_PARAGRAPH, r)
        self.assertNotIn(wc.RESUME_PARAGRAPH, p)
        self.assertEqual(r.replace(wc.RESUME_PARAGRAPH + "\n\n", "").replace('"kind": "resume"', '"kind": "pass"'), p)
        c = wc.session_prompt(BASE, "R0", "reconcile", "ALL", []).decode()
        self.assertIn(wc.RECONCILE_PARAGRAPH, c)
        with self.assertRaises(wc.Refused):
            wc.session_prompt(BASE, "R0", "reconcile", "A01", [])
        self.assertEqual(p.count("90 minutes"), 1)

    def test_record_template_round_trips_through_the_schema(self):
        import jsonschema
        schemas = wc.record_schemas()
        rec = {"schema": "wc_session_record.v1", "arm": "LT", "kind": "pass", "area_id": "A01", "area_paths": ["a/"],
               "pages_in_area": 3, "pages_edited": ["a/x.md"], "pages_created": [], "pages_removed": [], "done": True,
               "not_done_reason": None, "provenance": [{"page": "a/t.md", "sources": ["a/x.md"]}],
               "preservation_concerns": [], "elapsed_minutes_at_stop": 12}
        jsonschema.Draft202012Validator(schemas["schemas"]["wc_session_record.v1"]).validate(rec)
        msg = "done\n\n```json\n" + json.dumps(rec) + "\n```\n"
        self.assertEqual(wc.sfb.extract_record(msg, "wc_session_record.v1", schemas)[0], "valid")
        bad = dict(rec, provenance=[{"page": "a/t.md"}])
        self.assertEqual(wc.sfb.extract_record("```json\n" + json.dumps(bad) + "\n```", "wc_session_record.v1",
                                               schemas)[0], "record_invalid")

    def test_survey_prompt_and_partition_schema_bounds(self):
        text = wc.survey_prompt().decode()
        self.assertIn("between 15 and 25 areas", text)
        self.assertIn("Do not edit any file", text)
        schema = wc.record_schemas()["schemas"]["wc_area_partition.v1"]
        self.assertEqual((schema["properties"]["areas"]["minItems"], schema["properties"]["areas"]["maxItems"]), (15, 25))


class Partition(unittest.TestCase):
    rels = ["a/x.md", "a/y.md", "b/z.md", "b/c/w.md", "r.md"]

    def rec(self, areas):
        return {"schema": "wc_area_partition.v1", "trunk_inventory": [], "method": "",
                "areas": [{"area_id": f"A{i:02d}", "paths": p, "page_count": 1, "rationale": ""}
                          for i, p in enumerate(areas, 1)]}

    def test_valid_overlap_and_uncovered(self):
        ok = wc.partition_host_checks(self.rec([["a/"], ["b/"], ["r.md"]]), self.rels)
        self.assertTrue(ok["passes"])
        bad = wc.partition_host_checks(self.rec([["a/"], ["b/", "b/c/"]]), self.rels)
        self.assertIn("uncovered-pages:1", bad["problems"])
        over = wc.partition_host_checks(self.rec([["a/", "b/c/"], ["b/"], ["r.md"]]), self.rels)
        self.assertIn("overlapping-pages:1", over["problems"])

    def test_cover_prefixes_complements_taken_areas(self):
        taken = ["b/c/"]
        cover = wc.cover_prefixes(self.rels, taken)
        rec = self.rec([cover, taken])
        self.assertTrue(wc.partition_host_checks(rec, self.rels)["passes"], cover)


class Queue(unittest.TestCase):
    def test_alternation_and_seeded_order(self):
        order = wc.area_order(["A01", "A02", "A03", "A04"])
        self.assertEqual(order, wc.area_order(["A04", "A03", "A02", "A01"]))
        items = wc.planned_items(order)
        firsts = [items[i]["arm"] for i in range(0, 8, 2)]
        self.assertEqual(firsts, ["LT", "R0", "LT", "R0"])
        self.assertEqual([i["kind"] for i in items[-2:]], ["reconcile", "reconcile"])

    def rows(self):
        return [{"event": "launch", "arm": "LT", "kind": "pass", "area": "A01", "namespace": "LT-r01"},
                {"event": "end", "arm": "LT", "kind": "pass", "area": "A01", "namespace": "LT-r01", "tokens": 10,
                 "seconds": 5, "action": "advance", "head_after": "h1", "head_after_path": "/t1", "resume_due": True},
                {"event": "launch", "arm": "R0", "kind": "pass", "area": "A01", "namespace": "R0-r01"},
                {"event": "end", "arm": "R0", "kind": "pass", "area": "A01", "namespace": "R0-r01", "tokens": 7,
                 "seconds": 3, "action": "rollback", "head_after": None, "head_after_path": None}]

    def test_resume_first_then_plan(self):
        items = wc.planned_items(["A01", "A02"])
        item, skips = wc.next_item(self.rows(), items)
        self.assertEqual(item, {"arm": "LT", "kind": "resume", "area": "A01"})
        rows = self.rows() + [{"event": "end", "arm": "LT", "kind": "resume", "area": "A01", "namespace": "LT-r02",
                               "action": "rollback", "head_after": "h1", "head_after_path": "/t1"}]
        item, _ = wc.next_item(rows, items)
        self.assertEqual(item, {"arm": "R0", "kind": "pass", "area": "A02"})
        st = wc.ledger_state(rows)["arms"]
        self.assertEqual((st["LT"]["head"], st["R0"]["head"]), ("h1", None))  # rollbacks keep the head

    def test_budget_skips_and_refusals(self):
        items = wc.planned_items(["A01", "A02"])
        item, skips = wc.next_item(self.rows(), items, budget={"tokens": 9, "wall_seconds": 10 ** 9})
        self.assertEqual(item["arm"], "R0")
        self.assertEqual(skips[0]["reason"], "arm-budget:tokens")
        with self.assertRaises(wc.Refused):
            wc.next_item(self.rows()[:1], items)
        with self.assertRaises(wc.Refused):
            wc.next_item(self.rows() + [{"event": "halt", "arm": "LT", "kind": "pass", "area": "A02",
                                         "reason": "infra"}], items)

    def test_survey_tokens_count_to_lt(self):
        rows = [{"event": "end", "arm": "WS", "kind": "survey", "area": "ALL", "tokens": 100, "seconds": 2,
                 "action": "survey"}]
        self.assertEqual(wc.ledger_state(rows)["arms"]["LT"]["tokens"], 100)


class Chaining(unittest.TestCase):
    def test_seeded_copy_patches_and_restores(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            start = tmp / "start"
            (start / "a").mkdir(parents=True)
            (start / "a/x.md").write_text("x\n", encoding="utf-8")
            manifest = tmp / "m.json"
            manifest.write_text(json.dumps({"output_root": str(tmp)}), encoding="utf-8")
            original = wc.dcv.copy_baseline
            with wc.seeded_copy(start) as expected:
                wc.dcv.copy_baseline(SimpleNamespace(manifest=manifest, dest=tmp / "ns/editor/source"))
                self.assertEqual(wc.tree_id(tmp / "ns/editor/source"), expected)
                with self.assertRaises(ValueError):
                    wc.dcv.copy_baseline(SimpleNamespace(manifest=manifest, dest=tmp.parent / "outside"))
            self.assertIs(wc.dcv.copy_baseline, original)
            (start / "a/x.md").write_text("y\n", encoding="utf-8")
            (start / "a/n.md").write_text("n\n", encoding="utf-8")
            diff = wc.pass_diff(tmp / "ns/editor/source", start)
            self.assertEqual(diff, {"added": ["a/n.md"], "removed": [], "changed": ["a/x.md"]})
        finally:
            shutil.rmtree(tmp)

    def test_authorization_gate(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            with self.assertRaises(wc.Refused):
                wc.authorized(tmp, 2)
            (tmp / "host-private").mkdir()
            (tmp / "host-private/authorization.json").write_text(json.dumps(
                {"entries": [{"point": 1, "author_yes": "yes"}]}), encoding="utf-8")
            self.assertEqual(wc.authorized(tmp, 1)["point"], 1)
            with self.assertRaises(wc.Refused):
                wc.authorized(tmp, 2)
        finally:
            shutil.rmtree(tmp)


LINE = "This sentence is long enough to count as a distinctive unit of content."


class Provenance(unittest.TestCase):
    def setUp(self):
        self.base = [page("b1", "a/one.md", f"# One\n\n{LINE}\n"), page("b2", "a/two.md", "# Two\n\nShort line.\n"),
                     page("b3", "a/three.md", "# Three\n\nAnother unrelated paragraph that is quite long indeed.\n")]
        self.var = [self.base[0], page("b2", "a/two.md", "# Two\n\nShort line.\n\nSee [trunk](t.md).\n"),
                    page("t1", "a/t.md", f"# Trunk\n\n{LINE}\n"), self.base[2]]

    def test_host_alignment_and_declarations(self):
        d = wc.derived_sources(self.base, self.var, {"a/t.md": ["a/one.md", "a/three.md"]})
        self.assertEqual(d["t1"]["derived"], ["b1"])
        self.assertEqual(d["t1"]["unconfirmed"], ["b3"])
        self.assertEqual(d["b2"]["derived"], [])
        rows = wc.host_provenance_check({"a/t.md": ["a/one.md", "a/three.md"]}, d)
        self.assertEqual((rows[0]["confirmed"], rows[0]["unconfirmed"]), (["b1"], ["b3"]))

    def test_credit_adds_derived_page_to_the_support_unit(self):
        receipt = {"mapping": {}}
        d = wc.derived_sources(self.base, self.var)
        eq = wc.credited_equivalence(self.base, self.var, receipt, d)
        self.assertEqual(eq["b1"], ["b1", "t1"])
        self.assertEqual(eq["b3"], ["b3"])

    def test_boilerplate_is_not_credit(self):
        common = "This boilerplate line is long enough but appears on many pages."
        base = [page(f"b{i}", f"a/{i}.md", f"{common}\n") for i in range(5)]
        var = base + [page("t", "a/t.md", f"{common}\n")]
        self.assertEqual(wc.derived_sources(base, var)["t"]["derived"], [])


class Controls(unittest.TestCase):
    def setUp(self):
        self.nodes, self.o = toy_graph(n=60, m=140, seed=5)
        self.new = ["n60", "n61"]
        self.nodes = sorted(self.nodes + self.new)
        added = {("n00", "n60"), ("n01", "n60"), ("n02", "n61"), ("n20", "n61"), ("n03", "n25"), ("n60", "n04")}
        self.v = sorted(set(self.o) | added)
        self.vctx = {"o_mapped": self.o, "nodes": self.nodes, "v_edges": self.v}
        self.buckets = {"A01": {"T": ["n00", "n01", "n02", "n03", "n60"], "added_pages": ["n60"], "a": 4, "r": 0,
                                "f": 0.0, "added_edges": []},
                        "A02": {"T": ["n20", "n61"], "added_pages": ["n61"], "a": 2, "r": 1, "f": 0.0,
                                "added_edges": []}}
        self.area_nodes = {"A01": [f"n{i:02d}" for i in range(0, 20)] + ["n60"],
                           "A02": [f"n{i:02d}" for i in range(20, 40)] + ["n61"]}

    def test_counts_per_type(self):
        for t in ("LN", "PC", "RN"):
            edges, rec = wc.compose_null(t, self.vctx, self.buckets, "LT", 1, self.area_nodes)
            self.assertEqual(rec["planned_added"], 6)
            self.assertEqual((rec["added_total"], rec["removed_total"]), (6, 1), t)

    def test_ln_additions_stay_in_each_bucket_region(self):
        edges, rec = wc.compose_null("LN", self.vctx, self.buckets, "LT", 1, self.area_nodes)
        added = set(edges) - set(self.o)
        regions = [set(ln.locality_region(x["T"], self.o, self.v, x["added_pages"])) for x in self.buckets.values()]
        self.assertTrue(all(any(s in K and t in K for K in regions) for s, t in added))

    def test_pc_matches_new_pages_and_inbound_counts(self):
        edges, rec = wc.compose_null("PC", self.vctx, self.buckets, "LT", 2, self.area_nodes)
        pcs = {b: x["pc"] for b, x in rec["buckets"].items()}
        self.assertEqual(pcs["A01"]["inbound_per_page"], {"n60": 2})  # n00->n60, n01->n60 in v
        self.assertEqual(pcs["A02"]["inbound_per_page"], {"n61": 2})
        es = set(edges)
        for b, x in rec["buckets"].items():
            self.assertEqual(x["pc"]["placed"], sum(x["pc"]["inbound_per_page"].values()))
        for p, area in (("n60", "A01"), ("n61", "A02")):
            inbound = [s for s, t in es if t == p and s in self.area_nodes[area]]
            self.assertGreaterEqual(len(inbound), 2)

    def test_deterministic_and_salted(self):
        a, _ = wc.compose_null("LN", self.vctx, self.buckets, "LT", 1, self.area_nodes)
        b, _ = wc.compose_null("LN", self.vctx, self.buckets, "LT", 1, self.area_nodes)
        c, _ = wc.compose_null("LN", self.vctx, self.buckets, "LT", 2, self.area_nodes)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertNotEqual(wc.null_seed("LN", "LT", "A01", 1), wc.null_seed("RN", "LT", "A01", 1))
        self.assertNotIn(wc.SALTS["LN"], (ln.LN_SALT, sf.NULL_SEED_SALT))

    def test_refusal_is_reported_not_computable(self):
        buckets = {"ALL": {"T": ["n40"], "added_pages": [], "a": 500, "r": 0, "f": 0.0, "added_edges": []}}
        with self.assertRaises(wc.Refused) as cm:
            wc.compose_null("LN", self.vctx, buckets, "LT", 1, self.area_nodes)
        self.assertIn("null-not-computable:LN:LT:ALL:k1", str(cm.exception))

    def test_area_buckets_attribute_by_first_session(self):
        receipt = {"mapping": {}, "removed": [], "added": ["n60"], "changed": ["n00"]}
        vctx = {"o_mapped": self.o, "v_edges": sorted(set(self.o) | {("n00", "n60")}), "touched": ["n00", "n60"],
                "receipt": receipt, "edit_area": {"n00": "A01", "n60": "A02"}}
        buckets, un = wc.area_buckets(vctx)
        self.assertEqual(buckets["A01"]["a"], 1)  # the edge lives in its source page
        self.assertEqual(buckets["A02"]["added_pages"], ["n60"])
        self.assertEqual(un["edges"], [])


class SignFlip(unittest.TestCase):
    def test_exact_values(self):
        res = wc.sign_flip_test([1.0, 1.0, 1.0])
        self.assertAlmostEqual(res["p"], 2 / 8)
        self.assertEqual(res["minimum_p"], 2 / 8)
        self.assertAlmostEqual(wc.sign_flip_test([1.0, 1.0, 1.0], two_sided=False)["p"], 1 / 8)
        self.assertEqual(wc.sign_flip_test([1.0, None, -1.0])["n"], 2)
        self.assertEqual(wc.sign_flip_test([0.5] * 21)["assignments"], 2 ** 21)


class Tier2(unittest.TestCase):
    def test_ranked_scores_identity(self):
        eq = {"a": ["a"], "b": ["b"]}
        s = wc.ranked_scores(["x", "a", "y", "b"], ["a", "b"], eq)
        self.assertEqual(s["recall@5"], 1.0)
        self.assertAlmostEqual(s["rr"], 0.5)
        import math
        self.assertAlmostEqual(s["ndcg@10"], (1 / math.log2(3) + 1 / math.log2(5)) / (1 + 1 / math.log2(3)))

    def test_credited_unit_counts_once(self):
        eq = {"a": ["a", "t"], "b": ["b"]}
        s = wc.ranked_scores(["t", "a", "z", "z2", "z3", "b"], ["a", "b"], eq)
        self.assertEqual(s["recall@5"], 0.5)
        self.assertEqual(s["rr"], 1.0)

    def vctx(self):
        return {"equivalence": {"s1": ["s1", "t1"]}, "added_edges": [("p", "t1")], "edit_area": {"t1": "A01", "p": "A01"},
                "nodes": ["p", "q", "s1", "t1"]}

    def trace(self):
        return {"steps": [{"call": 1, "action": {"action": "search", "query": "q"}},
                          {"call": 2, "action": {"action": "read", "doc_id": "q"}},
                          {"call": 3, "action": {"action": "search", "query": "q2"}},
                          {"call": 4, "action": {"action": "follow", "source_doc_id": "p", "doc_id": "t1"}},
                          {"call": 5, "action": {"action": "follow", "source_doc_id": "t1", "doc_id": "q"}}],
                "answer": {"text": "a", "citations": [{"doc_id": "t1", "start_line": 1, "end_line": 2}]}}

    def test_navigation_metrics(self):
        m = wc.navigation_metrics(self.trace(), ["s1"], self.vctx())
        self.assertEqual((m["searches_to_first_support"], m["follows"], m["follows_landing_on_support"],
                          m["follows_over_editor_added_edges"]), (2, 2, 1, 1))
        self.assertTrue(m["support_cited"])
        self.assertEqual(m["share_follows_over_editor_added_edges"], 0.5)

    def test_trace_fields(self):
        f = wc.annotate_trace(self.trace(), self.vctx())
        self.assertEqual([x["editor_added_edge"] for x in f["follows"]], [True, False])
        pages = {p["doc_id"]: p for p in f["pages"]}
        self.assertEqual((pages["t1"]["edited"], pages["t1"]["edit_area"], pages["t1"]["cited"]), (True, "A01", True))
        self.assertFalse(pages["q"]["edited"])


class Quotes(unittest.TestCase):
    def test_survival(self):
        vctx = {"var_list": [page("t1", "a/t.md", "Some [moved](x.md) quote   text here."), page("b9", "b.md", "")],
                "equivalence": {"b1": ["t1"], "b2": []}}
        els = wc.quote_elements({"core": [{"element_id": "C1", "page_id": "b1", "exact_quote": "Some moved quote text"},
                                          {"element_id": "C2", "page_id": "b2", "exact_quote": "gone"}]})
        res = wc.quote_survival(els, vctx)
        self.assertEqual((res["elements"], res["in_credited_page"], res["anywhere"]), (2, 1, 1))


class Pins(unittest.TestCase):
    def test_retriever_configs_have_ten_items(self):
        self.assertEqual(wc.RETRIEVERS["lexical"]["config"]["top_k"], 10)
        self.assertEqual(wc.RETRIEVERS["embedding"]["config"]["top_k"], 10)
        g = wc.RETRIEVERS["graph_expand"]["config"]
        self.assertEqual(g["seed_top_k"] + g["neighbor_budget"], 10)

    def test_runner_sources_match_pins(self):
        for name, r in wc.RETRIEVERS.items():
            self.assertEqual(wc.sha(wc.rr.RA / "tests" / r["provider"] / "runner.py"), r["runner_sha256"], name)


if __name__ == "__main__":
    unittest.main()

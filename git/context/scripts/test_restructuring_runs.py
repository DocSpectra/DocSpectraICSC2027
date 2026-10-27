"""Sprint 10 fixture checks: no network, no model, never loads Docker questions."""
import json
import random
import shutil
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import docker_corpus_variants as dcv
import restructuring_runs as rr

EXPORTER = Path("C:/Users/rocke/Documents/ResearchProjects/DocSpectraCIKM2026/git/DocSpectraCIKM2026/docspectra_importer/retrieval_arena_export.py")
V5 = rr.CONTEXT / "evidence/docker-corpus-variant-input-v5-structural-target-2026-10-07.json"

PAGES = {
    "index.md": "# Fixture home\nStart with [alpha](a.md), [beta](b.md) and [gamma](c.md).\n",
    "a.md": "# Alpha\nAlpha installs the fixture engine.\nRun `fixture install`.\nAlpha warns about disk space.\n"
            "See [beta](b.md) and [delta](d.md).\n",
    "b.md": "# Beta\nBeta configures the fixture engine.\nBack to [alpha](a.md); next [gamma](c.md).\n",
    "c.md": "# Gamma\nGamma explains networking.\nHome is [index](index.md); see [epsilon](e.md).\n",
    "d.md": "# Delta\nDelta covers storage drivers.\nDelta notes the overlay limit.\nDelta lists volume flags.\n"
            "Read [epsilon](e.md) and [phi](f.md).\n",
    "e.md": "# Epsilon\nEpsilon covers bind mounts.\nEpsilon warns about permissions.\nBack to [delta](d.md).\n",
    "f.md": "# Phi\nPhi covers logging drivers.\nPhi shows the json-file option.\nSee [gamma](c.md) and [chi](g.md).\n",
    "g.md": "# Chi\nChi is linked only from phi.\nChi documents log rotation.\n",
    "orphan.md": "# Orphan\nOrphan holds a unique release caveat.\n",
}


class Runs(unittest.TestCase):
    def setUp(self):
        self.root = rr.ROOT / "artifacts" / ("restructuring-run-test-" + uuid.uuid4().hex)
        self.source = self.root / "source"
        self.source.mkdir(parents=True)
        for name, text in PAGES.items():
            (self.source / name).write_text(text, encoding="utf-8", newline="\n")
        self.variant_manifest = self.root / "variant-manifest.json"
        self.variants = self.root / "variants"
        rr.quiet(dcv.freeze, SimpleNamespace(source=self.source, exporter=EXPORTER, commit="fixture",
                                             variants=self.variants, manifest=self.variant_manifest))
        self.gm = dcv.pinned(rr.read_json(self.variant_manifest))
        self.ids = {p.rel_path: p.doc_id for p in dcv.pages(self.gm, self.source)}
        self.manifest = self.make_manifest("fixture-run")

    def tearDown(self):
        if (rr.ROOT / "artifacts").resolve() not in self.root.resolve().parents:
            raise RuntimeError("unsafe-test-cleanup")
        shutil.rmtree(self.root)

    def make_manifest(self, run_id, limits=rr.FIXTURE_LIMITS, **changes):
        data = rr.build_manifest(run_id=run_id, variant_manifest_path=self.variant_manifest, mode="fixture",
                                 anchors=[self.ids["index.md"]], fixture_limits=limits, created="2026-10-07")
        data.update(changes)
        path = self.root / f"{run_id}.json"
        rr.write_new(path, data)
        return path

    def launch(self, arm, replicate=1, editor=None, manifest=None):
        return rr.launch(manifest or self.manifest, arm, replicate, editor=editor)

    @staticmethod
    def editor(*ops):
        """A scripted editor: each op is (method, *args); finishes at the end."""
        def run(session):
            run.session = session
            for op in ops:
                getattr(session, op[0])(*op[1:])
            session.finish("fixture done")
        return run

    def ns(self, arm, rep=1, run_id="fixture-run"):
        return self.variants / "runs" / run_id / f"{arm}-r{rep:02d}"

    def receipt(self, arm, name, rep=1):
        return rr.read_json(self.ns(arm, rep) / "host" / "receipts" / name)

    # ------------------------------------------------------------ C admission

    def test_c_rejects_bridge_deletion_and_records_without_retry(self):
        calls = []
        edit = self.editor(("write_file", "f.md", PAGES["f.md"].replace(" and [chi](g.md)", "")),
                           ("checkpoint", "drop chi link"))
        def counted(session):
            calls.append(1)
            edit(session)
        state = self.launch("R0", editor=counted)
        self.assertEqual(state["status"], "c_rejected")
        self.assertFalse(state["c_passed"])
        reasons = self.receipt("R0", "c-admission.json")["result"]["reasons"]
        self.assertIn("C1-baseline-page-left-main-component", reasons)
        self.assertFalse(state["eligible_for_grading"])
        with self.assertRaisesRegex(rr.Refused, "namespace-exists-no-retry"):
            self.launch("R0", editor=counted)
        self.assertEqual(len(calls), 1)

    def test_c_rejects_orphaned_split(self):
        state = self.launch("R0", editor=self.editor(
            ("write_file", "a.md", "# Alpha\nAlpha installs the fixture engine.\nSee [beta](b.md) and [delta](d.md).\n"),
            ("write_file", "a-more.md", "# Alpha, continued\nRun `fixture install`.\nAlpha warns about disk space.\n")))
        self.assertEqual(state["status"], "c_rejected")
        reasons = self.receipt("R0", "c-admission.json")["result"]["reasons"]
        self.assertIn("C1b-new-page-without-in-link", reasons)
        self.assertIn("C1-new-page-outside-main-component", reasons)

    def test_c_admits_link_preserving_split_and_mapped_merge(self):
        split = self.launch("R0", 1, self.editor(
            ("write_file", "c.md", "# Gamma\nGamma explains networking.\n"
                                   "Home is [index](index.md); continue to [more gamma](c-more.md).\n"),
            ("write_file", "c-more.md", "# Gamma, continued\n"
                                        "See [epsilon](e.md); back to [gamma](c.md) or [home](index.md).\n"),
            ("checkpoint", "split gamma")))
        self.assertEqual(split["status"], "completed", self.receipt("R0", "c-admission.json")["result"])
        self.assertTrue(split["eligible_for_grading"])
        merged_text = PAGES["d.md"].replace("Read [epsilon](e.md) and [phi](f.md).\n", "Read [phi](f.md).\n") + \
            "\n## Epsilon\nEpsilon covers bind mounts.\nEpsilon warns about permissions.\n"
        merge = self.launch("R0", 2, self.editor(("write_file", "d.md", merged_text), ("delete_file", "e.md"),
                                                 ("write_file", "c.md", PAGES["c.md"].replace("(e.md)", "(d.md)"))))
        c = self.receipt("R0", "c-admission.json", rep=2)
        self.assertEqual(merge["status"], "completed", c["result"])
        self.assertEqual(c["mapping"], {self.ids["e.md"]: self.ids["d.md"]})

    def test_unmapped_removal_is_flagged_not_admitted(self):
        state = self.launch("R0", editor=self.editor(("delete_file", "orphan.md")))
        self.assertTrue(state["c_passed"])  # the page was outside the main component
        self.assertEqual(state["status"], "preservation_flagged")
        self.assertIn("preservation_flagged:unmapped_removal", state["flags"])
        self.assertFalse(state["eligible_for_grading"])
        mapping = self.receipt("R0", "mapping.json")
        self.assertEqual(mapping["unmapped_removals"], [self.ids["orphan.md"]])
        review = self.ns("R0") / "exports" / "preservation-review"
        self.assertTrue((review / "review.json").is_file() and (review / "mapping.json").is_file())

    # ------------------------------------------------------------ no leaks

    def test_no_c_or_spectral_values_reach_editor_visible_outputs(self):
        edit = self.editor(("list_files",), ("read_file", "f.md"), ("read_file", "../../../host/receipts/c-admission.json"),
                           ("write_file", "f.md", PAGES["f.md"].replace(" and [chi](g.md)", "")),
                           ("checkpoint", "drop chi link"))
        state = self.launch("R0", editor=edit)
        self.assertEqual(state["status"], "c_rejected")
        session = edit.session
        c = self.receipt("R0", "c-admission.json")
        spectral = self.receipt("R0", "spectral-profile.json")
        secrets = set(c["result"]["reasons"]) | {"lambda", "magnetic", "spectral", "reachab", "C0-", "C1", "C2-",
                                                  "c_rejected", "admission", "polarization"}
        def numbers(value):
            if isinstance(value, dict):
                for v in value.values():
                    yield from numbers(v)
            elif isinstance(value, list):
                for v in value:
                    yield from numbers(v)
            elif isinstance(value, float) and len(repr(value)) >= 8:
                yield repr(value)
                yield f"{value:.6f}"
        secrets |= set(numbers(spectral["profile"])) | set(numbers(c["result"]))
        visible = [json.dumps(r) for r in session.visible]
        editor_dir = self.ns("R0") / "editor"
        visible += [p.read_text(encoding="utf-8") for p in editor_dir.rglob("*") if p.is_file()]
        visible += [p.relative_to(editor_dir).as_posix() for p in editor_dir.rglob("*")]
        visible += session.registered_resources()
        for text in visible:
            for secret in secrets:
                self.assertNotIn(secret, text)
        self.assertEqual(session.visible[2], rr.GENERIC_REFUSAL)
        for private in ("host", "exports"):
            self.assertNotIn(editor_dir, (self.ns("R0") / private).parents)

    # ------------------------------------------------------------ spectral receipt

    def test_spectral_receipt_deterministic_order_invariant_and_drift_refused(self):
        _, nodes, edges = rr.tree_graph(self.gm, self.source)
        anchors = [self.ids["index.md"]]
        first = rr.spectral_profile(nodes, edges, anchors)
        shuffled_nodes, shuffled_edges = list(nodes), list(edges) + edges[:2]
        random.Random(7).shuffle(shuffled_nodes)
        random.Random(8).shuffle(shuffled_edges)
        second = rr.spectral_profile(shuffled_nodes, shuffled_edges, anchors)
        self.assertEqual(first, second)
        self.assertEqual(sorted(first["profile"]["retrieval_no_faq_lwcc"]["spectra"]),
                         ["0.00", "0.05", "0.10", "0.15", "0.20", "0.25", "subspace_polarization_v1"])
        with patch.dict(rr.FUNCTIONS_09C, {"spectra": "0" * 64}):
            with self.assertRaisesRegex(rr.Refused, "spectral-code-identity-mismatch:spectra"):
                rr.spectral_profile(nodes, edges, anchors)
        with patch.object(rr, "PHASE_CODE_SHA256", "0" * 64):
            with self.assertRaisesRegex(rr.Refused, "spectral-phase-pin"):
                rr.spectral_profile(nodes, edges, anchors)
        with patch.object(rr, "STRUCT_CODE_SHA256", "0" * 64):
            with self.assertRaisesRegex(rr.Refused, "structural-code-identity"):
                rr.spectral_profile(nodes, edges, anchors)
        with self.assertRaisesRegex(rr.Refused, "duplicate-node"):
            rr.spectral_profile(nodes + nodes[:1], edges, anchors)

    @unittest.skipUnless((rr.ROOT / "artifacts/docker-cikm-2026-05-14/dataset/graph_edges.csv").is_file(), "recovered export absent")
    def test_o_receipt_reproduces_09c_f(self):
        import csv
        dataset = rr.ROOT / "artifacts/docker-cikm-2026-05-14/dataset"
        nodes = sorted(p.stem for p in (dataset / "corpus").glob("*.md"))
        with (dataset / "graph_edges.csv").open(encoding="utf-8", newline="") as handle:
            edges = [(r["source"], r["target"]) for r in csv.DictReader(handle)]
        pin = rr.c_pin_from_variant_manifest(rr.read_json(V5))
        check = rr.f_reference_check(rr.spectral_profile(nodes, edges, pin["anchors"]))
        self.assertTrue(check["applies"])
        self.assertTrue(check["lambda1_exact"] and check["normalized_lambda2_exact"] and check["lwcc_nodes_equal"], check)

    # ------------------------------------------------------------ manifests

    def test_manifest_refusals(self):
        manifest = rr.read_json(self.manifest)
        with self.assertRaisesRegex(rr.Refused, "live-launch-refused:mode-not-live.*arm_plan.R0.replicates"):
            rr.validate_manifest(manifest, live=True)
        with self.assertRaisesRegex(rr.Refused, "live-launch-refused"):
            rr.launch(self.manifest, "O", 1, live=True)
        resolved = json.loads(json.dumps(manifest).replace(json.dumps(rr.UNRESOLVED), "1"))
        resolved["mode"] = "live"
        with self.assertRaisesRegex(rr.Refused, "fixture-only-values"):
            rr.validate_manifest(resolved, live=True)
        live = rr.build_manifest(run_id="live-template", variant_manifest_path=V5, mode="live", created="2026-10-07")
        unresolved = rr.unresolved_paths(live)
        for field in ("editor.model_identity", "editor.runtime_identity", "editor.sandbox_identity", "limits.edits",
                      "limits.tokens", "evaluation.question_set", "evaluation.answer_conditions",
                      "cost_admission.authorization", "arm_plan.R0.replicates",
                      "stopping.budget_stop_terminal_eligible_for_grading"):
            self.assertIn(field, unresolved)
        self.assertEqual(live["c_pin"]["baseline_graph_sha256"], rr.F_REFERENCE["graph_sha256"])
        rr.validate_manifest(live, live=False)
        # Source-tree drift is refused at copy time; manifest drift at validation.
        (self.source / "a.md").write_text("drifted\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "baseline-source-hash-mismatch"):
            self.launch("O")
        drifted = dict(manifest, baseline=dict(manifest["baseline"], source_tree_sha256="0" * 64))
        with self.assertRaisesRegex(rr.Refused, "baseline-source-hash-drift"):
            rr.validate_manifest(drifted, live=False)
        templates = rr.read_json(rr.TEMPLATES)
        templates["arms"]["R0"]["prompt"]["sha256"] = "0" * 64
        bad = self.root / "templates-bad.json"
        rr.write_new(bad, templates)
        tampered = dict(manifest, templates={"path": str(bad), "sha256": dcv.file_hash(bad)})
        with self.assertRaisesRegex(rr.Refused, "prompt-bytes-drift:R0"):
            rr.validate_manifest(tampered, live=False)

    def test_replicate_namespaces_are_isolated(self):
        one, two = self.launch("S", 1), self.launch("S", 2)
        self.assertEqual(one["terminal_checkpoint"], two["terminal_checkpoint"])  # same bytes, separate stores
        for rep in (1, 2):
            self.assertTrue((self.ns("S", rep) / "host/checkpoints" / one["terminal_checkpoint"] / "source").is_dir())
        self.assertNotEqual(one["session_id"], two["session_id"])
        with self.assertRaisesRegex(rr.Refused, "namespace-exists"):
            self.launch("S", 1)
        edit = self.editor(("read_file", "../../../S-r01/editor/source/a.md"), ("write_file", "../S-r02.md", "x"))
        state = self.launch("R0", editor=edit)
        self.assertEqual(edit.session.visible, [rr.GENERIC_REFUSAL, rr.GENERIC_REFUSAL, rr.GENERIC_ACK])
        self.assertEqual(state["status"], "no_op")

    # ------------------------------------------------------------ exports

    def test_sham_export_equals_original_and_grading_labels_are_blind(self):
        states = [self.launch("O"), self.launch("S")]
        states.append(self.launch("R0", editor=self.editor(
            ("write_file", "b.md", PAGES["b.md"].replace("next [gamma](c.md)", "next [gamma](c.md) or [phi](f.md)")))))
        self.assertTrue(all(s["eligible_for_grading"] for s in states), [s["status"] for s in states])
        dest = self.variants / "runs/fixture-run/grading-export"
        key_path = self.variants / "runs/fixture-run/host-private/grading-key.json"
        result = rr.grading_export(self.manifest, [self.ns(a) for a in ("O", "S", "R0")], dest, key_path)
        self.assertEqual(result["variants"], 3)
        key = rr.read_json(key_path)
        by_arm = {v["arm"]: k for k, v in key["variants"].items()}
        o, s = (rr.tree_bytes(dest / "variants" / by_arm[a]) for a in ("O", "S"))
        self.assertEqual(o, s)
        self.assertNotEqual(o, rr.tree_bytes(dest / "variants" / by_arm["R0"]))
        names = [p.relative_to(dest).as_posix() for p in dest.rglob("*")]
        self.assertFalse(any(n.endswith("export-receipt.json") for n in names))
        packet_text = (dest / "packet.json").read_text(encoding="utf-8")
        for token in ("R0", "sham", "original", "-r01", "S-r", "O-r", "fixture-run", "arm\""):
            self.assertNotIn(token, packet_text)
            self.assertFalse(any(token in n for n in names), token)
        self.assertNotIn(dest, key_path.parents)
        with self.assertRaisesRegex(rr.Refused, "grading-export-path"):
            rr.grading_export(self.manifest, [], dest, key_path.with_name("k2.json"))

    # ------------------------------------------------------------ statuses

    def test_budget_stop_failure_and_model_call_statuses_are_honest(self):
        small = self.make_manifest("small-budget", limits=dict(rr.FIXTURE_LIMITS, edits=2))
        edit = self.editor(("write_file", "b.md", PAGES["b.md"] + "Beta adds a note.\n"), ("checkpoint", "batch 1"),
                           ("write_file", "c.md", PAGES["c.md"] + "Gamma adds a note.\n"),
                           ("write_file", "a.md", PAGES["a.md"] + "Alpha adds a note.\n"))
        state = self.launch("R0", editor=edit, manifest=small)
        self.assertEqual(state["status"], "budget_stop")
        self.assertEqual(state["terminal_checkpoint"], edit.session.checkpoints[-1])
        self.assertNotEqual(state["budget_stop_residual"], state["terminal_checkpoint"])
        self.assertFalse(state["eligible_for_grading"])  # eligibility is UNRESOLVED, never assumed
        self.assertIn("c_admission", state["receipts"])

        def broken(session):
            session.read_file("a.md")
            raise RuntimeError("fixture editor crash")
        failed = self.launch("R0", 2, broken)
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(failed["terminal_checkpoint"])
        self.assertFalse(failed["eligible_for_grading"])
        events = (self.ns("R0", 2) / "host/events.jsonl").read_text(encoding="utf-8")
        self.assertIn("fixture editor crash", events)

        unfinished = self.launch("R0", 3, lambda session: session.list_files())
        self.assertEqual(unfinished["status"], "failed")

        model = self.launch("R0", 4, lambda session: session.record_model_call(10))
        self.assertEqual(model["status"], "budget_stop")  # fixture manifests allow zero model calls
        self.assertIsNone(model["terminal_checkpoint"])

        noop = self.launch("R0", 5, self.editor())
        self.assertEqual(noop["status"], "no_op")
        self.assertFalse(noop["eligible_for_grading"])

        links = self.launch("L", 1, self.editor(("write_file", "new.md", "# New\n"), ("delete_file", "g.md")))
        self.assertEqual(links["status"], "no_op")
        with self.assertRaisesRegex(rr.Refused, "editor-required"):
            self.launch("S", 3, self.editor())


class DocsScopeRuns(unittest.TestCase):
    """Sprint 10b: the run contracts on a docs_scope_v1 baseline."""

    def setUp(self):
        self.root = rr.ROOT / "artifacts" / ("restructuring-scope-test-" + uuid.uuid4().hex)
        self.source = self.root / "source"
        pages = dict(PAGES, **{"AGENTS.md": "# Agent rules\nSee [alpha](a.md).\n", ".agents/skills/s/SKILL.md": "# Skill\n",
                               "layouts/single.html": "<html></html>\n"})
        for name, text in pages.items():
            (self.source / name).parent.mkdir(parents=True, exist_ok=True)
            (self.source / name).write_text(text, encoding="utf-8", newline="\n")
        self.vm = self.root / "variant-manifest.json"
        rr.quiet(dcv.freeze, SimpleNamespace(source=self.source, exporter=EXPORTER, commit="fixture",
                                             variants=self.root / "variants", manifest=self.vm, scope="docs_scope_v1"))
        self.gm = dcv.pinned(rr.read_json(self.vm))
        ids = {p.rel_path: p.doc_id for p in dcv.pages(self.gm, self.source)}
        data = rr.build_manifest(run_id="scope-run", variant_manifest_path=self.vm, mode="fixture",
                                 anchors=[ids["index.md"]], fixture_limits=rr.FIXTURE_LIMITS, created="2026-10-07")
        self.manifest = self.root / "scope-run.json"
        rr.write_new(self.manifest, data)

    def tearDown(self):
        if (rr.ROOT / "artifacts").resolve() not in self.root.resolve().parents:
            raise RuntimeError("unsafe-test-cleanup")
        shutil.rmtree(self.root)

    def test_editor_cannot_create_out_of_scope_files_and_tree_graph_refuses_them(self):
        data = rr.read_json(self.manifest)
        self.assertEqual(data["baseline"]["scope"]["version"], "docs_scope_v1")
        self.assertEqual(data["spectral_pin"]["f_reference"], rr.F_REFERENCE_LWCC)
        rr.validate_manifest(data, live=False)
        def edit(session):
            edit.session = session
            for rel in ("AGENTS.md", "content/agents.md", "docs/CLAUDE.md", ".agents/new.md", ".github/prompts/x.md",
                        "layouts/new.md", "static/x.md", "GEMINI.md"):
                session.write_file(rel, "# Injected instructions\n")
            session.finish("tried")
        state = rr.launch(self.manifest, "R0", 1, editor=edit)
        self.assertEqual(edit.session.visible, [rr.GENERIC_REFUSAL] * 8 + [rr.GENERIC_ACK])
        self.assertEqual(state["status"], "no_op")
        tree = self.root / "variants/runs/scope-run/R0-r01/editor/source"
        self.assertEqual(sorted(dcv.inventory(tree)), sorted(PAGES))
        events = (self.root / "variants/runs/scope-run/R0-r01/host/events.jsonl").read_text(encoding="utf-8")
        self.assertEqual(events.count("write-out-of-scope-path"), 8)
        (tree / "AGENTS.md").write_text("# Injected\n", encoding="utf-8")
        with self.assertRaisesRegex(rr.Refused, "out-of-scope-path-refused"):
            rr.tree_graph(self.gm, tree, "docs_scope_v1")
        rr.tree_graph(self.gm, tree)  # repository_root scope does not refuse (historical replay)


V6 = rr.CONTEXT / "evidence/docker-corpus-variant-input-v6-docs-scope-2026-10-07.json"
SPRINT10_MANIFEST = rr.ROOT / "artifacts/docker-corpus-variants-v1/runs/sprint10-fixture-e2e/manifests/run-manifest.json"


@unittest.skipUnless(V6.is_file() and Path(rr.read_json(V5)["source_root"]).is_dir(), "pinned snapshot or v6 absent")
class PinnedV6(unittest.TestCase):
    def test_v6_c_pin_equal_lambda2_and_m_new_hash(self):
        v5, v6 = (rr.c_pin_from_variant_manifest(rr.read_json(p)) for p in (V5, V6))
        self.assertEqual((v6["baseline_lambda2"], v6["baseline_main_component_size"]),
                         (v5["baseline_lambda2"], v5["baseline_main_component_size"]))
        self.assertNotEqual(v6["baseline_graph_sha256"], v5["baseline_graph_sha256"])
        self.assertEqual({k: v for k, v in v6.items() if k != "baseline_graph_sha256"},
                         {k: v for k, v in v5.items() if k != "baseline_graph_sha256"})
        vm = rr.read_json(V6)
        gm = dcv.pinned(vm)
        pages = rr.baseline_pages(gm, vm)
        st = rr.load_struct()
        base = st.baseline_summary(sorted(p.doc_id for p in pages), gm.extract_markdown_edges(pages), v6["anchors"])
        self.assertEqual((base["graph_sha256"], base["lambda2"], len(base["main_component"])),
                         (v6["baseline_graph_sha256"], v6["baseline_lambda2"], v6["baseline_main_component_size"]))

    def test_lwcc_f_reference_exact_on_v6(self):
        vm = rr.read_json(V6)
        gm = dcv.pinned(vm)
        pages = rr.baseline_pages(gm, vm)
        nodes = sorted(p.doc_id for p in pages)
        receipt = rr.spectral_profile(nodes, gm.extract_markdown_edges(pages), rr.c_pin_from_variant_manifest(vm)["anchors"],
                                      pin=rr.spectral_pin_for(vm))
        check = rr.f_reference_check(receipt)
        self.assertTrue(check["applies"] and check["lwcc_edges_equal"] and check["lwcc_nodes_equal"], check)
        self.assertTrue(check["lambda1_exact"] and check["normalized_lambda2_exact"], check)
        self.assertFalse(check["retrieval_graph_equals_09c_F"])
        self.assertEqual(receipt["profile"]["retrieval_no_faq"]["nodes"], 1218)

    @unittest.skipUnless(SPRINT10_MANIFEST.is_file(), "Sprint 10 fixture run absent")
    def test_v5_manifests_still_validate(self):
        for path in (SPRINT10_MANIFEST, rr.CONTEXT / "evidence/restructuring-run-manifest-template-v1-2026-10-07.json"):
            data = rr.read_json(path)
            rr.validate_manifest(data, live=False)
            self.assertEqual(data["spectral_pin"], rr.spectral_pin())
        rebuilt = rr.build_manifest(run_id="sprint10-fixture-e2e", variant_manifest_path=V5, mode="fixture",
                                    fixture_limits=rr.FIXTURE_LIMITS, created="2026-10-07")
        self.assertEqual(rebuilt, rr.read_json(SPRINT10_MANIFEST))  # v5 manifest replays byte-for-value


class Templates(unittest.TestCase):
    def test_templates_hashed_and_free_of_metric_or_evaluation_content(self):
        templates = rr.read_json(rr.TEMPLATES)
        banned = ("lambda", "laplacian", "magnetic", "spectral", "reachab", "connectivity", "graph tool", "metric",
                  "rubric", "C0", "C1", "C2", "admission", "anchor", "0.0")
        for arm in rr.EDITED:
            prompt = templates["arms"][arm]["prompt"]
            path = rr.resolve(prompt["path"])
            self.assertEqual(dcv.file_hash(path), prompt["sha256"])
            text = path.read_text(encoding="utf-8").lower()
            for word in banned:
                self.assertNotIn(word.lower(), text, (arm, word))
        self.assertIsNone(templates["arms"]["O"]["prompt"])
        self.assertIsNone(templates["arms"]["S"]["prompt"])


if __name__ == "__main__":
    unittest.main()

"""Focused synthetic checks; never loads the real Docker questions."""
import importlib.util
import json
import shutil
import unittest
import uuid
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from docker_corpus_variants import (ROOT, analyze, checkpoint, copy_baseline, export_agent, freeze, graph_controls,
                                    inventory, normalized_lambda2, pinned, review, source_check, tree_hash)
from preflight_docker_variant import run as preflight

EXPORTER = Path("C:/Users/rocke/Documents/ResearchProjects/DocSpectraCIKM2026/git/DocSpectraCIKM2026/docspectra_importer/retrieval_arena_export.py")


class Variants(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "artifacts" / ("docker-variant-test-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "a.md").write_text("# Fixture navigation\n[Steps](b.md)\n", encoding="utf-8")
        (self.source / "b.md").write_text("# Steps\nRun fixture command.\n", encoding="utf-8")
        (self.source / "c.md").write_text("# Other\n", encoding="utf-8")
        self.manifest = self.root / "manifest.json"
        self.variants = self.root / "variants"
        freeze(SimpleNamespace(source=self.source, exporter=EXPORTER, commit="fixture", variants=self.variants, manifest=self.manifest))

    def tearDown(self):
        if (ROOT / "artifacts").resolve() not in self.root.resolve().parents:
            raise RuntimeError("unsafe-test-cleanup")
        shutil.rmtree(self.root)

    def copy(self, name):
        dest = self.variants / name / "source"
        copy_baseline(SimpleNamespace(manifest=self.manifest, dest=dest))
        return dest

    def test_hash_refusal_and_collision(self):
        dest = self.copy("sham")
        self.assertEqual(tree_hash(inventory(dest)), tree_hash(inventory(self.source)))
        with self.assertRaisesRegex(ValueError, "collision"):
            self.copy("sham")
        (self.source / "a.md").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "baseline-source-hash"):
            self.copy("new")

    def test_explicit_phase_pin_and_historical_replay(self):
        import sys
        from dataclasses import asdict
        sys.path.insert(0, str(ROOT / 'git/RetrievalArena'))
        from retrieval_arena.graph.magnetic_phase import PhaseConfig, PROJECTOR_VERSION, digest
        from docker_corpus_variants import file_hash, measure, pages
        manifest=json.loads(self.manifest.read_text()); gm=pinned(manifest)
        phase_path=ROOT/'git/RetrievalArena/retrieval_arena/graph/magnetic_phase.py'
        cfg=asdict(PhaseConfig())
        pin={'metric_version':PROJECTOR_VERSION,'code_path':str(phase_path),'code_sha256':file_hash(phase_path),
             'config':cfg,'config_sha256':digest(cfg)}
        old,_=measure(gm,pages(gm,self.source)); new,_=measure(gm,pages(gm,self.source),pin)
        for name in old:
            self.assertEqual({k:v for k,v in new[name].items() if k!='magnetic_subspace_edge_polarization_v1'},old[name])
            self.assertFalse(new[name]['magnetic_subspace_edge_polarization_v1']['optimization_admitted'])
        with self.assertRaisesRegex(ValueError,'phase-code-identity'):
            measure(gm,pages(gm,self.source),dict(pin,code_sha256='bad'))
        with self.assertRaisesRegex(ValueError,'phase-config-identity'):
            measure(gm,pages(gm,self.source),dict(pin,config_sha256='bad'))

    def test_link_only_review_and_unresolved(self):
        dest = self.copy("link")
        (dest / "a.md").write_text("# Fixture navigation\n[Steps](c.md)\n", encoding="utf-8")
        out = self.root / "review.json"
        review(SimpleNamespace(manifest=self.manifest, candidate=dest, output=out))
        data = json.loads(out.read_text())
        self.assertEqual(data["common_page_prose_drift"], [])
        self.assertEqual(len(data["edges_added"]), 1)
        self.assertEqual(len(data["edges_removed"]), 1)
        self.assertEqual(data["unresolved_internal_links"], [])
        (dest / "a.md").write_text("# Fixture navigation\n[Steps](missing.md)\n", encoding="utf-8")
        review(SimpleNamespace(manifest=self.manifest, candidate=dest, output=self.root / "broken.json"))
        self.assertEqual(len(json.loads((self.root / "broken.json").read_text())["unresolved_internal_links"]), 1)

    def test_relocated_support_review_and_spectral_cases(self):
        dest = self.copy("move")
        (dest / "b.md").rename(dest / "steps.md")
        (dest / "a.md").write_text("# Fixture navigation\n[Steps](steps.md)\n", encoding="utf-8")
        review(SimpleNamespace(manifest=self.manifest, candidate=dest, output=self.root / "move.json"))
        data = json.loads((self.root / "move.json").read_text())
        self.assertEqual(data["removed"], ["b.md"])
        self.assertEqual(data["added"], ["steps.md"])
        self.assertEqual(len(data["possible_moves_by_equal_hash"]), 1)
        self.assertAlmostEqual(normalized_lambda2(["a", "b"], [("a", "b")])["value"], 2)
        self.assertEqual(normalized_lambda2(["a", "b", "c"], [("a", "b")])["value"], 0)
        a = graph_controls(["a", "b", "c"], [("a", "b"), ("b", "c")])
        b = graph_controls(["c", "a", "b"], [("a", "b"), ("b", "c")])
        self.assertEqual(a, b)
        gm = pinned(json.loads(self.manifest.read_text()))
        nodes = ["a", "b", "c"]
        edges = [("a", "b"), ("b", "c")]
        magnetic_1 = gm.magnetic_laplacian_metrics(nodes, edges)
        magnetic_2 = gm.magnetic_laplacian_metrics(list(reversed(nodes)), edges)
        self.assertAlmostEqual(magnetic_1["lambda_2_magnetic"], magnetic_2["lambda_2_magnetic"], places=10)
        self.assertTrue(magnetic_1["hermitian_within_tolerance"])
        self.assertTrue(magnetic_1["real_eigenvalues_within_tolerance"])

    def test_path_escape_and_symlink(self):
        with self.assertRaisesRegex(ValueError, "outside-root"):
            copy_baseline(SimpleNamespace(manifest=self.manifest, dest=self.root / "escape"))
        dest = self.copy("symlink")
        pointer = dest / "pointer.md"
        pointer.write_text("fixture", encoding="utf-8")
        original = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda path: path == pointer or original(path)):
            with self.assertRaisesRegex(ValueError, "symlink-refused"):
                inventory(dest)

    def test_offline_fixture_end_to_end(self):
        dest = self.copy("e2e")
        before = tree_hash(inventory(dest))
        receipt = self.variants / "e2e" / "analysis.json"
        analyze(SimpleNamespace(manifest=self.manifest, candidate=dest, output=receipt))
        self.assertEqual(before, tree_hash(inventory(dest)))
        measured = json.loads(receipt.read_text())
        second = self.variants / "e2e" / "analysis-replay.json"
        analyze(SimpleNamespace(manifest=self.manifest, candidate=dest, output=second))
        self.assertEqual(receipt.read_bytes(), second.read_bytes())
        self.assertEqual(list(measured["views"]), ["full", "full_lwcc", "retrieval_no_faq", "retrieval_no_faq_lwcc"])
        self.assertEqual(measured["delta_from_locked_baseline"]["retrieval_no_faq"]["edges"], 0)
        dataset = self.variants / "e2e" / "dataset"
        export_agent(SimpleNamespace(manifest=self.manifest, candidate=dest, dest=dataset))
        result = preflight(dataset)
        self.assertEqual(result["status"], "offline_fixture_passed")
        self.assertEqual(len(result["condition_matrix"]), 4)
        self.assertEqual(result["model_calls"], 0)
        review(SimpleNamespace(manifest=self.manifest, candidate=dest, output=self.root / "review.json"))
        packet = json.loads((self.root / "review.json").read_text())
        self.assertFalse(packet["requires_human_factual_review"])
        self.assertEqual(packet["introduced_unresolved_internal_links"], [])
        metadata = self.root / "metadata.json"
        metadata.write_text(json.dumps({"arm": "S", "parent_tree_sha256": None,
            "prompt_sha256": None, "model_identity": "none_fixture", "session_id": "fixture",
            "tool_transcript_sha256": None, "status": "noop", "edit_reason": "fixture replay"}), encoding="utf-8")
        checkpoint(SimpleNamespace(manifest=self.manifest, candidate=dest, metadata=metadata))
        with self.assertRaisesRegex(ValueError, "checkpoint-collision"):
            checkpoint(SimpleNamespace(manifest=self.manifest, candidate=dest, metadata=metadata))


SCOPE_PAGES = {
    "content/a.md": "# Alpha\n[Beta](b.md)\n", "content/b.md": "# Beta\n[Alpha](a.md)\n",
    "content/manuals/ai/agents/gemini.md": "# Gemini agent product page\n",
    "content/faq.md": "# FAQ\n[Alpha](a.md)\n",
    "README.md": "# Contributing readme\n[Alpha](content/a.md)\n", "STYLE.md": "# Style\n",
    "AGENTS.md": "# Agent rules\n[Style](STYLE.md)\n", "CLAUDE.md": "# Claude rules\n",
    ".agents/skills/x/SKILL.md": "# Skill\n", ".github/prompts/review.prompt.md": "# Prompt\n",
    "layouts/single.html": "<html></html>\n", "static/google123.html": "google-site-verification\n",
}


class DocsScope(unittest.TestCase):
    """Sprint 10b: docs_scope_v1 filters the source side and refuses out-of-scope candidate paths."""

    def setUp(self):
        self.root = ROOT / "artifacts" / ("docker-variant-scope-test-" + uuid.uuid4().hex)
        self.source = self.root / "source"
        for rel, text in SCOPE_PAGES.items():
            (self.source / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.source / rel).write_text(text, encoding="utf-8", newline="\n")
        self.variants = self.root / "variants"
        self.manifest = self.root / "manifest.json"
        freeze(SimpleNamespace(source=self.source, exporter=EXPORTER, commit="fixture", variants=self.variants,
                               manifest=self.manifest, scope="docs_scope_v1"))
        self.data = json.loads(self.manifest.read_text(encoding="utf-8"))

    def tearDown(self):
        if (ROOT / "artifacts").resolve() not in self.root.resolve().parents:
            raise RuntimeError("unsafe-test-cleanup")
        shutil.rmtree(self.root)

    def test_scope_rule_classes_and_frozen_ids(self):
        from docker_corpus_variants import excluded_class
        excluded = self.data["source_policy"]["excluded"]
        self.assertEqual(self.data["source_policy"]["scope"], "docs_scope_v1")
        self.assertEqual(excluded["by_class"], {"agent_instructions": 4, "site_build": 2})
        self.assertEqual(self.data["baseline"]["full_pages"], 6)        # 5 kept non-FAQ + 1 locked FAQ
        self.assertEqual(self.data["baseline"]["retrieval_pages"], 5)
        self.assertEqual(self.data["baseline"]["retrieval_edges"], 3)   # AGENTS.md -> STYLE.md dropped
        for rel in ("AGENTS.md", "agents.md", "content/x/AGENTS.md", "docs/Claude.md", ".agents/a.md",
                    ".github/prompts/p.md", "GEMINI.md", ".claude/x.md", ".github/copilot-instructions.md"):
            self.assertEqual(excluded_class(rel, "docs_scope_v1"), "agent_instructions", rel)
        for rel in ("layouts/x.md", "static/y.md", "LAYOUTS/z.md"):
            self.assertEqual(excluded_class(rel, "docs_scope_v1"), "site_build", rel)
        for rel in ("README.md", "content/manuals/ai/agents/gemini.md", "content/layouts.md", ".github/pull_request_template.md"):
            self.assertIsNone(excluded_class(rel, "docs_scope_v1"), rel)
            self.assertIsNone(excluded_class(rel))  # repository_root keeps everything
        with self.assertRaisesRegex(ValueError, "unknown-scope"):
            excluded_class("a.md", "nope")

    def test_copy_is_in_scope_and_candidate_paths_are_refused(self):
        dest = self.variants / "c" / "source"
        copy_baseline(SimpleNamespace(manifest=self.manifest, dest=dest))
        self.assertEqual(sorted(inventory(dest)), ["README.md", "STYLE.md", "content/a.md", "content/b.md",
                                                   "content/manuals/ai/agents/gemini.md"])
        out = self.variants / "c" / "analysis.json"
        analyze(SimpleNamespace(manifest=self.manifest, candidate=dest, output=out))
        views = json.loads(out.read_text(encoding="utf-8"))["views"]
        self.assertEqual((views["full"]["nodes"], views["retrieval_no_faq"]["nodes"]), (6, 5))
        for rel in ("AGENTS.md", "content/nested/agents.md", "layouts/new.md"):
            bad = dest / rel
            bad.parent.mkdir(parents=True, exist_ok=True)
            bad.write_text("# Injected\n", encoding="utf-8")
            for stage, kwargs in ((analyze, {"output": self.variants / "c" / "x.json"}),
                                  (review, {"output": self.variants / "c" / "r.json"}),
                                  (export_agent, {"dest": self.variants / "c" / "export"})):
                with self.assertRaisesRegex(ValueError, "out-of-scope-path-refused"):
                    stage(SimpleNamespace(manifest=self.manifest, candidate=dest, **kwargs))
            bad.unlink()

    def test_frozen_exclusion_and_rule_drift_refused(self):
        tampered = dict(self.data, source_policy=dict(self.data["source_policy"],
                        excluded=dict(self.data["source_policy"]["excluded"], sha256="0" * 64)))
        path = self.root / "tampered.json"
        path.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "scope-exclusion-drift"):
            copy_baseline(SimpleNamespace(manifest=path, dest=self.variants / "t" / "source"))
        rule = json.loads(json.dumps(self.data["source_policy"]["excluded"]["rule"]))
        rule["classes"]["site_build"]["prefixes"] = ["layouts/"]
        tampered["source_policy"]["excluded"] = dict(self.data["source_policy"]["excluded"], rule=rule)
        path.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "scope-rule-drift"):
            copy_baseline(SimpleNamespace(manifest=path, dest=self.variants / "u" / "source"))

    def test_default_scope_keeps_every_page(self):
        from docker_corpus_variants import pages
        default = self.root / "default.json"
        freeze(SimpleNamespace(source=self.source, exporter=EXPORTER, commit="fixture", variants=self.variants,
                               manifest=default))
        data = json.loads(default.read_text(encoding="utf-8"))
        self.assertEqual(data["source_policy"]["scope"], "repository_root")
        self.assertNotIn("excluded", data["source_policy"])
        self.assertEqual(data["baseline"]["retrieval_pages"], 11)
        gm = pinned(data)
        self.assertEqual([p.rel_path for p in pages(gm, self.source)],
                         [p.rel_path for p in pages(gm, self.source, "repository_root", refuse=True)])


V5 = ROOT / "git/context/evidence/docker-corpus-variant-input-v5-structural-target-2026-10-07.json"
V6 = ROOT / "git/context/evidence/docker-corpus-variant-input-v6-docs-scope-2026-10-07.json"


@unittest.skipUnless(V6.is_file() and Path(json.loads(V5.read_text(encoding="utf-8"))["source_root"]).is_dir(),
                     "pinned Docker snapshot or v6 absent")
class PinnedSnapshotScope(unittest.TestCase):
    def test_v5_replays_unchanged_and_v6_filters_exactly(self):
        import hashlib
        from docker_corpus_variants import source_pages
        for path, pages_, edges_ in ((V5, 1263, 2496), (V6, 1218, 2494)):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            gm = pinned(manifest)
            ret = [p for p in source_pages(gm, manifest, source_check(manifest)) if not p.is_faq]
            edges = gm.extract_markdown_edges(ret)
            self.assertEqual((len(ret), len(edges)), (pages_, edges_))
            self.assertEqual(hashlib.sha256(json.dumps(edges, separators=(",", ":")).encode()).hexdigest(),
                             manifest["baseline"]["graph_edges_sha256"])
        v6 = json.loads(V6.read_text(encoding="utf-8"))
        self.assertEqual(v6["source_policy"]["excluded"]["by_class"], {"agent_instructions": 18, "site_build": 27})
        self.assertEqual(v6["baseline"]["change_from_v5"]["excluded_in_main_component"], [])


if __name__ == "__main__":
    unittest.main()

"""Pinned, local Docker corpus variant preparation and read-only diagnostics.

The editor receives only the FAQ-excluded page tree. This script must run in a
trusted process with access to the locked source; do not mount that source in an
editor session. No evaluation questions or judgments are read here.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
from collections import Counter, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = "docspectra.icsc.docker_variant.v1"
VIEWS = ("full", "full_lwcc", "retrieval_no_faq", "retrieval_no_faq_lwcc")
LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
PROTECTED = re.compile(r"(?m)^\s*(?:```[^\n]*|\$\s+\S[^\n]*|docker\s+\S[^\n]*|WARNING\b[^\n]*|Warning\b[^\n]*|CAUTION\b[^\n]*|Caution\b[^\n]*)")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    return digest(path.read_bytes())


def write_new(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


def inventory(root: Path) -> dict[str, str]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("tree-not-directory")
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symlink-refused:{path}")
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            if rel.startswith("../") or rel in result:
                raise ValueError("path-escape-or-collision")
            result[rel] = file_hash(path)
    return result


def tree_hash(files: dict[str, str]) -> str:
    return digest(json.dumps(files, sort_keys=True, separators=(",", ":")).encode())


def pinned(manifest: dict):
    code = manifest["code"]
    for key in ("exporter", "metrics"):
        path = Path(code[key]["path"])
        if not path.is_file() or file_hash(path) != code[key]["sha256"]:
            raise ValueError(f"code-identity-mismatch:{key}")
    repo = Path(code["exporter"]["path"]).parents[1]
    sys.path.insert(0, str(repo))
    from docspectra_importer import graph_metrics as gm
    return gm


def source_check(manifest: dict) -> Path:
    root = Path(manifest["source_root"])
    if tree_hash(inventory(root)) != manifest["source_tree_sha256"]:
        raise ValueError("baseline-source-hash-mismatch")
    return root


# Sprint 10b corpus scope. `repository_root` is the historical CIKM behavior
# (every discovered page); manifests v1-v5 carry it and replay unchanged.
# `docs_scope_v1` keeps product and contributor documentation and drops files
# written to steer AI agents and the site-build machinery. Matching is on the
# lowercased repository-relative path, so case variants are caught too.
DEFAULT_SCOPE = "repository_root"
DOCS_SCOPE_V1 = {
    "version": "docs_scope_v1",
    "classes": {
        "agent_instructions": {"basenames_any_depth": ["agents.md", "claude.md"],
                               "prefixes": [".agents/", ".claude/", ".cursor/", ".github/prompts/",
                                            ".github/instructions/"],
                               "paths": ["gemini.md", ".github/copilot-instructions.md"]},
        "site_build": {"basenames_any_depth": [], "prefixes": ["layouts/", "static/"], "paths": []},
    },
    "kept": "everything else: content/** product docs and root contributor docs",
    "note": "root gemini.md, .claude/, .cursor/, .github/instructions/ and copilot-instructions.md match nothing "
            "at commit 274681c; they are refuse-on-create guards for editor trees. gemini.md is root-only because "
            "content/manuals/ai/sandboxes/agents/gemini.md is product documentation. Any-depth agents.md/claude.md "
            "match no content/ page at 274681c; a future page with such a name fails the frozen-ID check "
            "(scope-exclusion-drift) instead of being dropped silently",
}
SCOPES = {"repository_root": None, "docs_scope_v1": DOCS_SCOPE_V1}


def excluded_class(rel: str, scope: str = DEFAULT_SCOPE):
    """Class label of an out-of-scope repository-relative path, else None."""
    if scope not in SCOPES:
        raise ValueError(f"unknown-scope:{scope}")
    rule = SCOPES[scope]
    if rule is None:
        return None
    path = rel.replace("\\", "/").lower()
    base = path.rsplit("/", 1)[-1]
    for name, spec in rule["classes"].items():
        if base in spec["basenames_any_depth"] or path in spec["paths"] or path.startswith(tuple(spec["prefixes"])):
            return name
    return None


def scope_of(manifest: dict) -> str:
    return manifest.get("source_policy", {}).get("scope", DEFAULT_SCOPE)


def pages(gm, root: Path, scope: str = DEFAULT_SCOPE, *, refuse: bool = False):
    """Discovered pages in scope. `refuse` (candidate trees) fails on any out-of-scope path."""
    found = gm.discover_markdown_pages(root, faq_patterns=["*faq*"])
    if scope == DEFAULT_SCOPE:
        return found
    out = [p for p in found if excluded_class(p.rel_path, scope) is None]
    if refuse and len(out) != len(found):
        bad = sorted(p.rel_path for p in found if excluded_class(p.rel_path, scope) is not None)
        raise ValueError("out-of-scope-path-refused:" + ",".join(bad[:5]))
    return out


def scope_exclusions(gm, root: Path, scope: str):
    """Out-of-scope source pages as sorted [{doc_id, rel_path, class}] records."""
    return sorted(({"doc_id": p.doc_id, "rel_path": p.rel_path, "class": excluded_class(p.rel_path, scope)}
                   for p in gm.discover_markdown_pages(root, faq_patterns=["*faq*"])
                   if excluded_class(p.rel_path, scope) is not None), key=lambda r: r["doc_id"])


def source_pages(gm, manifest: dict, root: Path):
    """Baseline pages under the manifest scope; the excluded set must equal the frozen list."""
    scope = scope_of(manifest)
    if scope != DEFAULT_SCOPE:
        frozen = manifest["source_policy"]["excluded"]
        if frozen["rule"] != SCOPES[scope]:
            raise ValueError("scope-rule-drift")
        found = scope_exclusions(gm, root, scope)
        if digest(json.dumps(found, sort_keys=True, separators=(",", ":")).encode()) != frozen["sha256"] \
                or len(found) != frozen["count"]:
            raise ValueError("scope-exclusion-drift")
    return pages(gm, root, scope)


def excluded_record(gm, root: Path, scope: str) -> dict:
    ids = scope_exclusions(gm, root, scope)
    return {"rule": SCOPES[scope], "count": len(ids), "by_class": dict(sorted(Counter(r["class"] for r in ids).items())),
            "ids": ids, "sha256": digest(json.dumps(ids, sort_keys=True, separators=(",", ":")).encode())}


def candidate_pages(gm, manifest: dict, root: Path):
    return pages(gm, root, scope_of(manifest), refuse=True)


def freeze(args) -> None:
    source = args.source.resolve(strict=True)
    scope = getattr(args, "scope", None) or DEFAULT_SCOPE
    gm_path = args.exporter.parents[0] / "graph_metrics.py"
    lock = json.loads((ROOT / "git/context/evidence/docker-cikm.lock.json").read_text(encoding="utf-8"))
    if args.commit != "fixture" and args.commit != lock["source"]["commit"]:
        raise ValueError("source-commit-does-not-match-recovery-lock")
    if args.commit != "fixture" and file_hash(args.exporter) != lock["exporter"]["sha256"]:
        raise ValueError("exporter-does-not-match-recovery-lock")
    prompts = {name: ROOT / f"git/context/prompts/docker-editor-{name}-v1.txt"
               for name in ("R", "L", "X-plus", "X-minus")}
    manifest = {"schema_version": SCHEMA, "source_commit": args.commit,
        "source_root": str(source), "source_tree_sha256": tree_hash(inventory(source)),
        "source_policy": {"scope": scope, "faq_patterns": ["*faq*"],
                          "editor_input": "discovered_non_faq_pages_only",
                          "document_ids": "cikm_lowercase_path_sha1_v1"},
        "code": {"exporter": {"path": str(args.exporter.resolve()), "sha256": file_hash(args.exporter)},
                 "metrics": {"path": str(gm_path.resolve()), "sha256": file_hash(gm_path)}},
        "metric_definition": {"magnetic": "CIKM unnormalized Dq-Hq, q=0.25, binary edges",
                              "undirected_normalized": "D^-1/2(D-A)D^-1/2; isolated diagonal zero",
                              "flow": "CIKM phase flow diagnostic only; not optimization target"},
        "views": list(VIEWS), "output_root": str(args.variants.resolve()),
        "recovered_export": {"lock_sha256": file_hash(ROOT / "git/context/evidence/docker-cikm.lock.json"),
            "corpus_inventory_sha256": lock["corpus_inventory_sha256"],
            "graph_csv_sha256": lock["dataset_files"]["graph_edges.csv"],
            "historical_question_file_sha256": lock["dataset_files"]["questions.jsonl"],
            "historical_question_count": lock["counts"]["questions"],
            "historically_supported_count": lock["counts"]["supported"],
            "question_material_location": "private_recovery_archive_outside_editor_input"},
        "editor": {"planned_model_alias": "gpt-5.6-sol", "model_revision": "unavailable_until_run",
                   "prompts_sha256": {name: file_hash(path) for name, path in prompts.items()},
                   "edit_budget": "must_be_frozen_in_later_run_manifest",
                   "tool_call_budget": "must_be_frozen_in_later_run_manifest"},
        "evaluation_firewall": "No questions, answers, grades, rubrics or task outcomes in editor input or analysis receipts"}
    gm = pinned(manifest)
    if scope != DEFAULT_SCOPE:
        manifest["source_policy"]["excluded"] = excluded_record(gm, source, scope)
    src_pages = source_pages(gm, manifest, source)
    retrieved = [p for p in src_pages if not p.is_faq]
    edges = gm.extract_markdown_edges(retrieved)
    manifest["baseline"] = {"full_pages": len(src_pages), "retrieval_pages": len(retrieved),
                            "retrieval_edges": len(edges),
                            "graph_edges_sha256": digest(json.dumps(edges, separators=(",", ":")).encode())}
    write_new(args.manifest, manifest)
    print(json.dumps({"manifest_sha256": file_hash(args.manifest), "baseline": manifest["baseline"]}))


def copy_baseline(args) -> None:
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    gm = pinned(manifest)
    source = source_check(manifest)
    dest = args.dest.resolve()
    allowed = Path(manifest["output_root"]).resolve()
    if dest == allowed or allowed not in dest.parents or dest.exists():
        raise ValueError("output-collision-or-outside-root")
    selected = [p for p in source_pages(gm, manifest, source) if not p.is_faq]
    dest.mkdir(parents=True)
    try:
        for page in selected:
            rel = Path(page.rel_path)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError("source-path-escape")
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(page.source_path, target)
        copied = inventory(dest)
        if len(copied) != manifest["baseline"]["retrieval_pages"]:
            raise ValueError("copy-count-mismatch")
        print(json.dumps({"candidate_root": str(dest), "tree_sha256": tree_hash(copied), "files": len(copied)}))
    except Exception:
        # A failed copy remains for inspection; never overwrite on retry.
        raise


def graph_controls(nodes, edges):
    n, m = len(nodes), len(edges)
    out, incoming = Counter(a for a, _ in edges), Counter(b for _, b in edges)
    adjacency = {x: set() for x in nodes}
    for a, b in edges:
        adjacency[a].add(b)
    reached = 0
    distance_sum = 0
    for start in nodes:
        seen = {start: 0}
        queue = deque([start])
        while queue:
            x = queue.popleft()
            for y in adjacency[x]:
                if y not in seen:
                    seen[y] = seen[x] + 1
                    queue.append(y)
        reached += len(seen) - 1
        distance_sum += sum(seen.values())
    return {"nodes": n, "edges": m, "mean_outdegree": m / n if n else 0,
            "max_outdegree": max(out.values(), default=0), "max_indegree": max(incoming.values(), default=0),
            "zero_outdegree": sum(out[x] == 0 for x in nodes), "zero_indegree": sum(incoming[x] == 0 for x in nodes),
            "directed_reachable_pair_fraction": reached / (n * (n - 1)) if n > 1 else 0,
            "mean_finite_directed_distance": distance_sum / reached if reached else None,
            "directed_degree_imbalance": sum(abs(out[x] - incoming[x]) for x in nodes) / (2 * m) if m else 0}


def normalized_lambda2(nodes, edges):
    import numpy as np
    n = len(nodes)
    if n < 2:
        return {"status": "insufficient_nodes", "value": None}
    idx = {x: i for i, x in enumerate(nodes)}
    a = np.zeros((n, n), dtype=float)
    for x, y in edges:
        a[idx[x], idx[y]] = a[idx[y], idx[x]] = 1
    d = a.sum(axis=1)
    inv = np.zeros(n)
    inv[d > 0] = 1 / np.sqrt(d[d > 0])
    lap = np.diag((d > 0).astype(float)) - inv[:, None] * a * inv[None, :]
    values = np.linalg.eigvalsh(lap)
    value = float(max(0, values[1]))
    return {"status": "computed", "value": value, "zero_eigenvalues": int(np.count_nonzero(abs(values) < 1e-9)),
            "range_ok": bool(-1e-8 <= values[0] and values[-1] <= 2 + 1e-8)}


def measure(gm, page_list, phase_pin=None):
    nodes = sorted(p.doc_id for p in page_list)
    edges = gm.extract_markdown_edges(page_list)
    if len(set(nodes)) != len(nodes):
        raise ValueError("document-id-collision")
    components = gm.weak_components(nodes, edges)
    selected = components[0] if components else set()
    records = {}
    for name, ns, es in (("base", nodes, edges), ("lwcc", sorted(selected), [(a,b) for a,b in edges if a in selected and b in selected])):
        c = graph_controls(ns, es)
        c.update({"weak_components": len(components) if name == "base" else (1 if ns else 0),
                  "largest_component_share": len(selected) / len(nodes) if nodes else 0,
                  "undirected_normalized_lambda2": normalized_lambda2(ns, es)})
        historical_u = gm.undirected_laplacian_metrics(ns, es)
        magnetic = gm.magnetic_laplacian_metrics(ns, es)
        c["historical_undirected_lambda2_over_max"] = historical_u.get("lambda_2_norm")
        c["magnetic_lambda2"] = magnetic.get("lambda_2_magnetic")
        c["magnetic_status"] = {"status": magnetic.get("status"), "hermitian_ok": magnetic.get("hermitian_within_tolerance"),
                               "real_spectrum_ok": magnetic.get("real_eigenvalues_within_tolerance"),
                               "normalize": magnetic.get("config", {}).get("normalize")}
        c["historical_phase_flow_diagnostic"] = magnetic.get("flow_imbalance")
        c["historical_phase_flow_target_valid"] = False
        if phase_pin is not None:
            path = Path(phase_pin['code_path'])
            if file_hash(path) != phase_pin['code_sha256']:
                raise ValueError('phase-code-identity-mismatch')
            sys.path.insert(0, str(ROOT / 'git/RetrievalArena'))
            from retrieval_arena.graph.magnetic_phase import PhaseConfig, digest as config_digest, measure_subspace, PROJECTOR_VERSION
            if phase_pin['metric_version'] != PROJECTOR_VERSION or config_digest(phase_pin['config']) != phase_pin['config_sha256']:
                raise ValueError('phase-config-identity-mismatch')
            c['magnetic_subspace_edge_polarization_v1'] = measure_subspace(ns, es, PhaseConfig(**phase_pin['config']))
        c["warnings"] = (["disconnected_graph"] if len(components) > 1 and name == "base" else []) + (
            ["near_zero_normalized_lambda2"] if c["undirected_normalized_lambda2"].get("value") is not None and
            c["undirected_normalized_lambda2"]["value"] < 1e-9 else []) + ["phase_flow_order_or_eigenspace_unstable"]
        records[name] = c
    return records, edges


def analyze(args) -> None:
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    gm = pinned(manifest)
    source = source_check(manifest)
    candidate = args.candidate.resolve(strict=True)
    allowed = Path(manifest["output_root"]).resolve()
    if candidate == allowed or allowed not in candidate.parents:
        raise ValueError("candidate-outside-isolated-root")
    output = args.output.resolve()
    if output == candidate or candidate in output.parents or allowed not in output.parents:
        raise ValueError("analysis-output-must-be-outside-candidate-within-artifacts")
    files = inventory(candidate)
    current = candidate_pages(gm, manifest, candidate)
    if len(current) != len(files) or any(p.is_faq for p in current):
        raise ValueError("candidate-nonpage-or-faq")
    baseline_pages = source_pages(gm, manifest, source)
    baseline_retrieval = [p for p in baseline_pages if not p.is_faq]
    locked_faq = [p for p in baseline_pages if p.is_faq]
    if len(locked_faq) + len(baseline_retrieval) != manifest["baseline"]["full_pages"]:
        raise ValueError("baseline-page-count-mismatch")
    phase_pin = manifest.get('phase_measurement_pin')
    base_views, baseline_edges = measure(gm, baseline_retrieval, phase_pin)
    if digest(json.dumps(baseline_edges, separators=(",", ":")).encode()) != manifest["baseline"]["graph_edges_sha256"]:
        raise ValueError("baseline-graph-hash-mismatch")
    ret, candidate_edges = measure(gm, current, phase_pin)
    full, _ = measure(gm, current + locked_faq, phase_pin)
    views = {"full": full["base"], "full_lwcc": full["lwcc"],
             "retrieval_no_faq": ret["base"], "retrieval_no_faq_lwcc": ret["lwcc"]}
    deltas = {}
    for view in ("retrieval_no_faq", "retrieval_no_faq_lwcc"):
        b = base_views["base" if view == "retrieval_no_faq" else "lwcc"]
        deltas[view] = {k: views[view][k] - b[k] for k in ("nodes", "edges", "mean_outdegree", "directed_reachable_pair_fraction", "directed_degree_imbalance")}
    result = {"schema_version": SCHEMA + ".analysis", "manifest_sha256": file_hash(args.manifest),
              "candidate_tree_sha256": tree_hash(files), "candidate_file_count": len(files),
              "graph_edges_sha256": digest(json.dumps(candidate_edges, separators=(",", ":")).encode()),
              "views": views, "delta_from_locked_baseline": deltas,
              "baseline_retrieval_tree_sha256": tree_hash({p.rel_path: file_hash(p.source_path) for p in baseline_retrieval}),
              "metric_code_sha256": manifest["code"]["metrics"]["sha256"],
              "warnings": ["full_view_uses_locked_unedited_faq_pages", "historical_phase_flow_diagnostic_only"]}
    write_new(args.output, result)
    print(json.dumps({"candidate_tree_sha256": result["candidate_tree_sha256"],
                      "graph_edges_sha256": result["graph_edges_sha256"], "views": {v: {k: views[v][k] for k in
                      ("nodes", "edges", "weak_components", "magnetic_lambda2", "historical_phase_flow_diagnostic")}
                      for v in VIEWS}, "receipt": str(args.output)}))


def review(args) -> None:
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    gm = pinned(manifest)
    source = source_check(manifest)
    baseline = {p.rel_path: p for p in source_pages(gm, manifest, source) if not p.is_faq}
    candidate = args.candidate.resolve(strict=True)
    allowed = Path(manifest["output_root"]).resolve()
    if candidate == allowed or allowed not in candidate.parents:
        raise ValueError("candidate-outside-isolated-root")
    inventory(candidate)
    changed = {p.rel_path: p for p in candidate_pages(gm, manifest, candidate)}
    if any(p.is_faq for p in changed.values()):
        raise ValueError("candidate-faq")
    removed = sorted(set(baseline) - set(changed))
    added = sorted(set(changed) - set(baseline))
    common = sorted(set(baseline) & set(changed))
    edits = [x for x in common if file_hash(baseline[x].source_path) != file_hash(changed[x].source_path)]
    old_tokens = Counter(PROTECTED.findall("\n".join(p.text for p in baseline.values())))
    new_tokens = Counter(PROTECTED.findall("\n".join(p.text for p in changed.values())))
    old_edges = set(gm.extract_markdown_edges(list(baseline.values())))
    new_edges = set(gm.extract_markdown_edges(list(changed.values())))
    # The historical extractor is permissive; unresolved local links are review flags.
    from docspectra_importer.retrieval_arena_export import _candidate_targets, _route_key
    def unresolved_links(page_map):
        rels = set(page_map)
        routes = {_route_key(x) for x in rels}
        return [{"page": rel, "target": target} for rel, page in page_map.items()
                for target in LINK.findall(page.text)
                if not target.startswith(("http:", "https:", "mailto:", "#"))
                and not any(x in rels for x in _candidate_targets(target, rel))
                and _route_key(target) not in routes]
    unresolved = unresolved_links(changed)
    original_unresolved = unresolved_links(baseline)
    old_unresolved_keys = {(x["page"], x["target"]) for x in original_unresolved}
    introduced_unresolved = [x for x in unresolved if (x["page"], x["target"]) not in old_unresolved_keys]
    old_prose = {x: LINK.sub("", p.text) for x,p in baseline.items()}
    new_prose = {x: LINK.sub("", p.text) for x,p in changed.items()}
    unified_diffs = {x: "".join(difflib.unified_diff(
        baseline[x].text.splitlines(keepends=True), changed[x].text.splitlines(keepends=True),
        fromfile="baseline/" + x, tofile="candidate/" + x)) for x in edits}
    report = {"schema_version": SCHEMA + ".review", "candidate_tree_sha256": tree_hash(inventory(candidate)),
              "removed": removed, "added": added, "changed": edits,
              "page_diff": unified_diffs,
              "page_inventory": {x: {"baseline_sha256": file_hash(baseline[x].source_path) if x in baseline else None,
                                     "candidate_sha256": file_hash(changed[x].source_path) if x in changed else None,
                                     "candidate_doc_id": changed[x].doc_id if x in changed else None}
                                 for x in sorted(set(baseline) | set(changed))},
              "possible_moves_by_equal_hash": [{"from": a, "to": b} for a in removed for b in added
                                               if file_hash(baseline[a].source_path) == file_hash(changed[b].source_path)],
              "edges_added": sorted(new_edges-old_edges), "edges_removed": sorted(old_edges-new_edges),
              "unresolved_internal_links": unresolved, "baseline_unresolved_internal_link_count": len(original_unresolved),
              "introduced_unresolved_internal_links": introduced_unresolved,
              "protected_lines_removed": list((old_tokens-new_tokens).elements()),
              "protected_lines_added": list((new_tokens-old_tokens).elements()),
              "common_page_prose_drift": [x for x in common if old_prose[x] != new_prose[x]],
              "requires_human_factual_review": bool(removed or added or edits)}
    write_new(args.output, report)
    print(json.dumps({"review": str(args.output), "changed": len(edits), "unresolved_links": len(unresolved),
                      "protected_removed": len(report["protected_lines_removed"])}))


def export_agent(args) -> None:
    """Build a provider-only dataset; no questions, answers, support or scores."""
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    gm = pinned(manifest)
    source_check(manifest)
    candidate = args.candidate.resolve(strict=True)
    allowed = Path(manifest["output_root"]).resolve()
    dest = args.dest.resolve()
    if candidate == allowed or allowed not in candidate.parents or dest == allowed or allowed not in dest.parents or dest.exists() or dest == candidate or candidate in dest.parents:
        raise ValueError("export-path-outside-root-or-collision")
    raw = inventory(candidate)
    page_list = candidate_pages(gm, manifest, candidate)
    if len(raw) != len(page_list) or any(p.is_faq for p in page_list):
        raise ValueError("candidate-nonpage-or-faq")
    ids = [p.doc_id for p in page_list]
    if len(set(ids)) != len(ids):
        raise ValueError("document-id-collision")
    edges = gm.extract_markdown_edges(page_list)
    dest.mkdir(parents=True)
    corpus = dest / "corpus"
    corpus.mkdir()
    for page in page_list:
        # The recovered CIKM export was written on Windows with CRLF. Preserve
        # those bytes even when a later preflight runs on another OS.
        (corpus / f"{page.doc_id}.md").write_bytes(page.text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    with (dest / "graph_edges.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["source", "target"])
        writer.writerows(edges)
    receipt = {"schema_version": SCHEMA + ".agent_export", "candidate_tree_sha256": tree_hash(raw),
               "corpus_tree_sha256": tree_hash(inventory(corpus)),
               "graph_file_sha256": file_hash(dest / "graph_edges.csv"),
               "nodes": len(ids), "edges": len(edges), "contains_evaluation_material": False}
    write_new(dest / "export-receipt.json", receipt)
    print(json.dumps(receipt))


def checkpoint(args) -> None:
    """Save one accepted or rejected candidate without overwriting prior bytes."""
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    source_check(manifest)
    candidate = args.candidate.resolve(strict=True)
    allowed = Path(manifest["output_root"]).resolve()
    if candidate == allowed or allowed not in candidate.parents:
        raise ValueError("candidate-outside-isolated-root")
    files = inventory(candidate)
    identity = tree_hash(files)
    # Sprint 10: a run namespace keeps replicates with equal trees apart.
    namespace = Path(getattr(args, "namespace", None) or allowed).resolve()
    if namespace != allowed and allowed not in namespace.parents:
        raise ValueError("checkpoint-namespace-outside-root")
    if namespace == candidate or candidate in namespace.parents:
        raise ValueError("checkpoint-namespace-inside-candidate")
    destination = namespace / "checkpoints" / identity
    if destination.exists():
        raise ValueError("checkpoint-collision")
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    required = {"arm", "parent_tree_sha256", "prompt_sha256", "model_identity", "session_id",
                "tool_transcript_sha256", "status", "edit_reason"}
    if set(metadata) != required or metadata["status"] not in {"accepted", "rejected", "failed", "invalid", "noop", "candidate"}:
        raise ValueError("checkpoint-metadata-contract")
    destination.mkdir(parents=True)
    shutil.copytree(candidate, destination / "source")
    if tree_hash(inventory(destination / "source")) != identity:
        raise ValueError("checkpoint-copy-hash-mismatch")
    record = {"schema_version": SCHEMA + ".checkpoint", "content_id": identity,
              "source_manifest_sha256": file_hash(args.manifest), "candidate_file_count": len(files),
              "metadata": metadata, "metadata_sha256": file_hash(args.metadata)}
    write_new(destination / "checkpoint.json", record)
    print(json.dumps({"checkpoint": str(destination), "content_id": identity, "status": metadata["status"]}))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("freeze")
    for flag, typ in (("--source", Path), ("--exporter", Path), ("--commit", str), ("--variants", Path), ("--manifest", Path)):
        f.add_argument(flag, type=typ, required=True)
    f.add_argument("--scope", choices=sorted(SCOPES), default=DEFAULT_SCOPE)
    c = sub.add_parser("copy")
    c.add_argument("--manifest", type=Path, required=True); c.add_argument("--dest", type=Path, required=True)
    for name in ("analyze", "review"):
        x = sub.add_parser(name)
        x.add_argument("--manifest", type=Path, required=True)
        x.add_argument("--candidate", type=Path, required=True)
        x.add_argument("--output", type=Path, required=True)
    e = sub.add_parser("export-agent")
    e.add_argument("--manifest", type=Path, required=True)
    e.add_argument("--candidate", type=Path, required=True)
    e.add_argument("--dest", type=Path, required=True)
    k = sub.add_parser("checkpoint")
    k.add_argument("--manifest", type=Path, required=True)
    k.add_argument("--candidate", type=Path, required=True)
    k.add_argument("--metadata", type=Path, required=True)
    k.add_argument("--namespace", type=Path)
    args = parser.parse_args()
    {"freeze": freeze, "copy": copy_baseline, "analyze": analyze, "review": review,
     "export-agent": export_agent, "checkpoint": checkpoint}[args.command](args)


if __name__ == "__main__":
    main()

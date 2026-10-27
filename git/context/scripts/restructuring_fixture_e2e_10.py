"""Sprint 10 live check: fixture end to end on the real Docker baseline, zero model calls.

O, S and a scripted synthetic R0 edit (one link-preserving split, one reciprocal
merge) go through copy -> checkpoint -> C admission -> spectral receipt ->
exports. The synthetic editor is chosen from the baseline graph by fixed rules
and acts only through EditorSession file tools. Raw outputs stay under the
ignored variant root; a compact summary goes to git/context/evidence.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import docker_corpus_variants as dcv  # noqa: E402
import restructuring_runs as rr  # noqa: E402

V5 = rr.CONTEXT / "evidence/docker-corpus-variant-input-v5-structural-target-2026-10-07.json"
EXCLUDED_DIRS = (".agents/", ".github/prompts/", "layouts/", "static/")
DATASET = rr.ROOT / "artifacts/docker-cikm-2026-05-14/dataset"
LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)]*)\)")


def resolve_target(target, rel, rels, routes):
    from docspectra_importer.retrieval_arena_export import _candidate_targets, _route_key
    for x in _candidate_targets(target, rel):
        if x in rels:
            return x
    key = _route_key(target)
    return routes.get(key)


def body_start(lines):
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return i + 1
    return 0


def title_of(text, fallback):
    m = re.search(r"(?m)^title:\s*(.+)$", text)
    return (m.group(1).strip().strip("'\"") if m else fallback) or fallback


def plan_edit(gm, vm, anchors):
    """Fixed rules: smallest-id merge pair and smallest-id split page that satisfy the checks."""
    from docspectra_importer.retrieval_arena_export import _route_key
    st = rr.load_struct()
    base = rr.baseline_pages(gm, vm)
    by_rel = {p.rel_path: p for p in base}
    by_id = {p.doc_id: p for p in base}
    rels = set(by_rel)
    routes = {_route_key(r): r for r in sorted(rels, reverse=True)}
    edges = sorted(set(gm.extract_markdown_edges(base)))
    m = st.main_component(sorted(by_id), edges)
    eset = set(edges)
    incoming = {}
    for s, t in edges:
        incoming.setdefault(t, set()).add(s)
    anchor_set = set(anchors)
    merge = None
    for a, b in edges:
        pa, pb = by_id[a], by_id[b]
        if (b, a) not in eset or a in anchor_set or b in anchor_set or a not in m or b not in m:
            continue
        if Path(pa.rel_path).parent != Path(pb.rel_path).parent or incoming.get(b) != {a}:
            continue
        if Path(pb.rel_path).name.startswith("_index") or "```" in pb.text:
            continue
        merge = (a, b)
        break
    split = None
    for p in sorted(base, key=lambda x: x.doc_id):
        if p.doc_id not in m or p.doc_id in anchor_set or (merge and p.doc_id in merge):
            continue
        if Path(p.rel_path).name.startswith(("_index", "index")) or "```" in p.text:
            continue
        lines = p.text.splitlines(keepends=True)
        start = body_start(lines)
        link_lines = [i for i in range(start, len(lines))
                      if any(resolve_target(t, p.rel_path, rels, routes) for _, t in LINK.findall(lines[i]))]
        if len(link_lines) >= 2 and len(set(link_lines)) >= 2:
            cut = link_lines[len(link_lines) // 2]
            if cut > start:
                split = (p.doc_id, cut)
                break
    if merge is None or split is None:
        raise SystemExit("no-candidate-for-synthetic-edit")
    return by_id, merge, split, rels, routes


def synthetic_editor(by_id, merge, split, rels, routes):
    a, b = merge
    pa, pb = by_id[a], by_id[b]
    p_id, cut = split
    p = by_id[p_id]
    stem = Path(p.rel_path).stem
    new_rel = (Path(p.rel_path).parent / f"{stem}-continued.md").as_posix()

    def unlink_b(text, rel):
        def sub(match):
            target = resolve_target(match.group(2), rel, rels, routes)
            return match.group(1) if target == pb.rel_path else match.group(0)
        return LINK.sub(sub, text)

    def run(session):
        session.list_files()
        # Merge b into a (same directory, b linked only from a).
        ta, tb = session.read_file(pa.rel_path), session.read_file(pb.rel_path)
        lb = tb.splitlines(keepends=True)
        merged = unlink_b(ta, pa.rel_path).rstrip("\n") + "\n\n## " + title_of(tb, Path(pb.rel_path).stem) + "\n\n" + \
            unlink_b("".join(lb[body_start(lb):]), pa.rel_path).lstrip("\n")
        session.write_file(pa.rel_path, merged)
        session.delete_file(pb.rel_path)
        session.checkpoint("merge reciprocal pair into its only linking page")
        # Split p at a link line; both halves link to each other.
        tp = session.read_file(p.rel_path)
        lines = tp.splitlines(keepends=True)
        title = title_of(tp, stem)
        first = "".join(lines[:cut]).rstrip("\n") + f"\n\nContinued in [{title} (continued)]({stem}-continued.md).\n"
        second = f"---\ntitle: {title} (continued)\n---\n\n" + "".join(lines[cut:]).rstrip("\n") + \
            f"\n\nBack to [{title}]({stem}.md).\n"
        session.write_file(p.rel_path, first)
        session.write_file(new_rel, second)
        session.checkpoint("split page at a link line with reciprocal navigation links")
        session.finish("synthetic merge and split")
    run.plan = {"merge": {"into": pa.rel_path, "removed": pb.rel_path, "into_id": a, "removed_id": b},
                "split": {"page": p.rel_path, "page_id": p_id, "cut_line": cut, "new_page": new_rel}}
    return run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="sprint10-fixture-e2e")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True, help="live study manifest template (unresolved)")
    parser.add_argument("--variant-manifest", type=Path, default=V5, help="Sprint 10b: v6 docs scope; default v5 replay")
    parser.add_argument("--sprint", default="10")
    parser.add_argument("--date", default="2026-10-07")
    args = parser.parse_args()
    V = args.variant_manifest.resolve()
    vm = rr.read_json(V)
    scope = dcv.scope_of(vm)
    gm = dcv.pinned(vm)
    root = Path(vm["output_root"])
    manifests = root / "runs" / args.run_id / "manifests"
    manifest_path = manifests / "run-manifest.json"
    data = rr.build_manifest(run_id=args.run_id, variant_manifest_path=V, mode="fixture",
                             fixture_limits=rr.FIXTURE_LIMITS, created=args.date)
    manifest_sha = rr.write_new(manifest_path, data)
    template = rr.build_manifest(run_id="docker-r0-vs-o", variant_manifest_path=V, mode="live", created=args.date)
    template_sha = rr.write_new(args.template, template)
    try:
        rr.validate_manifest(template, live=True)
        live_refusal = None
    except rr.Refused as error:
        live_refusal = str(error)
    if live_refusal is None:
        raise SystemExit("template-unexpectedly-launchable")
    rr.validate_manifest(template, live=False)

    anchors = data["c_pin"]["anchors"]
    by_id, merge, split, rels, routes = plan_edit(gm, vm, anchors)
    editor = synthetic_editor(by_id, merge, split, rels, routes)
    states = {"O": rr.launch(manifest_path, "O", 1), "S": rr.launch(manifest_path, "S", 1),
              "R0": rr.launch(manifest_path, "R0", 1, editor=editor)}
    ns = {arm: root / "runs" / args.run_id / f"{arm}-r01" for arm in states}
    rec = lambda arm, name: rr.read_json(ns[arm] / "host/receipts" / name)
    grading = rr.grading_export(manifest_path, [ns[a] for a in ("O", "S", "R0")],
                                root / "runs" / args.run_id / "grading-export",
                                root / "runs" / args.run_id / "host-private/grading-key.json")
    key = rr.read_json(root / "runs" / args.run_id / "host-private/grading-key.json")
    by_arm = {v["arm"]: k for k, v in key["variants"].items()}
    gx = root / "runs" / args.run_id / "grading-export/variants"
    o_bytes, s_bytes = rr.tree_bytes(gx / by_arm["O"]), rr.tree_bytes(gx / by_arm["S"])
    recovered = {f"corpus/{p.name}": p.read_bytes() for p in sorted((DATASET / "corpus").glob("*.md"))}
    recovered["graph_edges.csv"] = (DATASET / "graph_edges.csv").read_bytes()
    # Sprint 10b: an unedited docs-scope export equals the in-scope subset of the
    # recovered export (corpus files of in-scope ids; graph rows with both ends in scope).
    out_ids = {r["doc_id"] for r in vm["source_policy"]["excluded"]["ids"]} if scope != dcv.DEFAULT_SCOPE else set()
    subset = {k: v for k, v in recovered.items() if k == "graph_edges.csv" or k[len("corpus/"):-3] not in out_ids}
    rows = recovered["graph_edges.csv"].split(b"\r\n")
    kept_rows = [rows[0]] + [r for r in rows[1:] if r and not set(r.decode().split(",")) & out_ids]
    subset["graph_edges.csv"] = b"\r\n".join(kept_rows) + b"\r\n"
    o_tree = ns["O"] / "editor/source"
    o_paths = [p.relative_to(o_tree).as_posix() for p in o_tree.rglob("*") if p.is_file()]
    forbidden = [x for x in o_paths if x.rsplit("/", 1)[-1].lower() in ("agents.md", "claude.md")
                 or x.lower().startswith(EXCLUDED_DIRS)]

    o_spec, r_spec = rec("O", "spectral-profile.json"), rec("R0", "spectral-profile.json")
    c = rec("R0", "c-admission.json")
    mapping = rec("R0", "mapping.json")
    delta = rr.spectral_delta(r_spec, o_spec)
    key_delta = {k: delta[k] for k in ("lwcc.0.25.lambda1", "lwcc.0.25.lambda2", "lwcc.0.25.lambda3",
                                       "lwcc.subspace_polarization_v1", "lwcc.normalized_lambda2", "lwcc.nodes",
                                       "lwcc.edges", "lwcc.outdegree_gini", "lwcc.indegree_gini", "lwcc.sources",
                                       "lwcc.sinks", "retrieval.nodes", "retrieval.edges", "retrieval.isolated", "T")}
    receipt_hashes = {arm: dict(s.get("receipts", {}), run_state=dcv.file_hash(ns[arm] / "host/run-state.json"),
                                profile_sha256=s.get("profile_sha256"), terminal_checkpoint=s.get("terminal_checkpoint"))
                      for arm, s in states.items()}
    summary = {
        "schema_version": rr.SCHEMA + ".fixture_e2e_summary", "sprint": args.sprint, "date": args.date,
        "variant_manifest": {"path": str(V), "scope": scope},
        "editor_tree": {"files": len(o_paths), "excluded_class_paths": forbidden,
                        "top_level": sorted({x.split("/", 1)[0] for x in o_paths})},
        "model_calls": 0, "network": False, "run_id": args.run_id,
        "run_manifest": {"path": str(manifest_path), "sha256": manifest_sha, "mode": "fixture"},
        "study_template": {"path": str(args.template), "sha256": template_sha, "live_refusal": live_refusal,
                           "unresolved": rr.unresolved_paths(template)},
        "templates_sha256": data["templates"]["sha256"], "variant_manifest_sha256": data["variant_manifest"]["sha256"],
        "statuses": {arm: s["status"] for arm, s in states.items()},
        "eligible_for_grading": {arm: s["eligible_for_grading"] for arm, s in states.items()},
        "synthetic_edit": editor.plan,
        "R0_editor": {k: states["R0"][k] for k in ("editor_tool_calls", "editor_model_calls", "editor_edits",
                                                     "editor_checkpoints", "flags")},
        "R0_mapping": {"mapping": mapping["mapping"], "unmapped": mapping["unmapped_removals"],
                       "partial_unit_loss": mapping["partial_unit_loss"], "added": mapping["added"],
                       "changed": mapping["changed"]},
        "R0_C": {k: c["result"][k] for k in ("passed", "reasons", "anchors_missing", "baseline_pages_left_main_component",
                                             "new_pages", "orphan_new_pages", "main_component_size", "lambda2",
                                             "lambda2_threshold", "baseline_lambda2")},
        "spectral_delta_R0_minus_O": key_delta,
        "O_vs_09c_F": rr.f_reference_check(o_spec),
        "S_equals_O_bytes": o_bytes == s_bytes, "S_files": len(s_bytes),
        "O_equals_recovered_dataset_bytes": o_bytes == recovered,
        "O_equals_recovered_in_scope_subset_bytes": o_bytes == subset, "recovered_in_scope_subset_files": len(subset),
        "O_retrieval_view": {k: o_spec["profile"]["retrieval_no_faq"][k] for k in ("nodes", "edges", "graph_sha256", "controls")},
        "O_lwcc_view": {k: o_spec["profile"]["retrieval_no_faq_lwcc"][k] for k in ("nodes", "edges", "graph_sha256", "controls")},
        "R0_C_baseline_graph_sha256": data["c_pin"]["baseline_graph_sha256"],
        "S_profile_equals_O": rec("S", "spectral-profile.json")["profile_sha256"] == o_spec["profile_sha256"],
        "grading_export": grading, "receipt_hashes": receipt_hashes,
        "scope": "fixture contract check on the real baseline with a scripted synthetic edit; not an editing result, "
                 "no answer, no grading; spectral deltas are descriptors only",
    }
    sha = rr.write_new(args.evidence, summary)
    print(json.dumps({"evidence_sha256": sha, "statuses": summary["statuses"], "C": summary["R0_C"],
                      "O_vs_09c_F": summary["O_vs_09c_F"], "S_equals_O": summary["S_equals_O_bytes"],
                      "O_equals_recovered": summary["O_equals_recovered_dataset_bytes"],
                      "O_equals_recovered_in_scope": summary["O_equals_recovered_in_scope_subset_bytes"],
                      "editor_tree": summary["editor_tree"], "O_retrieval_controls": summary["O_retrieval_view"]["controls"],
                      "delta": key_delta}, indent=1))


if __name__ == "__main__":
    main()

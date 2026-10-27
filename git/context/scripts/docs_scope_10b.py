"""Sprint 10b: documentation corpus scope `docs_scope_v1`, variant manifest v6 and re-scopes.

Stages (offline; no model, embedding, network or container):

  manifest     derive variant manifest v6 from v5: scope, frozen excluded IDs,
               new baseline counts and hashes, re-pinned C baseline
  rescope-09c  declared docs-scope re-scope of 09c in a NEW artifact root: version
               edge files, saved semantic rankings and support restricted to
               in-scope IDs; then the frozen 09c `measures`, `exposure` and
               `analyze` functions run unchanged against it
  check-09d    09d baseline, versions and synthetic edit families on the
               docs-scope graph; C admission pattern compared with the original
  summarize    compact tracked evidence (original against re-scoped)

The original 09c/09d artifacts and manifests v1-v5 are read, never written.
`--exclude none` builds the same harness without exclusions; it must reproduce
the original 09c exposure files byte for byte (equivalence check).
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")  # 09c measured single-threaded
os.environ.setdefault("OMP_NUM_THREADS", "1")

import argparse
import copy
import hashlib
import importlib.util
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import docker_corpus_variants as dcv  # noqa: E402

ROOT = dcv.ROOT
CONTEXT = ROOT / "git/context"
EVIDENCE = CONTEXT / "evidence"
V5 = EVIDENCE / "docker-corpus-variant-input-v5-structural-target-2026-10-07.json"
V6 = EVIDENCE / "docker-corpus-variant-input-v6-docs-scope-2026-10-07.json"
O9C = ROOT / "artifacts/orientation-spectra-2026-10-06"
RESCOPE = ROOT / "artifacts/orientation-spectra-2026-10-06-docs-scope"
O9D = ROOT / "artifacts/structural-target-2026-10-07"
O9D_RESCOPE = ROOT / "artifacts/structural-target-2026-10-07-docs-scope"
RESULTS_JSON = EVIDENCE / "docs-scope-v1-rescope-results-2026-10-07.json"
RESULTS_MD = EVIDENCE / "docs-scope-v1-rescope-results-2026-10-07.md"
SCOPE = "docs_scope_v1"
SCHEMA = "docspectra.icsc.docs_scope.v1"
EXPECTED = {"count": 45, "by_class": {"agent_instructions": 18, "site_build": 27}}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canon(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_09c():
    return load_module(CONTEXT / "scripts/orientation_spectra_09c.py", "o9c_frozen")


def excluded_ids(vm=None) -> set[str]:
    vm = vm or read_json(V6)
    return {r["doc_id"] for r in vm["source_policy"]["excluded"]["ids"]}


# ---------------------------------------------------------------- manifest v6

def manifest(args) -> None:
    v5 = read_json(V5)
    gm = dcv.pinned(v5)
    source = dcv.source_check(v5)
    excluded = dcv.excluded_record(gm, source, SCOPE)
    if {k: excluded[k] for k in EXPECTED} != EXPECTED:
        raise SystemExit(f"excluded-set-differs-from-author-table:{excluded['by_class']}")
    vm = copy.deepcopy(v5)
    vm["parent_manifest_sha256"] = dcv.file_hash(V5)
    vm["source_policy"] = dict(v5["source_policy"], scope=SCOPE, editor_input="discovered_non_faq_in_scope_pages_only",
                               excluded=excluded, decided="2026-10-07 author decision (Sprint 10b)",
                               document_root="repository root (doc ids unchanged)")
    all_pages = dcv.source_pages(gm, vm, source)
    ret = [p for p in all_pages if not p.is_faq]
    edges = gm.extract_markdown_edges(ret)
    v5_ret = [p for p in dcv.pages(gm, source) if not p.is_faq]
    v5_edges = gm.extract_markdown_edges(v5_ret)
    removed_edges = sorted(set(v5_edges) - set(edges))
    if set(edges) - set(v5_edges) or any(p.is_faq and dcv.excluded_class(p.rel_path, SCOPE) for p in dcv.pages(gm, source)):
        raise SystemExit("scope-added-edges-or-excluded-faq")

    sys.path.insert(0, str(ROOT / "git/RetrievalArena"))
    import restructuring_runs as rr
    st = rr.load_struct()
    o9, mp = rr.load_spectral()
    nodes = sorted(p.doc_id for p in ret)
    pin = rr.c_pin_from_variant_manifest(v5)
    base = st.baseline_summary(nodes, edges, pin["anchors"])
    T = st.anchored_reachability(nodes, edges, pin["anchors"], depth=3)
    v5_base = st.baseline_summary(sorted(p.doc_id for p in v5_ret), v5_edges, pin["anchors"])
    if (v5_base["graph_sha256"], v5_base["lambda2"], len(v5_base["main_component"])) != (
            pin["baseline_graph_sha256"], pin["baseline_lambda2"], pin["baseline_main_component_size"]):
        raise SystemExit("v5-c-baseline-does-not-reproduce")
    lambda2_equal = base["lambda2"] == v5_base["lambda2"]
    m_equal = base["main_component"] == v5_base["main_component"]
    if not (lambda2_equal and m_equal):
        # Narrowing: if any LWCC value moves, stop and report; never adjust tolerances.
        raise SystemExit(f"STOP-lwcc-value-moved: lambda2 {base['lambda2']!r} vs {v5_base['lambda2']!r}; "
                         f"|M| {len(base['main_component'])} vs {len(v5_base['main_component'])}")
    canon_edges = mp.canonical_edges(nodes, edges)
    comps = o9.weak_components(nodes, canon_edges)
    lwcc = sorted(comps[0])
    lset = set(lwcc)
    ledges = [(s, t) for s, t in canon_edges if s in lset and t in lset]
    vm["baseline"] = {
        "full_pages": len(all_pages), "retrieval_pages": len(ret), "retrieval_edges": len(edges),
        "graph_edges_sha256": dcv.digest(json.dumps(edges, separators=(",", ":")).encode()),
        "faq_pages_locked": sum(p.is_faq for p in all_pages),
        "views": {"retrieval_no_faq": {"nodes": len(nodes), "edges": len(canon_edges),
                                       "graph_sha256": st.graph_digest(nodes, canon_edges), "weak_components": len(comps),
                                       "controls": o9.degree_controls(nodes, canon_edges)},
                  "retrieval_no_faq_lwcc": {"nodes": len(lwcc), "edges": len(ledges),
                                            "graph_sha256": st.graph_digest(lwcc, ledges),
                                            "controls": o9.degree_controls(lwcc, ledges)}},
        "change_from_v5": {"retrieval_pages": len(ret) - len(v5_ret), "retrieval_edges": len(edges) - len(v5_edges),
                           "removed_edges": [list(e) for e in removed_edges],
                           "v5_graph_edges_sha256": v5["baseline"]["graph_edges_sha256"],
                           "excluded_in_main_component": sorted(excluded_ids({"source_policy": {"excluded": excluded}})
                                                                & set(v5_base["main_component"]))},
    }
    sp = copy.deepcopy(v5["structural_target_pin"])
    sp["admission_rule"]["baseline"] = (
        f"locked docs_scope_v1 retrieval_no_faq graph (F restricted to {len(nodes)} in-scope pages); "
        f"edge-set graph_sha256 {base['graph_sha256']}; lambda2 {base['lambda2']!r}; |M| {len(base['main_component'])}")
    sp["repinned"] = {"sprint": "10b", "date": "2026-10-07", "from_manifest_sha256": dcv.file_hash(V5),
                      "v5_graph_sha256": v5_base["graph_sha256"], "graph_sha256": base["graph_sha256"],
                      "lambda2_bit_identical_to_v5": lambda2_equal, "main_component_identical_to_v5": m_equal,
                      "T_F": T["value"], "T_F_unchanged": T["value"] == st.anchored_reachability(
                          sorted(p.doc_id for p in v5_ret), v5_edges, pin["anchors"], depth=3)["value"],
                      "code_config_anchors_unchanged": True}
    vm["structural_target_pin"] = sp
    vm["recovered_export"] = dict(v5["recovered_export"], scope_note=(
        "the recovered export is the 1263-page repository_root dataset; under docs_scope_v1 an unedited O/S export "
        "equals its in-scope subset (corpus files of in-scope ids; graph rows with both endpoints in scope)"))
    vm["sprint_10b"] = {"decision": "experiment corpus = product and contributor documentation; agent-instruction and "
                                    "site-build files excluded", "views": list(dcv.VIEWS),
                        "full_view": "in-scope retrieval pages plus the 26 locked FAQ pages (all under content/)"}
    dcv.write_new(V6, vm)
    rr_pin = rr.c_pin_from_variant_manifest(read_json(V6))
    if (rr_pin["baseline_graph_sha256"], rr_pin["baseline_lambda2"], rr_pin["baseline_main_component_size"]) != (
            base["graph_sha256"], base["lambda2"], len(base["main_component"])):
        raise SystemExit("v6-c-pin-does-not-round-trip")
    print(json.dumps({"v6_sha256": dcv.file_hash(V6), "excluded": excluded["by_class"],
                      "excluded_sha256": excluded["sha256"], "baseline": {k: vm["baseline"][k] for k in
                      ("full_pages", "retrieval_pages", "retrieval_edges", "graph_edges_sha256")},
                      "removed_edges": removed_edges, "c_graph_sha256": base["graph_sha256"],
                      "lambda2": base["lambda2"], "M": len(base["main_component"]), "T_F": T["value"],
                      "lwcc_graph_sha256": vm["baseline"]["views"]["retrieval_no_faq_lwcc"]["graph_sha256"]}, indent=1))


# ---------------------------------------------------------------- 09c re-scope

def build_root(dest: Path, drop: set[str], o9) -> dict:
    """Restricted copy of the 09c inputs under `dest`; the original root is only read."""
    dest = dest.resolve()
    if dest.exists():
        raise SystemExit(f"output-root-exists:{dest}")
    if dest == O9C.resolve() or O9C.resolve() in dest.parents:
        raise SystemExit("refusing-to-write-into-original-09c-root")
    inputs = read_json(o9.INPUT_EVIDENCE)
    o9.check_plan(O9C)  # original plan and inputs untouched
    nodes = sorted(p.stem for p in (O9C / "retrieval-input/corpus").glob("*.md"))
    keep = [d for d in nodes if d not in drop]
    keep_set = set(keep)
    (dest / "retrieval-input/corpus").mkdir(parents=True)
    for d in keep:
        shutil.copyfile(O9C / "retrieval-input/corpus" / f"{d}.md", dest / "retrieval-input/corpus" / f"{d}.md")
    shutil.copyfile(O9C / "analysis-plan.sha256", dest / "analysis-plan.sha256")

    versions, edge_changes = [], {}
    for rec in inputs["versions"]:
        vid = rec["version_id"]
        new = dict(rec)
        for fname, key, count in (("graph_edges.csv", "edge_file_sha256", "directed_edges"),
                                  ("graph_edges_sensitivity.csv", "sensitivity_edge_file_sha256", "sensitivity_edges")):
            src = O9C / "versions" / vid / fname
            if dcv.file_hash(src) != rec[key]:
                raise SystemExit(f"original-edge-file-hash-mismatch:{vid}:{fname}")
            edges = o9.read_edges_csv(src)
            kept = [(s, t) for s, t in edges if s in keep_set and t in keep_set]
            data = o9.edges_csv_bytes(kept)
            out = dest / "versions" / vid / fname
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            new[key], new[count] = sha256_bytes(data), len(kept)
            edge_changes[f"{vid}:{fname}"] = len(edges) - len(kept)
        new["skeleton_pairs"] = len({(min(s, t), max(s, t)) for s, t in o9.read_edges_csv(dest / "versions" / vid / "graph_edges.csv")})
        versions.append(new)

    f_edges = o9.read_edges_csv(dest / "versions/F/graph_edges.csv")
    lwcc = sorted(o9.weak_components(keep, f_edges)[0])
    original_lwcc = read_json(O9C / "lwcc-nodes.json")
    (dest / "lwcc-nodes.json").write_text(json.dumps(lwcc) + "\n", encoding="utf-8")

    sem = read_json(O9C / "semantic/semantic.json")
    support = read_json(O9C / "support.json")
    support_dropped, empty = {}, []
    new_support = {}
    for qid, docs in support.items():
        kept = [d for d in docs if d in keep_set]
        if len(kept) != len(docs):
            support_dropped[qid] = len(docs) - len(kept)
        if kept:
            new_support[qid] = kept
        else:
            empty.append(qid)
    rankings = {qid: [[d, s] for d, s in ranked if d in keep_set]
                for qid, ranked in sem["rankings"].items() if qid not in empty}
    best_rank = {}
    for qid, ranked in sem["rankings"].items():
        for i, (d, _) in enumerate(ranked):
            if d in drop:
                best_rank[d] = min(best_rank.get(d, 10 ** 9), i + 1)
    (dest / "semantic").mkdir()
    sem_out = dict(sem, rankings=rankings)
    (dest / "semantic/semantic.json").write_text(json.dumps(sem_out, sort_keys=True) + "\n", encoding="utf-8")
    (dest / "support.json").write_text(json.dumps(new_support, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    scaffold = read_json(O9C / "scaffold-pages.json")
    (dest / "scaffold-pages.json").write_text(json.dumps({k: v for k, v in scaffold.items() if k in keep_set},
                                                         indent=1, sort_keys=True) + "\n", encoding="utf-8")

    ver = read_json(O9C / "e009-verification/verification.json")
    (dest / "e009-verification").mkdir()
    (dest / "e009-verification/verification.json").write_text(json.dumps({
        "schema_version": SCHEMA + ".e009_verification_reference", "admitted": ver["admitted"],
        "basis": "original 09c container verification of the pinned runner's host replay; the replay code is "
                 "unchanged and runs on restricted inputs; the container is not re-run (out of scope, offline)",
        "original_verification_sha256": dcv.file_hash(O9C / "e009-verification/verification.json")},
        indent=1, sort_keys=True) + "\n", encoding="utf-8")

    rescoped_inputs = dict(inputs, versions=versions, status="docs_scope_rescope_of_frozen_inputs",
                           rescope={"of_input_evidence_sha256": dcv.file_hash(o9.INPUT_EVIDENCE),
                                    "excluded_ids_sha256": sha256_bytes(canon(sorted(drop))),
                                    "excluded_count": len(drop), "rule": "versions, rankings and support restricted to "
                                    "in-scope ids; orientation of every remaining edge unchanged"})
    rescoped_inputs["graph"] = dict(inputs["graph"], nodes=len(keep), directed_edges=len(f_edges),
                                    lwcc_nodes=len(lwcc), lwcc_node_list_sha256=sha256_bytes(canon(lwcc)))
    rescoped_inputs["questions"] = dict(inputs["questions"], count=len(new_support),
                                        ids_sha256=sha256_bytes(canon(sorted(new_support))),
                                        support_sha256=sha256_bytes(canon(new_support)))
    (dest / "inputs-rescoped.json").write_text(json.dumps(rescoped_inputs, indent=1, sort_keys=True) + "\n",
                                               encoding="utf-8")
    report = {"excluded": len(drop), "nodes": len(keep), "lwcc_nodes": len(lwcc), "lwcc_identical": lwcc == original_lwcc,
              "edges_removed": {k: v for k, v in edge_changes.items() if v}, "support_lists_changed": support_dropped,
              "questions_with_empty_support": sorted(empty), "questions": len(new_support),
              "excluded_best_semantic_rank": dict(sorted(best_rank.items())),
              "scaffold_pages_dropped": sorted(set(scaffold) - keep_set)}
    (dest / "build-report.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return report


def frozen_stage(o9, dest: Path, stage: str):
    """Run a frozen 09c stage unchanged against `dest` with the re-scoped inputs evidence."""
    original_inputs, original_check = o9.INPUT_EVIDENCE, o9.check_plan

    def check_plan(root):
        o9.INPUT_EVIDENCE = original_inputs
        try:
            plan = original_check(O9C)  # the frozen plan and original inputs are verified, not replaced
        finally:
            o9.INPUT_EVIDENCE = dest / "inputs-rescoped.json"
        if (Path(root) / "analysis-plan.sha256").read_text(encoding="utf-8").split()[0] != plan:
            raise SystemExit("rescope-root-plan-mismatch")
        return plan

    o9.INPUT_EVIDENCE, o9.check_plan = dest / "inputs-rescoped.json", check_plan
    try:
        getattr(o9, stage)(SimpleNamespace(root=dest))
    finally:
        o9.INPUT_EVIDENCE, o9.check_plan = original_inputs, original_check


def rescope_09c(args) -> None:
    o9 = load_09c()
    drop = set() if args.exclude == "none" else excluded_ids()
    dest = Path(args.output).resolve() if args.output else RESCOPE
    report = build_root(dest, drop, o9)
    print(json.dumps(report, indent=1, sort_keys=True)[:3000], flush=True)
    stages = ["exposure"] if args.exposure_only else ["measures", "exposure", "analyze"]
    for stage in stages:
        frozen_stage(o9, dest, stage)


# ---------------------------------------------------------------- 09d check

def check_09d(args) -> None:
    import numpy as np
    m9 = load_module(CONTEXT / "scripts/structural_target_09d.py", "s09d_frozen")
    st = m9.load_struct()
    proto, proto_sha = m9.load_protocol()
    orig = read_json(O9D / "qualification.json")
    if dcv.file_hash(O9D / "qualification.json") != "a509d3b29538a87f9487b3ac0de8c2d632bd568d9881143199a2fff80d98cbea":
        raise SystemExit("original-09d-receipt-changed")
    nodes = sorted(p.stem for p in (RESCOPE / "retrieval-input/corpus").glob("*.md"))
    inputs = read_json(RESCOPE / "inputs-rescoped.json")
    depth, A1, A2 = proto["T"]["depth"], proto["anchors"]["A1"], proto["anchors"]["A2"]
    graphs = {}
    for rec in inputs["versions"]:
        for tag, fname, key in (("main", "graph_edges.csv", "edge_file_sha256"),
                                ("sensitivity", "graph_edges_sensitivity.csv", "sensitivity_edge_file_sha256")):
            path = RESCOPE / "versions" / rec["version_id"] / fname
            if dcv.file_hash(path) != rec[key]:
                raise SystemExit(f"rescoped-edge-file-hash-mismatch:{rec['version_id']}:{tag}")
            graphs[(rec["version_id"], tag)] = m9.read_edges(path)
    base = {tag: st.baseline_summary(nodes, graphs[("F", tag)], A1) for tag in ("main", "sensitivity")}
    f_edges = graphs[("F", "main")]
    t_f = st.anchored_reachability(nodes, f_edges, A1, depth)
    fm = base["main"]["main_component"]
    vm6 = read_json(V6)
    excluded = excluded_ids(vm6)
    vrows = []
    for rec in inputs["versions"]:
        vid = rec["version_id"]
        row = {"version_id": vid, "family": rec["family"]}
        for tag in ("main", "sensitivity"):
            row[tag] = m9.evaluate(st, nodes, graphs[(vid, tag)], base[tag], A1, depth,
                                   a2=A2 if tag == "main" else None, pairs=False)
        vrows.append(row)
    dist = st.forward_distances(nodes, f_edges, A1)
    unreached = sorted(v for v in fm if dist.get(v, depth + 1) > depth)
    erows = []
    for fam, spec in sorted(proto["edits"]["families"].items()):
        fid = spec["id"][:2]
        for size in spec["sizes"]:
            for rep in range(proto["edits"]["replicates"]):
                ed = m9.make_edit(fid, size, rep, nodes, f_edges, set(fm), A1, unreached, st)
                row = m9.evaluate(st, ed["nodes"], ed["edges"], base["main"], A1, depth, mapping=ed["mapping"], pairs=False)
                row |= {"edit": spec["id"], "class": spec["class"], "size": size, "replicate": rep,
                        "delta_T": row["T"] - t_f["value"],
                        "edit_spec_sha256": hashlib.sha256(json.dumps(
                            {k: ed[k] for k in ("added", "removed", "new_nodes", "mapping")}, sort_keys=True).encode()).hexdigest()}
                erows.append(row)
    cls = lambda c: [r for r in erows if r["class"] == c]
    nonsplit = [r["main"] for r in vrows] + [r["sensitivity"] for r in vrows]
    c01 = [(r.get("edit", "09c"), r.get("size"), r.get("replicate"), r["C_reasons"]) for r in cls("legitimate") + nonsplit
           if any(x.startswith(("C0", "C1")) for x in r["C_reasons"])]
    c2 = [(r.get("edit", "09c"), r.get("size"), r.get("replicate")) for r in cls("legitimate") + nonsplit
          if "C2-lambda2-below-tolerance" in r["C_reasons"]]
    eps = proto["admission"]["padding_delta_T_epsilon"]
    unrejected = [(r["edit"], r["size"], r["replicate"], r["delta_T"]) for r in cls("padding")
                  if r["guard_pass"] and r["delta_T"] > eps]
    admission = {"fragmentation_all_fail": all(not r["C_pass"] for r in cls("fragmentation")),
                 "fragmentation_all_fail_without_C2": all(not r["C_without_C2_pass"] for r in cls("fragmentation")),
                 "C0_C1_false_rejections": c01, "C2_false_rejections": c2, "C2_retained": not c2,
                 "T_headroom": .05 <= t_f["value"] <= .95, "T_padding_not_rejected": len(unrejected)}
    admission["C_admissible"] = (admission["fragmentation_all_fail"] and not c01 and not c2)
    admission["decision"] = "admit_C_only" if admission["C_admissible"] and (
        not admission["T_headroom"] or unrejected) else "changed"
    okey = {(r["edit"], r["size"], r["replicate"]): r for r in orig["edits"]}
    per_edit = []
    for r in erows:
        o = okey[(r["edit"], r["size"], r["replicate"])]
        per_edit.append({"edit": r["edit"], "size": r["size"], "replicate": r["replicate"],
                         "same_edit_spec": r["edit_spec_sha256"] == o["edit_spec_sha256"],
                         "C_pass": r["C_pass"], "original_C_pass": o["C_pass"], "C_reasons": r["C_reasons"],
                         "guard_pass": r["guard_pass"], "original_guard_pass": o["guard_pass"],
                         "delta_T": r["delta_T"], "original_delta_T": o["delta_T"]})
    ovk = {r["version_id"]: r for r in orig["versions"]}
    per_version = [{"version_id": r["version_id"],
                    **{f"{tag}_{k}": r[tag][k] for tag in ("main", "sensitivity") for k in ("C_pass", "T")},
                    **{f"original_{tag}_{k}": ovk[r["version_id"]][tag][k] for tag in ("main", "sensitivity")
                       for k in ("C_pass", "T")}} for r in vrows]
    traces = sorted((ROOT / proto["inputs"]["luna_traces_root"]).glob("docker_docs-*.json"))
    trace_hits = {p.name: sorted(d for d in excluded if d in p.read_text(encoding="utf-8")) for p in traces}
    result = {"schema_version": SCHEMA + ".09d_check", "protocol_sha256": proto_sha,
              "structural_code_sha256": m9.STRUCT_CODE_SHA256, "rescope_inputs_sha256": dcv.file_hash(RESCOPE / "inputs-rescoped.json"),
              "baselines": {tag: {k: v for k, v in b.items() if k not in ("nodes", "main_component")} |
                            {"main_component_size": len(b["main_component"])} for tag, b in base.items()},
              "original_baselines": orig["baselines"],
              "main_component_identical": sorted(fm) == sorted(st.baseline_summary(
                  sorted(p.stem for p in (O9C / "retrieval-input/corpus").glob("*.md")),
                  m9.read_edges(O9C / "versions/F/graph_edges.csv"), A1)["main_component"]),
              "T_F": t_f["value"], "original_T_F": orig["admission"]["T"]["T_F"],
              "T_F_equal": t_f["value"] == orig["admission"]["T"]["T_F"], "T_profile": t_f["profile"],
              "unreached_baseline_M_pages": len(unreached), "original_unreached": orig["unreached_baseline_M_pages"],
              "versions": per_version, "edits": per_edit, "admission": admission,
              "original_admission": orig["admission"], "luna_docker_traces": len(traces),
              "luna_docker_traces_with_excluded_ids": {k: v for k, v in trace_hits.items() if v},
              "numpy": np.__version__}
    O9D_RESCOPE.mkdir(parents=True, exist_ok=False)
    dcv.write_new(O9D_RESCOPE / "check.json", result)
    same_c = sum(e["C_pass"] == e["original_C_pass"] for e in per_edit)
    print(json.dumps({"check_sha256": dcv.file_hash(O9D_RESCOPE / "check.json"), "T_F": t_f["value"],
                      "T_F_equal": result["T_F_equal"], "M_identical": result["main_component_identical"],
                      "baseline_main": result["baselines"]["main"], "admission": admission,
                      "edits_same_C": f"{same_c}/{len(per_edit)}",
                      "edits_same_spec": sum(e["same_edit_spec"] for e in per_edit),
                      "versions_C_all_pass": all(v["main_C_pass"] and v["sensitivity_C_pass"] for v in per_version),
                      "trace_hits": result["luna_docker_traces_with_excluded_ids"]}, indent=1))


# ---------------------------------------------------------------- summary

RATES = ("expansion_added_rate", "nonseed_added_rate", "support_in_final_rate", "support_in_seeds_rate",
         "top1_expansion_added_rate", "mean_candidate_pool", "mean_reachable", "mean_seed_outdegree",
         "fallback_question_rate")
PER_Q = ("seed", "selected", "fallback", "support_in_seeds", "support_in_final", "expansion_added", "nonseed_added",
         "top1_expansion_added", "candidate_pool", "expanded_reachable", "seed_outdegree")


def compare_measures(old, new):
    ov, nv = ({r["version_id"]: r for r in m["versions"]} for m in (old, new))
    lwcc_identical, retrieval = [], {}
    for vid in ov:
        for tag in ("main", "sensitivity"):
            a, b = ov[vid][tag], nv[vid][tag]
            if a["lwcc"] == b["lwcc"] and a["lwcc_nodes"] == b["lwcc_nodes"]:
                lwcc_identical.append(f"{vid}:{tag}")
            if vid in ("F", "R", "U"):
                retrieval[f"{vid}:{tag}"] = {
                    "original": {**{k: a["retrieval"]["controls"][k] for k in ("nodes", "edges", "isolated", "sources", "sinks",
                                                                              "zero_outdegree", "outdegree_gini", "indegree_gini")},
                                 **{f"lambda{i}_q0.25": a["retrieval"]["spectra"]["0.25"][f"lambda{i}"] for i in (1, 2)}},
                    "rescoped": {**{k: b["retrieval"]["controls"][k] for k in ("nodes", "edges", "isolated", "sources", "sinks",
                                                                              "zero_outdegree", "outdegree_gini", "indegree_gini")},
                                 **{f"lambda{i}_q0.25": b["retrieval"]["spectra"]["0.25"][f"lambda{i}"] for i in (1, 2)}}}
    return {"lwcc_bit_identical": len(lwcc_identical), "lwcc_compared": 2 * len(ov),
            "lwcc_sensitivity_nodes": [old["lwcc_sensitivity_nodes"], new["lwcc_sensitivity_nodes"]],
            "lwcc_sensitivity_identical": old["lwcc_sensitivity_sha256"] == new["lwcc_sensitivity_sha256"],
            "retrieval_view_F_R_U": retrieval}


def compare_exposure(old_root, new_root):
    old, new = (read_json(r / "exposure/exposure.json") for r in (old_root, new_root))
    os_, ns_ = ({(s["version_id"], s["edge_set"]): s for s in e["summary"]} for e in (old, new))
    rows, changed_q, changed_any = [], {}, set()
    for key in os_:
        a, b = os_[key], ns_[key]
        rows.append({"version_id": key[0], "edge_set": key[1], "questions": [a["questions"], b["questions"]],
                     **{k: [a[k], b[k]] for k in RATES}})
        oq = {x["question_id"]: x for x in read_jsonl(old_root / "exposure" / f"{key[0]}-{key[1]}.jsonl")}
        nq = {x["question_id"]: x for x in read_jsonl(new_root / "exposure" / f"{key[0]}-{key[1]}.jsonl")}
        diff = {}
        for qid in sorted(oq):
            if qid not in nq:
                diff[qid] = ["question_removed"]
                continue
            fields = [k for k in PER_Q if oq[qid][k] != nq[qid][k]]
            if fields:
                diff[qid] = fields
                changed_any.add(qid)
        changed_q[f"{key[0]}:{key[1]}"] = diff
    flips = {k: {"expansion_added_changed": sum("expansion_added" in f for f in d.values()),
                 "support_in_final_changed": sum("support_in_final" in f for f in d.values()),
                 "selected_changed": sum("selected" in f for f in d.values()), "questions_changed": len(d)}
             for k, d in changed_q.items()}
    return {"rows": rows, "per_question_change_counts": flips,
            "questions_changed_in_any_version": sorted(changed_any),
            "F_R_U_question_changes": {k: v for k, v in changed_q.items() if k.split(":")[0] in ("F", "R", "U")}}


def analysis_digest(a):
    out = {}
    for tag, fam in a["families"].items():
        t = fam["tests"]
        out[tag] = {
            "primary": {k: {"rho": v["rho"], "p": v["p"], "holm_p": v["holm_p"], "bootstrap95": v["bootstrap95"]}
                        for k, v in t["primary"].items()},
            "incremental": {k: {"joint_rho_partial": v["joint"]["rho_partial"], "joint_p": v["joint"]["p"],
                                "reached_stage": v["reached_stage"], "holm_p": v["holm_p"],
                                "adds_information": v["adds_information"], "loo_r2_controls": v["loo_r2_controls"],
                                "loo_r2_controls_plus_measure": v["loo_r2_controls_plus_measure"]}
                            for k, v in t["incremental"].items()},
            "robustness": {k: {"random20_rho": v["random20"]["rho"], "random20_p": v["random20"]["p"],
                               "partial9_rho": v["partial9_rho"], "sign_agrees": v["sign_agrees"]}
                           for k, v in t["robustness"].items()},
            "secondary_descriptive": t["secondary_descriptive"],
            "contrasts": fam["contrasts"], "sanity": fam["sanity"]}
    return out


def conclusions(d):
    m, s = d["main"], d["sensitivity"]
    r = lambda fam: fam["contrasts"]["rates"]
    return {"no_primary_measure_adds_information": not any(v["adds_information"] for f in (m, s)
                                                           for v in f["incremental"].values()),
            "main_primary_not_significant": all(v["holm_p"] is None or v["holm_p"] >= .05 for v in m["primary"].values()),
            "sensitivity_lambda1_significant": s["primary"]["lambda1_q0.25"]["holm_p"] < .05,
            "ordering_U_gt_R_gt_F": {tag: r(f)["U"] > r(f)["R"] > r(f)["F"] for tag, f in (("main", m), ("sensitivity", s))},
            "F_minus_U_significant": {tag: f["contrasts"]["F-U"]["exact_p"] < .05 for tag, f in (("main", m), ("sensitivity", s))},
            "F_minus_R_significant": {tag: f["contrasts"]["F-R"]["exact_p"] < .05 for tag, f in (("main", m), ("sensitivity", s))},
            "R_minus_U_significant": {tag: f["contrasts"]["R-U"]["exact_p"] < .05 for tag, f in (("main", m), ("sensitivity", s))}}


def read_jsonl(path: Path):
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


def fmt(x, nd=3):
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, float):
        return f"{x:.{nd}g}" if abs(x) < 1e-3 and x != 0 else f"{x:.{nd}f}"
    return str(x)


def fmtp(x):
    return "—" if x is None else f"{x:.2g}"


def render(args) -> None:
    """Re-render the Markdown from the frozen evidence JSON (the JSON is never rewritten)."""
    RESULTS_MD.write_text(render_md(read_json(RESULTS_JSON), dcv.file_hash(RESULTS_JSON)), encoding="utf-8", newline="\n")
    print(json.dumps({"md_sha256": dcv.file_hash(RESULTS_MD)}))


def summarize(args) -> None:
    build = read_json(RESCOPE / "build-report.json")
    ma, mb = read_json(O9C / "measures/measures.json"), read_json(RESCOPE / "measures/measures.json")
    aa, ab = read_json(O9C / "analysis/analysis.json"), read_json(RESCOPE / "analysis/analysis.json")
    da, db = analysis_digest(aa), analysis_digest(ab)
    ca, cb = conclusions(da), conclusions(db)
    expo = compare_exposure(O9C, RESCOPE)
    meas = compare_measures(ma, mb)
    check = read_json(O9D_RESCOPE / "check.json")
    vm6 = read_json(V6)
    hashes = {"v6_manifest": dcv.file_hash(V6), "excluded_ids": vm6["source_policy"]["excluded"]["sha256"],
              "rescope_inputs": dcv.file_hash(RESCOPE / "inputs-rescoped.json"),
              "rescope_semantic": dcv.file_hash(RESCOPE / "semantic/semantic.json"),
              "rescope_support": dcv.file_hash(RESCOPE / "support.json"),
              "rescope_measures": dcv.file_hash(RESCOPE / "measures/measures.json"),
              "rescope_exposure": dcv.file_hash(RESCOPE / "exposure/exposure.json"),
              "rescope_analysis": dcv.file_hash(RESCOPE / "analysis/analysis.json"),
              "original_measures": dcv.file_hash(O9C / "measures/measures.json"),
              "original_exposure": dcv.file_hash(O9C / "exposure/exposure.json"),
              "original_analysis": dcv.file_hash(O9C / "analysis/analysis.json"),
              "09d_check": dcv.file_hash(O9D_RESCOPE / "check.json"),
              "original_09d_qualification": dcv.file_hash(O9D / "qualification.json"),
              "script": dcv.file_hash(Path(__file__))}
    result = {"schema_version": SCHEMA + ".rescope_results", "sprint": "10b", "date": "2026-10-07",
              "label": "declared docs_scope_v1 re-scope beside the October 6 pre-registered 09c analysis and the "
                       "October 7 09d qualification; not a replacement and not the pre-registered analysis",
              "support_label": "automatic candidate support (historical lexical top_docs), not qrels",
              "model_calls": 0, "network": False, "container_runs": 0,
              "build": build, "hashes": hashes, "measures": meas,
              "exposure": {"rows": expo["rows"], "per_question_change_counts": expo["per_question_change_counts"],
                           "questions_changed_in_any_version": expo["questions_changed_in_any_version"],
                           "F_R_U_question_changes": expo["F_R_U_question_changes"]},
              "analysis": {"original": da, "rescoped": db},
              "conclusions": {"original": ca, "rescoped": cb, "all_hold": ca == cb},
              "09d": {k: check[k] for k in ("T_F", "original_T_F", "T_F_equal", "main_component_identical", "baselines",
                                            "original_baselines", "admission", "unreached_baseline_M_pages",
                                            "original_unreached", "luna_docker_traces",
                                            "luna_docker_traces_with_excluded_ids")} | {
                  "edits_same_C": sum(e["C_pass"] == e["original_C_pass"] for e in check["edits"]),
                  "edits_same_guard": sum(e["guard_pass"] == e["original_guard_pass"] for e in check["edits"]),
                  "edits_same_spec": sum(e["same_edit_spec"] for e in check["edits"]), "edits": len(check["edits"]),
                  "edit_rows": [{k: e[k] for k in ("edit", "size", "replicate", "same_edit_spec", "C_pass", "original_C_pass",
                                                   "guard_pass", "original_guard_pass", "delta_T", "original_delta_T")}
                                for e in check["edits"]],
                  "versions_C_all_pass": all(v["main_C_pass"] and v["sensitivity_C_pass"] for v in check["versions"]),
                  "versions_T_equal": sum(v["main_T"] == v["original_main_T"] and v["sensitivity_T"] == v["original_sensitivity_T"]
                                          for v in check["versions"]), "versions": len(check["versions"])}}
    dcv.write_new(RESULTS_JSON, json.loads(json.dumps(result, allow_nan=False)))
    sha = dcv.file_hash(RESULTS_JSON)
    RESULTS_MD.write_text(render_md(result, sha), encoding="utf-8", newline="\n")
    print(json.dumps({"results_sha256": sha, "md_sha256": dcv.file_hash(RESULTS_MD), "conclusions_hold": ca == cb,
                      "rescoped": cb}, indent=1))


def render_md(r, sha):
    L = [f"# Docs-scope re-scope of 09c and 09d (Sprint 10b)", "",
         f"Evidence JSON: `{RESULTS_JSON.name}` (`{sha[:12]}…`). " + r["label"] + ".",
         "Offline: no model, embedding, network or container call. Support is automatic candidate support, not qrels.", "",
         "## Inputs", "",
         f"- Excluded IDs: {r['build']['excluded']}; in-scope nodes: {r['build']['nodes']}; LWCC identical: "
         f"{fmt(r['build']['lwcc_identical'])} ({r['build']['lwcc_nodes']} nodes).",
         f"- Questions: {r['build']['questions']}; questions with empty support after the re-scope: "
         f"{len(r['build']['questions_with_empty_support'])}; support lists changed: {len(r['build']['support_lists_changed'])}.",
         f"- Best 1-based semantic rank any excluded page reached: {min(r['build']['excluded_best_semantic_rank'].values())} "
         f"(agent-instruction pages: {min(v for k, v in r['build']['excluded_best_semantic_rank'].items() if not k.startswith(('static_', 'layouts_')))}).",
         f"- Edge rows removed per version: " + ", ".join(sorted({f'{v}' for v in r['build']['edges_removed'].values()})) +
         f" (in {len(r['build']['edges_removed'])} version files).",
         f"- LWCC measures bit-identical: {r['measures']['lwcc_bit_identical']} of {r['measures']['lwcc_compared']} "
         f"version × edge-set records; sensitivity LWCC identical: {fmt(r['measures']['lwcc_sensitivity_identical'])}.", "",
         "## Retrieval view (F, R, U): original → re-scoped", "",
         "| Version | Nodes | Edges | Isolated | Sources | Sinks | Out-degree Gini | λ₁ q=.25 | λ₂ q=.25 |",
         "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for k, v in r["measures"]["retrieval_view_F_R_U"].items():
        a, b = v["original"], v["rescoped"]
        L.append(f"| {k} | " + " | ".join(f"{fmt(a[x], 5)} → {fmt(b[x], 5)}" for x in
                                          ("nodes", "edges", "isolated", "sources", "sinks", "outdegree_gini",
                                           "lambda1_q0.25", "lambda2_q0.25")) + " |")
    L += ["", "## Exposure rates: original → re-scoped", "",
          "| Version | Edge set | Expansion-added | Support in final | Support in seeds | Fallback questions | Questions changed |",
          "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    pq = r["exposure"]["per_question_change_counts"]
    for row in r["exposure"]["rows"]:
        k = f"{row['version_id']}:{row['edge_set']}"
        L.append(f"| {row['version_id']} | {row['edge_set']} | " + " | ".join(
            f"{fmt(row[x][0])} → {fmt(row[x][1])}" for x in ("expansion_added_rate", "support_in_final_rate",
                                                              "support_in_seeds_rate", "fallback_question_rate")) +
                 f" | {pq[k]['questions_changed']} |")
    L += ["", f"Questions changed in any version: {len(r['exposure']['questions_changed_in_any_version'])} "
              f"({', '.join(r['exposure']['questions_changed_in_any_version'])}).", "",
          "## Frozen analysis: original → re-scoped", "",
          "| Family | Measure | ρ | Holm p | Joint partial ρ (p) | Adds info |", "| --- | --- | ---: | ---: | --- | --- |"]
    for tag in ("main", "sensitivity"):
        for k in ("lambda1_q0.25", "lambda2_q0.25"):
            a, b = r["analysis"]["original"][tag], r["analysis"]["rescoped"][tag]
            L.append(f"| {tag} | {k} | {fmt(a['primary'][k]['rho'])} → {fmt(b['primary'][k]['rho'])} | "
                     f"{fmtp(a['primary'][k]['holm_p'])} → {fmtp(b['primary'][k]['holm_p'])} | "
                     f"{fmt(a['incremental'][k]['joint_rho_partial'])} ({fmtp(a['incremental'][k]['joint_p'])}) → "
                     f"{fmt(b['incremental'][k]['joint_rho_partial'])} ({fmtp(b['incremental'][k]['joint_p'])}) | "
                     f"{fmt(a['incremental'][k]['adds_information'])} → {fmt(b['incremental'][k]['adds_information'])} |")
    L += ["", "| Family | Contrast | Difference | 95% CI | Exact p |", "| --- | --- | ---: | --- | ---: |"]
    for tag in ("main", "sensitivity"):
        for c in ("F-R", "F-U", "R-U"):
            a, b = r["analysis"]["original"][tag]["contrasts"][c], r["analysis"]["rescoped"][tag]["contrasts"][c]
            L.append(f"| {tag} | {c} | {fmt(a['difference'])} → {fmt(b['difference'])} | "
                     f"[{fmt(a['bootstrap95'][0])}, {fmt(a['bootstrap95'][1])}] → [{fmt(b['bootstrap95'][0])}, {fmt(b['bootstrap95'][1])}] | "
                     f"{fmtp(a['exact_p'])} → {fmtp(b['exact_p'])} |")
    L += ["", "## Conclusions", "", "| Check | Original | Re-scoped |", "| --- | --- | --- |"]
    for k, v in r["conclusions"]["original"].items():
        L.append(f"| {k} | {fmt(v) if not isinstance(v, dict) else ', '.join(f'{a}: {fmt(b)}' for a, b in v.items())} | "
                 f"{fmt(r['conclusions']['rescoped'][k]) if not isinstance(v, dict) else ', '.join(f'{a}: {fmt(b)}' for a, b in r['conclusions']['rescoped'][k].items())} |")
    L += ["", f"All conclusion checks unchanged: **{fmt(r['conclusions']['all_hold'])}**.", "",
          "## 09d on the docs-scope graph", ""]
    d = r["09d"]
    L += [f"- T(F) {fmt(d['T_F'], 4)} (original {fmt(d['original_T_F'], 4)}; bit-equal {fmt(d['T_F_equal'])}); "
          f"M identical {fmt(d['main_component_identical'])}; baseline graph `{d['baselines']['main']['graph_sha256'][:12]}…` "
          f"(was `{d['original_baselines']['main']['graph_sha256'][:12]}…`), λ₂ {d['baselines']['main']['lambda2']!r} "
          f"(was {d['original_baselines']['main']['lambda2']!r}).",
          f"- Versions: C passes on all {d['versions']} × 2 edge sets: {fmt(d['versions_C_all_pass'])}; T equal in "
          f"{d['versions_T_equal']} of {d['versions']}.",
          f"- Synthetic edits: same C outcome in {d['edits_same_C']} of {d['edits']}; same guard outcome in "
          f"{d['edits_same_guard']}; identical edit specification in {d['edits_same_spec']} (E1/E2 draw from all "
          f"pages, so their random picks change with the node set).",
          f"- Admission: C admissible {fmt(d['admission']['C_admissible'])}; fragmentation all rejected "
          f"{fmt(d['admission']['fragmentation_all_fail'])}; C0/C1 false rejections {len(d['admission']['C0_C1_false_rejections'])}; "
          f"C2 false rejections {len(d['admission']['C2_false_rejections'])}; T headroom {fmt(d['admission']['T_headroom'])}; "
          f"padding replicates not rejected {d['admission']['T_padding_not_rejected']}; decision **{d['admission']['decision']}**.",
          f"- Docker Luna panel traces scanned: {d['luna_docker_traces']}; with excluded IDs: "
          f"{len(d['luna_docker_traces_with_excluded_ids'])}.", ""]
    return "\n".join(L)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("manifest")
    r = sub.add_parser("rescope-09c")
    r.add_argument("--exclude", choices=("docs_scope_v1", "none"), default="docs_scope_v1")
    r.add_argument("--output", type=Path)
    r.add_argument("--exposure-only", action="store_true")
    sub.add_parser("check-09d")
    sub.add_parser("summarize")
    sub.add_parser("render")
    args = parser.parse_args()
    {"manifest": manifest, "rescope-09c": rescope_09c, "check-09d": check_09d, "summarize": summarize,
     "render": render}[args.command](args)


if __name__ == "__main__":
    main()

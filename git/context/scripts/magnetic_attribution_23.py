"""Sprint 23: matched-control structural attribution of normalized magnetic responses (zero model calls, offline).

Thin driver. The computation is RetrievalArena `magnetic_attribution.v1` (over the unchanged
`normalized_magnetic_spectrum.v1`), run by the pinned verification image's `measure-magnetic-attribution` command.
This driver keeps only the ICSC choices: the three anchors, the cycle budget, the small cases, the case mapping, the
frozen plan, the joins to Sprint 22 and the report tables. Sprint 22 sources and evidence are read, never written.

Stages (outputs under artifacts/magnetic-attribution-23-v1/):
  plan      build the baseline (historical LWCC induced once), global-reversal, cycle-reversal and small-case bundles,
            run the topology-only cycle selection with RA, write the frozen plan evidence (refuses to overwrite)
  request   write the operator input roots (small, anchors)
  measure   run the operator per root; a root is reused only if its receipt matches code, config, request and image
  analyze   baseline reproduction against Sprint 22, matched-control table, outcome-specific verdict; evidence JSON
  cards     resolve the fixed case-card pages to the frozen local sources (paths, titles, link lines)
  figures   small-graph diagram and the delta-lambda1 plot, rendered inside the pinned image
  check     Sprint 22 and earlier sources/evidence unchanged; plan, evidence and figure hashes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CONTEXT = ROOT / "git/context"
RA = ROOT / "git/RetrievalArena"
EVIDENCE = CONTEXT / "evidence"
OUT = ROOT / "artifacts/magnetic-attribution-23-v1"
INPUTS = OUT / "inputs"
DATE = "2026-10-25"
PLAN = EVIDENCE / f"magnetic-attribution-23-plan-v1-{DATE}.json"
EVIDENCE_OUT = EVIDENCE / f"magnetic-attribution-23-v1-{DATE}.json"
CARDS_OUT = OUT / "cards/case-cards.json"
RA_COMMIT = "99a7a04c9b042e84cb4217c9804e68cf06ae9ff9"
RECEIPT = OUT / "image/verification-image-99a7a04.json"
FOCUSED_LOG = OUT / "image/focused-99a7a04.log"
PROVISIONED = "sha256:07c2637a3a98c386dd0e02a3370fe75d97f3c45226635a681a80898e6a4c34d5"
S22 = ROOT / "artifacts/normalized-magnetic-22-v1"
S22_PLAN = EVIDENCE / "normalized-magnetic-22-plan-v1-2026-10-25.json"
S22_EVIDENCE = EVIDENCE / "normalized-magnetic-22-v1-2026-10-25.json"
ANCHORS = ("docker_docs", "react_docs", "github_docs")
Q = [0.0, 0.10, 0.25]
PRIMARY_Q, ORDINARY_Q, ALIAS_Q = 0.25, 0.0, 0.10
CYCLE_LENGTHS, MAX_CYCLES, BUDGET = [3, 4, 5, 6], 4, 100000
PERMUTATION = {"anchor": "react_docs", "seed": 2323}
BASELINE_TOLERANCE = {"eigenvalue_absolute": 1e-12, "trophic_absolute": 1e-9, "descriptors": "exact"}
CONCENTRATED_SUPPORT_FRACTION = 0.10
ROOTS = ("small", "anchors")
SMALL = {
    "path": (["a", "b", "c", "d"], [("a", "b"), ("b", "c"), ("c", "d")]),
    "path_reoriented": (["a", "b", "c", "d"], [("b", "a"), ("b", "c"), ("d", "c")]),
    "five": (["a", "b", "c", "d", "e"], [("a", "b"), ("b", "c"), ("c", "a"), ("a", "d"), ("d", "e"), ("e", "b")]),
    "five_triangle_reversed": (["a", "b", "c", "d", "e"],
                               [("b", "a"), ("c", "b"), ("a", "c"), ("a", "d"), ("d", "e"), ("e", "b")]),
    "five_plus_isolate": (["a", "b", "c", "d", "e", "f"],
                          [("a", "b"), ("b", "c"), ("c", "a"), ("a", "d"), ("d", "e"), ("e", "b")]),
}
PRESERVED = [
    "git/RetrievalArena/retrieval_arena/graph/magnetic_spectrum.py",
    "git/context/scripts/normalized_magnetic_22.py", "git/context/scripts/normalized_magnetic_22_figures.py",
    "git/context/scripts/test_normalized_magnetic_22.py",
    "git/context/evidence/normalized-magnetic-22-plan-v1-2026-10-25.json",
    "git/context/evidence/normalized-magnetic-22-v1-2026-10-25.json",
    "git/context/reports/2026-10-25-normalized-magnetic-validation.md",
    "artifacts/normalized-magnetic-22-v1/inputs/graph-input-manifest.json",
    "artifacts/normalized-magnetic-22-v1/runs/anchors/graph_spectra.v1.jsonl",
    "artifacts/normalized-magnetic-22-v1/runs/anchors/operator_receipt.v1.json",
    "artifacts/normalized-magnetic-22-v1/image/verification-image-6d277ac.json",
] + [f"artifacts/normalized-magnetic-22-v1/inputs/anchors/bundles/anchor__{c}.json" for c in ANCHORS]


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canon(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def write_json(path: Path, value, *, new=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if new and path.exists():
        raise SystemExit(f"refusing-to-overwrite:{path}")
    path.write_bytes(json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False).encode() + b"\n")
    return sha(path)


def read_json(path: Path):
    return json.loads(Path(path).read_bytes())


def ra():
    if str(RA) not in sys.path:
        sys.path.insert(0, str(RA))
    from retrieval_arena.graph import magnetic_attribution, magnetic_spectrum
    return magnetic_attribution, magnetic_spectrum


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


# ------------------------------------------------------------------ plan (bundles and topology-only selection)

def make_bundle(graph_id, nodes, edges, provenance):
    _, m = ra()
    admitted = m.admit_graph(nodes, edges)
    if admitted["duplicate_edges_removed"] or admitted["self_loops_removed"]:
        raise SystemExit(f"non-canonical-input:{graph_id}")
    b = {"schema_version": m.BUNDLE_SCHEMA, "graph_id": graph_id, "nodes": admitted["nodes"],
         "edges": [list(e) for e in admitted["edges"]],
         "canonical_graph_sha256": m.graph_sha256(admitted["nodes"], admitted["edges"]), "provenance": provenance}
    m.validate_bundle(b)
    return b


def save_bundle(root, b):
    name = b["graph_id"].replace("/", "__") + ".json"
    path = INPUTS / root / "bundles" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canon(b) + b"\n")
    return {"graph_id": b["graph_id"], "bundle": f"bundles/{name}", "bundle_sha256": sha(path),
            "canonical_graph_sha256": b["canonical_graph_sha256"], "nodes": len(b["nodes"]), "edges": len(b["edges"])}


def s22_anchor_rows():
    rows = {}
    for line in (S22 / "runs/anchors/graph_spectra.v1.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        rows[r["job_id"]] = r
    return rows


def plan(_args):
    if PLAN.exists():
        raise SystemExit(f"refusing-to-overwrite:{PLAN}")
    ma, m = ra()
    if git(RA, "rev-parse", "HEAD") != RA_COMMIT or git(RA, "status", "--porcelain"):
        raise SystemExit("ra-identity: HEAD must be the pinned commit with a clean tree")
    receipt = read_json(RECEIPT)
    if receipt["source_commit"] != RA_COMMIT:
        raise SystemExit("image-receipt-commit")
    s22 = s22_anchor_rows()
    anchors, manifest = [], {"small": [], "anchors": []}
    for cid in ANCHORS:
        src = S22 / f"inputs/anchors/bundles/anchor__{cid}.json"
        b22 = read_json(src)
        keep = set(b22["node_sets"]["historical_lwcc"])
        nodes = sorted(keep)
        edges = sorted((s, t) for s, t in map(tuple, b22["edges"]) if s in keep and t in keep)
        base_hash = m.graph_sha256(nodes, edges)
        s22_view = s22[f"anchor/{cid}#historical_lwcc"]["selection"]["view_graph_sha256"]
        if base_hash != s22_view:
            raise SystemExit(f"baseline-does-not-reproduce-s22-view:{cid}")
        prov = {"corpus_id": cid, "commit": b22["provenance"]["commit"], "s22_bundle": str(src.relative_to(ROOT)),
                "s22_bundle_sha256": sha(src), "view": "historical_lwcc induced once (Sprint 22 node set)",
                "s22_view_graph_sha256": s22_view}
        base = make_bundle(f"anchor23/{cid}/baseline", nodes, edges, dict(prov, role="baseline"))
        entries = [save_bundle("anchors", base)]
        glob = make_bundle(f"anchor23/{cid}/global_reversal", nodes, sorted((t, s) for s, t in edges),
                           dict(prov, role="global_reversal", change="every arc reversed"))
        entries.append(save_bundle("anchors", glob))
        selection = ma.nonreciprocal_cycles(nodes, edges, CYCLE_LENGTHS, MAX_CYCLES, BUDGET)
        variants = []
        for k, (cycle, arcs) in enumerate(zip(selection["cycles"], selection["arcs"]), start=1):
            ma.validate_cycle(edges, cycle)
            flipped = ma.reverse_arcs(edges, [tuple(a) for a in arcs])
            vb = make_bundle(f"anchor23/{cid}/cycle{k}", nodes, flipped,
                             dict(prov, role="cycle_reversal", cycle_index=k - 1, cycle=cycle, reversed_arcs=arcs,
                                  change="arcs of one selected cycle reversed from the baseline; never chained"))
            entry = save_bundle("anchors", vb)
            entries.append(entry)
            variants.append({"index": k - 1, "graph_id": vb["graph_id"], "cycle": cycle, "length": len(cycle),
                             "reversed_arcs": arcs, "canonical_graph_sha256": vb["canonical_graph_sha256"]})
        hashes = [e["canonical_graph_sha256"] for e in entries]
        if len(set(hashes)) != len(hashes):
            raise SystemExit(f"variant-hash-collision:{cid}")
        manifest["anchors"].extend(entries)
        anchors.append({"corpus_id": cid, "nodes": len(nodes), "arcs": len(edges),
                        "baseline_canonical_graph_sha256": base_hash, "s22_bundle_sha256": prov["s22_bundle_sha256"],
                        "selection": selection, "variants": variants,
                        "case_card_cycle": variants[0]["graph_id"] if variants else "no eligible cycle found"})
        print(cid, len(nodes), len(edges), selection["status"], selection["expansions"],
              [len(c) for c in selection["cycles"]], flush=True)
    for gid, (nodes, edges) in SMALL.items():
        manifest["small"].append(save_bundle("small", make_bundle(f"small/{gid}", nodes, edges,
                                                                  {"role": "specified small case"})))
    write_json(INPUTS / "graph-input-manifest.json", manifest)
    value = {
        "schema_version": "icsc.magnetic_attribution_23_plan.v1", "created": DATE,
        "status": "frozen before any counterfactual spectrum; RA unit fixtures only had run",
        "study_type": ("post hoc explanatory study chosen after Sprint 22 was known; not independent confirmation; "
                       "the three anchors are illustrative, not a random or representative sample"),
        "question": ("Can normalized magnetic lambda1 distinguish particular directed documentation graphs when the "
                     "undirected graph and the selected directed summaries are exactly preserved, and can the response "
                     "be explained through edge energy and mode localization?"),
        "model_calls": 0, "retrieval_or_answer_runs": 0,
        "ra": {"branch": git(RA, "branch", "--show-current"), "commit": RA_COMMIT, "tree": receipt["source_tree"],
               "attribution_module": "retrieval_arena/graph/magnetic_attribution.py",
               "attribution_module_sha256": ma.code_sha256(),
               "spectrum_module": "retrieval_arena/graph/magnetic_spectrum.py", "spectrum_module_sha256": m.code_sha256(),
               "image": receipt["image"], "image_receipt": str(RECEIPT.relative_to(ROOT)),
               "image_receipt_sha256": sha(RECEIPT), "dependency_lock_sha256": receipt["dependency_lock_sha256"],
               "provisioned_base": PROVISIONED, "platform": receipt["platform"],
               "image_build": "build-verification.ps1 -ProvisionedImage (icsc-c2 base, lock unchanged), --network none",
               "focused_log": str(FOCUSED_LOG.relative_to(ROOT)), "focused_log_sha256": sha(FOCUSED_LOG),
               "operator_command": "measure-magnetic-attribution"},
        "inputs": {"input_manifest_sha256": sha(INPUTS / "graph-input-manifest.json"),
                   "s22_plan_sha256": sha(S22_PLAN), "s22_evidence_sha256": sha(S22_EVIDENCE),
                   "s22_anchor_table_sha256": sha(S22 / "runs/anchors/graph_spectra.v1.jsonl")},
        "anchors": anchors,
        "cycle_rule": {"rule": ma.SELECTION_RULE, "lengths": CYCLE_LENGTHS, "max_cycles": MAX_CYCLES,
                       "budget_per_anchor": BUDGET, "arc_eligibility": "nonreciprocal arcs only",
                       "variant_rule": "one variant per selected cycle, all its arcs reversed from the original "
                                       "baseline; never chained; distinct graph hashes required",
                       "coverage_rule": "fewer than four cycles is reported coverage; no widening, corpus "
                                        "replacement, reciprocal edges or outcome-chosen alternatives",
                       "selection_inputs": "node ids and arcs only; no eigenvalue, energy or exposure"},
        "small_cases": {
            "path": {"a": "small/path", "b": "small/path_reoriented", "control": "forest_reorientation",
                     "purpose": "fixed-weight forest blindness; degree matching is not claimed"},
            "five": {"a": "small/five", "b": "small/five_triangle_reversed", "control": "matched_cycle_reversal",
                     "reversed_arcs": [["a", "b"], ["b", "c"], ["c", "a"]],
                     "purpose": "the triangle and the undirected four-cycle a-d-e-b share edge a-b; fixed candidate "
                                "for explaining cycle-phase changes; no lowest-eigenvalue response promised"},
            "isolate": {"bundle": "small/five_plus_isolate", "views": ["full", "lwcc"],
                        "purpose": "separate degeneracy control; changes the node set, so it is not in the matched "
                                   "study"}},
        "q": {"primary": PRIMARY_Q, "matched_ordinary_baseline": ORDINARY_Q, "aliasing_check": ALIAS_Q,
              "rule": "all three reported; no best-q selection; no grid expansion after a null"},
        "spectrum_config": {"solver": "dense"}, "attribution_config": {},
        "attribution_config_note": "RA AttributionConfig defaults at the pinned commit: cluster 1e-8, resolution "
                                   "max(1e-8, 10 (r_A + r_B)), identity 1e-10, trophic 1e-9, permutation eigenvalues "
                                   "1e-10 and fields 1e-8",
        "primary_scalar": "lowest normalized eigenvalue lambda1; lambda2 descriptive",
        "resolution_rule": ma.RESOLUTION_POLICY, "cluster_rule": ma.CLUSTER_POLICY, "energy_rule": ma.ENERGY_POLICY,
        "baseline_reproduction": dict(BASELINE_TOLERANCE, against="Sprint 22 anchor receipts (historical_lwcc view): "
                                      "view hash, lowest three eigenvalues at q 0, 0.10, 0.25, structural descriptors, "
                                      "trophic incoherence"),
        "permutation_check": dict(PERMUTATION, scope="baseline side at every q (on the global-reversal pair job)"),
        "control_rule": "per-pair exact assertions (RA control_checks); a failed assertion quarantines the variant; "
                        "global reversal and forest pairs are negative controls, not degree-matched positive cases",
        "localization_caveat_rule": f"a resolved pair carries the caveat when either ground mode is clustered, a "
                                    f"boundary is unqualified, the permutation check fails, or either side's "
                                    f"effective-support fraction is below {CONCENTRATED_SUPPORT_FRACTION}",
        "case_cards": {"selection": "first selected cycle per anchor, or an explicit no-eligible-cycle card",
                       "pages": "at most two: the lexicographically first page of that cycle, and the lexicographically "
                                "first endpoint (other than that page) of the highest-energy edge outside the cycle in "
                                "the baseline ground cluster at q = 0.25 (ties by ids); this second choice is "
                                "output-informed",
                       "source": "frozen local snapshot at the bundle's commit via the pinned importer; page path, "
                                 "title and the markdown link lines of the arcs involved",
                       "limits": "no procedural dependency, navigation benefit or LT role is inferred from link "
                                 "direction or path names"},
        "outcome_rule": {
            "resolved_matched_response": "at least one control-passing, qualified matched pair resolved at q = 0.25: "
                                         "these named graphs demonstrate a distinction not determined by the exactly "
                                         "preserved control set; no predictive generalization across corpora",
            "no_resolved_matched_response": "no further evidence for distinctness under these controls; report "
                                            "aliasing, eligible-cycle coverage and resolution",
            "resolved_with_localization_caveat": "report the scalar response with the caveat; not global "
                                                 "organizational coherence",
            "failed_controls_or_qualification": "no conclusion from the affected pair; partial results kept"},
        "outputs": {"evidence": str(EVIDENCE_OUT.relative_to(ROOT)), "artifacts": str(OUT.relative_to(ROOT)),
                    "report": f"git/context/reports/{DATE}-magnetic-structural-attribution.md"},
        "preserved_sha256": {p: sha(ROOT / p) for p in PRESERVED},
        "s22_historical_inputs_sha256": read_json(S22_PLAN)["historical_inputs_sha256"],
    }
    print("plan", write_json(PLAN, value, new=True))


# ------------------------------------------------------------------ operator requests and runs

def jobs_for(root):
    p = read_json(PLAN)
    if root == "small":
        b = lambda gid: f"bundles/small__{gid}.json"
        return [
            {"job_id": "small/path#forest", "kind": "pair", "bundle_a": b("path"), "bundle_b": b("path_reoriented"),
             "view": {"kind": "full"}, "q": Q, "control": "forest_reorientation",
             "changed_arcs": [["a", "b"], ["c", "d"]]},
            {"job_id": "small/five#triangle", "kind": "pair", "bundle_a": b("five"),
             "bundle_b": b("five_triangle_reversed"), "view": {"kind": "full"}, "q": Q,
             "control": "matched_cycle_reversal", "changed_arcs": p["small_cases"]["five"]["reversed_arcs"]},
            {"job_id": "small/five_plus_isolate#degeneracy", "kind": "single", "bundle": b("five_plus_isolate"),
             "views": [{"kind": "full"}, {"kind": "lwcc"}], "q": Q},
        ]
    jobs = []
    for a in p["anchors"]:
        cid = a["corpus_id"]
        bb = lambda role: f"bundles/anchor23__{cid}__{role}.json"
        jobs.append({"job_id": f"{cid}#selection", "kind": "cycle_selection", "bundle": bb("baseline"),
                     "view": {"kind": "full"}, "lengths": CYCLE_LENGTHS, "max_cycles": MAX_CYCLES, "budget": BUDGET})
        g = {"job_id": f"{cid}#global_reversal", "kind": "pair", "bundle_a": bb("baseline"),
             "bundle_b": bb("global_reversal"), "view": {"kind": "full"}, "q": Q, "control": "global_reversal",
             "changed_arcs": []}
        if cid == PERMUTATION["anchor"]:
            g["permutation_seed"] = PERMUTATION["seed"]
        jobs.append(g)
        for v in a["variants"]:
            jobs.append({"job_id": f"{cid}#cycle{v['index'] + 1}", "kind": "pair", "bundle_a": bb("baseline"),
                         "bundle_b": bb(f"cycle{v['index'] + 1}"), "view": {"kind": "full"}, "q": Q,
                         "control": "matched_cycle_reversal", "changed_arcs": v["reversed_arcs"],
                         "selection_ref": {"job_id": f"{cid}#selection", "index": v["index"]}})
    return jobs


def write_request(root: Path, config):
    (root / "config.json").write_bytes(canon(config) + b"\n")
    rows = []
    for path in sorted(root.rglob("*"), key=lambda x: x.relative_to(root).parts):
        if path.is_file() and path.name != "operator-request.json":
            rows.append({"path": path.relative_to(root).as_posix(), "sha256": sha(path), "size_bytes": path.stat().st_size})
    expected = {"config.json"} | {j[k] for j in config["jobs"] for k in ("bundle", "bundle_a", "bundle_b") if k in j}
    if {r["path"] for r in rows} != expected:
        raise SystemExit(f"input-root-contents:{root}")
    request_value = {"schema_version": "retrieval_arena.magnetic_attribution_operator_request.v1",
                     "command": "measure-magnetic-attribution", "config_path": "config.json", "input_inventory": rows,
                     "input_inventory_sha256": hashlib.sha256(canon(rows)).hexdigest()}
    (root / "operator-request.json").write_bytes(canon(request_value) + b"\n")
    return sha(root / "config.json")


def request(args):
    p = read_json(PLAN)
    out = {}
    for root in args.roots or list(ROOTS):
        config = {"schema_version": "retrieval_arena.magnetic_attribution_config.v1",
                  "spectrum_config": p["spectrum_config"], "attribution_config": p["attribution_config"],
                  "jobs": jobs_for(root)}
        out[root] = {"jobs": len(config["jobs"]), "config_sha256": write_request(INPUTS / root, config)}
    print(json.dumps(out, indent=1))


def run_operator(input_root: Path, output_root: Path):
    p = read_json(PLAN)
    receipt_path = output_root / "operator_receipt.v1.json"
    if receipt_path.exists():
        rec = read_json(receipt_path)
        valid = (rec["status"] == "completed" and rec["identity"]["source_commit"] == p["ra"]["commit"]
                 and rec["identity"]["image_reference"] == p["ra"]["image"]["reference"]
                 and rec["metric_code_sha256"] == p["ra"]["attribution_module_sha256"]
                 and rec["spectrum_code_sha256"] == p["ra"]["spectrum_module_sha256"]
                 and rec["config_sha256"] == sha(input_root / "config.json")
                 and rec["request_sha256"] == sha(input_root / "operator-request.json"))
        if valid:
            print(f"reuse {output_root.name}: receipt matches code, config, request and image", flush=True)
            return rec
        n = len(list(output_root.parent.glob(output_root.name + ".stale-*")))
        output_root.rename(output_root.with_name(output_root.name + f".stale-{n}"))
    output_root.mkdir(parents=True, exist_ok=False)
    (output_root / ".retrieval-arena-output-owner").write_bytes(b"retrieval_arena.output_root.v1\n")
    cmd = ["pwsh", "-NoProfile", "-File", str(RA / "scripts/run-verification.ps1"), "-Command",
           "measure-magnetic-attribution", "-InputRoot", str(input_root), "-OutputRoot", str(output_root),
           "-Receipt", str(RECEIPT)]
    log = output_root.with_name(output_root.name + ".log")
    with log.open("wb") as handle:
        code = subprocess.run(cmd, stdout=handle, stderr=subprocess.STDOUT).returncode
    rec = read_json(receipt_path) if receipt_path.exists() else {"status": "no-receipt"}
    print(json.dumps({"run": output_root.name, "exit": code, "status": rec["status"], "error": rec.get("error_code"),
                      "jobs": rec.get("job_count"), "artifacts": rec.get("artifact_count")}), flush=True)
    return rec


def measure(args):
    for root in args.roots or list(ROOTS):
        run_operator(INPUTS / root, OUT / "runs" / root)


# ------------------------------------------------------------------ analysis (pure helpers; context tests cover them)

def load_run(root):
    p = read_json(PLAN)
    run = OUT / "runs" / root
    rec = read_json(run / "operator_receipt.v1.json")
    if rec["status"] != "completed" or rec["identity"]["source_commit"] != p["ra"]["commit"] \
            or rec["metric_code_sha256"] != p["ra"]["attribution_module_sha256"] \
            or rec["identity"]["image_reference"] != p["ra"]["image"]["reference"]:
        raise SystemExit(f"run-identity:{root}")
    table = (run / "magnetic_attribution.v1.jsonl").read_bytes()
    if hashlib.sha256(table).hexdigest() != rec["outputs"]["magnetic_attribution.v1.jsonl"]:
        raise SystemExit(f"run-table-hash:{root}")
    manifest = read_json(run / "magnetic_attribution_manifest.json")
    for art in manifest["artifacts"]:
        if sha(run / art["path"]) != art["sha256"]:
            raise SystemExit(f"artifact-hash:{art['path']}")
    rows = {}
    for line in table.decode().splitlines():
        r = json.loads(line)
        rows[r["job_id"]] = r
    return rows, rec, manifest


def qkey(q):
    return f"{q:.2f}"


def baseline_reproduction(cid, row_a_side, s22_row, controls_a):
    """Compare the S23 baseline side (per q) and descriptors with the Sprint 22 receipt of the same view."""
    out = {"view_hash_equal": None, "eigenvalues": {}, "descriptors_equal": None, "trophic_abs_diff": None}
    worst = 0.0
    for q in Q:
        mine = row_a_side[qkey(q)]["lowest_eigenvalues"][:3]
        theirs = s22_row["normalized"][qkey(q)]["eigenvalues"][:3]
        diff = max(abs(x - y) for x, y in zip(mine, theirs))
        worst = max(worst, diff)
        out["eigenvalues"][qkey(q)] = {"max_abs_diff": diff, "s22": theirs, "s23": mine}
    out["eigenvalue_max_abs_diff"] = worst
    out["descriptors_equal"] = controls_a["descriptors_a"] == s22_row["structure"]
    out["trophic_abs_diff"] = abs(controls_a["trophic_incoherence"]["a"] - s22_row["trophic_incoherence"]["value"])
    out["passed"] = (worst <= BASELINE_TOLERANCE["eigenvalue_absolute"] and out["descriptors_equal"]
                     and out["trophic_abs_diff"] <= BASELINE_TOLERANCE["trophic_absolute"])
    return out


def side_row(side):
    f = side.get("fields") or {}
    cc = f.get("changed_cycle") or {}
    return {"lambda1": side["lambda1"], "lambda2": side["lambda2"], "cluster_rank": side["cluster"]["rank"],
            "next_gap": side["cluster"].get("next_gap"), "boundary_qualified": side["cluster"]["boundary_qualified"],
            "attribution_status": side["attribution_status"], "numerics": side["numerics"]["status"],
            "residual_abs": side["ground_residual_abs"], "effective_support_fraction": f.get("effective_support_fraction"),
            "degree_mass_tv_distance": f.get("degree_mass_tv_distance"),
            "energy_fractions_available": f.get("energy_fractions_available"),
            "energy_sum_identity_ok": f.get("energy_sum_identity_ok"),
            "mass_on_cycle_nodes": cc.get("mass_on_cycle_nodes"), "cycle_node_share": cc.get("node_share"),
            "cycle_degree_volume_share": cc.get("degree_volume_share"),
            "energy_fraction_on_changed_edges": cc.get("energy_fraction_on_changed_edges"),
            "changed_edges_w_share": cc.get("w_share_of_changed_edges"),
            "highest_energy_edge_outside_changed": cc.get("highest_energy_edge_outside_changed")}


def pair_rows(case, row):
    """One compact table row per q for a pair record."""
    out = []
    for q in Q:
        blk = row["q"][qkey(q)]
        fc = blk["finite_change"]
        out.append({"case": case, "job_id": row["job_id"], "control": row["control"], "q": q,
                    "controls_passed": row["controls"]["passed"], "degree_matched": row["controls"]["degree_matched"],
                    "descriptors_that_change": row["controls"]["descriptors_that_change"],
                    "delta_lambda1": blk["delta_lambda1"]["raw"], "tolerance": blk["delta_lambda1"]["tolerance"],
                    "resolved": blk["delta_lambda1"]["resolved"], "relative_to_baseline":
                        blk["delta_lambda1"]["relative_to_baseline"], "delta_lambda2": blk["delta_lambda2"],
                    "a": side_row(blk["a"]), "b": side_row(blk["b"]),
                    "finite_change": {k: fc.get(k) for k in ("status", "reasons", "fixed_mode_term", "relaxation_term",
                                                             "account_residual", "account_ok",
                                                             "relaxation_nonpositive")},
                    "permutation_check": blk.get("permutation_check")})
    return out


def caveat(r):
    reasons = []
    for s in ("a", "b"):
        if r[s]["cluster_rank"] != 1:
            reasons.append(f"{s}_clustered")
        if not r[s]["boundary_qualified"]:
            reasons.append(f"{s}_boundary_unqualified")
        fr = r[s]["effective_support_fraction"]
        if fr is not None and fr < CONCENTRATED_SUPPORT_FRACTION:
            reasons.append(f"{s}_concentrated")
    if r.get("permutation_check") and not r["permutation_check"].get("passed"):
        reasons.append("permutation_failed")
    return reasons


def verdict(matched_rows):
    """Outcome-specific verdict from the matched cycle-reversal rows at the primary q (plan outcome_rule)."""
    primary = [r for r in matched_rows if r["q"] == PRIMARY_Q]
    usable = [r for r in primary if r["controls_passed"] and r["a"]["numerics"] == "qualified"
              and r["b"]["numerics"] == "qualified"]
    failed = [r["job_id"] for r in primary if r not in usable]
    resolved = [r for r in usable if r["resolved"]]
    caveated = {r["job_id"]: caveat(r) for r in resolved if caveat(r)}
    if resolved and len(caveated) == len(resolved):
        outcome = "resolved_with_localization_caveat"
    elif resolved:
        outcome = "resolved_matched_response"
    else:
        outcome = "no_resolved_matched_response"
    return {"outcome": outcome, "matched_pairs": len(primary), "usable": len(usable), "resolved": len(resolved),
            "resolved_jobs": [r["job_id"] for r in resolved], "caveats": caveated,
            "failed_controls_or_qualification": failed,
            "q0_all_unresolved": all(r["resolved"] is False for r in matched_rows if r["q"] == ORDINARY_Q),
            "resolved_at_alias_q": [r["job_id"] for r in matched_rows if r["q"] == ALIAS_Q and r["resolved"]]}


def analyze(_args):
    p = read_json(PLAN)
    small, small_rec, small_man = load_run("small")
    anch, anch_rec, anch_man = load_run("anchors")
    s22 = s22_anchor_rows()
    table, reproduction, coverage, selection_replay, controls = [], {}, {}, {}, {}
    for a in p["anchors"]:
        cid = a["corpus_id"]
        sel = anch[f"{cid}#selection"]["result"]
        selection_replay[cid] = sel == a["selection"]
        coverage[cid] = {"status": sel["status"], "expansions": sel["expansions"], "found_per_length": sel["found_per_length"],
                         "cycles": len(sel["cycles"]), "lengths": [len(c) for c in sel["cycles"]],
                         "nonreciprocal_arcs": sel["nonreciprocal_arcs"]}
        g = anch[f"{cid}#global_reversal"]
        reproduction[cid] = baseline_reproduction(cid, {k: v["a"] for k, v in g["q"].items()},
                                                  s22[f"anchor/{cid}#historical_lwcc"], g["controls"])
        table += pair_rows(f"{cid}/global_reversal", g)
        controls[f"{cid}/global_reversal"] = g["controls"]["checks"]
        for v in a["variants"]:
            r = anch[f"{cid}#cycle{v['index'] + 1}"]
            table += pair_rows(f"{cid}/cycle{v['index'] + 1}", r)
            controls[f"{cid}/cycle{v['index'] + 1}"] = dict(r["controls"]["checks"], **r["controls"]["degree_checks"])
    five = small["small/five#triangle"]
    path = small["small/path#forest"]
    table += pair_rows("small/five_triangle", five) + pair_rows("small/path_forest", path)
    controls["small/five_triangle"] = dict(five["controls"]["checks"], **five["controls"]["degree_checks"])
    controls["small/path_forest"] = path["controls"]["checks"]
    iso = small["small/five_plus_isolate#degeneracy"]
    degeneracy = {v["view"]["kind"]: {qkey(q): {"lambda1": v["q"][qkey(q)]["lambda1"],
                                                "lowest": v["q"][qkey(q)]["lowest_eigenvalues"],
                                                "cluster_rank": v["q"][qkey(q)]["cluster"]["rank"],
                                                "isolate_mass": (v["q"][qkey(q)].get("fields") or {}).get("isolate_mass")}
                                       for q in Q} for v in iso["views"]}
    matched = [r for r in table if r["control"] == "matched_cycle_reversal"]
    numerics = [s for r in table for s in (r["a"], r["b"])]
    perm = [r["permutation_check"] for r in table if r.get("permutation_check")]
    value = {
        "schema_version": "icsc.magnetic_attribution_23_evidence.v1", "created": DATE, "plan_sha256": sha(PLAN),
        "model_calls": 0, "ra_commit": p["ra"]["commit"], "image": p["ra"]["image"],
        "runs": {name: {"receipt_sha256": rec["receipt_sha256"], "table_sha256": rec["outputs"]["magnetic_attribution.v1.jsonl"],
                        "manifest_sha256": rec["outputs"]["magnetic_attribution_manifest.json"],
                        "artifact_inventory_sha256": rec["artifact_inventory_sha256"], "artifacts": rec["artifact_count"],
                        "jobs": rec["job_count"], "measure_seconds": man["measure_seconds"], "runtime": rec["runtime"]}
                 for name, rec, man in (("small", small_rec, small_man), ("anchors", anch_rec, anch_man))},
        "selection_replay_matches_plan": selection_replay, "coverage": coverage,
        "baseline_reproduction": reproduction, "controls": controls,
        "numerical": {"sides": len(numerics), "qualified": sum(s["numerics"] == "qualified" for s in numerics),
                      "worst_residual_abs": max(s["residual_abs"] or 0.0 for s in numerics),
                      "energy_identity_all_ok": all(s["energy_sum_identity_ok"] is not False for s in numerics),
                      "account_all_ok": all(r["finite_change"]["account_ok"] in (True, None) for r in table),
                      "relaxation_all_nonpositive": all(r["finite_change"]["relaxation_nonpositive"] in (True, None)
                                                        for r in table),
                      "permutation_checks": perm},
        "table": table, "degeneracy": degeneracy, "verdict": verdict(matched),
    }
    print("evidence", write_json(EVIDENCE_OUT, value))
    print(json.dumps({"verdict": value["verdict"], "coverage": coverage, "reproduction": {k: v["passed"] for k, v in
                      reproduction.items()}, "selection_replay": selection_replay}, indent=1))


# ------------------------------------------------------------------ case cards (source mapping)

LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
FRONT_TITLE = re.compile(r"^title:\s*(.+)$", re.M)


def source_pages(cid):
    sys.path.insert(0, str(HERE))
    from diagnose_phase_flow import historical
    gm = historical()
    roster = read_json(EVIDENCE / "cross-corpus-roster-recovered-v4-2026-09-28.json")
    row = {r["corpus_id"]: r for r in roster["rows"]}[cid]
    rec = read_json(ROOT / row["export_receipt_path"])
    snap = next(s for s in rec["snapshots"] if s["commit"] == row["cikm_anchor_commit"])
    source = ROOT / snap["snapshot_path"] / "source" / snap["relative_scope"]
    pages = [p for p in gm.discover_markdown_pages(source, faq_patterns=row["faq_exclusion_patterns"]) if not p.is_faq]
    return gm, {p.doc_id: p for p in pages}, snap


def link_lines(gm, pages, src, dst):
    """Markdown link lines in page src whose target resolves (pinned importer rule) to page dst."""
    page, target = pages[src], pages[dst]
    out = []
    for n, line in enumerate(page.text.splitlines(), start=1):
        for text, raw in LINK.findall(line):
            if gm.extract_markdown_edges([type(page)(**dict(vars(page), text=f"[{text}]({raw})", link_targets=())),
                                          target]) == [(src, dst)]:
                out.append({"line": n, "link_text": text, "target": raw})
    return out


def cards(_args):
    p = read_json(PLAN)
    anch, _, _ = load_run("anchors")
    out = []
    for a in p["anchors"]:
        cid = a["corpus_id"]
        if not a["variants"]:
            out.append({"corpus_id": cid, "card": "no eligible cycle found within the fixed budget",
                        "coverage": a["selection"]["status"]})
            continue
        v = a["variants"][0]
        row = anch[f"{cid}#cycle1"]
        base = row["q"][qkey(PRIMARY_Q)]["a"]
        hot = ((base.get("fields") or {}).get("changed_cycle") or {}).get("highest_energy_edge_outside_changed")
        try:
            gm, pages, snap = source_pages(cid)
        except Exception as error:  # noqa: BLE001 - recorded, card falls back to topology only
            out.append({"corpus_id": cid, "cycle": v["cycle"], "source": f"unavailable: {error}"})
            continue
        first = min(v["cycle"])
        second = None
        if hot:
            second = min(x for x in hot["edge"] if x != first) if any(x != first for x in hot["edge"]) else None
        def describe(doc_id):
            if doc_id not in pages:
                return {"doc_id": doc_id, "source": "not found in the frozen snapshot"}
            pg = pages[doc_id]
            front = FRONT_TITLE.search(pg.text.split("\n---", 1)[0]) if pg.text.startswith("---") else None
            if front:
                return {"doc_id": doc_id, "rel_path": pg.rel_path, "title": front.group(1).strip().strip("'\""),
                        "title_source": "front matter title"}
            return {"doc_id": doc_id, "rel_path": pg.rel_path, "title": gm.title_from_markdown(pg.text, pg.rel_path),
                    "title_source": "pinned importer title_from_markdown (no front matter title)"}
        inspected = {first} | ({second} if second else set())
        arcs = []
        for s, t in v["reversed_arcs"]:
            arcs.append({"arc": [s, t], "source_page": describe(s).get("rel_path"), "target_page": describe(t).get("rel_path"),
                         "link_lines_in_source": link_lines(gm, pages, s, t) if s in inspected and s in pages and t in pages
                         else "not inspected (page budget)"})
        hot_links = None
        if hot and second in pages:
            other = hot["edge"][0] if hot["edge"][1] == second else hot["edge"][1]
            hot_links = {"page": second, "other_endpoint": other,
                         "lines_to_other": link_lines(gm, pages, second, other) if other in pages else None,
                         "note": "only the inspected page's own outgoing link lines are read"}
        out.append({"corpus_id": cid, "commit": snap["commit"], "cycle": v["cycle"], "length": v["length"],
                    "cycle_pages": [describe(x) for x in v["cycle"]], "inspected_pages": [describe(first)] +
                    ([describe(second)] if second else []), "arcs": arcs,
                    "highest_energy_edge_outside_cycle": hot, "hot_edge_links": hot_links,
                    "selection_note": "first page is topology-selected; the second is output-informed (highest-energy "
                                      "edge outside the cycle, baseline q = 0.25)"})
    print("cards", write_json(CARDS_OUT, out))


# ------------------------------------------------------------------ figures and check

def figures(_args):
    receipt = read_json(RECEIPT)
    out = OUT / "figures"
    out.mkdir(parents=True, exist_ok=True)
    small_run = OUT / "runs/small"
    cmd = ["docker", "run", "--rm", "--network", "none", "--read-only", "--entrypoint", "python",
           "-e", "MPLCONFIGDIR=/tmp", "--tmpfs", "/tmp", "--user", "65532:65532",
           "-v", f"{CONTEXT}:/ctx:ro", "-v", f"{small_run}:/small:ro", "-v", f"{out}:/out", receipt["image"]["reference"],
           "-I", "/ctx/scripts/magnetic_attribution_23_figures.py", f"/ctx/evidence/{EVIDENCE_OUT.name}", "/small", "/out"]
    subprocess.run(cmd, check=True)
    write_json(out / "figures-receipt.json", {"image": receipt["image"],
                                              "script_sha256": sha(HERE / "magnetic_attribution_23_figures.py"),
                                              "evidence_sha256": sha(EVIDENCE_OUT)})


def check(_args):
    p = read_json(PLAN)
    changed = {k: v for k, v in p["preserved_sha256"].items() if sha(ROOT / k) != v}
    changed.update({k: v for k, v in p["s22_historical_inputs_sha256"].items() if sha(ROOT / k) != v})
    out = {"preserved_unchanged": not changed, "changed": changed, "plan_sha256": sha(PLAN),
           "evidence_sha256": sha(EVIDENCE_OUT) if EVIDENCE_OUT.exists() else None,
           "cards_sha256": sha(CARDS_OUT) if CARDS_OUT.exists() else None,
           "figures": {f.name: sha(f) for f in sorted((OUT / "figures").glob("*")) if f.is_file()}}
    print(json.dumps(out, indent=1))
    if changed:
        raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="stage", required=True)
    sub.add_parser("plan").set_defaults(fn=plan)
    for name, fn in (("request", request), ("measure", measure)):
        s = sub.add_parser(name)
        s.add_argument("--roots", nargs="*", choices=ROOTS)
        s.set_defaults(fn=fn)
    for name, fn in (("analyze", analyze), ("cards", cards), ("figures", figures), ("check", check)):
        sub.add_parser(name).set_defaults(fn=fn)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

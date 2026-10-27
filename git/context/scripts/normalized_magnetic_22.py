"""Sprint 22: normalized magnetic measurement validation (zero model calls, offline).

Thin driver. The measurement itself is RetrievalArena `normalized_magnetic_spectrum.v1`, run by the pinned
verification image's `measure-graph-spectra` command over portable graph bundles. This driver keeps only the ICSC
choices: which historical graphs and views, the frozen plan, the panel joins and the exploratory analyses.
Historical drivers (09c, 12, 15, 17, 18) are imported read-only to reconstruct inputs and replay old statistics;
none of them is modified.

Stages (each resumable; outputs under artifacts/normalized-magnetic-22-v1/):
  plan      write the frozen plan evidence (refuses to overwrite)
  bundle    reconstruct graph bundles per panel, verify them against historical receipts, write the input manifest
            and the coverage/mismatch ledger; quarantined graphs are recorded, never replaced
  request   write each panel's operator input root (config.json, operator-request.json with its inventory)
  measure   run the operator per panel; a panel is reused only if its receipts match the graph, view, code, config
            and image identity
  analyze   analyses A (characterization), B (orientation replay) and C (refinement application); evidence JSON
  figures   tables' data plus a plotting script run inside the pinned image (matplotlib from its lock)
  check     historical source/evidence hashes unchanged; evidence and plan hashes
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CONTEXT = ROOT / "git/context"
RA = ROOT / "git/RetrievalArena"
EVIDENCE = CONTEXT / "evidence"
OUT = ROOT / "artifacts/normalized-magnetic-22-v1"
INPUTS = OUT / "inputs"
PLAN_DATE = "2026-10-25"
PLAN = EVIDENCE / f"normalized-magnetic-22-plan-v1-{PLAN_DATE}.json"
MANIFEST = INPUTS / "graph-input-manifest.json"
LEDGER = INPUTS / "coverage-ledger.json"
PANELS = ("anchors", "orientation", "wc", "area")
RECEIPT = OUT / "image/verification-image-6d277ac.json"
RA_COMMIT = "6d277ac"
GRID = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25]
ENDPOINTS = [0.0, 0.25]
ORIENT_ROOT = ROOT / "artifacts/orientation-spectra-2026-10-06"
ANCHOR_ROOT = ROOT / "artifacts/phase-subspace-v1-complete-2026-10-06/anchors"
WC_ANALYSIS = ROOT / "artifacts/docker-corpus-variants-v1/runs/wc-v1/analysis-18"

# Historical files this sprint must leave byte-identical (checked at close).
HISTORICAL = [
    "git/RetrievalArena/retrieval_arena/graph/magnetic_phase.py",
    "git/context/scripts/orientation_spectra_09c.py", "git/context/scripts/restructuring_runs.py",
    "git/context/scripts/structure_forcing_12.py", "git/context/scripts/wc_refinement_15.py",
    "git/context/scripts/wc_refine_17.py", "git/context/scripts/wc_spectra_18.py",
    "git/context/scripts/audit_phase_flow_v2.py", "git/context/scripts/audit_normalization_22.py",
    "git/context/evidence/phase-flow-repair-v1-2026-10-06.json",
    "git/context/evidence/orientation-spectra-inputs-v1-2026-10-06.json",
    "git/context/evidence/orientation-spectra-analysis-plan-v1-2026-10-06.json",
    "git/context/evidence/orientation-spectra-results-v1-2026-10-06.md",
    "git/context/evidence/wc-refinement-run-v1-2026-10-18.json",
    "git/context/evidence/wc-spectra-18-v1-2026-10-19.json",
    "git/context/evidence/normalization-planning-audit-v1-2026-10-25.json",
    "artifacts/orientation-spectra-2026-10-06/measures/measures.json",
    "artifacts/orientation-spectra-2026-10-06/exposure/exposure.json",
    "artifacts/orientation-spectra-2026-10-06/analysis/analysis.json",
]


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


def ms():
    if str(RA) not in sys.path:
        sys.path.insert(0, str(RA))
    from retrieval_arena.graph import magnetic_spectrum
    return magnetic_spectrum


# ------------------------------------------------------------------ plan

def plan(_args):
    receipt = read_json(RECEIPT)
    head = subprocess.run(["git", "-C", str(RA), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    if not head.startswith(RA_COMMIT) or receipt["source_commit"] != head:
        raise SystemExit("ra-head-differs-from-receipt")
    value = {
        "schema_version": "docspectra.icsc.normalized_magnetic_22.plan.v1", "created": PLAN_DATE, "model_calls": 0,
        "status": "frozen before any corpus normalized spectrum was computed; post hoc analyses of already observed "
                  "data, not independent confirmation",
        "ra": {"branch": "icsc2027/downstream-case", "commit": head, "image_receipt": str(RECEIPT.relative_to(ROOT)),
               "image_receipt_sha256": sha(RECEIPT), "image": receipt["image"], "platform": receipt["platform"],
               "dependency_lock_sha256": receipt["dependency_lock_sha256"],
               "image_build": "build-verification.ps1 -ProvisionedImage on icsc-c2 (lock unchanged), --network none",
               "module": "retrieval_arena/graph/magnetic_spectrum.py",
               "module_sha256": sha(RA / "retrieval_arena/graph/magnetic_spectrum.py")},
        "measurement": {
            "version": "normalized_magnetic_spectrum.v1",
            "operator": "L_q = diag(d>0) - D^+1/2 (W o exp(2 pi i q (A - A^T))) D^+1/2, W = (A + A^T)/2, binary A, "
                        "self loops excluded, isolates kept",
            "q0_comparator": "same W (weighted ordinary normalized Laplacian); legacy binary-skeleton value labelled legacy",
            "unnormalized_comparator": "D - W o exp(...) on the identical view (parity with magnetic_phase.magnetic_matrix)",
            "modes": "lowest lambda1, lambda2, lambda3, raw, with near-zero flags; no deletion, absolute value or clipping",
            "solver": "dense numpy.linalg.eigh, complex128, full spectrum, single-threaded BLAS inside the image",
            "tolerances": {"residual_relative": 1e-10, "orthogonality_absolute": 1e-10, "hermitian_absolute": 1e-12,
                           "bound_absolute": 1e-10, "near_zero_absolute": 1e-9},
            "tolerance_basis": "unit fixtures (20 tests) pass at these values; eigenvalue error of a dense Hermitian "
                               "solver is O(n eps ||L||) <= ~1e-12 for n <= 5000, ||L|| <= 2",
            "sparse_parity": "scipy eigsh on 2I - L for lowest 3 modes, compared on one small and one representative "
                             "graph: eigenvalues within 1e-10 and projector distance < 1e-8 where the 3/4 gap > 1e-6; "
                             "the corpus panels use the dense solver",
            "q": {"continuity": 0.25, "grid": GRID,
                  "grid_views": "anchor LWCCs, orientation historical LWCCs, whole-corpus LWCCs (normalized and unnormalized)",
                  "endpoint_views": "full graphs and area views: q in {0, 0.25} (normalized and unnormalized)",
                  "rule": "every charge reported; none selected by apparent effect"}},
        "panels": {
            "anchors": {"inputs": "14 corpora of phase-flow-repair-v1::migration", "views": [
                "induced on the frozen historical retrieval-no-FAQ LWCC (nodes.json/selected-edges.csv)",
                "own LWCC (deterministic: size, then smallest id); reproduction of the historical selection recorded",
                "full retrieval graph: all non-FAQ pages by the pinned importer when the count equals the manifest; "
                "else unavailable"], "not_mixed_with": "SDM snapshots"},
            "orientation": {"inputs": "32 versions x main and index/release-note-removed edge files (09c)", "views": [
                "induced on the historical fixed-skeleton LWCC of each family (main 844 nodes; sensitivity recomputed "
                "by 09c's rule)", "full traversal graph of 1,263 pages"],
                "association_set": "29 random/partial versions; F, R, U descriptive contrasts only"},
            "wc": {"inputs": "O, S, LT, R0, LT/O-prime, R0/O-prime and 30 saved LN/PC/RN nulls (36)",
                   "views": ["full retrieval graph", "own LWCC"], "verification": [
                       "LT/R0 final tree ids equal wc-refinement-run-v1 records", "retrieval and LWCC graph digests "
                       "equal the saved Sprint 18 profiles", "O and S profiles equal"]},
            "area": {"inputs": "18 LT and 23 R0 admitted areas; O-prime, own-area edited graph and up to five own-area "
                              "LN graphs per area (F6 bucket_removed_edges, bucket_ln, subgraph_nodes_v2)",
                     "views": ["fixed common S induced view", "each graph's own LWCC within S (historical rule)"],
                     "verification": "S count, edit counts, nulls built and every old measure-vector row recomputed "
                                     "with the pinned functions equal the saved analysis-18 summaries"}},
        "quarantine_rule": "a missing or mismatched graph is quarantined with its reason, never replaced; a source "
                           "mismatch is kept distinct from a floating-point difference; unexpected gaps mean partial "
                           "completion",
        "analyses": {
            "A": {"population": "14 anchors (one graph each; not pooled with variants)",
                  "outputs": ["profile table (normalized grid on historical LWCC, full view endpoints, structure, "
                              "trophic incoherence, q0 and legacy comparators, unnormalized modes)",
                              "q-response curves", "same-graph old (unnormalized) vs new ranks and scales"],
                  "statistics": "descriptive Spearman rank correlations only (n = 14), no p-values, no predictive model"},
            "B": {"replay": "09c frozen_tests (orientation_spectra_09c.py, imported read-only) on the saved exposure "
                            "table with normalized LWCC lambda1/lambda2 at q = 0.25 substituted for the primary "
                            "measures and normalized grid values for the secondary; unchanged controls (out-degree "
                            "Gini, sources, sinks, mean seed out-degree), seeds (seed_base 0 main, 100 sensitivity), "
                            "20,000 permutations, within-family Holm",
                  "families": "main and sensitivity reported separately", "comparison": "old analysis.json values "
                  "under the same outcomes and graph identities", "label": "newly exploratory; automatic-candidate-"
                  "support exposure, not verified relevance or answer quality"},
            "C": {"profiles": "O, S, LT, R0, O-prime vs LN/PC/RN families: raw deltas, null mean/SD/min/max, "
                              "component and node-set changes",
                  "normalized_analogue": "Sprint 18 rule with normalized lambda1 q = 0.25 on each graph's own LWCC "
                                         "within S: d = v - O', null deltas LN - O', z = (d - mean null) / pooled "
                                         "within-area null SD (sqrt of mean ddof-1 variances over all area-arm rows), "
                                         "LT - R0 per paired area, exact two-sided sign flip; SA1 drops LT A10, A12, A21",
                  "fixed_S_contrast": "same arithmetic on the fixed S view, reported separately",
                  "label": "normalized exploratory analogue; Sprint 18 p = 0.079 and SA1 stay the historical results"}},
        "verdict_rule": {
            "S1_numerical": "every required receipt qualified and parity/fixture tests pass in the container",
            "S2_distinct": "on the 14 anchors, |Spearman| < 0.9 between normalized LWCC lambda1(q=.25) and each of: "
                           "weighted q0 lambda2, reciprocal-arc fraction, trophic incoherence, out-degree Gini, "
                           "log node count, isolate fraction of the full view; and the same for lambda2(q=.25)",
            "S3_orientation": "on the main family, normalized lambda1(q=.25) range over the 32 versions > 1e-6 while "
                              "the q0 spectrum is constant over F/R/random/partial versions (< 1e-9), and |Spearman| "
                              "< 0.9 with each directed-degree control over the 29 association versions",
            "S4_application": "in C, LT or R0 normalized lambda1 or lambda2 (q=.25, whole-corpus LWCC) lies outside "
                              "[min, max] of its own LN family",
            "verdicts": {"structural characterization supported": "S1 and S2 and S3 and S4",
                         "validity/limits contribution only": "S1 and not (S2 and S3)",
                         "additional utility validation needed": "S1 and S2 and S3 but not S4",
                         "partial": "S1 fails on a required panel: no verdict beyond the evidence"},
            "boundary": "no verdict establishes retrieval or answer utility, an LT detector or a quality objective"},
        "historical_inputs_sha256": {p: sha(ROOT / p) for p in HISTORICAL},
    }
    print(json.dumps({"plan_sha256": write_json(PLAN, value, new=True)}))


# ------------------------------------------------------------------ bundles

def make_bundle(graph_id, nodes, edges, provenance, node_sets=None):
    m = ms()
    admitted = m.admit_graph(nodes, edges)
    if admitted["duplicate_edges_removed"] or admitted["self_loops_removed"]:
        provenance = dict(provenance, admission={k: admitted[k] for k in ("input_edge_rows", "duplicate_edges_removed",
                                                                          "self_loops_removed")})
    b = {"schema_version": m.BUNDLE_SCHEMA, "graph_id": graph_id, "nodes": admitted["nodes"],
         "edges": [list(e) for e in admitted["edges"]],
         "canonical_graph_sha256": m.graph_sha256(admitted["nodes"], admitted["edges"]), "provenance": provenance}
    if node_sets:
        b["node_sets"] = {k: sorted(v) for k, v in node_sets.items()}
    m.validate_bundle(b)
    return b


def save_bundle(panel, b):
    name = b["graph_id"].replace("/", "__") + ".json"
    path = INPUTS / panel / "bundles" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canon(b) + b"\n")
    return {"graph_id": b["graph_id"], "bundle": f"bundles/{name}", "bundle_sha256": sha(path),
            "canonical_graph_sha256": b["canonical_graph_sha256"], "nodes": len(b["nodes"]), "edges": len(b["edges"]),
            "node_sets": {k: {"size": len(v), "sha256": hashlib.sha256(canon(v)).hexdigest()}
                          for k, v in (b.get("node_sets") or {}).items()}, "provenance": b["provenance"]}


def read_edge_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return [(r["source"], r["target"]) for r in csv.DictReader(handle)]


def bundle_anchors(ledger):
    sys.path.insert(0, str(HERE))
    from diagnose_phase_flow import historical
    gm = historical()
    m = ms()
    repair = read_json(EVIDENCE / "phase-flow-repair-v1-2026-10-06.json")
    roster = read_json(EVIDENCE / "cross-corpus-roster-recovered-v4-2026-09-28.json")
    rows = {r["corpus_id"]: r for r in roster["rows"]}
    out = []
    for mig in repair["migration"]:
        cid, inp = mig["corpus_id"], mig["inputs"]
        entry = {"panel": "anchors", "graph_id": f"anchor/{cid}"}
        folder = ANCHOR_ROOT / cid
        if sha(Path(inp["source_graph_csv"])) != inp["source_graph_csv_sha256"] \
                or sha(folder / "nodes.json") != inp["frozen_nodes_sha256"] \
                or sha(folder / "selected-edges.csv") != inp["frozen_selected_edges_sha256"]:
            ledger.append(dict(entry, status="quarantined", reason="source-hash-mismatch"))
            continue
        row = rows[cid]
        rec = read_json(ROOT / row["export_receipt_path"])
        snap = next(s for s in rec["snapshots"] if s["commit"] == row["cikm_anchor_commit"])
        source = ROOT / snap["snapshot_path"] / "source" / snap["relative_scope"]
        ns = [p.doc_id for p in gm.discover_markdown_pages(source, faq_patterns=row["faq_exclusion_patterns"]) if not p.is_faq]
        es = read_edge_csv(inp["source_graph_csv"])
        frozen_nodes = read_json(folder / "nodes.json")
        frozen_edges = read_edge_csv(folder / "selected-edges.csv")
        full_ok = len(ns) == snap["manifest_counts"]["num_corpus_pages"] and len(set(ns)) == len(ns)
        known = set(ns)
        outside = [(s, t) for s, t in es if s not in known or t not in known]
        chosen = gm.largest_weak_component(ns, es)
        historical_ok = sorted(n for n in ns if n in chosen) == sorted(frozen_nodes) and \
            sorted({(s, t) for s, t in es if s in chosen and t in chosen}) == sorted(set(frozen_edges))
        prov = {"corpus_id": cid, "commit": mig["commit"], "source_graph_csv_sha256": inp["source_graph_csv_sha256"],
                "frozen_nodes_sha256": inp["frozen_nodes_sha256"],
                "frozen_selected_edges_sha256": inp["frozen_selected_edges_sha256"],
                "node_policy": "pinned CIKM importer discover_markdown_pages, FAQ excluded by the roster patterns",
                "import_faq_patterns": row["faq_exclusion_patterns"],
                "edge_rows_with_endpoint_outside_nodes": len(outside), "edge_csv_rows": len(es),
                "manifest_num_corpus_pages": snap["manifest_counts"]["num_corpus_pages"],
                "reconstruction": "audit_phase_flow_v2.anchor_graphs logic; importer pinned by diagnose_phase_flow.historical"}
        if not historical_ok:
            ledger.append(dict(entry, status="quarantined", reason="historical-lwcc-not-reproduced"))
            continue
        if full_ok:
            b = make_bundle(f"anchor/{cid}", ns, [e for e in es if e[0] in known and e[1] in known], prov,
                            {"historical_lwcc": frozen_nodes})
            own, _, rec_view = m.select_view(b["nodes"], [tuple(e) for e in b["edges"]], {"kind": "lwcc"})
            entry.update(status="bundled", full_view="available",
                         own_lwcc_reproduces_historical=sorted(own) == sorted(frozen_nodes),
                         own_lwcc_tie_count=rec_view["lwcc_tie_count"])
        else:
            b = make_bundle(f"anchor/{cid}", frozen_nodes, frozen_edges, dict(prov, full_view="unavailable: node count "
                            "differs from the manifest"), {"historical_lwcc": frozen_nodes})
            entry.update(status="bundled", full_view="unavailable", reason="full node inventory not established")
        out.append(save_bundle("anchors", b))
        ledger.append(entry)
        print("anchor", cid, entry["status"], len(b["nodes"]), flush=True)
    return out


def bundle_orientation(ledger):
    sys.path.insert(0, str(HERE))
    import orientation_spectra_09c as o9
    inputs = read_json(EVIDENCE / "orientation-spectra-inputs-v1-2026-10-06.json")
    nodes = sorted(p.stem for p in (ORIENT_ROOT / "retrieval-input/corpus").glob("*.md"))
    lwcc = read_json(ORIENT_ROOT / "lwcc-nodes.json")
    lwcc_hash_ok = hashlib.sha256(o9.canon(sorted(lwcc))).hexdigest() == inputs["graph"]["lwcc_node_list_sha256"]
    f_sens = o9.read_edges_csv(ORIENT_ROOT / "versions/F/graph_edges_sensitivity.csv")
    lwcc_sens = sorted(o9.weak_components(nodes, f_sens)[0])
    out = []
    if len(nodes) != inputs["graph"]["nodes"] or len(lwcc) != inputs["graph"]["lwcc_nodes"] or not lwcc_hash_ok:
        ledger.append({"panel": "orientation", "graph_id": "orientation/*", "status": "quarantined",
                       "reason": "node universe or historical LWCC not reproduced"})
        return out
    for rec in inputs["versions"]:
        vid = rec["version_id"]
        for tag, fname, key, lw in (("main", "graph_edges.csv", "edge_file_sha256", lwcc),
                                    ("sensitivity", "graph_edges_sensitivity.csv", "sensitivity_edge_file_sha256", lwcc_sens)):
            gid = f"orientation/{vid}/{tag}"
            path = ORIENT_ROOT / "versions" / vid / fname
            if sha(path) != rec[key]:
                ledger.append({"panel": "orientation", "graph_id": gid, "status": "quarantined", "reason": "edge-hash-mismatch"})
                continue
            edges = o9.read_edges_csv(path)
            own = sorted(o9.weak_components(nodes, edges)[0])
            prov = {"version_id": vid, "family": rec["family"], "edge_set": tag, "edge_file_sha256": rec[key],
                    "node_policy": "09c retrieval-input corpus stems (retrieval_no_faq, 1,263 pages)",
                    "historical_view": "09c fixed-skeleton LWCC (lwcc-nodes.json for main; weak_components of F "
                                       "sensitivity edges for sensitivity)"}
            b = make_bundle(gid, nodes, edges, prov, {"historical_lwcc": lw})
            out.append(save_bundle("orientation", b))
            ledger.append({"panel": "orientation", "graph_id": gid, "status": "bundled",
                           "own_lwcc_equals_historical": own == lw})
    print("orientation bundles", len(out), flush=True)
    return out


def wc_context():
    """Reconstruct the Sprint 18 whole-corpus contexts exactly as wc_spectra_18.run does (read-only imports)."""
    sys.path.insert(0, str(HERE))
    import wc_spectra_18 as s18
    w17, wr, rr, wf = s18.w17, s18.wr, s18.rr, s18.wf
    s18.gates()
    records = wf.check_lock(w17.RECORDS)
    plan18 = wr.load_run_plan(w17.REFINE_PLAN)
    rows = w17.effective(w17.ledger_rows())
    vm = rr.read_json(wr.V6)
    gm = wr.dcv.pinned(vm)
    finals = {arm: records["arms"][arm]["final_tree_path"] for arm in wr.EDIT_ARMS}
    # records is wc-refinement-run-v1-2026-10-18.json (lock-checked); recompute each final tree's id.
    tree_checks = {arm: wr.tree_id(p) == records["arms"][arm]["final_tree_id"] for arm, p in finals.items()}
    vctx = {arm: wr.variant_context(gm, vm, finals[arm], ledger_rows=rows, arm=arm) for arm in wr.EDIT_ARMS}
    o_ctx = wr.variant_context(gm, vm, s18.RUN_ROOT / "O-r01" / "editor" / "source")
    s_ctx = wr.variant_context(gm, vm, s18.RUN_ROOT / "S-r01" / "editor" / "source")
    return {"s18": s18, "wr": wr, "rr": rr, "records": records, "plan": plan18, "vctx": vctx, "O": o_ctx, "S": s_ctx,
            "tree_checks": tree_checks}


def bundle_wc(ledger, ctx):
    rr, wr, s18 = ctx["rr"], ctx["wr"], ctx["s18"]
    st = rr.load_struct()
    o9, mp = rr.load_spectral()
    out = []
    graphs = [("O", ctx["O"]["nodes"], ctx["O"]["v_edges"], {}), ("S", ctx["S"]["nodes"], ctx["S"]["v_edges"], {})]
    for arm in wr.EDIT_ARMS:
        v = ctx["vctx"][arm]
        graphs += [(arm, v["nodes"], v["v_edges"], {"arm": arm, "final_tree_id": ctx["records"]["arms"][arm]["final_tree_id"],
                                                    "final_tree_id_verified": ctx["tree_checks"][arm]}),
                   (f"{arm}/O-prime", v["nodes"], v["o_mapped"], {"arm": arm, "role": "mapped baseline"})]
        for t in ("ln", "pc", "rn"):
            for k in range(1, wr.NULLS_PER_TYPE + 1):
                path = WC_ANALYSIS / "controls" / f"{arm}__{t}-{k}.json"
                g = read_json(path)
                graphs.append((f"{arm}/{t}-{k}", g["nodes"], [tuple(e) for e in g["edges"]],
                               {"arm": arm, "null_type": t.upper(), "seed_index": k, "control_file_sha256": sha(path),
                                "nodes_equal_arm_nodes": list(g["nodes"]) == list(v["nodes"])}))
    for gid, nodes, edges, extra in graphs:
        entry = {"panel": "wc", "graph_id": f"wc/{gid}"}
        saved_path = WC_ANALYSIS / "spectra" / "full" / f"{gid.replace('/', '__')}.json"
        saved = read_json(saved_path)["profile"]
        ns = sorted(nodes)
        es = mp.canonical_edges(ns, edges)
        lw, le = s18.sf.lwcc_of(o9, mp, ns, es)
        checks = {"retrieval_digest_equal": st.graph_digest(ns, es) == saved["retrieval_no_faq"]["graph_sha256"],
                  "lwcc_digest_equal": st.graph_digest(lw, le) == saved["retrieval_no_faq_lwcc"]["graph_sha256"],
                  "lwcc_nodes_equal": len(lw) == saved["retrieval_no_faq_lwcc"]["nodes"]}
        if not all(checks.values()) or extra.get("final_tree_id_verified") is False:
            ledger.append(dict(entry, status="quarantined", reason="digest-or-tree-mismatch", checks=checks))
            continue
        prov = dict(extra, saved_profile_sha256=sha(saved_path), sprint18_checks=checks,
                    node_policy="variant_context nodes (docs_scope_v1 retrieval pages, FAQ excluded by the pinned importer)")
        b = make_bundle(f"wc/{gid}", ns, es, prov)
        own, _, _ = ms().select_view(b["nodes"], [tuple(e) for e in b["edges"]], {"kind": "lwcc"})
        out.append(save_bundle("wc", b))
        ledger.append(dict(entry, status="bundled", own_lwcc_equals_historical=own == sorted(lw), checks=checks))
    print("wc bundles", len(out), flush=True)
    return out


def bundle_area(ledger, ctx):
    rr, wr, s18 = ctx["rr"], ctx["wr"], ctx["s18"]
    sf = s18.sf
    o9, mp = rr.load_spectral()
    areas = ctx["plan"]["areas"]
    out = []
    for arm in wr.EDIT_ARMS:
        v = ctx["vctx"][arm]
        om = v["o_mapped"]
        buckets, _ = wr.area_buckets(v)
        removed = s18.bucket_removed_edges(v)
        nodes_by_area = wr.area_node_sets(v, areas)
        for b, x in sorted(buckets.items()):
            if b not in areas:
                continue
            saved_path = WC_ANALYSIS / "subgraph" / f"{arm}__{b}.json"
            entry = {"panel": "area", "graph_id": f"area/{arm}/{b}"}
            if not saved_path.exists():
                ledger.append(dict(entry, status="quarantined", reason="saved-area-summary-missing"))
                continue
            saved = read_json(saved_path)
            v_b = sorted((set(om) - set(removed.get(b, []))) | set(map(tuple, x["added_edges"])))
            lns = []
            for k in range(1, wr.NULLS_PER_TYPE + 1):
                e = s18.bucket_ln(v, x, b, k, f"{s18.wr_run_id()}/{arm}", nodes_by_area)
                if e is not None:
                    lns.append((f"{arm}/ln-{k}/{b}", e))
            S = sf.subgraph_nodes_v2(x["T"], om, v_b, x["added_pages"], [e for _, e in lns])
            rows = wr.subgraph_rows(o9, mp, S, [("O-prime", om), ("v", v_b)] + lns, om)
            counts_ok = (len(S) == saved["S"] and len(x["added_edges"]) == saved["edits_added"]
                         and len(removed.get(b, [])) == saved["edits_removed"] and len(lns) == saved["nulls_built"])
            diffs = []
            exact = True
            for gid, row in rows.items():
                old = saved["rows"].get(gid)
                if old is None:
                    exact = False
                    diffs.append(float("inf"))
                    continue
                for key, val in row.items():
                    o = old.get(key)
                    if isinstance(val, float) and isinstance(o, float):
                        diffs.append(abs(val - o))
                        exact &= val == o
                    elif val != o:
                        exact = False
                        diffs.append(float("inf"))
            max_diff = max(diffs) if diffs else 0.0
            if not counts_ok or set(rows) != set(saved["rows"]) or not math.isfinite(max_diff) or max_diff > 1e-9:
                ledger.append(dict(entry, status="quarantined", reason="source-mismatch" if not counts_ok or
                                   set(rows) != set(saved["rows"]) else "tolerance-mismatch", max_abs_difference=max_diff))
                continue
            base = {"arm": arm, "area": b, "S_size": len(S), "S_sha256": hashlib.sha256(canon(sorted(S))).hexdigest(),
                    "edits_added": len(x["added_edges"]), "edits_removed": len(removed.get(b, [])),
                    "rule": "F6: own-area edits (bucket_removed_edges, added_edges), own-area LN (bucket_ln), "
                            "S = subgraph_nodes_v2; bundle = graph induced on S (isolates in S kept)",
                    "saved_summary_sha256": sha(saved_path), "old_rows_exact": exact, "old_rows_max_abs_difference": max_diff}
            graphs = [("O-prime", om), ("v", v_b)] + [(g.split("/")[1], e) for g, e in lns]
            for role, edges in graphs:
                ind = sf.induced(edges, S)
                full_hash = ms().graph_sha256(sorted(v["nodes"]), mp.canonical_edges(sorted(v["nodes"]), edges))
                bb = make_bundle(f"area/{arm}/{b}/{role}", S, ind, dict(base, role=role, parent_graph_sha256=full_hash))
                out.append(save_bundle("area", bb))
            ledger.append(dict(entry, status="bundled", graphs=len(graphs), old_rows_exact=exact, max_abs_difference=max_diff))
            print("area", arm, b, len(S), "exact" if exact else f"max diff {max_diff:.2e}", flush=True)
    return out


def bundle(args):
    if not PLAN.exists():
        raise SystemExit("plan-not-frozen")
    panels = args.panels or list(PANELS)
    manifest = read_json(MANIFEST) if MANIFEST.exists() else {"schema_version": "docspectra.icsc.normalized_magnetic_22.inputs.v1",
                                                               "plan_sha256": sha(PLAN), "panels": {}}
    ledger = read_json(LEDGER) if LEDGER.exists() else {"schema_version": "docspectra.icsc.normalized_magnetic_22.ledger.v1",
                                                         "panels": {}}
    ctx = wc_context() if {"wc", "area"} & set(panels) else None
    for panel in panels:
        rows = []
        if panel == "anchors":
            out = bundle_anchors(rows)
        elif panel == "orientation":
            out = bundle_orientation(rows)
        elif panel == "wc":
            out = bundle_wc(rows, ctx)
        else:
            out = bundle_area(rows, ctx)
        manifest["panels"][panel] = out
        ledger["panels"][panel] = rows
    if ctx:
        manifest["wc_final_trees"] = {arm: {"recorded": ctx["records"]["arms"][arm]["final_tree_id"],
                                            "recomputed_equal": ctx["tree_checks"][arm]} for arm in ctx["tree_checks"]}
    manifest["plan_sha256"] = sha(PLAN)
    manifest["driver_sha256"] = sha(Path(__file__))
    manifest["external_source_hashes"] = {"phase_flow_repair": sha(EVIDENCE / "phase-flow-repair-v1-2026-10-06.json"),
                                          "orientation_inputs": sha(EVIDENCE / "orientation-spectra-inputs-v1-2026-10-06.json"),
                                          "wc_run": sha(EVIDENCE / "wc-refinement-run-v1-2026-10-18.json"),
                                          "wc_spectra_18": sha(EVIDENCE / "wc-spectra-18-v1-2026-10-19.json")}
    for panel, rows in ledger["panels"].items():
        ledger.setdefault("summary", {})[panel] = {s: sum(1 for r in rows if r["status"] == s) for s in ("bundled", "quarantined")}
    print(json.dumps({"manifest_sha256": write_json(MANIFEST, manifest), "ledger_sha256": write_json(LEDGER, ledger),
                      "summary": ledger["summary"]}, indent=1))


# ------------------------------------------------------------------ operator requests and runs

def panel_jobs(panel, entry):
    both = lambda qs: {"normalized_q": qs, "unnormalized_q": qs, "legacy": True}
    gid, path = entry["graph_id"], entry["bundle"]
    if panel in ("anchors", "orientation"):
        jobs = [dict(job_id=f"{gid}#historical_lwcc", bundle=path, view={"kind": "induced", "node_set": "historical_lwcc"},
                     **both(GRID))]
        if panel == "orientation" or entry["provenance"].get("full_view") != "unavailable":
            jobs.append(dict(job_id=f"{gid}#full", bundle=path, view={"kind": "full"}, **both(ENDPOINTS)))
        return jobs
    if panel == "wc":
        return [dict(job_id=f"{gid}#lwcc", bundle=path, view={"kind": "lwcc"}, **both(GRID)),
                dict(job_id=f"{gid}#full", bundle=path, view={"kind": "full"}, **both(ENDPOINTS))]
    return [dict(job_id=f"{gid}#fixed_S", bundle=path, view={"kind": "full"}, **both(ENDPOINTS)),
            dict(job_id=f"{gid}#lwcc_in_S", bundle=path, view={"kind": "lwcc"}, **both(ENDPOINTS))]


def write_request(root: Path, config):
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_bytes(canon(config) + b"\n")
    rows = []
    # The operator sorts POSIX paths case-sensitively; Windows Path ordering is case-insensitive.
    for path in sorted(root.rglob("*"), key=lambda x: x.relative_to(root).parts):
        if path.is_file() and path.name != "operator-request.json":
            rows.append({"path": path.relative_to(root).as_posix(), "sha256": sha(path), "size_bytes": path.stat().st_size})
    expected = {"config.json"} | {j["bundle"] for j in config["jobs"]}
    if {r["path"] for r in rows} != expected:
        raise SystemExit(f"input-root-contents:{root}")
    request = {"schema_version": "retrieval_arena.graph_spectra_operator_request.v1", "command": "measure-graph-spectra",
               "config_path": "config.json", "input_inventory": rows,
               "input_inventory_sha256": hashlib.sha256(canon(rows)).hexdigest()}
    (root / "operator-request.json").write_bytes(canon(request) + b"\n")
    return sha(root / "config.json")


def request(args):
    manifest = read_json(MANIFEST)
    out = {}
    for panel in args.panels or list(PANELS):
        jobs = [j for e in manifest["panels"][panel] for j in panel_jobs(panel, e)]
        config = {"schema_version": "retrieval_arena.graph_spectra_config.v1", "spectrum_config": {"solver": "dense"},
                  "jobs": jobs}
        out[panel] = {"jobs": len(jobs), "config_sha256": write_request(INPUTS / panel, config)}
    print(json.dumps(out, indent=1))


def benchmark(_args):
    """One small and one representative graph, dense and sparse, before the panels (operator runs)."""
    manifest = read_json(MANIFEST)
    picks = {"anchors": "anchor/agopengps", "wc": "wc/O"}
    for solver in ("dense", "sparse"):
        root = OUT / "benchmark" / f"input-{solver}"
        jobs = []
        for panel, gid in picks.items():
            e = next(x for x in manifest["panels"][panel] if x["graph_id"] == gid)
            dst = root / e["bundle"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((INPUTS / panel / e["bundle"]).read_bytes())
            view = {"kind": "induced", "node_set": "historical_lwcc"} if panel == "anchors" else {"kind": "lwcc"}
            jobs.append({"job_id": f"{gid}#{solver}", "bundle": e["bundle"], "view": view, "normalized_q": GRID,
                         "unnormalized_q": [], "legacy": False})
        write_request(root, {"schema_version": "retrieval_arena.graph_spectra_config.v1",
                             "spectrum_config": {"solver": solver}, "jobs": jobs})
        run_operator(root, OUT / "benchmark" / f"run-{solver}")


def run_operator(input_root: Path, output_root: Path):
    plan_value = read_json(PLAN)
    receipt_path = output_root / "operator_receipt.v1.json"
    if receipt_path.exists():
        rec = read_json(receipt_path)
        valid = (rec["status"] == "completed" and rec["identity"]["source_commit"] == plan_value["ra"]["commit"]
                 and rec["identity"]["image_reference"] == plan_value["ra"]["image"]["reference"]
                 and rec["metric_code_sha256"] == plan_value["ra"]["module_sha256"]
                 and rec["config_sha256"] == sha(input_root / "config.json")
                 and rec["request_sha256"] == sha(input_root / "operator-request.json"))
        if valid:
            print(f"reuse {output_root.name}: receipt matches graph inputs, code, config and image", flush=True)
            return rec
        stale = output_root.with_name(output_root.name + f".stale-{len(list(output_root.parent.glob(output_root.name + '.stale-*')))}")
        output_root.rename(stale)
        print(f"moved stale run to {stale.name}", flush=True)
    output_root.mkdir(parents=True, exist_ok=False)
    (output_root / ".retrieval-arena-output-owner").write_bytes(b"retrieval_arena.output_root.v1\n")
    cmd = ["pwsh", "-NoProfile", "-File", str(RA / "scripts/run-verification.ps1"), "-Command", "measure-graph-spectra",
           "-InputRoot", str(input_root), "-OutputRoot", str(output_root), "-Receipt", str(RECEIPT)]
    log = output_root.with_name(output_root.name + ".log")
    with log.open("wb") as handle:
        code = subprocess.run(cmd, stdout=handle, stderr=subprocess.STDOUT).returncode
    rec = read_json(receipt_path) if receipt_path.exists() else {"status": "no-receipt"}
    print(json.dumps({"run": output_root.name, "exit": code, "status": rec["status"],
                      "error": rec.get("error_code"), "jobs": rec.get("job_count"),
                      "numerical": rec.get("numerical_status_counts")}), flush=True)
    return rec


def measure(args):
    for panel in args.panels or list(PANELS):
        run_operator(INPUTS / panel, OUT / "runs" / panel)


# ------------------------------------------------------------------ analysis helpers (pure; context tests cover them)

def load_rows(panel):
    """Receipts of a completed panel run, keyed by job id, after checking the run against the plan."""
    run = OUT / "runs" / panel
    rec = read_json(run / "operator_receipt.v1.json")
    plan_value = read_json(PLAN)
    if rec["status"] != "completed" or rec["identity"]["source_commit"] != plan_value["ra"]["commit"] \
            or rec["metric_code_sha256"] != plan_value["ra"]["module_sha256"]:
        raise SystemExit(f"panel-run-not-admitted:{panel}")
    if sha(run / "graph_spectra.v1.jsonl") != rec["outputs"]["graph_spectra.v1.jsonl"]:
        raise SystemExit(f"panel-table-hash:{panel}")
    rows = {}
    for line in (run / "graph_spectra.v1.jsonl").read_bytes().splitlines():
        r = json.loads(line)
        rows[r["job_id"]] = r
    return rows, rec


def lam(r, q, i=1, operator="normalized"):
    s = r[operator].get(f"{q:.2f}")
    if s is None or s["status"] != "qualified":
        return None
    return s.get(f"lambda{i}")


def spearman(x, y):
    import numpy as np
    from scipy.stats import rankdata
    pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    if len(pairs) < 3:
        return None
    rx, ry = rankdata([a for a, _ in pairs]), rankdata([b for _, b in pairs])
    if np.std(rx) == 0 or np.std(ry) == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def pooled_null_sd(null_sets):
    """sqrt(mean within-row null variance, ddof 1) over rows with >= 2 complete nulls (Sprint 12/18 rule)."""
    vs = []
    for xs in null_sets:
        if len(xs) >= 2 and all(x is not None for x in xs):
            mean = sum(xs) / len(xs)
            vs.append(sum((x - mean) ** 2 for x in xs) / (len(xs) - 1))
    return math.sqrt(sum(vs) / len(vs)) if vs else None


def area_analogue(per_area, sign_flip, drop=None):
    """per_area: {area: {arm: {"d": float|None, "nulls": [float|None]}}}; returns z, paired LT - R0 and the test.

    z = (d - mean null) / pooled SD over every area-arm row; only areas with both arms enter the test."""
    rows = [r for arms in per_area.values() for r in arms.values()]
    pooled = pooled_null_sd([r["nulls"] for r in rows])
    z = {}
    for a, arms in per_area.items():
        for arm, r in arms.items():
            xs = r["nulls"]
            ok = r["d"] is not None and len(xs) >= 2 and all(x is not None for x in xs) and pooled
            z.setdefault(a, {})[arm] = (r["d"] - sum(xs) / len(xs)) / pooled if ok else None
    diffs, not_computable = {}, {}
    for a, arms in sorted(z.items()):
        if "LT" in arms and "R0" in arms:
            if arms["LT"] is None or arms["R0"] is None:
                not_computable[a] = arms
            elif not (drop and a in drop):
                diffs[a] = arms["LT"] - arms["R0"]
    test = sign_flip(list(diffs.values()))
    return {"pooled_null_sd": pooled, "z": z, "differences": diffs, "not_computable": not_computable,
            "areas_one_arm_only": {a: sorted(v) for a, v in z.items() if not ("LT" in v and "R0" in v)}, **test}


def null_summary(values):
    import numpy as np
    xs = [v for v in values if v is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "mean": float(np.mean(xs)), "sd": float(np.std(xs, ddof=1)) if len(xs) > 1 else None,
            "min": float(min(xs)), "max": float(max(xs))}


# ------------------------------------------------------------------ analyses

ANCHOR_DESCRIPTORS = ("weighted_q0_lambda2", "reciprocal_arc_fraction", "trophic_incoherence", "outdegree_gini",
                      "log_nodes_full", "isolate_fraction_full")


def analysis_a(rows):
    from scipy.stats import rankdata
    table = []
    for gid in sorted({r["graph_id"] for r in rows.values()}):
        lw, full = rows[f"{gid}#historical_lwcc"], rows.get(f"{gid}#full")
        rec = {"graph_id": gid, "lwcc_nodes": lw["selection"]["nodes"], "lwcc_arcs": lw["selection"]["edges"],
               "numerical_status": [lw["numerical_status"], full["numerical_status"] if full else None]}
        for q in GRID:
            for i in (1, 2, 3):
                rec[f"lambda{i}_q{q:.2f}"] = lam(lw, q, i)
            rec[f"unnorm_lambda1_q{q:.2f}"] = lam(lw, q, 1, "unnormalized")
            rec[f"unnorm_lambda2_q{q:.2f}"] = lam(lw, q, 2, "unnormalized")
        rec["weighted_q0_lambda2"] = lw["weighted_normalized_lambda2_q0"]
        rec["legacy_binary_skeleton_lambda2"] = lw["legacy_binary_skeleton_normalized_lambda2"]
        st = lw["structure"]
        for k in ("reciprocal_arc_fraction", "outdegree_gini", "largest_scc_fraction", "source_fraction", "sink_fraction"):
            rec[k] = st[k]
        rec["mean_weighted_degree"] = st["weighted_degree"]["mean"]
        rec["weighted_degree_cv"] = st["weighted_degree"]["cv"]
        rec["trophic_incoherence"] = lw["trophic_incoherence"]["value"]
        if full:
            fs = full["structure"]
            rec.update(nodes_full=fs["nodes"], arcs_full=fs["arcs"], weak_components_full=fs["weak_components"],
                       isolate_fraction_full=fs["isolate_fraction"], lwcc_coverage_full=fs["lwcc_coverage"],
                       log_nodes_full=math.log(fs["nodes"]), trophic_incoherence_full=full["trophic_incoherence"]["value"],
                       full_lambda1_q0=lam(full, 0.0), full_lambda1_q0_25=lam(full, .25), full_lambda2_q0_25=lam(full, .25, 2),
                       full_zero_count_q0=full["normalized"]["0.00"]["numerical_zero_count"],
                       full_zero_count_q0_25=full["normalized"]["0.25"]["numerical_zero_count"])
        table.append(rec)
    keys = ["lambda1_q0.25", "lambda2_q0.25", "unnorm_lambda1_q0.25", "unnorm_lambda2_q0.25", "legacy_binary_skeleton_lambda2",
            "mean_weighted_degree", "weighted_degree_cv", "largest_scc_fraction", "lwcc_coverage_full", *ANCHOR_DESCRIPTORS]
    corr = {a: {b: spearman([r.get(a) for r in table], [r.get(b) for r in table]) for b in keys} for a in keys}

    def ranks(key):
        vals = [r[key] for r in table]
        return dict(zip([r["graph_id"] for r in table], (len(vals) + 1 - rankdata(vals)).tolist()))
    r_old, r_new = ranks("unnorm_lambda1_q0.25"), ranks("lambda1_q0.25")
    old_new = {"descending_rank_unnormalized_lambda1_q0.25": r_old, "descending_rank_normalized_lambda1_q0.25": r_new,
               "rank_spearman_lambda1": corr["lambda1_q0.25"]["unnorm_lambda1_q0.25"],
               "rank_spearman_lambda2": corr["lambda2_q0.25"]["unnorm_lambda2_q0.25"],
               "max_abs_rank_change_lambda1": max(abs(r_old[g] - r_new[g]) for g in r_old),
               "scale_ratio_lambda1_unnorm_over_norm": {r["graph_id"]: (r["unnorm_lambda1_q0.25"] / r["lambda1_q0.25"]
                                                                       if r["lambda1_q0.25"] else None) for r in table},
               "unnormalized_lambda1_vs_mean_weighted_degree": corr["unnorm_lambda1_q0.25"]["mean_weighted_degree"],
               "normalized_lambda1_vs_mean_weighted_degree": corr["lambda1_q0.25"]["mean_weighted_degree"]}
    # Collisions: the closest normalized lambda1 pairs and how far apart their simple descriptors are.
    pairs = []
    for i, a in enumerate(table):
        for b in table[i + 1:]:
            pairs.append({"pair": [a["graph_id"], b["graph_id"]],
                          "abs_diff_lambda1_q0.25": abs(a["lambda1_q0.25"] - b["lambda1_q0.25"]),
                          **{f"abs_diff_{k}": abs(a[k] - b[k]) for k in ANCHOR_DESCRIPTORS
                             if a.get(k) is not None and b.get(k) is not None}})
    pairs.sort(key=lambda p: p["abs_diff_lambda1_q0.25"])
    s2 = {m: {k: corr[m][k] for k in ANCHOR_DESCRIPTORS} for m in ("lambda1_q0.25", "lambda2_q0.25")}
    s2_pass = all(v is not None and abs(v) < 0.9 for d in s2.values() for v in d.values())
    return {"n_anchors": len(table), "table": table, "spearman": corr, "old_vs_new": old_new,
            "closest_lambda1_pairs": pairs[:5], "S2": {"correlations": s2, "pass": s2_pass},
            "q0_lambda1_connected_lwcc": {r["graph_id"]: r["lambda1_q0.00"] for r in table},
            "q_response": {r["graph_id"]: {f"{q:.2f}": [r[f"lambda{i}_q{q:.2f}"] for i in (1, 2, 3)] for q in GRID}
                           for r in table},
            "label": "descriptive; 14 corpora, one graph each; no p-values"}


def analysis_b(rows):
    sys.path.insert(0, str(HERE))
    import orientation_spectra_09c as o9
    meas = read_json(ORIENT_ROOT / "measures/measures.json")
    expo = read_json(ORIENT_ROOT / "exposure/exposure.json")
    old = read_json(ORIENT_ROOT / "analysis/analysis.json")
    out = {"label": "newly exploratory replay; automatic-candidate-support exposure, not verified relevance or answer "
                    "quality; versions share one skeleton, one seed set and one corpus", "families": {}}
    for i, tag in enumerate(("main", "sensitivity")):
        old_rows = o9.family_table(meas, expo, tag)
        new_rows = {}
        for vid, r in old_rows.items():
            lw, full = rows[f"orientation/{vid}/{tag}#historical_lwcc"], rows[f"orientation/{vid}/{tag}#full"]
            n = dict(r)
            for q in o9.Q_VALUES:
                n[f"lambda1_q{q:.2f}"], n[f"lambda2_q{q:.2f}"] = lam(lw, q, 1), lam(lw, q, 2)
            n["retrieval_lambda1_q0.25"], n["retrieval_lambda2_q0.25"] = lam(full, .25, 1), lam(full, .25, 2)
            n["normalized_q0_lambda2"] = lam(lw, 0.0, 2)
            n["normalized_q0_spectrum"] = [lam(lw, 0.0, k) for k in (1, 2, 3)]
            n["unnorm_lambda1_q0.25_recomputed"] = lam(lw, .25, 1, "unnormalized")
            n["unnorm_matches_09c"] = abs(n["unnorm_lambda1_q0.25_recomputed"] - r["lambda1_q0.25"]) <= 1e-9
            n["numerical_status"] = [lw["numerical_status"], full["numerical_status"]]
            new_rows[vid] = n
        tests = o9.frozen_tests(new_rows, i * 100)
        vals = list(new_rows.values())
        non_u = [r for r in vals if r["version_id"] != "U"]
        test_set = [r for r in vals if r["family"] == "random_half" or r["family"].startswith("partial_")]
        q0_range = max(max(r["normalized_q0_spectrum"][k] for r in non_u) - min(r["normalized_q0_spectrum"][k] for r in non_u)
                       for k in range(3))
        controls = {c: spearman([r["lambda1_q0.25"] for r in test_set], [r[c] for r in test_set]) for c in o9.CONTROLS}
        lam1 = [r["lambda1_q0.25"] for r in vals]
        fam = {"tests_normalized": tests, "tests_historical_unnormalized": old["families"][tag]["tests"],
               "table": [{k: r[k] for k in ("version_id", "family", "lambda1_q0.25", "lambda2_q0.25",
                                             "unnorm_lambda1_q0.25_recomputed", "normalized_q0_lambda2",
                                             "expansion_added_rate", "outdegree_gini", "sources", "sinks",
                                             "mean_seed_outdegree", "numerical_status")} for r in vals],
               "unnormalized_recomputation_matches_09c": all(r["unnorm_matches_09c"] for r in vals),
               "normalized_lambda1_q0.25_range_all_32": max(lam1) - min(lam1),
               "normalized_q0_spectrum_range_non_U": q0_range,
               "F_R_normalized_max_abs_difference": max(abs(new_rows["F"][k] - new_rows["R"][k]) for k in new_rows["F"]
                                                        if k.startswith("lambda") and new_rows["F"][k] is not None),
               "U": {k: new_rows["U"][k] for k in ("lambda1_q0.25", "lambda2_q0.25", "normalized_q0_lambda2")},
               "old_vs_new_spearman_lambda1_over_29": spearman([r["unnorm_lambda1_q0.25_recomputed"] for r in test_set],
                                                               [r["lambda1_q0.25"] for r in test_set]),
               "lambda1_vs_controls_over_29": controls,
               "rank_deficiency": {k: tests["incremental"][k]["joint"].get("control_rank") for k in o9.PRIMARY}}
        if tag == "main":
            fam["S3"] = {"pass": fam["normalized_lambda1_q0.25_range_all_32"] > 1e-6 and q0_range < 1e-9
                         and all(v is not None and abs(v) < 0.9 for v in controls.values()),
                         "range": fam["normalized_lambda1_q0.25_range_all_32"], "q0_range": q0_range, "controls": controls}
        out["families"][tag] = fam
    return out


def area_inputs(area_rows, view):
    """Join the area panel receipts into {area: {arm: {d, nulls, raw}}} for one view (normalized lambda1, q = .25)."""
    roles_by = {}
    for jid, r in area_rows.items():
        gid, v = jid.split("#")
        if v != view:
            continue
        _, arm, area, role = gid.split("/")
        roles_by.setdefault(area, {}).setdefault(arm, {})[role] = r
    built = {}
    for area, by_arm in roles_by.items():
        for arm, roles in by_arm.items():
            op, v = lam(roles["O-prime"], .25), lam(roles["v"], .25)
            nulls = [lam(roles[k], .25) for k in sorted(roles) if k.startswith("ln-")]
            built.setdefault(area, {})[arm] = {
                "d": v - op if v is not None and op is not None else None,
                "nulls": [x - op if x is not None and op is not None else None for x in nulls],
                "raw": {"O_prime": op, "v": v, "ln": nulls},
                "view_nodes": {k: rr["selection"]["nodes"] for k, rr in roles.items()}}
    return built


def analysis_c(wc_rows, area_rows):
    sys.path.insert(0, str(HERE))
    import wc_refinement_15 as wr
    graphs = sorted({r["graph_id"] for r in wc_rows.values()})
    prof = {}
    for gid in graphs:
        lw, full = wc_rows[f"{gid}#lwcc"], wc_rows[f"{gid}#full"]
        st, fs = lw["structure"], full["structure"]
        prof[gid] = {"lambda1_q0.25": lam(lw, .25), "lambda2_q0.25": lam(lw, .25, 2), "lambda3_q0.25": lam(lw, .25, 3),
                     "weighted_q0_lambda2": lw["weighted_normalized_lambda2_q0"],
                     "legacy_binary_skeleton_lambda2": lw["legacy_binary_skeleton_normalized_lambda2"],
                     "unnorm_lambda1_q0.25": lam(lw, .25, 1, "unnormalized"), "unnorm_lambda2_q0.25": lam(lw, .25, 2, "unnormalized"),
                     "full_lambda1_q0.25": lam(full, .25), "full_lambda2_q0.25": lam(full, .25, 2),
                     "full_zero_count_q0": full["normalized"]["0.00"]["numerical_zero_count"],
                     "full_zero_count_q0.25": full["normalized"]["0.25"]["numerical_zero_count"],
                     "nodes": fs["nodes"], "arcs": fs["arcs"], "lwcc_nodes": st["nodes"], "lwcc_arcs": st["arcs"],
                     "weak_components": fs["weak_components"], "isolates": fs["isolates"],
                     "reciprocal_arc_fraction": st["reciprocal_arc_fraction"], "largest_scc_fraction": st["largest_scc_fraction"],
                     "outdegree_gini": st["outdegree_gini"], "trophic_incoherence": lw["trophic_incoherence"]["value"],
                     "mean_weighted_degree": st["weighted_degree"]["mean"]}
        prof[gid]["q_sweep_lambda1"] = {f"{q:.2f}": lam(lw, q) for q in GRID}
        prof[gid]["identity"] = {"lwcc_view_sha256": lw["selection"]["view_graph_sha256"],
                                 "canonical_graph_sha256": lw["canonical_graph_sha256"],
                                 "numerical_status": [lw["numerical_status"], full["numerical_status"]]}
    keys = [k for k, v in prof["wc/O"].items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    arms, s4 = {}, {}
    for arm in wr.EDIT_ARMS:
        a, op = prof[f"wc/{arm}"], prof[f"wc/{arm}/O-prime"]
        rec = {}
        for k in keys:
            fams = {t: null_summary([prof[g][k] for g in graphs if g.startswith(f"wc/{arm}/{t.lower()}-")])
                    for t in ("LN", "PC", "RN")}
            rec[k] = {"arm": a[k], "O": prof["wc/O"][k], "O_prime": op[k],
                      "delta_vs_O_prime": a[k] - op[k] if a[k] is not None and op[k] is not None else None}
            for t, f in fams.items():
                rec[k][t] = dict(f, arm_outside_range=(a[k] < f["min"] or a[k] > f["max"]) if f.get("n") else None,
                                 null_mean_delta_vs_O_prime=(f["mean"] - op[k]) if f.get("n") and op[k] is not None else None)
        arms[arm] = rec
        s4[arm] = {k: rec[k]["LN"]["arm_outside_range"] for k in ("lambda1_q0.25", "lambda2_q0.25")}
    analogues = {}
    for view in ("lwcc_in_S", "fixed_S"):
        built = area_inputs(area_rows, view)
        primary = area_analogue(built, wr.sign_flip_test)
        sa1 = area_analogue(built, wr.sign_flip_test, drop={"A10", "A12", "A21"})
        analogues[view] = {"test": {k: v for k, v in primary.items() if k != "z"}, "z": primary["z"],
                           "SA1": {k: sa1.get(k) for k in ("n", "statistic_mean", "p", "minimum_p", "assignments", "differences")},
                           "rows": built}
    hist = read_json(EVIDENCE / "wc-spectra-18-v1-2026-10-19.json")
    o, s_ = dict(prof["wc/O"]), dict(prof["wc/S"])
    return {"profiles": prof, "arms_vs_nulls": arms,
            "O_equals_S": o == s_,
            "S4": {"pass": any(v for d in s4.values() for v in d.values()), "detail": s4},
            "normalized_exploratory_analogue": {"label": "normalized exploratory analogue of the Sprint 18 sign-flip test "
                                                         "(historical per-graph-LWCC-within-S rule); not a confirmation",
                                                **analogues["lwcc_in_S"]},
            "fixed_S_contrast": {"label": "fixed common S view, reported separately; not substituted into the inherited test",
                                 **analogues["fixed_S"]},
            "historical_sprint18": {"p": hist["primary_test"]["p"], "n": hist["primary_test"]["n"],
                                    "statistic_mean": hist["primary_test"]["statistic_mean"], "SA1_p": hist["SA1"]["p"],
                                    "SA1_n": hist["SA1"]["n"], "label": "unnormalized, pre-registered; unchanged"}}


def verdict(a, b, c, statuses):
    s1 = all(v == "qualified" for v in statuses.values())
    s2, s3, s4 = a["S2"]["pass"], b["families"]["main"]["S3"]["pass"], c["S4"]["pass"]
    if not s1:
        v = "partial"
    elif s2 and s3 and s4:
        v = "structural characterization supported"
    elif s2 and s3:
        v = "additional utility validation needed"
    else:
        v = "validity/limits contribution only"
    return {"S1": s1, "S2": s2, "S3": s3, "S4": s4, "verdict": v, "rule": "frozen in the plan (verdict_rule)"}


EVIDENCE_OUT = EVIDENCE / f"normalized-magnetic-22-v1-{PLAN_DATE}.json"


def analyze(_args):
    plan_value = read_json(PLAN)
    rows, recs = {}, {}
    for panel in PANELS:
        rows[panel], recs[panel] = load_rows(panel)
    statuses = {f"{panel}:{jid}": r["numerical_status"] for panel, rr in rows.items() for jid, r in rr.items()}
    failed = {k: v for k, v in statuses.items() if v != "qualified"}
    a = analysis_a(rows["anchors"])
    b = analysis_b(rows["orientation"])
    c = analysis_c(rows["wc"], rows["area"])
    out = {"schema_version": "docspectra.icsc.normalized_magnetic_22.v1", "created": PLAN_DATE, "model_calls": 0,
           "plan_sha256": sha(PLAN), "input_manifest_sha256": sha(MANIFEST), "coverage_ledger_sha256": sha(LEDGER),
           "driver_sha256": sha(Path(__file__)), "ra_commit": plan_value["ra"]["commit"],
           "operator_receipts": {p: {"receipt_sha256": r["receipt_sha256"], "job_count": r["job_count"],
                                     "numerical_status_counts": r["numerical_status_counts"],
                                     "table_sha256": r["outputs"]["graph_spectra.v1.jsonl"],
                                     "image": r["identity"]["image_reference"], "runtime": r["runtime"]}
                                 for p, r in recs.items()},
           "numerical": {"jobs": len(statuses), "qualified": len(statuses) - len(failed), "not_qualified": failed},
           "A_characterization": a, "B_orientation": b, "C_refinement": c, "verdict": verdict(a, b, c, statuses)}
    print(json.dumps({"evidence_sha256": write_json(EVIDENCE_OUT, out), "verdict": out["verdict"],
                      "numerical": {k: v for k, v in out["numerical"].items() if k != "not_qualified"}}, indent=1))


def check(_args):
    """Historical sources and evidence byte-identical to the plan; plan, evidence and figure data hashes."""
    plan_value = read_json(PLAN)
    changed = {p: h for p, h in plan_value["historical_inputs_sha256"].items() if sha(ROOT / p) != h}
    out = {"historical_unchanged": not changed, "changed": changed, "plan_sha256": sha(PLAN),
           "evidence_sha256": sha(EVIDENCE_OUT) if EVIDENCE_OUT.exists() else None,
           "figures": {p.name: sha(p) for p in sorted((OUT / "figures").glob("*")) if p.is_file()}}
    print(json.dumps(out, indent=1))
    if changed:
        raise SystemExit(1)


def figures(_args):
    receipt = read_json(RECEIPT)
    out = OUT / "figures"
    out.mkdir(parents=True, exist_ok=True)
    cmd = ["docker", "run", "--rm", "--network", "none", "--read-only", "--entrypoint", "python",
           "-e", "MPLCONFIGDIR=/tmp", "--tmpfs", "/tmp", "--user", "65532:65532",
           "-v", f"{CONTEXT}:/ctx:ro", "-v", f"{out}:/out", receipt["image"]["reference"], "-I",
           "/ctx/scripts/normalized_magnetic_22_figures.py", f"/ctx/evidence/{EVIDENCE_OUT.name}", "/out"]
    subprocess.run(cmd, check=True)
    write_json(out / "figures-receipt.json", {"image": receipt["image"], "script_sha256": sha(HERE / "normalized_magnetic_22_figures.py"),
                                              "evidence_sha256": sha(EVIDENCE_OUT)})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="stage", required=True)
    sub.add_parser("plan").set_defaults(fn=plan)
    p = sub.add_parser("bundle")
    p.add_argument("--panels", nargs="*", choices=PANELS)
    p.set_defaults(fn=bundle)
    for name, fn in (("request", request), ("measure", measure)):
        p = sub.add_parser(name)
        p.add_argument("--panels", nargs="*", choices=PANELS)
        p.set_defaults(fn=fn)
    sub.add_parser("benchmark").set_defaults(fn=benchmark)
    sub.add_parser("analyze").set_defaults(fn=analyze)
    sub.add_parser("figures").set_defaults(fn=figures)
    sub.add_parser("check").set_defaults(fn=check)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

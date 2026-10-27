"""Sprint 18: nulls, spectra, the pre-registered sign-flip test and descriptives on the Sprint 17 final trees.

Zero model calls. Thin driver over scripts/wc_refinement_15.py (unchanged) and scripts/wc_refine_17.py (the run's
ledger replay and fixes). The analysis is the Sprint 15 fixture's analysis half run on the live trees:

  nulls      LN, PC and RN, 5 seeds each, built per area bucket from that arm's edits and composed on its whole tree
             (wc_refinement_15.compose_null); a bucket the frozen rule cannot compute is recorded, never widened silently.
  spectra    the pinned profile plus flow imbalance (wc_refinement_15.spectral_receipt) on O, S, each arm, its O' and
             every null; O must equal the 12b O profile and the 09c F reference; S must equal O.
  test       per area: z of magnetic_lambda1_q0.25 against LN on the per-area subgraph view (pooled null SD), LT minus
             R0, exact two-sided sign-flip over areas with a computable difference (pre-registration a04c70c5).
  SA1        the same test without LT A10, A12 and A21 (the areas whose admission came from a rerun).

Stages: `run` (idempotent; every graph and receipt is cached under the run root) and `check`.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wc_refine_17 as w17  # noqa: E402  (registers the Sprint 17 fixes and the effective ledger)

wr, wf, rr, sfb, sf = w17.wr, w17.wf, w17.rr, w17.sfb, w17.wr.sf
sha, write_json, Refused = wr.sha, wr.write_json, wr.Refused
ROOT, EVIDENCE = rr.ROOT, w17.EVIDENCE
DATE = "2026-10-19"
RUN_ROOT = w17.RUN_ROOT
OUT = RUN_ROOT / "analysis-18"
SPECTRA = EVIDENCE / f"wc-spectra-18-v1-{DATE}.json"
MEASURE = "magnetic_lambda1_q0.25"
SA1_DROP = {"LT": ("A10", "A12", "A21")}


def gates():
    pre = w17.prereg_check()
    plan = wr.load_run_plan(w17.REFINE_PLAN)
    records = wf.check_lock(w17.RECORDS)
    return pre, plan, records


def run(args):
    t_start = time.time()
    pre, plan, records = gates()
    if SPECTRA.exists():
        raise Refused("spectra-evidence-already-written")
    rows = w17.effective(w17.ledger_rows())
    vm = rr.read_json(wr.V6)
    gm = wr.dcv.pinned(vm)
    areas = plan["areas"]
    finals = {arm: records["arms"][arm]["final_tree_path"] for arm in wr.EDIT_ARMS}
    for arm, path in finals.items():
        if wr.tree_id(path) != records["arms"][arm]["final_tree_id"]:
            raise Refused(f"final-tree-differs-from-records:{arm}")
    baseline_tree = RUN_ROOT / "O-r01" / "editor" / "source"
    sham_tree = RUN_ROOT / "S-r01" / "editor" / "source"
    vctx = {arm: wr.variant_context(gm, vm, finals[arm], ledger_rows=rows, arm=arm) for arm in wr.EDIT_ARMS}
    o_ctx = wr.variant_context(gm, vm, baseline_tree)
    s_ctx = wr.variant_context(gm, vm, sham_tree)
    stage = {"contexts": round(time.time() - t_start, 1)}

    # ---- nulls
    t1 = time.time()
    controls, null_graphs = {}, {}
    for arm in wr.EDIT_ARMS:
        buckets, unattributed = wr.area_buckets(vctx[arm])
        nodes_by_area = wr.area_node_sets(vctx[arm], areas)
        controls[arm] = {"buckets": {b: {k: (v if k != "added_edges" else len(v)) for k, v in x.items() if k != "T"}
                                     | {"T": len(x["T"])} for b, x in buckets.items()},
                         "unattributed": {k: len(v) for k, v in unattributed.items()}, "nulls": {}}
        for t in ("LN", "PC", "RN"):
            for k in range(1, wr.NULLS_PER_TYPE + 1):
                gid = f"{arm}/{t.lower()}-{k}"
                path = OUT / "controls" / f"{arm}__{t.lower()}-{k}.json"
                if not path.exists():
                    try:
                        edges, rec = wr.compose_null(t, vctx[arm], buckets, f"{wr_run_id()}/{arm}", k, nodes_by_area)
                    except Refused as error:
                        controls[arm]["nulls"][gid] = {"not_computable": str(error)}
                        print(f"null not computable {gid}: {error}", flush=True)
                        continue
                    write_json(path, {"graph_id": gid, "receipt": rec, "nodes": vctx[arm]["nodes"], "edges": edges})
                g = rr.read_json(path)
                null_graphs[gid] = [tuple(e) for e in g["edges"]]
                controls[arm]["nulls"][gid] = {k2: g["receipt"][k2] for k2 in (
                    "added_total", "removed_total", "planned_added", "planned_removed", "added_existing_edges")}
                if t == "PC":
                    pcs = {b: x["pc"] for b, x in g["receipt"]["buckets"].items() if x["pc"]}
                    controls[arm]["nulls"][gid]["pc_new_pages"] = sum(x["new_pages"] for x in pcs.values())
                    controls[arm]["nulls"][gid]["pc_inbound_placed"] = sum(x["placed"] for x in pcs.values())
                print(f"null {gid} ready", flush=True)
        added = set(vctx[arm]["receipt"]["added"])
        controls[arm]["arm_new_pages"] = len(added)
        controls[arm]["arm_new_page_inbound"] = sum(1 for s, t in vctx[arm]["v_edges"] if t in added)
    stage["nulls"] = round(time.time() - t1, 1)

    # ---- spectra, full view
    t1 = time.time()
    manifest = rr.read_json(rr.read_json(wf.SURVEY_PLAN)["manifests"][wf.SURVEY_KEY]["path"])
    anchors, pin = manifest["c_pin"]["anchors"], manifest["spectral_pin"]

    def receipt(gid, nodes, edges, mapping=None):
        path = OUT / "spectra" / "full" / f"{gid.replace('/', '__')}.json"
        if not path.exists():
            write_json(path, wr.spectral_receipt(gm, nodes, edges, anchors, mapping, pin))
            print(f"spectrum {gid} ready", flush=True)
        return rr.read_json(path)

    o_rec = receipt("O", o_ctx["nodes"], o_ctx["v_edges"])
    s_rec = receipt("S", s_ctx["nodes"], s_ctx["v_edges"])
    ref_12b = rr.read_json(sfb.sf_run_root(sfb.RUN_ID) / "O-r01/host/receipts/spectral-profile.json")
    controls_check = {"O_profile_sha256": o_rec["profile_sha256"],
                      "O_equals_12b_O": o_rec["profile_sha256"] == ref_12b["profile_sha256"],
                      "O_f_reference": rr.f_reference_check(o_rec),
                      "S_tree_equals_O_tree": wr.tree_id(sham_tree) == wr.tree_id(baseline_tree),
                      "S_profile_equals_O": s_rec["profile_sha256"] == o_rec["profile_sha256"],
                      "flow_imbalance_O": o_rec["flow_imbalance"]["value"]}

    def summary(r):
        lw = r["profile"]["retrieval_no_faq_lwcc"]
        return {"lambda1_q0.25": lw["spectra"]["0.25"]["lambda1"], "normalized_lambda2": lw["normalized_undirected_lambda2"],
                "flow_imbalance": r["flow_imbalance"]["value"], "lwcc_nodes": r["flow_imbalance"]["lwcc_nodes"],
                "q_sweep_lambda1": {q: s["lambda1"] for q, s in lw["spectra"].items() if isinstance(s, dict) and "lambda1" in s}}

    full = {"O": summary(o_rec), "S": summary(s_rec)}
    for arm in wr.EDIT_ARMS:
        v = vctx[arm]
        mapping = v["receipt"]["mapping"]
        recs = {arm: receipt(arm, v["nodes"], v["v_edges"], mapping),
                f"{arm}/O-prime": receipt(f"{arm}/O-prime", v["nodes"], v["o_mapped"], mapping)}
        for gid, edges in null_graphs.items():
            if gid.startswith(arm + "/"):
                recs[gid] = receipt(gid, v["nodes"], edges, mapping)
        full[arm] = {g: summary(r) for g, r in recs.items()}
    stage["full_spectra"] = round(time.time() - t1, 1)

    # ---- per-area subgraph view and the pre-registered statistic
    t1 = time.time()
    o9, mp = rr.load_spectral()
    per_area = {}
    for arm in wr.EDIT_ARMS:
        v = vctx[arm]
        om = v["o_mapped"]
        buckets, _ = wr.area_buckets(v)
        removed_by_bucket = bucket_removed_edges(v)
        nodes_by_area = wr.area_node_sets(v, areas)
        for b, x in sorted(buckets.items()):
            if b not in areas:
                continue
            path = OUT / "subgraph" / f"{arm}__{b}.json"
            if not path.exists():
                # F6: the area's own edits only (v_b) and the area's own LN components (same seeds as the composites)
                v_b = sorted((set(om) - set(removed_by_bucket.get(b, []))) | set(map(tuple, x["added_edges"])))
                lns_b = []
                for k in range(1, wr.NULLS_PER_TYPE + 1):
                    edges = bucket_ln(v, x, b, k, f"{wr_run_id()}/{arm}", nodes_by_area)
                    if edges is not None:
                        lns_b.append((f"{arm}/ln-{k}/{b}", edges))
                S = sf.subgraph_nodes_v2(x["T"], om, v_b, x["added_pages"], [e for _, e in lns_b])
                rows_sub = wr.subgraph_rows(o9, mp, S, [("O-prime", om), ("v", v_b)] + lns_b, om)
                write_json(path, {"S": len(S), "edits_added": len(x["added_edges"]),
                                  "edits_removed": len(removed_by_bucket.get(b, [])), "nulls_built": len(lns_b),
                                  "d": sf.delta(rows_sub["v"], rows_sub["O-prime"]),
                                  "nulls": [sf.delta(rows_sub[g], rows_sub["O-prime"]) for g, _ in lns_b],
                                  "rows": rows_sub})
                print(f"subgraph {arm} {b} ready (|S| = {len(S)})", flush=True)
            r = rr.read_json(path)
            per_area.setdefault(b, {})[arm] = {"S": r["S"], "d": r["d"], "nulls": r["nulls"]}
    zrows = [r for a in per_area.values() for r in a.values()]
    pooled = sf.pooled_null_sd([r["nulls"] for r in zrows]) if zrows else {}
    diffs, not_computable = {}, {}
    for a, arms in sorted(per_area.items()):
        for arm, r in arms.items():
            r["z"], _ = sf.standardize(r["d"], r["nulls"], pooled)
        if all(x in arms for x in wr.EDIT_ARMS):
            zl, zr = arms["LT"]["z"].get(MEASURE), arms["R0"]["z"].get(MEASURE)
            if None in (zl, zr):
                not_computable[a] = {"LT": zl, "R0": zr}
            else:
                diffs[a] = zl - zr
    test = wr.sign_flip_test(list(diffs.values()))
    sa1_diffs = {a: d for a, d in diffs.items() if a not in SA1_DROP["LT"]}
    sa1 = wr.sign_flip_test(list(sa1_diffs.values()))
    only_one_arm = {a: sorted(arms) for a, arms in per_area.items() if not all(x in arms for x in wr.EDIT_ARMS)}
    stage["subgraph"] = round(time.time() - t1, 1)

    # ---- descriptives: each arm against each null type, full view, by measure; per-area tallies
    def null_summary(arm, ntype, key):
        vals = [full[arm][g][key] for g in full[arm] if g.startswith(f"{arm}/{ntype.lower()}-")]
        return {"n": len(vals), "min": min(vals) if vals else None, "max": max(vals) if vals else None,
                "mean": sum(vals) / len(vals) if vals else None}

    descriptives = {}
    for arm in wr.EDIT_ARMS:
        descriptives[arm] = {}
        for key in ("lambda1_q0.25", "normalized_lambda2", "flow_imbalance"):
            descriptives[arm][key] = {"arm": full[arm][arm][key], "O": full["O"][key],
                                      "O_prime": full[arm][f"{arm}/O-prime"][key],
                                      **{t: null_summary(arm, t, key) for t in ("LN", "PC", "RN")}}
    tallies = {}
    for arm in wr.EDIT_ARMS:
        zs = {a: arms[arm]["z"] for a, arms in per_area.items() if arm in arms}
        tallies[arm] = {m: {"areas": sum(1 for z in zs.values() if z.get(m) is not None),
                            "positive": sum(1 for z in zs.values() if (z.get(m) or 0) > 0),
                            "negative": sum(1 for z in zs.values() if (z.get(m) or 0) < 0)} for m in sf.MEASURES}

    out = {"schema_version": "docspectra.icsc.wc_spectra_18.v1", "created": DATE, "model_calls": 0,
           "preregistration": pre, "refine_plan_sha256": sha(w17.REFINE_PLAN), "records_sha256": sha(w17.RECORDS),
           "code_sha256": {"wc_spectra_18": sha(Path(__file__)), "wc_refine_17": sha(HERE / "wc_refine_17.py"),
                           "wc_refinement_15": sha(HERE / "wc_refinement_15.py")},
           "final_trees": {arm: records["arms"][arm]["final_tree_id"] for arm in wr.EDIT_ARMS},
           "controls_check": controls_check, "controls": controls,
           "null_count_per_type": {arm: {t: sum(1 for g in null_graphs if g.startswith(f"{arm}/{t.lower()}-"))
                                         for t in ("LN", "PC", "RN")} for arm in wr.EDIT_ARMS},
           "full_view": full, "descriptives": descriptives,
           "per_area_subgraph": {a: {arm: {"S": r["S"], "d": r["d"], "z": r["z"]} for arm, r in arms.items()}
                                 for a, arms in per_area.items()},
           "pooled_null_sd": pooled, "z_tallies": tallies,
           "primary_test": {"measure": MEASURE, "view": "per-area subgraph, built from the area's own edits (F6)", "null": "LN",
                            "f6": "the frozen helper gave every area the whole arm's edit subgraph (LT: 1,585 nodes and the "
                                  "same delta in every area); evidence kept in analysis-18/subgraph-frozen-literal-defect", "sidedness": "two-sided",
                            "threshold": 0.05, "areas_in_test": sorted(diffs), "differences": diffs,
                            "areas_one_arm_only": only_one_arm, "not_computable": not_computable, **test,
                            "dependence_disclosure": "areas are chained on one tree and share trunk pages; "
                                                     "pre-registered within an exploratory run"},
           "SA1": {"drops": SA1_DROP, "areas_in_test": sorted(sa1_diffs), **sa1,
                   "note": "the frozen test already drops areas edited by one arm only, so SA4 (paired areas) "
                           "equals the primary test"},
           "stage_seconds": stage, "wall_seconds": round(time.time() - t_start, 1)}
    write_json(SPECTRA, out)
    wf.lock(SPECTRA)
    print(json.dumps({"spectra_sha256": sha(SPECTRA), "controls_check": controls_check,
                      "null_count_per_type": out["null_count_per_type"], "primary_test":
                      {k: out["primary_test"][k] for k in ("n", "statistic_mean", "p", "minimum_p", "areas_in_test")},
                      "SA1": {k: out["SA1"][k] for k in ("n", "statistic_mean", "p", "minimum_p")},
                      "full_lambda2": {a: (full[a][a]["normalized_lambda2"] if a in wr.EDIT_ARMS else full[a]["normalized_lambda2"])
                                       for a in ("O", "LT", "R0")},
                      "stage_seconds": stage}, indent=1, default=str))


def wr_run_id():
    return w17.RUN_ID


# F6 (found October 19): sf.subgraph_nodes_v2 takes every edit endpoint of the variant and of its nulls. In Sprints
# 12/13 a variant held one area's edits, so that set was local; on a whole-corpus tree it is the whole arm's edit set,
# so every area got the same subgraph (LT: 1,585 nodes, the same delta in every area) and the sign-flip test would see
# identical differences. The per-area view is therefore built from the area's own edits: v_b = O' with only bucket b's
# added and removed edges, and the LN nulls' bucket-b components, generated exactly as compose_null generates them
# (same seed, same locality region), before they are combined on the whole tree.
def bucket_removed_edges(vctx):
    """Bucket -> the removed edges attributed to it (same attribution rule as wr.area_buckets)."""
    area_of, touched = vctx["edit_area"], set(vctx["touched"])
    bucket_of = {t: area_of.get(t, "unattributed") for t in touched}
    out = {}
    for s, t in sorted(set(vctx["o_mapped"]) - set(vctx["v_edges"])):
        b = bucket_of.get(s) or bucket_of.get(t)
        if b is not None:
            out.setdefault(b, []).append((s, t))
    return out


def bucket_ln(vctx, x, b, k, variant_id, nodes_by_area):
    """The LN null's component for one bucket: the full edge list over O' with only this bucket's null edits."""
    T = x["T"]
    if not T or (x["a"] == 0 and x["r"] == 0):
        return None
    om, nodes, v_edges = vctx["o_mapped"], vctx["nodes"], vctx["v_edges"]
    seed = wr.null_seed("LN", variant_id, b, k)
    K = wr.ln.locality_region(T, om, v_edges, x["added_pages"])
    try:
        edges, _, _ = wr._bucket_null("LN", om, nodes, v_edges, x, T, K, x["a"], x["r"], x["f"], seed,
                                      nodes_by_area.get(b, nodes))
    except Refused:
        return None
    return [tuple(e) for e in edges]


def check(_args):
    gates()
    if SPECTRA.exists():
        wf.check_lock(SPECTRA)
    print(json.dumps({"gates": "ok", "spectra": "ok" if SPECTRA.exists() else "not yet written"}))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="stage", required=True)
    sub.add_parser("run").set_defaults(fn=run)
    sub.add_parser("check").set_defaults(fn=check)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

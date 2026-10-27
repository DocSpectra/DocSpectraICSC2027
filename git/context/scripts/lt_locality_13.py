"""Sprint 13: the LT full-view finding of Sprint 12b against a locality-matched null (zero model calls).

The 12b random-edit null (RN) draws a random partner for each touched page, so LT's nulls put only 0-6 of their added
edges in the v1 core while every LT edge lies inside it. The locality-matched null (LN) is RN with its candidate node
universe restricted to the endpoint's own locality region K_v (the v1 core: touched set, its 1-hop neighbours in O',
the v-neighbours of added pages). sf.random_null is called unchanged with nodes = K_v, so the removal pools, the
"at least one endpoint in T" addition pool, the single widening (to T + 1-hop, itself inside K_v), the counts and the
reciprocal rule are exactly RN's; only locality changes.

Stages (each refuses to run twice; every stage after freeze refuses on a changed pre-registration, code or input):
  freeze    the pre-registration (rule, seeds, statistic, threshold, descriptives, outcome table, input hashes) + lock
  nulls     5 LN graphs per admitted 12b endpoint into artifacts/sf-13-ln/controls/
  spectra   full-view receipts (pinned 09c spectral_profile) for every LN graph, subgraph measures for O', v and the LN
            nulls on S_LN, and one determinism check against a 12b receipt; the 12b receipts are read, never written
  analyse   the one confirmatory test (area-stratified exact permutation, LT vs the rest, full view against LN) and the
            pre-registered descriptives into evidence/lt-locality-13-results-v1-<date>.{json,md}

structure_forcing_12 and structure_forcing_12b are imported as libraries and never edited.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")  # as restructuring_runs and 09c

import argparse
import hashlib
import itertools
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import structure_forcing_12 as sf  # noqa: E402
import structure_forcing_12b as sfb  # noqa: E402

rr, dcv = sf.rr, sf.dcv
Refused = sf.Refused
sha, sha_text, write_json = sf.sha, sf.sha_text, sf.write_json

DATE = "2026-10-09"
SOURCE_RUN = sfb.RUN_ID                      # sf-12-v2, read only
EVIDENCE = sf.EVIDENCE
PREREG = EVIDENCE / f"lt-locality-13-preregistration-v1-{DATE}.json"
RESULTS_GLOB = "lt-locality-13-results-v1-*.json"
OUT = rr.ROOT / "artifacts/sf-13-ln"
CODE = Path(__file__).resolve()
INPUTS = {  # name -> path; their sha256 is frozen in the pre-registration
    "plan_12b_v3": sfb.PLAN,
    "admission_12b": EVIDENCE / "structure-forcing-12b-admission-v1-2026-10-09.json",
    "spectra_12b": EVIDENCE / "structure-forcing-12b-spectra-v1-2026-10-09.json",
    "driver_sf12": Path(sf.__file__).resolve(),
    "driver_sf12b": Path(sfb.__file__).resolve(),
    "code_sprint13": CODE,
}

LN_SALT = "icsc2027-13-ln:"
NULLS_PER_VARIANT = sf.NULLS_PER_VARIANT     # 5, as RN
ALPHA = 0.05
GROUP = "LT"
AREAS = sfb.AREAS
# The RN full-view arm-mean z signs that motivated the sprint (12b report, section 7.2); the outcome table asks whether
# LT keeps all five against LN.
DIRECTION_MEASURES = {"magnetic_lambda1_q0.25": +1, "magnetic_lambda2_q0.25": +1, "outdegree_gini": -1,
                      "sources": -1, "sinks": -1}

LN_RULE = (
    "Locality-matched null sf13_locality_null_v1. For each admitted 12b endpoint v: K_v = sf.subgraph_nodes(T, O', "
    "E_v, added) = T, its 1-hop neighbours in O', and the neighbours in v of pages v added. LN = sf.random_null("
    "nodes=K_v, o_mapped=O', touched=T, a, r, f, seed) with a, r, f from sf.edit_magnitude(O', E_v, T) on v's full "
    "node set: remove r edges among O' edges with both endpoints in T (widened once to W = T + 1-hop, W inside K_v); "
    "add a edges among ordered non-edges with both endpoints in K_v and at least one in T (widened once to at least one "
    "in W, still inside K_v); round(f*a) reciprocal via existing reverses, shortfall by mutual pairs. The graph keeps "
    "v's full node set and every O' edge not removed. A pool too small after the one widening refuses (no fallback): "
    "the primary is then reported as not computable.")
SEED_RULE = f"int(sha256('{LN_SALT}' + variant_id + ':' + k)), k = 1..{NULLS_PER_VARIANT}"
STATISTIC = (
    "Full view (pinned 09c spectral_profile on the LWCC, as 12b). d = vector(graph) - vector(O') per measure, for v and "
    "its 5 LN nulls; z = (d_v - mean LN null d) / pooled within-variant LN null SD (sf.standardize, sf.pooled_null_sd "
    "over the 12 endpoints' LN nulls). Measures: the 8 of 12b; a measure with any undefined z is dropped (recorded). "
    "Statistic: sf.between_arm_ss(Z, labels) with labels LT / other (n_LT * ||mean_LT - grand||^2 + n_other * "
    "||mean_other - grand||^2). Reference: every assignment with exactly one LT label per area (A1..A4, 3 endpoints "
    "each): 3^4 = 81, enumerated. p = #{assignments with statistic >= observed - 1e-12} / 81 (the observed included); "
    "minimum 1/81. Threshold p < .05.")
DESCRIPTIVES = [
    "D1: the same stratified statistic on the full view against RN, from the 12b spectra evidence z unchanged "
    "(separates stratification from locality)",
    "D2: the stratified statistic on the subgraph view against LN (S_LN = K_v plus every edit endpoint of v and its "
    "LN nulls, the plan v2 rule) and against RN (12b subgraph z)",
    "D3: the 12b-form statistic against LN on the full view (3 arms, unstratified, sf.permutation_p, 10,000 "
    "permutations, the 12b seed)",
    "D4: arm-mean z per measure on both views against LN, beside the RN values",
    "D5: signature tallies against LN on both views (sf.signature_tally)",
    "D6: locality of the LN additions (inside K_v, must be 100%), share with source in T, beside RN's inside-core "
    "counts; the widening count",
    "D7: determinism: one recomputed O' full receipt equals 12b's profile_sha256",
]
OUTCOME_TABLE = [
    {"row": "R1", "when": "p_LN < .05 and LT's LN arm-mean full-view z keeps the RN sign on all five direction measures",
     "statement": "LT's full-view signature is not explained by locality: it separates LT from MT and MR within area "
                  "against local random links of the same size; page creation remains confounded"},
    {"row": "R2", "when": "p_LN < .05 and at least one of the five direction measures changes sign",
     "statement": "LT differs from MT and MR beyond locality, but not with the RN signature; report the LN pattern "
                  "instead of the RN one"},
    {"row": "R3", "when": "p_LN >= .05 and stratified p_RN (D1) < .05",
     "statement": "the 12b full-view separation is consistent with locality: against local random links LT is not "
                  "distinguishable from MT and MR within area at this scale"},
    {"row": "R4", "when": "p_LN >= .05 and stratified p_RN (D1) >= .05",
     "statement": "the 12b full-view separation does not survive area stratification even against RN; the descriptive "
                  "is fragile, independent of locality"},
    {"row": "R0", "when": "an LN pool refuses after its one widening, or any full-view z is undefined on every measure",
     "statement": "not computable; report the refusal"},
]
DISCLOSURES = [
    "The design is informed by the 12b RN results; testing them against a locality-matched null is its purpose.",
    "Kind is not matched: LT creates 3-22 pages, MT and MR none; a null rewires existing pages only.",
    "Direction of added links is not matched: every editor edge has its source in T (links live in the edited page), "
    "while RN and LN also allow edges from outside T into T. The share is reported (D6).",
    "K_v is partly defined by v's own edges (neighbours of added pages), so it is tailored to v; this makes LN closer "
    "to v and the test conservative against an LT effect.",
    "The RN full-view z vectors are already published; the stratified RN value (D1) was not computed before freezing.",
]


# ------------------------------------------------------------------ rules (pure; tested offline)

def ln_seed(variant_id: str, k: int) -> int:
    return int(hashlib.sha256(f"{LN_SALT}{variant_id}:{k}".encode()).hexdigest(), 16)


def locality_region(touched, o_mapped, v_edges, added):
    """K_v, the v1 core: the region every 12b editor edge occupies."""
    return sf.subgraph_nodes(touched, o_mapped, v_edges, added)


def locality_null(o_mapped, touched, region, a, r, f, seed):
    """sf.random_null with its node universe restricted to K_v; refuses unless every added edge lies inside K_v."""
    region = sorted(region)
    rset, tset = set(region), set(touched)
    if not tset <= rset:
        raise Refused("touched-set-outside-locality-region")
    edges, rec = sf.random_null(region, o_mapped, sorted(tset), a, r, f, seed)
    o = set(o_mapped)
    added = sorted(set(edges) - o)
    removed = sorted(o - set(edges))
    inside = sum(1 for s, t in added if s in rset and t in rset)
    if inside != len(added):
        raise Refused(f"ln-addition-outside-region:{len(added) - inside}")
    rec = dict(rec, rule="sf13_locality_null_v1", region_size=len(region),
               locality={"added": len(added), "added_inside_region": inside,
                         "added_source_in_T": sum(1 for s, _ in added if s in tset),
                         "removed": len(removed), "removed_inside_T": sum(1 for s, t in removed if s in tset and t in tset)})
    return edges, rec


def stratified_assignments(strata, group_counts):
    """Every labelling that puts group_counts[s] group labels inside stratum s (rows given as strata[i])."""
    rows = {}
    for i, s in enumerate(strata):
        rows.setdefault(s, []).append(i)
    keys = sorted(rows)
    choices = [list(itertools.combinations(rows[s], group_counts[s])) for s in keys]
    for pick in itertools.product(*choices):
        chosen = {i for c in pick for i in c}
        yield tuple(i in chosen for i in range(len(strata)))


def stratified_exact_p(Z, labels, strata, group=GROUP):
    """Exact p of the between-group SS (group vs the rest) over all within-stratum relabellings."""
    labels = list(labels)
    counts = {}
    for lab, s in zip(labels, strata):
        counts[s] = counts.get(s, 0) + (lab == group)
    obs_lab = [group if lab == group else "other" for lab in labels]
    observed = sf.between_arm_ss(Z, obs_lab)
    stats = [sf.between_arm_ss(Z, [group if g else "other" for g in mask])
             for mask in stratified_assignments(strata, counts)]
    hits = sum(x >= observed - 1e-12 for x in stats)
    return {"statistic": observed, "p": hits / len(stats), "assignments": len(stats), "at_least_observed": hits,
            "minimum_p": 1 / len(stats), "group_per_stratum": counts}


def stratified_test(rows, area_of, group=GROUP):
    """rows: [{"variant_id", "arm", "z"}]; drops measures with any undefined z (recorded), as sf.primary_statistic."""
    used = [m for m in sf.MEASURES if all(r["z"].get(m) is not None for r in rows)]
    dropped = [m for m in sf.MEASURES if m not in used]
    if not used:
        return {"measures_used": [], "measures_dropped": dropped, "statistic": None, "p": None}
    Z = [[r["z"][m] for m in used] for r in rows]
    res = stratified_exact_p(Z, [r["arm"] for r in rows], [area_of[r["variant_id"]] for r in rows], group)
    return dict(res, measures_used=used, measures_dropped=dropped, endpoints=len(rows),
                rejects=res["p"] < ALPHA, threshold=ALPHA)


def arm_means(rows):
    out = {}
    for arm in sf.STRUCTURE_ARMS:
        mine = [r for r in rows if r["arm"] == arm]
        out[arm] = {m: (sum(r["z"][m] for r in mine) / len(mine)
                        if mine and all(r["z"].get(m) is not None for r in mine) else None) for m in sf.MEASURES}
    return out


def outcome_row(p_ln, p_rn, lt_ln_means):
    """The pre-registered outcome table row."""
    if p_ln is None:
        return "R0"
    if p_ln < ALPHA:
        kept = all(lt_ln_means.get(m) is not None and lt_ln_means[m] * s > 0 for m, s in DIRECTION_MEASURES.items())
        return "R1" if kept else "R2"
    return "R3" if p_rn is not None and p_rn < ALPHA else "R4"


# ------------------------------------------------------------------ pre-registration and the hash gate

def prereg_payload(inputs=INPUTS):
    admission = rr.read_json(inputs["admission_12b"])
    area_of = {n: admission["edit_magnitude"][n]["area"] for s, n in admission["slots"].items() if s not in ("O", "S")}
    return {"schema_version": "docspectra.icsc.lt_locality_13.v1.preregistration", "created": DATE, "sprint": "13",
            "question": "Is LT's 12b full-view signature leaf-and-trunk structure, or locality?",
            "source_run": SOURCE_RUN, "output_root": "artifacts/sf-13-ln", "model_calls": 0,
            "endpoints": {"area_of": area_of, "group": GROUP, "strata": list(AREAS)},
            "ln_rule": LN_RULE, "seeds": {"salt": LN_SALT, "rule": SEED_RULE, "nulls_per_variant": NULLS_PER_VARIANT},
            "primary": {"view": "full", "null": "LN", "statistic": STATISTIC, "threshold": ALPHA,
                        "assignments": 3 ** len(AREAS), "minimum_p": 1 / 3 ** len(AREAS)},
            "direction_measures_rn_sign": DIRECTION_MEASURES, "descriptives": DESCRIPTIVES,
            "outcome_table": OUTCOME_TABLE, "disclosures": DISCLOSURES,
            "hashes": {k: {"path": str(Path(p).resolve().relative_to(rr.ROOT)).replace("\\", "/"), "sha256": sha(p)}
                       for k, p in inputs.items()}}


def check_prereg(prereg=PREREG, root=rr.ROOT):
    """Refuses unless the lock matches the pre-registration and every frozen input still has its frozen hash."""
    prereg = Path(prereg)
    lock = prereg.with_suffix(".sha256")
    if not prereg.exists() or not lock.exists():
        raise Refused("preregistration-not-frozen")
    if sha(prereg) != lock.read_text(encoding="ascii").split()[0]:
        raise Refused("preregistration-hash-changed-since-freeze")
    data = rr.read_json(prereg)
    for key, h in data["hashes"].items():
        if sha(Path(root) / h["path"]) != h["sha256"]:
            raise Refused(f"frozen-input-changed:{key}")
    return data


def freeze(_args):
    if PREREG.exists():
        raise Refused("already-preregistered")
    if OUT.exists():
        raise Refused("ln-output-exists-before-preregistration")
    digest = write_json(PREREG, prereg_payload())
    PREREG.with_suffix(".sha256").write_text(f"{digest}  {PREREG.name}\n", encoding="ascii", newline="\n")
    print(json.dumps({"preregistration_sha256": digest}, indent=1))


# ------------------------------------------------------------------ stages

def admission():
    return rr.read_json(INPUTS["admission_12b"])


def endpoints_of(adm):
    return sorted(n for s, n in adm["slots"].items() if s not in ("O", "S"))


def source_root():
    return sfb.sf_run_root(SOURCE_RUN)


def env():
    vm = rr.read_json(sf.V6)
    return dcv.pinned(vm), vm


def nulls(_args):
    check_prereg()
    marker = OUT / "controls/complete.json"
    if marker.exists():
        raise Refused("ln-nulls-already-generated")
    gm, vm = env()
    src = source_root()
    summary = {}
    for name in endpoints_of(admission()):
        om, nodes, ve, receipt, T = sfb.namespace_graphs(gm, vm, src / name)
        mag = sf.edit_magnitude(om, ve, T)
        K = locality_region(T, om, ve, receipt["added"])
        rows = []
        for k in range(1, NULLS_PER_VARIANT + 1):
            path = OUT / "controls" / name / f"ln-{k}.json"
            if not path.exists():
                edges, rec = locality_null(om, T, K, mag["edges_added"], mag["edges_removed"],
                                           mag["reciprocal_fraction_added"], ln_seed(name, k))
                write_json(path, {"graph_id": f"{name}/ln-{k}", "variant_id": name, "k": k,
                                  "rule": "sf13_locality_null_v1", "receipt": rec, "region": K,
                                  "nodes": nodes, "edges": edges})
            rec = rr.read_json(path)["receipt"]
            rows.append({"k": k, "widened": rec["widened"], "locality": rec["locality"],
                         "reciprocal": rec["reciprocal"], "result": rec["result"]})
            print(f"{name}/ln-{k} widened={rec['widened']} locality={rec['locality']}", flush=True)
        summary[name] = {"region_size": len(K), "touched": len(T), "a": mag["edges_added"], "r": mag["edges_removed"],
                         "f": mag["reciprocal_fraction_added"], "nulls": rows}
    out = {"nulls": sum(len(v["nulls"]) for v in summary.values()), "endpoints": len(summary), "per_endpoint": summary,
           "widened_nulls": sum(1 for v in summary.values() for x in v["nulls"] if x["widened"])}
    write_json(marker, out)
    print(json.dumps({k: out[k] for k in ("nulls", "endpoints", "widened_nulls")}, indent=1))


def full_path(base, gid):
    return Path(base) / "spectra" / "full" / f"{gid.replace('/', '__')}.json"


def spectra(_args):
    check_prereg()
    marker = OUT / "spectra/complete.json"
    if marker.exists():
        raise Refused("ln-spectra-already-complete")
    if not (OUT / "controls/complete.json").exists():
        raise Refused("ln-nulls-not-generated")
    gm, vm = env()
    o9, mp = rr.load_spectral()
    manifest = rr.read_json(sf.manifest_path(SOURCE_RUN))
    anchors, pin = manifest["c_pin"]["anchors"], manifest["spectral_pin"]
    src = source_root()
    t0 = time.time()
    names = endpoints_of(admission())
    determinism = None
    for name in names:
        om, nodes, ve, receipt, T = sfb.namespace_graphs(gm, vm, src / name)
        mapping = receipt["mapping"]
        K = locality_region(T, om, ve, receipt["added"])
        lns = [(f"{name}/ln-{k}", sf.edges_of(OUT / "controls" / name / f"ln-{k}.json"))
               for k in range(1, NULLS_PER_VARIANT + 1)]
        for gid, edges in lns:
            path = full_path(OUT, gid)
            if not path.exists():
                write_json(path, rr.spectral_profile(nodes, edges, anchors, mapping, pin))
                print(f"full {gid} {time.time() - t0:.0f}s", flush=True)
        if determinism is None:  # D7: recompute one O' receipt and compare with 12b's
            ref = rr.read_json(full_path(src, f"{name}/O-prime"))
            again = rr.spectral_profile(nodes, om, anchors, mapping, pin)
            determinism = {"graph": f"{name}/O-prime", "profile_sha256_12b": ref["profile_sha256"],
                           "profile_sha256_recomputed": again["profile_sha256"],
                           "equal": ref["profile_sha256"] == again["profile_sha256"]}
            print(json.dumps(determinism), flush=True)
        sub_path = OUT / "spectra" / "subgraph" / f"{name}.json"
        if not sub_path.exists():
            graphs = [(f"{name}/O-prime", om), (name, ve)] + lns
            S = sf.subgraph_nodes_v2(T, om, ve, receipt["added"], [e for _, e in lns])
            rows = {}
            kset = set(K)
            for gid, edges in graphs:
                ind = sf.induced(edges, S)
                added = sorted(set(edges) - set(om))
                rows[gid] = dict(sf.measure_vector(o9, mp, S, ind), added_edges=len(added),
                                 added_inside_S=sum(1 for e in added if e in set(ind)),
                                 added_inside_v1_core=sum(1 for a, b in added if a in kset and b in kset),
                                 added_source_in_T=sum(1 for a, _ in added if a in set(T)))
            write_json(sub_path, {"variant_id": name, "S_nodes": len(S), "v1_core_nodes": len(K),
                                  "S_equals_core": set(S) == kset, "S_sha256": sha_text(json.dumps(S)),
                                  "touched": len(T), "graphs": rows})
            print(f"subgraph {name} |S|={len(S)} |K|={len(K)} {time.time() - t0:.0f}s", flush=True)
    write_json(marker, {"determinism": determinism, "endpoints": len(names)})
    print(json.dumps({"determinism": determinism}, indent=1))


def analyse_rows(gm, vm, o9, mp, adm):
    """Full and subgraph rows (d, LN null d) per endpoint, and the RN rows from the 12b spectra evidence."""
    src = source_root()
    views = {"full": [], "subgraph": []}
    locality = {}
    for name in endpoints_of(adm):
        om, nodes, ve, receipt, T = sfb.namespace_graphs(gm, vm, src / name)
        arm = name.split("-r")[0]

        def fv(path, edges):
            lw, le = sf.lwcc_of(o9, mp, nodes, edges)
            return sf.full_vector(rr.read_json(path), sf.reciprocal_fraction(le))

        base = fv(full_path(src, f"{name}/O-prime"), om)
        v_vec = fv(full_path(src, name), ve)
        ln_ids = [f"{name}/ln-{k}" for k in range(1, NULLS_PER_VARIANT + 1)]
        ln_edges = [sf.edges_of(OUT / "controls" / name / f"ln-{k}.json") for k in range(1, NULLS_PER_VARIANT + 1)]
        views["full"].append({"variant_id": name, "arm": arm, "d": sf.delta(v_vec, base),
                              "nulls": [sf.delta(fv(full_path(OUT, g), e), base) for g, e in zip(ln_ids, ln_edges)]})
        sub = rr.read_json(OUT / "spectra" / "subgraph" / f"{name}.json")
        g = sub["graphs"]
        views["subgraph"].append({"variant_id": name, "arm": arm, "d": sf.delta(g[name], g[f"{name}/O-prime"]),
                                  "nulls": [sf.delta(g[x], g[f"{name}/O-prime"]) for x in ln_ids]})
        locality[name] = {"S_LN_nodes": sub["S_nodes"], "K_v_nodes": sub["v1_core_nodes"],
                          "S_equals_core": sub["S_equals_core"],
                          "v": [g[name]["added_inside_v1_core"], g[name]["added_source_in_T"], g[name]["added_edges"]],
                          "ln": [[g[x]["added_inside_v1_core"], g[x]["added_source_in_T"], g[x]["added_edges"]]
                                 for x in ln_ids]}
    return views, locality


def analyse(_args):
    pre = check_prereg()
    if list(EVIDENCE.glob(RESULTS_GLOB)):
        raise Refused("ln-already-analysed")
    if not (OUT / "spectra/complete.json").exists():
        raise Refused("ln-spectra-not-complete")
    gm, vm = env()
    o9, mp = rr.load_spectral()
    adm = admission()
    area_of = pre["endpoints"]["area_of"]
    views, locality = analyse_rows(gm, vm, o9, mp, adm)
    ln = {}
    for view, rows in views.items():
        pooled = sf.pooled_null_sd([r["nulls"] for r in rows])
        for r in rows:
            r["z"], r["z_rule"] = sf.standardize(r["d"], r["nulls"], pooled)
        zrows = [{"variant_id": r["variant_id"], "arm": r["arm"], "z": r["z"]} for r in rows]
        ln[view] = {"pooled_null_sd": pooled, "endpoints": rows, "arm_mean_z": arm_means(rows),
                    "signature_tally": sf.signature_tally(rows), "stratified": stratified_test(zrows, area_of)}
    ln["full"]["three_arm_unstratified"] = sf.primary_statistic(
        [{"variant_id": r["variant_id"], "arm": r["arm"], "z": r["z"]} for r in views["full"]])
    rn_ev = rr.read_json(INPUTS["spectra_12b"])
    rn = {}
    for view in ("full", "subgraph"):
        rows = rn_ev["views"][view]["endpoints"]
        zrows = [{"variant_id": r["variant_id"], "arm": r["arm"], "z": r["z"]} for r in rows]
        rn[view] = {"arm_mean_z": arm_means(rows), "stratified": stratified_test(zrows, area_of),
                    "three_arm_unstratified_12b": (rn_ev["full_view_statistic_descriptive"] if view == "full"
                                                   else rn_ev["primary"])}
        rn[view]["inside_v1_core_12b"] = ({r["variant_id"]: r["inside_v1_core"] for r in rows}
                                          if view == "subgraph" else None)
    primary = ln["full"]["stratified"]
    row = outcome_row(primary["p"], rn["full"]["stratified"]["p"], ln["full"]["arm_mean_z"][GROUP])
    controls = rr.read_json(OUT / "controls/complete.json")
    ln_added = sum(x["locality"]["added"] for v in controls["per_endpoint"].values() for x in v["nulls"])
    ln_inside = sum(x["locality"]["added_inside_region"] for v in controls["per_endpoint"].values() for x in v["nulls"])
    ln_src = sum(x["locality"]["added_source_in_T"] for v in controls["per_endpoint"].values() for x in v["nulls"])
    res = {"schema_version": "docspectra.icsc.lt_locality_13.v1.results", "preregistration_sha256": sha(PREREG),
           "primary": dict(primary, view="full", null="LN", label="the one confirmatory test"),
           "outcome_row": row, "outcome_statement": next(o["statement"] for o in OUTCOME_TABLE if o["row"] == row),
           "ln": ln, "rn": rn,
           "locality": {"ln_added": ln_added, "ln_added_inside_K_v": ln_inside, "ln_added_source_in_T": ln_src,
                        "per_endpoint": locality, "widened_nulls": controls["widened_nulls"],
                        "widenings": {n: [x["widened"] for x in v["nulls"] if x["widened"]]
                                      for n, v in controls["per_endpoint"].items()}},
           "determinism": rr.read_json(OUT / "spectra/complete.json")["determinism"],
           "inputs": pre["hashes"]}
    path = EVIDENCE / f"lt-locality-13-results-v1-{sf.today()}.json"
    digest = write_json(path, res)
    path.with_suffix(".md").write_text(results_markdown(res, digest), encoding="utf-8", newline="\n")
    print(json.dumps({"sha256": digest, "primary_p": primary["p"], "statistic": primary["statistic"],
                      "outcome_row": row, "rn_stratified_p": rn["full"]["stratified"]["p"]}, indent=1))


def results_markdown(res, digest):
    f = sf.fmt
    lines = [f"# Sprint 13 results: LT full view against a locality-matched null", "",
             f"Evidence `{digest}`; pre-registration `{res['preregistration_sha256']}`.", "",
             "## Primary (confirmatory)", "",
             f"- Full view, LN, LT vs the rest, area-stratified exact permutation: statistic "
             f"{res['primary']['statistic']:.3f}, **p = {res['primary']['p']:.4f}** "
             f"({res['primary']['at_least_observed']}/{res['primary']['assignments']}; minimum "
             f"{res['primary']['minimum_p']:.4f}); measures dropped: {res['primary']['measures_dropped'] or 'none'}.",
             f"- Outcome row **{res['outcome_row']}**: {res['outcome_statement']}.", "",
             "## Stratified statistic, all views and nulls (descriptive except the primary)", "",
             "| View | LN p | RN p |", "|---|---|---|"]
    for view in ("full", "subgraph"):
        lines.append(f"| {view} | {res['ln'][view]['stratified']['p']:.4f} | {res['rn'][view]['stratified']['p']:.4f} |")
    lines += ["", f"12b-form 3-arm unstratified, full view, LN: p = {res['ln']['full']['three_arm_unstratified']['p']:.4f}"
              f" (RN, 12b: {res['rn']['full']['three_arm_unstratified_12b']['p']:.4f}).", ""]
    for view in ("full", "subgraph"):
        lines += [f"## Arm-mean z, {view} view (LN / RN)", "", "| Measure | LT | MT | MR |", "|---|---|---|---|"]
        for m in sf.MEASURES:
            cells = [f"{f(res['ln'][view]['arm_mean_z'][a][m], 2)} / {f(res['rn'][view]['arm_mean_z'][a][m], 2)}"
                     for a in sf.STRUCTURE_ARMS]
            lines.append(f"| {m} | " + " | ".join(cells) + " |")
        lines.append("")
        lines += [f"Signature tally against LN, {view} view:", "", "| Measure | LT | MT | MR |", "|---|---|---|---|"]
        for m, arms in res["ln"][view]["signature_tally"].items():
            cells = [(f"{c['prediction']} {c['agree_and_outside_central_90']}/{c['endpoints']}" if c["counted"]
                      else "not counted") for c in (arms[a] for a in sf.STRUCTURE_ARMS)]
            lines.append(f"| {m} | " + " | ".join(cells) + " |")
        lines.append("")
    loc = res["locality"]
    lines += ["## Locality", "",
              f"- LN additions inside K_v: {loc['ln_added_inside_K_v']}/{loc['ln_added']}; source in T: "
              f"{loc['ln_added_source_in_T']}/{loc['ln_added']}. Widened nulls: {loc['widened_nulls']}.", "",
              "| Endpoint | K_v | S_LN | v inside K_v / source in T / added | LN inside K_v (per null) | RN inside core (12b) |",
              "|---|---|---|---|---|---|"]
    rn_core = res["rn"]["subgraph"]["inside_v1_core_12b"]
    for name, x in loc["per_endpoint"].items():
        lines.append(f"| {name} | {x['K_v_nodes']} | {x['S_LN_nodes']} | {x['v'][0]} / {x['v'][1]} / {x['v'][2]} | "
                     + ", ".join(f"{a}/{c}" for a, _, c in x["ln"]) + " | "
                     + ", ".join(f"{a}/{c}" for a, c in rn_core[name]["nulls"]) + " |")
    d = res["determinism"]
    lines += ["", f"Determinism: {d['graph']} recomputed profile_sha256 equals 12b's: {d['equal']}.", ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="stage", required=True)
    for name, fn in (("freeze", freeze), ("nulls", nulls), ("spectra", spectra), ("analyse", analyse)):
        sub.add_parser(name).set_defaults(fn=fn)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

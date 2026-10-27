"""Sprint 12: structure-forcing final experiment (LT / MT / MR versus matched random edits).

Stages (each refuses to run twice; long stages resume per item and refuse once complete):
  amend              plan v2 (author, after the fixture): subgraph node set and pooled-sd standardization
  freeze             three arm prompts (R0-v4 + one target-structure sentence), arm templates v4, the live run
                     manifest sf-12-v1 and the analysis plan; no model call
  fixture            zero model calls: scripted LT/MT/MR stand-in edits through the container runtime, admission,
                     controls, spectra and the analysis code on a separate fixture run
  edit               --arm --replicate, or --next (the frozen seeded order, then at most one replacement per arm);
                     O and S through the same pipeline
  admit              admission per endpoint (C, normalized PL2, preservation), edit magnitude, the blind export of
                     the 14 answerable variants (opaque ids, key outside the packet) and the sham identity
  generate-controls  5 random-edit nulls per admitted endpoint, BD from O, LT-BD per admitted LT endpoint
  spectra            full view (pinned 09c spectral_profile) and subgraph view (touched set + 1-hop) for every graph
  analyse-spectra    the one primary statistic, the descriptive tallies and the BD / LT-BD checks
  answer             560 Luna cells (14 variants x 5 questions x 4 conditions x 2 replicates), one ledger per replicate
  archive-infra      exclude a model_api_error attempt under the author rule for infrastructure stops
  score              --pass A|B --worker 0|1: Luna scorer v2 on every answer, k = 2 passes, two workers
  rubric             secondary boolean rubric (think-off qwen3:14b, relaxed quote match), blind
  analyse-answers    exploratory answer tables with S - O beside every arm - O
  explore-spectra    EXPLORATORY (added after the editing shortfall): every C-passed completed session
  dose-edit          optional; refuses until the author's go is recorded

Extends Sprints 10, 11 and 11b without modifying them: restructuring_runs and restructuring_pilot_11b are imported,
never edited. The structure arms are added to restructuring_runs' arm tuples at import.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")  # as restructuring_runs and 09c

import argparse
import hashlib
import json
import math
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import docker_corpus_variants as dcv  # noqa: E402
import restructuring_runs as rr  # noqa: E402

STRUCTURE_ARMS = ("LT", "MT", "MR")
# The Sprint 10 launch knows O/S/R0/L; the structure arms are restructuring arms with their own prompts.
rr.ARMS = tuple(dict.fromkeys(rr.ARMS + STRUCTURE_ARMS))
rr.EDITED = tuple(dict.fromkeys(rr.EDITED + STRUCTURE_ARMS))

DATE = "2026-10-09"
RUN_ID = "sf-12-v1"
FIXTURE_RUN_ID = "sf-12-fixture-v2"  # v1: fixture under plan v1 (exposed the subgraph defect)
REALIZATIONS = 4
REPLACEMENTS_PER_ARM = 1
CONTEXT = rr.CONTEXT
EVIDENCE = CONTEXT / "evidence"
V6 = EVIDENCE / "docker-corpus-variant-input-v6-docs-scope-2026-10-07.json"
TEMPLATES_V3 = EVIDENCE / "restructuring-arm-templates-v3-codex-2026-10-08.json"
TEMPLATES_V4 = EVIDENCE / f"restructuring-arm-templates-v4-structure-forcing-{DATE}.json"
PLAN_V1 = EVIDENCE / f"structure-forcing-12-plan-v1-{DATE}.json"
PLAN = EVIDENCE / f"structure-forcing-12-plan-v2-{DATE}.json"
FIXTURE_V1_EVIDENCE = EVIDENCE / f"structure-forcing-12-fixture-v1-{DATE}.json"
FIXTURE_EVIDENCE = EVIDENCE / f"structure-forcing-12-fixture-v2-{DATE}.json"
QUESTIONS_11B = EVIDENCE / "restructuring-pilot-11b-questions-2026-10-08.json"
PANEL_PLAN = EVIDENCE / "agent-search-factorial-panel-plan-v5-2026-09-30.json"
R0_V4 = CONTEXT / "prompts/docker-editor-R0-v4.txt"
DESIGN = CONTEXT / "design/structure-forcing-minimum-plan-2026-10-09.md"
REVIEW = CONTEXT / "reviews/2026-10-09-structure-forcing-minimum-plan-review.md"
ANSWERS = rr.ROOT / "artifacts/sf-12-v1-answers"
SCORER = rr.ROOT / "artifacts/sf-12-v1-scorer"
JUDGE = rr.ROOT / "artifacts/sf-12-v1-judge"
AUTH_DEFAULT = Path("C:/Users/rocke/.docspectra-codex-auth/auth.json")

# ------------------------------------------------------------------ frozen constants (copied into the plan)

ARM_TARGETS = {
    "LT": "specific leaf pages that link one way to shared, abstracted procedure and context pages",
    "MT": "a table of context links on every page, pointing to the procedures and concepts the page depends on",
    "MR": "related-page links between pages that cover adjacent topics",
}
R0_ROLE = "arm R0, prompt version 4."
R0_MORPHOLOGY = "Do not aim for any prescribed graph morphology."
TARGET_SENTENCE = "Build toward this target structure, within the session limits stated below: {target}."

MEASURES = ("normalized_lambda2", "magnetic_lambda1_q0.25", "magnetic_lambda2_q0.25", "subspace_polarization_v1",
            "reciprocal_edge_fraction", "outdegree_gini", "sources", "sinks")
SIGNATURES = {  # minimum plan revision 2, section 2, verbatim directions; "flat" and "down or flat" are not counted
    "normalized_lambda2": {"LT": "up", "MT": "up", "MR": "up"},
    "magnetic_lambda1_q0.25": {"LT": "up", "MT": "up", "MR": "down"},
    "magnetic_lambda2_q0.25": {"LT": "up", "MT": "up", "MR": "up"},
    "reciprocal_edge_fraction": {"LT": "down or flat", "MT": "down or flat", "MR": "up"},
    "outdegree_gini": {"LT": "up", "MT": "down", "MR": "down"},
    "sinks": {"LT": "up", "MT": "flat", "MR": "down"},
    "sources": {"LT": "flat", "MT": "flat", "MR": "down"},
}
NULLS_PER_VARIANT = 5
NULL_SEED_SALT = "icsc2027-12-rn:"
PERMUTATIONS = 10_000
PERMUTATION_SEED_LABEL = "icsc2027-12-primary-permutation"
ALPHA = 0.05
BD_TOLERANCE = 1e-9
CENTRAL = (5.0, 95.0)  # the null's central 90%, numpy.percentile (linear) over the 5 null deltas
EDIT_ORDER_SEED = 20261009
SCHEDULE_SEED = 20261009
SCORER_SEEDS = {"A": 20261021, "B": 20261022}
BOOTSTRAP = {"replicates": 10_000, "seed": 20261023}
REPLICATES_PER_CELL = 2
ANSWER_LEDGER_STOP_USD = 27.0   # per replicate ledger; >= the 5,600-call worst case $26.87 (panel plan v5)
HARD_STOP_USD = 60.0
DOSE_TOPIC = "container networking"
REVIEW_LABEL = "author-chat-2026-10-09-sprint-12-usd60"


class Refused(SystemExit):
    pass


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(path: Path, data):
    """Write once (refuses to overwrite); returns the file sha256."""
    return rr.write_new(Path(path), data)


def write_or_same(path: Path, data):
    path = Path(path)
    if path.exists():
        if rr.read_json(path) != json.loads(json.dumps(data)):
            raise Refused(f"refusing-to-overwrite:{path.name}")
        return sha(path)
    return write_json(path, data)


# ------------------------------------------------------------------ prompts and templates

def arm_prompt(r0_bytes: bytes, arm: str) -> bytes:
    """R0-v4 with its role line naming the arm and its no-morphology sentence replaced by the target structure."""
    text = r0_bytes.decode("utf-8")
    if text.count(R0_ROLE) != 1 or text.count(R0_MORPHOLOGY) != 1:
        raise Refused("r0-v4-anchor-text-missing")
    text = text.replace(R0_ROLE, f"arm {arm}, prompt version 1.")
    text = text.replace(R0_MORPHOLOGY, TARGET_SENTENCE.format(target=ARM_TARGETS[arm]))
    return text.encode("utf-8")


def prompt_path(arm):
    return CONTEXT / f"prompts/docker-editor-{arm}-v1.txt"


def templates_v4():
    v3 = rr.read_json(TEMPLATES_V3)
    r0 = v3["arms"]["R0"]["prompt"]
    if sha(R0_V4) != r0["sha256"]:
        raise Refused("r0-v4-prompt-drift")
    arms = dict(v3["arms"])
    for arm in STRUCTURE_ARMS:
        path = prompt_path(arm)
        arms[arm] = {"editor": "structure", "scope": "restructure",
                     "prompt": {"path": f"git/context/prompts/{path.name}", "sha256": sha(path), "version": f"{arm}-v1"},
                     "target_structure": ARM_TARGETS[arm],
                     "parent": {"path": r0["path"], "sha256": r0["sha256"], "version": r0["version"]},
                     "differences_from_parent": [f"role line: '{R0_ROLE}' -> 'arm {arm}, prompt version 1.'",
                                                 f"'{R0_MORPHOLOGY}' -> '" + TARGET_SENTENCE.format(
                                                     target=ARM_TARGETS[arm]) + "'"]}
    return {"schema_version": "docspectra.icsc.restructuring_run.v1.arm_templates", "created": DATE, "sprint": "12",
            "arms": arms,
            "shared": dict(v3["shared"], supersedes={"path": "git/context/evidence/" + TEMPLATES_V3.name,
                                                    "sha256": sha(TEMPLATES_V3)},
                           structure_arms="LT, MT and MR keep R0-v4's preservation rules, limits (60 minutes, 150 "
                                          "paths), the 'no checkpoint step' sentence and the no-metric statement; "
                                          "each states one target structure",
                           design=str(DESIGN.relative_to(rr.ROOT)).replace("\\", "/"))}


# ------------------------------------------------------------------ graph rules (pure; tested offline)

def canonical(edges):
    return sorted({(s, t) for s, t in edges if s != t})


def mapped_edges(o_edges, mapping, nodes):
    """O's edges in v's node space: removed ids -> survivors; unmapped removals and self-loops dropped."""
    nodes = set(nodes)
    out = set()
    for s, t in o_edges:
        s, t = mapping.get(s, s), mapping.get(t, t)
        if s != t and s in nodes and t in nodes:
            out.add((s, t))
    return sorted(out)


def touched_set(mapping_receipt):
    """Pages v changed, added or removed; a removed page is represented by its survivor in v."""
    m = mapping_receipt["mapping"]
    return sorted(set(mapping_receipt["changed"]) | set(mapping_receipt["added"]) |
                  {m[d] for d in mapping_receipt["removed"] if d in m})


def neighbors(edges, centre):
    centre = set(centre)
    out = set()
    for s, t in edges:
        if s in centre:
            out.add(t)
        if t in centre:
            out.add(s)
    return out


def subgraph_nodes(touched, o_mapped, v_edges, added):
    """Subgraph view node set: touched set, its 1-hop neighbours in O (mapped), and added pages' neighbours in v."""
    touched = set(touched)
    return sorted(touched | neighbors(o_mapped, touched) | neighbors(v_edges, set(added)))


def edit_endpoints(o_mapped, graphs):
    """Every endpoint of an edge added or removed (against O') by any of `graphs`."""
    o = set(o_mapped)
    return {x for g in graphs for e in set(g) ^ o for x in e}


def subgraph_nodes_v2(touched, o_mapped, v_edges, added, null_edge_lists):
    """Plan v2: the v1 node set plus every edit endpoint of v and its nulls, common to v, its nulls and O'."""
    return sorted(set(subgraph_nodes(touched, o_mapped, v_edges, added)) |
                  edit_endpoints(o_mapped, [v_edges] + list(null_edge_lists)))


def induced(edges, nodes):
    keep = set(nodes)
    return [(s, t) for s, t in edges if s in keep and t in keep]


def reciprocal_fraction(edges, within=None):
    """Share of edges (or of `within`, a subset) whose reverse is in `edges`."""
    es = set(edges)
    pool = list(within) if within is not None else list(es)
    return sum((t, s) in es for s, t in pool) / len(pool) if pool else 0.0


def edit_magnitude(o_mapped, v_edges, touched):
    o, v = set(o_mapped), set(v_edges)
    added, removed = sorted(v - o), sorted(o - v)
    return {"edges_added": len(added), "edges_removed": len(removed),
            "reciprocal_fraction_added": reciprocal_fraction(v, added), "touched": len(touched),
            "added_edges": added, "removed_edges": removed}


def null_seed(variant_id: str, k: int) -> int:
    return int(hashlib.sha256(f"{NULL_SEED_SALT}{variant_id}:{k}".encode()).hexdigest(), 16)


def random_null(nodes, o_mapped, touched, a, r, f, seed):
    """Frozen RN rule: remove r, then add a edges (reciprocal share f) around the touched set; deterministic per seed.

    Pools: removals level 0 = O edges with both endpoints in T; additions level 0 = non-edges with at least one
    endpoint in T. A pool too small for its count is widened once to W = T + 1-hop neighbours (both endpoints in W /
    at least one in W). Reciprocal additions take non-edges whose reverse exists; any shortfall is made up by mutual
    pairs (both directions), so the reciprocal count can exceed round(f*a) by one edge. Never drops an edge silently.
    """
    rng = random.Random(seed)
    nodes = sorted(nodes)
    node_set = set(nodes)
    T = set(touched) & node_set
    W = T | (neighbors(o_mapped, T) & node_set)
    current = set(o_mapped)
    receipt = {"seed_sha256_prefix": hashlib.sha256(str(seed).encode()).hexdigest()[:16], "a": a, "r": r,
               "f_target": f, "widened": []}
    # removals
    level0 = sorted((s, t) for s, t in current if s in T and t in T)
    pool, level = level0, 0
    if r > len(level0):
        pool, level = sorted((s, t) for s, t in current if s in W and t in W), 1
        receipt["widened"].append({"stage": "remove", "need": r, "level0": len(level0), "level1": len(pool)})
    if r > len(pool):
        raise Refused(f"null-removal-pool-too-small:{r}>{len(pool)}")
    removed = set(rng.sample(pool, r)) if r else set()
    receipt["remove_pool"] = {"level": level, "size": len(pool)}
    current -= removed
    # additions
    k = round(f * a) if a else 0
    for level, centre in ((0, T), (1, W)):
        around = {(c, x) for c in centre for x in nodes if x != c} | {(x, c) for c in centre for x in nodes if x != c}
        pairs = sorted(p for p in around if p not in current and p not in removed)
        if len(pairs) >= a:
            break
    if level == 1:
        receipt["widened"].append({"stage": "add", "need": a})
    if len(pairs) < a:
        raise Refused(f"null-addition-pool-too-small:{a}>{len(pairs)}")
    pair_set = set(pairs)
    recip_pool = [p for p in pairs if (p[1], p[0]) in current]
    plain_pool = [p for p in pairs if (p[1], p[0]) not in current]
    chosen = set(rng.sample(recip_pool, min(k, len(recip_pool))))
    short = k - len(chosen)
    rng.shuffle(plain_pool)
    mutual = []
    if short > 0:
        for u, w in plain_pool:
            if len(chosen) >= k or len(chosen) + 2 > a:
                break
            if (w, u) in pair_set and (u, w) not in chosen and (w, u) not in chosen and u < w:
                chosen.update({(u, w), (w, u)})
                mutual.append((u, w))
    receipt["reciprocal"] = {"target": k, "from_existing_reverse": min(k, len(recip_pool)), "mutual_pairs": len(mutual)}
    for u, w in plain_pool:
        if len(chosen) >= a:
            break
        if (u, w) in chosen or (w, u) in chosen:
            continue
        chosen.add((u, w))
    if len(chosen) != a:
        raise Refused(f"null-addition-count:{len(chosen)}!={a}")
    receipt["add_pool"] = {"level": level, "size": len(pairs), "reciprocal_candidates": len(recip_pool)}
    edges = sorted(current | chosen)
    receipt["result"] = {"edges": len(edges), "added": len(chosen), "removed": len(removed),
                         "reciprocal_fraction_added": reciprocal_fraction(edges, sorted(chosen))}
    return edges, receipt


def mirror(edges):
    return sorted(set(edges) | {(t, s) for s, t in edges})


def bd_identity(mp, nodes, edges, qs):
    """Max |eigenvalue difference| between the magnetic Laplacian at each q and the real Laplacian of the all-ones
    symmetrization (weight 1 on every linked pair)."""
    import numpy as np
    nodes = sorted(nodes)
    es = mp.canonical_edges(nodes, edges)
    idx = {x: i for i, x in enumerate(nodes)}
    a = np.zeros((len(nodes), len(nodes)))
    for s, t in es:
        a[idx[s], idx[t]] = a[idx[t], idx[s]] = 1.0
    real = np.linalg.eigvalsh(np.diag(a.sum(axis=1)) - a)
    out = {}
    for q in qs:
        e = np.linalg.eigvalsh(mp.magnetic_matrix(nodes, es, q))
        out[f"{q:.2f}"] = float(np.max(np.abs(e - real)))
    return {"max_abs_eigenvalue_difference": out, "worst": max(out.values()),
            "passes": max(out.values()) <= BD_TOLERANCE, "tolerance": BD_TOLERANCE,
            "magnetic_lambda1_q0.25": float(np.linalg.eigvalsh(mp.magnetic_matrix(nodes, es, 0.25))[0])}


# ------------------------------------------------------------------ measures

def measure_vector(o9, mp, nodes, edges):
    """The frozen measure vector on the largest weakly connected component of (nodes, edges), with the pinned 09c
    functions (spectra at q = .25 with the subspace descriptor, normalized_lambda2, degree_controls)."""
    nodes = sorted(nodes)
    es = mp.canonical_edges(nodes, edges)
    lw = sorted(o9.weak_components(nodes, es)[0]) if nodes else []
    ls = set(lw)
    le = [(s, t) for s, t in es if s in ls and t in ls]
    out = {"nodes": len(nodes), "edges": len(es), "lwcc_nodes": len(lw), "lwcc_edges": len(le)}
    if len(lw) < 3 or not le:
        return dict(out, **{m: None for m in MEASURES}, degenerate=True)
    spec = o9.spectra(mp, lw, le, [0.25], subspace=True)
    ctl = o9.degree_controls(lw, le)
    sub = spec["subspace_polarization_v1"]
    return dict(out, **{"normalized_lambda2": o9.normalized_lambda2(lw, le),
                        "magnetic_lambda1_q0.25": spec["0.25"]["lambda1"],
                        "magnetic_lambda2_q0.25": spec["0.25"]["lambda2"],
                        "subspace_polarization_v1": sub.get("value"),
                        "subspace_status": sub.get("status"),
                        "reciprocal_edge_fraction": reciprocal_fraction(le),
                        "outdegree_gini": ctl["outdegree_gini"], "sources": ctl["sources"], "sinks": ctl["sinks"]})


def delta(a, b):
    return {m: (a[m] - b[m]) if a.get(m) is not None and b.get(m) is not None else None for m in MEASURES}


def standardize(d_v, null_deltas, pooled_sd):
    """Plan v2: z per measure = (d_v - the variant's null mean) / the pooled within-variant null sd of the measure;
    pooled sd 0 or any value missing -> None. Returns (z, rule used per measure)."""
    z, rule = {}, {}
    for m in MEASURES:
        xs = [d[m] for d in null_deltas]
        if d_v.get(m) is None or any(x is None for x in xs) or len(xs) < 2:
            z[m], rule[m] = None, "unavailable"
            continue
        mean = sum(xs) / len(xs)
        if pooled_sd.get(m):  # plan v2: always the pooled within-variant null sd (v1 used the variant's own sd)
            z[m], rule[m] = (d_v[m] - mean) / pooled_sd[m], "pooled"
        else:
            z[m], rule[m] = None, "zero_sd"
    return z, rule


def pooled_null_sd(null_sets):
    """sqrt(mean within-variant null variance) per measure over every endpoint's nulls (ddof 1)."""
    out = {}
    for m in MEASURES:
        vs = []
        for deltas in null_sets:
            xs = [d[m] for d in deltas]
            if len(xs) >= 2 and all(x is not None for x in xs):
                mean = sum(xs) / len(xs)
                vs.append(sum((x - mean) ** 2 for x in xs) / (len(xs) - 1))
        out[m] = math.sqrt(sum(vs) / len(vs)) if vs else None
    return out


def between_arm_ss(Z, labels):
    """Sum over arms of n_arm * ||arm mean z - grand mean z||^2."""
    import numpy as np
    Z = np.asarray(Z, dtype=float)
    labels = np.asarray(labels)
    grand = Z.mean(axis=0)
    return float(sum((labels == a).sum() * np.sum((Z[labels == a].mean(axis=0) - grand) ** 2)
                     for a in sorted(set(labels.tolist()))))


def permutation_p(Z, labels, n=PERMUTATIONS, seed=None):
    import numpy as np
    seed = seed if seed is not None else int(hashlib.sha256(PERMUTATION_SEED_LABEL.encode()).hexdigest(), 16) % 2**63
    rng = np.random.default_rng(seed)
    observed = between_arm_ss(Z, labels)
    labels = np.asarray(labels)
    hits = sum(between_arm_ss(Z, rng.permutation(labels)) >= observed - 1e-12 for _ in range(n))
    return {"statistic": observed, "p": (1 + hits) / (1 + n), "permutations": n, "seed": seed}


def primary_statistic(endpoints):
    """endpoints: [{"variant_id", "arm", "z": {measure: value|None}}]. Measures with any None are dropped (recorded)."""
    used = [m for m in MEASURES if all(e["z"].get(m) is not None for e in endpoints)]
    dropped = [m for m in MEASURES if m not in used]
    if len(endpoints) < 2 or not used:
        return {"measures_used": used, "measures_dropped": dropped, "statistic": None, "p": None,
                "endpoints": len(endpoints)}
    Z = [[e["z"][m] for m in used] for e in endpoints]
    res = permutation_p(Z, [e["arm"] for e in endpoints])
    return dict(res, measures_used=used, measures_dropped=dropped, endpoints=len(endpoints),
                rejects=res["p"] < ALPHA, threshold=ALPHA)


def outside_central(d, null_values):
    import numpy as np
    lo, hi = np.percentile(np.asarray(null_values, dtype=float), CENTRAL)
    return bool(d < lo or d > hi)


def signature_tally(rows):
    """rows: [{"arm", "d": {...}, "nulls": [{...}], "z": {...}}] -> per arm and measure, endpoints agreeing in sign
    (relative to the null median via z) and outside the null's central 90%; only up/down predictions count."""
    import numpy as np
    out = {}
    for m, preds in SIGNATURES.items():
        for arm, pred in preds.items():
            mine = [r for r in rows if r["arm"] == arm]
            cell = {"prediction": pred, "endpoints": len(mine), "counted": pred in ("up", "down")}
            if cell["counted"]:
                agree = 0
                for r in mine:
                    d, nulls = r["d"].get(m), [x[m] for x in r["nulls"]]
                    if d is None or any(x is None for x in nulls):
                        continue
                    med = float(np.median(nulls))
                    sign_ok = d > med if pred == "up" else d < med
                    agree += bool(sign_ok and outside_central(d, nulls))
                cell["agree_and_outside_central_90"] = agree
            out.setdefault(m, {})[arm] = cell
    return out


# ------------------------------------------------------------------ answer schedule (pure)

def slots():
    return ["O", "S"] + [f"{a}-{i}" for a in STRUCTURE_ARMS for i in range(1, REALIZATIONS + 1)]


def schedule(question_ids, condition_ids, seed=SCHEDULE_SEED):
    """Per replicate, a seeded shuffle of slot x question x condition; replicate 1 runs entirely before replicate 2."""
    out = []
    for rep in range(1, REPLICATES_PER_CELL + 1):
        cells = [{"slot": s, "question_id": q, "condition": c, "replicate": rep}
                 for s in slots() for q in question_ids for c in condition_ids]
        random.Random(seed + rep).shuffle(cells)
        out += cells
    return out


def cell_stem(variant, question_id, condition, replicate):
    return f"{variant}-{question_id[:12]}-{condition}-r{replicate}"


def cell_command(a, *, dataset, question, dest, ledger, future_calls, campaign_calls, stop_usd, condition):
    """The 11b cell command (restructuring_pilot_11b.answer), with this sprint's ledger and review label."""
    b = a["budgets"]
    cmd = [sys.executable, str(rr.ROOT / a["agent_loop"]), "--dataset", str(dataset),
           "--question-id", question["question_id"], "--question", question["question"], "--prompt", a["prompt_path"],
           "--output", str(dest), "--model", a["model"], "--provider", "openai-responses",
           "--outbound-review-id", REVIEW_LABEL, "--cost-ledger", str(ledger),
           "--future-calls-after-episode", str(future_calls),
           "--campaign-calls", str(campaign_calls), "--openai-output-cap", str(a["max_output_tokens"]),
           "--openai-input-cap", str(a["max_input_tokens"]),
           "--estimated-charge-stop-usd", str(stop_usd),
           "--search-budget", str(condition["searches"]), "--read-budget", str(b["reads"]),
           "--call-budget", str(b["model_calls"]), "--history-bytes", str(b["history_bytes"]),
           "--wall-seconds", str(b["wall_seconds"]), "--note-budget", str(b["notes"]),
           "--note-chars", str(b["note_chars"]), "--min-distinct-searches", str(condition["min_distinct_searches"])]
    if condition["follow_links"]:
        cmd.append("--follow-links")
    return cmd


# ------------------------------------------------------------------ plan and hash gate

def runtime_block(es):
    return es.runtime_block(model="gpt-6.1-sol", reasoning_effort="medium", driver="codex-exec",
                            grading_consequence="usage- or rate-limit stop: excluded; a fresh session from a new "
                                                "baseline copy may run (author rule for infrastructure stops); not "
                                                "counted as the arm's replacement")


ADMISSION_RULE = {
    "version": "sf12_admission_v1",
    "rule": "status completed (not no_op, failed, budget_stop, c_rejected or preservation_flagged); C v1 passed; "
            "preservation review: 0 introduced unresolved internal links and 0 protected lines removed corpus-wide "
            "after stripping leading and trailing whitespace from each protected line (the 11b PL2 amendment, "
            "re-declared here before any session); partial_unit_loss is reported, not gating",
    "replacement": "one extra session per arm (replicate 5) if any of its 4 endpoints is not admitted; the outcome is "
                   "recorded. An infrastructure stop (usage or rate limit) is excluded and rerun as a fresh session "
                   "under the author rule; it is not counted as the replacement",
}


def manifest_path(run_id=RUN_ID):
    return Path(rr.read_json(V6)["output_root"]) / "runs" / run_id / "manifests/run-manifest.json"


def run_root(run_id=RUN_ID):
    return Path(rr.read_json(V6)["output_root"]) / "runs" / run_id


def answering_block():
    plan_v5 = rr.read_json(PANEL_PLAN)
    provider, budgets = plan_v5["provider"], plan_v5["budgets"]
    return {"model": provider["model"], "prompt_path": plan_v5["prompt_path"], "prompt_sha256": plan_v5["prompt_sha256"],
            "conditions": plan_v5["conditions"], "budgets": budgets,
            "max_input_tokens": provider["max_input_tokens_planning"],
            "max_output_tokens": provider["max_output_tokens_planning"],
            "agent_loop": "git/context/scripts/complex_task_agent_loop.py",
            "agent_loop_sha256": sha(CONTEXT / "scripts/complex_task_agent_loop.py"),
            "ra_model_turn_sha256": sha(rr.RA / "retrieval_arena/model_turn.py"),
            "agent_model_provider_sha256": sha(CONTEXT / "scripts/agent_model_provider.py"),
            "retry_rule": "a recorded 429 rate-limit trace: archive it, wait 60 s, retry the cell at most twice; an "
                          "infrastructure failure (model_api_error) is archived with archive-infra and rerun; other "
                          "terminals stay in the denominator",
            "datasets": "blind export variant folders (corpus/ + graph_edges.csv), opaque ids",
            "review_label": REVIEW_LABEL}


def build_plan(m_sha, t_sha, manifest_file):
    import answer_quality_scorer as aqs
    import answer_quality_scorer_luna as aql
    qs = rr.read_json(QUESTIONS_11B)
    a = answering_block()
    q_ids = [q["question_id"] for q in qs["questions"]]
    if len({q[:12] for q in q_ids}) != len(q_ids):
        raise Refused("question-id-prefix-collision")
    cells = schedule(q_ids, [c["id"] for c in a["conditions"]])
    per_rep_calls = len(cells) // REPLICATES_PER_CELL * a["budgets"]["model_calls"]
    per_call = (a["max_input_tokens"] * 0.125 + a["max_output_tokens"] * 0.50) / 1e6
    luna = aql.LunaConfig(authorization=REVIEW_LABEL)
    variants = [f"{arm}-r{i:02d}" for arm in STRUCTURE_ARMS for i in range(1, REALIZATIONS + REPLACEMENTS_PER_ARM + 1)]
    order = [f"{arm}-r{i:02d}" for arm in STRUCTURE_ARMS for i in range(1, REALIZATIONS + 1)]
    random.Random(EDIT_ORDER_SEED).shuffle(order)
    return {
        "schema_version": "docspectra.icsc.structure_forcing_12.v1.plan", "created": DATE, "run_id": RUN_ID,
        "design": {"minimum_plan": {"path": "git/context/design/" + DESIGN.name, "sha256": sha(DESIGN)},
                   "senior_review": {"path": "git/context/reviews/" + REVIEW.name, "sha256": sha(REVIEW)}},
        "run_manifest": {"path": str(manifest_file), "sha256": m_sha},
        "arm_templates": {"path": "git/context/evidence/" + TEMPLATES_V4.name, "sha256": t_sha},
        "author_decisions": {
            "recorded": "author in chat, 2026-10-09, before the freeze (Sprint 12 session)",
            "arm_set": "LT, MT, MR (4 realizations each), RN nulls, O, S, BD, LT-BD; inverted and disconnected "
                       "arms dropped (author, October 8)",
            "answers": "the 12 admitted 100% endpoints plus O and S; 2 replicates per cell; Luna scorer v2 with "
                       "k = 2 passes, aggregated; $60 hard stop",
            "question_set": "(i): the five 11b development questions, reused with the disclosure that three scorer "
                            "configurations (11e, 11f, 11h) were chosen on answers to them; the editor never sees them",
            "dose_topic": DOSE_TOPIC + " (optional dose stage, only after steps 6-8 and the author's go)",
            "paper": "every paper decision (framing, CIKM backbone) is deferred until this evidence exists",
        },
        "arms": {"O": 1, "S": 1, **{arm: REALIZATIONS for arm in STRUCTURE_ARMS},
                 "replacement": ADMISSION_RULE["replacement"]},
        "edit_order": {"seed": EDIT_ORDER_SEED, "order": order,
                       "then": "O and S first (no editor), then this order; replacements after all 12"},
        "admission": ADMISSION_RULE,
        "measures": {
            "vector": list(MEASURES),
            "definitions": {
                "normalized_lambda2": "09c normalized_lambda2 (undirected, binary symmetrized)",
                "magnetic_lambda1_q0.25": "09c spectra lambda1 at q = .25 (magnetic_matrix, w = .5(a + a^T), unnormalized)",
                "magnetic_lambda2_q0.25": "09c spectra lambda2 at q = .25",
                "subspace_polarization_v1": "09c spectra subspace_polarization_v1 value at q = .25 (None if not computed)",
                "reciprocal_edge_fraction": "share of directed edges whose reverse edge is present",
                "outdegree_gini": "09c degree_controls outdegree_gini", "sources": "09c degree_controls sources",
                "sinks": "09c degree_controls sinks"},
            "graph": "every measure on the largest weakly connected component of the graph in the view, as the pinned "
                     "full view does",
            "edit_magnitude_covariates": ["paths changed", "pages added", "pages removed", "edges added",
                                          "edges removed", "reciprocal fraction of added edges", "touched set size"],
        },
        "views": {
            "full": "retrieval_no_faq_lwcc as pinned in 09c (restructuring_runs.spectral_profile), merge mapping "
                    "applied: deltas are v minus O' (O's edges mapped into v's node space)",
            "subgraph": "S_v = touched set T + 1-hop neighbours of T in O' (either direction) + the 1-hop neighbours "
                        "in v of pages v added. T = pages v changed or added, plus the survivor of every merged-away "
                        "page. The same S_v for v, its 5 nulls and O'; each graph is induced on S_v and measured on "
                        "the largest weakly connected component of the induced graph. Every comparison is a "
                        "per-variant delta (graph minus O' on S_v); raw subgraph spectra are never compared across "
                        "variants",
            "visibility": "edges from S_v to pages outside S_v are not in the subgraph view, for v and nulls alike; "
                          "the share of added edges inside S_v is reported per graph",
        },
        "signatures": {"table": SIGNATURES, "relative_to": "the matched null's median, not O",
                       "counted": "only 'up' and 'down'; 'flat' and 'down or flat' are not counted"},
        "null_rule": {
            "version": "sf12_random_edit_null_v1",
            "text": "For each editor endpoint v: node set = v's node set after merge mapping and page additions "
                    "(added pages exist as nodes with no content role); start from O' (O's author-link edges mapped "
                    "through v's merge mapping onto that node set); a = |E_v - E_O'|, r = |E_O' - E_v|, f = share "
                    "of v's added edges whose reverse is in E_v. Remove r edges uniformly among O' edges with both "
                    "endpoints in T; then add a edges uniformly among ordered non-edge pairs with at least one "
                    "endpoint in T (pairs just removed excluded), round(f*a) of them reciprocal (reverse already "
                    "present; any shortfall made up by mutual pairs added in both directions, at most one edge over).",
            "widening": "a pool too small for its count is widened once to W = T + its 1-hop neighbours in O' (both "
                        "endpoints in W for removals, at least one for additions), drawn uniformly from the smallest "
                        "level that fits, and recorded in the receipt; never silently dropped",
            "per_variant": NULLS_PER_VARIANT,
            "seed_rule": "int(sha256('icsc2027-12-rn:' + variant_id + ':' + k).hexdigest(), 16) for k = 1..5, as "
                         "Python random.Random seeds; variant_id is the namespace name (e.g. LT-r01)",
            "seeds_sha256_prefix": {v: [hashlib.sha256(str(null_seed(v, k)).encode()).hexdigest()[:16]
                                        for k in range(1, NULLS_PER_VARIANT + 1)] for v in variants},
        },
        "primary": {
            "claim": "A1: spectral descriptors distinguish documentation structure built for a purpose from random "
                     "edits of the same size",
            "statistic": "subgraph view, the admitted 100% endpoints (12 planned): d_v = v - O' on S_v and its 5 null "
                         "deltas; z_v per measure = (d_v - null mean) / null sd (ddof 1); between-arm sum of squares "
                         "of the z_v (sum over arms of n_arm * ||arm mean - grand mean||^2) over the measures defined "
                         "for every endpoint",
            "degenerate_rules": "null sd 0 -> the pooled within-variant null sd of that measure over all endpoints; "
                                "a measure still undefined (sd 0 everywhere, or a value missing) for any endpoint is "
                                "dropped from the statistic and recorded",
            "reference": f"{PERMUTATIONS} permutations of the arm labels, numpy default_rng seeded from "
                         f"sha256('{PERMUTATION_SEED_LABEL}'); p = (1 + #{{T_perm >= T_obs}}) / (1 + {PERMUTATIONS})",
            "threshold": f"p < {ALPHA}", "only_confirmatory_test": True,
        },
        "descriptives": [
            "per arm and measure: endpoints whose d_v lies on the signature's side of the null median and outside "
            "the null's central 90% (numpy percentile 5 and 95 over the 5 null deltas); subgraph view",
            "the same tally on the full view",
            "edit-magnitude table beside every table (covariate for the reader, no adjustment)",
            "share of added edges inside S_v for v and its nulls",
            "full-view normalized lambda2 change range across endpoints",
        ],
        "checks": {"sham_identity": "S blind-export bytes = O blind-export bytes; fails the run if not",
                   "BD": "O with every edge mirrored; full view; the magnetic spectrum at every q in the 09c sweep "
                         f"equals the real Laplacian spectrum of the all-ones symmetrization within {BD_TOLERANCE}",
                   "LT-BD": "the same check on each admitted LT endpoint mirrored",
                   "f_reference": "O's full-view receipt equals the 09c F reference exactly (f_reference_check)"},
        "answers": {
            "variants": "the 12 admitted endpoints, O and S (14)", "replicates": REPLICATES_PER_CELL,
            "questions": {"path": "git/context/evidence/" + QUESTIONS_11B.name, "sha256": sha(QUESTIONS_11B),
                          "ids": q_ids},
            "answering": a,
            "schedule": {"seed": SCHEDULE_SEED, "cells": cells, "count": len(cells),
                         "slot_rule": "slot <arm>-<i> = the arm's i-th admitted endpoint in launch order "
                                      "(replacement last); O and S are their own slots"},
            "ledgers": {"answers": f"one ledger per replicate, estimated-charge stop ${ANSWER_LEDGER_STOP_USD} each "
                                   f"({per_rep_calls} campaign calls; worst case ${per_rep_calls * per_call:.2f}; "
                                   "restructuring_runs' CostLedger caps a ledger at $40)",
                        "scorer": "one ledger per worker; each worker's stop = min(40, (60 - everything reserved in "
                                  "the answer ledgers) / 2), fixed when scoring starts",
                        "hard_stop_usd": HARD_STOP_USD,
                        "deviation": "the sprint asks for one shared ledger; the pinned CostLedger refuses limits above "
                                     "$40 and the agent loop refuses a campaign whose worst case exceeds its stop, so "
                                     "the $60 stop is enforced as the sum of reservations over four ledgers"},
            "scorer": {"model": luna.model,
                       "config": dict(luna.as_dict(), limit_usd="per worker, from the scorer ledger rule"),
                       "prompt_sha256": sha(aqs.PROMPT),
                       "schema_sha256": sha(aqs.SCHEMA), "base_module_sha256": sha(aqs.__file__),
                       "luna_module_sha256": sha(aql.__file__),
                       "passes": {"A": {"label_seed": SCORER_SEEDS["A"], "notes_seed": None},
                                  "B": {"label_seed": SCORER_SEEDS["B"], "notes_seed": None}},
                       "k": 2, "workers": 2},
            "rubric": {"model": "qwen3:14b (local Ollama, think off)", "prompt": "git/context/design/complex-task-answer-judge-prompt-v1.txt",
                       "prompt_sha256": sha(CONTEXT / "design/complex-task-answer-judge-prompt-v1.txt"),
                       "score": "relaxed quote match (rubric_quote_match.quote_match_relaxed_v1), satisfied core "
                                "elements / 5", "role": "secondary, disclosed"},
            "endpoint": "per measure (correctness leading, then fit, completeness, caveats, clarity): the score "
                        "averaged over the 2 passes, then over the 2 replicates per cell; arm mean per question over "
                        "its realizations and conditions; arm - O per question, averaged over questions, with a "
                        f"bootstrap interval ({BOOTSTRAP['replicates']} replicates, seed {BOOTSTRAP['seed']}) "
                        "resampling questions (paired) and realizations within the arm; S - O in every table beside "
                        "each arm - O; no significance claim",
            "expectation": "exploratory; with 5 questions B will very likely show arm - O no larger than S - O",
        },
        "outcome_table": {
            "A1 rejects and signature agreement is high": "spectra distinguish purposeful structure from random edits "
                                                          "of the same size; the pre-declared signatures hold",
            "A1 rejects but signatures disagree": "spectra register purposeful structure, but not in the predicted "
                                                  "directions; report observed directions, do not rewrite signatures",
            "A1 does not reject": "one-session edits are not distinguishable from random edits of the same size by "
                                  "these descriptors; keep A3, move A1 to limitations",
            "full view also separates": "add the corpus-scale statement; otherwise say edits are local",
            "B arm - O no larger than S - O": "restructuring at this scale does not move answers detectably",
        },
        "disclosures": ["Luna self-grading", "no model snapshot (API reports gpt-6-luna only)", "no human graders",
                        "scorer v2 selected on the 11b pilot answers to these 5 questions",
                        "editor sees no metric, question, answer, score or other arm",
                        "11b variants, answers and judgments are not Sprint 12 realizations"],
        "dose": {"optional": True, "topic": DOSE_TOPIC,
                 "rule": "only after steps 6-8 and the author's go: one topic-only session per arm, admitted and "
                         "measured as the endpoints, with its own nulls; descriptive, no test"},
        "data_sent_out": ["the 1,218-page public Docker docs tree to Codex (editing)",
                          "public Docker docs excerpts and the 5 development questions to Luna (answering, scoring)"],
    }


def check_frozen(plan_path=PLAN):
    """Every stage after the freeze refuses on a changed plan, manifest or template."""
    plan_path = Path(plan_path)
    lock = plan_path.with_suffix(".sha256")
    if not plan_path.exists() or not lock.exists():
        raise Refused("plan-not-frozen")
    if sha(plan_path) != lock.read_text(encoding="ascii").split()[0]:
        raise Refused("plan-hash-changed-since-freeze")
    plan = rr.read_json(plan_path)
    if sha(plan["run_manifest"]["path"]) != plan["run_manifest"]["sha256"]:
        raise Refused("run-manifest-hash-changed-since-freeze")
    if sha(rr.ROOT / plan["arm_templates"]["path"]) != plan["arm_templates"]["sha256"]:
        raise Refused("arm-templates-hash-changed-since-freeze")
    return plan


def freeze(_args):
    if PLAN.exists() or manifest_path().exists():
        raise Refused("already-frozen")
    for arm in STRUCTURE_ARMS:
        path = prompt_path(arm)
        data = arm_prompt(R0_V4.read_bytes(), arm)
        if path.exists() and path.read_bytes() != data:
            raise Refused(f"prompt-file-differs:{path.name}")
        path.write_bytes(data)
    t_sha = write_or_same(TEMPLATES_V4, templates_v4())
    es = rr.load_sandbox()
    data = rr.build_manifest(
        run_id=RUN_ID, variant_manifest_path=V6, mode="live", created=DATE, templates_path=TEMPLATES_V4,
        runtime=runtime_block(es), model_identity="gpt-6.1-sol (Codex CLI 0.161.0, reasoning effort medium)",
        decided_limits={"model_calls": 1, "tool_calls": None, "tokens": 3_000_000, "wall_seconds": 3600, "edits": 150},
        stopping={"budget_stop_terminal_eligible_for_grading": False,
                  "author_decisions": "git/context/reviews/2026-10-08-author-decisions-editor-runtime.md; Sprint 12 "
                                      "decisions in the structure-forcing plan"})
    data["arm_plan"] = {"O": {"replicates": 1}, "S": {"replicates": 1},
                        **{arm: {"replicates": REALIZATIONS, "replacements_max": REPLACEMENTS_PER_ARM}
                           for arm in STRUCTURE_ARMS},
                        "R0": {"replicates": 0, "note": "not run in Sprint 12"},
                        "L": {"replicates": 0, "optional": True, "note": "not run in Sprint 12"}}
    data["allowed"]["scope"].update({arm: "restructure" for arm in STRUCTURE_ARMS})
    data["admission"] = ADMISSION_RULE
    data["cost_admission"] = {"authorization": "pending: explicit author yes in chat before any live call (step 5)",
                              "usd_cap": HARD_STOP_USD, "editor_billing": "ChatGPT subscription usage (no USD)"}
    data["evaluation"] = {"question_set": {"path": str(QUESTIONS_11B), "sha256": sha(QUESTIONS_11B),
                                           "role": "frozen Sprint 12 question set (author choice (i)), disclosed"},
                          "answer_conditions": answering_block()["conditions"],
                          "grading": "blind; opaque variant ids; key outside packet"}
    unresolved = rr.unresolved_paths(data)
    if unresolved:
        raise Refused("manifest-unresolved:" + ",".join(unresolved))
    m_sha = write_json(manifest_path(), data)
    rr.validate_manifest(rr.read_json(manifest_path()), live=True)
    plan = build_plan(m_sha, t_sha, manifest_path())
    p_sha = write_json(PLAN, plan)
    PLAN.with_suffix(".sha256").write_text(p_sha + "  " + PLAN.name + "\n", encoding="ascii")
    print(json.dumps({"plan_sha256": p_sha, "run_manifest_sha256": m_sha, "arm_templates_sha256": t_sha,
                      "prompts": {a: sha(prompt_path(a)) for a in STRUCTURE_ARMS},
                      "cells": plan["answers"]["schedule"]["count"], "edit_order": plan["edit_order"]["order"]},
                     indent=1))


AMENDMENTS_V2 = [
    {"field": "views.subgraph",
     "v1": "S_v = T + 1-hop neighbours of T in O' + the v-neighbours of added pages",
     "v2": "the v1 set plus every endpoint of an edge added or removed (against O') by v or any of its 5 nulls; still "
           "one node set common to v, its nulls and O'",
     "reason": "fixture v1: the LT stand-in's 6 added edges all fell inside the v1 set and its nulls' 0 of 6 (random "
               "targets lie elsewhere in the corpus), so every LT null delta was exactly 0; the view compared visible "
               "edits with invisible ones, and differently by arm (reciprocal null edges always land inside)"},
    {"field": "primary.statistic (standardization)",
     "v1": "z = (d_v - null mean) / the variant's own null sd (ddof 1), pooled sd only when that sd is 0",
     "v2": "z = (d_v - the variant's null mean) / the pooled within-variant null sd of the measure over all endpoints "
           "(ddof 1 per variant, root mean variance)",
     "reason": "fixture v1: with 5 nulls a per-variant sd can be near zero (MR stand-in magnetic lambda1: z = 577), "
               "so one measure of one endpoint set the between-arm sum of squares"},
]


def amend(_args):
    """Plan v2 from the frozen v1 with the author's two amendments (chat, 2026-10-09, after the zero-call fixture,
    before any model call); v1 stays on disk unchanged."""
    v1 = check_frozen(PLAN_V1)
    if PLAN.exists():
        raise Refused("plan-v2-already-written")
    if not FIXTURE_V1_EVIDENCE.exists():
        raise Refused("fixture-v1-evidence-missing")
    import copy
    v2 = copy.deepcopy(v1)
    v2["schema_version"] = "docspectra.icsc.structure_forcing_12.v2.plan"
    v2["supersedes"] = {"path": "git/context/evidence/" + PLAN_V1.name, "sha256": sha(PLAN_V1),
                        "fixture_evidence": {"path": "git/context/evidence/" + FIXTURE_V1_EVIDENCE.name,
                                             "sha256": sha(FIXTURE_V1_EVIDENCE)},
                        "decided_by": "author in chat, 2026-10-09, after the zero-model-call fixture and before any "
                                      "editor session, answer or score existed",
                        "amendments": AMENDMENTS_V2,
                        "unchanged": "null rule and seeds, measures, signatures, permutation test and threshold, "
                                     "admission rule, run manifest, prompts, answer plan, budgets"}
    head, tail = v1["views"]["subgraph"].split(". T = pages", 1)
    v2["views"]["subgraph"] = (head + " + every endpoint of an edge added or removed (against O') by v or any of its "
                               "5 nulls (plan v2). T = pages" + tail)
    v2["views"]["visibility"] = ("every edit of v and of its nulls is inside S_v (plan v2); the share of added edges "
                                 "inside the v1 node set is reported per graph as a locality descriptive")
    v2["primary"]["statistic"] = v1["primary"]["statistic"].replace(
        "z_v per measure = (d_v - null mean) / null sd (ddof 1)",
        "z_v per measure = (d_v - the variant's null mean) / the pooled within-variant null sd of the measure over "
        "all endpoints (ddof 1 per variant, root mean variance; plan v2)")
    v2["primary"]["degenerate_rules"] = ("a measure whose pooled null sd is 0, or with a value missing for any "
                                         "endpoint, is dropped from the statistic and recorded")
    v2["descriptives"] = [d.replace("share of added edges inside S_v", "share of added edges inside the v1 node set "
                                    "(locality)") for d in v1["descriptives"]]
    if v2["primary"]["statistic"] == v1["primary"]["statistic"]:
        raise Refused("amendment-text-not-applied")
    digest = write_json(PLAN, v2)
    PLAN.with_suffix(".sha256").write_text(digest + "  " + PLAN.name + "\n", encoding="ascii")
    print(json.dumps({"plan_v2_sha256": digest, "supersedes_v1": sha(PLAN_V1)}, indent=1))


# ------------------------------------------------------------------ admission and graphs per namespace

def namespace_graphs(gm, vm, ns):
    """(O' edges, v nodes, v edges, mapping receipt, touched, review) for an edited namespace's terminal tree."""
    state = rr.read_json(ns / "host/run-state.json")
    tree = Path(state["terminal_tree"])
    base = rr.baseline_pages(gm, vm)
    o_edges = canonical(gm.extract_markdown_edges(base))
    _, nodes, v_edges = rr.tree_graph(gm, tree, dcv.scope_of(vm))
    receipt = rr.read_json(ns / "host/receipts/mapping.json")
    om = mapped_edges(o_edges, receipt["mapping"], nodes)
    return om, nodes, canonical(v_edges), receipt, touched_set(receipt)


def pl2_normalized(review):
    old = Counter(x.strip() for x in review["protected_lines_removed"])
    new = Counter(x.strip() for x in review["protected_lines_added"])
    return sum((old - new).values())


def admission_record(ns):
    state = rr.read_json(ns / "host/run-state.json")
    rec = {k: state.get(k) for k in ("status", "flags", "terminal_checkpoint", "eligible_for_grading", "c_passed",
                                     "profile_sha256", "editor_model_calls", "editor_tool_calls", "editor_tokens",
                                     "editor_edits", "editor_wall_seconds", "editor_stop", "editor_errors",
                                     "editor_exit_code")}
    review_path = ns / "exports/preservation-review/review.json"
    review = rr.read_json(review_path) if review_path.exists() else None
    if review:
        rec["review"] = {"added": len(review["added"]), "removed": len(review["removed"]),
                         "changed": len(review["changed"]),
                         "edges_added_raw": len(review["edges_added"]), "edges_removed_raw": len(review["edges_removed"]),
                         "introduced_unresolved_internal_links": len(review["introduced_unresolved_internal_links"]),
                         "protected_lines_removed_raw": len(review["protected_lines_removed"]),
                         "protected_lines_removed_normalized": pl2_normalized(review)}
    infra = any(str(f).startswith("infrastructure_stop:") for f in state.get("flags", []))
    rec["infrastructure_stop"] = infra
    rec["admitted"] = bool(review and state["status"] == "completed" and state.get("c_passed") is True
                           and state.get("eligible_for_grading")
                           and rec["review"]["introduced_unresolved_internal_links"] == 0
                           and rec["review"]["protected_lines_removed_normalized"] == 0)
    return rec


# ------------------------------------------------------------------ edit

def launch_live(arm, replicate, run_id=RUN_ID):
    if not os.environ.get(rr.AUTH_ENV) and AUTH_DEFAULT.is_file():
        os.environ[rr.AUTH_ENV] = str(AUTH_DEFAULT)
    return rr.launch(manifest_path(run_id), arm, replicate, live=True)


def authorized():
    path = run_root() / "host-private/authorization.json"
    if not path.exists():
        raise Refused("no-author-authorization-recorded: step 5 needs an explicit yes before any live call")
    return rr.read_json(path)


def edit_queue(plan, root):
    """Planned namespaces not yet launched, then replacements an arm has earned."""
    todo = [(a, 1) for a in ("O", "S") if not (root / f"{a}-r01").exists()]
    for v in plan["edit_order"]["order"]:
        arm, rep = v.split("-r")
        if not (root / v).exists():
            todo.append((arm, int(rep)))
    if todo:
        return todo
    for arm in STRUCTURE_ARMS:
        recs = [admission_record(root / f"{arm}-r{i:02d}") for i in range(1, REALIZATIONS + 1)]
        repl = f"{arm}-r{REALIZATIONS + 1:02d}"
        if not all(r["admitted"] or r["infrastructure_stop"] for r in recs) and not (root / repl).exists():
            todo.append((arm, REALIZATIONS + 1))
    return todo


def edit(args):
    plan = check_frozen()
    authorized()
    root = run_root()
    if args.next:
        queue = edit_queue(plan, root)
        if not queue:
            print(json.dumps({"edit": "nothing-left"}))
            return
        targets = queue[:1] if not args.all else queue
    else:
        targets = [(args.arm, args.replicate)]
    for arm, rep in targets:
        state = launch_live(arm, rep)
        ns = root / f"{arm}-r{rep:02d}"
        rec = admission_record(ns) if arm in STRUCTURE_ARMS else {"status": state["status"]}
        print(json.dumps({f"{arm}-r{rep:02d}": rec}, indent=1), flush=True)


# ------------------------------------------------------------------ admit

def endpoints_by_slot(root):
    """Slot -> namespace name: each arm's admitted endpoints in launch order (planned first, replacement last)."""
    out = {"O": "O-r01", "S": "S-r01"}
    shortfall = {}
    for arm in STRUCTURE_ARMS:
        admitted = [f"{arm}-r{i:02d}" for i in range(1, REALIZATIONS + REPLACEMENTS_PER_ARM + 1)
                    if (root / f"{arm}-r{i:02d}").exists() and admission_record(root / f"{arm}-r{i:02d}")["admitted"]]
        for i, name in enumerate(admitted[:REALIZATIONS], 1):
            out[f"{arm}-{i}"] = name
        shortfall[arm] = max(0, REALIZATIONS - len(admitted))
    return out, shortfall


def admit_run(run_id, plan, evidence_path, *, gm, vm):
    root = run_root(run_id)
    records = {p.name: admission_record(p) for p in sorted(root.glob("*-r[0-9][0-9]"))
               if p.name.split("-r")[0] in STRUCTURE_ARMS}
    magnitude = {}
    for name, rec in records.items():
        if rec.get("review") is None:
            continue
        om, nodes, ve, receipt, T = namespace_graphs(gm, vm, root / name)
        mag = edit_magnitude(om, ve, T)
        magnitude[name] = {"paths_changed": rec["review"]["added"] + rec["review"]["removed"] + rec["review"]["changed"],
                           "pages_added": len(receipt["added"]), "pages_removed": len(receipt["removed"]),
                           "edges_added": mag["edges_added"], "edges_removed": mag["edges_removed"],
                           "reciprocal_fraction_added": mag["reciprocal_fraction_added"], "touched": len(T),
                           "unmapped_removals": len(receipt["unmapped_removals"]),
                           "partial_unit_loss": len(receipt["partial_unit_loss"])}
    slots_map, shortfall = endpoints_by_slot(root)
    namespaces = [root / slots_map[s] for s in sorted(slots_map)]
    dest, key_path = root / "grading-export", root / "host-private/grading-key.json"
    grading = rr.grading_export(manifest_path(run_id), namespaces, dest, key_path)
    key = rr.read_json(key_path)
    by_ns = {f"{v['arm']}-r{v['replicate']:02d}": k for k, v in key["variants"].items()}
    sham = rr.tree_bytes(dest / "variants" / by_ns["O-r01"]) == rr.tree_bytes(dest / "variants" / by_ns["S-r01"])
    o_state = rr.read_json(root / "O-r01/host/run-state.json")
    f_ref = rr.f_reference_check(rr.read_json(root / "O-r01/host/receipts/spectral-profile.json"))
    out = {"schema_version": "docspectra.icsc.structure_forcing_12.v1.admission", "run_id": run_id,
           "plan_sha256": sha(PLAN), "admission_rule": ADMISSION_RULE, "records": records,
           "edit_magnitude": magnitude, "slots": slots_map, "shortfall": shortfall,
           "grading_export": grading, "sham_identity": sham, "O_status": o_state["status"], "f_reference": f_ref,
           "note": "opaque variant ids; the key stays in host-private and is read again only by analyse-answers"}
    return out, write_json(evidence_path, out)


def admit(_args):
    plan = check_frozen()
    path = EVIDENCE / f"structure-forcing-12-admission-v1-{today()}.json"
    if list(EVIDENCE.glob("structure-forcing-12-admission-v1-*.json")):
        raise Refused("already-admitted")
    if edit_queue(plan, run_root()):
        raise Refused("editing-not-finished")
    vm = rr.read_json(V6)
    out, digest = admit_run(RUN_ID, plan, path, gm=dcv.pinned(vm), vm=vm)
    print(json.dumps({"sha256": digest, "sham_identity": out["sham_identity"], "shortfall": out["shortfall"],
                      "slots": out["slots"], "f_reference_exact": out["f_reference"]["lambda1_exact"] and
                      out["f_reference"]["normalized_lambda2_exact"]}, indent=1))


def today():
    import datetime
    return datetime.date.today().isoformat()


# ------------------------------------------------------------------ controls and spectra

def controls_run(run_id, admission, *, gm, vm, log=print):
    """Nulls, BD and LT-BD graphs as edge files with receipts under <run>/controls/."""
    root = run_root(run_id)
    out = root / "controls"
    base = rr.baseline_pages(gm, vm)
    o_nodes = sorted(p.doc_id for p in base)
    o_edges = canonical(gm.extract_markdown_edges(base))
    endpoints = [n for s, n in admission["slots"].items() if s not in ("O", "S")]
    for name in sorted(endpoints):
        om, nodes, ve, receipt, T = namespace_graphs(gm, vm, root / name)
        mag = edit_magnitude(om, ve, T)
        for k in range(1, NULLS_PER_VARIANT + 1):
            path = out / name / f"rn-{k}.json"
            if path.exists():
                continue
            edges, rec = random_null(nodes, om, T, mag["edges_added"], mag["edges_removed"],
                                     mag["reciprocal_fraction_added"], null_seed(name, k))
            write_json(path, {"graph_id": f"{name}/rn-{k}", "variant_id": name, "k": k, "rule": "sf12_random_edit_null_v1",
                              "receipt": rec, "nodes": nodes, "edges": edges})
            log(f"{name}/rn-{k} widened={rec['widened']}")
    bd = out / "BD.json"
    if not bd.exists():
        write_json(bd, {"graph_id": "BD", "from": "O", "nodes": o_nodes, "edges": mirror(o_edges)})
    for name in sorted(n for n in endpoints if n.startswith("LT-")):
        path = out / f"{name}-BD.json"
        if not path.exists():
            _, nodes, ve, _, _ = namespace_graphs(gm, vm, root / name)
            write_json(path, {"graph_id": f"{name}-BD", "from": name, "nodes": nodes, "edges": mirror(ve)})
    marker = {"nulls": sum(1 for _ in out.glob("*/rn-*.json")), "endpoints": len(endpoints)}
    return marker


def spectra_run(run_id, admission, *, gm, vm, log=print):
    """Full-view receipts (pinned spectral_profile) and subgraph measures for every graph; resumable per graph."""
    o9, mp = rr.load_spectral()
    manifest = rr.read_json(manifest_path(run_id))
    anchors, pin = manifest["c_pin"]["anchors"], manifest["spectral_pin"]
    root = run_root(run_id)
    out = root / "spectra"
    base = rr.baseline_pages(gm, vm)
    o_edges = canonical(gm.extract_markdown_edges(base))
    t0 = time.time()

    def full(gid, nodes, edges, mapping=None):
        path = out / "full" / f"{gid.replace('/', '__')}.json"
        if not path.exists():
            write_json(path, rr.spectral_profile(nodes, edges, anchors, mapping, pin))
            log(f"full {gid} {time.time() - t0:.0f}s")
        return rr.read_json(path)

    full("O", sorted(p.doc_id for p in base), o_edges)
    s_state = rr.read_json(root / "S-r01/host/run-state.json")
    _, s_nodes, s_edges = rr.tree_graph(gm, Path(s_state["terminal_tree"]), dcv.scope_of(vm))
    full("S", s_nodes, s_edges)
    endpoints = sorted(n for s, n in admission["slots"].items() if s not in ("O", "S"))
    for name in endpoints:
        om, nodes, ve, receipt, T = namespace_graphs(gm, vm, root / name)
        mapping = receipt["mapping"]
        graphs = [(f"{name}/O-prime", om), (name, ve)]
        for k in range(1, NULLS_PER_VARIANT + 1):
            graphs.append((f"{name}/rn-{k}", edges_of(root / "controls" / name / f"rn-{k}.json")))
        core = set(subgraph_nodes(T, om, ve, receipt["added"]))  # the plan v1 node set, kept as a descriptive
        S = subgraph_nodes_v2(T, om, ve, receipt["added"], [e for _, e in graphs[2:]])
        sub_path = out / "subgraph" / f"{name}.json"
        sub = rr.read_json(sub_path) if sub_path.exists() else None
        for gid, edges in graphs:
            full(gid, nodes, edges, mapping)
        if sub is None:
            added_v = set(ve) - set(om)
            rows = {}
            for gid, edges in graphs:
                ind = induced(edges, S)
                added = sorted(set(edges) - set(om))
                rows[gid] = dict(measure_vector(o9, mp, S, ind), added_edges=len(added),
                                 added_inside_S=sum(1 for e in added if e in set(ind)),
                                 added_inside_v1_core=sum(1 for a, b in added if a in core and b in core))
            write_json(sub_path, {"variant_id": name, "S_nodes": len(S), "v1_core_nodes": len(core),
                                  "S_sha256": sha_text(json.dumps(S)),
                                  "touched": len(T), "v_added_edges": len(added_v), "graphs": rows})
            log(f"subgraph {name} |S|={len(S)} {time.time() - t0:.0f}s")
    bd = rr.read_json(root / "controls/BD.json")
    checks = {"BD": bd_identity(mp, *lwcc_of(o9, mp, bd["nodes"], bd["edges"]), rr.Q_SWEEP)}
    for path in sorted((root / "controls").glob("LT-r*-BD.json")):
        g = rr.read_json(path)
        checks[g["graph_id"]] = bd_identity(mp, *lwcc_of(o9, mp, g["nodes"], g["edges"]), rr.Q_SWEEP)
    return checks


def edges_of(path):
    """Edge list of a control graph file as tuples (JSON stores pairs as lists)."""
    return [tuple(e) for e in rr.read_json(path)["edges"]]


def lwcc_of(o9, mp, nodes, edges):
    nodes = sorted(nodes)
    es = mp.canonical_edges(nodes, edges)
    lw = sorted(o9.weak_components(nodes, es)[0])
    ls = set(lw)
    return lw, [(s, t) for s, t in es if s in ls and t in ls]


def full_vector(receipt, edges_lwcc_recip):
    lw = receipt["profile"]["retrieval_no_faq_lwcc"]
    sp = lw["spectra"]
    return {"normalized_lambda2": lw["normalized_undirected_lambda2"],
            "magnetic_lambda1_q0.25": sp["0.25"]["lambda1"], "magnetic_lambda2_q0.25": sp["0.25"]["lambda2"],
            "subspace_polarization_v1": sp["subspace_polarization_v1"].get("value"),
            "reciprocal_edge_fraction": edges_lwcc_recip,
            "outdegree_gini": lw["controls"]["outdegree_gini"], "sources": lw["controls"]["sources"],
            "sinks": lw["controls"]["sinks"]}


def analyse_spectra_run(run_id, admission, checks, *, gm, vm):
    o9, mp = rr.load_spectral()
    root = run_root(run_id)
    out = root / "spectra"
    slots_map = admission["slots"]
    endpoints = sorted(n for s, n in slots_map.items() if s not in ("O", "S"))

    def fv(gid, nodes, edges):
        rec = rr.read_json(out / "full" / f"{gid.replace('/', '__')}.json")
        lw, le = lwcc_of(o9, mp, nodes, edges)
        return full_vector(rec, reciprocal_fraction(le))

    views = {"subgraph": [], "full": []}
    lambda2_change = []
    for name in endpoints:
        om, nodes, ve, receipt, T = namespace_graphs(gm, vm, root / name)
        arm = name.split("-r")[0]
        sub = rr.read_json(out / "subgraph" / f"{name}.json")["graphs"]
        null_ids = [f"{name}/rn-{k}" for k in range(1, NULLS_PER_VARIANT + 1)]
        views["subgraph"].append({"variant_id": name, "arm": arm,
                                  "d": delta(sub[name], sub[f"{name}/O-prime"]),
                                  "nulls": [delta(sub[g], sub[f"{name}/O-prime"]) for g in null_ids],
                                  "inside_v1_core": {"v": [sub[name]["added_inside_v1_core"], sub[name]["added_edges"]],
                                                     "nulls": [[sub[g]["added_inside_v1_core"], sub[g]["added_edges"]]
                                                               for g in null_ids]}})
        base_v = fv(f"{name}/O-prime", nodes, om)
        null_edges = [edges_of(root / "controls" / name / f"rn-{k}.json")
                      for k in range(1, NULLS_PER_VARIANT + 1)]
        v_vec = fv(name, nodes, ve)
        views["full"].append({"variant_id": name, "arm": arm, "d": delta(v_vec, base_v),
                              "nulls": [delta(fv(g, nodes, e), base_v) for g, e in zip(null_ids, null_edges)]})
        o_full = rr.read_json(out / "full" / "O.json")["profile"]["retrieval_no_faq_lwcc"]["normalized_undirected_lambda2"]
        lambda2_change.append({"variant_id": name, "relative": v_vec["normalized_lambda2"] / o_full - 1})
    result = {}
    for view, rows in views.items():
        pooled = pooled_null_sd([r["nulls"] for r in rows])
        for r in rows:
            r["z"], r["z_rule"] = standardize(r["d"], r["nulls"], pooled)
        result[view] = {"pooled_null_sd": pooled, "endpoints": rows, "signature_tally": signature_tally(rows),
                        "primary_form_statistic": primary_statistic([{"variant_id": r["variant_id"], "arm": r["arm"],
                                                                      "z": r["z"]} for r in rows])}
    return {"primary": dict(result["subgraph"]["primary_form_statistic"], view="subgraph",
                            label="the one confirmatory A1 test"),
            "full_view_statistic_descriptive": result["full"]["primary_form_statistic"],
            "views": result, "checks": checks, "full_view_normalized_lambda2_relative_change": lambda2_change,
            "edit_magnitude": admission.get("edit_magnitude")}


def generate_controls(_args):
    check_frozen()
    admission = latest_admission()
    marker = run_root() / "controls/complete.json"
    if marker.exists():
        raise Refused("controls-already-generated")
    vm = rr.read_json(V6)
    write_json(marker, controls_run(RUN_ID, admission, gm=dcv.pinned(vm), vm=vm))
    print(marker.read_text(encoding="utf-8"))


def latest_admission():
    found = sorted(EVIDENCE.glob("structure-forcing-12-admission-v1-*.json"))
    if len(found) != 1:
        raise Refused("admission-missing-or-ambiguous")
    return rr.read_json(found[0])


def spectra(_args):
    check_frozen()
    admission = latest_admission()
    marker = run_root() / "spectra/complete.json"
    if marker.exists():
        raise Refused("spectra-already-complete")
    if not (run_root() / "controls/complete.json").exists():
        raise Refused("controls-not-generated")
    vm = rr.read_json(V6)
    checks = spectra_run(RUN_ID, admission, gm=dcv.pinned(vm), vm=vm, log=lambda s: print(s, flush=True))
    write_json(marker, {"checks": checks})
    print(json.dumps(checks, indent=1))


def analyse_spectra(_args):
    check_frozen()
    if list(EVIDENCE.glob("structure-forcing-12-spectra-v1-*.json")):
        raise Refused("spectra-already-analysed")
    admission = latest_admission()
    checks = rr.read_json(run_root() / "spectra/complete.json")["checks"]
    vm = rr.read_json(V6)
    res = analyse_spectra_run(RUN_ID, admission, checks, gm=dcv.pinned(vm), vm=vm)
    res.update(schema_version="docspectra.icsc.structure_forcing_12.v1.spectra", plan_sha256=sha(PLAN),
               admission_sha256=sha(sorted(EVIDENCE.glob("structure-forcing-12-admission-v1-*.json"))[0]))
    path = EVIDENCE / f"structure-forcing-12-spectra-v1-{today()}.json"
    digest = write_json(path, res)
    md = path.with_suffix(".md")
    md.write_text(spectra_markdown(res, digest), encoding="utf-8")
    print(json.dumps({"sha256": digest, "primary": res["primary"],
                      "bd": {k: v["passes"] for k, v in checks.items()}}, indent=1))


def fmt(x, nd=3):
    return "—" if x is None else (f"{x:+.{nd}f}" if isinstance(x, float) else str(x))


def spectra_markdown(res, digest):
    p = res["primary"]
    lines = [f"# Sprint 12 spectra (evidence sha256 `{digest}`)", "",
             f"**Primary A1 (subgraph view, {p['endpoints']} endpoints):** statistic {fmt(p['statistic'])}, "
             f"permutation p = {fmt(p['p'], 4)} ({p.get('permutations')} permutations); measures used: "
             f"{', '.join(p['measures_used'])}; dropped: {', '.join(p['measures_dropped']) or 'none'}.", ""]
    for view in ("subgraph", "full"):
        tally = res["views"][view]["signature_tally"]
        lines += [f"## Signature agreement, {view} view (descriptive)", "",
                  "| Measure | " + " | ".join(STRUCTURE_ARMS) + " |", "|---|" + "---|" * len(STRUCTURE_ARMS)]
        for m, row in tally.items():
            cells = [f"{c['prediction']}: {c['agree_and_outside_central_90']}/{c['endpoints']}" if c["counted"]
                     else f"{c['prediction']} (not counted)" for c in (row[a] for a in STRUCTURE_ARMS)]
            lines.append(f"| {m} | " + " | ".join(cells) + " |")
        lines += ["", f"### Null-standardized deltas, {view} view", "",
                  "| Endpoint | " + " | ".join(MEASURES) + " |", "|---|" + "---|" * len(MEASURES)]
        for r in res["views"][view]["endpoints"]:
            lines.append(f"| {r['variant_id']} | " + " | ".join(fmt(r["z"][m], 2) for m in MEASURES) + " |")
        lines.append("")
    if res.get("edit_magnitude"):
        keys = ("paths_changed", "pages_added", "pages_removed", "edges_added", "edges_removed",
                "reciprocal_fraction_added", "touched")
        lines += ["## Edit magnitude", "", "| Endpoint | " + " | ".join(keys) + " |", "|---|" + "---|" * len(keys)]
        for name, mag in sorted(res["edit_magnitude"].items()):
            lines.append(f"| {name} | " + " | ".join(fmt(mag[k], 3) if isinstance(mag[k], float) else str(mag[k])
                                                     for k in keys) + " |")
        lines.append("")
    lines += ["## BD checks", "", "| Graph | worst |Δλ| | passes |", "|---|---|---|"]
    for k, v in res["checks"].items():
        lines.append(f"| {k} | {v['worst']:.2e} | {v['passes']} |")
    rng = [x["relative"] for x in res["full_view_normalized_lambda2_relative_change"]]
    if rng:
        lines += ["", f"Full-view normalized λ₂ relative change across endpoints: {min(rng):+.4%} to {max(rng):+.4%}."]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ fixture (zero model calls)

def fixture_batches(gm, vm, arm):
    """A small synthetic stand-in edit per arm on the v6 baseline (manuals/engine/network area), as shell batches."""
    import restructuring_fixture_e2e_10 as e10
    from docspectra_importer.retrieval_arena_export import _route_key
    base = rr.baseline_pages(gm, vm)
    by_rel = {p.rel_path: p for p in base}
    rels = set(by_rel)
    routes = {_route_key(r): r for r in sorted(rels, reverse=True)}
    area = sorted(r for r in rels if "/manuals/engine/network/" in r and not Path(r).name.startswith("_index"))
    if len(area) < 8:
        raise Refused("fixture-area-too-small")
    pick = area[:8]

    def link(src, dst):
        target = os.path.relpath(dst, os.path.dirname(src)).replace("\\", "/")
        if e10.resolve_target(target, src, rels | {dst}, routes) != dst:
            raise Refused(f"fixture-link-unresolved:{src}->{dst}")
        return target

    def text(rel):
        return by_rel[rel].text

    batches, plan = [], {}
    if arm == "LT":
        hub = (Path(pick[0]).parent / "shared-network-procedures.md").as_posix()
        hub_text = "---\ntitle: Shared network procedures\n---\n\nNavigation page for shared network procedures.\n"
        ops = [("write", hub, hub_text.encode())]
        for rel in pick[1:7]:
            ops.append(("write", rel, (text(rel).rstrip("\n") + f"\n\nSee [shared network procedures]({link(rel, hub)}).\n").encode()))
        batches, plan = [ops], {"hub": hub, "leaves": pick[1:7]}
    elif arm == "MT":
        ops = []
        for i, rel in enumerate(pick[:6]):
            targets = [pick[(i + j) % 8] for j in (2, 3, 5)]
            table = "\n".join(f"| [{Path(t).stem}]({link(rel, t)}) | context |" for t in targets)
            ops.append(("write", rel, (text(rel).rstrip("\n") + "\n\n| Context | Role |\n|---|---|\n" + table + "\n").encode()))
        batches, plan = [ops], {"pages": pick[:6]}
    else:  # MR
        pairs = [(pick[0], pick[1]), (pick[2], pick[3]), (pick[4], pick[5]), (pick[6], pick[7])]
        texts = {r: text(r) for r in pick}
        for a, b in pairs:
            texts[a] = texts[a].rstrip("\n") + f"\n\nRelated: [{Path(b).stem}]({link(a, b)}).\n"
            texts[b] = texts[b].rstrip("\n") + f"\n\nRelated: [{Path(a).stem}]({link(b, a)}).\n"
        batches, plan = [[("write", r, texts[r].encode()) for r in pick]], {"pairs": pairs}
    return batches, plan


def fixture(_args):
    plan = check_frozen()
    if FIXTURE_EVIDENCE.exists():
        raise Refused("fixture-already-run")
    es = rr.load_sandbox()
    vm = rr.read_json(V6)
    gm = dcv.pinned(vm)
    mpath = manifest_path(FIXTURE_RUN_ID)
    if not mpath.exists():
        data = rr.build_manifest(run_id=FIXTURE_RUN_ID, variant_manifest_path=V6, mode="fixture",
                                 fixture_limits=rr.FIXTURE_LIMITS, created=DATE, templates_path=TEMPLATES_V4,
                                 runtime=es.runtime_block(model=None, reasoning_effort=None, driver="scripted-shell-v1"))
        data["allowed"]["scope"].update({arm: "restructure" for arm in STRUCTURE_ARMS})
        data["stopping"]["budget_stop_terminal_eligible_for_grading"] = False
        write_json(mpath, data)
    root = run_root(FIXTURE_RUN_ID)
    t0 = time.time()
    edits = {}
    for arm in ("O", "S"):
        if not (root / f"{arm}-r01").exists():
            rr.launch(mpath, arm, 1)
    for arm in STRUCTURE_ARMS:
        batches, edit_plan = fixture_batches(gm, vm, arm)
        edits[arm] = edit_plan
        if not (root / f"{arm}-r01").exists():
            rr.launch(mpath, arm, 1, driver=es.ScriptedShellDriver(batches))
    stage_times = {"edit": round(time.time() - t0, 1)}
    # admission and grading export on the fixture run (slots filled with what exists)
    records = {f"{arm}-r01": admission_record(root / f"{arm}-r01") for arm in STRUCTURE_ARMS}
    magnitude = {}
    for name in records:
        om, nodes, ve, receipt, T = namespace_graphs(gm, vm, root / name)
        mag = edit_magnitude(om, ve, T)
        magnitude[name] = {k: mag[k] for k in ("edges_added", "edges_removed", "reciprocal_fraction_added", "touched")}
        magnitude[name].update(pages_added=len(receipt["added"]), pages_removed=len(receipt["removed"]))
    admission = {"slots": {"O": "O-r01", "S": "S-r01", **{f"{a}-1": f"{a}-r01" for a in STRUCTURE_ARMS}},
                 "edit_magnitude": magnitude}
    t1 = time.time()
    controls = controls_run(FIXTURE_RUN_ID, admission, gm=gm, vm=vm, log=lambda s: None)
    stage_times["generate-controls"] = round(time.time() - t1, 1)
    t1 = time.time()
    checks = spectra_run(FIXTURE_RUN_ID, admission, gm=gm, vm=vm, log=lambda s: print(s, flush=True))
    stage_times["spectra"] = round(time.time() - t1, 1)
    t1 = time.time()
    res = analyse_spectra_run(FIXTURE_RUN_ID, admission, checks, gm=gm, vm=vm)
    stage_times["analyse-spectra"] = round(time.time() - t1, 1)
    o_receipt = rr.read_json(root / "O-r01/host/receipts/spectral-profile.json")
    nulls = {name: [rr.read_json(root / "controls" / name / f"rn-{k}.json")["receipt"]
                    for k in range(1, NULLS_PER_VARIANT + 1)] for name in records}
    summary = {"schema_version": "docspectra.icsc.structure_forcing_12.v1.fixture", "date": today(),
               "model_calls": 0, "plan_sha256": sha(PLAN), "run_manifest": {"path": str(mpath), "sha256": sha(mpath)},
               "synthetic_edits": edits, "admission": records, "edit_magnitude": magnitude,
               "null_receipts": nulls, "controls": controls,
               "subgraph_example": res["views"]["subgraph"]["endpoints"][0],
               "full_example": res["views"]["full"]["endpoints"][0],
               "checks": checks, "primary_on_fixture": res["primary"],
               "full_view_statistic_on_fixture": res["full_view_statistic_descriptive"],
               "signature_tally_subgraph": res["views"]["subgraph"]["signature_tally"],
               "f_reference_check": rr.f_reference_check(o_receipt), "stage_seconds": stage_times,
               "scope": "tooling check on synthetic stand-in edits; zero model calls; not evidence about any arm"}
    digest = write_json(FIXTURE_EVIDENCE, summary)
    print(json.dumps({"sha256": digest, "admission": {k: {x: v.get(x) for x in ("status", "c_passed", "admitted",
                                                                                   "review")} for k, v in records.items()},
                      "edit_magnitude": magnitude, "primary": res["primary"],
                      "bd": {k: (v["worst"], v["passes"]) for k, v in checks.items()},
                      "f_reference": summary["f_reference_check"], "stage_seconds": stage_times}, indent=1))


# ------------------------------------------------------------------ answers

def answer(args):
    import subprocess
    import run_agent_search_factorial_v1 as panel
    plan = check_frozen()
    authorized()
    admission = latest_admission()
    if not admission["sham_identity"]:
        raise Refused("sham-identity-failed-no-answering")
    a = plan["answers"]["answering"]
    if sha(rr.ROOT / a["agent_loop"]) != a["agent_loop_sha256"] or \
            sha(rr.RA / "retrieval_arena/model_turn.py") != a["ra_model_turn_sha256"] or \
            sha(a["prompt_path"]) != a["prompt_sha256"] or \
            sha(CONTEXT / "scripts/agent_model_provider.py") != a["agent_model_provider_sha256"]:
        raise Refused("answer-runtime-drift")
    if not os.environ.get("OPENAI_API_KEY"):
        raise Refused("OPENAI_API_KEY-not-visible")
    key = rr.read_json(run_root() / "host-private/grading-key.json")
    opaque = {f"{v['arm']}-r{v['replicate']:02d}": k for k, v in key["variants"].items()}
    variant_of = {s: opaque[n] for s, n in admission["slots"].items()}
    qs = {q["question_id"]: q for q in rr.read_json(QUESTIONS_11B)["questions"]}
    conds = {c["id"]: c for c in a["conditions"]}
    datasets = run_root() / "grading-export/variants"
    ANSWERS.mkdir(parents=True, exist_ok=True)
    lock = ANSWERS / "plan.sha256"
    if lock.exists() and lock.read_text(encoding="ascii").strip() != sha(PLAN):
        raise Refused("answers-folder-belongs-to-another-plan")
    lock.write_text(sha(PLAN) + "\n", encoding="ascii")
    commands = ANSWERS / "commands.jsonl"
    prior = [json.loads(x) for x in commands.read_text(encoding="utf-8").splitlines()] if commands.exists() else []
    last_archive = {r["cell"]: i for i, r in enumerate(prior)
                    if r.get("event") in {"rate_limit_retry_archive", "infrastructure_attempt_archive"}}
    prior = [r for i, r in enumerate(prior) if i > last_archive.get(r.get("cell"), -1)]
    cells = [c for c in plan["answers"]["schedule"]["cells"] if c["slot"] in variant_of
             and (args.replicate is None or c["replicate"] == args.replicate)]
    per_rep = defaultdict(list)
    for c in cells:
        per_rep[c["replicate"]].append(c)
    calls = a["budgets"]["model_calls"]
    for rep, rows in sorted(per_rep.items()):
        ledger = ANSWERS / f"cost-ledger-r{rep}.jsonl"
        for index, cell in enumerate(rows):
            variant = variant_of[cell["slot"]]
            q, c = qs[cell["question_id"]], conds[cell["condition"]]
            dest = ANSWERS / (cell_stem(variant, q["question_id"], c["id"], rep) + ".json")
            if panel.completed_cell(prior, dest):
                continue
            cmd = cell_command(a, dataset=datasets / variant, question=q, dest=dest, ledger=ledger,
                               future_calls=(len(rows) - index - 1) * calls, campaign_calls=len(rows) * calls,
                               stop_usd=ANSWER_LEDGER_STOP_USD, condition=c)
            attempt = 1
            while True:
                panel.log(commands, {"event": "command_start", "cell": dest.stem, "attempt": attempt,
                                     "plan_sha256": sha(PLAN), "review_label": REVIEW_LABEL, "replicate": rep,
                                     "schedule_index": index})
                result = subprocess.run(cmd, cwd=rr.ROOT, capture_output=True, text=True,
                                        timeout=a["budgets"]["wall_seconds"] + 300)
                trace = json.loads(dest.read_text(encoding="utf-8")) if dest.exists() else None
                terminal = trace["terminal"] if trace else None
                panel.log(commands, {"event": "command_end", "cell": dest.stem, "attempt": attempt,
                                     "returncode": result.returncode, "terminal": terminal,
                                     "trace_sha256": sha(dest) if dest.exists() else None,
                                     "stderr": panel.redact(result.stderr[-1200:]),
                                     "stdout": panel.redact(result.stdout[-500:])})
                print(f"r{rep}", index + 1, dest.stem, terminal, "attempt", attempt, flush=True)
                if result.returncode == 0 and trace and panel.is_recorded_rate_limit(trace) and attempt < 3:
                    panel.archive_rate_limited_attempt(ANSWERS, dest, attempt, commands)
                    attempt += 1
                    time.sleep(60)
                    continue
                if result.returncode or terminal in {"model_api_error", "model_incomplete", "model_resource_error"}:
                    raise Refused("answer-transport-stopped:" + dest.stem)
                break


def archive_infra(args):
    import restructuring_pilot_11b as p11b
    saved = p11b.ANSWERS
    p11b.ANSWERS = ANSWERS
    try:
        p11b.archive_infra(args)
    finally:
        p11b.ANSWERS = saved


# ------------------------------------------------------------------ scoring, rubric, answer analysis

def answer_files():
    return sorted(p for p in ANSWERS.glob("v-*-r[0-9].json"))


def answer_inputs():
    answers, forbidden = [], set()
    for path in answer_files():
        trace = rr.read_json(path)
        variant = "-".join(path.stem.split("-")[:2])
        forbidden.update({path.name, path.stem, variant})
        if trace["terminal"] == "answer":
            answers.append({"answer_id": path.name, "question_id": trace["question_id"],
                            "cell": path.stem.split("-", 3)[3], "text": trace["answer"]["text"]})
    qs = {q["question_id"]: q for q in rr.read_json(QUESTIONS_11B)["questions"]}
    return answers, qs, forbidden


def scorer_ledger_stop():
    reserved = 0.0
    for ledger in ANSWERS.glob("cost-ledger-r*.jsonl"):
        for line in ledger.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("event") == "reserved_before_request":
                reserved += row["projected_usd"]
    return round(min(40.0, (HARD_STOP_USD - reserved) / 2), 4), reserved


def score(args):
    import answer_quality_scorer as aqs
    import answer_quality_scorer_luna as aql
    plan = check_frozen()
    authorized()
    stop_path = SCORER / "ledger-stop.json"
    if not stop_path.exists():
        stop, reserved = scorer_ledger_stop()
        write_json(stop_path, {"per_worker_stop_usd": stop, "answer_ledgers_reserved_usd": reserved,
                               "rule": plan["answers"]["ledgers"]["scorer"]})
    stop = rr.read_json(stop_path)["per_worker_stop_usd"]
    config = aql.LunaConfig(authorization=REVIEW_LABEL, limit_usd=stop)
    root = SCORER / f"pass-{args.pass_}"
    if not (root / "packet-manifest.json").exists():
        if args.worker != 0:
            raise Refused("worker-0-writes-the-packets")
        answers, qs, forbidden = answer_inputs()
        seeds = plan["answers"]["scorer"]["passes"][args.pass_]
        packets, key = aqs.build_packets(answers, qs, seeds["label_seed"], seeds["notes_seed"])
        aql.write_packets(root, packets, key, seeds["label_seed"], config, seeds["notes_seed"], forbidden,
                          run_label=f"sf12-{args.pass_}")
    transport = aql.LunaTransport(config, SCORER / f"cost-ledger-w{args.worker}.jsonl")
    manifest = rr.read_json(root / "packet-manifest.json")
    mine = manifest[args.worker::2]
    for retry in (False, True):
        for entry in mine:
            if retry and (root / "calls" / entry["packet_id"] / "completion.json").is_file():
                continue
            print(json.dumps(aql.call(root, entry, transport, retry=retry)), flush=True)


def rubric(_args):
    import subprocess
    check_frozen()
    packets = JUDGE / "packets"
    if not (JUDGE / "packet-manifest.json").exists():
        qs = {q["question_id"]: q for q in rr.read_json(QUESTIONS_11B)["questions"]}
        packets.mkdir(parents=True)
        manifest, aliases = [], {}
        for path in answer_files():
            trace = rr.read_json(path)
            if trace["terminal"] != "answer":
                continue
            text = trace["answer"]["text"]
            uid = hashlib.sha256(("sf12-judge|" + trace["question_id"] + "|" + text).encode()).hexdigest()
            aliases.setdefault(uid, []).append(path.name)
            if (packets / f"{uid}.json").exists():
                continue
            q = qs[trace["question_id"]]
            unit = {"unit_id": uid, "question": q["question"], "answer": text,
                    "elements": [{k: e[k] for k in ("element_id", "requirement", "acceptance")} for e in q["core_elements"]]}
            (packets / f"{uid}.json").write_text(json.dumps({"units": [unit]}, ensure_ascii=False, sort_keys=True) + "\n",
                                                 encoding="utf-8")
            manifest.append({"packet": f"{uid}.json", "sha256": sha(packets / f"{uid}.json"), "unit_id": uid})
        random.Random(SCHEDULE_SEED + 7).shuffle(manifest)
        (JUDGE / "packet-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (JUDGE / "aliases.json").write_text(json.dumps(aliases, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    subprocess.run([sys.executable, str(CONTEXT / "scripts/run_complex_task_local_judge.py"), "--root", str(JUDGE)],
                   cwd=rr.ROOT, check=True)


def answer_tables(cells, arms_of_variant, questions, measures, boot=BOOTSTRAP):
    """cells: {(variant, question, condition, replicate): {measure: score|None}} -> per-measure tables of
    arm - O and S - O (paired by question), with bootstrap intervals resampling questions and realizations."""
    import numpy as np
    by = defaultdict(list)  # (variant, question, condition) -> [scores per replicate]
    for (v, q, c, rep), s in cells.items():
        by[(v, q, c)].append(s)
    vq = defaultdict(list)  # (variant, question, measure) -> condition means (replicates averaged)
    for (v, q, c), reps in by.items():
        for m in measures:
            xs = [r[m] for r in reps if r.get(m) is not None]
            if xs:
                vq[(v, q, m)].append(sum(xs) / len(xs))
    variants_of = defaultdict(list)
    for v, arm in sorted(arms_of_variant.items()):
        variants_of[arm].append(v)

    def arm_q(arm, q, m, vs=None):
        xs = [x for v in (vs if vs is not None else variants_of[arm]) for x in vq.get((v, q, m), ())]
        return sum(xs) / len(xs) if xs else None

    rng = np.random.default_rng(boot["seed"])
    out = {}
    for m in measures:
        table = {}
        for arm in sorted(variants_of):
            if arm == "O":
                continue
            diffs = [arm_q(arm, q, m) - arm_q("O", q, m) for q in questions
                     if arm_q(arm, q, m) is not None and arm_q("O", q, m) is not None]
            est = float(np.mean(diffs)) if diffs else None
            draws = []
            for _ in range(boot["replicates"] if diffs else 0):
                qq = rng.choice(questions, size=len(questions), replace=True)
                vs = list(rng.choice(variants_of[arm], size=len(variants_of[arm]), replace=True))
                d = [arm_q(arm, q, m, vs) - arm_q("O", q, m) for q in qq
                     if arm_q(arm, q, m, vs) is not None and arm_q("O", q, m) is not None]
                if d:
                    draws.append(np.mean(d))
            ci = [float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))] if draws else None
            table[f"{arm} - O"] = {"estimate": est, "ci95": ci, "questions": len(diffs),
                                   "arm_mean": float(np.mean([arm_q(arm, q, m) for q in questions
                                                               if arm_q(arm, q, m) is not None])) if diffs else None}
        out[m] = table
    return out


def pass_scores(root):
    """{answer file name: {measure: score}} for one scorer pass (valid completions only)."""
    import answer_quality_scorer as aqs
    outputs, _ = aqs.load_scores(root)
    key = rr.read_json(Path(root) / "host-private/answer-key.json")
    return {key[label]["answer_id"]: {m: out[m]["score"] for m in aqs.MEASURES} for label, out in outputs.items()}


def analyse_answers(_args):
    import answer_quality_scorer as aqs
    import rubric_quote_match as rqm
    plan = check_frozen()
    if list(EVIDENCE.glob("structure-forcing-12-answers-v1-*.json")):
        raise Refused("answers-already-analysed")
    admission = latest_admission()
    passes = {p: pass_scores(SCORER / f"pass-{p}") for p in ("A", "B")}
    key = rr.read_json(run_root() / "host-private/grading-key.json")  # unblinding happens here
    arm_of = {k: v["arm"] for k, v in key["variants"].items()}
    measures = list(aqs.MEASURES)
    cells = {}
    usage = {"prompt_tokens": 0, "output_tokens": 0, "model_calls": 0, "invalid_actions": 0}
    terminals = Counter()
    for path in answer_files():
        trace = rr.read_json(path)
        stem = path.stem
        variant = "-".join(stem.split("-")[:2])
        rep = int(stem.rsplit("-r", 1)[1])
        cond = stem.split("-", 3)[3].rsplit("-r", 1)[0]
        terminals[trace["terminal"]] += 1
        for k, src in (("prompt_tokens", "prompt_tokens_reported"), ("output_tokens", "output_tokens_reported"),
                       ("model_calls", "model_calls"), ("invalid_actions", "invalid_actions")):
            usage[k] += int(trace["usage"].get(src) or 0)
        per_pass = [passes[p].get(path.name) for p in passes]
        cells[(variant, trace["question_id"], cond, rep)] = {
            m: (sum(s[m] for s in per_pass if s) / sum(1 for s in per_pass if s)) if any(per_pass) else None
            for m in measures}
    rubric_scores = rqm.score_judge_root(JUDGE)
    rubric_cells = {k: {"rubric_relaxed": (rubric_scores.get(f"{k[0]}-{k[1][:12]}-{k[2]}-r{k[3]}.json") or {}).get(
        "rubric_v1_relaxed")} for k in cells}
    qids = plan["answers"]["questions"]["ids"]
    variant_arm = {v: arm_of[v] for v in {k[0] for k in cells}}
    tables = answer_tables(cells, variant_arm, qids, measures)
    rubric_table = answer_tables(rubric_cells, variant_arm, qids, ["rubric_relaxed"])
    out = {"schema_version": "docspectra.icsc.structure_forcing_12.v1.answers", "plan_sha256": sha(PLAN),
           "scope": "exploratory; 5 questions; Luna self-grading; no snapshot; no human graders; scorer chosen on "
                    "the 11b answers; S - O is the yardstick", "terminals": dict(terminals),
           "answers": len(cells), "usage": usage, "scorer_v2": tables, "rubric_relaxed": rubric_table,
           "slots": admission["slots"]}
    path = EVIDENCE / f"structure-forcing-12-answers-v1-{today()}.json"
    print(json.dumps({"sha256": write_json(path, out), "terminals": dict(terminals), "usage": usage}, indent=1))


def explore_spectra(_args):
    """EXPLORATORY, not in the frozen plan (decided 2026-10-09 under the author's delegation, after the editing
    shortfall): the frozen controls/spectra/analysis code on every structure-arm session that completed with a
    terminal tree and passed C, admitted or not. Not confirmatory; never replaces the frozen analysis."""
    check_frozen()
    if not list(EVIDENCE.glob("structure-forcing-12-spectra-v1-*.json")):
        raise Refused("run-the-frozen-analysis-first")
    path = EVIDENCE / f"structure-forcing-12-spectra-exploratory-v1-{today()}.json"
    if path.exists():
        raise Refused("exploratory-already-written")
    admission = latest_admission()
    root = run_root()
    names, excluded = [], {}
    for p in sorted(root.glob("*-r[0-9][0-9]")):
        arm = p.name.split("-r")[0]
        if arm not in STRUCTURE_ARMS:
            continue
        rec = admission_record(p)
        if rec["status"] == "completed" and rec.get("c_passed") is True and rec.get("review"):
            names.append(p.name)
        else:
            excluded[p.name] = rec["status"]
    slots = {"O": "O-r01", "S": "S-r01", **{f"x-{n}": n for n in names}}
    pseudo = {"slots": slots}
    vm = rr.read_json(V6)
    gm = dcv.pinned(vm)
    controls_run(RUN_ID, pseudo, gm=gm, vm=vm, log=lambda s: print(s, flush=True))
    checks = spectra_run(RUN_ID, pseudo, gm=gm, vm=vm, log=lambda s: print(s, flush=True))
    magnitude = {}
    for n in names:
        rec = admission_record(root / n)
        om, nodes, ve, receipt, T = namespace_graphs(gm, vm, root / n)
        mag = edit_magnitude(om, ve, T)
        magnitude[n] = {"paths_changed": rec["review"]["added"] + rec["review"]["removed"] + rec["review"]["changed"],
                        "pages_added": len(receipt["added"]), "pages_removed": len(receipt["removed"]),
                        "edges_added": mag["edges_added"], "edges_removed": mag["edges_removed"],
                        "reciprocal_fraction_added": mag["reciprocal_fraction_added"], "touched": len(T),
                        "admitted": rec["admitted"]}
    pseudo["edit_magnitude"] = magnitude
    res = analyse_spectra_run(RUN_ID, pseudo, checks, gm=gm, vm=vm)
    res.update(schema_version="docspectra.icsc.structure_forcing_12.v1.spectra_exploratory", plan_sha256=sha(PLAN),
               scope="EXPLORATORY sensitivity, decided after the editing shortfall (Claude, under the author's "
                     "delegation, 2026-10-09): every structure-arm session that completed and passed C, including "
                     "LT endpoints that failed only the preservation rule; same frozen code, rules and seeds; not "
                     "the confirmatory A1 test", endpoints=names, excluded=excluded)
    digest = write_json(path, res)
    path.with_suffix(".md").write_text("> EXPLORATORY sensitivity, not the frozen A1 test.\n\n" +
                                       spectra_markdown(res, digest), encoding="utf-8")
    print(json.dumps({"sha256": digest, "endpoints": names, "primary_form": res["primary"]}, indent=1))


def dose_edit(_args):
    check_frozen()
    raise Refused("dose-stage-needs-the-author-go: steps 6-8 must be complete and the go recorded")


# ------------------------------------------------------------------ CLI

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="stage", required=True)
    sub.add_parser("freeze")
    sub.add_parser("amend")
    sub.add_parser("fixture")
    e = sub.add_parser("edit")
    e.add_argument("--arm", choices=("O", "S") + STRUCTURE_ARMS)
    e.add_argument("--replicate", type=int)
    e.add_argument("--next", action="store_true")
    e.add_argument("--all", action="store_true", help="with --next: run the whole remaining queue")
    sub.add_parser("admit")
    sub.add_parser("generate-controls")
    sub.add_parser("spectra")
    sub.add_parser("analyse-spectra")
    an = sub.add_parser("answer")
    an.add_argument("--replicate", type=int, choices=(1, 2))
    ai = sub.add_parser("archive-infra")
    ai.add_argument("cell")
    ai.add_argument("--reason", required=True)
    sc = sub.add_parser("score")
    sc.add_argument("--pass", dest="pass_", choices=("A", "B"), required=True)
    sc.add_argument("--worker", type=int, choices=(0, 1), required=True)
    sub.add_parser("rubric")
    sub.add_parser("analyse-answers")
    sub.add_parser("dose-edit")
    sub.add_parser("explore-spectra")
    args = parser.parse_args()
    if args.stage == "edit" and not args.next and (args.arm is None or args.replicate is None):
        parser.error("edit needs --arm and --replicate, or --next")
    {"freeze": freeze, "amend": amend, "fixture": fixture, "edit": edit, "admit": admit, "generate-controls": generate_controls,
     "spectra": spectra, "analyse-spectra": analyse_spectra, "answer": answer, "archive-infra": archive_infra,
     "score": score, "rubric": rubric, "analyse-answers": analyse_answers, "dose-edit": dose_edit, "explore-spectra": explore_spectra}[args.stage](args)


if __name__ == "__main__":
    main()

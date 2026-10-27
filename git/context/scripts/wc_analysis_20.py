"""Sprint 20: analysis of the whole-corpus refinement run. Zero model calls.

  python wc_analysis_20.py analyse    compute every table; write the evidence (write-once) and the generated tables
  python wc_analysis_20.py verify     recompute in memory and compare the bytes with the recorded evidence and tables

Reads only recorded outputs: Sprint 19 answers, Luna scorer passes, rubric, RAGAS and fixed-retriever rankings; the
tier-2 qrels before (verify) and after (verify-escalated) the D-P5 re-judge; HR-20; Sprint 17 records; Sprint 18
spectra. Unblinding happens here, through the answer key. Tables follow the frozen rule: one per tier, never pooled,
LT - O, R0 - O and LT - R0 with S - O beside every row; paired means over questions, bootstrap 95% intervals that
resample question clusters (duplicate-text pairs together). Everything is exploratory except the Sprint 18 test.

Per-question values: tier 1 averages replicates within a condition, then conditions (12b `answer_tables`); tier 2
averages its two conditions and is also reported per condition. Tier-2 rows (SA5, SA6, SA7, D-P5):
  primary          common 105 (D-P5), re-judged qrels, `undetermined` pages as misses (as recorded)
  sa5_hits         the same with `undetermined` pages as relevant (bound; Recall@5, RR@10 and navigation only)
  original_101     S19-4 set on the qrels before the re-judge (as Sprint 19 recorded it)
  strict_111       all 111, re-judged qrels; a question a variant still lost scores 0 on that variant
  lenient_o1_111   O1: C2 baseline gold with the credited equivalence (PROVENANCE_RULE), all 111
  o1_surviving_111 O1 with surviving-page credit only (identity plus moved-content survivor; no derived pages)
  sa7_without_6    the primary without the SA7 questions still in it
  historical_top5  the old automatic top-5 labels with O1 credit (historical comparison only, never a result)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wc_answers_19 as a19  # noqa: E402

rr, wr, sf, w17 = a19.rr, a19.wr, a19.sf, a19.w17
sha, Refused = a19.sha, a19.Refused
ROOT, EVIDENCE = a19.ROOT, a19.EVIDENCE
DATE = "2026-10-24"
RESULT = EVIDENCE / f"wc-analysis-20-v1-{DATE}.json"
TABLES = rr.CONTEXT / f"reports/{DATE}-wc-analysis-20-tables.md"
REJUDGE = EVIDENCE / "wc-rejudge-19-result-v1-2026-10-24.json"
RUN19 = a19.RECORD
RUN17 = EVIDENCE / "wc-refinement-run-v1-2026-10-18.json"
SPECTRA18 = EVIDENCE / "wc-spectra-18-v1-2026-10-19.json"
HR20 = EVIDENCE / "wc-hr20-verdicts-v1-2026-10-24.json"
HR20_KEY = ROOT / "artifacts/wc-hr20-19/host-private/key.json"
C2_QRELS = ROOT / "artifacts/c2-pooled-qrels/verify-full/qrels.jsonl"
OLD_LABELS = ROOT / "artifacts/orientation-spectra-2026-10-06-docs-scope/support.json"
BOOT = {"replicates": 10_000, "seed": 20261024, "interval": "percentile 2.5-97.5", "unit": "question cluster"}
CONTRASTS = (("LT", "O"), ("R0", "O"), ("LT", "R0"), ("S", "O"))
SA7 = ("docker_docs_faq_0068", "docker_docs_faq_0077", "docker_docs_faq_0080", "docker_docs_faq_0103",
       "docker_docs_faq_0127", "docker_docs_faq_0140")          # HR-20: unanswerable in both versions (author)
RAGAS_METRICS = ("factual_correctness", "faithfulness", "context_recall")   # FactualCorrectness is the main one
RETRIEVAL_METRICS = ("recall@5", "ndcg@10", "rr")
NAV_METRICS = ("support_read_or_cited", "support_read", "support_cited")
NAV_FOLLOW_METRICS = ("follows_landing_on_support",)                        # follow condition only
SA5_METRICS = ("recall@5", "rr") + NAV_METRICS + NAV_FOLLOW_METRICS
T2_CONDITIONS = ("s1-follow", "s1-no-follow")
ZERO_NAV = {"support_read": False, "support_cited": False, "support_read_or_cited": False,
            "follows_landing_on_support": 0, "searches_to_first_support": None}
ZERO_RET = {"recall@5": 0.0, "ndcg@10": 0.0, "rr": 0.0}


def jsonl(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def mean(xs):
    xs = [float(x) for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


# ------------------------------------------------------------------ statistics

def clusters_for(qids, duplicate_clusters):
    """Question clusters over `qids`: each duplicate-text pair is one cluster, every other question its own."""
    keep, seen, out = set(qids), set(), []
    for group in duplicate_clusters:
        g = tuple(q for q in group if q in keep)
        if g:
            out.append(g)
            seen.update(g)
    out += [(q,) for q in qids if q not in seen]
    return sorted(out)


def cluster_bootstrap(diffs, clusters, boot=BOOT):
    """Mean of the per-question differences, and a percentile interval from resampling clusters with replacement.
    Each question weighs 1; questions without a difference drop out, and so do clusters left empty."""
    import numpy as np
    cl = [[diffs[q] for q in c if q in diffs] for c in clusters]
    cl = [c for c in cl if c]
    n = sum(len(c) for c in cl)
    if not n:
        return {"estimate": None, "ci95": None, "n": 0, "clusters": 0}
    sums, counts = np.array([sum(c) for c in cl]), np.array([len(c) for c in cl], dtype=float)
    rng = np.random.default_rng(boot["seed"])
    idx = rng.integers(0, len(cl), size=(boot["replicates"], len(cl)))
    draws = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    return {"estimate": float(sums.sum() / counts.sum()),
            "ci95": [float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))], "n": n, "clusters": len(cl)}


def contrast_table(vals, qids, clusters):
    """vals: {arm: {question: value or None}} -> {"LT - O": {...}, ...}, paired on questions with both values."""
    out = {}
    for a, b in CONTRASTS:
        pairs = {q: (vals[a].get(q), vals[b].get(q)) for q in qids}
        pairs = {q: p for q, p in pairs.items() if p[0] is not None and p[1] is not None}
        res = cluster_bootstrap({q: x - y for q, (x, y) in pairs.items()}, clusters)
        res.update(mean_a=mean(p[0] for p in pairs.values()), mean_b=mean(p[1] for p in pairs.values()))
        out[f"{a} - {b}"] = res
    return out


def sign(res):
    e = res["estimate"]
    return 0 if e is None or e == 0 else (1 if e > 0 else -1)


def excludes_zero(res):
    return res["ci95"] is not None and (res["ci95"][0] > 0 or res["ci95"][1] < 0)


def per_question(cells, conditions=None):
    """cells: {(arm, q, cond): [values per replicate]} -> {arm: {q: mean over conditions of replicate means}}."""
    by = defaultdict(list)
    for (arm, q, c), xs in sorted(cells.items()):
        if conditions is None or c in conditions:
            m = mean(xs)
            if m is not None:
                by[(arm, q)].append(m)
    out = defaultdict(dict)
    for (arm, q), ms in by.items():
        out[arm][q] = mean(ms)
    return out


# ------------------------------------------------------------------ inputs

def context():
    key = rr.read_json(a19.KEY)["variants"]
    arm_of = {oid: v["variant"] for oid, v in key.items()}
    plan = a19.load_plan()
    rejudge = rr.read_json(REJUDGE)
    run19 = rr.read_json(RUN19)
    tier2 = rr.read_json(a19.TIER2)
    return {"arm_of": arm_of, "oid_of": {a: o for o, a in arm_of.items()}, "plan": plan, "rejudge": rejudge,
            "run19": run19, "all_q": [q["question_id"] for q in plan["questions"]["2"]],
            "common105": rejudge["common_set"]["question_ids"], "original101": run19["s19_4_common_questions"],
            "duplicates": tier2["duplicate_text_clusters"]}


def qrels(oid, which):
    pos, judged = defaultdict(set), defaultdict(set)
    for r in jsonl(a19.POOL / oid / which / "qrels.jsonl"):
        judged[r["query_id"]].add(r["support_target_id"])
        if r["relevance"] > 0:
            pos[r["query_id"]].add(r["support_target_id"])
    return pos, judged


def decisions(oid, which):
    """{(query, doc): decision} for the current pool pairs (page hash as pooled on this variant)."""
    cur = {(p["query_id"], p["doc_id"]): p["page_sha256"] for p in jsonl(a19.POOL / oid / "pool-agents/pool.jsonl")}
    return {(d["query_id"], d["doc_id"]): d["decision"] for d in jsonl(a19.POOL / oid / which / "page_decisions.jsonl")
            if cur.get((d["query_id"], d["doc_id"])) == d["page_sha256"]}


def undetermined(oid, judged):
    """Re-judged pool pairs still `undetermined` (they have no qrels row): {query: {doc}}."""
    out = defaultdict(set)
    for (q, d), dec in decisions(oid, "verify-escalated").items():
        if dec == "undetermined" and d not in judged[q]:
            out[q].add(d)
    return out


def variant_contexts(ctx):
    """The Sprint 19 `navigate` contexts (pages, mapping, credited equivalence, added edges, edit areas)."""
    vm = rr.read_json(wr.V6)
    gm = wr.dcv.pinned(vm)
    eff = w17.effective(w17.ledger_rows())
    tr = a19.trees()
    out = {}
    for oid, arm in sorted(ctx["arm_of"].items()):
        v = wr.variant_context(gm, vm, tr[arm], ledger_rows=eff, arm=arm if arm in wr.EDIT_ARMS else None)
        v["surviving"] = wr.credited_equivalence(v["base_list"], v["var_list"], v["receipt"], {})
        out[oid] = v
    return out


def rankings(oid):
    """Each fixed retriever's ranking per question, repeated pages collapsed to their first rank (as RA scores it)."""
    out = {}
    for r in wr.RETRIEVERS:
        rows = {x["question_id"]: x for x in jsonl(a19.RETRIEVAL / oid / r / "output/predictions.jsonl")}
        out[r] = {}
        for q, row in rows.items():
            seen, ranking = set(), []
            for c in sorted(row["retrieved_context"], key=lambda c: int(c["rank"])):
                if c["doc_id"] not in seen:
                    seen.add(c["doc_id"])
                    ranking.append(c["doc_id"])
            out[r][q] = ranking
    return out


def t2_traces(oid):
    out = []
    for path in sorted((a19.OUT / "t2").glob(f"{oid}-*.json")):
        t = rr.read_json(path)
        out.append((t["question_id"], "s1-follow" if t["follow_links"] else "s1-no-follow", path.name, t))
    return out


# ------------------------------------------------------------------ tier 1

def tier1(ctx):
    import answer_quality_scorer as aqs
    import rubric_quote_match as rqm
    passes = {p: sf.pass_scores(a19.SCORER / f"pass-{p}") for p in a19.SCORER_PASSES}
    rubric = rqm.score_judge_root(a19.JUDGE)
    measures = list(aqs.MEASURES) + ["rubric_relaxed"]
    cells = {m: defaultdict(list) for m in measures}
    counts = defaultdict(Counter)
    for path in sorted((a19.OUT / "t1").glob("*.json")):
        t = rr.read_json(path)
        arm = ctx["arm_of"]["-".join(path.stem.split("-")[:2])]
        cond = path.stem.split("-", 3)[3].rsplit("-r", 1)[0]
        counts[arm][t["terminal"]] += 1
        got = [passes[p][path.name] for p in passes if path.name in passes[p]]
        for m in aqs.MEASURES:
            cells[m][(arm, t["question_id"], cond)].append(mean(s[m] for s in got))
        cells["rubric_relaxed"][(arm, t["question_id"], cond)].append((rubric.get(path.name) or {}).get("rubric_v1_relaxed"))
        counts[arm]["luna_scored"] += bool(got)
        counts[arm]["rubric_scored"] += (rubric.get(path.name) or {}).get("rubric_v1_relaxed") is not None
    qids = sorted({q for (_, q, _) in cells["correctness"]})
    clusters = clusters_for(qids, [])
    tables = {m: contrast_table(per_question(cells[m]), qids, clusters) for m in measures}
    return {"questions": len(qids), "measures": measures, "primary": list(aqs.MEASURES), "secondary": ["rubric_relaxed"],
            "answers": {a: dict(c) for a, c in sorted(counts.items())}, "tables": tables,
            "disclosures": ["Luna scorer grades answers Luna wrote", "no model snapshot",
                            "scorer chosen on the original 5 questions; 10 tasks are out of sample for it",
                            "1 tier-1 answer ended invalid_action_limit (kept, unscored)",
                            "1 rubric packet failed on a local TimeoutError (kept as a failure)",
                            "11 archived answer attempts rerun under the frozen rule (7 model behaviour)",
                            "no RAGAS on tier 1 (pre-registration: FactualCorrectness omitted, author October 14)"]}


# ------------------------------------------------------------------ tier 2

def label_schemes(ctx, oid, vctx, pre, esc, und, c2pos, old):
    """Per row: (questions, {q: support ids}, equivalence or None for identity, judged sets or None, lost set)."""
    lost = {q for q in ctx["all_q"] if not esc[0][q]}
    hits = {q: esc[0][q] | und[q] for q in ctx["all_q"]}
    keep7 = [q for q in ctx["common105"] if q not in SA7]
    return {
        "primary": (ctx["common105"], esc[0], None, esc[1], set()),
        "sa5_hits": (ctx["common105"], hits, None, None, set()),
        "original_101": (ctx["original101"], pre[0], None, pre[1], set()),
        "strict_111": (ctx["all_q"], esc[0], None, esc[1], lost),
        "lenient_o1_111": (ctx["all_q"], c2pos, vctx["equivalence"], None, set()),
        "o1_surviving_111": (ctx["all_q"], c2pos, vctx["surviving"], None, set()),
        "sa7_without_6": (keep7, esc[0], None, esc[1], set()),
        "historical_top5": (ctx["all_q"], old, vctx["equivalence"], None, set()),
    }


def score_ranking(ranking, support, eq, judged):
    ids = sorted(support)
    res = wr.ranked_scores(ranking, ids, eq if eq is not None else {d: [d] for d in ids})
    if judged is not None and res["ndcg@10"] is not None and any(d not in judged for d in ranking[:10]):
        res["ndcg@10"] = None                         # RA pooled policy: an unjudged page in the top 10
    return res


def tier2(ctx, vctxs):
    c2pos = defaultdict(set)
    for r in jsonl(C2_QRELS):
        if r["relevance"] > 0:
            c2pos[r["query_id"]].add(r["support_target_id"])
    old = {q: set(v) for q, v in rr.read_json(OLD_LABELS).items()}
    ret_vals = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))      # row -> (retriever, metric) -> arm -> q
    nav_cells = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))   # row -> metric -> (arm, q, cond) -> [x]
    ragas_cells = {m: defaultdict(list) for m in RAGAS_METRICS}
    counts, decomposition, undetermined_counts, item_rows = {}, {}, {}, defaultdict(dict)
    for oid, arm in sorted(ctx["arm_of"].items()):
        vctx = vctxs[oid]
        pre, esc = qrels(oid, "verify"), qrels(oid, "verify-escalated")
        und = undetermined(oid, esc[1])
        undetermined_counts[arm] = {"pairs": sum(len(und[q]) for q in ctx["all_q"]),
                                    "pairs_in_primary_set": sum(len(und[q]) for q in ctx["common105"])}
        schemes = label_schemes(ctx, oid, vctx, pre, esc, und, c2pos, old)
        ranks = rankings(oid)
        for row, (qids, support, eq, judged, lost) in schemes.items():
            for r in wr.RETRIEVERS:
                for q in qids:
                    s = dict(ZERO_RET) if q in lost else score_ranking(ranks[r][q], support[q], eq, (judged or {}).get(q) if judged is not None else None)
                    for m in RETRIEVAL_METRICS:
                        ret_vals[row][(r, m)][arm][q] = s[m]
                    if row in ("primary", "original_101"):
                        item_rows[(row, oid, r)][q] = s
        traces = t2_traces(oid)
        for row, (qids, support, eq, judged, lost) in schemes.items():
            if row == "historical_top5":
                continue
            keep = set(qids)
            for q, cond, _name, t in traces:
                if q not in keep:
                    continue
                if q in lost:
                    nm = dict(ZERO_NAV)
                else:
                    ids = sorted(support[q])
                    nm = wr.navigation_metrics(t, ids, dict(vctx, equivalence=eq if eq is not None else {d: [d] for d in ids}))
                for m in NAV_METRICS + NAV_FOLLOW_METRICS:
                    if m in NAV_FOLLOW_METRICS and cond != "s1-follow":
                        continue
                    nav_cells[row][m][(arm, q, cond)].append(float(nm[m]))
                nav_cells[row]["searches_to_first_support"][(arm, q, cond)].append(nm["searches_to_first_support"])
        scores = rr.read_json(a19.RAGAS / oid / "run/scores.json")
        for rec in scores["records"]:
            q, cond = rec["unit"][0], rec["unit"][1]
            for m in RAGAS_METRICS:
                ragas_cells[m][(arm, q, cond)].append(rec[m]["value"] if rec[m]["status"] == "ok" else None)
        decomposition[arm] = o1_decomposition(ctx, vctx, c2pos, ranks, traces)
        counts[arm] = {"traces": len(traces), "questions_supported_rejudged": len(ctx["all_q"]) - len(schemes["strict_111"][4]),
                       "questions_lost_rejudged": sorted(schemes["strict_111"][4])}
    return ret_vals, nav_cells, ragas_cells, counts, decomposition, undetermined_counts, item_rows


def o1_decomposition(ctx, vctx, c2pos, ranks, traces):
    """Per question: O1 gold-set size (credited variant pages), surviving-only size, derived-only pages, and the share
    of O1 hits (retriever top-10 pages and pages the traces opened) that land on derived-only pages."""
    rows = {}
    opened = defaultdict(set)
    for q, _cond, _n, t in traces:
        for step in t.get("steps", []):
            act = step.get("action") or {}
            if act.get("action") in ("read", "follow") and act.get("doc_id"):
                opened[q].add(act["doc_id"])
    for q in ctx["all_q"]:
        full = set().union(*(set(vctx["equivalence"].get(b, [])) for b in c2pos[q])) if c2pos[q] else set()
        surv = set().union(*(set(vctx["surviving"].get(b, [])) for b in c2pos[q])) if c2pos[q] else set()
        derived = full - surv
        ret_hits = [d for r in wr.RETRIEVERS for d in ranks[r][q][:10] if d in full]
        nav_hits = [d for d in sorted(opened[q]) if d in full]
        rows[q] = {"c2_gold": len(c2pos[q]), "o1_gold": len(full), "surviving_gold": len(surv), "derived_only": len(derived),
                   "retriever_hits": len(ret_hits), "retriever_hits_derived": sum(d in derived for d in ret_hits),
                   "trace_hits": len(nav_hits), "trace_hits_derived": sum(d in derived for d in nav_hits)}
    tot = lambda k: sum(r[k] for r in rows.values())
    summary = {k: tot(k) for k in ("c2_gold", "o1_gold", "surviving_gold", "derived_only", "retriever_hits",
                                   "retriever_hits_derived", "trace_hits", "trace_hits_derived")}
    summary["share_retriever_hits_derived"] = summary["retriever_hits_derived"] / summary["retriever_hits"] if summary["retriever_hits"] else None
    summary["share_trace_hits_derived"] = summary["trace_hits_derived"] / summary["trace_hits"] if summary["trace_hits"] else None
    summary["questions_with_derived_credit"] = sum(r["derived_only"] > 0 for r in rows.values())
    return {"summary": summary, "per_question": rows}


def tier2_tables(ctx, ret_vals, nav_cells, ragas_cells):
    dup = ctx["duplicates"]
    rows = {}
    qsets = {"primary": ctx["common105"], "sa5_hits": ctx["common105"], "original_101": ctx["original101"],
             "strict_111": ctx["all_q"], "lenient_o1_111": ctx["all_q"], "o1_surviving_111": ctx["all_q"],
             "sa7_without_6": [q for q in ctx["common105"] if q not in SA7], "historical_top5": ctx["all_q"]}
    for row, qids in qsets.items():
        cl = clusters_for(qids, dup)
        ret = {f"{r}/{m}": contrast_table(ret_vals[row][(r, m)], qids, cl) for r in wr.RETRIEVERS for m in RETRIEVAL_METRICS
               if not (row == "sa5_hits" and m == "ndcg@10")}
        nav = {}
        if row != "historical_top5":
            for m in NAV_METRICS:
                nav[f"{m}/pooled"] = contrast_table(per_question(nav_cells[row][m]), qids, cl)
                for c in T2_CONDITIONS:
                    nav[f"{m}/{c}"] = contrast_table(per_question(nav_cells[row][m], {c}), qids, cl)
            for m in NAV_FOLLOW_METRICS:
                nav[f"{m}/s1-follow"] = contrast_table(per_question(nav_cells[row][m], {"s1-follow"}), qids, cl)
        rows[row] = {"questions": len(qids), "clusters": len(cl), "retrieval": ret, "navigation": nav}
    s2f = Counter(x for xs in nav_cells["primary"]["searches_to_first_support"].values() for x in xs)
    rows["primary"]["searches_to_first_support_values"] = {str(k): v for k, v in sorted(s2f.items(), key=str)}
    ragas = {}
    for name, qids in (("full_111", ctx["all_q"]), ("sa7_without_6", [q for q in ctx["all_q"] if q not in SA7])):
        cl = clusters_for(qids, dup)
        block = {}
        for m in RAGAS_METRICS:
            block[f"{m}/pooled"] = contrast_table(per_question(ragas_cells[m]), qids, cl)
            for c in T2_CONDITIONS:
                block[f"{m}/{c}"] = contrast_table(per_question(ragas_cells[m], {c}), qids, cl)
        ragas[name] = {"questions": len(qids), "clusters": len(cl), "tables": block}
    return rows, ragas


def robustness(rows):
    """SA5: sign of the primary equal under misses and hits. SA6: sign equal across primary, original 101, strict and
    lenient. O1: a lenient difference whose interval excludes 0 is called derived-credit driven only if the surviving-
    only recomputation's interval includes 0 or its sign reverses."""
    sa5, sa6, o1 = {}, {}, {}
    for block in ("retrieval", "navigation"):
        for key, table in rows["primary"][block].items():
            for c, res in table.items():
                metric = key.split("/")[1] if block == "retrieval" else key.split("/")[0]
                if metric in SA5_METRICS and key in rows["sa5_hits"][block]:
                    h = rows["sa5_hits"][block][key][c]
                    sa5[f"{block}:{key}:{c}"] = {"misses": res["estimate"], "hits": h["estimate"],
                                                 "robust": sign(res) != 0 and sign(res) == sign(h)}
                signs = [sign(rows[r][block][key][c]) for r in ("primary", "original_101", "strict_111", "lenient_o1_111")
                         if key in rows[r][block]]
                sa6[f"{block}:{key}:{c}"] = {"signs": signs, "robust": signs[0] != 0 and len(set(signs)) == 1}
        for key, table in rows["lenient_o1_111"][block].items():
            for c, res in table.items():
                if excludes_zero(res):
                    s = rows["o1_surviving_111"][block][key][c]
                    o1[f"{block}:{key}:{c}"] = {"lenient": res["estimate"], "lenient_ci95": res["ci95"],
                                                "surviving": s["estimate"], "surviving_ci95": s["ci95"],
                                                "derived_credit_driven": (not excludes_zero(s)) or sign(s) != sign(res)}
    return {"sa5": sa5, "sa6": sa6, "o1_derived": o1}


# ------------------------------------------------------------------ lost questions, HR-20, navigation shares

def lost_questions(ctx, ragas_cells):
    """The 10 questions lost before the re-judge: page decisions per variant before and after, the supported pages
    after, and RAGAS FactualCorrectness by arm on them."""
    before = {oid: {q for q in ctx["all_q"] if not qrels(oid, "verify")[0][q]} for oid in ctx["arm_of"]}
    lost = sorted(set().union(*before.values()))
    out = {}
    for oid, arm in sorted(ctx["arm_of"].items()):
        pre, post = decisions(oid, "verify"), decisions(oid, "verify-escalated")
        pos_after = qrels(oid, "verify-escalated")[0]
        for q in lost:
            out.setdefault(q, {})[arm] = {
                "before": dict(Counter(d for (qq, _), d in pre.items() if qq == q)),
                "after": dict(Counter(d for (qq, _), d in post.items() if qq == q)),
                "lost_before": q in before[oid], "lost_after": not pos_after[q],
                "supported_after": sorted(pos_after[q])}
    fc = per_question(ragas_cells["factual_correctness"])
    by_arm = {a: {"mean": mean(fc[a].get(q) for q in lost), "per_question": {q: fc[a].get(q) for q in lost}}
              for a in ("O", "S", "LT", "R0")}
    table = contrast_table(fc, lost, clusters_for(lost, ctx["duplicates"]))
    return {"questions": lost, "decisions": out, "factual_correctness": {"by_arm": by_arm, "contrasts": table}}


def hr20(ctx):
    key = rr.read_json(HR20_KEY)["key"]
    verdicts = {r["item"]: r for r in rr.read_json(HR20)["reviews"]}
    agreement = {r["item"]: r for r in ctx["rejudge"]["hr20_agreement"]["items"]}
    items = []
    for item in sorted(key):
        k, v, a = key[item], verdicts[item], agreement[item]
        if (k["variant"], k["question_id"]) != (v["variant"], v["question_id"]):
            raise Refused(f"hr20-key-mismatch:{item}")
        items.append({"item": item, "question_id": k["question_id"], "arm": ctx["arm_of"][k["variant"]],
                      "verdict": v["verdict"], "author_answer_present": v["verdict"] in ("still_answers", "answer_moved"),
                      "rejudge_supported": a["escalated_supported"], "agrees": a["agrees"],
                      "blind": item != "Q01"})
    table = Counter((i["author_answer_present"], i["rejudge_supported"]) for i in items)
    return {"source_sha256": sha(HR20), "key_sha256": sha(HR20_KEY), "items": items,
            "table": {f"author_{a}_rejudge_{b}": table[(a, b)] for a in (True, False) for b in (True, False)},
            "agree": sum(i["agrees"] for i in items), "n": len(items)}


def navigation_shares(ctx, vctxs):
    """Step 3 (A4, A5): follows over editor-added edges and edited pages opened, by arm, tier and condition."""
    out = {}
    for oid, arm in sorted(ctx["arm_of"].items()):
        vctx = vctxs[oid]
        for tier in ("1", "2"):
            for path in sorted((a19.OUT / f"t{tier}").glob(f"{oid}-*.json")):
                t = rr.read_json(path)
                cond = path.stem.split("-", 3)[3].rsplit("-r", 1)[0] if tier == "1" else \
                    ("s1-follow" if t["follow_links"] else "s1-no-follow")
                f = wr.annotate_trace(t, vctx)
                c = out.setdefault(f"t{tier}/{arm}/{cond}", Counter())
                c["traces"] += 1
                c["follows"] += len(f["follows"])
                c["follows_over_editor_added_edges"] += sum(x["editor_added_edge"] for x in f["follows"])
                c["pages_opened"] += sum(p["read"] for p in f["pages"])
                c["edited_pages_opened"] += sum(p["read"] and p["edited"] for p in f["pages"])
                c["pages_cited"] += sum(p["cited"] for p in f["pages"])
                c["edited_pages_cited"] += sum(p["cited"] and p["edited"] for p in f["pages"])
    res = {}
    for k, c in sorted(out.items()):
        res[k] = dict(c, share_follows_over_editor_added_edges=c["follows_over_editor_added_edges"] / c["follows"] if c["follows"] else None,
                      share_edited_pages_opened=c["edited_pages_opened"] / c["pages_opened"] if c["pages_opened"] else None,
                      share_edited_pages_cited=c["edited_pages_cited"] / c["pages_cited"] if c["pages_cited"] else None)
    return res


def spectra_and_coverage():
    sp = rr.read_json(SPECTRA18)
    run = rr.read_json(RUN17)
    cov = {}
    for area, a in sorted(run["areas"].items()):
        for arm in wr.EDIT_ARMS:
            if arm in a:
                cov.setdefault(area, {})[arm] = {**a[arm]["support_overlap"], "admission": a[arm]["admission"]}
    totals = {arm: {t: {k: sum(cov[x][arm][t][k] for x in cov if arm in cov[x]) for k in ("pages", "touched")}
                    for t in ("tier1", "tier2")} for arm in wr.EDIT_ARMS}
    return {"source_sha256": sha(SPECTRA18), "primary_test": sp["primary_test"], "SA1": sp["SA1"],
            "full_view": sp["full_view"], "z_tallies": sp["z_tallies"], "controls_check": sp["controls_check"],
            "coverage_source_sha256": sha(RUN17), "coverage_per_area": cov, "coverage_totals": totals,
            "coverage_note": "Sprint 17 support_overlap: frozen support pages of each tier inside an area, and how many the "
                             "arm touched (changed, removed or merged)"}


def cross_checks(ctx, item_rows):
    """The original-101 retrieval rows against the recorded RA score_ranked items, and the primary O1 navigation and
    retriever rows against Sprint 19's navigation-t2 records (largest absolute difference, availability equality)."""
    worst, mismatched, compared = 0.0, 0, 0
    for oid in ctx["arm_of"]:
        for r in wr.RETRIEVERS:
            mine = item_rows[("original_101", oid, r)]
            for k, metric, ra in ((5, "recall@5", "evidence_recall"), (10, "rr", "reciprocal_rank"), (10, "ndcg@10", "ndcg")):
                for row in jsonl(a19.RANKED / oid / f"{r}-k{k}" / "ranked_item_scores.v2.jsonl"):
                    a, b = mine[row["query_id"]][metric], row[ra]
                    compared += 1
                    if (a is None) != (b is None):
                        mismatched += 1
                    elif a is not None:
                        worst = max(worst, abs(a - b))
    return {"ra_score_ranked_original_101": {"compared": compared, "availability_mismatches": mismatched,
                                             "max_abs_diff": worst}}


def o1_cross_check(ctx, ret_vals, nav_cells):
    """Lenient O1 against Sprint 19's recorded O1 rows (retrievers and traces)."""
    worst, n = 0.0, 0
    for oid, arm in ctx["arm_of"].items():
        for x in jsonl(a19.NAV / f"{oid}.retrievers-o1.jsonl"):
            for m in RETRIEVAL_METRICS:
                a, b = ret_vals["lenient_o1_111"][(x["retriever"], m)][arm][x["question_id"]], x["o1"][m]
                n += 1
                if (a is None) != (b is None):
                    raise Refused(f"o1-availability-differs:{oid}")
                if a is not None:
                    worst = max(worst, abs(a - b))
    recorded = Counter()
    for oid, arm in ctx["arm_of"].items():
        for x in jsonl(a19.NAV / f"{oid}.traces.jsonl"):
            recorded[(arm, x["question_id"], "s1-follow" if x["follow_links"] else "s1-no-follow")] = float(x["o1"]["support_read_or_cited"])
    mine = nav_cells["lenient_o1_111"]["support_read_or_cited"]
    nav_diff = sum(abs(mine[k][0] - v) for k, v in recorded.items())
    return {"retriever_values": n, "retriever_max_abs_diff": worst, "trace_support_read_or_cited_abs_diff_sum": nav_diff,
            "traces": len(recorded)}


# ------------------------------------------------------------------ assembly

def compute():
    ctx = context()
    vctxs = variant_contexts(ctx)
    ret_vals, nav_cells, ragas_cells, counts, decomposition, und, item_rows = tier2(ctx, vctxs)
    rows, ragas = tier2_tables(ctx, ret_vals, nav_cells, ragas_cells)
    run19 = ctx["run19"]
    pool = {ctx["arm_of"][o]: {**{k: v for k, v in p.items() if k in ("pool_pairs", "queries_kept", "qrels_positive", "decisions")},
                               "windows_judged": p["run"].get("windows_judged", p["run"]),
                               "rejudge": {k: ctx["rejudge"]["variants"][o][k] for k in ("outcomes", "decisions", "questions_supported",
                                                                                          "questions_supported_before")}}
            for o, p in sorted(run19["pool_completion"].items())}
    sa7_in_primary = sorted(set(SA7) & set(ctx["common105"]))
    out = {
        "schema_version": "docspectra.icsc.wc_analysis_20.v1", "created": DATE, "model_calls": 0,
        "scope": "exploratory; one per tier, never pooled; S - O beside every row; the Sprint 18 test is the only "
                 "pre-registered result (pre-registered within an exploratory run)",
        "inputs_sha256": {p.name: sha(p) for p in (a19.PLAN, RUN19, REJUDGE, RUN17, SPECTRA18, HR20, a19.TIER2, C2_QRELS, OLD_LABELS)},
        "code_sha256": {"wc_analysis_20": sha(Path(__file__)), "wc_answers_19": sha(Path(a19.__file__)),
                        "wc_refinement_15": sha(Path(wr.__file__))},
        "bootstrap": BOOT, "contrasts": [f"{a} - {b}" for a, b in CONTRASTS],
        "arms": {a: o for o, a in sorted(ctx["arm_of"].items())},
        "tier1": tier1(ctx),
        "tier2": {
            "label": rr.read_json(EVIDENCE / "wc-refinement-preregistration-v1-2026-10-14.json")["tier2"]["label"]
                     + "; C2 spot-check (D11a) on O only; the pool-completion and re-judge labels were not spot-checked",
            "questions": {"all": len(ctx["all_q"]), "primary_common": len(ctx["common105"]), "original_101": len(ctx["original101"])},
            "duplicate_clusters": ctx["duplicates"], "pool_completion": pool, "per_arm": counts,
            "undetermined_after_rejudge": und,
            "navigation_unjudged_rule": "Sprint 15 navigation_metrics receives only the supported pages of the question; "
                                        "a read or cited page with no judgment (undetermined or unpooled) is not a target, "
                                        "so it counts as not reaching support (a miss)",
            "rows": rows, "ragas": ragas, "robustness": robustness(rows), "o1_decomposition": decomposition,
            "sa7": {"questions": list(SA7), "in_primary_common_set": sa7_in_primary,
                    "outside_primary_common_set": sorted(set(SA7) - set(ctx["common105"])),
                    "note": "only the lost questions were reviewed by HR-20; other questions may have generous labels"},
            "lost_questions": lost_questions(ctx, ragas_cells), "hr20": hr20(ctx)},
        "navigation_shares": navigation_shares(ctx, vctxs),
        "spectra_18": spectra_and_coverage(),
        "cross_checks": {**cross_checks(ctx, item_rows), "o1_against_sprint19": o1_cross_check(ctx, ret_vals, nav_cells)},
    }
    return out


def canonical(data):
    return json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n"


# ------------------------------------------------------------------ generated tables (markdown)

def fmt(res, digits=3):
    if res["estimate"] is None:
        return "n/a"
    lo, hi = res["ci95"]
    return f"{res['estimate']:+.{digits}f} [{lo:+.{digits}f}, {hi:+.{digits}f}]"


def table_md(title, tables, keys=None, n_col=True):
    head = "| Measure | " + " | ".join(f"{a} − {b}" for a, b in CONTRASTS) + (" | n |" if n_col else " |")
    lines = [f"**{title}**", "", head, "|" + "---|" * (len(CONTRASTS) + 1 + n_col)]
    for key in keys or tables:
        t = tables[key]
        cells = [fmt(t[f"{a} - {b}"]) for a, b in CONTRASTS]
        ns = sorted({t[f"{a} - {b}"]["n"] for a, b in CONTRASTS})
        lines.append(f"| {key} | " + " | ".join(cells) + (f" | {'/'.join(map(str, ns))} |" if n_col else " |"))
    return "\n".join(lines) + "\n"


def tables_md(res, digest):
    t1, t2 = res["tier1"], res["tier2"]
    out = [f"# Sprint 20 generated tables (whole-corpus refinement)\n",
           f"Generated by `scripts/wc_analysis_20.py analyse` from `evidence/{RESULT.name}` (`{digest[:8]}`). Do not edit by "
           "hand. Every cell: paired mean difference [bootstrap 95% interval, question clusters, 10,000 draws]. "
           "Exploratory, except the Sprint 18 test. Tiers are never pooled.\n",
           "## Tier 1 (15 complex tasks; Luna scorer v2, k = 2, primary; rubric relaxed, secondary)\n",
           table_md("Tier 1", t1["tables"], t1["measures"]),
           f"Answers by arm: {json.dumps(t1['answers'], sort_keys=True)}\n",
           f"## Tier 2 ({t2['label']})\n"]
    for row in ("primary", "sa5_hits", "original_101", "strict_111", "lenient_o1_111", "o1_surviving_111", "sa7_without_6",
                "historical_top5"):
        r = t2["rows"][row]
        out.append(f"### Row `{row}` ({r['questions']} questions, {r['clusters']} clusters)\n")
        out.append(table_md("Fixed retrievers", r["retrieval"]))
        if r["navigation"]:
            out.append(table_md("Navigation (answer traces)", r["navigation"]))
    for name, block in t2["ragas"].items():
        out.append(f"### RAGAS `{name}` ({block['questions']} questions; FactualCorrectness main)\n")
        out.append(table_md("RAGAS", block["tables"]))
    lq = t2["lost_questions"]["factual_correctness"]
    out.append("### The 10 lost questions: RAGAS FactualCorrectness\n")
    out.append("| Arm | Mean |\n|---|---|\n" + "".join(f"| {a} | {v['mean']:.3f} |\n" for a, v in lq["by_arm"].items()))
    out.append("\n" + table_md("Contrasts on the 10", {"factual_correctness": lq["contrasts"]}))
    out.append("## Navigation shares (A4, A5)\n")
    out.append("| Tier/arm/condition | Traces | Follows | Over editor-added edges | Pages opened | Edited opened | Share follows added | Share edited opened |\n|---|---|---|---|---|---|---|---|\n")
    for k, c in res["navigation_shares"].items():
        f = lambda x: "n/a" if x is None else f"{x:.3f}"
        out.append(f"| {k} | {c['traces']} | {c['follows']} | {c['follows_over_editor_added_edges']} | {c['pages_opened']} | "
                   f"{c['edited_pages_opened']} | {f(c['share_follows_over_editor_added_edges'])} | {f(c['share_edited_pages_opened'])} |\n")
    return "\n".join(out)


def analyse(_args):
    if RESULT.exists():
        raise Refused(f"evidence-exists:{RESULT.name}")
    data = compute()
    text = canonical(data)
    RESULT.write_text(text, encoding="utf-8", newline="\n")
    digest = sha(RESULT)
    RESULT.with_suffix(".sha256").write_text(f"{digest}  {RESULT.name}\n", encoding="ascii", newline="\n")
    TABLES.write_text(tables_md(data, digest), encoding="utf-8", newline="\n")
    print(json.dumps({"evidence": RESULT.name, "sha256": digest, "tables": TABLES.name, "tables_sha256": sha(TABLES)}, indent=1))


def verify(_args):
    recorded = RESULT.with_suffix(".sha256").read_text(encoding="ascii").split()[0]
    if sha(RESULT) != recorded:
        raise Refused("evidence-changed-since-record")
    data = compute()
    text = canonical(data)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    tables = tables_md(data, digest)
    same = digest == recorded and tables == TABLES.read_text(encoding="utf-8")
    print(json.dumps({"recorded": recorded, "recomputed": digest, "tables_equal": tables == TABLES.read_text(encoding="utf-8"),
                      "reproduced": same}, indent=1))
    if not same:
        raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="stage", required=True)
    sub.add_parser("analyse").set_defaults(fn=analyse)
    sub.add_parser("verify").set_defaults(fn=verify)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

"""Sprint 11e: exploratory pointwise scorer v2 pilot on the 11b answers (qualification before Sprint 12).

Stages, each refusing to overwrite its output:
  smoke     one answer, own folder and seed: transport, schema validity and raw field order
  freeze    write the 11e plan (prompt, schema, module, driver by SHA-256; model and options; seeds;
            runs; ranking rule; human key hash; criteria and decision rule) before any scorer call
            on the 60 answers
  score     --run A | A2 | B: blind packets, one pass plus one retry pass, blind summary
  analyse   after all three runs: blind rankings are saved first (per-run summaries, hashed), then
            the arm key, rubric scores and human key are joined; descriptive tables, no tests
  evaluate  criteria against the frozen thresholds, human agreement (or "pending"), the decision

Exploratory only: the scorer was designed after the frozen 11b no-go and never changes it.
The frozen 11b and 11d stages, the ranker v1 files and the 11d evidence are not modified.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import answer_quality_scorer as aqs  # noqa: E402
import restructuring_pilot_11b as p11b  # noqa: E402
import restructuring_runs as rr  # noqa: E402

DATE = "2026-10-08"
SEED = 20261015
NOTES_SEED = SEED  # run B only; hashed per question ('<seed>|notes|<question_id>'), a stream apart from labels
SMOKE_SEED = 1
PLAN = rr.CONTEXT / f"evidence/scoring-pilot-11e-plan-{DATE}.json"
OUT = rr.ROOT / "artifacts/pilot-11b-v3-scorer-v2"
SMOKE = rr.ROOT / "artifacts/pilot-11b-v3-scorer-v2-smoke"
RUNS = {"A": {"notes_seed": None, "role": "base run; sham balance, spread, ranking, human agreement"},
        "A2": {"notes_seed": None, "role": "identical rerun of A (same packets and order); reported only"},
        "B": {"notes_seed": NOTES_SEED, "role": "reference notes shuffled per question; stability criterion"}}
HUMAN = rr.ROOT / "artifacts/pilot-11b-v3-human-ranking"
DIAGNOSTIC = ("3b5890ab4c45", "s1-follow")
ARMS = p11b.ARMS_ANSWERED

CRITERIA = {
    "sham_balance": {"measure": "run A derived O-S pair outcomes over the 20 cells: |S wins - O wins| "
                                "(ties and unavailable pairs count for neither)", "threshold": "<= 4"},
    "stability": {"measure": "run A vs run B: share of the 300 measure scores (60 answers x 5) within +-1 "
                             "(a score missing in either run counts as not within), and share of the 20 cells "
                             "with the same derived ranking (tie groups included; an unranked cell is not the same)",
                  "threshold": ">= 0.90 scores within +-1 and >= 0.80 cells same ranking",
                  "reported_only": "run A vs run A2 (identical rerun): identical outputs, scores and rankings"},
    "spread": {"measure": "share of the 60 answers scoring 5 on every measure in run A (unavailable is not all-5)",
               "threshold": "<= 0.50"},
    "schema_validity": {"measure": "packets valid on the first attempt over all 180 packets (A, A2, B); "
                                   "attempts per packet", "threshold": ">= 0.95 first-attempt valid; at most "
                                                                       "one retry (2 attempts) per packet",
                        "procedure": "one pass, then one automatic retry pass over failed packets; still-failed "
                                     "packets stay unavailable and every pair with them is unavailable"},
    "human_agreement": {"measure": "the 21 pairs of the 7 11d human triplets: denominator = pairs the non-author "
                                   "reviewer ranks as non-tied; agreement = the run A derived ranking names the same "
                                   "winner; reversal = it names the other answer; a derived tie or an unavailable "
                                   "pair counts as neither; the 3b5890 s1-follow diagnostic is included and also "
                                   "reported separately",
                        "threshold": "agreement >= 0.60 and reversals <= 0.20",
                        "pending": "until `ranking_pilot_11d.py record-human` records a non-author reviewer's form; "
                                   "the decision is then provisional"},
}

DECISION_RULE = {
    "qualified_for_triage": "every criterion passes",
    "not_usable": "sham_balance and stability both fail and human_agreement fails",
    "needs_revision": "otherwise (any failed criterion short of not_usable; gap-fill as in 11d, author-confirmed)",
    "provisional": "while human_agreement is pending, the decision rests on the automated criteria only and is "
                   "reported as provisional",
}


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inputs():
    answers, qs, forbidden = p11b.ranker_inputs()
    return [{k: v for k, v in a.items() if k != "arm"} for a in answers], qs, forbidden


def identity():
    return {"prompt": aqs.PROMPT.name, "prompt_sha256": sha(aqs.PROMPT), "schema": aqs.SCHEMA.name,
            "schema_sha256": sha(aqs.SCHEMA), "module_sha256": sha(aqs.__file__), "driver_sha256": sha(__file__),
            "transport_module_sha256": sha(aqs.apr.__file__)}


# ------------------------------------------------------------------ smoke, freeze, score

def smoke(_args):
    if not (SMOKE / "packet-manifest.json").exists():
        answers, qs, forbidden = inputs()
        first = min(answers, key=lambda a: (a["question_id"], a["cell"], a["answer_id"]))
        packets, key = aqs.build_packets([first], qs, SMOKE_SEED)
        aqs.write_packets(SMOKE, packets, key, SMOKE_SEED, None, forbidden, run_label="smoke")
    aqs.run(SMOKE)
    s = aqs.summarize(SMOKE)
    calls = [rr.read_json(p) for p in sorted(SMOKE.glob("calls/*/attempt-[0-9][0-9].json"))]
    print(json.dumps({"receipt_sha256": s["receipt_sha256"], "usage": s["usage"],
                      "calls": [{k: c.get(k) for k in ("valid", "validation_error", "field_order_ok", "field_order",
                                                        "usage", "raw_response_sha256", "digest_before",
                                                        "digest_after")} for c in calls]}, indent=1))


def freeze(_args):
    if PLAN.exists():
        raise SystemExit("plan-already-written")
    if any((OUT / f"run-{r}").exists() for r in RUNS):
        raise SystemExit("scorer-run-exists: the plan must freeze before any scorer packet or call")
    answers, _, _ = inputs()
    files = p11b.answer_files()
    digest = hashlib.sha256("".join(f"{p.name} {sha(p)}\n" for p in files).encode()).hexdigest()
    plan = {
        "schema_version": "docspectra.icsc.scoring_pilot_11e.v1.plan",
        "created": DATE,
        "scope": "exploratory: scorer v2 designed after the frozen 11b no-go; never changes it; not paper evidence",
        "frozen_before": "any scorer call on the 60 answers (only the one-answer smoke exists, in "
                         "artifacts/pilot-11b-v3-scorer-v2-smoke with seed 1)",
        "scorer": {**identity(), "model": aqs.MODEL, "expected_digest": aqs.EXPECTED_DIGEST,
                   "options": {**aqs.OPTIONS, "think": False, "format": "schema v2"},
                   "measures_priority_order": list(aqs.MEASURES), "field_order": list(aqs.FIELD_ORDER),
                   "max_attempts": aqs.MAX_ATTEMPTS},
        "ranking": {"rule": aqs.RANK_RULE, "margin": aqs.MARGIN,
                    "definition": "within a cell, compare two answers measure by measure in priority order; the "
                                  "first measure where |difference| >= margin decides; smaller or equal differences "
                                  "fall through; no deciding measure is a tie; per-cell ranks by net wins; cycles "
                                  "checked and reported; computed on opaque labels before unblinding",
                    "unavailable": "an answer without a valid score after one retry makes every pair with it "
                                   "unavailable and its cell unranked"},
        "seeds": {"seed": SEED, "uses": "answer labels and run order (random.Random(seed)); same for A, A2 and B",
                  "notes_seed": NOTES_SEED, "notes_rule": "run B only: per question, random.Random(int(sha256("
                  "'<notes_seed>|notes|<question_id>'))) shuffles the note order, reshuffled while identity"},
        "runs": {r: {**v, "packets": len(answers), "root": f"artifacts/pilot-11b-v3-scorer-v2/run-{r}",
                     "stage": f"scoring_pilot_11e.py score --run {r}"} for r, v in RUNS.items()},
        "calls_planned": 3 * len(answers), "cost_usd": 0, "endpoint": "localhost Ollama only",
        "inputs": {"answers_dir": "artifacts/pilot-11b-v3-answers", "answer_files": len(files),
                   "answers_scored": len(answers), "answer_files_digest": digest,
                   "questions_sha256": sha(p11b.QUESTIONS), "judge_aliases_sha256": sha(p11b.JUDGE / "aliases.json"),
                   "rubric_scores_sha256": sha(p11b.RUBRIC_SCORES),
                   "human_key_sha256": sha(HUMAN / "host-private/key.json"),
                   "human_packet_sha256": sha(HUMAN / "packet.md")},
        "criteria": CRITERIA,
        "decision_rule": DECISION_RULE,
        "author_confirmations": {
            "date": DATE, "channel": "chat, before freezing",
            "fit_rule": "amended wording approved after two synthetic qwen interpretation probes (no 11b answers): "
                        "'If the asker names a tool or service to perform a step, an answer that moves that step to "
                        "a different tool changes a constraint; a different route within the named tools does not. "
                        "If no tool is named for a step, any correct tool may perform it.' Inlined in prompt v2.",
            "measure_4": "added 'If the question raises no failure or special case, score 5.'",
            "editorial": "return line names the keys in order (the model does not see the schema text)",
            "thresholds": "sprint defaults", "decision_gap_fill": "as in 11d"},
        "field_order_probe": "before freezing, a dummy-schema call (no 11b data) returned keys in the schema "
                             "'properties' order, nested included; the smoke checks the real schema",
        "reports": ["calls, valid share, wall time", "criteria", "per-arm means per measure (A; B beside)",
                    "derived R0-vs-O, S-vs-O and S-vs-R0 wins/ties/losses", "deciding-measure distribution",
                    "agreement with rubric_v1_strict / relaxed / raw", "3b5890 s1-follow fit notes and scores",
                    "sham S-O score differences per measure for replicate sizing",
                    "no significance tests and no effect claims"],
    }
    print(json.dumps({"plan": str(PLAN), "sha256": rr.write_new(PLAN, plan)}, indent=1))


def check_plan():
    plan = rr.read_json(PLAN)
    if {k: plan["scorer"][k] for k in identity()} != identity():
        raise SystemExit("scorer-identity-drift-since-freeze")
    return plan


def score(args):
    plan = check_plan()
    root = OUT / f"run-{args.run}"
    if not (root / "packet-manifest.json").exists():
        answers, qs, forbidden = inputs()
        notes_seed = plan["runs"][args.run]["notes_seed"]
        packets, key = aqs.build_packets(answers, qs, plan["seeds"]["seed"], notes_seed)
        receipt = aqs.write_packets(root, packets, key, plan["seeds"]["seed"], notes_seed, forbidden,
                                    run_label=args.run)
        print(json.dumps({"run": args.run, "packets": receipt["packets"],
                          "manifest_sha256": receipt["manifest_sha256"]}), flush=True)
    aqs.run(root, limit=args.limit)
    s = aqs.summarize(root)  # blind: labels only
    print(json.dumps({"run": args.run, "usage": {k: v for k, v in s["usage"].items() if k != "failures"},
                      "failures": s["usage"]["failures"], "unavailable": s["unavailable"]}, indent=1))


# ------------------------------------------------------------------ analyse

def counts(rows):
    out = {"pairs": len(rows), "base_wins": 0, "other_wins": 0, "ties": 0, "unavailable": 0}
    for r in rows:
        out[{"base": "base_wins", "other": "other_wins", "tie": "ties", None: "unavailable"}[r["outcome"]]] += 1
    return out


def analyse(_args):
    plan = check_plan()
    summaries = {}
    for r in RUNS:
        root = OUT / f"run-{r}"
        manifest = rr.read_json(root / "packet-manifest.json")
        missing = [e for e in manifest if not any((root / "calls" / e["packet_id"]).glob("attempt-*.json"))]
        if missing:
            raise SystemExit(f"run-{r}-incomplete:{len(missing)}")
        rec = rr.read_json(root / "receipt.json")
        if rec["prompt_sha256"] != plan["scorer"]["prompt_sha256"] or rec["seed"] != plan["seeds"]["seed"] \
                or rec["notes_seed"] != plan["runs"][r]["notes_seed"]:
            raise SystemExit(f"run-{r}-does-not-match-plan")
        summaries[r] = aqs.summarize(root)  # blind rankings saved before the arm key is read
    blind_hashes = {r: sha(OUT / f"run-{r}" / "scoring-summary.json") for r in RUNS}
    keys = {r: rr.read_json(OUT / f"run-{r}" / "host-private/answer-key.json") for r in RUNS}
    if not (keys["A"].keys() == keys["A2"].keys() == keys["B"].keys()):
        raise SystemExit("labels-differ-across-runs")
    key = keys["A"]
    # ---- unblinding starts here
    ids = p11b.variant_ids()
    arm_of = {v: a for a, v in ids.items()}
    arm = lambda label: arm_of["-".join(key[label]["answer_id"].split("-")[:2])]  # noqa: E731
    order = {a: i for i, a in enumerate(ARMS)}
    A, A2, B = (summaries[r] for r in ("A", "A2", "B"))

    def pair_rows(summary):
        rows = []
        for c in summary["cells"]:
            for p in c["pairs"]:
                base, other = sorted((p["x"], p["y"]), key=lambda i: order[arm(i)])
                outcome = p["outcome"]
                if outcome in ("x", "y"):
                    outcome = "other" if p[outcome] == other else "base"
                rows.append({"question_id": c["question_id"][:12], "condition": c["cell"],
                             "pair_type": f"{arm(base)}-{arm(other)}", "base_label": base, "other_label": other,
                             "base_answer": key[base]["answer_id"], "other_answer": key[other]["answer_id"],
                             "outcome": outcome, "deciding_measure": p["deciding_measure"]})
        return rows
    rows_a = pair_rows(A)
    by_type = {t: counts([r for r in rows_a if r["pair_type"] == t]) for t in ("O-S", "O-R0", "S-R0")}
    by_type_b = {t: counts([r for r in pair_rows(B) if r["pair_type"] == t]) for t in ("O-S", "O-R0", "S-R0")}
    deciding = {}
    for r in rows_a:
        k = r["deciding_measure"] or ("tie" if r["outcome"] == "tie" else "unavailable")
        deciding[k] = deciding.get(k, 0) + 1
    # stability and identity
    labels = sorted(key)
    within = sum(1 for lab in labels for m in aqs.MEASURES
                 if lab in A["scores"] and lab in B["scores"] and abs(A["scores"][lab][m] - B["scores"][lab][m]) <= 1)
    exact_ab = sum(1 for lab in labels for m in aqs.MEASURES
                   if lab in A["scores"] and lab in B["scores"] and A["scores"][lab][m] == B["scores"][lab][m])

    def same_cells(s1, s2):
        r1 = {(c["question_id"], c["cell"]): c["ranking"] for c in s1["cells"]}
        return sum(1 for c in s2["cells"] if c["ranking"] is not None
                   and r1[(c["question_id"], c["cell"])] == c["ranking"])

    def norm_hash(r, lab):
        done = OUT / f"run-{r}" / "calls" / lab / "completion.json"
        return rr.read_json(done)["normalized_response_sha256"] if done.exists() else None
    identical_outputs = sum(1 for lab in labels if norm_hash("A", lab) and norm_hash("A", lab) == norm_hash("A2", lab))
    stability = {"scores": len(labels) * len(aqs.MEASURES), "within_1": within,
                 "within_1_share": within / (len(labels) * len(aqs.MEASURES)), "exact": exact_ab,
                 "cells": len(A["cells"]), "same_ranking_cells": same_cells(A, B),
                 "same_ranking_share": same_cells(A, B) / len(A["cells"]),
                 "per_measure_within_1": {m: sum(1 for lab in labels if lab in A["scores"] and lab in B["scores"]
                                                 and abs(A["scores"][lab][m] - B["scores"][lab][m]) <= 1)
                                          for m in aqs.MEASURES},
                 "per_measure_exact": {m: sum(1 for lab in labels if lab in A["scores"] and lab in B["scores"]
                                              and A["scores"][lab][m] == B["scores"][lab][m]) for m in aqs.MEASURES}}
    identity_a2 = {"answers": len(labels), "identical_normalized_outputs": identical_outputs,
                   "identical_score_vectors": sum(1 for lab in labels if lab in A["scores"]
                                                  and A["scores"].get(lab) == A2["scores"].get(lab)),
                   "same_ranking_cells": same_cells(A, A2)}
    all5 = sum(1 for lab in labels if lab in A["scores"] and all(v == 5 for v in A["scores"][lab].values()))
    # per-arm means
    def means(summary):
        out = {}
        for a in ARMS:
            labs = [lab for lab in labels if arm(lab) == a and lab in summary["scores"]]
            out[a] = {"n": len(labs), **{m: statistics.mean(summary["scores"][lab][m] for lab in labs)
                                         if labs else None for m in aqs.MEASURES}}
        return out
    dist = {m: {str(v): sum(1 for lab in labels if A["scores"].get(lab, {}).get(m) == v) for v in range(1, 6)}
            for m in aqs.MEASURES}
    # sham score differences (S - O) per measure, run A
    cell_arm = {}
    for lab in labels:
        cell_arm.setdefault((key[lab]["question_id"], key[lab]["cell"]), {})[arm(lab)] = lab
    def diffs(x, y):
        out = {}
        for m in aqs.MEASURES:
            d = [A["scores"][c[y]][m] - A["scores"][c[x]][m] for c in cell_arm.values()
                 if c.get(x) in A["scores"] and c.get(y) in A["scores"]]
            out[m] = {"n": len(d), "mean": statistics.mean(d) if d else None,
                      "mean_abs": statistics.mean(abs(v) for v in d) if d else None,
                      "sd": statistics.stdev(d) if len(d) > 1 else None, "nonzero": sum(v != 0 for v in d)}
        return out
    # rubric agreement (run A derived direction vs rubric score difference other - base)
    import rubric_quote_match as rqm
    rubric = rqm.score_judge_root(p11b.JUDGE)
    agreement = {}
    for m in ("rubric_v1_strict", "rubric_v1_relaxed", "rubric_v1_raw"):
        a = {"agree": 0, "disagree": 0, "derived_tie_rubric_differs": 0, "derived_win_rubric_tie": 0, "both_tie": 0}
        for r in rows_a:
            sb, so = (rubric.get(r["base_answer"]) or {}).get(m), (rubric.get(r["other_answer"]) or {}).get(m)
            if r["outcome"] is None or sb is None or so is None:
                continue
            d = so - sb
            if r["outcome"] == "tie":
                a["both_tie" if d == 0 else "derived_tie_rubric_differs"] += 1
            elif d == 0:
                a["derived_win_rubric_tie"] += 1
            else:
                a["agree" if (d > 0) == (r["outcome"] == "other") else "disagree"] += 1
        agreement[m] = a
    # diagnostic
    diag = []
    for lab in labels:
        if (key[lab]["question_id"][:12], key[lab]["cell"]) == DIAGNOSTIC:
            row = {"arm": arm(lab), "label": lab}
            for r in RUNS:
                o = aqs.load_scores(OUT / f"run-{r}")[0].get(lab)
                row[r] = None if o is None else {"errors": o["errors"], "fit": o["fit"],
                                                 "scores": {m: o[m]["score"] for m in aqs.MEASURES}}
            diag.append(row)
    diag.sort(key=lambda x: order[x["arm"]])
    cells = [{"question_id": c["question_id"][:12], "condition": c["cell"],
              "ranking": [[arm(i) for i in g] for g in c["ranking"]] if c["ranking"] else None,
              "net_wins": {arm(i): v for i, v in c["net_wins"].items()} if c["net_wins"] else None,
              "scores": {arm(i): A["scores"].get(i) for i in c["items"]}} for c in A["cells"]]
    usage = {r: {k: v for k, v in summaries[r]["usage"].items()} for r in RUNS}
    out = {"schema_version": "docspectra.icsc.scoring_pilot_11e.v1.analysis",
           "created": datetime.date.today().isoformat(), "scope": plan["scope"], "plan_sha256": sha(PLAN),
           "blind_summary_sha256": blind_hashes,
           "receipt_sha256": {r: summaries[r]["receipt_sha256"] for r in RUNS},
           "usage": usage, "unavailable": {r: summaries[r]["unavailable"] for r in RUNS},
           "cells_with_cycles": {r: summaries[r]["cells_with_cycles"] for r in RUNS},
           "by_pair_type_A": by_type, "by_pair_type_B": by_type_b, "deciding_measure_A": deciding,
           "stability_A_vs_B": stability, "identity_A_vs_A2": identity_a2,
           "spread_A": {"answers": len(labels), "all_5": all5, "all_5_share": all5 / len(labels)},
           "score_distribution_A": dist, "per_arm_means_A": means(A), "per_arm_means_B": means(B),
           "per_arm_means_A2": means(A2),
           "score_differences_A": {"S_minus_O": diffs("O", "S"), "R0_minus_O": diffs("O", "R0")},
           "rubric_agreement_A": agreement, "cells_A": cells, "pairs_A": rows_a,
           "diagnostic_3b5890_s1_follow": diag}
    path = rr.CONTEXT / f"evidence/scoring-pilot-11e-analysis-{out['created']}.json"
    print(json.dumps({"path": str(path), "sha256": rr.write_new(path, out), "by_pair_type_A": by_type,
                      "stability": {k: stability[k] for k in ("within_1_share", "same_ranking_share")},
                      "identity_A_vs_A2": identity_a2, "spread": out["spread_A"]}, indent=1))


# ------------------------------------------------------------------ evaluate

def human_agreement(analysis):
    resp = rr.read_json(HUMAN / "responses.json")
    hkey = rr.read_json(HUMAN / "host-private/key.json")
    derived = {}
    for r in analysis["pairs_A"]:
        winner = {"base": r["base_answer"], "other": r["other_answer"]}.get(r["outcome"], r["outcome"])
        derived[frozenset((r["base_answer"], r["other_answer"]))] = winner
    rows = []
    for tid, k in sorted(hkey.items()):
        ranks = resp["responses"][tid]["ranks"]
        labs = sorted(k["answers"])
        for i, a in enumerate(labs):
            for b in labs[i + 1:]:
                ia, ib = k["answers"][a], k["answers"][b]
                human = "tie" if ranks[a] == ranks[b] else (ia if ranks[a] < ranks[b] else ib)
                d = derived.get(frozenset((ia, ib)))
                status = ("reviewer_tie" if human == "tie" else "derived_unavailable" if d is None else
                          "agree" if d == human else "derived_tie" if d == "tie" else "reversal")
                rows.append({"triplet": tid, "question_id": k["question_id"][:12], "condition": k["condition"],
                             "diagnostic": (k["question_id"][:12], k["condition"]) == DIAGNOSTIC, "status": status})

    def tally(rs):
        base = [r for r in rs if r["status"] != "reviewer_tie"]
        n = len(base)
        agree = sum(r["status"] == "agree" for r in base)
        rev = sum(r["status"] == "reversal" for r in base)
        return {"pairs": len(rs), "reviewer_non_tie": n, "agree": agree, "reversal": rev,
                "derived_tie": sum(r["status"] == "derived_tie" for r in base),
                "derived_unavailable": sum(r["status"] == "derived_unavailable" for r in base),
                "agreement_rate": agree / n if n else None, "reversal_rate": rev / n if n else None}
    overall = tally(rows)
    passed = overall["agreement_rate"] is not None and overall["agreement_rate"] >= 0.60 \
        and overall["reversal_rate"] <= 0.20
    return {"status": "pass" if passed else "fail", **overall,
            "diagnostic_only": tally([r for r in rows if r["diagnostic"]]),
            "without_diagnostic": tally([r for r in rows if not r["diagnostic"]]),
            "reviewer_role": resp.get("reviewer_role"), "responses_sha256": sha(HUMAN / "responses.json"),
            "pairs": rows}


def evaluate(args):
    plan = check_plan()
    path_a = Path(args.analysis)
    analysis = rr.read_json(path_a)
    if analysis["plan_sha256"] != sha(PLAN):
        raise SystemExit("analysis-from-another-plan")
    os_ = analysis["by_pair_type_A"]["O-S"]
    sham = abs(os_["other_wins"] - os_["base_wins"])
    st = analysis["stability_A_vs_B"]
    sp = analysis["spread_A"]
    packets = sum(u["packets"] for u in analysis["usage"].values())
    first = sum(u["first_attempt_valid"] for u in analysis["usage"].values())
    max_att = max(u["max_attempts"] for u in analysis["usage"].values())
    crit = {
        "sham_balance": {"O_wins": os_["base_wins"], "S_wins": os_["other_wins"], "ties": os_["ties"],
                         "unavailable": os_["unavailable"], "abs_difference": sham,
                         "status": "pass" if sham <= 4 else "fail"},
        "stability": {"within_1_share": st["within_1_share"], "same_ranking_share": st["same_ranking_share"],
                      "status": "pass" if st["within_1_share"] >= 0.90 and st["same_ranking_share"] >= 0.80
                      else "fail", "reported_only_A_vs_A2": analysis["identity_A_vs_A2"]},
        "spread": {"all_5_share": sp["all_5_share"], "status": "pass" if sp["all_5_share"] <= 0.50 else "fail"},
        "schema_validity": {"packets": packets, "first_attempt_valid": first, "first_attempt_share": first / packets,
                            "max_attempts": max_att,
                            "status": "pass" if first / packets >= 0.95 and max_att <= 2 else "fail"},
    }
    crit["human_agreement"] = human_agreement(analysis) if (HUMAN / "responses.json").exists() else \
        {"status": "pending"}
    sb, stb, ha = (crit[k]["status"] for k in ("sham_balance", "stability", "human_agreement"))
    statuses = [c["status"] for c in crit.values()]
    if sb == "fail" and stb == "fail" and ha == "fail":
        decision = "not_usable"
    elif "fail" in statuses:
        decision = "needs_revision"
    else:
        decision = "qualified_for_triage"
    provisional = ha == "pending"
    wall = sum(u["total_duration_ns"] for u in analysis["usage"].values()) / 1e9
    out = {"schema_version": "docspectra.icsc.scoring_pilot_11e.v1.decision",
           "created": datetime.date.today().isoformat(), "scope": plan["scope"], "plan_sha256": sha(PLAN),
           "analysis": path_a.name, "analysis_sha256": sha(path_a), "criteria": crit, "decision": decision,
           "provisional": provisional,
           "provisional_note": "human agreement pending; decision rests on the automated criteria only"
           if provisional else None, "model_time_s": wall}
    name = f"evidence/scoring-pilot-11e-decision-{'provisional' if provisional else 'final'}-{out['created']}.json"
    path = rr.CONTEXT / name
    print(json.dumps({"path": str(path), "sha256": rr.write_new(path, out), "decision": decision,
                      "provisional": provisional,
                      "criteria": {k: {kk: vv for kk, vv in v.items() if kk != "pairs"} for k, v in crit.items()}},
                     indent=1))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="stage", required=True)
    sub.add_parser("smoke")
    sub.add_parser("freeze")
    sc = sub.add_parser("score")
    sc.add_argument("--run", required=True, choices=sorted(RUNS))
    sc.add_argument("--limit", type=int)
    sub.add_parser("analyse")
    ev = sub.add_parser("evaluate")
    ev.add_argument("--analysis", required=True)
    args = ap.parse_args()
    {"smoke": smoke, "freeze": freeze, "score": score, "analyse": analyse, "evaluate": evaluate}[args.stage](args)


if __name__ == "__main__":
    main()

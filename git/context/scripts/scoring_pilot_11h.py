"""Sprint 11h: scorer v2 unchanged with gpt-6-luna as the scorer (exploratory; third scorer configuration on the
60 11b answers, disclosed; Luna also wrote the answers, disclosed). No human graders (author decision).

Stages, each refusing to overwrite its output:
  smoke     one answer, own folder: validity, field order, usage, reasoning tokens, cost, wall time (no scores)
  freeze    write the 11h plan (prompt, schema, 11e module, the Luna module, the cost-ledger module and this driver
            by SHA-256; the Luna request settings; the $3 stop and its authorization label; seeds; packet identity
            to 11e; the 11e criteria; the decision rule without human agreement; disclosures) before any call
            on the 60 answers
  score     --run A | A2 | B: blind packets (asserted identical to the 11e packets of the same run), one pass plus
            one retry pass, blind summary. All runs share one cost ledger (the $3 hard stop covers the sprint).
  analyse   blind summaries saved first, then the arm key and the rubric scores are joined
  evaluate  criteria against the frozen thresholds; human agreement `not_collected`

Exploratory only: never changes the frozen 11b no-go. The 11e/11f modules, drivers, prompt, schema and evidence
are not modified; this driver imports the 11e driver for inputs and counts only.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import answer_quality_scorer as aqs  # noqa: E402
import answer_quality_scorer_luna as aql  # noqa: E402
import agent_model_provider as amp  # noqa: E402
import restructuring_pilot_11b as p11b  # noqa: E402
import restructuring_runs as rr  # noqa: E402
import scoring_pilot_11e as p11e  # noqa: E402

SEED = p11e.SEED  # 20261015: labels and run order, identical to 11e
NOTES_SEED = p11e.NOTES_SEED
SMOKE_SEED = 3  # differs from the 11e (1) and 11f (2) smoke seeds
AUTHORIZATION = "author-chat-2026-10-08-sprint-11h-luna-scorer-usd3"
CONFIG = aql.LunaConfig(authorization=AUTHORIZATION)
PLAN_DATE = "2026-10-08"
EVIDENCE = rr.CONTEXT / "evidence"
PLAN = EVIDENCE / f"scoring-pilot-11h-plan-{PLAN_DATE}.json"
PLAN_11E = rr.CONTEXT / "evidence/scoring-pilot-11e-plan-2026-10-08.json"
ANALYSIS_11E = rr.CONTEXT / "evidence/scoring-pilot-11e-analysis-2026-10-08.json"
ANALYSIS_11F = rr.CONTEXT / "evidence/scoring-pilot-11f-analysis-2026-10-08.json"
OUT = rr.ROOT / "artifacts/pilot-11b-v3-scorer-v2-luna"
SMOKE = rr.ROOT / "artifacts/pilot-11b-v3-scorer-v2-luna-smoke"
LEDGER = OUT / "cost-ledger.jsonl"  # shared by the smoke and every run: one $3 stop for the sprint
OUT_11E = p11e.OUT
CLIENT = None  # None: the real OpenAI client; tests inject a fake
RUNS = {"A": {"notes_seed": None, "role": "base run: sham balance, spread, ranking, rubric agreement"},
        "A2": {"notes_seed": None, "role": "identical repeat of A (same packets and order; the API has no seed): "
                                         "nondeterminism, reported only"},
        "B": {"notes_seed": NOTES_SEED, "role": "reference notes shuffled per question: stability criterion"}}
DIAGNOSTIC = p11e.DIAGNOSTIC
ARMS = p11e.ARMS
HUMAN_STATUS = {"status": "not_collected",
                "authority": "author decision in chat, 2026-10-08: close the refinement experiment with no human "
                             "graders; the decision rests on the automated criteria only"}
DECISION_RULE = {
    "qualified_for_triage": "every automated criterion (sham_balance, stability, spread, schema_validity) passes",
    "not_usable": "sham_balance and stability both fail",
    "needs_revision": "otherwise",
    "human_agreement": "not_collected by author decision; the 11e rule's human conditions are dropped and the "
                       "decision is final on the automated criteria (not provisional)",
}
DISCLOSURES = {
    "configuration": "third scorer configuration on these same 60 11b answers (11e: qwen3:14b think false, failed "
                     "spread 59/60 all-5; 11f: qwen3:14b think true, failed spread 37/60 all-5). Only the scorer "
                     "model changes; prompt v2, schema v2, ranking rule, criteria, thresholds, seeds and answers are "
                     "unchanged. One configuration is frozen here and not adjusted after outputs are seen.",
    "self_grading": "gpt-6-luna wrote the 60 answers it scores. Every arm is a Luna answer, so self-preference "
                    "shifts all arms alike; it is reported, not corrected.",
    "reproducibility": "closed model: no digest pin. The model string the API reports, response ids, request "
                       "settings and full responses are saved; A vs A2 measures nondeterminism.",
}


def transport(client=None):
    return aql.LunaTransport(CONFIG, LEDGER, client if client is not None else CLIENT)


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity():
    return {"prompt": aqs.PROMPT.name, "prompt_sha256": sha(aqs.PROMPT), "schema": aqs.SCHEMA.name,
            "schema_sha256": sha(aqs.SCHEMA), "base_module_sha256": sha(aqs.__file__),
            "luna_module_sha256": sha(aql.__file__), "ledger_module_sha256": sha(amp.__file__),
            "driver_sha256": sha(__file__), "driver_11e_sha256": sha(p11e.__file__)}


# ------------------------------------------------------------------ smoke, freeze, score

def smoke(_args):
    if not (SMOKE / "packet-manifest.json").exists():
        answers, qs, forbidden = p11e.inputs()
        first = min(answers, key=lambda a: (a["question_id"], a["cell"], a["answer_id"]))
        packets, key = aqs.build_packets([first], qs, SMOKE_SEED)
        aql.write_packets(SMOKE, packets, key, SMOKE_SEED, CONFIG, None, forbidden, run_label="smoke")
    t0 = time.time()
    aql.run(SMOKE, transport(), log=lambda _: None)
    wall = time.time() - t0
    calls = [rr.read_json(p) for p in sorted(SMOKE.glob("calls/*/attempt-[0-9][0-9].json"))]
    out = {"wall_s": round(wall, 1), "cost": aql.cost_stats(SMOKE),
           "calls": [{k: c.get(k) for k in ("valid", "validation_error", "field_order_ok", "usage", "model_reported",
                                             "service_tier", "response_status", "cost_reserved_usd",
                                             "cost_reported_usd")} for c in calls]}
    print(json.dumps(out, indent=1))
    return out


def freeze(args):
    if PLAN.exists():
        raise SystemExit("plan-already-written")
    if any((OUT / f"run-{r}").exists() for r in RUNS):
        raise SystemExit("scorer-run-exists: the plan must freeze before any scorer packet or call")
    plan_11e = rr.read_json(PLAN_11E)
    answers, _, _ = p11e.inputs()
    files = p11b.answer_files()
    digest = hashlib.sha256("".join(f"{p.name} {sha(p)}\n" for p in files).encode()).hexdigest()
    if digest != plan_11e["inputs"]["answer_files_digest"]:
        raise SystemExit("answers-differ-from-11e")
    smoke_calls = [rr.read_json(p) for p in sorted(SMOKE.glob("calls/*/attempt-[0-9][0-9].json"))]
    if not smoke_calls:
        raise SystemExit("smoke-not-run")
    human_key = p11e.HUMAN / "host-private/key.json"
    plan = {
        "schema_version": "docspectra.icsc.scoring_pilot_11h.v1.plan",
        "created": PLAN_DATE,
        "scope": "exploratory: scorer v2 (designed after the frozen 11b no-go) with gpt-6-luna as the scorer; never "
                 "changes the 11b decision; not answer-quality or restructuring-effect evidence for the paper",
        "disclosures": DISCLOSURES,
        "author_decisions": {"date": PLAN_DATE, "channel": "chat",
                             "items": ["run scorer v2 (the ranked category rubric) with Luna",
                                       "close the refinement experiment with no human graders",
                                       "a $3 hard stop for all Luna calls in Sprint 11h"],
                             "reverses": ["the earlier open-weight-judge preference for this endpoint",
                                          "Sprint 12 Stage A text 'human rankers final'"]},
        "frozen_before": "any scorer call on the 60 answers in this configuration (only the toy-schema probe, with "
                         "no study data, and the one-answer smoke in artifacts/pilot-11b-v3-scorer-v2-luna-smoke "
                         f"with seed {SMOKE_SEED} exist; the smoke's scores were not read)",
        "scorer": {**identity(), **CONFIG.as_dict(), "endpoint": "OpenAI Responses API",
                   "sdk": f"openai {aql.SDK_VERSION} (.tools/openai-sdk)", "max_retries_sdk": 0, "timeout_s": 300,
                   "completion_rule": "an attempt whose response status is not 'completed', or with a refusal or no "
                                      "output text, is failed (retry rule applies)",
                   "validity": "local jsonschema validation against schema v2 (unchanged), as in 11e",
                   "measures_priority_order": list(aqs.MEASURES), "field_order": list(aqs.FIELD_ORDER),
                   "max_attempts": aqs.MAX_ATTEMPTS},
        "cost": {"hard_stop_usd": CONFIG.limit_usd, "authorization": AUTHORIZATION,
                 "ledger": "artifacts/pilot-11b-v3-scorer-v2-luna/cost-ledger.jsonl (shared by smoke and all runs)",
                 "reservation_per_call_usd": aql.reservation(CONFIG),
                 "prices_per_million": {"input": 0.10, "cached": 0.01, "cache_write": 0.125, "output": 0.50},
                 "worst_case_reserved_usd": 360 * aql.reservation(CONFIG),
                 "projection_basis": "180 calls plus at most 180 retries at the reservation per call",
                 "author_time_and_cost_confirmation": args.time_confirmation},
        "ranking": plan_11e["ranking"],
        "seeds": {"seed": SEED, "uses": "answer labels and run order, identical to 11e (packets asserted identical "
                                        "to the 11e packets of the same run before any call)",
                  "notes_seed": NOTES_SEED, "notes_rule": plan_11e["seeds"]["notes_rule"],
                  "sampling": "the Responses API has no seed; A2 is an identical repeat of A"},
        "runs": {r: {**v, "packets": len(answers), "root": f"artifacts/pilot-11b-v3-scorer-v2-luna/run-{r}",
                     "stage": f"scoring_pilot_11h.py score --run {r}"} for r, v in RUNS.items()},
        "calls_planned": 3 * len(answers), "endpoint": "OpenAI Responses API (gpt-6-luna)",
        "inputs": {**{k: plan_11e["inputs"][k] for k in ("answers_dir", "answer_files", "answers_scored",
                                                          "answer_files_digest")},
                   "questions_sha256": sha(p11b.QUESTIONS), "judge_aliases_sha256": sha(p11b.JUDGE / "aliases.json"),
                   "rubric_scores_sha256": sha(p11b.RUBRIC_SCORES),
                   "human_key_sha256": sha(human_key) if human_key.exists() else None,
                   "plan_11e_sha256": sha(PLAN_11E)},
        "criteria": {k: v for k, v in plan_11e["criteria"].items() if k != "human_agreement"},
        "human_agreement": HUMAN_STATUS,
        "decision_rule": DECISION_RULE,
        "criteria_note": "the four automated criteria and thresholds are copied unchanged from the 11e plan; "
                         "schema validity counts all 180 packets",
        "author_confirmations": {"date": PLAN_DATE, "channel": "chat, before freezing",
                                 "decision_rule": args.rule_confirmation, "time_and_cost": args.time_confirmation},
        "smoke": {"root": "artifacts/pilot-11b-v3-scorer-v2-luna-smoke", "seed": SMOKE_SEED,
                  "calls": [{k: c.get(k) for k in ("valid", "field_order_ok", "usage", "model_reported",
                                                    "cost_reported_usd")} for c in smoke_calls]},
        "reports": plan_11e["reports"] + ["A vs A2 nondeterminism", "cost reserved and reported",
                                          "11e and 11f numbers beside, descriptively"],
        "prohibited": ["significance tests", "effect claims"],
    }
    digest = rr.write_new(PLAN, plan)
    print(json.dumps({"plan": str(PLAN), "sha256": digest}, indent=1))
    return digest


def check_plan():
    plan = rr.read_json(PLAN)
    if {k: plan["scorer"][k] for k in identity()} != identity():
        raise SystemExit("scorer-identity-drift-since-freeze")
    if {k: plan["scorer"][k] for k in CONFIG.as_dict()} != CONFIG.as_dict():
        raise SystemExit("scorer-config-drift-since-freeze")
    return plan


def score(args):
    plan = check_plan()
    root = OUT / f"run-{args.run}"
    if not (root / "packet-manifest.json").exists():
        answers, qs, forbidden = p11e.inputs()
        notes_seed = plan["runs"][args.run]["notes_seed"]
        packets, key = aqs.build_packets(answers, qs, plan["seeds"]["seed"], notes_seed)
        aql.write_packets(root, packets, key, plan["seeds"]["seed"], CONFIG, notes_seed, forbidden,
                          run_label=args.run)
    same = all(sha(root / f) == sha(OUT_11E / f"run-{args.run}" / f)
               for f in ("packet-manifest.json", "host-private/answer-key.json"))
    print(json.dumps({"run": args.run, "manifest_sha256": sha(root / "packet-manifest.json"),
                      "identical_to_11e_packets": same}), flush=True)
    if not same:
        raise SystemExit("packets-differ-from-11e")
    t0 = time.time()
    aql.run(root, transport(), limit=args.limit, log=lambda line: print(line, flush=True))
    s = aqs.summarize(root)  # blind: labels only
    out = {"run": args.run, "wall_s": round(time.time() - t0, 1),
           "usage": {k: v for k, v in s["usage"].items() if k != "failures"},
           "failures": s["usage"]["failures"], "unavailable": s["unavailable"], "cost": aql.cost_stats(root)}
    print(json.dumps(out, indent=1), flush=True)
    return out


# ------------------------------------------------------------------ analyse

def spread(summary):
    n = summary["usage"]["packets"]
    all5 = sum(1 for s in summary["scores"].values() if all(v == 5 for v in s.values()))
    share = all5 / n if n else None
    return {"answers": n, "all_5": all5, "all_5_share": share}


def analyse(_args):
    plan = check_plan()
    summaries = {}
    for r in RUNS:
        root = OUT / f"run-{r}"
        manifest = rr.read_json(root / "packet-manifest.json")
        if any(not any((root / "calls" / e["packet_id"]).glob("attempt-*.json")) for e in manifest):
            raise SystemExit(f"run-{r}-incomplete")
        rec = rr.read_json(root / "receipt.json")
        if rec["prompt_sha256"] != plan["scorer"]["prompt_sha256"] or rec["seed"] != plan["seeds"]["seed"] \
                or rec["notes_seed"] != plan["runs"][r]["notes_seed"] or rec["model"] != CONFIG.model:
            raise SystemExit(f"run-{r}-does-not-match-plan")
        summaries[r] = aqs.summarize(root)  # blind rankings saved before the arm key is read
    blind_hashes = {r: sha(OUT / f"run-{r}" / "scoring-summary.json") for r in RUNS}
    keys = {r: rr.read_json(OUT / f"run-{r}" / "host-private/answer-key.json") for r in RUNS}
    if any(keys[r].keys() != keys["A"].keys() for r in RUNS):
        raise SystemExit("labels-differ-across-runs")
    key = keys["A"]
    # ---- unblinding starts here
    arm_of = {v: a for a, v in p11b.variant_ids().items()}
    arm = lambda label: arm_of["-".join(key[label]["answer_id"].split("-")[:2])]  # noqa: E731
    order = {a: i for i, a in enumerate(ARMS)}
    A = summaries["A"]
    labels = sorted(key)

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
    by_type = {r: {t: p11e.counts([x for x in pair_rows(summaries[r]) if x["pair_type"] == t])
                   for t in ("O-S", "O-R0", "S-R0")} for r in RUNS}
    deciding = {}
    for r in rows_a:
        k = r["deciding_measure"] or ("tie" if r["outcome"] == "tie" else "unavailable")
        deciding[k] = deciding.get(k, 0) + 1

    def same_cells(s1, s2):
        r1 = {(c["question_id"], c["cell"]): c["ranking"] for c in s1["cells"]}
        return sum(1 for c in s2["cells"] if c["ranking"] is not None
                   and r1[(c["question_id"], c["cell"])] == c["ranking"])

    def agreement_between(s1, s2):
        both = [lab for lab in labels if lab in s1["scores"] and lab in s2["scores"]]
        n = len(labels) * len(aqs.MEASURES)
        within = sum(1 for lab in both for m in aqs.MEASURES if abs(s1["scores"][lab][m] - s2["scores"][lab][m]) <= 1)
        exact = sum(1 for lab in both for m in aqs.MEASURES if s1["scores"][lab][m] == s2["scores"][lab][m])
        return {"scores": n, "within_1": within, "within_1_share": within / n, "exact": exact,
                "identical_score_vectors": sum(1 for lab in both if s1["scores"][lab] == s2["scores"][lab]),
                "cells": len(s1["cells"]), "same_ranking_cells": same_cells(s1, s2),
                "same_ranking_share": same_cells(s1, s2) / len(s1["cells"]),
                "per_measure_within_1": {m: sum(1 for lab in both if abs(s1["scores"][lab][m] - s2["scores"][lab][m])
                                                <= 1) for m in aqs.MEASURES},
                "per_measure_exact": {m: sum(1 for lab in both if s1["scores"][lab][m] == s2["scores"][lab][m])
                                      for m in aqs.MEASURES}}

    def means(summary):
        out = {}
        for a in ARMS:
            labs = [lab for lab in labels if arm(lab) == a and lab in summary["scores"]]
            out[a] = {"n": len(labs), **{m: statistics.mean(summary["scores"][lab][m] for lab in labs)
                                         if labs else None for m in aqs.MEASURES}}
        return out
    dist = {m: {str(v): sum(1 for lab in labels if A["scores"].get(lab, {}).get(m) == v) for v in range(1, 6)}
            for m in aqs.MEASURES}
    outputs_a = aqs.load_scores(OUT / "run-A")[0]
    errors_listed = {"answers_with_errors": sum(1 for o in outputs_a.values() if o["errors"]),
                     "errors_total": sum(len(o["errors"]) for o in outputs_a.values()),
                     "answers_scored": len(outputs_a)}
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
    diag = []
    for lab in labels:
        if (key[lab]["question_id"][:12], key[lab]["cell"]) == DIAGNOSTIC:
            row = {"arm": arm(lab), "label": lab}
            for r in RUNS:
                o = aqs.load_scores(OUT / f"run-{r}")[0].get(lab)
                row[r] = None if o is None else {"errors": o["errors"],
                                                 **{m: o[m] for m in aqs.MEASURES}}
            diag.append(row)
    diag.sort(key=lambda x: order[x["arm"]])
    cells = [{"question_id": c["question_id"][:12], "condition": c["cell"],
              "ranking": [[arm(i) for i in g] for g in c["ranking"]] if c["ranking"] else None,
              "net_wins": {arm(i): v for i, v in c["net_wins"].items()} if c["net_wins"] else None,
              "scores": {arm(i): A["scores"].get(i) for i in c["items"]}} for c in A["cells"]]
    prior = {}
    for name, path in (("11e", ANALYSIS_11E), ("11f", ANALYSIS_11F)):
        if path.exists():
            p = rr.read_json(path)
            prior[name] = {k: p.get(k) for k in ("spread_A", "by_pair_type_A", "per_arm_means_A",
                                                 "deciding_measure_A", "rubric_agreement_A")}
            if name == "11f":
                prior[name]["by_pair_type_A"] = p["by_pair_type"]["A"]
                prior[name]["per_arm_means_A"] = p["per_arm_means"]["A"]
    out = {"schema_version": "docspectra.icsc.scoring_pilot_11h.v1.analysis",
           "created": datetime.date.today().isoformat(), "scope": plan["scope"], "disclosures": plan["disclosures"],
           "plan_sha256": sha(PLAN), "runs": list(RUNS), "blind_summary_sha256": blind_hashes,
           "receipt_sha256": {r: summaries[r]["receipt_sha256"] for r in RUNS},
           "usage": {r: summaries[r]["usage"] for r in RUNS},
           "cost": {r: aql.cost_stats(OUT / f"run-{r}") for r in RUNS},
           "unavailable": {r: summaries[r]["unavailable"] for r in RUNS},
           "cells_with_cycles": {r: summaries[r]["cells_with_cycles"] for r in RUNS},
           "by_pair_type": by_type, "by_pair_type_A": by_type["A"], "deciding_measure_A": deciding,
           "stability_A_vs_B": agreement_between(A, summaries["B"]),
           "nondeterminism_A_vs_A2": agreement_between(A, summaries["A2"]),
           "spread_A": spread(A), "errors_listed_A": errors_listed, "score_distribution_A": dist,
           "per_arm_means": {r: means(summaries[r]) for r in RUNS}, "per_arm_means_A": means(A),
           "score_differences_A": {"S_minus_O": diffs("O", "S"), "R0_minus_O": diffs("O", "R0")},
           "rubric_agreement_A": agreement, "cells_A": cells, "pairs_A": rows_a,
           "diagnostic_3b5890_s1_follow": diag, "prior_configurations": prior,
           "human_agreement": HUMAN_STATUS}
    path = EVIDENCE / f"scoring-pilot-11h-analysis-{out['created']}.json"
    digest = rr.write_new(path, out)
    print(json.dumps({"path": str(path), "sha256": digest, "by_pair_type_A": by_type["A"], "spread": out["spread_A"],
                      "stability": {k: out["stability_A_vs_B"][k] for k in ("within_1_share", "same_ranking_share")},
                      "nondeterminism": {k: out["nondeterminism_A_vs_A2"][k]
                                         for k in ("identical_score_vectors", "same_ranking_cells")}}, indent=1))
    return path


# ------------------------------------------------------------------ evaluate

def evaluate_criteria(analysis, criteria_thresholds=None):
    os_ = analysis["by_pair_type"]["A"]["O-S"]
    sham = abs(os_["other_wins"] - os_["base_wins"])
    st, sp = analysis["stability_A_vs_B"], analysis["spread_A"]
    packets = sum(u["packets"] for u in analysis["usage"].values())
    first = sum(u["first_attempt_valid"] for u in analysis["usage"].values())
    max_att = max(u["max_attempts"] for u in analysis["usage"].values())
    crit = {
        "sham_balance": {"O_wins": os_["base_wins"], "S_wins": os_["other_wins"], "ties": os_["ties"],
                         "unavailable": os_["unavailable"], "abs_difference": sham,
                         "status": "pass" if sham <= 4 else "fail"},
        "stability": {"within_1_share": st["within_1_share"], "same_ranking_share": st["same_ranking_share"],
                      "status": "pass" if st["within_1_share"] >= 0.90 and st["same_ranking_share"] >= 0.80
                      else "fail"},
        "spread": {"all_5_share": sp["all_5_share"], "status": "pass" if sp["all_5_share"] <= 0.50 else "fail"},
        "schema_validity": {"packets": packets, "first_attempt_valid": first, "first_attempt_share": first / packets,
                            "max_attempts": max_att,
                            "status": "pass" if first / packets >= 0.95 and max_att <= 2 else "fail"},
    }
    crit["stability"]["reported_only_A_vs_A2"] = {k: analysis["nondeterminism_A_vs_A2"][k] for k in
                                                  ("within_1_share", "identical_score_vectors", "same_ranking_share")}
    crit["human_agreement"] = HUMAN_STATUS
    if crit["sham_balance"]["status"] == "fail" and crit["stability"]["status"] == "fail":
        decision = "not_usable"
    elif any(crit[k]["status"] == "fail" for k in ("sham_balance", "stability", "spread", "schema_validity")):
        decision = "needs_revision"
    else:
        decision = "qualified_for_triage"
    return crit, decision


def evaluate(args):
    plan = check_plan()
    path_a = Path(args.analysis)
    analysis = rr.read_json(path_a)
    if analysis["plan_sha256"] != sha(PLAN):
        raise SystemExit("analysis-from-another-plan")
    crit, decision = evaluate_criteria(analysis)
    out = {"schema_version": "docspectra.icsc.scoring_pilot_11h.v1.decision",
           "created": datetime.date.today().isoformat(), "scope": plan["scope"], "disclosures": plan["disclosures"],
           "plan_sha256": sha(PLAN), "analysis": path_a.name, "analysis_sha256": sha(path_a), "criteria": crit,
           "decision_rule": plan["decision_rule"], "decision": decision, "provisional": False,
           "cost": {r: analysis["cost"][r] for r in analysis["cost"]},
           "model_time_s": sum(u["total_duration_ns"] for u in analysis["usage"].values()) / 1e9}
    path = EVIDENCE / f"scoring-pilot-11h-decision-{out['created']}.json"
    digest = rr.write_new(path, out)
    print(json.dumps({"path": str(path), "sha256": digest, "decision": decision,
                      "criteria": {k: {kk: vv for kk, vv in v.items() if kk != "reported_only_A_vs_A2"}
                                   for k, v in crit.items()}}, indent=1))
    return path


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="stage", required=True)
    sub.add_parser("smoke")
    fr = sub.add_parser("freeze")
    fr.add_argument("--rule-confirmation", required=True)
    fr.add_argument("--time-confirmation", required=True)
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

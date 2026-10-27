"""Sprint 11f: scorer v2 unchanged on qwen3:14b with thinking on (exploratory; second configuration on the
60 11b answers, disclosed).

Stages, each refusing to overwrite its output:
  smoke     one answer, own folder, smoke seed: validity, field order, trace presence and length, wall time
            (prints no scores)
  freeze    write the 11f plan (prompt, schema, both modules and this driver by SHA-256; model digest and full
            options; seeds; run gate; the 11e criteria and decision rule copied from the 11e plan; human key
            hash; disclosure) before any call on the 60 answers
  score     --run A | A2 | B: blind packets (asserted identical to the 11e packets for the same run), one pass
            plus one retry pass, blind summary. A2 and B refuse to start unless the blind run gate passed on A.
  gate      blind: spread on run A (all-5 share); writes gate.json under the run folder
  analyse   blind summaries saved first, then the arm key, rubric scores and human key are joined
  evaluate  criteria against the frozen thresholds; stability "skipped_by_gate" when A2/B did not run

Exploratory only: never changes the frozen 11b no-go. The 11e module, driver, prompt, schema and evidence are
not modified; this driver imports the 11e driver for inputs, counts and the human-agreement tally only.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import answer_quality_scorer as aqs  # noqa: E402
import answer_quality_scorer_config as aqsc  # noqa: E402
import restructuring_pilot_11b as p11b  # noqa: E402
import restructuring_runs as rr  # noqa: E402
import scoring_pilot_11e as p11e  # noqa: E402

SEED = p11e.SEED  # 20261015: labels and run order, identical to 11e
NOTES_SEED = p11e.NOTES_SEED
SMOKE_SEED = 2  # differs from the plan seed and from the 11e smoke seed (1)
SAMPLING = {"temperature": 0.6, "top_p": 0.95, "top_k": 20}  # Qwen thinking-mode guidance
LIMITS = {"num_ctx": 32768, "num_predict": 16384}  # num_predict: thinking-loop guard; length => failed attempt
SAMPLING_SEEDS = {"A": SEED, "A2": SEED + 1, "B": SEED, "smoke": SMOKE_SEED}
PLAN_DATE = "2026-10-08"
PLAN = rr.CONTEXT / f"evidence/scoring-pilot-11f-plan-{PLAN_DATE}.json"
PLAN_11E = rr.CONTEXT / "evidence/scoring-pilot-11e-plan-2026-10-08.json"
ANALYSIS_11E = rr.CONTEXT / "evidence/scoring-pilot-11e-analysis-2026-10-08.json"
OUT = rr.ROOT / "artifacts/pilot-11b-v3-scorer-v2-think"
SMOKE = rr.ROOT / "artifacts/pilot-11b-v3-scorer-v2-think-smoke"
OUT_11E = p11e.OUT
RUNS = {"A": {"notes_seed": None, "role": "base run; gate, sham balance, spread, ranking, human agreement"},
        "A2": {"notes_seed": None, "role": "rerun of A with a different sampling seed (same packets and order); "
                                         "sampling noise, reported only"},
        "B": {"notes_seed": NOTES_SEED, "role": "reference notes shuffled per question, A's sampling seed; "
                                                "stability criterion"}}
GATE = {"rule": "after run A, compute spread blind (share of answers all-5); if it fails (> 0.50) the "
                "configuration cannot qualify, so A2 and B are skipped, stability is 'skipped_by_gate' and the "
                "decision is needs_revision; otherwise run A2 and B",
        "spread_threshold": 0.50, "independent_of": "which arm wins (spread is computed on labels only)"}
DIAGNOSTIC = p11e.DIAGNOSTIC
ARMS = p11e.ARMS
TRACE_PATTERN = re.compile(r"constraint|GitHub Actions|Docker Hub|autobuild|automated build", re.I)


def config(run):
    return aqsc.ScorerConfig(aqs.MODEL, aqs.EXPECTED_DIGEST, True,
                             {**SAMPLING, "seed": SAMPLING_SEEDS[run], **LIMITS})


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity():
    return {"prompt": aqs.PROMPT.name, "prompt_sha256": sha(aqs.PROMPT), "schema": aqs.SCHEMA.name,
            "schema_sha256": sha(aqs.SCHEMA), "base_module_sha256": sha(aqs.__file__),
            "config_module_sha256": sha(aqsc.__file__), "driver_sha256": sha(__file__),
            "driver_11e_sha256": sha(p11e.__file__), "transport_module_sha256": sha(aqs.apr.__file__)}


# ------------------------------------------------------------------ gate (blind)

def spread(summary, threshold=GATE["spread_threshold"]):
    """Share of packets scoring 5 on every measure; an unavailable packet is not all-5."""
    n = summary["usage"]["packets"]
    all5 = sum(1 for s in summary["scores"].values() if all(v == 5 for v in s.values()))
    share = all5 / n if n else None
    return {"answers": n, "all_5": all5, "all_5_share": share,
            "status": "pass" if share is not None and share <= threshold else "fail"}


def may_run(run, gate_record):
    """A always runs; A2 and B need a recorded gate on A that passed."""
    if run == "A":
        return True, None
    if gate_record is None:
        return False, "gate-not-evaluated: run `gate` after run A"
    if gate_record["spread_A"]["status"] != "pass":
        return False, "skipped-by-gate: spread failed on run A"
    return True, None


# ------------------------------------------------------------------ smoke, freeze, score

def smoke(_args):
    if not (SMOKE / "packet-manifest.json").exists():
        answers, qs, forbidden = p11e.inputs()
        first = min(answers, key=lambda a: (a["question_id"], a["cell"], a["answer_id"]))
        packets, key = aqs.build_packets([first], qs, SMOKE_SEED)
        aqsc.write_packets(SMOKE, packets, key, SMOKE_SEED, config("smoke"), None, forbidden, run_label="smoke")
    t0 = time.time()
    aqsc.run(SMOKE, config("smoke"), log=lambda _: None)
    wall = time.time() - t0
    s = aqs.summarize(SMOKE)
    calls = [rr.read_json(p) for p in sorted(SMOKE.glob("calls/*/attempt-[0-9][0-9].json"))]
    print(json.dumps({"receipt_sha256": s["receipt_sha256"], "wall_s": round(wall, 1),
                      "usage": {k: v for k, v in s["usage"].items()}, "thinking": aqsc.thinking_stats(SMOKE),
                      "calls": [{k: c.get(k) for k in ("valid", "validation_error", "field_order_ok", "field_order",
                                                        "usage", "thinking_present", "thinking_chars",
                                                        "thinking_sha256", "digest_before", "digest_after")}
                                for c in calls]}, indent=1))


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
    plan = {
        "schema_version": "docspectra.icsc.scoring_pilot_11f.v1.plan",
        "created": PLAN_DATE,
        "scope": "exploratory: scorer v2 (designed after the frozen 11b no-go) with qwen thinking on; never "
                 "changes the 11b decision; not answer-quality or restructuring-effect evidence for the paper",
        "disclosure": "second scorer configuration qualified on these same 60 11b answers (11e was the first, "
                      "think false, temperature 0; it failed spread with 59/60 all-5). Only the model "
                      "configuration changes; prompt v2, schema v2, ranking rule, criteria, thresholds, decision "
                      "rule, seed rule and answers are unchanged. The reason for the change is the 11e spread "
                      "failure, which does not depend on which arm wins. One configuration is frozen here and is "
                      "not adjusted after outputs are seen.",
        "frozen_before": "any scorer call on the 60 answers in this configuration (only the one-answer smoke exists, "
                         f"in artifacts/pilot-11b-v3-scorer-v2-think-smoke with seed {SMOKE_SEED}; its scores were "
                         "not read)",
        "scorer": {**identity(), "model": aqs.MODEL, "expected_digest": aqs.EXPECTED_DIGEST, "think": True,
                   "options_by_run": {r: config(r).options for r in RUNS}, "format": "schema v2",
                   "done_reason_rule": "an attempt whose done_reason is not 'stop' is failed (retry rule applies)",
                   "thinking_trace": "message.thinking saved per attempt (attempt-NN.thinking.txt) and hashed; a "
                                     "missing trace is tolerated and recorded",
                   "measures_priority_order": list(aqs.MEASURES), "field_order": list(aqs.FIELD_ORDER),
                   "max_attempts": aqs.MAX_ATTEMPTS},
        "ranking": plan_11e["ranking"],
        "seeds": {"seed": SEED, "uses": "answer labels and run order, identical to 11e (packets asserted identical "
                                        "to the 11e packets of the same run before any call)",
                  "notes_seed": NOTES_SEED, "notes_rule": plan_11e["seeds"]["notes_rule"],
                  "sampling_seeds": {r: SAMPLING_SEEDS[r] for r in RUNS},
                  "sampling_seed_rule": "Ollama option `seed`, same for every call in a run; A2 differs from A so "
                                        "A vs A2 measures sampling noise (probe: the same seed replays "
                                        "byte-identically while the model stays loaded); B uses A's seed"},
        "runs": {r: {**v, "packets": len(answers), "root": f"artifacts/pilot-11b-v3-scorer-v2-think/run-{r}",
                     "stage": f"scoring_pilot_11f.py score --run {r}"} for r, v in RUNS.items()},
        "run_gate": GATE,
        "calls_planned": {"if_gate_fails": len(answers), "if_gate_passes": 3 * len(answers)}, "cost_usd": 0,
        "endpoint": "localhost Ollama only",
        "inputs": {**{k: plan_11e["inputs"][k] for k in ("answers_dir", "answer_files", "answers_scored",
                                                          "answer_files_digest")},
                   "questions_sha256": sha(p11b.QUESTIONS), "judge_aliases_sha256": sha(p11b.JUDGE / "aliases.json"),
                   "rubric_scores_sha256": sha(p11b.RUBRIC_SCORES),
                   "human_key_sha256": sha(p11e.HUMAN / "host-private/key.json"),
                   "human_packet_sha256": sha(p11e.HUMAN / "packet.md"), "plan_11e_sha256": sha(PLAN_11E)},
        "criteria": plan_11e["criteria"],
        "decision_rule": plan_11e["decision_rule"],
        "criteria_note": "copied unchanged from the 11e plan; with the gate, stability is 'skipped_by_gate' (not a "
                         "pass) when A2/B do not run, and schema validity counts the packets actually run",
        "author_confirmations": {"date": PLAN_DATE, "channel": "chat, before freezing",
                                 "sampling": args.sampling_confirmation, "time_estimate": args.time_confirmation},
        "smoke": {"root": "artifacts/pilot-11b-v3-scorer-v2-think-smoke", "seed": SMOKE_SEED,
                  "calls": [{k: c.get(k) for k in ("valid", "field_order_ok", "thinking_present", "thinking_chars",
                                                    "usage")} for c in smoke_calls]},
        "reports": plan_11e["reports"] + ["thinking trace presence and median length", "A vs A2 sampling noise (or "
                                          "skipped by the gate)", "3b5890 trace excerpts on the route-versus-"
                                          "constraint rule", "11e think-off numbers beside, descriptively"],
    }
    print(json.dumps({"plan": str(PLAN), "sha256": rr.write_new(PLAN, plan)}, indent=1))


def check_plan():
    plan = rr.read_json(PLAN)
    if {k: plan["scorer"][k] for k in identity()} != identity():
        raise SystemExit("scorer-identity-drift-since-freeze")
    for r in RUNS:
        if plan["scorer"]["options_by_run"][r] != config(r).options:
            raise SystemExit("scorer-options-drift-since-freeze")
    return plan


def read_gate():
    path = OUT / "gate.json"
    return rr.read_json(path) if path.exists() else None


def score(args):
    plan = check_plan()
    ok, why = may_run(args.run, read_gate())
    if not ok:
        raise SystemExit(why)
    root = OUT / f"run-{args.run}"
    if not (root / "packet-manifest.json").exists():
        answers, qs, forbidden = p11e.inputs()
        notes_seed = plan["runs"][args.run]["notes_seed"]
        packets, key = aqs.build_packets(answers, qs, plan["seeds"]["seed"], notes_seed)
        receipt = aqsc.write_packets(root, packets, key, plan["seeds"]["seed"], config(args.run), notes_seed,
                                     forbidden, run_label=args.run)
        same = sha(root / "packet-manifest.json") == sha(OUT_11E / f"run-{args.run}" / "packet-manifest.json")
        print(json.dumps({"run": args.run, "packets": receipt["packets"], "manifest_sha256": receipt["manifest_sha256"],
                          "identical_to_11e_packets": same}), flush=True)
        if not same:
            raise SystemExit("packets-differ-from-11e")
    t0 = time.time()
    aqsc.run(root, config(args.run), limit=args.limit)
    s = aqs.summarize(root)  # blind: labels only
    print(json.dumps({"run": args.run, "wall_s": round(time.time() - t0, 1),
                      "usage": {k: v for k, v in s["usage"].items() if k != "failures"},
                      "failures": s["usage"]["failures"], "unavailable": s["unavailable"],
                      "thinking": aqsc.thinking_stats(root)}, indent=1))


def gate(_args):
    check_plan()
    if (OUT / "gate.json").exists():
        raise SystemExit("gate-already-written")
    root = OUT / "run-A"
    manifest = rr.read_json(root / "packet-manifest.json")
    if any(not any((root / "calls" / e["packet_id"]).glob("attempt-*.json")) for e in manifest):
        raise SystemExit("run-A-incomplete")
    s = aqs.summarize(root)
    record = {"schema_version": "docspectra.icsc.scoring_pilot_11f.v1.gate", "created_utc": aqs.now(),
              "plan_sha256": sha(PLAN), "blind_summary_A_sha256": sha(root / "scoring-summary.json"),
              "rule": GATE["rule"], "spread_A": spread(s)}
    record["next"] = "run A2 and B" if record["spread_A"]["status"] == "pass" else "stop: A2 and B skipped by the gate"
    rr.write_json(OUT / "gate.json", record)
    print(json.dumps({**record, "sha256": sha(OUT / "gate.json")}, indent=1))


# ------------------------------------------------------------------ analyse

def excerpts(text, pattern=TRACE_PATTERN, width=350, limit=4):
    """Non-overlapping windows of the trace around matches (route-versus-constraint handling)."""
    out, end = [], -1
    for m in pattern.finditer(text or ""):
        if m.start() < end:
            continue
        a, end = max(0, m.start() - width), min(len(text), m.end() + width)
        out.append(text[a:end])
        if len(out) >= limit:
            break
    return out


def analyse(_args):
    plan = check_plan()
    g = read_gate()
    if g is None:
        raise SystemExit("gate-not-evaluated")
    runs = ["A"] + (["A2", "B"] if g["spread_A"]["status"] == "pass" else [])
    summaries = {}
    for r in runs:
        root = OUT / f"run-{r}"
        manifest = rr.read_json(root / "packet-manifest.json")
        if any(not any((root / "calls" / e["packet_id"]).glob("attempt-*.json")) for e in manifest):
            raise SystemExit(f"run-{r}-incomplete")
        rec = rr.read_json(root / "receipt.json")
        if rec["prompt_sha256"] != plan["scorer"]["prompt_sha256"] or rec["seed"] != plan["seeds"]["seed"] \
                or rec["notes_seed"] != plan["runs"][r]["notes_seed"] \
                or rec["options"] != plan["scorer"]["options_by_run"][r]:
            raise SystemExit(f"run-{r}-does-not-match-plan")
        summaries[r] = aqs.summarize(root)  # blind rankings saved before the arm key is read
    blind_hashes = {r: sha(OUT / f"run-{r}" / "scoring-summary.json") for r in runs}
    keys = {r: rr.read_json(OUT / f"run-{r}" / "host-private/answer-key.json") for r in runs}
    if any(keys[r].keys() != keys["A"].keys() for r in runs):
        raise SystemExit("labels-differ-across-runs")
    key = keys["A"]
    # ---- unblinding starts here
    ids = p11b.variant_ids()
    arm_of = {v: a for a, v in ids.items()}
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
                   for t in ("O-S", "O-R0", "S-R0")} for r in runs}
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
    skipped = {"status": "skipped_by_gate", "gate_sha256": sha(OUT / "gate.json")}
    stability = agreement_between(A, summaries["B"]) if "B" in runs else skipped
    sampling_noise = agreement_between(A, summaries["A2"]) if "A2" in runs else skipped
    sp = spread(A)

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
            for r in runs:
                o = aqs.load_scores(OUT / f"run-{r}")[0].get(lab)
                done = OUT / f"run-{r}" / "calls" / lab / "completion.json"
                trace = None
                if done.exists():
                    t = OUT / f"run-{r}" / "calls" / lab / f"attempt-{rr.read_json(done)['attempt']:02d}.thinking.txt"
                    trace = t.read_text(encoding="utf-8") if t.exists() else None
                row[r] = None if o is None else {"errors": o["errors"], "fit": o["fit"],
                                                 "scores": {m: o[m]["score"] for m in aqs.MEASURES},
                                                 "thinking_chars": len(trace or ""),
                                                 "thinking_excerpts": excerpts(trace)}
            diag.append(row)
    diag.sort(key=lambda x: order[x["arm"]])
    cells = [{"question_id": c["question_id"][:12], "condition": c["cell"],
              "ranking": [[arm(i) for i in g] for g in c["ranking"]] if c["ranking"] else None,
              "net_wins": {arm(i): v for i, v in c["net_wins"].items()} if c["net_wins"] else None,
              "scores": {arm(i): A["scores"].get(i) for i in c["items"]}} for c in A["cells"]]
    a11e = rr.read_json(ANALYSIS_11E)
    scores_11e = rr.read_json(OUT_11E / "run-A/scoring-summary.json")["scores"]
    comparison_11e = {"note": "descriptive only; 11e = think false, temperature 0; same packets and labels",
                      "spread_A": {"11e": a11e["spread_A"], "11f": sp},
                      "by_pair_type_A": {"11e": a11e["by_pair_type_A"], "11f": by_type["A"]},
                      "per_arm_means_A": {"11e": a11e["per_arm_means_A"], "11f": means(A)},
                      "deciding_measure_A": {"11e": a11e["deciding_measure_A"], "11f": deciding},
                      "rubric_agreement_A": {"11e": a11e["rubric_agreement_A"], "11f": agreement},
                      "score_vectors_identical_to_11e_A": sum(1 for lab in labels if lab in A["scores"]
                                                              and scores_11e.get(lab) == A["scores"][lab])}
    out = {"schema_version": "docspectra.icsc.scoring_pilot_11f.v1.analysis",
           "created": datetime.date.today().isoformat(), "scope": plan["scope"], "disclosure": plan["disclosure"],
           "plan_sha256": sha(PLAN), "gate": g, "gate_sha256": sha(OUT / "gate.json"), "runs": runs,
           "blind_summary_sha256": blind_hashes,
           "receipt_sha256": {r: summaries[r]["receipt_sha256"] for r in runs},
           "usage": {r: summaries[r]["usage"] for r in runs},
           "thinking": {r: aqsc.thinking_stats(OUT / f"run-{r}") for r in runs},
           "unavailable": {r: summaries[r]["unavailable"] for r in runs},
           "cells_with_cycles": {r: summaries[r]["cells_with_cycles"] for r in runs},
           "by_pair_type": by_type, "deciding_measure_A": deciding, "stability_A_vs_B": stability,
           "sampling_noise_A_vs_A2": sampling_noise, "spread_A": sp, "errors_listed_A": errors_listed,
           "score_distribution_A": dist, "per_arm_means": {r: means(summaries[r]) for r in runs},
           "score_differences_A": {"S_minus_O": diffs("O", "S"), "R0_minus_O": diffs("O", "R0")},
           "rubric_agreement_A": agreement, "cells_A": cells, "pairs_A": rows_a,
           "diagnostic_3b5890_s1_follow": diag, "comparison_11e": comparison_11e}
    path = rr.CONTEXT / f"evidence/scoring-pilot-11f-analysis-{out['created']}.json"
    print(json.dumps({"path": str(path), "sha256": rr.write_new(path, out), "by_pair_type_A": by_type["A"],
                      "spread": sp, "stability": stability if "B" not in runs else
                      {k: stability[k] for k in ("within_1_share", "same_ranking_share")}}, indent=1))


# ------------------------------------------------------------------ evaluate

def evaluate(args):
    plan = check_plan()
    path_a = Path(args.analysis)
    analysis = rr.read_json(path_a)
    if analysis["plan_sha256"] != sha(PLAN):
        raise SystemExit("analysis-from-another-plan")
    os_ = analysis["by_pair_type"]["A"]["O-S"]
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
        "stability": st if st.get("status") == "skipped_by_gate" else
        {"within_1_share": st["within_1_share"], "same_ranking_share": st["same_ranking_share"],
         "status": "pass" if st["within_1_share"] >= 0.90 and st["same_ranking_share"] >= 0.80 else "fail"},
        "spread": {"all_5_share": sp["all_5_share"], "status": "pass" if sp["all_5_share"] <= 0.50 else "fail"},
        "schema_validity": {"packets": packets, "first_attempt_valid": first, "first_attempt_share": first / packets,
                            "max_attempts": max_att,
                            "status": "pass" if first / packets >= 0.95 and max_att <= 2 else "fail"},
    }
    crit["stability"]["reported_only_A_vs_A2"] = analysis["sampling_noise_A_vs_A2"]
    crit["human_agreement"] = p11e.human_agreement(analysis) \
        if (p11e.HUMAN / "responses.json").exists() else {"status": "pending"}
    sb, stb, ha = (crit[k]["status"] for k in ("sham_balance", "stability", "human_agreement"))
    statuses = [c["status"] for c in crit.values()]
    if sb == "fail" and stb == "fail" and ha == "fail":
        decision = "not_usable"
    elif "fail" in statuses or "skipped_by_gate" in statuses:
        decision = "needs_revision"
    else:
        decision = "qualified_for_triage"
    provisional = ha == "pending"
    wall = sum(u["total_duration_ns"] for u in analysis["usage"].values()) / 1e9
    out = {"schema_version": "docspectra.icsc.scoring_pilot_11f.v1.decision",
           "created": datetime.date.today().isoformat(), "scope": plan["scope"], "disclosure": plan["disclosure"],
           "plan_sha256": sha(PLAN), "analysis": path_a.name, "analysis_sha256": sha(path_a), "criteria": crit,
           "decision": decision, "provisional": provisional,
           "provisional_note": "human agreement pending; decision rests on the automated criteria only"
           if provisional else None, "model_time_s": wall}
    name = f"evidence/scoring-pilot-11f-decision-{'provisional' if provisional else 'final'}-{out['created']}.json"
    path = rr.CONTEXT / name
    print(json.dumps({"path": str(path), "sha256": rr.write_new(path, out), "decision": decision,
                      "provisional": provisional,
                      "criteria": {k: {kk: vv for kk, vv in v.items() if kk not in ("pairs", "reported_only_A_vs_A2")}
                                   for k, v in crit.items()}}, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="stage", required=True)
    sub.add_parser("smoke")
    fr = sub.add_parser("freeze")
    fr.add_argument("--sampling-confirmation", required=True)
    fr.add_argument("--time-confirmation", required=True)
    sc = sub.add_parser("score")
    sc.add_argument("--run", required=True, choices=sorted(RUNS))
    sc.add_argument("--limit", type=int)
    sub.add_parser("gate")
    sub.add_parser("analyse")
    ev = sub.add_parser("evaluate")
    ev.add_argument("--analysis", required=True)
    args = ap.parse_args()
    {"smoke": smoke, "freeze": freeze, "score": score, "gate": gate, "analyse": analyse,
     "evaluate": evaluate}[args.stage](args)


if __name__ == "__main__":
    main()

"""Sprint 11b: restructuring development pilot (O, S, one R0 realization).

Stages, each refusing to run twice:
  freeze   write the pilot run manifest (live, every field resolved), the five
           development questions with their rubric elements, the shuffled answer
           schedule and the go/no-go criteria; nothing has run yet
  edit     O and S copies, then one live Codex R0 session (ChatGPT subscription)
  admit    pipeline and edit-substance criteria on the frozen terminals, then a
           blind export (opaque variant ids; arm key outside the packet)
  answer   60 Luna episodes (3 variants x 5 questions x 4 conditions), shuffled
  judge    local triage judge on the core rubric elements, blind to arm
  analyse  per-cell tables and the decision against the frozen criteria only

Sprint 11c additions (exploratory ranking channel; never rewrite the stages above):
  rank             blind pairwise ranker (answer_pairwise_ranker) on the answers, by opaque variant id
  rank-smoke       one O-S pair in both orders, a transport check only
  rescore-rubric   strict / relaxed / raw rubric scores from the saved judge outputs
  analyse-ranking  joins ranker outputs, the three rubric scores and the arm key, after both channels

Development evidence only: questions are never reused as confirmatory, nothing
here is tuned on answer outcomes, and no significance is computed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import docker_corpus_variants as dcv  # noqa: E402
import restructuring_runs as rr  # noqa: E402

DATE = "2026-10-08"
RUN_ID = "pilot-11b-v3"  # v1, v2: excluded attempts (see prior_attempts in the plan)
V6 = rr.CONTEXT / "evidence/docker-corpus-variant-input-v6-docs-scope-2026-10-07.json"
TEMPLATES_V2 = rr.CONTEXT / "evidence/restructuring-arm-templates-v3-codex-2026-10-08.json"  # R0-v4 / L-v4
PANEL = rr.CONTEXT / "evidence/cross-corpus-complex-task-panel-v1-2026-09-28.json"
PANEL_PLAN = rr.CONTEXT / "evidence/agent-search-factorial-panel-plan-v5-2026-09-30.json"
DECISIONS = rr.CONTEXT / "reviews/2026-10-08-author-decisions-editor-runtime.md"
PLAN_V1 = rr.CONTEXT / "evidence/restructuring-pilot-11b-plan-2026-10-08.json"
PLAN_V2 = rr.CONTEXT / "evidence/restructuring-pilot-11b-plan-v2-2026-10-08.json"
PLAN = rr.CONTEXT / "evidence/restructuring-pilot-11b-plan-v3-2026-10-08.json"
QUESTIONS = rr.CONTEXT / "evidence/restructuring-pilot-11b-questions-2026-10-08.json"
ADMISSION = rr.CONTEXT / "evidence/restructuring-pilot-11b-admission-v3-2026-10-08.json"
PL2_AMENDMENT = rr.CONTEXT / "evidence/restructuring-pilot-11b-pl2-amendment-v3-2026-10-08.json"
ANSWERS = rr.ROOT / "artifacts/pilot-11b-v3-answers"
SEED = 20261008
ARMS_ANSWERED = ("O", "S", "R0")


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_root():
    return Path(rr.read_json(V6)["output_root"]) / "runs" / RUN_ID


def manifest_path():
    return run_root() / "manifests/run-manifest.json"


# ------------------------------------------------------------------ freeze

CRITERIA = {
    "version": "pilot_11b_criteria_v1",
    "frozen_before": "any editor session, variant or answer exists",
    "pipeline": {
        "PL1_sham_replay": "S blind-export bytes equal O blind-export bytes (exact)",
        "PL2_R0_admission": "R0 status completed (not budget_stop, failed, c_rejected or preservation_flagged); C passed; "
                            "preservation review: 0 introduced unresolved internal links and 0 protected lines removed "
                            "corpus-wide; partial_unit_loss is reported for human review, not gating",
        "PL3_answer_completion": "at least 54 of 60 cells end in terminal `answer`, with no transport stop",
    },
    "edit_substance": {
        "ES1": "R0 changes at least 5 page paths (net, against the baseline) AND either adds or removes at least 1 page "
               "or changes at least 10 internal link edges (edges_added + edges_removed in the preservation review); "
               "otherwise the R0 arm is a no-go (no-op or cosmetic edit)",
    },
    "exposure_signal": {
        "support_pages": "the baseline page_ids of each question's 5 core rubric elements, translated to variant ids "
                         "through the R0 support mapping (diagnostic labels; human-unreviewed)",
        "per_cell": "for each (question, condition): support pages reached (search hits), opened (read or follow) "
                    "and cited (answer citations), as sets",
        "contrast": "cells where the cited-support set differs, R0 vs O, against the same count for S vs O "
                    "(S vs O is answer-run noise on byte-identical corpora)",
    },
    "answer_signal": {
        "judge_role": "triage only; no automated judge is qualified (handoffs/2026-09-27-gptoss-judge-calibration.md, "
                      "2026-09-28-gptoss-v2-clean-replacement.md)",
        "score": "per answer: satisfied core elements / 5; missing, contradicted and uncertain count 0; a judge parse "
                 "failure makes the answer unavailable and drops its pairs",
        "paired_differences": "d_R0 = R0 - O and d_S = S - O over the 20 (question, condition) cells",
        "worth_grading": "pipeline and ES1 pass AND at least one of: mean|d_R0| - mean|d_S| >= 0.05; |mean d_R0| >= 0.10; "
                         "cited-support set differs R0 vs O in at least 8 of 20 cells and in at least 4 more cells "
                         "than S vs O",
        "no_visible_signal": "pipeline and ES1 pass AND mean|d_R0| <= mean|d_S| AND |mean d_R0| < 0.05 AND cited-support "
                             "differences R0 vs O do not exceed S vs O by more than 1 cell",
        "otherwise": "inconclusive; reported as such for the author",
    },
    "decision": {"pipeline_fix": "any PL criterion fails", "no_go": "ES1 fails or no_visible_signal",
                 "go": "worth_grading", "inconclusive": "neither"},
    "spot_check_rule": "blind to arm: the 3 R0/O pairs with the largest |d_R0| and the 3 with the smallest, ties broken "
                       "by (question_id, condition id) ascending",
    "forbidden": ["significance tests", "effect-size claims", "tuning prompts, budgets or targets on answer outcomes"],
}


def questions():
    panel = rr.read_json(PANEL)
    plan = rr.read_json(PANEL_PLAN)
    rows = []
    for t in (t for t in plan["tasks"] if t["corpus_id"] == "docker_docs"):
        qid = t["question_id"]
        matches = [r for r in panel["selected"] if r["corpus_id"] == "docker_docs" and r["task"]["question"] == t["question"]]
        if len(matches) != 1:
            raise SystemExit("panel-question-match:" + qid[:12])
        task = matches[0]["task"]
        rows.append({"question_id": qid, "question": task["question"],
                     "core_elements": [{k: e[k] for k in ("element_id", "requirement", "acceptance", "page_id")}
                                       for e in task["core_elements"]],
                     "support_page_ids": sorted({e["page_id"] for e in task["core_elements"]})})
    if len(rows) != 5:
        raise SystemExit("expected-5-docker-development-questions")
    return {"schema_version": "docspectra.icsc.restructuring_pilot.v1.questions", "created": DATE,
            "label": "development questions; never reusable as the Sprint 12 confirmatory panel",
            "source": {"panel": str(PANEL), "panel_sha256": sha(PANEL), "plan": str(PANEL_PLAN),
                       "plan_sha256": sha(PANEL_PLAN)},
            "status_of_labels": "source-checked model proposals pending human review", "questions": rows}


def freeze(_args):
    if PLAN.exists() or manifest_path().exists():
        raise SystemExit("pilot-already-frozen")
    es = rr.load_sandbox()
    q = questions()
    if QUESTIONS.exists():  # v2 reuses the v1 question file unchanged
        if rr.read_json(QUESTIONS) != q:
            raise SystemExit("question-file-differs")
        q_sha = sha(QUESTIONS)
    else:
        q_sha = rr.write_new(QUESTIONS, q)
    plan_v5 = rr.read_json(PANEL_PLAN)
    conditions = plan_v5["conditions"]
    data = rr.build_manifest(
        run_id=RUN_ID, variant_manifest_path=V6, mode="live", created=DATE, templates_path=TEMPLATES_V2,
        runtime=es.runtime_block(model="gpt-6.1-sol", reasoning_effort="medium", driver="codex-exec",
                                 grading_consequence="usage- or rate-limit stop: excluded from grading; a fresh "
                                                     "replicate from a new baseline copy may run later"),
        model_identity="gpt-6.1-sol (Codex CLI 0.161.0, reasoning effort medium)",
        decided_limits={"model_calls": 1, "tool_calls": None, "tokens": 3_000_000, "wall_seconds": 3600, "edits": 150},
        stopping={"budget_stop_terminal_eligible_for_grading": False, "author_decisions": str(DECISIONS)})
    data["arm_plan"] = {"O": {"replicates": 1}, "S": {"replicates": 1}, "R0": {"replicates": 1},
                        "L": {"replicates": 0, "optional": True, "note": "not run in the pilot"}}
    data["cost_admission"] = {"authorization": f"author in chat, {DATE}; {DECISIONS.name}", "usd_cap": 10.0,
                              "editor_billing": "ChatGPT subscription usage (no USD)"}
    data["evaluation"] = {"question_set": {"path": str(QUESTIONS), "sha256": q_sha, "role": "development only"},
                          "answer_conditions": conditions,
                          "grading": "blind; opaque variant ids; key outside packet; pilot judge is triage only"}
    unresolved = rr.unresolved_paths(data)
    if unresolved:
        raise SystemExit("pilot-manifest-unresolved:" + ",".join(unresolved))
    m_sha = rr.write_new(manifest_path(), data)
    rr.validate_manifest(rr.read_json(manifest_path()), live=True)
    cells = [{"arm": a, "question_id": r["question_id"], "condition": c["id"]}
             for a in ARMS_ANSWERED for r in q["questions"] for c in conditions]
    random.Random(SEED).shuffle(cells)
    budgets, provider = plan_v5["budgets"], plan_v5["provider"]
    calls = len(cells) * budgets["model_calls"]
    reservation = calls * (provider["max_input_tokens_planning"] * 0.125 + provider["max_output_tokens_planning"] * 0.50) / 1e6
    plan = {
        "schema_version": "docspectra.icsc.restructuring_pilot.v1.plan", "created": DATE, "run_id": RUN_ID,
        "run_manifest": {"path": str(manifest_path()), "sha256": m_sha},
        "questions": {"path": str(QUESTIONS), "sha256": q_sha},
        "criteria": CRITERIA, "criteria_sha256": hashlib.sha256(rr.canon(CRITERIA)).hexdigest(),
        "answering": {"model": provider["model"], "prompt_path": plan_v5["prompt_path"],
                      "prompt_sha256": plan_v5["prompt_sha256"], "conditions": conditions, "budgets": budgets,
                      "max_input_tokens": provider["max_input_tokens_planning"],
                      "max_output_tokens": provider["max_output_tokens_planning"],
                      "agent_loop": "git/context/scripts/complex_task_agent_loop.py",
                      "agent_loop_sha256": sha(rr.CONTEXT / "scripts/complex_task_agent_loop.py"),
                      "ra_model_turn_sha256": sha(rr.RA / "retrieval_arena/model_turn.py"),
                      "retry_rule": "a recorded 429 rate-limit trace: archive it, wait 60 s, retry the cell at most twice; "
                                    "other failures stay in the denominator",
                      "datasets": "blind export variant folders (corpus/ + graph_edges.csv), opaque ids"},
        "schedule": {"seed": SEED, "cells": cells},
        "cost": {"model_calls_max": calls, "worst_case_reservation_usd": round(reservation, 4),
                 "estimated_charge_stop_usd": 10.0,
                 "expected_usd_at_panel_rate": round(len(cells) * 2.41613853 / 280, 4)},
        "judge": {"model": "qwen3:14b (local Ollama, default store)", "prompt": "git/context/design/complex-task-answer-judge-prompt-v1.txt",
                  "prompt_sha256": sha(rr.CONTEXT / "design/complex-task-answer-judge-prompt-v1.txt"),
                  "elements": "the 5 core elements per question", "blind": "answers keyed by opaque variant id only",
                  "cost_usd": 0},
        "prior_attempts": prior_attempts(),
        "data_sent_out": ["the 1,218-page public Docker docs tree to Codex (editing)",
                          "public Docker docs excerpts and the 5 development questions to Luna (answering)"],
    }
    p_sha = rr.write_new(PLAN, plan)
    print(json.dumps({"plan_sha256": p_sha, "run_manifest_sha256": m_sha, "questions_sha256": q_sha,
                      "criteria_sha256": plan["criteria_sha256"], "cells": len(cells), "cost": plan["cost"]}, indent=1))


PRIOR = (
    (PLAN_V1, "pilot-11b",
     "excluded infrastructure attempt: the pinned Codex config disabled features.code_mode_host, through which Codex "
     "0.161.0 routes its shell tool; every shell call failed closed and the editor stopped without editing (recorded "
     "then as no_op). Fixed by keeping code_mode_host at its default; tool-unavailable errors now map to failed + "
     "infrastructure_stop:tool_unavailable."),
    (PLAN_V2, "pilot-11b-v2",
     "excluded prompt-defect attempt: R0-v3 told the editor to follow limits in a 'separate locked run manifest' it "
     "cannot see; after 3 read-only commands it asked for the manifest and stopped without editing (recorded as no_op). "
     "Fixed by the author-approved R0-v4 (limits stated in the prompt; non-interactive session)."),
)


def prior_attempts():
    return [prior_attempt(*row) for row in PRIOR]


def prior_attempt(plan_path, run_id, disposition):
    v1 = rr.read_json(plan_path)
    if v1["criteria_sha256"] != hashlib.sha256(rr.canon(CRITERIA)).hexdigest():
        raise SystemExit("criteria-changed-since-" + run_id)
    ns = Path(rr.read_json(V6)["output_root"]) / "runs" / run_id / "R0-r01"
    state = rr.read_json(ns / "host/run-state.json")
    return {"plan": str(plan_path), "plan_sha256": sha(plan_path), "run_id": run_id,
            "R0_r01": {k: state.get(k) for k in ("status", "terminal_checkpoint", "editor_model_calls",
                                                 "editor_tool_calls", "editor_tokens", "editor_edits", "editor_wall_seconds")},
            "disposition": disposition + " No answer was generated; criteria, questions, seed and schedule are unchanged.",
            "authority": "author rule for infrastructure stops (excluded; fresh replicate may run)"}


# ------------------------------------------------------------------ edit

def edit(args):
    plan = rr.read_json(PLAN)
    mp = manifest_path()
    if sha(mp) != plan["run_manifest"]["sha256"]:
        raise SystemExit("run-manifest-drift")
    out = {}
    for arm in args.arms:
        state = rr.launch(mp, arm, 1, live=True)
        out[arm] = {k: state.get(k) for k in ("status", "flags", "terminal_checkpoint", "eligible_for_grading",
                                              "editor_model_calls", "editor_tool_calls", "editor_tokens",
                                              "editor_edits", "editor_wall_seconds", "editor_stop", "editor_errors",
                                              "editor_exit_code", "c_passed", "profile_sha256")}
        print(json.dumps({arm: out[arm]}, indent=1), flush=True)


# ------------------------------------------------------------------ admit

def admit(_args):
    if ADMISSION.exists():
        raise SystemExit("already-admitted")
    plan = rr.read_json(PLAN)
    root = run_root()
    ns = {a: root / f"{a}-r01" for a in ARMS_ANSWERED}
    states = {a: rr.read_json(ns[a] / "host/run-state.json") for a in ARMS_ANSWERED}
    r0 = states["R0"]
    review = rr.read_json(ns["R0"] / "exports/preservation-review/review.json") \
        if (ns["R0"] / "exports/preservation-review/review.json").exists() else None
    pl2 = {"status": r0["status"], "c_passed": r0.get("c_passed"), "flags": r0["flags"],
           "introduced_unresolved_internal_links": len(review["introduced_unresolved_internal_links"]) if review else None,
           "protected_lines_removed": len(review["protected_lines_removed"]) if review else None}
    pl2["pass"] = bool(review and r0["status"] == "completed" and r0.get("c_passed") is True and
                       pl2["introduced_unresolved_internal_links"] == 0 and pl2["protected_lines_removed"] == 0)
    es1 = None
    if review:
        changed_paths = len(review["added"]) + len(review["removed"]) + len(review["changed"])
        edge_changes = len(review["edges_added"]) + len(review["edges_removed"])
        es1 = {"changed_paths": changed_paths, "pages_added": len(review["added"]),
               "pages_removed": len(review["removed"]), "edge_changes": edge_changes,
               "pass": changed_paths >= 5 and (len(review["added"]) + len(review["removed"]) >= 1 or edge_changes >= 10)}
    eligible = [ns[a] for a in ARMS_ANSWERED if states[a]["eligible_for_grading"]]
    dest = root / "grading-export"
    key_path = root / "host-private/grading-key.json"
    grading = rr.grading_export(manifest_path(), eligible, dest, key_path) if eligible else None
    key = rr.read_json(key_path) if grading else {"variants": {}}
    by_arm = {v["arm"]: k for k, v in key["variants"].items()}
    pl1 = None
    if "O" in by_arm and "S" in by_arm:
        pl1 = {"pass": rr.tree_bytes(dest / "variants" / by_arm["O"]) == rr.tree_bytes(dest / "variants" / by_arm["S"])}
    summary = {"schema_version": "docspectra.icsc.restructuring_pilot.v1.admission", "created": DATE,
               "plan_sha256": sha(PLAN), "criteria_sha256": plan["criteria_sha256"],
               "states": {a: {k: s.get(k) for k in ("status", "flags", "terminal_checkpoint", "eligible_for_grading",
                                                     "profile_sha256", "c_passed", "editor_model_calls",
                                                     "editor_tool_calls", "editor_tokens", "editor_edits",
                                                     "editor_wall_seconds", "editor_stop")} for a, s in states.items()},
               "PL1": pl1, "PL2": pl2, "ES1": es1, "grading_export": grading,
               "answerable_variants": sorted(key["variants"]),
               "note": "variant ids are opaque; the arm key stays in host-private and is read again only at analysis"}
    print(json.dumps({"sha256": rr.write_new(ADMISSION, summary), "PL1": pl1, "PL2": pl2, "ES1": es1,
                      "variants": len(key["variants"])}, indent=1))


# ------------------------------------------------------------------ answer

def pl2_pass(admission):
    """Frozen PL2, or the author's recorded whitespace amendment (decided before any answer existed)."""
    if admission["PL2"]["pass"]:
        return True
    if not PL2_AMENDMENT.exists():
        return False
    amendment = rr.read_json(PL2_AMENDMENT)
    if amendment["admission_sha256"] != sha(ADMISSION):
        raise SystemExit("pl2-amendment-admission-mismatch")
    return (amendment["PL2_amended_pass"] and admission["PL2"]["status"] == "completed" and
            admission["PL2"]["c_passed"] is True and admission["PL2"]["introduced_unresolved_internal_links"] == 0)


REVIEW_LABEL = "author-chat-2026-10-08-pilot-11b-usd10-v3"


def variant_ids():
    """Arm -> opaque variant id (operator side only; Luna sees just the opaque dataset folder)."""
    key = rr.read_json(run_root() / "host-private/grading-key.json")
    return {v["arm"]: k for k, v in key["variants"].items()}


def answer(_args):
    import os
    import subprocess
    import time
    import run_agent_search_factorial_v1 as panel
    plan = rr.read_json(PLAN)
    admission = rr.read_json(ADMISSION)
    if sha(PLAN) != admission["plan_sha256"]:
        raise SystemExit("plan-drift-since-admission")
    if not (admission["PL1"] and admission["PL1"]["pass"] and pl2_pass(admission)):
        raise SystemExit("pipeline-criteria-failed-no-answering")
    a = plan["answering"]
    if sha(rr.ROOT / a["agent_loop"]) != a["agent_loop_sha256"] or \
            sha(rr.RA / "retrieval_arena/model_turn.py") != a["ra_model_turn_sha256"] or \
            sha(a["prompt_path"]) != a["prompt_sha256"]:
        raise SystemExit("answer-runtime-drift")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY-not-visible")
    ids = variant_ids()
    qs = {q["question_id"]: q for q in rr.read_json(QUESTIONS)["questions"]}
    conds = {c["id"]: c for c in a["conditions"]}
    b = a["budgets"]
    datasets = run_root() / "grading-export/variants"
    ANSWERS.mkdir(parents=True, exist_ok=True)
    lock = ANSWERS / "plan.sha256"
    if lock.exists() and lock.read_text(encoding="ascii").strip() != sha(PLAN):
        raise SystemExit("answers-folder-belongs-to-another-plan")
    lock.write_text(sha(PLAN) + "\n", encoding="ascii")
    ledger, commands = ANSWERS / "cost-ledger.jsonl", ANSWERS / "commands.jsonl"
    prior = [json.loads(l) for l in commands.read_text(encoding="utf-8").splitlines()] if commands.exists() else []
    # An archived attempt is excluded: drop each cell's rows up to its last archive so the cell reruns.
    last_archive = {r["cell"]: i for i, r in enumerate(prior)
                    if r.get("event") in {"rate_limit_retry_archive", "infrastructure_attempt_archive"}}
    prior = [r for i, r in enumerate(prior) if i > last_archive.get(r.get("cell"), -1)]
    cells = plan["schedule"]["cells"]
    calls_total = len(cells) * b["model_calls"]
    for index, cell in enumerate(cells):
        variant = ids[cell["arm"]]
        q, c = qs[cell["question_id"]], conds[cell["condition"]]
        dest = ANSWERS / f"{variant}-{q['question_id'][:12]}-{c['id']}.json"
        if panel.completed_cell(prior, dest):
            print(dest.stem, "already-complete", flush=True)
            continue
        cmd = [sys.executable, str(rr.ROOT / a["agent_loop"]), "--dataset", str(datasets / variant),
               "--question-id", q["question_id"], "--question", q["question"], "--prompt", a["prompt_path"],
               "--output", str(dest), "--model", a["model"], "--provider", "openai-responses",
               "--outbound-review-id", REVIEW_LABEL, "--cost-ledger", str(ledger),
               "--future-calls-after-episode", str((len(cells) - index - 1) * b["model_calls"]),
               "--campaign-calls", str(calls_total), "--openai-output-cap", str(a["max_output_tokens"]),
               "--openai-input-cap", str(a["max_input_tokens"]),
               "--estimated-charge-stop-usd", str(plan["cost"]["estimated_charge_stop_usd"]),
               "--search-budget", str(c["searches"]), "--read-budget", str(b["reads"]),
               "--call-budget", str(b["model_calls"]), "--history-bytes", str(b["history_bytes"]),
               "--wall-seconds", str(b["wall_seconds"]), "--note-budget", str(b["notes"]),
               "--note-chars", str(b["note_chars"]), "--min-distinct-searches", str(c["min_distinct_searches"])]
        if c["follow_links"]:
            cmd.append("--follow-links")
        attempt = 1
        while True:
            panel.log(commands, {"event": "command_start", "cell": dest.stem, "attempt": attempt,
                                 "plan_sha256": sha(PLAN), "review_label": REVIEW_LABEL, "schedule_index": index})
            result = subprocess.run(cmd, cwd=rr.ROOT, capture_output=True, text=True, timeout=b["wall_seconds"] + 300)
            trace = json.loads(dest.read_text(encoding="utf-8")) if dest.exists() else None
            terminal = trace["terminal"] if trace else None
            panel.log(commands, {"event": "command_end", "cell": dest.stem, "attempt": attempt,
                                 "returncode": result.returncode, "terminal": terminal,
                                 "trace_sha256": sha(dest) if dest.exists() else None,
                                 "stderr": panel.redact(result.stderr[-1200:]), "stdout": panel.redact(result.stdout[-500:])})
            print(index + 1, dest.stem, terminal, "attempt", attempt, flush=True)
            if result.returncode == 0 and trace and panel.is_recorded_rate_limit(trace) and attempt < 3:
                panel.archive_rate_limited_attempt(ANSWERS, dest, attempt, commands)
                attempt += 1
                time.sleep(60)
                continue
            if result.returncode or terminal in {"model_api_error", "model_incomplete", "model_resource_error"}:
                raise SystemExit("answer-transport-stopped:" + dest.stem)
            break


def archive_infra(args):
    """Exclude a failed answer attempt under the author rule for infrastructure stops."""
    import run_agent_search_factorial_v1 as panel
    dest = ANSWERS / (args.cell + ".json")
    commands = ANSWERS / "commands.jsonl"
    trace = json.loads(dest.read_text(encoding="utf-8"))
    if trace["terminal"] != "model_api_error":
        raise SystemExit("not-an-infrastructure-terminal:" + str(trace["terminal"]))
    folder = ANSWERS / "infrastructure-attempts" / args.cell
    n = 1 + sum(1 for _ in folder.glob("*")) if folder.exists() else 1
    target = folder / str(n)
    target.mkdir(parents=True)
    trace_sha = sha(dest)
    for source, name in ((dest, "trace.json"), (dest.with_suffix(".events.jsonl"), "events.jsonl"),
                         (ANSWERS / (args.cell + "-provider"), "provider")):
        if source.exists():
            source.rename(target / name)
    panel.log(commands, {"event": "infrastructure_attempt_archive", "cell": args.cell, "reason": args.reason,
                         "authority": "author rule for infrastructure stops (excluded; fresh replicate may run)",
                         "trace_sha256": trace_sha, "archive": str(target.relative_to(ANSWERS))})
    print(args.cell, "archived to", target.relative_to(ANSWERS))


# ------------------------------------------------------------------ judge

JUDGE = rr.ROOT / "artifacts/pilot-11b-v3-judge"


def answer_files():
    return sorted(p for p in ANSWERS.glob("v-*.json"))


def judge(_args):
    import subprocess
    plan = rr.read_json(PLAN)
    if sha(rr.CONTEXT / "design/complex-task-answer-judge-prompt-v1.txt") != plan["judge"]["prompt_sha256"]:
        raise SystemExit("judge-prompt-drift")
    qs = {q["question_id"]: q for q in rr.read_json(QUESTIONS)["questions"]}
    packets = JUDGE / "packets"
    if not (JUDGE / "packet-manifest.json").exists():
        packets.mkdir(parents=True)
        manifest, aliases = [], {}
        for path in answer_files():
            trace = rr.read_json(path)
            if trace["terminal"] != "answer":
                continue
            text = trace["answer"]["text"]
            uid = hashlib.sha256(("pilot-11b-judge|" + trace["question_id"] + "|" + text).encode()).hexdigest()
            aliases.setdefault(uid, []).append(path.name)
            if (packets / f"{uid}.json").exists():
                continue  # identical answers to the same question are judged once
            q = qs[trace["question_id"]]
            unit = {"unit_id": uid, "question": q["question"], "answer": text,
                    "elements": [{k: e[k] for k in ("element_id", "requirement", "acceptance")} for e in q["core_elements"]]}
            (packets / f"{uid}.json").write_text(json.dumps({"units": [unit]}, ensure_ascii=False, sort_keys=True) + "\n",
                                                 encoding="utf-8")
            manifest.append({"packet": f"{uid}.json", "sha256": sha(packets / f"{uid}.json"), "unit_id": uid})
        random.Random(SEED + 1).shuffle(manifest)  # judge order independent of variant
        (JUDGE / "packet-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (JUDGE / "aliases.json").write_text(json.dumps(aliases, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    subprocess.run([sys.executable, str(rr.CONTEXT / "scripts/run_complex_task_local_judge.py"), "--root", str(JUDGE)],
                   cwd=rr.ROOT, check=True)


# ------------------------------------------------------------------ analyse

def judge_scores():
    aliases = rr.read_json(JUDGE / "aliases.json")
    scores = {}
    for uid, files in aliases.items():
        done = JUDGE / "local-qwen-v4" / uid / "completion.json"
        score = None
        if done.exists() and rr.read_json(done)["valid"]:
            norm = JUDGE / "local-qwen-v4" / uid / Path(rr.read_json(done)["packet"]).stem
            record = rr.read_json(done)
            attempt = JUDGE / "local-qwen-v4" / uid / f"attempt-{record['attempt']:02d}.normalized.json"
            verdicts = rr.read_json(attempt)["units"][0]["verdicts"]
            score = sum(v["verdict"] == "satisfied" for v in verdicts) / 5
        for f in files:
            scores[f] = score
    return scores


def cell_exposure(trace, support):
    hits, opened = set(), set()
    for step in trace["steps"]:
        act, res = step["action"], step.get("result") or {}
        if act["action"] == "search":
            hits.update(h["doc_id"] for h in res.get("hits", []))
        elif act["action"] in ("read", "follow") and "doc_id" in act:
            opened.add(act["doc_id"])
    cited = {c["doc_id"] for c in (trace.get("answer") or {}).get("citations", [])} if trace["terminal"] == "answer" else set()
    return {"reached": sorted(hits & support), "opened": sorted(opened & support), "cited": sorted(cited & support)}


def analyse(_args):
    plan = rr.read_json(PLAN)
    admission = rr.read_json(ADMISSION)
    ids = variant_ids()  # unblinding happens here, after judging
    arm_of = {v: a for a, v in ids.items()}
    qs = {q["question_id"]: q for q in rr.read_json(QUESTIONS)["questions"]}
    smap = rr.read_json(run_root() / "R0-r01/exports/support-mapping.json")["baseline_to_variant"]
    scores = judge_scores()
    cells = {}
    usage = {"prompt_tokens": 0, "output_tokens": 0, "model_calls": 0, "invalid_actions": 0}
    terminals = {}
    for path in answer_files():
        trace = rr.read_json(path)
        variant, cond = path.stem.split("-")[0] + "-" + path.stem.split("-")[1], path.stem.split("-", 3)[3]
        arm = arm_of[variant]
        q = qs[trace["question_id"]]
        support = set(q["support_page_ids"])
        if arm == "R0":
            support = {v for p in support for v in smap.get(p, {}).get("variant_ids", [p])}
        cells[(arm, q["question_id"], cond)] = {"terminal": trace["terminal"], "score": scores.get(path.name),
                                                 **cell_exposure(trace, support)}
        terminals[trace["terminal"]] = terminals.get(trace["terminal"], 0) + 1
        for k, src in (("prompt_tokens", "prompt_tokens_reported"), ("output_tokens", "output_tokens_reported"),
                       ("model_calls", "model_calls"), ("invalid_actions", "invalid_actions")):
            usage[k] += int(trace["usage"].get(src) or 0)
    pairs = []
    for qid in sorted(qs):
        for c in plan["answering"]["conditions"]:
            o, s, r = (cells.get((a, qid, c["id"])) for a in ("O", "S", "R0"))
            row = {"question_id": qid[:12], "condition": c["id"]}
            for name, x in (("O", o), ("S", s), ("R0", r)):
                row[name] = x
            ok = lambda x: x is not None and x["score"] is not None
            row["d_R0"] = r["score"] - o["score"] if ok(r) and ok(o) else None
            row["d_S"] = s["score"] - o["score"] if ok(s) and ok(o) else None
            row["cited_differs_R0"] = (set(r["cited"]) != set(o["cited"])) if r and o else None
            row["cited_differs_S"] = (set(s["cited"]) != set(o["cited"])) if s and o else None
            pairs.append(row)
    dR = [p["d_R0"] for p in pairs if p["d_R0"] is not None]
    dS = [p["d_S"] for p in pairs if p["d_S"] is not None]
    mean = lambda xs: sum(xs) / len(xs) if xs else None
    stats = {"pairs_R0": len(dR), "pairs_S": len(dS), "mean_abs_d_R0": mean([abs(x) for x in dR]),
             "mean_abs_d_S": mean([abs(x) for x in dS]), "mean_d_R0": mean(dR), "mean_d_S": mean(dS),
             "R0_wins_ties_losses": [sum(x > 0 for x in dR), sum(x == 0 for x in dR), sum(x < 0 for x in dR)],
             "S_wins_ties_losses": [sum(x > 0 for x in dS), sum(x == 0 for x in dS), sum(x < 0 for x in dS)],
             "cited_differs_R0": sum(bool(p["cited_differs_R0"]) for p in pairs),
             "cited_differs_S": sum(bool(p["cited_differs_S"]) for p in pairs)}
    answered = terminals.get("answer", 0)
    pl = {"PL1": admission["PL1"]["pass"], "PL2": pl2_pass(admission), "PL2_frozen": admission["PL2"]["pass"], "PL3": answered >= 54 and len(cells) == 60}
    es1 = bool(admission["ES1"] and admission["ES1"]["pass"])
    worth = no_signal = False
    if all(v for k, v in pl.items() if k != "PL2_frozen") and es1 and stats["mean_abs_d_R0"] is not None:
        worth = (stats["mean_abs_d_R0"] - stats["mean_abs_d_S"] >= 0.05 or abs(stats["mean_d_R0"]) >= 0.10 or
                 (stats["cited_differs_R0"] >= 8 and stats["cited_differs_R0"] - stats["cited_differs_S"] >= 4))
        no_signal = (stats["mean_abs_d_R0"] <= stats["mean_abs_d_S"] and abs(stats["mean_d_R0"]) < 0.05 and
                     stats["cited_differs_R0"] - stats["cited_differs_S"] <= 1)
    decision = ("pipeline_fix" if not all(v for k, v in pl.items() if k != "PL2_frozen") else "no_go" if (not es1 or no_signal) else
                "go" if worth else "inconclusive")
    ranked = sorted((p for p in pairs if p["d_R0"] is not None), key=lambda p: (-abs(p["d_R0"]), p["question_id"], p["condition"]))
    spot = {"most_changed": [(p["question_id"], p["condition"]) for p in ranked[:3]],
            "least_changed": [(p["question_id"], p["condition"]) for p in sorted(
                ranked, key=lambda p: (abs(p["d_R0"]), p["question_id"], p["condition"]))[:3]]}
    est = (usage["prompt_tokens"] * 0.125 + usage["output_tokens"] * 0.50) / 1e6
    out = {"schema_version": "docspectra.icsc.restructuring_pilot.v1.analysis", "created": DATE,
           "plan_sha256": sha(PLAN), "criteria_sha256": plan["criteria_sha256"], "admission_sha256": sha(ADMISSION),
           "terminals": terminals, "pipeline": pl, "ES1": admission["ES1"], "stats": stats,
           "worth_grading": worth, "no_visible_signal": no_signal, "decision": decision, "spot_check": spot,
           "answer_usage": usage, "estimated_answer_usd_reported_tokens": round(est, 4), "pairs": pairs,
           "scope": "development pilot; one R0 realization; unqualified triage judge; no significance computed"}
    path = rr.CONTEXT / "evidence/restructuring-pilot-11b-analysis-v3-2026-10-08.json"
    print(json.dumps({"sha256": rr.write_new(path, out), "decision": decision, "pipeline": pl, "stats": stats,
                      "terminals": terminals, "usd_est": out["estimated_answer_usd_reported_tokens"]}, indent=1))


# ------------------------------------------------------------------ ranking channel (Sprint 11c tooling)
# Exploratory, added after the frozen 11b decision; nothing below reads or rewrites the 11b analysis.
# The ranker never sees the arm key: it pairs answers by opaque variant id. Only analyse-ranking and the
# operator-side smoke/rescore helpers read the key.

RANKER = rr.ROOT / "artifacts/pilot-11b-v3-ranker-v1"
RANKER_SMOKE = rr.ROOT / "artifacts/pilot-11b-v3-ranker-v1-smoke"
RUBRIC_SCORES = rr.ROOT / "artifacts/pilot-11b-v3-rubric-scores-v1/scores.json"


def ranker_inputs():
    """Answers keyed by opaque variant id (as `arm`), the questions, and strings the packets must not carry."""
    answers, forbidden = [], set()
    for path in answer_files():
        trace = rr.read_json(path)
        variant = "-".join(path.stem.split("-")[:2])
        forbidden.update({path.name, path.stem, variant})
        if trace["terminal"] == "answer":
            answers.append({"answer_id": path.name, "question_id": trace["question_id"],
                            "cell": path.stem.split("-", 3)[3], "arm": variant, "text": trace["answer"]["text"]})
    forbidden.update(rr.read_json(JUDGE / "aliases.json"))  # boolean-channel unit ids
    qs = {q["question_id"]: q for q in rr.read_json(QUESTIONS)["questions"]}
    return answers, qs, forbidden


def rank(args):
    import answer_pairwise_ranker as apr
    if not (RANKER / "packet-manifest.json").exists():
        answers, qs, forbidden = ranker_inputs()
        variants = sorted(rr.read_json(ADMISSION)["answerable_variants"])
        packets, key = apr.build_pairs(answers, qs, variants, args.seed)
        receipt = apr.write_packets(RANKER, packets, key, args.seed, forbidden)
        print(json.dumps({"packets": receipt["packets"], "manifest_sha256": receipt["manifest_sha256"]}), flush=True)
    apr.run(RANKER, limit=args.limit, retry=args.retry)
    s = apr.summarize(RANKER)
    print(json.dumps({"usage": s["usage"]}, indent=1))  # transport only; analysis is analyse-ranking


def rank_smoke(args):
    """Transport check: one O-S pair (first cell by question id, then condition) in both orders."""
    import answer_pairwise_ranker as apr
    if not (RANKER_SMOKE / "packet-manifest.json").exists():
        answers, qs, forbidden = ranker_inputs()
        ids = variant_ids()  # operator side, to pick the O and S answers only
        first = min((a["question_id"], a["cell"]) for a in answers)
        chosen = [a for a in answers if (a["question_id"], a["cell"]) == first and a["arm"] in (ids["O"], ids["S"])]
        packets, key = apr.build_pairs(chosen, qs, [ids["O"], ids["S"]], args.seed)
        apr.write_packets(RANKER_SMOKE, packets, key, args.seed, forbidden)
    apr.run(RANKER_SMOKE)
    s = apr.summarize(RANKER_SMOKE)
    calls = [rr.read_json(p) for p in sorted(RANKER_SMOKE.glob("calls/*/attempt-[0-9][0-9].json"))]
    print(json.dumps({"receipt_sha256": s["receipt_sha256"], "usage": s["usage"],
                      "calls": [{k: c.get(k) for k in ("packet_id", "valid", "usage", "prompt_sha256",
                                                        "raw_response_sha256", "normalized_response_sha256",
                                                        "digest_before", "digest_after")} for c in calls],
                      "preferences": [{k: r[k] for k in ("preference_xy", "preference_yx", "kind")} for r in s["pairs"]]},
                     indent=1))


def rescore_rubric(_args):
    """The three boolean-rubric scores for every answer, from the saved judge outputs (no model calls)."""
    import rubric_quote_match as rqm
    if RUBRIC_SCORES.exists():
        raise SystemExit("rubric-scores-already-written")
    scores = rqm.score_judge_root(JUDGE)
    arm_of = {v: a for a, v in variant_ids().items()}
    by_arm = {}
    for name, s in scores.items():
        arm = arm_of["-".join(name.split("-")[:2])]
        for k in ("rubric_v1_strict", "rubric_v1_relaxed", "rubric_v1_raw"):
            by_arm.setdefault(arm, {}).setdefault(k, []).append(s[k] if s else None)
    means = {a: {k: (sum(x for x in v if x is not None) / sum(x is not None for x in v)) for k, v in d.items()}
             for a, d in by_arm.items()}
    logs = {}
    for s in scores.values():
        if s:
            logs[s["unit_id"]] = s["relaxed_log"]
    flat = [x for log in logs.values() for x in log]
    rules = {}
    for x in flat:
        rules[x["rule"] or "rejected"] = rules.get(x["rule"] or "rejected", 0) + 1
    RUBRIC_SCORES.parent.mkdir(parents=True, exist_ok=True)
    RUBRIC_SCORES.write_text(json.dumps({"rule": rqm.RULE, "judge_root": str(JUDGE), "scores": scores,
                                         "per_arm_means": means, "relaxed_rule_counts": rules}, indent=1) + "\n",
                             encoding="utf-8")
    print(json.dumps({"sha256": sha(RUBRIC_SCORES), "answers": len(scores), "per_arm_means": means,
                      "strict_rejected_satisfied": len(flat), "relaxed_rule_counts": rules}, indent=1))


def analyse_ranking(_args):
    """Join ranker outputs, the three rubric scores and the arm key, after both channels are complete."""
    import datetime
    import answer_pairwise_ranker as apr
    import rubric_quote_match as rqm
    manifest = rr.read_json(RANKER / "packet-manifest.json")
    missing = [e["packet_id"] for e in manifest if not any((RANKER / "calls" / e["packet_id"]).glob("attempt-*.json"))]
    if missing:
        raise SystemExit(f"ranker-incomplete:{len(missing)}-packets-never-attempted")
    aliases = rr.read_json(JUDGE / "aliases.json")
    if any(not (JUDGE / "local-qwen-v4" / uid / "completion.json").exists() for uid in aliases):
        raise SystemExit("boolean-channel-incomplete")
    summary = apr.summarize(RANKER)
    rubric = rqm.score_judge_root(JUDGE)
    ids = variant_ids()  # unblinding happens here, after both channels are complete
    arm_of = {v: a for a, v in ids.items()}
    arm = lambda answer_id: arm_of["-".join(answer_id.split("-")[:2])]
    measures = ("rubric_v1_strict", "rubric_v1_relaxed", "rubric_v1_raw")
    order = {a: i for i, a in enumerate(ARMS_ANSWERED)}
    rows = []
    for p in summary["pairs"]:
        base, other = sorted((p["x"], p["y"]), key=lambda i: order[arm(i)])  # O before S before R0
        outcome = p["outcome"]
        if outcome in ("x", "y"):
            winner = p[outcome]
            outcome = "other" if winner == other else "base"
        row = {"question_id": p["question_id"][:12], "condition": p["cell"], "pair_type": f"{arm(base)}-{arm(other)}",
               "base": arm(base), "other": arm(other), "outcome": outcome, "kind": p["kind"],
               "position_flip": p["position_flip"],
               "decisive_criteria": [p["decisive_xy"], p["decisive_yx"]]}
        for m in measures:
            sb, so = (rubric.get(base) or {}).get(m), (rubric.get(other) or {}).get(m)
            row["d_" + m] = (so - sb) if sb is not None and so is not None else None
        rows.append(row)
    by_type = {}
    for r in rows:
        t = by_type.setdefault(r["pair_type"], {"pairs": 0, "available": 0, "flips": 0, "half_ties": 0,
                                                "other_wins": 0, "ties": 0, "base_wins": 0})
        t["pairs"] += 1
        if r["outcome"] is None:
            continue
        t["available"] += 1
        t["flips"] += r["kind"] == "flip"
        t["half_ties"] += r["kind"] == "half_tie"
        t[{"other": "other_wins", "base": "base_wins", "tie": "ties"}[r["outcome"]]] += 1
    agreement = {}
    for m in measures:
        a = {"agree": 0, "disagree": 0, "preference_tie_rubric_differs": 0, "preference_win_rubric_tie": 0,
             "both_tie": 0}
        for r in rows:
            d = r["d_" + m]
            if r["outcome"] is None or d is None:
                continue
            if r["outcome"] == "tie":
                a["both_tie" if d == 0 else "preference_tie_rubric_differs"] += 1
            elif d == 0:
                a["preference_win_rubric_tie"] += 1
            else:
                a["agree" if (d > 0) == (r["outcome"] == "other") else "disagree"] += 1
        agreement[m] = a
    decisive = {}
    for p in summary["pairs"]:
        for c in (p["decisive_xy"], p["decisive_yx"]):
            if p["preference_xy"] is not None or p["preference_yx"] is not None:
                decisive[str(c)] = decisive.get(str(c), 0) + 1
    cells = [{"question_id": c["question_id"][:12], "condition": c["cell"],
              "ranking": [[arm(i) for i in g] for g in c["ranking"]] if c["ranking"] else None,
              "cycles": [[arm(i) for i in cyc] for cyc in c["cycles"]] if c["cycles"] else c["cycles"],
              "net_wins": {arm(i): v for i, v in c["net_wins"].items()} if c["net_wins"] else None}
             for c in summary["cells"]]
    diag = []
    for p in summary["pairs"]:
        if p["question_id"].startswith("3b5890") and p["cell"] == "s1-follow":
            for o in ("xy", "yx"):
                done = RANKER / "calls" / p["packet_" + o] / "completion.json"
                if done.exists():
                    rec = rr.read_json(done)
                    out = rr.read_json(RANKER / "calls" / p["packet_" + o] / f"attempt-{rec['attempt']:02d}.normalized.json")
                    a_id, b_id = (p["x"], p["y"]) if o == "xy" else (p["y"], p["x"])
                    diag.append({"A": arm(a_id), "B": arm(b_id), **out})
    out = {"schema_version": "docspectra.icsc.restructuring_pilot.v1.ranking_analysis",
           "created": datetime.date.today().isoformat(),
           "scope": "exploratory: ranker designed after the frozen 11b no-go; never changes it; not paper evidence",
           "ranker_receipt_sha256": summary["receipt_sha256"], "combine_rule": summary["combine_rule"],
           "relaxed_rule": rqm.RULE, "plan_sha256": sha(PLAN),
           "usage": summary["usage"], "position_flips": summary["position_flips"],
           "cells_with_cycles": summary["cells_with_cycles"], "by_pair_type": by_type,
           "decisive_criterion_counts": decisive, "rubric_agreement": agreement,
           "cells": cells, "pairs": rows, "diagnostic_3b5890_s1_follow": diag}
    path = rr.CONTEXT / f"evidence/restructuring-pilot-11b-ranking-analysis-v1-{out['created']}.json"
    print(json.dumps({"sha256": rr.write_new(path, out), "path": str(path), "by_pair_type": by_type,
                      "position_flips": summary["position_flips"], "cells_with_cycles": summary["cells_with_cycles"]},
                     indent=1))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="stage", required=True)
    sub.add_parser("freeze")
    e = sub.add_parser("edit")
    e.add_argument("--arms", nargs="+", default=["O", "S", "R0"], choices=["O", "S", "R0"])
    sub.add_parser("admit")
    sub.add_parser("answer")
    ai = sub.add_parser("archive-infra")
    ai.add_argument("cell")
    ai.add_argument("--reason", required=True)
    sub.add_parser("judge")
    sub.add_parser("analyse")
    rk = sub.add_parser("rank")
    rk.add_argument("--seed", type=int, required=True, help="pair-label and run-order seed (frozen in the 11d plan)")
    rk.add_argument("--limit", type=int)
    rk.add_argument("--retry", action="store_true")
    rs = sub.add_parser("rank-smoke")
    rs.add_argument("--seed", type=int, required=True)
    sub.add_parser("rescore-rubric")
    sub.add_parser("analyse-ranking")
    args = parser.parse_args()
    {"freeze": freeze, "edit": edit, "admit": admit, "answer": answer, "archive-infra": archive_infra,
     "judge": judge, "analyse": analyse, "rank": rank, "rank-smoke": rank_smoke, "rescore-rubric": rescore_rubric,
     "analyse-ranking": analyse_ranking}[args.stage](args)


if __name__ == "__main__":
    main()

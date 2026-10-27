"""Preflight or run the frozen eight-cell search/link Luna pilot.

Live execution requires a fresh, normally approved outbound command. The review
label records that approval; it is not a substitute for it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "git/context/evidence/agent-search-factorial-pilot-v2-2026-09-30.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def log(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({**row, "utc": datetime.now(timezone.utc).isoformat()},
                                ensure_ascii=False) + "\n")
        stream.flush()


def redact(value: str) -> str:
    secret = os.environ.get("OPENAI_API_KEY")
    if secret:
        value = value.replace(secret, "[REDACTED_API_KEY]")
    return re.sub(r"sk-[A-Za-z0-9_*.-]{8,}", "[REDACTED_API_KEY]", value)


def completed_cell(prior_commands: list[dict], dest: Path) -> bool:
    previous = [row for row in prior_commands if row.get("cell") == dest.stem]
    if not previous and not dest.exists():
        return False
    starts = [row for row in previous if row["event"] == "command_start"]
    ends = [row for row in previous if row["event"] == "command_end"]
    if len(starts) == len(ends) and ends and ends[-1]["returncode"] == 0 and \
            dest.exists() and ends[-1]["trace_sha256"] == sha(dest):
        return True
    raise RuntimeError("incomplete-cell-requires-manual-disposition:" + dest.stem)


def cell_stem(task: dict, condition: dict, first_question_ids: dict[str, str]) -> str:
    """Keep the first panel question's four already written cell names stable."""
    corpus = task["corpus_id"]
    if not first_question_ids or task["question_id"] == first_question_ids[corpus]:
        return f"{corpus}-{condition['id']}"
    return f"{corpus}-{task['question_id']}-{condition['id']}"


def is_recorded_rate_limit(trace: dict) -> bool:
    if trace.get("terminal") != "model_api_error" or not trace.get("steps"):
        return False
    error = trace["steps"][-1].get("result", {}).get("error", "")
    return "RateLimitError:" in error and "Error code: 429" in error


def is_amended_charge_stop(trace: dict, manifest: dict) -> bool:
    return (manifest["schema_version"].endswith("panel_plan.v5") and
            trace.get("terminal") == "model_api_error" and bool(trace.get("steps")) and
            trace["steps"][-1].get("result", {}).get("error") ==
            "RuntimeError:estimated-charge-stop")


def is_panel(manifest: dict) -> bool:
    return manifest["schema_version"] in {
        "docspectra.icsc.agent_search_factorial_panel_plan.v4",
        "docspectra.icsc.agent_search_factorial_panel_plan.v5",
    }


def archive_rate_limited_attempt(output: Path, dest: Path, attempt: int,
                                 commands: Path) -> None:
    # Preserve every failed trace, event log, and raw provider record before retry.
    folder = output / "retry-attempts" / hashlib.sha256(dest.stem.encode()).hexdigest()[:20] / str(attempt)
    if not output.resolve().is_relative_to(ROOT.resolve()) or folder.exists():
        raise RuntimeError("retry-archive-target-invalid:" + dest.stem)
    sources = ((dest, "trace.json"),
               (dest.with_suffix(".events.jsonl"), "events.jsonl"),
               (output / (dest.stem + "-provider"), "provider"))
    for source, name in sources:
        if name == "provider" and not source.exists():
            continue  # A local charge-stop happens before provider output is created.
        if not source.exists() or not source.resolve().is_relative_to(output.resolve()):
            raise RuntimeError("retry-source-missing-or-outside-output:" + str(source))
    folder.mkdir(parents=True)
    for source, name in sources:
        if not source.exists() and name == "provider":
            continue
        source.rename(folder / name)
    log(commands, {"event": "rate_limit_retry_archive", "cell": dest.stem,
                   "attempt": attempt, "trace_sha256": sha(folder / "trace.json"),
                   "archive": str(folder.relative_to(output))})


def preflight(manifest_path: Path = MANIFEST) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["schema_version"] not in {"docspectra.icsc.agent_search_factorial_pilot.v2",
                                          "docspectra.icsc.agent_search_factorial_pilot.v3",
                                          "docspectra.icsc.agent_search_factorial_pilot.v4",
                                          "docspectra.icsc.agent_search_factorial_pilot.v5",
                                          "docspectra.icsc.agent_search_factorial_panel_plan.v4",
                                          "docspectra.icsc.agent_search_factorial_panel_plan.v5"}:
        raise SystemExit("pilot-schema-mismatch")
    if not 1 <= len(manifest["tasks"]) <= 70 or len(manifest["conditions"]) != 4:
        raise SystemExit("pilot-scope-mismatch")
    if is_panel(manifest):
        if len(manifest["tasks"]) != 70 or len({t["corpus_id"] for t in manifest["tasks"]}) != 14:
            raise SystemExit("panel-population-mismatch")
        if sha(ROOT / "git/context/evidence/cross-corpus-complex-task-questions-v1/manifest.json") != manifest["question_manifest_sha256"] or \
                sha(ROOT / "git/context/evidence/cross-corpus-roster-recovered-v4-2026-09-28.json") != manifest["roster_sha256"]:
            raise SystemExit("panel-source-hash-mismatch")
    expected = {(1, False), (1, True), (6, False), (6, True)}
    observed = {(c["searches"], c["follow_links"]) for c in manifest["conditions"]}
    if observed != expected or any(c["min_distinct_searches"] != 1 for c in manifest["conditions"]):
        raise SystemExit("condition-mismatch")
    if sha(Path(manifest["prompt_path"])) != manifest["prompt_sha256"]:
        raise SystemExit("prompt-hash-mismatch")
    if sha(ROOT / "git/RetrievalArena/retrieval_arena/model_turn.py") != manifest["ra_model_turn_sha256"]:
        raise SystemExit("ra-contract-hash-mismatch")
    for name, expected_hash in manifest["context_code_sha256"].items():
        if sha(ROOT / "git/context/scripts" / name) != expected_hash:
            raise SystemExit("context-code-hash-mismatch:" + name)
    if manifest["schema_version"].endswith("panel_plan.v5"):
        old_path = Path(manifest["amends_manifest_path"])
        old = json.loads(old_path.read_text(encoding="utf-8"))
        if sha(old_path) != manifest["amends_manifest_sha256"]:
            raise SystemExit("amended-manifest-hash-mismatch")
        for key in ("tasks", "conditions", "budgets", "prompt_path", "prompt_sha256",
                    "ra_model_turn_sha256", "question_manifest_sha256", "roster_sha256", "output_root"):
            if manifest[key] != old[key]:
                raise SystemExit("amended-manifest-design-drift:" + key)
        for key in ("model", "sdk", "store", "automatic_retries", "campaign_calls",
                    "max_input_tokens_planning", "max_output_tokens_planning",
                    "conservative_full_reservation_usd"):
            if manifest["provider"][key] != old["provider"][key]:
                raise SystemExit("amended-manifest-provider-drift:" + key)
        if manifest["provider"]["estimated_charge_stop_usd"] != 40:
            raise SystemExit("amended-charge-stop-mismatch")
    for task in manifest["tasks"]:
        if sha(Path(task["dataset_inventory_path"])) != task["dataset_inventory_sha256"]:
            raise SystemExit("dataset-inventory-hash-mismatch:" + task["corpus_id"])
        if sha(Path(task["dataset_path"]) / "graph_edges.csv") != task["graph_sha256"]:
            raise SystemExit("graph-hash-mismatch:" + task["corpus_id"])
    if len({t["question_id"] for t in manifest["tasks"]}) != len(manifest["tasks"]):
        raise SystemExit("duplicate-question-id")
    provider = manifest["provider"]
    b = manifest["budgets"]
    if provider["campaign_calls"] != len(manifest["tasks"]) * len(manifest["conditions"]) * b["model_calls"]:
        raise SystemExit("campaign-call-mismatch")
    projected = provider["campaign_calls"] * (provider["max_input_tokens_planning"] * 0.125 +
                  provider["max_output_tokens_planning"] * 0.50) / 1_000_000
    if abs(projected - provider["conservative_full_reservation_usd"]) > 1e-8 or \
            projected > provider["estimated_charge_stop_usd"]:
        raise SystemExit("cost-reservation-mismatch")
    affordable_cap = int((provider["estimated_charge_stop_usd"] / provider["campaign_calls"] -
                          provider["max_output_tokens_planning"] * 0.50 / 1_000_000) /
                         (0.125 / 1_000_000))
    if not 0 < provider["max_input_tokens_planning"] <= affordable_cap:
        raise SystemExit("planned-input-cap-unaffordable")
    return manifest


def run(manifest: dict, review_label: str, manifest_path: Path = MANIFEST) -> None:
    if not review_label or review_label == "pending":
        raise SystemExit("approved-outbound-review-label-required")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY-not-visible-to-approved-process")
    output = ROOT / manifest["output_root"]
    lock = output / "manifest.sha256"
    if output.exists():
        expected_lock = manifest.get("amends_manifest_sha256", sha(manifest_path))
        if not lock.exists() or lock.read_text(encoding="ascii").strip() != expected_lock:
            raise SystemExit("output-exists-without-matching-manifest-lock")
    else:
        output.mkdir(parents=True)
        lock.write_text(sha(manifest_path) + "\n", encoding="ascii")
    ledger = output / "cost-ledger.jsonl"
    commands = output / "commands.jsonl"
    prior_commands = ([json.loads(line) for line in commands.read_text(encoding="utf-8").splitlines()]
                      if commands.exists() else [])
    cells = [(task, condition) for task in manifest["tasks"] for condition in manifest["conditions"]]
    first_question_ids = {}
    if is_panel(manifest):
        for task in manifest["tasks"]:
            first_question_ids.setdefault(task["corpus_id"], task["question_id"])
    b = manifest["budgets"]
    p = manifest["provider"]
    for index, (task, condition) in enumerate(cells):
        dest = output / f"{cell_stem(task, condition, first_question_ids)}.json"
        try:
            was_complete = completed_cell(prior_commands, dest)
            prior_ends = [row for row in prior_commands if row.get("cell") == dest.stem and
                          row["event"] == "command_end"]
            prior_trace = json.loads(dest.read_text(encoding="utf-8")) if was_complete else None
            max_attempts = 4 if prior_trace and is_amended_charge_stop(prior_trace, manifest) else 3
            retry_existing = (was_complete and is_panel(manifest) and
                              (is_recorded_rate_limit(prior_trace) or
                               is_amended_charge_stop(prior_trace, manifest)) and
                              len(prior_ends) < max_attempts)
            if was_complete and not retry_existing:
                print(dest.stem, "already-complete", flush=True)
                continue
            if retry_existing:
                archive_rate_limited_attempt(output, dest, len(prior_ends), commands)
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
        cmd = [sys.executable, str(ROOT / "git/context/scripts/complex_task_agent_loop.py"),
               "--dataset", task["dataset_path"], "--question-id", task["question_id"],
               "--question", task["question"], "--prompt", manifest["prompt_path"],
               "--output", str(dest), "--model", p["model"], "--provider", "openai-responses",
               "--outbound-review-id", review_label, "--cost-ledger", str(ledger),
               "--future-calls-after-episode", str((len(cells) - index - 1) * b["model_calls"]),
               "--campaign-calls", str(p["campaign_calls"]),
               "--openai-output-cap", str(p["max_output_tokens_planning"]),
               "--openai-input-cap", str(p["max_input_tokens_planning"]),
               "--estimated-charge-stop-usd", str(p["estimated_charge_stop_usd"]),
               "--search-budget", str(condition["searches"]), "--read-budget", str(b["reads"]),
               "--call-budget", str(b["model_calls"]), "--history-bytes", str(b["history_bytes"]),
               "--wall-seconds", str(b["wall_seconds"]), "--note-budget", str(b["notes"]),
               "--note-chars", str(b["note_chars"]),
               "--min-distinct-searches", str(condition["min_distinct_searches"])]
        if condition["follow_links"]:
            cmd.append("--follow-links")
        attempt = len(prior_ends) + 1
        while True:
            log(commands, {"event": "command_start", "cell": dest.stem, "attempt": attempt,
                           "manifest_sha256": sha(manifest_path), "review_label": review_label})
            result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                                    timeout=b["wall_seconds"] + 300)
            trace = json.loads(dest.read_text(encoding="utf-8")) if dest.exists() else None
            terminal = trace["terminal"] if trace else None
            log(commands, {"event": "command_end", "cell": dest.stem, "attempt": attempt,
                           "returncode": result.returncode, "terminal": terminal,
                           "trace_sha256": sha(dest) if dest.exists() else None,
                           "stderr": redact(result.stderr[-1200:]), "stdout": redact(result.stdout[-500:])})
            print(dest.stem, terminal, "attempt", attempt, flush=True)
            if (is_panel(manifest) and result.returncode == 0 and
                    trace and is_recorded_rate_limit(trace)):
                if attempt >= max_attempts:
                    break  # Final rate-limit failure stays in the denominator.
                archive_rate_limited_attempt(output, dest, attempt, commands)
                attempt += 1
                time.sleep(60)
                continue
            if result.returncode or terminal in {"model_api_error", "model_incomplete", "model_resource_error"}:
                raise SystemExit("pilot-transport-stopped:" + dest.stem)
            break


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--approved-outbound-review-label")
    args = parser.parse_args()
    manifest = preflight(args.manifest)
    if args.preflight:
        print("pilot-preflight-ok:" + sha(args.manifest))
        return
    run(manifest, args.approved_outbound_review_label, args.manifest)


if __name__ == "__main__":
    main()

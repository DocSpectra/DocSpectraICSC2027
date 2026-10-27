"""Sprint 25: paired tier-1 completeness audit over saved answers and saved judgments. Zero model calls.

  python tier1_completeness_25.py reconcile   join checks only (identities, aliases, element lists, coverage); no
                                              element outcome or difference is computed
  python tier1_completeness_25.py plan        write the frozen retrospective plan and the input inventory (write-once)
  python tier1_completeness_25.py prepare     adapt the legacy records into the RA operator input root
  python tier1_completeness_25.py run         RA `compare-answer-elements` in the pinned verification image (network none)
  python tier1_completeness_25.py analyse     check the receipt, reconcile with Sprint 20, write evidence, tables,
                                              review packet files and analyst-side provenance cards (write-once)
  python tier1_completeness_25.py verify      rebuild everything in memory and compare with the recorded bytes

ICSC-specific choices live here: the Sprint 19 paths, the opaque-arm key, the 15-task / 73-element rubric, the
selection salt, the Sprint 20 reconciliation and the Sprint 24 provenance join. The comparison itself is
`retrieval_arena.answer_element_comparison` (RA), run in its container. The legacy readers below only read; they
never instantiate a provider or invoke a grading stage. Aggregate tier-1 results were known before this plan
(Sprint 20); the analysis is retrospective, not pre-registered and not an independent confirmation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTEXT = HERE.parent
ROOT = CONTEXT.parents[1]
RA = ROOT / "git/RetrievalArena"
EVIDENCE = CONTEXT / "evidence"
RUN = ROOT / "artifacts/docker-corpus-variants-v1/runs/wc-v1"
T1 = RUN / "answers-19/t1"
KEY = RUN / "host-private/answers-19-key.json"
LUNA = RUN / "scoring-19/luna-t1"
RUBRIC = RUN / "scoring-19/rubric-t1"
TRANSPORT = "local-qwen-v4"
O_CORPUS = RUN / "answers-19-export/variants/v-5d89a3217d52/corpus"
TASKS = EVIDENCE / "wc-tier1-tasks-v2-2026-10-18.json"
PLAN19 = EVIDENCE / "wc-answers-19-plan-v1-2026-10-22.json"
RUN19 = EVIDENCE / "wc-answers-19-run-v1-2026-10-24.json"
S20 = EVIDENCE / "wc-analysis-20-v1-2026-10-24.json"
S24 = EVIDENCE / "lt-trace-audit-24-v1-2026-10-25.json"
S24_INVENTORY = EVIDENCE / "lt-trace-audit-24-planning-inventory-v1-2026-10-25.json"
S24_OUT = ROOT / "artifacts/lt-trace-audit-24-v1/operator-output"
OUT = ROOT / "artifacts/tier1-completeness-25-v1"
OP_IN, OP_OUT = OUT / "operator-input", OUT / "operator-output"
DATE = "2026-10-25"
PLAN = EVIDENCE / f"tier1-completeness-25-plan-v1-{DATE}.json"
INVENTORY = EVIDENCE / f"tier1-completeness-25-inputs-v1-{DATE}.json"
RESULT = EVIDENCE / f"tier1-completeness-25-v1-{DATE}.json"
ELEMENTS = EVIDENCE / f"tier1-completeness-25-elements-v1-{DATE}.json"
CARDS = EVIDENCE / f"tier1-completeness-25-cards-v1-{DATE}.json"
REVIEW_RECEIPT = EVIDENCE / f"tier1-completeness-25-review-packet-v1-{DATE}.json"
TABLES_MD = CONTEXT / f"reports/{DATE}-tier1-paired-completeness-tables.md"
IMAGE_RECEIPT = OUT / "image/verification-image.json"
ARMS = ("O", "S", "LT", "R0")
CONDITIONS = ("s1-no-follow", "s1-follow", "s6-no-follow", "s6-follow")
REPLICATES = (1, 2)
CONTRASTS = (("LT", "O"), ("LT", "R0"), ("R0", "O"), ("S", "O"))
SELECTION_CONTRASTS = (("LT", "O"), ("LT", "R0"))
SALT = "25|20261025"
MEASURES = ("correctness", "fit", "completeness", "caveats", "clarity")
PASSES = ("A", "B")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rel(path):
    return Path(path).resolve().relative_to(ROOT).as_posix()


def pin(path):
    return {"path": rel(path), "bytes": Path(path).stat().st_size, "sha256": sha(path)}


def canonical(value):
    return json.dumps(value, indent=1, ensure_ascii=False, sort_keys=True) + "\n"


def write_new(path, data):
    path = Path(path)
    if path.exists():
        raise SystemExit(f"refusing to overwrite {rel(path)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else canonical(data), encoding="utf-8", newline="\n")


# ------------------------------------------------------------------ legacy readers (read-only)

def parse_stem(stem):
    variant = "-".join(stem.split("-")[:2])
    rest = stem[len(variant) + 1:]
    q12, rest = rest.split("-", 1)
    cond, rep = rest.rsplit("-r", 1)
    return variant, q12, cond, int(rep)


def load_tasks():
    tasks = read_json(TASKS)["tasks"]
    return [{"task_id": t["task_id"], "question": t["question"], "question_sha256": sha_text(t["question"]),
             "elements": [{"element_id": e["element_id"], "requirement": e["requirement"], "acceptance": e["acceptance"]}
                          for e in t["core_elements"]],
             "element_pages": {e["element_id"]: e["page_id"] for e in t["core_elements"]},
             "element_quotes": {e["element_id"]: e["supporting_quotes"] for e in t["core_elements"]},
             "support_page_ids": t["support_page_ids"]} for t in tasks]


def load_answers(issues):
    """One record per active tier-1 trace, with schedule identity and the answer text when one was produced."""
    arm_of = {o: v["variant"] for o, v in read_json(KEY)["variants"].items()}
    tasks = {t["task_id"]: t for t in load_tasks()}
    conds = {c["id"]: c for c in read_json(PLAN19)["answering"]["conditions"]}
    out = []
    for path in sorted(T1.glob("*.json")):
        t = read_json(path)
        variant, q12, cond, rep = parse_stem(path.stem)
        checks = {"variant_in_key": variant in arm_of, "task_known": t["question_id"] in tasks,
                  "filename_task_prefix": t["question_id"][:12] == q12,
                  "question_text": t["question_id"] in tasks and t["question"] == tasks[t["question_id"]]["question"],
                  "question_sha256": t["question_sha256"] == sha_text(t["question"]),
                  "condition_known": cond in conds,
                  "follow_setting": cond in conds and t["follow_links"] == conds[cond]["follow_links"],
                  "search_budget": cond in conds and t["budgets"]["searches"] == conds[cond]["searches"]}
        bad = sorted(k for k, ok in checks.items() if not ok)
        if bad:
            issues.append({"issue": "trace_identity", "answer_id": path.stem, "failed": bad})
        text = (t.get("answer") or {}).get("text") if t["terminal"] == "answer" else None
        out.append({"answer_id": path.stem, "trace": path, "variant": variant, "arm": arm_of.get(variant),
                    "task_id": t["question_id"], "condition": cond, "replicate": rep, "terminal": t["terminal"],
                    "text": text, "question_sha256": t["question_sha256"], "trace_sha256": sha(path)})
    return out


def load_rubric(issues):
    """{unit_id: record} in RA's element_judgment_record.v1 form, read from aliases, packets and the selected
    attempt of each valid completion (raw and normalized verdicts verbatim)."""
    aliases = read_json(RUBRIC / "aliases.json")
    manifest = {e["unit_id"]: e for e in read_json(RUBRIC / "packet-manifest.json")}
    if set(manifest) != set(aliases):
        issues.append({"issue": "rubric_manifest_alias_mismatch",
                       "only_manifest": sorted(set(manifest) - set(aliases)), "only_aliases": sorted(set(aliases) - set(manifest))})
    out = {}
    for uid, files in sorted(aliases.items()):
        packet_path = RUBRIC / "packets" / f"{uid}.json"
        if sha(packet_path) != manifest[uid]["sha256"]:
            issues.append({"issue": "rubric_packet_hash", "unit_id": uid})
        units = read_json(packet_path)["units"]
        if len(units) != 1 or units[0]["unit_id"] != uid:
            issues.append({"issue": "rubric_packet_units", "unit_id": uid, "units": len(units)})
        unit = units[0]
        folder = RUBRIC / TRANSPORT / uid
        sources = {"packet": sha(packet_path)}
        record = {"schema_version": "retrieval_arena.element_judgment_record.v1", "judgment_id": uid,
                  "question": unit["question"], "answer": unit["answer"], "elements": unit["elements"],
                  "status": "no_completion", "selected_attempt": None, "raw_verdicts": None, "strict_verdicts": None}
        done = folder / "completion.json"
        attempts = sorted(folder.glob("attempt-[0-9][0-9].json"))
        if done.exists():
            sources["completion"] = sha(done)
            completion = read_json(done)
            if completion["valid"]:
                n = completion["attempt"]
                raw_path = folder / f"attempt-{n:02d}.raw.json"
                norm_path = folder / f"attempt-{n:02d}.normalized.json"
                sources.update(raw=sha(raw_path), normalized=sha(norm_path))
                if sources["raw"] != completion["raw_response_sha256"] or sources["normalized"] != completion["normalized_response_sha256"]:
                    issues.append({"issue": "rubric_attempt_hash", "unit_id": uid})
                raw_units = json.loads(read_json(raw_path)["message"]["content"])["units"]
                norm_units = read_json(norm_path)["units"]
                if len(raw_units) != 1 or len(norm_units) != 1:
                    issues.append({"issue": "rubric_verdict_units", "unit_id": uid})
                record.update(status="valid", selected_attempt=n, raw_verdicts=raw_units[0]["verdicts"],
                              strict_verdicts=norm_units[0]["verdicts"])
            else:
                record["status"] = "invalid_completion"
        elif attempts:
            last = read_json(attempts[-1])
            sources["last_attempt"] = sha(attempts[-1])
            record["status"] = "no_completion:" + str(last.get("validation_error"))
        record["sources"] = sources
        out[uid] = {"record": record, "answer_ids": [Path(f).stem for f in files]}
    return out


def load_luna(issues, answers):
    """{measure: {answer_id: [pass A score or None, pass B score or None]}} from each pass's valid completions,
    after checking that every scored packet carries the trace's question and answer text."""
    text = {a["answer_id"]: a for a in answers}
    tasks = {t["task_id"]: t for t in load_tasks()}
    scores = {m: defaultdict(lambda: [None, None]) for m in MEASURES}
    counts = {}
    for i, p in enumerate(PASSES):
        root = LUNA / f"pass-{p}"
        key = read_json(root / "host-private/answer-key.json")
        valid = 0
        for entry in read_json(root / "packet-manifest.json"):
            pid = entry["packet_id"]
            packet_path = root / "packets" / entry["packet"]
            if sha(packet_path) != entry["sha256"]:
                issues.append({"issue": "luna_packet_hash", "pass": p, "packet_id": pid})
            aid = Path(key[pid]["answer_id"]).stem
            packet = read_json(packet_path)
            a = text.get(aid)
            if a is None or packet["answer"] != a["text"] or packet["question"] != tasks[a["task_id"]]["question"]:
                issues.append({"issue": "luna_packet_identity", "pass": p, "packet_id": pid, "answer_id": aid})
                continue
            done = root / "calls" / pid / "completion.json"
            if done.exists() and read_json(done)["valid"]:
                c = read_json(done)
                norm = read_json(root / "calls" / pid / f"attempt-{c['attempt']:02d}.normalized.json")
                for m in MEASURES:
                    scores[m][aid][i] = norm[m]["score"]
                valid += 1
        counts[p] = {"packets": len(key), "valid": valid}
    return {m: {aid: v for aid, v in sorted(d.items())} for m, d in scores.items()}, counts


def reconcile():
    """Every join this audit relies on, with counts; no element outcome is read beyond verdict ids."""
    issues = []
    tasks = load_tasks()
    answers = load_answers(issues)
    rubric = load_rubric(issues)
    _, luna_counts = load_luna(issues, answers)
    by_id = {a["answer_id"]: a for a in answers}
    plan19 = read_json(PLAN19)
    scheduled = {(c["variant"], c["question_id"], c["condition"], c["replicate"])
                 for led in plan19["ledgers"] if led["tier"] == 1 for c in led["cells"]}
    observed = {(a["variant"], a["task_id"], a["condition"], a["replicate"]) for a in answers}
    s24 = {Path(r["path"]).stem: r["sha256"] for r in read_json(S24_INVENTORY)["trace_inventory"]}
    trace_pins = Counter("match" if s24.get(a["answer_id"]) == a["trace_sha256"] else "differs" for a in answers)
    alias_targets = Counter(aid for u in rubric.values() for aid in u["answer_ids"])
    join = Counter()
    frozen = {t["task_id"]: [(e["element_id"], e["requirement"], e["acceptance"]) for e in t["elements"]] for t in tasks}
    for uid, u in rubric.items():
        rec = u["record"]
        for aid in u["answer_ids"]:
            a = by_id.get(aid)
            if a is None:
                join["alias_to_unknown_answer"] += 1
                continue
            ok_answer = rec["answer"] == a["text"]
            ok_question = sha_text(rec["question"]) == a["question_sha256"]
            ok_elements = [(e["element_id"], e["requirement"], e["acceptance"]) for e in rec["elements"]] == frozen[a["task_id"]]
            join[f"answer_text_{ok_answer}"] += 1
            join[f"question_{ok_question}"] += 1
            join[f"elements_{ok_elements}"] += 1
        if rec["status"] == "valid":
            ids = [e["element_id"] for e in rec["elements"]]
            for kind in ("raw_verdicts", "strict_verdicts"):
                got = Counter(v.get("element_id") for v in rec[kind])
                if sorted(got) != sorted(ids) or any(n > 1 for n in got.values()):
                    join[f"{kind}_id_problem"] += 1
    unjudged = sorted(a["answer_id"] for a in answers if a["answer_id"] not in alias_targets)
    return {
        "tasks": len(tasks), "elements": sum(len(t["elements"]) for t in tasks),
        "elements_per_task": [len(t["elements"]) for t in tasks],
        "answers": len(answers), "schedule_cells_plan19": len(scheduled), "schedule_matches_traces": scheduled == observed,
        "terminals": dict(Counter(a["terminal"] for a in answers)),
        "per_arm_condition": {f"{arm}|{c}": n for (arm, c), n in sorted(Counter((a["arm"], a["condition"]) for a in answers).items())},
        "trace_hash_vs_sprint24_inventory": dict(trace_pins),
        "rubric_units": len(rubric), "rubric_alias_targets": len(alias_targets),
        "rubric_aliases_per_unit": dict(Counter(len(u["answer_ids"]) for u in rubric.values())),
        "rubric_answers_with_several_units": sum(n > 1 for n in alias_targets.values()),
        "rubric_status": dict(Counter(u["record"]["status"] for u in rubric.values())),
        "answers_without_rubric_unit": [{"answer_id": aid, "terminal": by_id[aid]["terminal"]} for aid in unjudged],
        "rubric_unit_without_completion": sorted((uid, u["answer_ids"]) for uid, u in rubric.items()
                                                 if u["record"]["status"] != "valid"),
        "join_checks": dict(sorted(join.items())), "luna": luna_counts, "issues": issues,
    }


# ------------------------------------------------------------------ inventory and plan

def inventory():
    """Every consumed input file with bytes and SHA-256 (paths relative to the workspace root)."""
    files = [TASKS, PLAN19, RUN19, S20, S24, S24_INVENTORY, KEY, RUBRIC / "aliases.json", RUBRIC / "packet-manifest.json"]
    files += sorted(T1.glob("*.json"))
    for uid in sorted(read_json(RUBRIC / "aliases.json")):
        files.append(RUBRIC / "packets" / f"{uid}.json")
        folder = RUBRIC / TRANSPORT / uid
        files += sorted(p for p in folder.iterdir() if p.suffix == ".json")
    for p in PASSES:
        root = LUNA / f"pass-{p}"
        files += [root / "packet-manifest.json", root / "receipt.json", root / "host-private/answer-key.json"]
        for entry in read_json(root / "packet-manifest.json"):
            files.append(root / "packets" / entry["packet"])
            files += sorted(q for q in (root / "calls" / entry["packet_id"]).iterdir() if q.suffix == ".json")
    for t in load_tasks():
        for pid in sorted(set(t["element_pages"].values()) | set(t["support_page_ids"])):
            if (O_CORPUS / f"{pid}.md").exists():
                files.append(O_CORPUS / f"{pid}.md")
    for name in ("trace_provenance.traces.jsonl", "trace_provenance.citations.jsonl", "trace_provenance.actions.jsonl",
                 "trace_provenance_manifest.json", "operator_receipt.v1.json"):
        files.append(S24_OUT / name)
    rows = [pin(f) for f in files]
    return {"schema_version": "docspectra.icsc.tier1_completeness_25.inputs.v1", "created": DATE, "files": rows,
            "files_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()}


def ra_identity():
    commit = subprocess.run(["git", "-C", str(RA), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    tree = subprocess.run(["git", "-C", str(RA), "rev-parse", "HEAD^{tree}"], capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(RA), "status", "--porcelain"], capture_output=True, text=True, check=True).stdout.strip()
    return {"commit": commit, "tree": tree, "clean": not dirty,
            "module": pin(RA / "retrieval_arena/answer_element_comparison.py"),
            "operator": pin(RA / "containers/verification/answer_element_comparison.py"),
            "contract": pin(RA / "docs/evaluations/answer_element_comparison.md"),
            "tests": pin(RA / "unit_tests/test_answer_element_comparison.py")}


def plan(_args):
    rec = reconcile()
    if rec["issues"] or not rec["schedule_matches_traces"]:
        raise SystemExit("reconciliation issues: resolve or amend before freezing: " + json.dumps(rec["issues"][:5]))
    inv = inventory()
    ra = ra_identity()
    if not ra["clean"]:
        raise SystemExit("RA worktree is dirty; commit before freezing")
    image = read_json(IMAGE_RECEIPT)
    if image["source_commit"] != ra["commit"]:
        raise SystemExit("image receipt is not for the RA HEAD")
    s20 = read_json(S20)["tier1"]["tables"]
    write_new(INVENTORY, inv)
    data = {
        "schema_version": "docspectra.icsc.tier1_completeness_25.plan.v1", "created": DATE, "version": 1,
        "status": "frozen before any element-level difference, persistence table or example selection was computed",
        "retrospective": ("Aggregate tier-1 results were already known (Sprint 20, October 24) when this plan was "
                          "written. The analysis is a retrospective, descriptive decomposition of saved answers and "
                          "saved judgments; it is not pre-registration and not independent confirmation. Reusing the "
                          "same model verdicts does not independently validate answer completeness."),
        "model_calls": 0, "network_calls": 0,
        "question": ("Which required pieces of information do the saved LT, O, R0 and S tier-1 answers cover or omit "
                     "according to the frozen rubric judgments, and how consistent are the differences across the "
                     "existing conditions and replicates?"),
        "inputs": {"inventory": pin(INVENTORY), "files": len(inv["files"]), "files_sha256": inv["files_sha256"],
                   "sources": [pin(p) for p in (TASKS, PLAN19, RUN19, S20, S24, S24_INVENTORY)]},
        "code": {"driver": pin(Path(__file__)), "ra": ra, "image": image,
                 "note": "rendering and report code in the driver may be completed after the freeze; any change "
                         "to joins, populations, score rules, aggregation or selection needs a versioned amendment"},
        "reconciliation": rec,
        "populations": {
            "scheduled_answers": 480, "arms": list(ARMS), "conditions": list(CONDITIONS), "replicates": list(REPLICATES),
            "tasks": 15, "elements": 73, "element_rows": sum(len(ARMS) * len(CONDITIONS) * len(REPLICATES) * n for n in rec["elements_per_task"]),
            "exclusions": "none: every scheduled answer x element is one row; an answer without an answer, a rubric "
                          "unit or a valid completion stays as not observed with its status",
            "answer_replicates_from": "the Sprint 19 schedule (trace file names and the plan's ledgers), never from "
                                      "the number of judge packets or scoring passes"},
        "join_keys": {
            "answer": "trace file stem = opaque variant + 12-hex task prefix + condition + replicate; arm from the "
                      "host-private key; task from the trace question_id (full 64-hex task_id), checked against the "
                      "prefix, question text and question SHA-256; condition checked against follow and search budget",
            "rubric": "aliases.json unit -> answer files (one-to-many kept); a unit joins an answer only when its "
                      "packet answer equals the trace answer text, its question hash equals the trace's, and its "
                      "elements equal the frozen task elements (ids, requirement, acceptance, order)",
            "luna": "each pass's host-private answer key label -> answer file; packet answer and question checked "
                    "against the trace and task"},
        "score_rules": {
            "verdicts": "the completion's selected attempt only: raw verdicts from attempt-XX.raw.json, strict from "
                        "attempt-XX.normalized.json; element_id, verdict, answer_span and reason preserved",
            "relaxed": "quote_match_relaxed_v1, reproduced exactly in RA; counted = raw satisfied and span admitted",
            "outcomes": "relaxed_satisfied | quote_rejected (raw positive, span fails) | judged_not_satisfied | "
                        "not_observed (no answer, no unit, no valid completion, identity mismatch, missing or "
                        "duplicate verdict); a quote match shows the text exists, not that it is correct",
            "luna": "five measures per pass from the valid completion's normalized attempt; the two passes of one "
                    "answer are averaged (missing dropped); they are two grades, not two answers"},
        "aggregation": {
            "rule": "replicate_condition_task_v1 (Sprint 20 order): replicates within condition, conditions within "
                    "task, tasks equally; missing values dropped at each level as Sprint 20 did",
            "contrasts": [f"{a} - {b}" for a, b in CONTRASTS],
            "main": ["LT - O", "LT - R0"], "context": ["R0 - O", "S - O"],
            "element_cells": "per task x condition x element x arm: counted eligible verdicts and observed grades "
                             "over two answer replicates with the scheduled denominator; difference of rates; "
                             "incomplete cells flagged; complete-cell comparison shown beside the historical order",
            "intervals": "none new; Sprint 20 bootstrap intervals are linked from wc-analysis-20 tier1.tables",
            "descriptive_only": "condition- and replicate-specific tables are descriptive; no best condition, no "
                                "subgroup search, no new test family; identically numbered replicates are display "
                                "pairs, not paired draws; S - O is context, not a noise threshold or equivalence bound"},
        "reproduction_targets": {m: {c: {k: s20[m][c][k] for k in ("estimate", "mean_a", "mean_b", "n", "ci95")}
                                     for c in s20[m]} for m in ("completeness", "rubric_relaxed")},
        "selection": {
            "max_pairs": 8, "contrasts": ["LT-O", "LT-R0"], "focal": "LT",
            "candidates": "same task, condition and numbered replicate with complete valid element records on both sides",
            "sets": "gains = elements counted for LT only; losses = elements counted for the comparator only (relaxed)",
            "strata": ["net_gain: gains > losses", "net_loss: losses > gains", "tradeoff: equal nonzero",
                       "label_agreement: no gains and no losses"],
            "rule": f"lowest SHA-256 of '{SALT}|<contrast>|<full_task_id>|<condition>|<replicate>' per stratum "
                    "(contrast written LT-O / LT-R0, replicate as 1 or 2); an empty stratum stays empty",
            "position": f"LT side alternates A, B, A, B in ascending SHA-256 of '{SALT}|position|<contrast>|...' "
                        "within each contrast (balanced within one)",
            "order": f"packet order by ascending SHA-256 of '{SALT}|order|<contrast>|...'",
            "interpretation": "retrospective and outcome-stratified: illustrates differences and checks judgments; "
                              "cannot estimate prevalence or population improvement"},
        "packet": {
            "shows": "task, frozen requirements and acceptance criteria, the original (O) corpus pages the rubric "
                     "cites as reference excerpts, complete saved answers A/B, empty review form",
            "hides": "arm identities, opaque variant ids, prior scores, verdicts and reasons, strata, trace file "
                     "names, the key, conditions and replicate numbers",
            "disclosed_risks": "answer content (wording, cited page ids of LT-created pages) and author familiarity "
                               "may reveal treatment; the author is not an independent reviewer",
            "human": "optional; registered in pending_review as optional, ready; no human claim without returned "
                     "labels; labels never prefilled from model output; a model never acts as the reviewer"},
        "provenance_cards": "selected packet cases only, analyst-side, from saved Sprint 24 tables: cited pages, "
                            "first exposure routes, follow edge classes; requirement-to-evidence mapping "
                            "`unresolved` unless reliable; page ids and similarity are not support",
        "runtime": {"command": "compare-answer-elements", "network": "none", "image_receipt": rel(IMAGE_RECEIPT)},
        "preservation": "source artifacts and frozen evidence are read-only; the inventory hashes are re-verified at "
                        "analyse and verify",
    }
    write_new(PLAN, data)
    print(rel(PLAN), sha(PLAN))


# ------------------------------------------------------------------ prepare and run

def references(task):
    out = []
    for pid in sorted(set(task["element_pages"].values()) | set(task["support_page_ids"])):
        if (O_CORPUS / f"{pid}.md").exists():
            out.append(pid)
    return out


def build_config():
    issues = []
    tasks = load_tasks()
    answers = load_answers(issues)
    rubric = load_rubric(issues)
    luna, _ = load_luna(issues, answers)
    if issues:
        raise SystemExit("join issues: " + json.dumps(issues[:5]))
    files = {}
    cfg_answers = []
    for a in answers:
        path = None
        if a["text"] is not None:
            path = f"answers/{a['answer_id']}.txt"
            files[path] = a["text"].encode("utf-8")
        cfg_answers.append({"answer_id": a["answer_id"], "task_id": a["task_id"], "arm": a["arm"],
                            "condition": a["condition"], "replicate": a["replicate"], "terminal": a["terminal"],
                            "answer_path": path, "answer_sha256": sha_text(a["text"]) if a["text"] is not None else None,
                            "question_sha256": a["question_sha256"]})
    judgments = []
    for uid, u in sorted(rubric.items()):
        path = f"records/{uid}.json"
        rec = dict(u["record"])
        if rec["status"] != "valid":
            rec["status"] = rec["status"] if rec["status"].startswith("no_") else "invalid_completion"
        files[path] = (json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        judgments.append({"judgment_id": uid, "answer_ids": u["answer_ids"], "record_path": path})
    cfg_tasks = []
    for t in tasks:
        refs = []
        for pid in references(t):
            path = f"references/{pid}.md"
            files[path] = (O_CORPUS / f"{pid}.md").read_bytes()
            refs.append({"label": f"Original documentation page {pid}", "path": path})
        cfg_tasks.append({"task_id": t["task_id"], "question": t["question"], "question_sha256": t["question_sha256"],
                          "elements": t["elements"], "references": refs})
    forbidden = sorted(read_json(KEY)["variants"]) + ["answers-19", "host-private", "LT-r", "R0-r"]
    config = {"schema_version": "retrieval_arena.answer_element_comparison_config.v1", "arms": list(ARMS),
              "conditions": list(CONDITIONS), "replicates": list(REPLICATES),
              "contrasts": [list(c) for c in CONTRASTS], "aggregation": "replicate_condition_task_v1",
              "relaxed_rule": "quote_match_relaxed_v1", "tasks": cfg_tasks, "answers": cfg_answers,
              "judgments": judgments, "scores": luna,
              "selection": {"salt": SALT, "focal": "LT", "contrasts": [list(c) for c in SELECTION_CONTRASTS],
                            "max_pairs": 8, "forbidden_strings": forbidden}}
    files["config.json"] = (json.dumps(config, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")
    return files


def prepare(_args):
    if OP_IN.exists():
        raise SystemExit(f"{rel(OP_IN)} exists")
    sys.path.insert(0, str(RA))
    from retrieval_arena.candidate_retrieval import canonical as ra_canonical, regular_inventory, sha256_bytes
    files = build_config()
    for name, blob in files.items():
        p = OP_IN / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)
    inv = regular_inventory(OP_IN)
    request = {"schema_version": "retrieval_arena.answer_element_operator_request.v1",
               "command": "compare-answer-elements", "config_path": "config.json",
               "input_inventory": inv, "input_inventory_sha256": sha256_bytes(ra_canonical(inv))}
    (OP_IN / "operator-request.json").write_bytes(ra_canonical(request) + b"\n")
    print(len(inv), "input files;", request["input_inventory_sha256"])


def run(_args):
    if OP_OUT.exists():
        raise SystemExit(f"{rel(OP_OUT)} exists")
    OP_OUT.mkdir(parents=True)
    (OP_OUT / ".retrieval-arena-output-owner").write_text("retrieval_arena.output_root.v1\n", encoding="utf-8", newline="\n")
    cmd = ["pwsh", "-NoProfile", "-File", str(RA / "scripts/run-verification.ps1"), "compare-answer-elements",
           "-InputRoot", str(OP_IN), "-OutputRoot", str(OP_OUT), "-Receipt", str(IMAGE_RECEIPT)]
    print(" ".join(cmd))
    sys.exit(subprocess.run(cmd).returncode)


# ------------------------------------------------------------------ analyse

AMENDMENT = EVIDENCE / f"tier1-completeness-25-plan-amendment-1-{DATE}.json"
REVIEW_DIR = OUT / "review"
OUTPUT_TABLES = ("answer_element.rows.jsonl", "answer_element.answers.jsonl", "answer_element.issues.jsonl",
                 "answer_element.cells.jsonl", "answer_element.contrasts.jsonl", "answer_element.elements.jsonl",
                 "answer_element.tasks.jsonl", "answer_element.pairs.jsonl")
NOT_ESTABLISHED = [
    "independent validation of answer completeness: every element outcome is a legacy local-judge (qwen3:14b) "
    "verdict, and the Luna scores are model grades of answers Luna wrote",
    "human-verified gains, losses or judge errors (HR-22 is optional and unreviewed)",
    "correctness of satisfied requirements: a matching quotation shows the text exists in the answer",
    "unscored additions outside the frozen rubric (more words are not more completeness)",
    "statistical equivalence of arms (non-rejection is not equivalence; S - O is not a noise bound)",
    "reliance on, or causal benefit of, LT links or pages for any requirement",
    "generalization beyond one corpus, one editor realization per treatment and the fixed 15-task rubric",
    "any LT detector or spectral predictor of answer completeness"]


def jsonl_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def task_index():
    return {t["task_id"]: i + 1 for i, t in enumerate(read_json(TASKS)["tasks"])}


def check_run():
    """The receipt, manifest and input identity of the operator run; raises on any mismatch."""
    receipt = read_json(OP_OUT / "operator_receipt.v1.json")
    image = read_json(IMAGE_RECEIPT)
    manifest = read_json(OP_OUT / "answer_element_manifest.json")
    amendment = read_json(AMENDMENT)
    problems = []
    if receipt["status"] != "completed" or receipt["exit_code"] != 0:
        problems.append("receipt-status")
    if receipt["identity"]["source_commit"] != image["source_commit"] or image["source_commit"] != amendment["ra"]["commit"]:
        problems.append("ra-commit")
    if receipt["identity"]["image_reference"] != image["image"]["reference"]:
        problems.append("image")
    if receipt["metric_code_sha256"] != amendment["ra"]["module"]["sha256"]:
        problems.append("module-hash")
    if receipt["config_sha256"] != sha(OP_IN / "config.json") or receipt["request_sha256"] != sha(OP_IN / "operator-request.json"):
        problems.append("input-identity")
    for name, digest in receipt["outputs"].items():
        if digest is not None and (name == "answer_element_manifest.json" and sha(OP_OUT / name) != digest
                                   or name != "answer_element_manifest.json" and manifest["outputs"].get(name) != digest
                                   or sha(OP_OUT / name) != digest):
            problems.append(f"output:{name}")
    if problems:
        raise SystemExit("operator run check failed: " + ",".join(problems))
    return receipt, image, manifest


def preservation():
    """Re-hash every pinned input; the plan's inventory must still hold byte for byte."""
    recorded = read_json(INVENTORY)
    changed = [r["path"] for r in recorded["files"] if sha(ROOT / r["path"]) != r["sha256"]]
    return {"files": len(recorded["files"]), "changed": changed, "inventory_sha256": sha(INVENTORY)}


def cross_check_rules(rows):
    """RA per-answer strict/relaxed/raw scores against the context `rubric_quote_match` reader (Sprint 19/20 rule)."""
    import rubric_quote_match as rqm
    ctx = rqm.score_judge_root(RUBRIC, TRANSPORT)
    per = defaultdict(lambda: {"strict": 0, "relaxed": 0, "raw": 0, "n": 0, "judged": True})
    for r in rows:
        p = per[r["answer_id"]]
        p["n"] += 1
        p["judged"] &= r["judgment_status"] == "judged"
        p["strict"] += r["strict_verdict"] == "satisfied"
        p["relaxed"] += bool(r["counted"])
        p["raw"] += r["raw_verdict"] == "satisfied"
    out = Counter()
    for f, scores in ctx.items():
        p = per[Path(f).stem]
        if scores is None:
            out["both_unscored" if not p["judged"] else "context_unscored_only"] += 1
            continue
        for k, ck in (("strict", "rubric_v1_strict"), ("relaxed", "rubric_v1_relaxed"), ("raw", "rubric_v1_raw")):
            out[f"{k}_equal" if abs(p[k] / p["n"] - scores[ck]) < 1e-12 else f"{k}_differs"] += 1
    return dict(sorted(out.items()))


def signed_persistence(contrasts):
    out = Counter()
    for c in contrasts:
        d = [x for x in c["replicate_directions"].values() if x]
        sign = "+" if d and d[0] > 0 else "-" if d else ""
        out[c["persistence"] + (sign if c["persistence"] in ("persists", "one_sided") else "")] += 1
    return dict(sorted(out.items()))


def contrast_summaries(summary, contrasts, elements, tasks, pairs, breakdowns):
    out = {}
    for label, base in summary["contrasts"].items():
        cs = [c for c in contrasts if c["contrast"] == label]
        es = [e for e in elements if e["contrast"] == label]
        ts = [t for t in tasks if t["contrast"] == label]
        ps = [p for p in pairs if p["contrast"] == label]
        out[label] = {
            "cells": base["cells"], "cells_complete": base["cells_complete"],
            "cell_directions": {"favourable": base["cell_directions"].get("1", 0),
                                "unfavourable": base["cell_directions"].get("-1", 0),
                                "unchanged": base["cell_directions"].get("0", 0)},
            "cell_persistence_signed": signed_persistence(cs), "cell_shared": base["cell_shared"],
            "elements_by_mean_difference": {"favourable": base["elements_favourable"],
                                            "unfavourable": base["elements_unfavourable"],
                                            "unchanged": base["elements_unchanged"]},
            "elements_by_replicate_pairs": {
                "gain_only_at_least_2": sum(e["replicate_pairs_gain"] >= 2 and not e["replicate_pairs_loss"] for e in es),
                "loss_only_at_least_2": sum(e["replicate_pairs_loss"] >= 2 and not e["replicate_pairs_gain"] for e in es),
                "both_gains_and_losses": sum(e["replicate_pairs_gain"] > 0 and e["replicate_pairs_loss"] > 0 for e in es),
                "single_pair_or_none": sum(e["replicate_pairs_gain"] + e["replicate_pairs_loss"] <= 1 for e in es)},
            "replicate_pair_element_changes": {"gains": sum(e["replicate_pairs_gain"] for e in es),
                                               "losses": sum(e["replicate_pairs_loss"] for e in es)},
            "tasks_by_mean_difference": {"favourable": sum(t["mean_difference"] > 0 for t in ts),
                                         "unfavourable": sum(t["mean_difference"] < 0 for t in ts),
                                         "zero": sum(t["mean_difference"] == 0 for t in ts)},
            "tasks_with_elements_both_ways": sum(t["elements_favourable"] > 0 and t["elements_unfavourable"] > 0 for t in ts),
            "replicate_pair_strata": dict(sorted(Counter(p["stratum"] or "incomplete" for p in ps).items())),
            "by_condition": breakdowns[label]["by_condition"], "by_replicate": breakdowns[label]["by_replicate"],
            "complete_cells_overall": breakdowns[label]["complete_cells_overall"],
            "sum_of_element_contributions": base["sum_of_contributions"]}
    return out


def element_records(cells, contrasts, elements, tasks_out):
    """All 73 frozen elements with per-arm rates and the four contrasts by condition and numbered replicate."""
    idx = task_index()
    frozen = {t["task_id"]: t for t in load_tasks()}
    cell = {(c["task_id"], c["condition"], c["element_id"], c["arm"]): c for c in cells}
    con = defaultdict(dict)
    for c in contrasts:
        con[(c["task_id"], c["element_id"], c["contrast"])][c["condition"]] = c
    summ = {(e["task_id"], e["element_id"], e["contrast"]): e for e in elements}
    out = []
    for task_id, t in sorted(frozen.items(), key=lambda x: idx[x[0]]):
        for e in t["elements"]:
            eid = e["element_id"]
            arms = {}
            for arm in ARMS:
                cs = [cell[(task_id, c, eid, arm)] for c in CONDITIONS]
                arms[arm] = {"counted": sum(c["counted"] for c in cs), "observed": sum(c["observed"] for c in cs),
                             "scheduled": sum(c["scheduled"] for c in cs),
                             "by_condition": {c["condition"]: f"{c['counted']}/{c['observed']}" for c in cs}}
            rec = {"task": idx[task_id], "task_id": task_id, "element_id": eid, "requirement": e["requirement"],
                   "acceptance": e["acceptance"], "page_id": t["element_pages"][eid], "arms": arms, "contrasts": {}}
            for a, b in CONTRASTS:
                label = f"{a}-{b}"
                s = summ[(task_id, eid, label)]
                rec["contrasts"][label] = {
                    "mean_difference": s["mean_difference"], "mean_difference_complete_cells": s["mean_difference_complete_cells"],
                    "contribution_to_overall": s["contribution_to_overall"],
                    "replicate_pairs": {"gain": s["replicate_pairs_gain"], "loss": s["replicate_pairs_loss"],
                                        "same": s["replicate_pairs_same"], "incomplete": s["replicate_pairs_incomplete"]},
                    "persistence": s["persistence"],
                    "shared_omission_conditions": s["shared_omission_conditions"],
                    "by_condition": {c: {"difference": x["difference"], "complete": x["complete"],
                                         "replicate_directions": x["replicate_directions"], "persistence": x["persistence"]}
                                     for c, x in sorted(con[(task_id, eid, label)].items())}}
            out.append(rec)
    task_rows = [{"task": idx[t["task_id"]], **{k: t[k] for k in t if k != "task_id"}, "task_id": t["task_id"]}
                 for t in tasks_out]
    return out, sorted(task_rows, key=lambda r: (r["contrast"], r["task"]))


def provenance_card(answer_id, element_pages, s24):
    """Analyst-side Sprint 24 observations for one answer: never shown to a blinded reviewer first."""
    trace = s24["traces"].get(answer_id)
    cites = s24["citations"].get(answer_id, [])
    follows = [a for a in s24["actions"].get(answer_id, []) if a["action"] == "follow"]
    o_pages = {p.stem for p in O_CORPUS.glob("*.md")}
    pages = {}
    for pid in sorted(set(element_pages)):
        cs = [c for c in cites if c["doc_id"] == pid]
        pages[pid] = {"cited": bool(cs), "first_exposure_routes": sorted({r for c in cs for r in c["first_exposure_routes"]}),
                      "reached_by_follow": any(c["page_reached_by_follow"] for c in cs),
                      "follow_edge_classes": sorted({k for c in cs for k in c["first_exposing_follow_edge_classes"]}),
                      "mapping": "unresolved: page-level co-occurrence only; tier 1 has no line-level support labels"}
    return {"trace_found": trace is not None,
            "trace": {k: trace[k] for k in ("identity_ok", "terminal", "follows_attempted", "follows_accepted",
                                            "follows_accepted_added", "final_context", "replay")} if trace else None,
            "citations": [{"doc_id": c["doc_id"], "lines": [c["start_line"], c["end_line"]],
                           "first_exposure": c["first_exposure"], "routes": c["first_exposure_routes"],
                           "follow_edge_classes": c["first_exposing_follow_edge_classes"],
                           "page_in_original_corpus": c["doc_id"] in o_pages}
                          for c in cites if c["kind"] == "answer"],
            "accepted_follows": [{"doc_id": a["doc_id"], "edge_class": a["edge_class"], "new_page_incident": a["new_page_incident"]}
                                 for a in follows if a["status"] == "accepted"],
            "element_reference_pages": pages}


def load_s24(answer_ids):
    want = set(answer_ids)
    out = {"traces": {}, "citations": defaultdict(list), "actions": defaultdict(list)}
    for r in jsonl_rows(S24_OUT / "trace_provenance.traces.jsonl"):
        if r["cell_id"] in want:
            out["traces"][r["cell_id"]] = r
    for name, key in (("trace_provenance.citations.jsonl", "citations"), ("trace_provenance.actions.jsonl", "actions")):
        for r in jsonl_rows(S24_OUT / name):
            if r["cell_id"] in want:
                out[key][r["cell_id"]].append(r)
    return out


def cards(key, rows, pairs):
    idx = task_index()
    frozen = {t["task_id"]: t for t in load_tasks()}
    by = {(r["answer_id"], r["element_id"]): r for r in rows}
    pair_of = {(p["contrast"], p["task_id"], p["condition"], p["replicate"]): p for p in pairs}
    ids = [x[s]["answer_id"] for x in key["pairs"] for s in ("A", "B")]
    s24 = load_s24(ids)
    out = []
    for kp in key["pairs"]:
        p = pair_of[(kp["contrast"], kp["task_id"], kp["condition"], kp["replicate"])]
        task = frozen[kp["task_id"]]
        lt_side = "A" if kp["A"]["arm"] == "LT" else "B"
        differing = sorted(set(p["gains"]) | set(p["losses"]))
        elements = []
        for e in task["elements"]:
            eid = e["element_id"]
            side = {}
            for s in ("A", "B"):
                r = by[(kp[s]["answer_id"], eid)]
                side[s] = {"arm": kp[s]["arm"], "raw_verdict": r["raw_verdict"], "strict_verdict": r["strict_verdict"],
                           "outcome": r["outcome"], "answer_span": r["raw_answer_span"], "quote_location": r["quote_location"],
                           "relaxed_rule": r["relaxed_rule"], "reason": r["raw_reason"]}
            elements.append({"element_id": eid, "requirement": e["requirement"],
                             "judge_label": ("gain for LT" if eid in p["gains"] else "loss for LT" if eid in p["losses"]
                                             else "same label"), "sides": side})
        out.append({"pair_id": kp["pair_id"], "contrast": kp["contrast"], "stratum": kp["stratum"],
                    "task": idx[kp["task_id"]], "task_id": kp["task_id"], "condition": kp["condition"],
                    "replicate": kp["replicate"], "lt_side": lt_side,
                    "answers": {s: {"arm": kp[s]["arm"], "answer_id": kp[s]["answer_id"],
                                    "path": rel(OP_IN / f"answers/{kp[s]['answer_id']}.txt"),
                                    "sha256": sha(OP_IN / f"answers/{kp[s]['answer_id']}.txt")} for s in ("A", "B")},
                    "judge_labelled": {"gains": p["gains"], "losses": p["losses"]},
                    "status": "legacy model labels; no human verdict recorded",
                    "elements": elements,
                    "provenance": {s: provenance_card(kp[s]["answer_id"],
                                                      [task["element_pages"][e] for e in differing], s24)
                                   for s in ("A", "B")},
                    "provenance_note": ("analyst-side, from saved Sprint 24 tables; a cited or followed page shows "
                                        "co-occurrence, not reliance or support; text and link edits are coupled")})
    return out


def packet_markdown(packet):
    lines = ["# Tier-1 answer comparison: blinded review packet (HR-22)", "",
             f"Packet `{hashlib.sha256(json.dumps(packet, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()}`. "
             "Record answers in `response-blank.json` (copy it first). Labels: " + ", ".join(packet["labels"]) +
             "; overall: " + ", ".join(packet["overall_labels"]) + ".", "", packet["instructions"], "",
             "Reference pages are the original documentation pages the rubric cites, in `references/`. They are "
             "references, not an exhaustive answer key; use `unsure` when they do not settle a requirement.", ""]
    for p in packet["pairs"]:
        lines += [f"## {p['pair_id']}", "", f"**Task.** {p['question']}", "", "| Element | Requirement | Acceptance |",
                  "|---|---|---|"]
        lines += [f"| {e['element_id']} | {e['requirement']} | {e['acceptance']} |" for e in p["elements"]]
        lines += ["", "References: " + ", ".join(f"`references/{r['label'].rsplit(' ', 1)[-1]}.md`" for r in p["references"]), ""]
        for s in ("A", "B"):
            lines += [f"### Answer {s}", "", p["answers"][s].strip(), ""]
    return "\n".join(lines) + "\n"


def compute():
    receipt, image, manifest = check_run()
    T = {name: jsonl_rows(OP_OUT / name) for name in OUTPUT_TABLES}
    s = read_json(OP_OUT / "answer_element.summary.json")
    s20 = read_json(S20)["tier1"]["tables"]
    reproduction, mismatches = {}, []
    for m, res in s["historical"].items():
        reproduction[m] = {}
        for label, r in res.items():
            h = s20[m][label.replace("-", " - ")]
            d = {k: r[k] - h[k] for k in ("estimate", "mean_a", "mean_b")}
            ok = all(abs(x) < 1e-12 for x in d.values()) and r["n"] == h["n"]
            mismatches += [] if ok else [f"{m}:{label}"]
            reproduction[m][label] = {"estimate": r["estimate"], "sprint20_estimate": h["estimate"],
                                      "max_abs_difference": max(abs(x) for x in d.values()), "n": r["n"],
                                      "sprint20_ci95": h["ci95"], "match": ok}
    elements, task_rows = element_records(T["answer_element.cells.jsonl"], T["answer_element.contrasts.jsonl"],
                                          T["answer_element.elements.jsonl"], T["answer_element.tasks.jsonl"])
    key = read_json(OP_OUT / "answer_element.review_key.json")
    packet = read_json(OP_OUT / "answer_element.review_packet.json")
    selection = read_json(OP_OUT / "answer_element.selection.json")
    card_list = cards(key, T["answer_element.rows.jsonl"], T["answer_element.pairs.jsonl"])
    outputs = {name: {"path": rel(OP_OUT / name), "sha256": sha(OP_OUT / name),
                      "rows": manifest["rows"].get(name)} for name in sorted(manifest["outputs"])}
    outputs["answer_element_manifest.json"] = {"path": rel(OP_OUT / "answer_element_manifest.json"),
                                               "sha256": sha(OP_OUT / "answer_element_manifest.json"), "rows": None}
    outputs["operator_receipt.v1.json"] = {"path": rel(OP_OUT / "operator_receipt.v1.json"),
                                           "sha256": sha(OP_OUT / "operator_receipt.v1.json"), "rows": None}
    lt_cited_new = sorted({(c["pair_id"], s) for c in card_list for s in ("A", "B")
                           if any(not x["page_in_original_corpus"] for x in c["provenance"][s]["citations"])})
    contrasts = contrast_summaries(s["summary"], T["answer_element.contrasts.jsonl"], T["answer_element.elements.jsonl"],
                                   T["answer_element.tasks.jsonl"], T["answer_element.pairs.jsonl"], s["breakdowns"])
    result = {
        "schema_version": "docspectra.icsc.tier1_completeness_25.v1", "created": DATE, "model_calls": 0,
        "network_calls": 0,
        "scope": ("retrospective descriptive decomposition of saved tier-1 answers and saved judgments (Sprint 19); "
                  "element outcomes are legacy local-judge verdicts under quote_match_relaxed_v1; not independent "
                  "validation, not pre-registered, aggregates known beforehand"),
        "plan": pin(PLAN), "amendments": [pin(AMENDMENT)], "inventory": pin(INVENTORY),
        "code": {"driver": pin(Path(__file__)), "ra_commit": image["source_commit"], "ra_tree": image["source_tree"],
                 "ra_module_sha256": receipt["metric_code_sha256"], "image": image["image"],
                 "image_receipt": pin(IMAGE_RECEIPT)},
        "operator": {"receipt_sha256": receipt["receipt_sha256"], "config_sha256": receipt["config_sha256"],
                     "request_sha256": receipt["request_sha256"], "isolation": receipt["isolation"],
                     "input_root": rel(OP_IN), "outputs": outputs},
        "preservation": preservation(),
        "reproduction": {"matches": not mismatches, "mismatches": mismatches, "by_measure": reproduction,
                         "intervals": "Sprint 20 cluster-bootstrap intervals (10,000 replicates, seed 20261024), linked "
                                      "unchanged from wc-analysis-20 tier1.tables; none recomputed"},
        "cross_checks": {"per_answer_scores_vs_context_rubric_quote_match": cross_check_rules(T["answer_element.rows.jsonl"]),
                         "element_contributions_sum_to_relaxed_estimate": {
                             label: abs(c["sum_of_element_contributions"] - reproduction["rubric_relaxed"][label]["estimate"]) < 1e-12
                             for label, c in contrasts.items()}},
        "coverage": {k: s["summary"][k] for k in ("answers", "element_rows", "answers_by_arm_status",
                                                  "outcomes_by_arm", "raw_satisfied_by_rule", "issues")},
        "contrasts": contrasts,
        "review_packet": {"id": "HR-22", "status": "optional, ready", "human_review": "pending_optional",
                          "packet_sha256": selection["packet_sha256"], "pairs": len(selection["pairs"]),
                          "receipt": rel(REVIEW_RECEIPT),
                          "answer_cites_page_absent_from_original_corpus": [f"{p}{s}" for p, s in lt_cited_new]},
        "not_established": NOT_ESTABLISHED,
    }
    elements_ev = {"schema_version": "docspectra.icsc.tier1_completeness_25.elements.v1", "created": DATE,
                   "note": ("judge-labelled differences: rates are counted relaxed-eligible verdicts over observed "
                            "grades across two answer replicates per condition; scheduled denominators are kept; "
                            "rows with raw verdicts, spans, reasons and quote locations are in the artifact table "
                            "answer_element.rows.jsonl"),
                   "rows_table": outputs["answer_element.rows.jsonl"], "elements": elements, "tasks": task_rows}
    cards_ev = {"schema_version": "docspectra.icsc.tier1_completeness_25.cards.v1", "created": DATE,
                "note": ("analyst-side cards for the selected packet pairs only; never shown to a reviewer before "
                         "verdicts are recorded; labels are legacy model verdicts"), "cards": card_list}
    review_ev = {"schema_version": "docspectra.icsc.tier1_completeness_25.review_packet.v1", "created": DATE,
                 "id": "HR-22", "status": "optional, ready", "human_review": "pending_optional",
                 "selection": selection,
                 "files": {"packet_json": rel(REVIEW_DIR / "packet.json"), "packet_md": rel(REVIEW_DIR / "packet.md"),
                           "response_blank": rel(REVIEW_DIR / "response-blank.json"),
                           "references": rel(REVIEW_DIR / "references"),
                           "key": rel(REVIEW_DIR / "host-private/key.json")},
                 "hashes": {"packet": sha(OP_OUT / "answer_element.review_packet.json"),
                            "response_blank": sha(OP_OUT / "answer_element.review_response_blank.json"),
                            "key": sha(OP_OUT / "answer_element.review_key.json")},
                 "blinding": ("hidden: arms, opaque variant ids, scores, verdicts, reasons, strata, file names, "
                              "conditions, replicate numbers; disclosed risks: answer content and wording, cited page "
                              "names (some LT answers cite pages the edit created), author familiarity; the author "
                              "is not an independent reviewer"),
                 "rules": ("labels never prefilled from model output; a model never acts as the reviewer; reviewer "
                           "identity/role, date, packet hash, reviewed denominator and unblinding recorded before "
                           "the key is joined; partial credit stays qualitative"),
                 "interpretation": ("retrospective, outcome-stratified: illustrates differences and checks "
                                    "judgments; cannot estimate prevalence or population improvement")}
    review_files = {"packet.json": json.dumps(packet, indent=1, ensure_ascii=False) + "\n",
                    "packet.md": packet_markdown(packet),
                    "response-blank.json": json.dumps(read_json(OP_OUT / "answer_element.review_response_blank.json"),
                                                      indent=1, ensure_ascii=False) + "\n"}
    return result, elements_ev, cards_ev, review_ev, review_files, packet, key


def fmt(x, d=3):
    return "—" if x is None else f"{x:+.{d}f}"


def tables_md(result, elements_ev):
    r = result
    lines = [f"# Sprint 25 tier-1 completeness tables (generated)", "",
             f"Generated by `scripts/tier1_completeness_25.py analyse` from evidence `{RESULT.name}`. Judge-labelled "
             "differences from saved legacy verdicts; descriptive; no new intervals.", "",
             "## Reproduction of Sprint 20 (tier 1)", "", "| Measure | Contrast | Sprint 25 | Sprint 20 | Sprint 20 95% CI | Match |",
             "|---|---|---:|---:|---|---|"]
    for m, res in r["reproduction"]["by_measure"].items():
        for label, x in res.items():
            ci = x["sprint20_ci95"]
            lines.append(f"| {m} | {label} | {fmt(x['estimate'], 4)} | {fmt(x['sprint20_estimate'], 4)} | "
                         f"[{ci[0]:+.3f}, {ci[1]:+.3f}] | {'yes' if x['match'] else 'NO'} |")
    lines += ["", "## Decomposition by contrast (relaxed rubric, 73 elements x 4 conditions = 292 cells)", "",
              "| | LT-O | LT-R0 | R0-O | S-O |", "|---|---:|---:|---:|---:|"]
    C = r["contrasts"]
    labels = ["LT-O", "LT-R0", "R0-O", "S-O"]
    rows = [("Cells favourable / unfavourable / unchanged", lambda c: f"{c['cell_directions']['favourable']} / {c['cell_directions']['unfavourable']} / {c['cell_directions']['unchanged']}"),
            ("Cells persisting + / − (both numbered pairs)", lambda c: f"{c['cell_persistence_signed'].get('persists+', 0)} / {c['cell_persistence_signed'].get('persists-', 0)}"),
            ("Cells one-sided + / −", lambda c: f"{c['cell_persistence_signed'].get('one_sided+', 0)} / {c['cell_persistence_signed'].get('one_sided-', 0)}"),
            ("Cells reversing / incomplete", lambda c: f"{c['cell_persistence_signed'].get('reverses', 0)} / {c['cell_persistence_signed'].get('incomplete', 0)}"),
            ("Shared omission / shared satisfied cells", lambda c: f"{c['cell_shared'].get('shared_omission', 0)} / {c['cell_shared'].get('shared_satisfied', 0)}"),
            ("Elements favourable / unfavourable / unchanged (mean over conditions)", lambda c: " / ".join(str(c['elements_by_mean_difference'][k]) for k in ('favourable', 'unfavourable', 'unchanged'))),
            ("Elements gain-only (≥2 pairs) / loss-only (≥2) / both ways", lambda c: f"{c['elements_by_replicate_pairs']['gain_only_at_least_2']} / {c['elements_by_replicate_pairs']['loss_only_at_least_2']} / {c['elements_by_replicate_pairs']['both_gains_and_losses']}"),
            ("Element changes over numbered pairs: gains / losses", lambda c: f"{c['replicate_pair_element_changes']['gains']} / {c['replicate_pair_element_changes']['losses']}"),
            ("Tasks favourable / unfavourable / zero", lambda c: " / ".join(str(c['tasks_by_mean_difference'][k]) for k in ('favourable', 'unfavourable', 'zero'))),
            ("Tasks with elements moving both ways", lambda c: str(c['tasks_with_elements_both_ways'])),
            ("Answer pairs: net gain / net loss / tradeoff / agreement", lambda c: " / ".join(str(c['replicate_pair_strata'].get(k, 0)) for k in ('net_gain', 'net_loss', 'tradeoff', 'label_agreement'))),
            ("Overall relaxed difference (historical order)", lambda c: fmt(c['sum_of_element_contributions'], 4)),
            ("Overall over complete cells only", lambda c: fmt(c['complete_cells_overall'], 4))]
    for name, f in rows:
        lines.append(f"| {name} | " + " | ".join(f(C[l]) for l in labels) + " |")
    lines += ["", "## By condition and numbered replicate (descriptive; not a selection)", "",
              "| Contrast | s1-no-follow | s1-follow | s6-no-follow | s6-follow | r1 | r2 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for l in labels:
        c = C[l]
        lines.append(f"| {l} | " + " | ".join(fmt(c['by_condition'][k]) for k in CONDITIONS) + " | "
                     + " | ".join(fmt(c['by_replicate'][k]['mean_difference']) for k in ('1', '2')) + " |")
    lines += ["", "## Per task (relaxed, mean over elements; elements moving + / −)", "",
              "| Task | LT-O | LT-R0 | R0-O | S-O |", "|---:|---:|---:|---:|---:|"]
    by = defaultdict(dict)
    for t in elements_ev["tasks"]:
        by[t["task"]][t["contrast"]] = t
    for task in sorted(by):
        lines.append(f"| {task} | " + " | ".join(
            f"{fmt(by[task][l]['mean_difference'])} ({by[task][l]['elements_favourable']}+/{by[task][l]['elements_unfavourable']}−)" for l in labels) + " |")
    lines += ["", "## All 73 elements (mean difference over conditions; numbered-pair gains/losses)", "",
              "| Task | El. | Requirement | LT rate | O | R0 | S | LT-O | LT-R0 | R0-O | S-O |",
              "|---:|---|---|---:|---:|---:|---:|---|---|---|---|"]
    for e in elements_ev["elements"]:
        rate = lambda a: f"{e['arms'][a]['counted']}/{e['arms'][a]['observed']}"
        cell = lambda l: f"{fmt(e['contrasts'][l]['mean_difference'], 2)} ({e['contrasts'][l]['replicate_pairs']['gain']}/{e['contrasts'][l]['replicate_pairs']['loss']})"
        req = e["requirement"].replace("|", "\\|")
        lines.append(f"| {e['task']} | {e['element_id']} | {req} | {rate('LT')} | {rate('O')} | {rate('R0')} | {rate('S')} | "
                     + " | ".join(cell(l) for l in labels) + " |")
    return "\n".join(lines) + "\n"


def write_outputs(result, elements_ev, cards_ev, review_ev, review_files, packet, key, check=False):
    files = {RESULT: canonical(result), ELEMENTS: canonical(elements_ev), CARDS: canonical(cards_ev),
             REVIEW_RECEIPT: canonical(review_ev), TABLES_MD: tables_md(result, elements_ev)}
    files.update({REVIEW_DIR / k: v for k, v in review_files.items()})
    if check:
        return {rel(p): p.exists() and p.read_text(encoding="utf-8") == v for p, v in files.items()}
    for p, v in files.items():
        write_new(p, v)
    (REVIEW_DIR / "host-private").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(OP_OUT / "answer_element.review_key.json", REVIEW_DIR / "host-private/key.json")
    (REVIEW_DIR / "references").mkdir(exist_ok=True)
    for p in packet["pairs"]:
        for r in p["references"]:
            pid = r["label"].rsplit(" ", 1)[-1]
            shutil.copyfile(O_CORPUS / f"{pid}.md", REVIEW_DIR / "references" / f"{pid}.md")
    return {rel(p): sha(p) for p in files}


def analyse(_args):
    out = compute()
    if not out[0]["reproduction"]["matches"] or out[0]["preservation"]["changed"]:
        raise SystemExit("reproduction or preservation failed: " + json.dumps(
            {"mismatches": out[0]["reproduction"]["mismatches"], "changed": out[0]["preservation"]["changed"][:5]}))
    for path, digest in write_outputs(*out).items():
        print(path, digest)


def verify(_args):
    """Rebuild: the operator input from the legacy records, the RA tables on the host from that input, and every
    evidence file from the operator output; compare bytes."""
    rebuilt = build_config()
    inputs = {name: (OP_IN / name).read_bytes() == blob for name, blob in rebuilt.items()}
    sys.path.insert(0, str(RA))
    from retrieval_arena import answer_element_comparison as aec
    from retrieval_arena.candidate_retrieval import canonical as ra_canonical
    res = aec.run(read_json(OP_IN / "config.json"), OP_IN)
    tables = {}
    for name in OUTPUT_TABLES:
        key = name.split(".")[1]
        blob = b"".join(ra_canonical(r) + b"\n" for r in res[key])
        tables[name] = hashlib.sha256(blob).hexdigest() == sha(OP_OUT / name)
    evidence = write_outputs(*compute(), check=True)
    report = {"operator_input_files_equal": sum(inputs.values()), "operator_input_files": len(inputs),
              "host_tables_equal_container": tables, "evidence_bytes_equal": evidence,
              "preservation_changed": preservation()["changed"]}
    print(canonical(report))
    if not all(inputs.values()) or not all(tables.values()) or not all(evidence.values()) or report["preservation_changed"]:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("reconcile", "plan", "prepare", "run", "analyse", "verify"):
        sub.add_parser(name)
    args = parser.parse_args()
    if args.command == "reconcile":
        print(canonical(reconcile()))
        return
    globals()[args.command](args)


if __name__ == "__main__":
    main()

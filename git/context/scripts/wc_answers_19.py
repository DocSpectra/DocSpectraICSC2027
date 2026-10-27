"""Sprint 19: the answer manifest (authorization 3), the Luna answer runs and the cross-provider spend check.

Thin driver over the frozen 12b answer loop (structure_forcing_12.cell_command; author decision S19-1: the host loop,
unchanged, not the RA provider layer) on the four whole-corpus variants O, S, LT and R0 (docs/wc-answers-19.md).

  freeze      blind-export the four final trees (opaque ids, key in host-private), fix the questions, conditions,
              seeded schedule, ledgers and stops, and write the answer plan to evidence (refuses to overwrite)
  authorize   record the author's verbatim yes for point 3 against the plan hash (host-private/authorization.json)
  answer      run one ledger's cells (resumable; 429 and infrastructure retry rule of 12b)
  spend       sum the estimated charge of every Sprint 19 ledger (OpenAI and Anthropic) against the $100 stop

Tier 1: 15 tasks x s1/s6 x follow on/off x 2 replicates x 4 variants (480). Tier 2: 111 questions x s1 x follow
on/off x 4 variants (888). Spend is the upper estimate (all input at the cache-write price), as in 12b.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wc_refine_17 as w17  # noqa: E402

wr, rr, sf = w17.wr, w17.rr, w17.wr.sf
sfb = w17.sfb
sha, Refused = wr.sha, wr.Refused
ROOT, EVIDENCE = rr.ROOT, w17.EVIDENCE
DATE = "2026-10-22"
RUN_ROOT = w17.RUN_ROOT
OUT = RUN_ROOT / "answers-19"
PRIVATE = RUN_ROOT / "host-private"
KEY = PRIVATE / "answers-19-key.json"
EXPORT = RUN_ROOT / "answers-19-export"
PLAN = EVIDENCE / f"wc-answers-19-plan-v1-{DATE}.json"
TIER1 = EVIDENCE / "wc-tier1-tasks-v2-2026-10-18.json"
TIER2 = EVIDENCE / "wc-tier2-freeze-v1-2026-10-14.json"
DATASET = ROOT / "artifacts/docker-cikm-2026-05-14/dataset"
REVIEW_LABEL = "author-chat-2026-10-22-sprint-19-usd100"
SCHEDULE_SEED = 20261022
USD_STOP = 100.0                  # D11d, from October 14; $0 spent October 14-22 (Sprints 16-18 subscription only)
ANSWER_STOP = 60.0                # answers may use at most this much of the stop; the rest is held for scoring
CALL_WORST_USD = None             # set from the ledger price at freeze
PRICE = {"input_upper": 0.125 / 1_000_000, "output": 0.50 / 1_000_000}   # CostLedger CACHE_WRITE and OUTPUT
VARIANTS = ("O", "S", "LT", "R0")


def put_json(path, data):
    """Overwrite a JSON record (summaries and the authorization file; evidence uses rr.write_new)."""
    Path(path).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def trees():
    records = w17.wf.check_lock(w17.RECORDS)
    out = {"O": RUN_ROOT / "O-r01/editor/source", "S": RUN_ROOT / "S-r01/editor/source"}
    for arm in wr.EDIT_ARMS:
        path = Path(records["arms"][arm]["final_tree_path"])
        if wr.tree_id(path) != records["arms"][arm]["final_tree_id"]:
            raise Refused(f"final-tree-differs-from-records:{arm}")
        out[arm] = path
    return out


def tier1_questions():
    tasks = rr.read_json(TIER1)["tasks"]
    return [{"question_id": t["task_id"], "question": t["question"]} for t in tasks]


def tier2_questions():
    ids = rr.read_json(TIER2)["question_ids"]
    text = {}
    for line in (DATASET / "questions.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            q = json.loads(line)
            text[q["question_id"]] = q["question"]
    missing = [i for i in ids if i not in text]
    if missing:
        raise Refused(f"tier2-question-text-missing:{len(missing)}")
    return [{"question_id": i, "question": text[i]} for i in ids]


def ledgers(opaque):
    """One ledger per tier-1 replicate and one per tier-2 variant, each under CostLedger.LIMIT at its worst case."""
    out = [{"id": f"t1-r{r}", "tier": 1, "replicate": r, "variants": list(opaque)} for r in (1, 2)]
    out += [{"id": f"t2-{v}", "tier": 2, "replicate": 1, "variants": [v]} for v in opaque]
    return out


def schedule(qs, conds, variants, replicate, seed):
    cells = [{"variant": v, "question_id": q["question_id"], "condition": c["id"], "replicate": replicate}
             for v in variants for q in qs for c in conds]
    random.Random(seed).shuffle(cells)
    return cells


def freeze(_args):
    if PLAN.exists() or KEY.exists() or EXPORT.exists():
        raise Refused("answer-plan-already-frozen")
    a = sf.answering_block()
    a["review_label"] = REVIEW_LABEL
    if sha(ROOT / a["agent_loop"]) != a["agent_loop_sha256"]:
        raise Refused("agent-loop-drift")
    frozen_12b = rr.read_json(EVIDENCE / "structure-forcing-12-plan-v3-2026-10-09.json")["answers"]["answering"]
    drift = {k: (frozen_12b.get(k), a.get(k)) for k in ("agent_loop_sha256", "agent_model_provider_sha256",
                                                        "ra_model_turn_sha256", "prompt_sha256", "budgets",
                                                        "conditions", "max_input_tokens", "max_output_tokens", "model")
             if frozen_12b.get(k) != a.get(k)}
    if drift:
        raise Refused("answering-differs-from-12b:" + ",".join(drift))
    import secrets
    from types import SimpleNamespace
    t = trees()
    vm_path = rr.CONTEXT / "evidence/docker-corpus-variant-input-v6-docs-scope-2026-10-07.json"
    PRIVATE.mkdir(parents=True, exist_ok=True)
    key = {"schema_version": "docspectra.icsc.wc_answers_19.v1.key", "variants": {}}
    opaque = {}
    names = list(VARIANTS)
    secrets.SystemRandom().shuffle(names)
    for name in names:
        oid = "v-" + secrets.token_hex(6)
        while oid in key["variants"]:
            oid = "v-" + secrets.token_hex(6)
        target = EXPORT / "variants" / oid
        rr.quiet(wr.dcv.export_agent, SimpleNamespace(manifest=vm_path, candidate=t[name], dest=target))
        receipt = target / "export-receipt.json"
        (PRIVATE / "answers-19-export-receipts").mkdir(exist_ok=True)
        receipt.replace(PRIVATE / "answers-19-export-receipts" / f"{oid}.json")
        key["variants"][oid] = {"variant": name, "tree": str(t[name]), "tree_id": wr.tree_id(t[name]),
                                "corpus_tree_sha256": wr.dcv.tree_hash(wr.dcv.inventory(target / "corpus")),
                                "graph_file_sha256": wr.dcv.file_hash(target / "graph_edges.csv")}
        opaque[oid] = key["variants"][oid]["corpus_tree_sha256"]
    rr.write_new(KEY, key)
    conds = a["conditions"]
    q1, q2 = tier1_questions(), tier2_questions()
    s1 = [c for c in conds if c["searches"] == 1]
    calls = a["budgets"]["model_calls"]
    per_call = a["max_input_tokens"] * PRICE["input_upper"] + a["max_output_tokens"] * PRICE["output"]
    rows = []
    for i, led in enumerate(ledgers(sorted(opaque))):
        if led["tier"] == 1:
            cells = schedule(q1, conds, led["variants"], led["replicate"], SCHEDULE_SEED + i)
        else:
            cells = schedule(q2, s1, led["variants"], 1, SCHEDULE_SEED + i)
        worst = round(len(cells) * calls * per_call, 2)
        stop = min(40.0, round(worst + 0.5, 0))
        if worst > stop:
            raise Refused(f"ledger-worst-case-over-limit:{led['id']}")
        rows.append(dict(led, cells=cells, worst_case_usd=worst, ledger_stop_usd=stop))
    est = {"t1": 0.0189, "t2": 0.0172}
    n1 = sum(len(r["cells"]) for r in rows if r["tier"] == 1)
    n2 = sum(len(r["cells"]) for r in rows if r["tier"] == 2)
    plan = {
        "schema_version": "docspectra.icsc.wc_answers_19.v1.plan", "created": DATE,
        "record": "docs/wc-answers-19.md", "prompt": "prompts/19-wc-answers-scoring.md",
        "author_decisions": {"S19-1": "frozen 12b host loop, unchanged", "S19-2": "pool completion as O (generator top 20)"},
        "preregistration_sha256": rr.read_json(TIER2)["preregistration_sha256"],
        "records_sha256": sha(w17.RECORDS),
        "variants": {oid: {"corpus_tree_sha256": h} for oid, h in sorted(opaque.items())},
        "variant_key": {"path": str(KEY.relative_to(ROOT)), "sha256": sha(KEY), "blind": True},
        "answering": a,
        "tiers": {"1": {"source": str(TIER1.relative_to(ROOT)), "source_sha256": sha(TIER1), "questions": len(q1),
                        "conditions": [c["id"] for c in conds], "replicates": 2, "answers": n1},
                  "2": {"source": str(TIER2.relative_to(ROOT)), "source_sha256": sha(TIER2), "questions": len(q2),
                        "question_ids_sha256": rr.read_json(TIER2)["question_ids_sha256"],
                        "conditions": [c["id"] for c in s1], "replicates": 1, "answers": n2,
                        "label": "LLM-verified support (Claude Haiku 5.5)"}},
        "questions": {"1": q1, "2": q2},
        "ledgers": rows,
        "spend": {"usd_stop": USD_STOP, "answer_stop_usd": ANSWER_STOP,
                  "basis": "upper estimate: reported input x cache-write price + output x output price; "
                           "failed_possible_billable reservations counted in full",
                  "per_call_worst_usd": round(per_call, 6),
                  "estimate_upper_usd": {"answers_tier1": round(n1 * est["t1"], 2),
                                         "answers_tier2": round(n2 * est["t2"], 2),
                                         "luna_scorer_tier1": round(n1 * 0.0012, 2),
                                         "ragas_tier2": round(n2 * 0.0075, 2),
                                         "pool_completion": "3.0-7.5"}},
        "parallelism": "at most 2 ledgers at a time (12b ran its 2 replicates in parallel)",
    }
    rr.write_new(PLAN, plan)
    (PLAN.with_suffix(".sha256")).write_text(sha(PLAN) + "\n", encoding="ascii")
    print(json.dumps({"plan": str(PLAN.relative_to(ROOT)), "sha256": sha(PLAN), "answers": n1 + n2,
                      "ledgers": {r["id"]: (len(r["cells"]), r["ledger_stop_usd"]) for r in rows},
                      "estimate": plan["spend"]["estimate_upper_usd"]}, indent=1))


def load_plan():
    if not PLAN.exists():
        raise Refused("answer-plan-not-frozen")
    if (PLAN.with_suffix(".sha256")).read_text(encoding="ascii").strip() != sha(PLAN):
        raise Refused("answer-plan-hash-mismatch")
    return rr.read_json(PLAN)


def authorize(args):
    load_plan()
    path = PRIVATE / "authorization.json"
    data = rr.read_json(path) if path.exists() else {"entries": []}
    if any(e.get("point") == 3 and e.get("author_yes") for e in data["entries"]):
        raise Refused("point-3-already-recorded")
    data["entries"].append({"author_yes": True, "point": 3, "plan_sha256": sha(PLAN), "quote": args.quote,
                            "scope": args.scope, "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    put_json(path, data)
    print(json.dumps(data["entries"][-1], indent=1))


# ------------------------------------------------------------------ spend

def ledger_files():
    """Answer ledgers, scorer and RA ledgers under scoring-19, and the RA verifier ledgers under the pool folders."""
    return sorted(OUT.glob("cost-ledger-*.jsonl")) + sorted((RUN_ROOT / "scoring-19").rglob("cost-ledger*.jsonl")) +         sorted((RUN_ROOT / "answers-19-pool").rglob("cost-ledger*.jsonl"))


def ledger_charge(path):
    usd, reserved = 0.0, {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("event") == "reported_usage":
            u = e["usage"]
            # agent ledger rows use input/output; the Luna scorer records the raw input_tokens/output_tokens
            inp = u["input"] if "input" in u else u.get("input_tokens")
            out = u["output"] if "output" in u else u.get("output_tokens")
            usd += (inp or 0) * PRICE["input_upper"] + (out or 0) * PRICE["output"]
        elif e.get("event") == "failed_possible_billable":
            usd += e.get("projected_usd") or 0.0
        elif e.get("event") == "reserved" and "reservation" in e:     # RA provider layer
            reserved[e["reservation"]] = e.get("projected_usd") or 0.0
        elif e.get("event") == "failed" and "reservation" in e:       # RA failure: its reservation in full (S19-3)
            usd += reserved.get(e["reservation"], 0.0)
        elif "cost_usd" in e:                       # RA provider-layer rows (Anthropic)
            usd += e.get("cost_usd") or 0.0
    return usd


def spend_total():
    per = {p.relative_to(RUN_ROOT).as_posix(): round(ledger_charge(p), 4) for p in ledger_files()}
    return sum(per.values()), per


def spend(_args):
    total, per = spend_total()
    print(json.dumps({"total_usd_upper": round(total, 4), "stop_usd": USD_STOP, "ledgers": per}, indent=1))


# ------------------------------------------------------------------ answers

def provider_env(name):
    """The child process environment with `name` taken from the Windows user-level setting when it is set there
    (keys replaced after this process started); the value is passed through, never logged."""
    import os
    env = dict(os.environ)
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                env[name] = winreg.QueryValueEx(k, name)[0]
        except OSError:
            pass
    if not env.get(name):
        raise Refused(f"{name}-not-set")
    return env


def cell_stem(tier, variant, question_id, condition, replicate):
    """Tier 1 keeps the 12b stem (12-character task hashes, unique). Tier-2 ids all begin `docker_docs_`, so the 12b
    truncation would give every question the same name; tier 2 uses the full id."""
    if str(tier) == "1":
        return sf.cell_stem(variant, question_id, condition, replicate)
    return f"{variant}-{question_id}-{condition}-r{replicate}"


def answer(args):
    import subprocess
    import os
    import run_agent_search_factorial_v1 as panel
    plan = load_plan()
    auth = wr.authorized(RUN_ROOT, 3)
    if auth.get("plan_sha256") != sha(PLAN):
        raise Refused("authorization-3-is-for-another-plan")
    a = plan["answering"]
    if sha(ROOT / a["agent_loop"]) != a["agent_loop_sha256"] or \
            sha(rr.RA / "retrieval_arena/model_turn.py") != a["ra_model_turn_sha256"] or \
            sha(a["prompt_path"]) != a["prompt_sha256"] or \
            sha(rr.CONTEXT / "scripts/agent_model_provider.py") != a["agent_model_provider_sha256"]:
        raise Refused("answer-runtime-drift")
    child_env = provider_env("OPENAI_API_KEY")
    led = next((r for r in plan["ledgers"] if r["id"] == args.ledger), None)
    if led is None:
        raise Refused(f"unknown-ledger:{args.ledger}")
    qs = {q["question_id"]: q for t in ("1", "2") for q in plan["questions"][t]}
    conds = {c["id"]: c for c in a["conditions"]}
    OUT.mkdir(parents=True, exist_ok=True)
    commands = OUT / f"commands-{led['id']}.jsonl"
    prior = [json.loads(x) for x in commands.read_text(encoding="utf-8").splitlines()] if commands.exists() else []
    last_archive = {r["cell"]: i for i, r in enumerate(prior)
                    if r.get("event") in {"rate_limit_retry_archive", "infrastructure_attempt_archive"}}
    prior = [r for i, r in enumerate(prior) if i > last_archive.get(r.get("cell"), -1)]
    ledger = OUT / f"cost-ledger-{led['id']}.jsonl"
    calls = a["budgets"]["model_calls"]
    rows = led["cells"][: args.limit] if args.limit else led["cells"]
    for index, cell in enumerate(rows):
        q, c = qs[cell["question_id"]], conds[cell["condition"]]
        dest = OUT / f"t{led['tier']}" / (cell_stem(led["tier"], cell["variant"], q["question_id"], c["id"],
                                                    cell["replicate"]) + ".json")
        dest.parent.mkdir(exist_ok=True)
        if panel.completed_cell(prior, dest):
            continue
        total, _ = spend_total()
        answers_spent = sum(ledger_charge(p) for p in OUT.glob("cost-ledger-*.jsonl"))
        if total >= USD_STOP or answers_spent >= ANSWER_STOP:
            raise Refused(f"usd-stop: total {total:.2f}, answers {answers_spent:.2f}")
        cmd = sf.cell_command(a, dataset=EXPORT / "variants" / cell["variant"], question=q, dest=dest, ledger=ledger,
                              future_calls=(len(led["cells"]) - index - 1) * calls,
                              campaign_calls=len(led["cells"]) * calls, stop_usd=led["ledger_stop_usd"],
                              condition=c)
        cmd[cmd.index("--outbound-review-id") + 1] = REVIEW_LABEL
        attempt = 1
        while True:
            panel.log(commands, {"event": "command_start", "cell": dest.stem, "attempt": attempt,
                                 "plan_sha256": sha(PLAN), "ledger": led["id"], "schedule_index": index})
            result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=child_env,
                                    timeout=a["budgets"]["wall_seconds"] + 300)
            trace = json.loads(dest.read_text(encoding="utf-8")) if dest.exists() else None
            terminal = trace["terminal"] if trace else None
            panel.log(commands, {"event": "command_end", "cell": dest.stem, "attempt": attempt,
                                 "returncode": result.returncode, "terminal": terminal,
                                 "trace_sha256": sha(dest) if dest.exists() else None,
                                 "stderr": panel.redact(result.stderr[-1200:]),
                                 "stdout": panel.redact(result.stdout[-500:])})
            print(led["id"], index + 1, len(rows), dest.stem, terminal, "attempt", attempt, flush=True)
            if result.returncode == 0 and trace and panel.is_recorded_rate_limit(trace) and attempt < 3:
                panel.archive_rate_limited_attempt(dest.parent, dest, attempt, commands)
                attempt += 1
                time.sleep(60)
                continue
            if result.returncode or terminal in {"model_api_error", "model_incomplete", "model_resource_error"}:
                raise Refused("answer-transport-stopped:" + dest.stem)
            break


def archive_infra(args):
    """Exclude a model_api_error attempt under the author rule for infrastructure stops (as 11b); the next
    `answer` run on the same ledger reruns the cell."""
    import run_agent_search_factorial_v1 as panel
    tier = "1" if args.ledger.startswith("t1") else "2"
    dest = OUT / f"t{tier}" / (args.cell + ".json")
    commands = OUT / f"commands-{args.ledger}.jsonl"
    trace = json.loads(dest.read_text(encoding="utf-8"))
    if trace["terminal"] != "model_api_error":
        raise Refused("not-an-infrastructure-terminal:" + str(trace["terminal"]))
    folder = OUT / "infrastructure-attempts" / args.cell
    target = folder / str(1 + sum(1 for _ in folder.glob("*")) if folder.exists() else 1)
    target.mkdir(parents=True)
    trace_sha = sha(dest)
    for source, name in ((dest, "trace.json"), (dest.with_suffix(".events.jsonl"), "events.jsonl"),
                         (dest.parent / (args.cell + "-provider"), "provider")):
        if source.exists():
            source.rename(target / name)
    panel.log(commands, {"event": "infrastructure_attempt_archive", "cell": args.cell, "reason": args.reason,
                         "authority": "author rule for infrastructure stops (excluded; fresh replicate may run)",
                         "trace_sha256": trace_sha, "archive": str(target.relative_to(OUT))})
    print(args.cell, "archived to", target.relative_to(OUT))


# ------------------------------------------------------------------ tier-1 scoring: Luna scorer v2 (k = 2)

SCORING = RUN_ROOT / "scoring-19"
SCORER = SCORING / "luna-t1"
SCORER_PASSES = {"A": SCHEDULE_SEED + 1, "B": SCHEDULE_SEED + 2}   # label seeds; notes order unpermuted, as 12b
SCORER_WORKER_STOP = 5.0          # per worker ledger; upper estimate for all of tier 1 is $0.58


def tier1_answers():
    answers, forbidden = [], set(OUT.parts[-1:])
    for path in sorted((OUT / "t1").glob("*.json")):
        trace = rr.read_json(path)
        forbidden.update({path.name, path.stem, "-".join(path.stem.split("-")[:2])})
        if trace["terminal"] == "answer":
            answers.append({"answer_id": path.name, "question_id": trace["question_id"],
                            "cell": path.stem.split("-", 3)[3], "text": trace["answer"]["text"]})
    return answers, forbidden


def score(args):
    """The 12b Luna scorer v2 unchanged (module, prompt and schema hashes checked against the 12b plan), two passes
    with different label seeds, two workers per pass; every valid tier-1 answer."""
    import os
    import answer_quality_scorer as aqs
    import answer_quality_scorer_luna as aql
    plan = load_plan()
    auth = wr.authorized(RUN_ROOT, 3)
    if auth.get("plan_sha256") != sha(PLAN):
        raise Refused("authorization-3-is-for-another-plan")
    frozen = rr.read_json(sf.PLAN)["answers"]["scorer"]
    if (sha(aqs.__file__), sha(aql.__file__), sha(aqs.PROMPT), sha(aqs.SCHEMA)) != \
            (frozen["base_module_sha256"], frozen["luna_module_sha256"], frozen["prompt_sha256"], frozen["schema_sha256"]):
        raise Refused("scorer-not-the-12b-instrument")
    if len(list((OUT / "t1").glob("*.json"))) != plan["tiers"]["1"]["answers"]:
        raise Refused("tier-1-answers-incomplete")
    total, _ = spend_total()
    if total >= USD_STOP:
        raise Refused(f"usd-stop: total {total:.2f}")
    os.environ["OPENAI_API_KEY"] = provider_env("OPENAI_API_KEY")["OPENAI_API_KEY"]   # this process only; not logged
    config = aql.LunaConfig(authorization=REVIEW_LABEL, limit_usd=SCORER_WORKER_STOP)
    root = SCORER / f"pass-{args.pass_}"
    if not (root / "packet-manifest.json").exists():
        if args.worker != 0:
            raise Refused("worker-0-writes-the-packets")
        answers, forbidden = tier1_answers()
        qs = {t["task_id"]: t for t in rr.read_json(TIER1)["tasks"]}
        packets, key = aqs.build_packets(answers, qs, SCORER_PASSES[args.pass_], None)
        aql.write_packets(root, packets, key, SCORER_PASSES[args.pass_], config, None, forbidden,
                          run_label=f"wc19-t1-{args.pass_}")
    transport = aql.LunaTransport(config, SCORER / f"cost-ledger-{args.pass_}-w{args.worker}.jsonl")
    manifest = rr.read_json(root / "packet-manifest.json")
    for retry in (False, True):
        for entry in manifest[args.worker::2]:
            if retry and (root / "calls" / entry["packet_id"] / "completion.json").is_file():
                continue
            print(json.dumps(aql.call(root, entry, transport, retry=retry)), flush=True)


JUDGE = SCORING / "rubric-t1"
JUDGE_PROMPT = rr.CONTEXT / "design/complex-task-answer-judge-prompt-v1.txt"


def rubric(_args):
    """The 12b qwen3:14b boolean rubric judge (think off; relaxed quote match applied at scoring), one packet per
    distinct (task, answer text), through the same local runner; no model spend."""
    import hashlib
    import subprocess
    frozen = rr.read_json(sf.PLAN)["answers"]["rubric"]
    if sha(JUDGE_PROMPT) != frozen["prompt_sha256"]:
        raise Refused("rubric-prompt-not-the-12b-prompt")
    packets = JUDGE / "packets"
    if not (JUDGE / "packet-manifest.json").exists():
        qs = {t["task_id"]: t for t in rr.read_json(TIER1)["tasks"]}
        packets.mkdir(parents=True)
        manifest, aliases = [], {}
        for path in sorted((OUT / "t1").glob("*.json")):
            trace = rr.read_json(path)
            if trace["terminal"] != "answer":
                continue
            text = trace["answer"]["text"]
            uid = hashlib.sha256(("wc19-judge|" + trace["question_id"] + "|" + text).encode()).hexdigest()
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
    subprocess.run([sys.executable, str(rr.CONTEXT / "scripts/run_complex_task_local_judge.py"), "--root", str(JUDGE)],
                   cwd=ROOT, check=True)


# ------------------------------------------------------------------ tier-2 RAGAS (Haiku 5.5, RA external_api)

RAGAS = SCORING / "ragas-t2"
RAGAS_RECEIPT = ".tmp/ragas-image-wc19.json"  # ragas:icsc-wc19 at RA 60af29d: the C3 image recipe and lock, rebuilt
                                              # because run-ragas.ps1 binds the receipt to the checkout HEAD
RAGAS_CAP_USD = 3.0                            # per variant (222 answers); upper estimate $1.67


def ragas(args):
    """C3's judge config v4 for the `real-tier1` set (Faithfulness, ContextRecall, FactualCorrectness; Haiku 5.5,
    effort medium) on one variant's 222 tier-2 answers, against the historical FAQ reference answers. Contexts are the
    lines the agent read or followed (15b `trace_contexts`). One uncached run per variant (D11c)."""
    import os
    import subprocess
    import time
    import wc_ragas_c3 as c3
    b15, rs = c3.b15, c3.rs
    plan = load_plan()
    auth = wr.authorized(RUN_ROOT, 3)
    if auth.get("plan_sha256") != sha(PLAN):
        raise Refused("authorization-3-is-for-another-plan")
    oid = args.variant
    if oid not in plan["variants"]:
        raise Refused(f"unknown-variant:{oid}")
    traces = sorted((OUT / "t2").glob(f"{oid}-*.json"))
    if len(traces) != 2 * plan["tiers"]["2"]["questions"]:
        raise Refused("tier-2-answers-incomplete")
    total, _ = spend_total()
    if total + RAGAS_CAP_USD > USD_STOP:
        raise Refused(f"usd-stop: total {total:.2f} + cap {RAGAS_CAP_USD}")
    folder, run, staged = RAGAS / oid / "inputs", RAGAS / oid / "run", RAGAS / oid / "staged"
    if run.exists() or staged.exists():
        raise Refused(f"refusing to reuse {run}")
    refs = b15.tier2_references()
    units = []
    for path in traces:
        trace = rr.read_json(path)
        qid = trace["question_id"]
        units.append({"qid": qid, "question": trace["question"], "reference": refs[qid],
                      "condition": "s1-follow" if trace["follow_links"] else "s1-no-follow", "run": f"wc19-{oid}",
                      "text": b15.answer_text(trace), "contexts": b15.trace_contexts(trace),
                      "source_sha": sha(path), "record_id": path.name})
    provenance = {"authoring_method": "historical Docker FAQ answers (docker-cikm-2026-05-14 answers.jsonl)",
                  "reviewer_id": "docker-cikm-2026-05-14", "frozen_at": "2026-05-14", "independent_of_predictions": True}
    batch, rubrics = b15.assemble(units, provenance)
    cfg = c3.config("real-tier1")
    cfg["spend_cap_usd"] = RAGAS_CAP_USD
    rs.validate_config(cfg)
    folder.mkdir(parents=True, exist_ok=True)
    for file, value in (("batch.json", batch), ("rubrics.json", rubrics), ("config.json", cfg)):
        rs.write_json(folder / file, value)
    os.environ["ANTHROPIC_API_KEY"] = provider_env("ANTHROPIC_API_KEY")["ANTHROPIC_API_KEY"]  # by name to RA; not logged
    run.mkdir(parents=True)
    (run / ".retrieval-arena-output-owner").touch()
    common = [str(b15.PWSH), "-NoProfile", "-File"]
    subprocess.run(common + [str(rr.RA / "scripts/prepare-ragas-input.ps1"), "-Command", "judge",
                             "-Batch", str(folder / "batch.json"), "-Rubrics", str(folder / "rubrics.json"),
                             "-Artifact", str(folder / "config.json"), "-InputRoot", str(staged), "-NoCache"],
                   cwd=rr.RA, check=True)
    started = time.time()
    done = subprocess.run(common + [str(rr.RA / "scripts/run-ragas.ps1"), "-Command", "judge", "-InputRoot", str(staged),
                                    "-OutputRoot", str(run), "-Receipt", RAGAS_RECEIPT,
                                    "-Network", "external_api", "-CredentialEnv", "ANTHROPIC_API_KEY"], cwd=rr.RA)
    put_json(RAGAS / oid / "wall.json", {"exit_code": done.returncode, "wall_seconds": time.time() - started,
                                         "units": len(units), "receipt": RAGAS_RECEIPT})
    print(oid, done.returncode)


# ------------------------------------------------------------------ tier-2 retrieval metrics (RA score_ranked)

RANKED = SCORING / "ranked-t2"
RANKED_IMAGE = "retrieval-arena/verification:icsc-c2"   # ir-measures 0.4.3, pytrec-eval-terrier 0.5.10 (its lock)
RANKED_CUTOFFS = (5, 10)          # Recall@5 from k = 5; nDCG@10 and MRR (RR@10) from k = 10


def common_questions(plan):
    """S19-4: the tier-2 questions every variant's completed qrels keep (at least one verified page), plan order."""
    kept = [set(rr.read_json(POOL / oid / "verify/qrels_manifest.json")["ordered_query_ids"]) for oid in plan["variants"]]
    return [q["question_id"] for q in plan["questions"]["2"] if all(q["question_id"] in k for k in kept)]


def ranked_policy(k):
    from retrieval_arena.support.pooling import sha256_json
    p = {"schema_version": "retrieval_arena.ranked_metric_policy.v2", "policy_id": f"wc19-t2-k{k}",
         "primary_metric": "evidence_recall", "secondary_metrics": ["complete_support", "ndcg", "reciprocal_rank"],
         "context_item_budget_k": k, "gain": "identity", "discount": "log2_rank_plus_one", "aggregation": "query_macro",
         "uncertainty_unit": "connected_support_group", "qrels_scope": "pooled", "unjudged_policy": "unavailable_ndcg",
         "tie_policy": "score_desc_target_id_asc", "duplicate_policy": "reject", "engine_name": "ir_measures",
         "engine_version": "0.4.3",
         "engine_artifact_sha256": "3d8b3b5283e4a77df85f9f6c8a1c2c99a7421b78fa014ef97f2ddab3037d3dcf",
         "provider_name": "pytrec_eval", "provider_version": "0.5.10", "parity_oracle_name": "trec_eval",
         "parity_oracle_version": "9.0.8", "parity_corpus_sha256": sha256_json("no-parity-run-wc19")}
    p["policy_sha256"] = sha256_json(p)
    return p


def ranked_predictions(path, qids, k):
    """Legacy retriever rows -> ranked_prediction.v2 in the retriever's own order (score = -rank, so the RA tie policy
    reproduces the order exactly); a repeated page keeps its first rank and is counted as a collapse."""
    from retrieval_arena.support.pooling import sha256_json
    src = sha(path)
    rows = {r["question_id"]: r for r in (json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x)}
    out = []
    for q in qids:
        seen, items, dupes = set(), [], []
        for c in sorted(rows[q]["retrieved_context"], key=lambda c: int(c["rank"])):
            if c["doc_id"] in seen:
                dupes.append(c["doc_id"])
                continue
            seen.add(c["doc_id"])
            rank = len(items) + 1
            items.append({"support_target_id": c["doc_id"], "rank": rank, "score": float(-rank),
                          "source_rank": int(c["rank"]), "source_item_id": c["doc_id"], "source_provenance_sha256": src})
        out.append({"schema_version": "retrieval_arena.ranked_prediction.v2", "query_id": q, "context_item_budget_k": k,
                    "max_rank": len(items), "retrieval_exhausted": len(items) < k, "items": items,
                    "duplicate_collapse_count": len(dupes), "duplicate_collapse_sha256": sha256_json(dupes) if dupes else None})
    return out


def rank(_args):
    """RA score_ranked (pinned engine, verification image, --network none, RA source mounted read-only) for the
    three fixed retrievers on every variant, against that variant's completed qrels on the common questions."""
    import subprocess
    sys.path.insert(0, str(rr.RA))
    from retrieval_arena.support.pooling import sha256_json
    from retrieval_arena.support.verification import ranked_dataset_fields
    plan = load_plan()
    qids = common_questions(plan)
    summary = {"questions": len(qids), "question_ids_sha256": sha256_json(qids), "image": RANKED_IMAGE, "runs": {}}
    for oid in plan["variants"]:
        manifest = rr.read_json(POOL / oid / "verify/qrels_manifest.json")
        root = RANKED / oid
        root.mkdir(parents=True, exist_ok=True)
        keep = set(qids)
        qrels = [r for r in (json.loads(x) for x in (POOL / oid / "verify/qrels.jsonl").read_text(encoding="utf-8").splitlines() if x)
                 if r["query_id"] in keep]
        _write_jsonl(root / "qrels.jsonl", qrels)
        fields = ranked_dataset_fields(dict(manifest, ordered_query_ids=qids, qrels_sha256=sha(root / "qrels.jsonl"),
                                            judgment_provenance_sha256=sha256_json([r["judgment_provenance_sha256"] for r in qrels])))
        dataset = {"schema_version": "retrieval_arena.ranked_dataset.v2", "dataset_id": f"wc19-t2-{oid}",
                   "corpus_id": manifest["corpus_id"], "query_set_id": f"wc19-t2-common-{len(qids)}",
                   "support_surface_id": f"wc19-{oid}-qrels-v{manifest['qrels_version']}",
                   "corpus_snapshot_id": plan["variants"][oid]["corpus_tree_sha256"], "graph_snapshot_id": "not-used",
                   "ranked_item_granularity": "support_target", **fields}
        dataset["dataset_manifest_sha256"] = sha256_json(dataset)
        put_json(root / "dataset.json", dataset)
        for k in RANKED_CUTOFFS:
            put_json(root / f"policy-k{k}.json", ranked_policy(k))
            for r in wr.RETRIEVERS:
                name = f"{r}-k{k}"
                _write_jsonl(root / f"{name}.predictions.jsonl",
                             ranked_predictions(RETRIEVAL / oid / r / "output/predictions.jsonl", qids, k))
                out = root / name
                out.mkdir(exist_ok=True)
                cmd = ["docker", "run", "--rm", "--network", "none", "--entrypoint", "python",
                       "-e", "PYTHONPATH=/workspace/source", "-v", f"{rr.RA}:/workspace/source:ro",
                       "-v", f"{root}:/in:ro", "-v", f"{out}:/out", RANKED_IMAGE, "-I", "-B", "-c",
                       "import sys; sys.path.insert(0, '/workspace/source'); from retrieval_arena.cli import main; "
                       f"sys.argv = ['ra', 'score-ranked', '--dataset', '/in/dataset.json', '--qrels', '/in/qrels.jsonl', "
                       f"'--predictions', '/in/{name}.predictions.jsonl', '--policy', '/in/policy-k{k}.json', "
                       f"'--out-dir', '/out']; sys.exit(main())"]
                done = subprocess.run(cmd, capture_output=True, text=True)
                if done.returncode:
                    raise Refused(f"score-ranked:{oid}/{name}:{done.stderr[-600:]}")
                agg = rr.read_json(out / "ranked_aggregate.v2.json")
                summary["runs"][f"{oid}/{name}"] = {m: agg[m] for m in ("query_count", "macro_evidence_recall", "macro_ndcg",
                                                                        "mrr", "available_counts", "unavailable_counts")}
                print(oid, name, json.dumps(summary["runs"][f"{oid}/{name}"]), flush=True)
    summary["ra_source_commit"] = subprocess.run(["git", "-C", str(rr.RA), "rev-parse", "HEAD"], capture_output=True,
                                                 text=True).stdout.strip()
    put_json(RANKED / "summary.json", summary)


# ------------------------------------------------------------------ tier-2 navigation metrics and the O1 sensitivity

NAV = SCORING / "navigation-t2"


def navigate(_args):
    """Sprint 15 navigation_metrics on every tier-2 trace. Direct judgment (primary, S19-4): the variant's own verified
    pages (qrels v3) on the common questions, identity equivalence. O1 sensitivity: C2's baseline gold on all 111
    questions with the credited equivalence (PROVENANCE_RULE, freeze row O1), for the traces and, with Sprint 15's
    ranked_scores, the fixed retrievers. The arm key is read only to build each variant's context; outputs carry
    opaque ids only."""
    from collections import defaultdict
    plan = load_plan()
    qids = common_questions(plan)
    common = set(qids)
    all_q = [q["question_id"] for q in plan["questions"]["2"]]
    key = rr.read_json(KEY)["variants"]
    vm = rr.read_json(wr.V6)
    gm = wr.dcv.pinned(vm)
    eff = w17.effective(w17.ledger_rows())
    tr = trees()
    c2pos = defaultdict(list)
    for line in (ROOT / "artifacts/c2-pooled-qrels/verify-full/qrels.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line) if line else None
        if r and r["relevance"] > 0:
            c2pos[r["query_id"]].append(r["support_target_id"])
    NAV.mkdir(parents=True, exist_ok=True)
    summary = {"common_questions": len(qids), "all_questions": len(all_q), "variants": {}}
    for oid in plan["variants"]:
        arm = key[oid]["variant"]
        vctx = wr.variant_context(gm, vm, tr[arm], ledger_rows=eff, arm=arm if arm in wr.EDIT_ARMS else None)
        pos = defaultdict(list)
        for line in (POOL / oid / "verify/qrels.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line) if line else None
            if r and r["relevance"] > 0:
                pos[r["query_id"]].append(r["support_target_id"])
        direct_ctx = dict(vctx, equivalence={d: [d] for ids in pos.values() for d in ids})
        rows = []
        for path in sorted((OUT / "t2").glob(f"{oid}-*.json")):
            trace = rr.read_json(path)
            q = trace["question_id"]
            rows.append({"trace": path.name, "question_id": q, "follow_links": trace["follow_links"],
                         "terminal": trace["terminal"], "in_common": q in common,
                         "direct": wr.navigation_metrics(trace, pos[q], direct_ctx) if q in common else None,
                         "o1": wr.navigation_metrics(trace, c2pos[q], vctx)})
        _write_jsonl(NAV / f"{oid}.traces.jsonl", rows)
        ret = []
        for r in wr.RETRIEVERS:
            preds = {x["question_id"]: x for x in (json.loads(l) for l in (RETRIEVAL / oid / r / "output/predictions.jsonl")
                                                   .read_text(encoding="utf-8").splitlines() if l)}
            for q in all_q:
                ranking = [c["doc_id"] for c in sorted(preds[q]["retrieved_context"], key=lambda c: int(c["rank"]))]
                ret.append({"retriever": r, "question_id": q, "in_common": q in common,
                            "o1": wr.ranked_scores(ranking, c2pos[q], vctx["equivalence"])})
        _write_jsonl(NAV / f"{oid}.retrievers-o1.jsonl", ret)
        summary["variants"][oid] = {
            "traces": len(rows), "direct_scored": sum(r["direct"] is not None for r in rows),
            "o1_scored": sum(r["o1"]["support_units"] > 0 for r in rows),
            "direct_support_read_or_cited": sum(bool(r["direct"] and r["direct"]["support_read_or_cited"]) for r in rows),
            "retriever_o1_rows": len(ret), "retriever_o1_scored": sum(x["o1"]["recall@5"] is not None for x in ret),
            "credited_pages_beyond_identity": sum(len(v) - (k in v) for k, v in vctx["equivalence"].items()),
            "editor_added_edges": len(vctx["added_edges"])}
        print(oid, json.dumps(summary["variants"][oid]), flush=True)
    put_json(NAV / "summary.json", summary)


RECORD = EVIDENCE / "wc-answers-19-run-v1-2026-10-24.json"


def record(_args):
    """Sprint 19 evidence: answer terminals and archived attempts, scorer/rubric/verifier/RAGAS validity, pool
    completion, metric coverage, cost per ledger and provider, and the hashes of every output Sprint 20 reads.
    Opaque variant ids only. Write-once."""
    from collections import Counter
    plan = load_plan()
    hashes = lambda folder, pattern: {p.relative_to(RUN_ROOT).as_posix(): sha(p) for p in sorted(folder.glob(pattern))}
    terminals = {t: dict(Counter(rr.read_json(p)["terminal"] for p in (OUT / f"t{t}").glob("*.json"))) for t in ("1", "2")}
    archived = Counter()
    for p in (OUT / "infrastructure-attempts").glob("*/*/trace.json"):
        archived[rr.read_json(p)["steps"][-1]["result"].get("error")] += 1
    scorer = {}
    for ps in SCORER_PASSES:
        root = SCORER / f"pass-{ps}"
        m = rr.read_json(root / "packet-manifest.json")
        done = [rr.read_json(root / "calls" / e["packet_id"] / "completion.json") for e in m
                if (root / "calls" / e["packet_id"] / "completion.json").is_file()]
        scorer[ps] = {"packets": len(m), "valid": sum(bool(c.get("valid")) for c in done),
                      "manifest_sha256": sha(root / "packet-manifest.json")}
    rub = [json.loads(l) for l in (SCORING / "run-rubric.log").read_text(encoding="utf-8").splitlines() if l.startswith("{")]
    pool, ragas_v = {}, {}
    for oid in plan["variants"]:
        m = rr.read_json(POOL / oid / "verify/qrels_manifest.json")
        receipt = rr.read_json(POOL / oid / "verify/receipt.json")
        pool[oid] = {"pool_pairs": sum(1 for _ in (POOL / oid / "pool-agents/pool.jsonl").open(encoding="utf-8")),
                     "run": receipt["result"]["run"], "qrels_version": m["qrels_version"],
                     "queries_kept": m["counts"]["queries_kept"], "qrels_positive": m["counts"]["qrels_positive"],
                     "decisions": m["counts"]["decisions"], "qrels_sha256": m["qrels_sha256"],
                     "queries_dropped_no_verified_page": m["counts"]["queries_dropped_no_verified_page"]}
        s = rr.read_json(RAGAS / oid / "run/scores.json")
        st = {k: Counter() for k in ("faithfulness", "context_recall", "factual_correctness")}
        for blk in s["summary"]:
            for k in st:
                st[k].update(blk["metrics"][k]["statuses"])
        ragas_v[oid] = {"units": len(s["records"]), "statuses": {k: dict(v) for k, v in st.items()},
                        "scores_sha256": sha(RAGAS / oid / "run/scores.json")}
    total, per = spend_total()
    provider = {"openai": sum(v for k, v in per.items() if k.startswith(("answers-19/", "scoring-19/luna"))),
                "anthropic": sum(v for k, v in per.items() if "/verify/" in k or "ragas-t2" in k)}
    out = {"schema_version": "docspectra.icsc.wc_answers_19.run.v1", "created": "2026-10-24",
           "plan_sha256": sha(PLAN), "authorization": {k: v for k, v in wr.authorized(RUN_ROOT, 3).items()
                                                        if k in ("point", "quote", "plan_sha256", "recorded_utc")},
           "run_record": "docs/wc-answers-19.md", "label_tier2": plan["tiers"]["2"]["label"],
           "answers": {"terminals": terminals, "archived_attempts": dict(archived),
                       "archive_rule": "author rule for infrastructure stops (11b/12b): excluded, rerun; disclosed"},
           "tier1": {"luna_scorer": scorer, "rubric": dict(Counter(r["status"] for r in rub)),
                     "rubric_note": "one local TimeoutError kept as a failure (12b precedent, runner without --retry)"},
           "pool_completion": pool,
           "s19_4_common_questions": common_questions(plan),
           "ragas_tier2": ragas_v,
           "ranked_tier2": rr.read_json(RANKED / "summary.json"), "navigation_tier2": rr.read_json(NAV / "summary.json"),
           "cost_upper_usd": {"total": round(total, 4), "by_provider": {k: round(v, 4) for k, v in provider.items()},
                              "by_ledger": per, "stop_usd": USD_STOP, "answer_stop_usd": ANSWER_STOP,
                              "basis": plan["spend"]["basis"]},
           "outputs_sha256": {**hashes(OUT / "t1", "*.json"), **hashes(OUT / "t2", "*.json"),
                              **hashes(NAV, "*.jsonl"), **hashes(RANKED, "*/*/ranked_item_scores.v2.jsonl")}}
    rr.write_new(RECORD, out)
    print(RECORD, sha(RECORD), round(total, 4))


def _write_jsonl(path, rows):
    with Path(path).open("w", encoding="utf-8", newline="\n") as s:
        for r in rows:
            s.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


# ------------------------------------------------------------------ fixed retrievers (no model calls, --network none)

RETRIEVAL = RUN_ROOT / "answers-19-retrievers"


def retrievers(_args):
    """The pinned Sprint 15 retrievers (wc_refinement_15.RETRIEVERS, k = 10) on every blind export, tier-2 questions."""
    plan = load_plan()
    problems = wr.check_retriever_pins()
    if problems:
        raise Refused("retriever-pins:" + ",".join(problems))
    qs = plan["questions"]["2"]
    summary_path = RETRIEVAL / "summary.json"
    summary = rr.read_json(summary_path) if summary_path.exists() else {
        "schema_version": "docspectra.icsc.wc_answers_19.v1.retrievers", "plan_sha256": sha(PLAN),
        "retrievers": {k: {x: v[x] for x in ("provider", "image_id", "runner_sha256", "config")}
                       for k, v in wr.RETRIEVERS.items()}, "runs": {}}
    for oid in sorted(plan["variants"]):
        for name in wr.RETRIEVERS:
            run_id = f"{oid}/{name}"
            if run_id in summary["runs"]:
                continue
            dest = RETRIEVAL / oid / name
            ranked, meta = wr.run_retriever(name, EXPORT / "variants" / oid, qs, dest)
            short = [q for q, r in ranked.items() if len(r) < 10]
            summary["runs"][run_id] = {"questions": len(ranked), "short_rankings": len(short),
                                       "seconds": meta["seconds"], "predictions_sha256": meta["predictions_sha256"]}
            put_json(summary_path, summary)
            print(run_id, summary["runs"][run_id], flush=True)
    print(json.dumps({"runs": len(summary["runs"])}, indent=1))


# ------------------------------------------------------------------ pool completion (S19-2: built as O's C2 pool)

POOL = RUN_ROOT / "answers-19-pool"


def pool_queries(plan):
    answers = {}
    for line in (DATASET / "answers.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            answers[r["question_id"]] = r["answer"]
    return [dict(q, answer=answers[q["question_id"]]) for q in plan["questions"]["2"]]


def pool(args):
    """RA `support pool` on one blind export: its three retriever runs (top 10), plus agent read/cited pages as a
    pseudo-run when --with-agents is given (after the answers), plus the generator top 20 by reference answer.
    Local embedding image, no model calls."""
    import c2_pooled_qrels as c2
    plan = load_plan()
    oid = args.variant
    if oid not in plan["variants"]:
        raise Refused(f"unknown-variant:{oid}")
    name = "pool-agents" if args.with_agents else "pool-runs"
    root = POOL / oid
    inp = c2.fresh(root / f"{name}-input")
    shutil.copytree(EXPORT / "variants" / oid / "corpus", inp / "corpus")
    c2.write_jsonl(inp / "queries.jsonl", pool_queries(plan))
    (inp / "runs").mkdir()
    config = rr.read_json(c2.CONFIGS / "pool_config.json")
    config["pool_id"] = f"wc19-{oid}-{name}"
    config["corpus_id"] = f"docker_wc19_{oid}_{plan['variants'][oid]['corpus_tree_sha256']}"
    config["runs"] = []
    for r in wr.RETRIEVERS:
        shutil.copyfile(RETRIEVAL / oid / r / "output/predictions.jsonl", inp / "runs" / f"{r}.jsonl")
        config["runs"].append({"run_id": f"wc19-{oid}-{r}", "path": f"{r}.jsonl", "format": "legacy_predictions"})
    if args.with_agents:
        rows = agent_pseudo_run(oid)
        c2.write_jsonl(inp / "runs" / "agents.jsonl", rows)
        config["runs"].append({"run_id": f"wc19-{oid}-agents", "path": "agents.jsonl", "format": "legacy_predictions"})
    (inp / "pool_config.json").write_text(json.dumps(config, indent=1), encoding="utf-8")
    a = {"config": "pool_config.json", "corpus": "corpus", "queries": "queries.jsonl", "run_root": "runs",
         "model_dir": "/models/embedding"}
    return c2.launch("pool", c2.EMBED_IMAGE, inp, c2.fresh(root / name), a, model_dir=c2.MODEL)


VERIFY_CAP_USD = 4.0              # per variant; measured worst case about $1.9 on an edited variant


def verify(args):
    """RA `support verify` on one variant's completed pool (runs + agents + generator), with the frozen C2 verifier
    config and C2's verify-full as the prior: only (query, page, page_sha256) triples C2 never judged are sent to
    Haiku. Output: a new qrels version per variant; C2 judgments are never changed."""
    import os
    import c2_pooled_qrels as c2
    plan = load_plan()
    auth = wr.authorized(RUN_ROOT, 3)
    if auth.get("plan_sha256") != sha(PLAN):
        raise Refused("authorization-3-is-for-another-plan")
    oid = args.variant
    if oid not in plan["variants"]:
        raise Refused(f"unknown-variant:{oid}")
    root = POOL / oid
    if not (root / "pool-agents" / "pool_manifest.json").exists():
        raise Refused("pool-with-agents-missing")
    total, _ = spend_total()
    if total + VERIFY_CAP_USD > USD_STOP:
        raise Refused(f"usd-stop: total {total:.2f} + cap {VERIFY_CAP_USD}")
    os.environ["ANTHROPIC_API_KEY"] = provider_env("ANTHROPIC_API_KEY")["ANTHROPIC_API_KEY"]  # by name to RA; not logged
    inp = c2.fresh(root / "verify-input")
    shutil.copytree(root / "pool-agents", inp / "pool", ignore=shutil.ignore_patterns("receipt.json"))
    c2.write_jsonl(inp / "queries.jsonl", pool_queries(plan))
    config = rr.read_json(c2.CONFIGS / "verifier_config.json")
    config["spend_cap_usd"] = VERIFY_CAP_USD
    (inp / "verifier_config.json").write_text(json.dumps(config, indent=1), encoding="utf-8")
    shutil.copytree(c2.OUT / "verify-full", inp / "prior",
                    ignore=shutil.ignore_patterns("receipt.json", "cost-ledger.jsonl", "verify_state.json"))
    a = {"config": "verifier_config.json", "pool": "pool", "queries": "queries.jsonl", "prior": "prior",
         "query_ids": None, "limit": None, "resume": None}
    return c2.launch("verify", c2.VERIFY_IMAGE, inp, c2.fresh(root / "verify"), a)


def agent_pseudo_run(oid):
    """Pages each tier-2 agent read or cited on this variant, as legacy_predictions rows (rank = first touch)."""
    pages = {}
    for path in sorted((OUT / "t2").glob(f"{oid}-*.json")):
        trace = rr.read_json(path)
        seen = pages.setdefault(trace["question_id"], [])
        opened = [(s.get("action") or {}).get("doc_id") for s in trace.get("steps", [])
                  if (s.get("action") or {}).get("action") in ("read", "follow")]   # as wc_refinement_15.annotate_trace
        for doc in [*opened, *wr.cited_ids(trace)]:
            if doc and doc not in seen:
                seen.append(doc)
    return [{"question_id": q, "retrieved_context": [{"doc_id": d, "rank": i + 1} for i, d in enumerate(docs)]}
            for q, docs in sorted(pages.items())]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="stage", required=True)
    sub.add_parser("freeze").set_defaults(fn=freeze)
    au = sub.add_parser("authorize")
    au.add_argument("--quote", required=True)
    au.add_argument("--scope", required=True)
    au.set_defaults(fn=authorize)
    an = sub.add_parser("answer")
    an.add_argument("--ledger", required=True)
    an.add_argument("--limit", type=int, default=None, help="run only the first N scheduled cells (smoke)")
    an.set_defaults(fn=answer)
    ai = sub.add_parser("archive-infra")
    ai.add_argument("--ledger", required=True)
    ai.add_argument("--cell", required=True)
    ai.add_argument("--reason", required=True)
    ai.set_defaults(fn=archive_infra)
    sc = sub.add_parser("score")
    sc.add_argument("--pass", dest="pass_", choices=sorted(SCORER_PASSES), required=True)
    sc.add_argument("--worker", type=int, choices=(0, 1), required=True)
    sc.set_defaults(fn=score)
    ve = sub.add_parser("verify")
    ve.add_argument("--variant", required=True)
    ve.set_defaults(fn=verify)
    rg = sub.add_parser("ragas")
    rg.add_argument("--variant", required=True)
    rg.set_defaults(fn=ragas)
    sub.add_parser("rubric").set_defaults(fn=rubric)
    sub.add_parser("rank").set_defaults(fn=rank)
    sub.add_parser("navigate").set_defaults(fn=navigate)
    sub.add_parser("record").set_defaults(fn=record)
    sub.add_parser("spend").set_defaults(fn=spend)
    sub.add_parser("retrievers").set_defaults(fn=retrievers)
    po = sub.add_parser("pool")
    po.add_argument("--variant", required=True)
    po.add_argument("--with-agents", action="store_true")
    po.set_defaults(fn=pool)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

"""D-P5 (author, October 24): whole-page re-judge of the undetermined tier-2 pool pages, then the rebuilt common set.

  python wc_rejudge_19.py plan                     freeze the plan: targets per variant, verifier config, cost bound,
                                                   the HR-20 agreement expectation (offline, no model calls)
  python wc_rejudge_19.py authorize --quote "..."  record the author's spend yes against the frozen plan
  python wc_rejudge_19.py escalate --variant <oid> [--limit N]   RA `support escalate` on one variant (Haiku)
  python wc_rejudge_19.py record                   outcomes, rebuilt common set, HR-20 agreement -> evidence

Every variant's `undetermined` decisions for the 111 tier-2 questions in its Sprint 19 qrels (answers-19-pool/<oid>/verify) are judged once more by
the frozen C2 verifier (same model, prompt and settings except the input cap) on the whole page. RA
`whole_page_escalation_v1` writes a new qrels version beside the old one (verify-escalated); nothing old is changed.
The common set is rebuilt by the S19-4 rule: questions with at least one verified page on all four variants.
Opaque variant ids only; nothing reads the arm key.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wc_answers_19 as a19  # noqa: E402

rr, sha, Refused = a19.rr, a19.sha, a19.Refused
RA = a19.ROOT / "git/RetrievalArena"
DATE = "2026-10-24"
PLAN = a19.EVIDENCE / f"wc-rejudge-19-plan-v1-{DATE}.json"
RESULT = a19.EVIDENCE / f"wc-rejudge-19-result-v1-{DATE}.json"
AUTH = a19.PRIVATE / "rejudge-19-authorization.json"
HR20 = a19.EVIDENCE / "wc-hr20-verdicts-v1-2026-10-24.json"
HR20_KEY = a19.ROOT / "artifacts/wc-hr20-19/host-private/key.json"
MAX_INPUT_BYTES = 400_000          # UTF-8 byte ceiling per prompt; the longest pooled page is about 275,000 characters
CAP_PER_VARIANT_USD = 2.0          # reservations at the recorded rates; the estimate is about $0.65 in all
OUT_NAME = "verify-escalated"


def escalation_module():
    sys.path.insert(0, str(RA))
    from retrieval_arena.support import verification  # noqa: PLC0415
    return verification


def variants():
    return sorted(p.name for p in a19.POOL.glob("v-*"))


def verifier_config():
    import c2_pooled_qrels as c2
    config = rr.read_json(c2.CONFIGS / "verifier_config.json")
    return dict(config, max_input_tokens=MAX_INPUT_BYTES, spend_cap_usd=CAP_PER_VARIANT_USD)


def inputs(oid):
    root = a19.POOL / oid
    return root / "pool-agents", root / "pool-agents-input" / "corpus", root / "verify"


def run_for(oid, queries, out_dir, transport=None):
    pool, corpus, prior = inputs(oid)
    return escalation_module().EscalateRun(pool, corpus, queries, verifier_config(), out_dir, transport, prior,
                                           query_ids=[q["question_id"] for q in queries])


def write_evidence(path, data):
    if path.exists():
        raise Refused(f"evidence-exists:{path.name}")
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    path.with_suffix(".sha256").write_text(f"{sha(path)}  {path.name}\n", encoding="ascii", newline="\n")
    return sha(path)


def hr20_items():
    verdicts = rr.read_json(HR20)
    return [{"item": r["item"], "variant": r["variant"], "question_id": r["question_id"], "verdict": r["verdict"],
             "answer_present": r["verdict"] in ("still_answers", "answer_moved")} for r in verdicts["reviews"]]


def plan(_args):
    plan19 = a19.load_plan()
    queries = a19.pool_queries(plan19)
    v = escalation_module()
    per, total_bytes = {}, 0
    for oid in variants():
        run = run_for(oid, queries, a19.POOL / oid / "_plan-probe")      # constructor only: validates, never judges
        sizes = [len(run.windows[d]["text"].encode("utf-8")) for _, d, _ in run.targets]
        total_bytes += sum(sizes) + 1500 * len(sizes)
        per[oid] = {"targets": len(run.targets), "targets_sha256": v.sha256_json(run.targets),
                    "prior_qrels_manifest_sha256": hashlib.sha256((inputs(oid)[2] / "qrels_manifest.json").read_bytes()).hexdigest(),
                    "pool_sha256": run.pool_manifest["pool_sha256"], "max_page_bytes": max(sizes, default=0),
                    "pages_over_cap": sum(s + 1500 > MAX_INPUT_BYTES for s in sizes)}
    config = verifier_config()
    estimate = total_bytes / 4 * config["pricing"]["input_per_mtok"] / 1e6 + \
        sum(p["targets"] for p in per.values()) * 120 * config["pricing"]["output_per_mtok"] / 1e6
    data = {"schema_version": "docspectra.icsc.wc_rejudge_plan.v1", "decision": "D-P5 (author, 2026-10-24)",
            "policy": v.ESCALATION_POLICY_ID, "prompt_sha256": v.prompt_sha256(), "settings_sha256": v.settings_sha256(config),
            "verifier_config": config, "ra_source_commit": None, "variants": per, "estimate_usd": round(estimate, 3), "estimate_rule": "UTF-8 bytes / 4 input tokens, 120 output tokens per page",
            "cap_usd_total": CAP_PER_VARIANT_USD * len(per), "usd_stop": a19.USD_STOP,
            "common_set_rule": "S19-4: a tier-2 question is in the common set if it has >= 1 supported page on all four variants (escalated qrels)",
            "replaces": "the 101-question common set of S19-4 as the tier-2 primary; the 101 become a sensitivity row",
            "hr20_expectation": {"source": HR20.name, "source_sha256": sha(HR20),
                                 "rule": "answer_present (still_answers or answer_moved) -> the escalated qrels have a supported page "
                                         "for that question on that variant; unsure -> they do not",
                                 "items": hr20_items(), "note": "Q01 is not blind: the author saw Haiku's window verdicts on it"}}
    import subprocess
    data["ra_source_commit"] = subprocess.run(["git", "-C", str(RA), "rev-parse", "HEAD"], capture_output=True, text=True,
                                              check=True).stdout.strip()
    print(json.dumps({"plan": PLAN.name, "sha256": write_evidence(PLAN, data), "variants": per, "estimate_usd": data["estimate_usd"]}, indent=1))


def load_plan():
    if not PLAN.exists() or PLAN.with_suffix(".sha256").read_text(encoding="ascii").split()[0] != sha(PLAN):
        raise Refused("rejudge-plan-missing-or-changed")
    return rr.read_json(PLAN)


def authorize(args):
    load_plan()
    if AUTH.exists():
        raise Refused("rejudge-authorization-already-recorded")
    entry = {"author_yes": True, "plan_sha256": sha(PLAN), "quote": args.quote,
             "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    a19.put_json(AUTH, entry)
    print(json.dumps(entry, indent=1))


def escalate(args):
    import c2_pooled_qrels as c2
    p = load_plan()
    auth = rr.read_json(AUTH) if AUTH.exists() else {}
    if not auth.get("author_yes") or auth.get("plan_sha256") != sha(PLAN):
        raise Refused("rejudge-not-authorized")
    oid = args.variant
    if oid not in p["variants"]:
        raise Refused(f"unknown-variant:{oid}")
    total, _ = a19.spend_total()
    if total + CAP_PER_VARIANT_USD > a19.USD_STOP:
        raise Refused(f"usd-stop: total {total:.2f} + cap {CAP_PER_VARIANT_USD}")
    os.environ["ANTHROPIC_API_KEY"] = a19.provider_env("ANTHROPIC_API_KEY")["ANTHROPIC_API_KEY"]  # by name to RA; not logged
    pool, corpus, prior = inputs(oid)
    root = a19.POOL / oid
    inp = c2.fresh(root / f"{OUT_NAME}-input")
    shutil.copytree(pool, inp / "pool", ignore=shutil.ignore_patterns("receipt.json"))
    shutil.copytree(corpus, inp / "corpus")
    shutil.copytree(prior, inp / "prior", ignore=shutil.ignore_patterns("receipt.json", "cost-ledger.jsonl", "verify_state.json"))
    queries = a19.pool_queries(a19.load_plan())
    c2.write_jsonl(inp / "queries.jsonl", queries)
    (inp / "query_ids.json").write_text(json.dumps(sorted(q["question_id"] for q in queries)), encoding="utf-8")
    (inp / "verifier_config.json").write_text(json.dumps(p["verifier_config"], indent=1), encoding="utf-8")
    a = {"config": "verifier_config.json", "pool": "pool", "corpus": "corpus", "queries": "queries.jsonl", "prior": "prior",
         "query_ids": "query_ids.json", "limit": args.limit, "resume": None}
    return c2.launch("escalate", c2.VERIFY_IMAGE, inp, c2.fresh(root / OUT_NAME), a)


def jsonl(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def record(_args):
    p = load_plan()
    plan19 = a19.load_plan()
    all_q = sorted(q["question_id"] for q in plan19["questions"]["2"])
    per, supported = {}, {}
    for oid in p["variants"]:
        out = a19.POOL / oid / OUT_NAME
        receipt = rr.read_json(out / "receipt.json")
        manifest = rr.read_json(out / "qrels_manifest.json")
        old = rr.read_json(a19.POOL / oid / "verify" / "qrels_manifest.json")
        if (receipt["status"] != "completed" or receipt["result"]["run"]["remaining_windows"] != 0
                or manifest["escalation"]["targets_sha256"] != p["variants"][oid]["targets_sha256"]):
            raise Refused(f"escalation-incomplete-or-different:{oid}")
        supported[oid] = {r["query_id"] for r in jsonl(out / "qrels.jsonl") if r["relevance"] > 0}
        per[oid] = {"receipt_sha256": receipt["sha256"], "source_commit": receipt["source_commit"],
                    "qrels_sha256": manifest["qrels_sha256"], "qrels_manifest_sha256": sha(out / "qrels_manifest.json"),
                    "outcomes": manifest["escalation"]["outcomes"], "decisions": manifest["counts"]["decisions"],
                    "questions_supported": len(supported[oid]), "questions_supported_before": len(old["ordered_query_ids"]),
                    "questions_still_lost": sorted(set(all_q) - supported[oid])}
    common = sorted(set(all_q).intersection(*supported.values()))
    agreement = []
    for it in p["hr20_expectation"]["items"]:
        found = it["question_id"] in supported[it["variant"]]
        agreement.append({**{k: it[k] for k in ("item", "question_id", "variant", "verdict", "answer_present")},
                          "escalated_supported": found, "agrees": found == it["answer_present"]})
    table = {f"author_{a}_rejudge_{b}": sum(r["answer_present"] == a and r["escalated_supported"] == b for r in agreement)
             for a in (True, False) for b in (True, False)}
    data = {"schema_version": "docspectra.icsc.wc_rejudge_result.v1", "plan_sha256": sha(PLAN), "variants": per,
            "common_set": {"rule": p["common_set_rule"], "n": len(common), "question_ids": common,
                           "question_ids_sha256": hashlib.sha256(json.dumps(common).encode()).hexdigest()},
            "hr20_agreement": {"table": table, "items": agreement},
            "spend_usd_upper_total": round(a19.spend_total()[0], 4),
            "note": "old 101 common set: see wc-answers-19-run-v1-2026-10-24.json (S19-4); kept as a sensitivity row"}
    print(json.dumps({"result": RESULT.name, "sha256": write_evidence(RESULT, data), "common_n": len(common),
                      "hr20": table, "per_variant": {o: {k: v[k] for k in ("outcomes", "questions_supported",
                                                                             "questions_supported_before")} for o, v in per.items()}}, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="stage", required=True)
    sub.add_parser("plan").set_defaults(fn=plan)
    au = sub.add_parser("authorize")
    au.add_argument("--quote", required=True)
    au.set_defaults(fn=authorize)
    es = sub.add_parser("escalate")
    es.add_argument("--variant", required=True)
    es.add_argument("--limit", type=int, default=None, help="judge at most N pages (smoke)")
    es.set_defaults(fn=escalate)
    sub.add_parser("record").set_defaults(fn=record)
    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()

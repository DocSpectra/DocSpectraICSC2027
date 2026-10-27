"""Reproduce the Sprint 24 planning inventory. Metadata only; no model/runtime imports.

Context owns this ICSC-specific input inventory, not a reusable scoring operator.
Run from any directory with `python -B .../inventory_lt_trace_audit_24.py write|verify`.
The write command refuses an existing output; verify compares the exact bytes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONTEXT = ROOT / "git/context"
RUN = ROOT / "artifacts/docker-corpus-variants-v1/runs/wc-v1"
OUTPUT = CONTEXT / "evidence/lt-trace-audit-24-planning-inventory-v1-2026-10-25.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def pin(path):
    data = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def build():
    source_names = [
        "evidence/wc-answers-19-plan-v1-2026-10-22.json",
        "evidence/wc-answers-19-run-v1-2026-10-24.json",
        "evidence/wc-analysis-20-v1-2026-10-24.json",
        "evidence/wc-rejudge-19-result-v1-2026-10-24.json",
        "evidence/wc-tier2-freeze-v1-2026-10-14.json",
        "evidence/wc-tier1-tasks-v2-2026-10-18.json",
        "evidence/normalized-magnetic-22-v1-2026-10-25.json",
        "evidence/magnetic-attribution-23-v1-2026-10-25.json",
        "scripts/complex_task_agent_loop.py", "scripts/wc_refinement_15.py",
        "scripts/wc_analysis_20.py", "scripts/wc_answers_19.py",
        "design/complex-task-agentic-operator-prompt-v12.txt",
    ]
    key_path = RUN / "host-private/answers-19-key.json"  # opaque arm map, not a credential
    variants = read(key_path)["variants"]
    traces, coverage = [], {}
    for tier in ("t1", "t2"):
        terminals, policies, cells = Counter(), Counter(), Counter()
        fields = Counter()
        for path in sorted((RUN / "answers-19" / tier).glob("*.json")):
            data = read(path)
            if "steps" not in data or "question_id" not in data:
                raise ValueError(f"unexpected top-level JSON: {path}")
            oid = next(o for o in variants if path.name.startswith(o + "-"))
            condition = next(c for c in ("s1-no-follow", "s1-follow", "s6-no-follow", "s6-follow")
                             if f"-{c}-" in path.name)
            terminals[data["terminal"]] += 1
            policies[data.get("history_compaction_policy", "missing")] += 1
            cells[f"{variants[oid]['variant']}/{condition}"] += 1
            for name in ("graph_sha256", "corpus_inventory_sha256", "question_sha256", "budgets", "answer"):
                fields[name] += name in data
            traces.append(dict(pin(path), tier=tier, variant=oid, question_id=data["question_id"],
                               condition=condition, terminal=data["terminal"]))
        coverage[tier] = {"trace_files": sum(terminals.values()), "terminals": dict(terminals),
                          "history_policies": dict(policies), "cells": dict(cells),
                          "field_presence_only": dict(fields)}
    artifacts = [pin(key_path)]
    for oid in sorted(variants):
        paths = [RUN / f"answers-19-export/variants/{oid}/graph_edges.csv",
                 RUN / f"host-private/answers-19-export-receipts/{oid}.json"]
        for name in ("qrels.jsonl", "qrels_manifest.json", "page_decisions.jsonl", "judgments.jsonl"):
            paths.append(RUN / f"answers-19-pool/{oid}/verify-escalated/{name}")
        paths.append(RUN / f"answers-19-pool/{oid}/pool-agents/pool.jsonl")
        artifacts.extend(pin(p) for p in paths)
    example = RUN / "answers-19/t2/v-6337600cdc97-docker_docs_faq_0006-s1-follow-r1.json"
    data = read(example)
    steps = []
    for step in data["steps"]:
        action, result = step.get("action") or {}, step.get("result") or {}
        if action.get("action") != "follow":
            continue
        steps.append({"call": step["call"], "action": action,
                      "result_keys": sorted(result), "returned_line_count": len(result.get("lines", [])),
                      "returned_file_sha256": result.get("file_sha256"), "error_present": "error" in result})
    s20 = read(CONTEXT / "evidence/wc-analysis-20-v1-2026-10-24.json")
    table = s20["tier2"]["ragas"]["full_111"]["tables"]
    nav = s20["tier2"]["rows"]["primary"]["navigation"]
    return {
        "schema_version": "docspectra.icsc.lt_trace_audit_24.planning_inventory.v1",
        "created": "2026-10-25", "model_calls": 0, "network_calls": 0,
        "scope": "Planning inventory and extraction of already recorded Sprint 20 values; not the Sprint 24 audit.",
        "script": pin(Path(__file__)), "sources": [pin(CONTEXT / p) for p in source_names],
        "artifacts": artifacts, "trace_inventory": traces, "coverage": coverage,
        "arm_map": {o: v["variant"] for o, v in sorted(variants.items())},
        "recorded_s20": {
            "navigation_shares_t2_follow": {k: v for k, v in s20["navigation_shares"].items()
                                            if k.startswith("t2/") and k.endswith("/s1-follow")},
            "support_landings_all_follows_primary105": nav["follows_landing_on_support/s1-follow"],
            "factual_correctness_all111_pooled": table["factual_correctness/pooled"],
            "factual_correctness_all111_follow": table["factual_correctness/s1-follow"],
        },
        "example": {"trace": pin(example),
                    "selection": "Lexicographically first LT tier-2 follow-enabled trace with a follow action, as inspected in planning; illustrative field availability only.",
                    "follow_actions": steps, "budgets": data["budgets"],
                    "history_compaction_policy": data["history_compaction_policy"],
                    "answer_citations": data["answer"].get("citations", [])},
        "not_established": ["trace-to-qrel and exact page-byte joins", "added-edge status of individual example follows",
                            "support-span exposure", "final-context visibility", "citation correctness",
                            "answer reliance or benefit", "spectral-view membership of followed edges"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("write", "verify"))
    args = parser.parse_args()
    result = build()
    payload = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")
    if args.mode == "write":
        with OUTPUT.open("xb") as stream:
            stream.write(payload)
    elif OUTPUT.read_bytes() != payload:
        raise SystemExit("Planning inventory differs from current input bytes; do not overwrite the old evidence.")
    print(json.dumps({"mode": args.mode, "path": OUTPUT.relative_to(ROOT).as_posix(),
                      "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
                      "coverage": result["coverage"]}, sort_keys=True))


if __name__ == "__main__":
    main()

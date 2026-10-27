"""Sprint C3: RAGAS judge requalification on Claude Haiku 5.5 (design D11c), 15b thresholds unchanged.

A new version of the 15b adapter (scripts/wc_ragas_15b.py, imported, never edited). Changes from 15b:
  * positive-control contexts are C2's verified passages (the windows Haiku accepted, merged into line
    runs of the O corpus), not windows of the size-biased top-5 support pages;
  * questions without verified support are replaced (15b's seeded 40 first, then a seeded fill);
  * judge config v4, provider anthropic, model claude-haiku-5-5, through the RAGAS operator's
    external_api mode (credential by name); no temperature or seed exists for hosted Claude.
Real tier-1 units are 15b's exactly (same seed, same traces).

Commands:
  build                       inputs under artifacts/wc-ragas-c3/inputs (no model calls)
  judge --set S --repeat N    one uncached judge run (authorization: author "yes")
  analyse                     thresholds, cost, Sprint 19 projection -> evidence
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import time
from pathlib import Path

import wc_ragas_15b as b15

rs = b15.rs
ROOT, RA, HERE = b15.ROOT, b15.RA, b15.HERE
OUT = ROOT / "artifacts/wc-ragas-c3"
EVIDENCE = HERE.parent / "evidence/wc-ragas-judge-qualification-c3-2026-10-12.json"
IMAGE_RECEIPT = ".tmp/ragas-image-c3.json"
C2 = ROOT / "artifacts/c2-pooled-qrels"
O_CORPUS = ROOT / "artifacts/docker-corpus-variants-v1/runs/wc-15-fixture-v1/exports/O/corpus"
SCHEMA = "docspectra.icsc.wc_ragas_c3.v1"
FILL_SEED = "wc-c3-controls-fill-v1"
PRICING = {"currency": "USD", "input_per_mtok": 0.1, "output_per_mtok": 0.5,
           "long_context": {"above_input_tokens": 100000, "input_per_mtok": 0.5, "output_per_mtok": 2.5},
           "source": "Anthropic first-party API price list as recorded by the claude-api skill (cached 2026-10-06), rechecked 2026-10-12"}
JUDGE = {"model": "claude-haiku-5-5", "revision": "claude-haiku-5-5"}
GENERATION = {"thinking": "disabled", "effort": "medium"}
# Pre-declared before any C3 score existed (author "yes", October 12): positive controls whose C2-verified passages
# the agent's read judged off-topic or too weak. Headline verdicts use all 40; separation without them is a sensitivity.
SUSPECT_CONTROLS = ("docker_docs_faq_0020", "docker_docs_faq_0026", "docker_docs_faq_0044", "docker_docs_faq_0051",
                    "docker_docs_faq_0065")
CAPS = {"controls-response": 0.30, "controls-reference": 0.30, "real-tier1": 0.90,  # sums to $3 over two repeats
        "controls-reference-clean": 0.15}
# Clean-controls rerun (author "yes", October 14, after the C2 spot-check; rule fixed before the run): drop the
# pre-declared suspects and every control whose verified pages include a page the author rejected in the spot-check,
# refill to 40 with a seeded draw from the remaining verified questions. ContextRecall only; descriptive check of
# whether the metric separates on clean controls. The C3 headline verdict is unchanged.
CLEAN_SET = "controls-reference-clean"
CLEAN_FILL_SEED = "wc-c3-controls-clean-fill-v1"
SPOT_CHECK = C2 / "review-result/review_result.json"


def config(name):
    metrics = b15.SETS["controls-reference" if name == CLEAN_SET else name]
    cfg = {"schema_version": rs.PREFIX + "judge_config.v4", "provider": "anthropic", **JUDGE,
           "temperature": None, "seed": None, "retries": 1, "timeout_seconds": 600,
           "max_output_tokens": 16000, "max_input_tokens": 64000,
           "answer_prompt": "answer_satisfaction.v4", "context_prompt": "context_support.v4", "thinking": False,
           "element_completeness": False, "faithfulness": metrics["faithfulness"],
           "context_recall": metrics["context_recall"],
           "factual_correctness": dict(b15.FACTUAL_CORRECTNESS) if metrics["factual_correctness"] else None,
           "generation": dict(GENERATION), "pricing": PRICING, "spend_cap_usd": CAPS[name]}
    rs.validate_config(cfg)
    return cfg


# ------------------------------------------------------------------ controls from verified passages

def verified():
    """{question_id: [(doc_id, [(start, end), ...]) in pool order]} for supported pages on O."""
    manifest = b15.read_json(C2 / "verify-full/qrels_manifest.json")
    inventory = b15.read_json(C2 / "pool/corpus_inventory.json")
    windows = {w["window_id"]: w for w in b15.read_jsonl(C2 / "pool/windows.jsonl")}
    out = {}
    for d in b15.read_jsonl(C2 / "verify-full/page_decisions.jsonl"):
        if d["decision"] != "supported" or inventory.get(d["doc_id"]) != d["page_sha256"]:
            continue
        spans = sorted((windows[w]["start_line"], windows[w]["end_line"]) for w in d["answering_window_ids"])
        out.setdefault(d["query_id"], []).append((min(d["sources"].values()), d["doc_id"], spans))
    return {q: [(doc, spans) for _, doc, spans in sorted(v)] for q, v in out.items()}, manifest


def verified_contexts(pages, cap=b15.CONTROL_CONTEXT_CAP):
    """Accepted windows merged into contiguous line runs per page, pages in pool order, under the byte cap."""
    out, used = [], 0
    for doc, spans in pages:
        lines = (O_CORPUS / f"{doc}.md").read_text(encoding="utf-8").splitlines()
        numbers = sorted({n for s, e in spans for n in range(s, e + 1) if n <= len(lines)})
        for run in b15.runs(numbers):
            piece = [(n, lines[n - 1]) for n in run]
            size = sum(len(t.encode()) + 1 for _, t in piece)
            if used + size > cap:
                continue
            out.append(b15.chunk(doc, piece))
            used += size
    return out


def control_sample(support):
    original, _, _ = b15.control_sample()
    kept = [q for q in original if q in support]
    pool = sorted(q for q in support if q not in original)
    fill = random.Random(FILL_SEED).sample(pool, b15.N_CONTROLS - len(kept))
    picked = kept + fill
    return picked, {q: picked[(i + 1) % len(picked)] for i, q in enumerate(picked)}, \
        {"from_15b_sample": len(kept), "15b_without_verified_support": len(original) - len(kept), "filled": len(fill),
         "fill_seed": FILL_SEED}


def build_controls():
    support, manifest = verified()
    picked, other, sample_meta = control_sample(support)
    refs = b15.tier2_references()
    qtext = {r["question_id"]: r["question"] for r in b15.read_jsonl(b15.DATASET / "questions.jsonl")}
    answers_sha = b15.sha((b15.DATASET / "answers.jsonl").read_bytes())
    response_units, reference_units, meta = [], [], []
    for q in picked:
        ctx = verified_contexts(support[q])
        common = {"question": qtext[q], "run": "controls-c3", "contexts": ctx, "source_sha": answers_sha}
        pos = {**common, "qid": q, "reference": refs[q], "condition": "positive", "text": refs[q], "record_id": q}
        neg_response = {**common, "qid": q, "reference": refs[q], "condition": "negative", "text": refs[other[q]],
                        "record_id": other[q]}
        neg_reference = {**common, "qid": q + "::mismatched-reference", "reference": refs[other[q]],
                         "condition": "negative", "text": refs[q], "record_id": other[q]}
        response_units += [pos, neg_response]
        reference_units += [pos, neg_reference]
        shared = sorted({d for d, _ in support[q]} & {d for d, _ in support[other[q]]})
        meta.append({"question_id": q, "mismatched_with": other[q], "verified_pages": len(support[q]),
                     "context_chunks": len(ctx), "context_bytes": sum(len(c["text"].encode()) for c in ctx),
                     "topic_overlap_shared_pages": shared})
    provenance = {"authoring_method": "historical FAQ reference answers (answers.jsonl, CIKM 2026-05-14) over C2 "
                                      "LLM-verified passages (Haiku 5.5, qrels v2); controls reuse references as answers",
                  "reviewer_id": "automatic-historical-faq-answers", "frozen_at": "2026-05-14", "independent_of_predictions": True}
    return ({"controls-response": b15.assemble(response_units, provenance),
             "controls-reference": b15.assemble(reference_units, provenance)}, meta,
            {**sample_meta, "qrels_sha256": manifest["qrels_sha256"]})


def build(args):
    sets, control_meta, sample_meta = build_controls()
    real, real_meta, real_pool = b15.build_real()
    sets.update(real)
    plan = {"schema_version": SCHEMA + ".plan", "thresholds": b15.THRESHOLDS, "judge": JUDGE, "generation": GENERATION,
            "factual_correctness": b15.FACTUAL_CORRECTNESS, "seeds": {"controls": b15.CONTROL_SEED, "fill": FILL_SEED, "real": b15.REAL_SEED},
            "control_context_cap_bytes": b15.CONTROL_CONTEXT_CAP, "control_sample": sample_meta,
            "topic_overlap_pairs": sum(1 for m in control_meta if m["topic_overlap_shared_pages"]),
            "controls": control_meta, "real": real_meta, "real_pool": real_pool, "sets": {}}
    for name, (batch, rubrics) in sets.items():
        folder = OUT / "inputs" / name
        folder.mkdir(parents=True, exist_ok=True)
        for file, value in (("batch.json", batch), ("rubrics.json", rubrics), ("config.json", config(name))):
            rs.write_json(folder / file, value)
        plan["sets"][name] = {"units": len(batch["predictions"]), "chunks": len(batch["corpus"]["chunks"]),
                              **{f: b15.sha((folder / f).read_bytes()) for f in ("batch.json", "rubrics.json", "config.json")}}
    rs.write_json(OUT / "inputs/plan.json", plan)
    print(json.dumps({"control_sample": sample_meta, "topic_overlap_pairs": plan["topic_overlap_pairs"], "sets": plan["sets"]}, indent=1))


# ------------------------------------------------------------------ judge runs (external_api)

def build_clean(args):
    support, manifest = verified()
    picked, _, _ = control_sample(support)
    review = b15.read_json(SPOT_CHECK)
    rejected = {(d["query_id"], d["doc_id"]) for d in review["disagreements"] if d["stratum"] == "verifier_yes" and d["response"] == "no"}
    bad = {q for q in picked if q in SUSPECT_CONTROLS or any((q, doc) in rejected for doc, _ in support[q])}
    kept = [q for q in picked if q not in bad]
    pool = sorted(q for q in support if q not in picked)
    fill = random.Random(CLEAN_FILL_SEED).sample(pool, len(picked) - len(kept))
    final = kept + fill
    other = {q: final[(i + 1) % len(final)] for i, q in enumerate(final)}
    refs = b15.tier2_references()
    qtext = {r["question_id"]: r["question"] for r in b15.read_jsonl(b15.DATASET / "questions.jsonl")}
    answers_sha = b15.sha((b15.DATASET / "answers.jsonl").read_bytes())
    units, meta = [], []
    for q in final:
        ctx = verified_contexts(support[q])
        common = {"question": qtext[q], "run": "controls-c3-clean", "contexts": ctx, "source_sha": answers_sha}
        units += [{**common, "qid": q, "reference": refs[q], "condition": "positive", "text": refs[q], "record_id": q},
                  {**common, "qid": q + "::mismatched-reference", "reference": refs[other[q]], "condition": "negative",
                   "text": refs[q], "record_id": other[q]}]
        meta.append({"question_id": q, "mismatched_with": other[q], "context_bytes": sum(len(c["text"].encode()) for c in ctx)})
    provenance = {"authoring_method": "historical FAQ reference answers over C2 verified passages; controls with suspect or "
                                      "spot-check-rejected pages replaced", "reviewer_id": "automatic-historical-faq-answers",
                  "frozen_at": "2026-05-14", "independent_of_predictions": True}
    batch, rubrics = b15.assemble(units, provenance)
    folder = OUT / "inputs" / CLEAN_SET
    folder.mkdir(parents=True, exist_ok=True)
    for file, value in (("batch.json", batch), ("rubrics.json", rubrics), ("config.json", config(CLEAN_SET))):
        rs.write_json(folder / file, value)
    plan = {"rule": "drop pre-declared suspects and controls with a spot-check-rejected verified page; seeded refill",
            "dropped": sorted(bad), "dropped_suspects": sorted(bad & set(SUSPECT_CONTROLS)),
            "dropped_spot_check": sorted(bad - set(SUSPECT_CONTROLS)), "filled": fill, "fill_seed": CLEAN_FILL_SEED,
            "spot_check_sha256": b15.sha(SPOT_CHECK.read_bytes()), "controls": meta,
            **{f: b15.sha((folder / f).read_bytes()) for f in ("batch.json", "rubrics.json", "config.json")}}
    rs.write_json(OUT / "inputs" / (CLEAN_SET + "-plan.json"), plan)
    print(json.dumps({k: plan[k] for k in ("dropped_suspects", "dropped_spot_check", "filled")}, indent=1))


def analyse_clean(args):
    runs = [b15.metric_values(b15.read_json(OUT / "runs" / f"{CLEAN_SET}-r{r}" / "scores.json"), "context_recall") for r in (1, 2)]
    th = b15.THRESHOLDS
    row = {"parse_rate_by_run": [b15.parse_rate(v) for v in runs], "separation_by_run": [b15.separation(v) for v in runs],
           "repeat": b15.agreement(*runs),
           "positive_mean_by_run": [sum(v[k][1] for k in v if k[1] == "positive" and v[k][1] is not None) / sum(1 for k in v if k[1] == "positive") for v in runs],
           "cost": {f"r{r}": ledger(OUT / "runs" / f"{CLEAN_SET}-r{r}" / "cost-ledger.jsonl") for r in (1, 2)}}
    row["checks"] = {"parse": all(p["rate"] >= th["parse_rate_min"] for p in row["parse_rate_by_run"]),
                     "separation": all(s["share"] >= th["separation_min"] for s in row["separation_by_run"]),
                     "repeat": row["repeat"]["share"] >= th["repeat_share_min"]}
    row["pass_all_frozen_checks"] = all(row["checks"].values())
    row["plan"] = b15.read_json(OUT / "inputs" / (CLEAN_SET + "-plan.json"))
    row["note"] = "descriptive check after the C2 spot-check; the C3 headline verdict is unchanged"
    result = b15.read_json(EVIDENCE)
    result["clean_controls_rerun"] = row
    rs.write_json(EVIDENCE, result)
    print(json.dumps({k: row[k] for k in ("checks", "pass_all_frozen_checks", "positive_mean_by_run")}, indent=1))
    print(json.dumps({"separation": [s["share"] for s in row["separation_by_run"]], "repeat": row["repeat"]["share"],
                      "usd": sum(c["usd"] for c in row["cost"].values())}, indent=1))


def judge(args):
    folder = OUT / "inputs" / args.set
    run = OUT / "runs" / f"{args.set}-r{args.repeat}"
    staged = OUT / "staged" / f"{args.set}-r{args.repeat}"
    if run.exists() or staged.exists():
        raise SystemExit(f"refusing to reuse {run} / {staged}")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not in this process's environment")
    run.mkdir(parents=True)
    (run / ".retrieval-arena-output-owner").touch()
    common = [str(b15.PWSH), "-NoProfile", "-File"]
    subprocess.run(common + [str(RA / "scripts/prepare-ragas-input.ps1"), "-Command", "judge",
                             "-Batch", str(folder / "batch.json"), "-Rubrics", str(folder / "rubrics.json"),
                             "-Artifact", str(folder / "config.json"), "-InputRoot", str(staged), "-NoCache"],
                   cwd=RA, check=True)
    started = time.time()
    done = subprocess.run(common + [str(RA / "scripts/run-ragas.ps1"), "-Command", "judge", "-InputRoot", str(staged),
                                    "-OutputRoot", str(run), "-Receipt", args.receipt,
                                    "-Network", "external_api", "-CredentialEnv", "ANTHROPIC_API_KEY"], cwd=RA)
    rs.write_json(OUT / "runs" / f"{args.set}-r{args.repeat}.wall.json",
                  {"exit_code": done.returncode, "wall_seconds": time.time() - started})
    print(run, done.returncode)


# ------------------------------------------------------------------ analysis

def ledger(path):
    events = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l] if Path(path).exists() else []
    rep = [e for e in events if e["event"] == "reported"]
    return {"calls": len(rep), "usd": sum(e["cost_usd"] for e in rep), "failed": sum(e["event"] == "failed" for e in events),
            "stopped": any(e["event"] == "stopped" for e in events),
            "input_tokens": sum(e["usage"]["input_tokens"] for e in rep), "output_tokens": sum(e["usage"]["output_tokens"] for e in rep)}


def analyse(args):
    b15.OUT, b15.EVIDENCE = OUT, EVIDENCE   # reuse the 15b analysis on this sprint's runs
    b15.analyse(args)
    result = b15.read_json(EVIDENCE)
    result["schema_version"] = SCHEMA + ".qualification"
    result["judge"] = {**JUDGE, "generation": GENERATION, "max_output_tokens": 16000, "max_input_tokens": 64000,
                       "image_receipt": IMAGE_RECEIPT, "pricing": PRICING}
    result["conventions"] = [c.replace("with the same seed and temperature 0", "(hosted Claude: no seed or temperature)")
                             for c in result["conventions"]]
    costs = {name: ledger(OUT / "runs" / name / "cost-ledger.jsonl") for name in result["runs"]}
    result["cost"] = {"by_run": costs, "total_usd": sum(c["usd"] for c in costs.values())}
    real = [costs[k] for k in costs if k.startswith("real-tier1")]
    if real and "projection_sprint19" in result:
        per_answer = sum(c["usd"] for c in real) / (40 * len(real))
        p = result["projection_sprint19"]
        p["usd_per_answer_all_three_metrics"] = per_answer
        p["usd"] = 1616 * per_answer
        p["usd_batch_api_50pct"] = 1616 * per_answer / 2
        p["note"] = "sequential operator latency; Batch API is not in the provider layer yet (follow-up)"
    sens = {}
    for name, metric in (("controls-response", "factual_correctness"), ("controls-response", "faithfulness"),
                         ("controls-reference", "context_recall")):
        runs = [b15.metric_values(b15.read_json(OUT / "runs" / f"{name}-r{r}" / "scores.json"), metric)
                for r in (1, 2) if (OUT / "runs" / f"{name}-r{r}" / "scores.json").exists()]
        sens[f"{name}/{metric}"] = [b15.separation({k: v for k, v in run.items() if k[0] not in SUSPECT_CONTROLS}) for run in runs]
    result["sensitivity_without_suspect_controls"] = {"excluded": list(SUSPECT_CONTROLS), "separation_by_run": sens,
                                                      "note": "pre-declared; descriptive only, never the headline verdict"}
    rs.write_json(EVIDENCE, result)
    print(json.dumps({"sensitivity": {k: [x["share"] for x in v] for k, v in sens.items()}}, indent=1))
    print(json.dumps({"cost": result["cost"]["total_usd"], "projection": result.get("projection_sprint19")}, indent=1))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    j = sub.add_parser("judge")
    j.add_argument("--set", required=True, choices=sorted(b15.SETS) + [CLEAN_SET])
    j.add_argument("--receipt", default=IMAGE_RECEIPT)
    sub.add_parser("build-clean")
    sub.add_parser("analyse-clean")
    j.add_argument("--repeat", required=True, type=int, choices=(1, 2))
    sub.add_parser("analyse")
    args = parser.parse_args(argv)
    {"build": build, "judge": judge, "analyse": analyse, "build-clean": build_clean,
     "analyse-clean": analyse_clean}[args.command](args)


if __name__ == "__main__":
    main()

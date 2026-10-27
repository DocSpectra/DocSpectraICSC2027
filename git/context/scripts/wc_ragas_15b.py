"""Sprint 15b (design D10): RAGAS inputs from agent traces, and the local judge qualification.

Converts a 12b agent trace (complex_task_agent_trace.v2) plus a reference into RetrievalArena
ragas batch/rubric inputs, builds the qualification sets and runs them through RA's unchanged
operator scripts (judge config v3: published Faithfulness, ContextRecall, FactualCorrectness).

Commands:
  build                       write inputs under artifacts/wc-ragas-15b/inputs (no model calls)
  judge --set S --repeat N    one uncached RA judge run of set S (local qwen3:14b through Ollama)
  analyse                     parse rate, positive/negative separation, repeat agreement, timing

The 12/12b/13/15 drivers and their evidence are read, never edited.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RA = ROOT / "git/RetrievalArena"
sys.path.insert(0, str(RA))
from retrieval_arena import ragas_scoring as rs  # noqa: E402

DATASET = ROOT / "artifacts/docker-cikm-2026-05-14/dataset"
ANSWERS_12B = ROOT / "artifacts/sf-12-v2-answers"
QUESTIONS_11B = HERE.parent / "evidence/restructuring-pilot-11b-questions-2026-10-08.json"
OUT = ROOT / "artifacts/wc-ragas-15b"
EVIDENCE = HERE.parent / "evidence/wc-ragas-judge-qualification-15b-2026-10-11.json"
PWSH = Path("C:/Program Files/PowerShell/7/pwsh.exe")
IMAGE_RECEIPT = ".tmp/ragas-image-icsc-15b.json"

SCHEMA = "docspectra.icsc.wc_ragas_15b.v1"
CONTROL_SEED = "wc-15b-controls-v1"
REAL_SEED = "wc-15b-real-v1"
N_CONTROLS = 40
N_REAL = 40
CONTROL_CONTEXT_CAP = 24000  # bytes; support pages are 300-800 KB per question (deviation, see handoff)
WINDOW = 2  # lines either side of a matching line

# Frozen in the sprint prompt; changing them needs the author.
THRESHOLDS = {"parse_rate_min": 0.95, "separation_min": 0.90, "repeat_delta_max": 0.1, "repeat_share_min": 0.90}

JUDGE = {"model": "qwen3:14b", "revision": "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8"}
FACTUAL_CORRECTNESS = {"mode": "f1", "beta": 1.0, "atomicity": "low", "coverage": "low"}  # published defaults
STOP = frozenset("that this with from your have will they them then than there their when what which into "
                 "also only more most some such were been being does each other about after before".split())

# Each set names the published metrics it enables; never pooled with another set.
SETS = {
    "controls-response": {"faithfulness": True, "context_recall": False, "factual_correctness": True},
    "controls-reference": {"faithfulness": False, "context_recall": True, "factual_correctness": False},
    "real-tier1": {"faithfulness": True, "context_recall": True, "factual_correctness": True},
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


# ------------------------------------------------------------------ trace -> RAGAS inputs

def runs(numbers):
    """Split sorted-by-observation line numbers into contiguous ascending runs."""
    out = []
    for n in numbers:
        if out and n == out[-1][-1] + 1:
            out[-1].append(n)
        else:
            out.append([n])
    return out


def chunk(doc_id, lines):
    """lines: [(number, text)], contiguous. Identity binds the doc, range and exact text."""
    text = "\n".join(t for _, t in lines)
    return {"document_id": doc_id, "chunk_id": f"L{lines[0][0]}-{lines[-1][0]}#{sha(text.encode())[:12]}",
            "text": text, "sha256": rs.text_hash(text)}


def trace_contexts(trace):
    """The lines the agent read or followed, in observation order, each (doc, line) once.

    Each read/follow step contributes its newly observed lines, split into contiguous runs.
    No size cap: an oversized prompt is RA's explicit input_too_large, never silent truncation.
    """
    seen, chunks = set(), []
    for step in trace.get("steps", []):
        act, res = step.get("action") or {}, step.get("result")
        if act.get("action") not in ("read", "follow") or not isinstance(res, dict):
            continue
        doc = res.get("doc_id")
        fresh = [(row["line"], row["text"]) for row in res.get("lines") or []
                 if isinstance(row, dict) and (doc, row["line"]) not in seen]
        seen.update((doc, n) for n, _ in fresh)
        texts = dict(fresh)
        for run in runs([n for n, _ in fresh]):
            chunks.append(chunk(doc, [(n, texts[n]) for n in run]))
    return chunks


def answer_text(trace):
    """The final answer text; None when the trace ended without one (missing output, not zero)."""
    ans = trace.get("answer")
    return ans.get("text") if trace.get("terminal") == "answer" and isinstance(ans, dict) else None


def tier1_reference(question):
    """Tier-1 reference (disclosed adaptation, D10): frozen core-element acceptance texts in element order."""
    elements = sorted(question["core_elements"], key=lambda e: e["element_id"])
    return "\n".join(e["acceptance"] for e in elements)


def tier2_references():
    return {r["question_id"]: r["answer"] for r in read_jsonl(DATASET / "answers.jsonl")}


def prediction(unit_qid, question, condition, run, text, contexts, source_sha, record_id):
    return {"schema_version": rs.PREFIX + "prediction.v1", "question_id": unit_qid, "question": question,
            "condition_id": condition, "run_id": run, "stage": "answer", "corpus_sha256": None, "text": text,
            "contexts": [{k: c[k] for k in ("document_id", "chunk_id", "sha256")} for c in contexts],
            "source": {"artifact_sha256": source_sha, "record_id": record_id}}


def assemble(units, provenance):
    """units: [{qid, question, reference, condition, run, text, contexts, source_sha, record_id}] -> (batch, rubrics)."""
    chunks = {}
    for u in units:
        for c in u["contexts"]:
            chunks.setdefault((c["document_id"], c["chunk_id"]), c)
    corpus = {"corpus_id": "wc-ragas-15b", "chunks": [chunks[k] for k in sorted(chunks)]}
    corpus["sha256"] = rs.digest(corpus)
    predictions, questions = [], {}
    for u in units:
        row = prediction(u["qid"], u["question"], u["condition"], u["run"], u["text"], u["contexts"],
                         u["source_sha"], u["record_id"])
        row["corpus_sha256"] = corpus["sha256"]
        predictions.append(row)
        prior = questions.setdefault(u["qid"], {"question_id": u["qid"], "question": u["question"],
                                                "elements": [], "reference": u["reference"]})
        assert prior["reference"] == u["reference"] and prior["question"] == u["question"], u["qid"]
    batch = {"schema_version": rs.PREFIX + "batch.v1", "corpus": corpus,
             "units": [rs.unit_id(r) for r in predictions], "predictions": predictions}
    rubrics = {"schema_version": rs.PREFIX + "rubrics.v2", "corpus_sha256": corpus["sha256"],
               "provenance": provenance, "questions": list(questions.values())}
    rs.validate_inputs(batch, rubrics)
    return batch, rubrics


def config(metrics):
    cfg = {"schema_version": rs.PREFIX + "judge_config.v3", "provider": "ollama", **JUDGE,
           "temperature": 0, "seed": 7, "retries": 1, "timeout_seconds": 600,
           # 36,000 + 3,072 + 1,024 keeps num_ctx within qwen3's 40,960 tokens; every 12b trace fits whole.
           "max_output_tokens": 3072, "max_input_tokens": 36000,
           "answer_prompt": "answer_satisfaction.v4", "context_prompt": "context_support.v4", "thinking": False,
           "element_completeness": False, "faithfulness": metrics["faithfulness"],
           "context_recall": metrics["context_recall"],
           "factual_correctness": dict(FACTUAL_CORRECTNESS) if metrics["factual_correctness"] else None}
    rs.validate_config(cfg)
    return cfg


# ------------------------------------------------------------------ qualification sets

def tokens(text):
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) >= 4 and t not in STOP}


def support_windows(reference, pages, cap=CONTROL_CONTEXT_CAP):
    """Positive-control contexts: windows of the support pages around lines sharing reference words.

    pages: [(doc_id, [line text, ...])] in support rank order. Lines sharing >= 2 distinct reference
    content words (else >= 1) are taken best-first with +-WINDOW lines until the byte cap; output is
    contiguous runs in page rank and line order. A control construction, disclosed as a deviation.
    """
    ref = tokens(reference)
    scored = [(len(tokens(text) & ref), rank, i) for rank, (_, lines) in enumerate(pages) for i, text in enumerate(lines)]
    floor = 2 if any(s >= 2 for s, _, _ in scored) else 1
    picked, used = set(), 0
    for s, rank, i in sorted((x for x in scored if x[0] >= floor), key=lambda x: (-x[0], x[1], x[2])):
        lines = pages[rank][1]
        window = [(rank, j) for j in range(max(0, i - WINDOW), min(len(lines), i + WINDOW + 1)) if (rank, j) not in picked]
        size = sum(len(lines[j].encode()) + 1 for _, j in window)
        if used + size > cap:
            continue
        picked.update(window)
        used += size
    out = []
    for rank, (doc, lines) in enumerate(pages):
        numbers = sorted(j for r, j in picked if r == rank)
        for run in runs(numbers):
            out.append(chunk(doc, [(j + 1, lines[j]) for j in run]))
    return out


def control_sample():
    audit = read_jsonl(DATASET / "faq_support_audit.jsonl")
    ids = sorted(r["question_id"] for r in audit if r["support_label"] == "supported")
    picked = random.Random(CONTROL_SEED).sample(ids, N_CONTROLS)
    # Mismatch: each question gets the next question's reference in the seeded order (a derangement).
    return picked, {q: picked[(i + 1) % len(picked)] for i, q in enumerate(picked)}, {r["question_id"]: r for r in audit}


def build_controls():
    picked, other, audit = control_sample()
    refs = tier2_references()
    qtext = {r["question_id"]: r["question"] for r in read_jsonl(DATASET / "questions.jsonl")}
    answers_sha = sha((DATASET / "answers.jsonl").read_bytes())
    response_units, reference_units, meta = [], [], []
    for q in picked:
        pages = []
        for doc in audit[q]["top_docs"]:
            raw = (DATASET / "corpus" / f"{doc['doc_id']}.md").read_text(encoding="utf-8")
            pages.append((doc["doc_id"], raw.splitlines()))
        ctx = support_windows(refs[q], pages)
        common = {"question": qtext[q], "run": "controls-v1", "contexts": ctx, "source_sha": answers_sha}
        # Positive: the question's own reference as the answer, over its own support windows.
        pos = {**common, "qid": q, "reference": refs[q], "condition": "positive", "text": refs[q], "record_id": q}
        # Negative (response side): another question's reference as the answer; own reference kept.
        neg_response = {**common, "qid": q, "reference": refs[q], "condition": "negative", "text": refs[other[q]],
                        "record_id": other[q]}
        # Negative (reference side, ContextRecall reads no answer): another question's reference over own contexts.
        neg_reference = {**common, "qid": q + "::mismatched-reference", "reference": refs[other[q]],
                         "condition": "negative", "text": refs[q], "record_id": other[q]}
        response_units += [pos, neg_response]
        reference_units += [pos, neg_reference]
        meta.append({"question_id": q, "mismatched_with": other[q], "context_chunks": len(ctx),
                     "context_bytes": sum(len(c["text"].encode()) for c in ctx)})
    provenance = {"authoring_method": "historical FAQ reference answers (answers.jsonl, CIKM 2026-05-14); controls "
                                      "reuse them as answers by design", "reviewer_id": "automatic-historical-faq-answers",
                  "frozen_at": "2026-05-14", "independent_of_predictions": True}
    return {"controls-response": assemble(response_units, provenance),
            "controls-reference": assemble(reference_units, provenance)}, meta


def real_sample():
    files = sorted(p for p in ANSWERS_12B.glob("v-*.json"))
    answered = [p for p in files if read_json(p).get("terminal") == "answer"]
    return random.Random(REAL_SEED).sample(answered, N_REAL), len(files), len(answered)


def build_real():
    picked, total, answered = real_sample()
    questions = {q["question_id"]: q for q in read_json(QUESTIONS_11B)["questions"]}
    units, meta = [], []
    for path in picked:
        trace = read_json(path)
        q = questions[trace["question_id"]]
        ctx = trace_contexts(trace)
        units.append({"qid": q["question_id"], "question": q["question"], "reference": tier1_reference(q),
                      "condition": path.stem, "run": "sf-12-v2", "text": answer_text(trace), "contexts": ctx,
                      "source_sha": sha(path.read_bytes()), "record_id": path.name})
        meta.append({"trace": path.name, "question_id": q["question_id"], "context_chunks": len(ctx),
                     "context_bytes": sum(len(c["text"].encode()) for c in ctx)})
    provenance = {"authoring_method": "tier-1 reference: frozen 11b core-element acceptance texts joined in element "
                                      "order (D10 disclosed adaptation)", "reviewer_id": "11b-question-set-2026-10-08",
                  "frozen_at": "2026-10-08", "independent_of_predictions": True}
    return {"real-tier1": assemble(units, provenance)}, meta, {"traces": total, "answered": answered}


def build(args):
    sets, control_meta = build_controls()
    real, real_meta, real_pool = build_real()
    sets.update(real)
    plan = {"schema_version": SCHEMA + ".plan", "thresholds": THRESHOLDS, "judge": JUDGE,
            "factual_correctness": FACTUAL_CORRECTNESS, "seeds": {"controls": CONTROL_SEED, "real": REAL_SEED},
            "control_context_cap_bytes": CONTROL_CONTEXT_CAP, "controls": control_meta, "real": real_meta,
            "real_pool": real_pool, "sets": {}}
    for name, (batch, rubrics) in sets.items():
        folder = OUT / "inputs" / name
        folder.mkdir(parents=True, exist_ok=True)
        cfg = config(SETS[name])
        for file, value in (("batch.json", batch), ("rubrics.json", rubrics), ("config.json", cfg)):
            rs.write_json(folder / file, value)
        plan["sets"][name] = {"units": len(batch["predictions"]), "chunks": len(batch["corpus"]["chunks"]),
                              **{f: sha((folder / f).read_bytes()) for f in ("batch.json", "rubrics.json", "config.json")}}
    rs.write_json(OUT / "inputs/plan.json", plan)
    print(json.dumps(plan["sets"], indent=1))


# ------------------------------------------------------------------ judge runs through the RA operator

def judge(args):
    folder = OUT / "inputs" / args.set
    run = OUT / "runs" / f"{args.set}-r{args.repeat}"
    staged = OUT / "staged" / f"{args.set}-r{args.repeat}"
    if run.exists() or staged.exists():
        raise SystemExit(f"refusing to reuse {run} / {staged}")
    run.mkdir(parents=True)
    (run / ".retrieval-arena-output-owner").touch()
    common = [str(PWSH), "-NoProfile", "-File"]
    subprocess.run(common + [str(RA / "scripts/prepare-ragas-input.ps1"), "-Command", "judge",
                             "-Batch", str(folder / "batch.json"), "-Rubrics", str(folder / "rubrics.json"),
                             "-Artifact", str(folder / "config.json"), "-InputRoot", str(staged), "-NoCache"],
                   cwd=RA, check=True)
    started = time.time()
    done = subprocess.run(common + [str(RA / "scripts/run-ragas.ps1"), "-Command", "judge", "-InputRoot", str(staged),
                                    "-OutputRoot", str(run), "-Receipt", IMAGE_RECEIPT], cwd=RA)
    rs.write_json(OUT / "runs" / f"{args.set}-r{args.repeat}.wall.json",
                  {"exit_code": done.returncode, "wall_seconds": time.time() - started})
    print(run, done.returncode)


# ------------------------------------------------------------------ analysis

PARSED = {"ok", "no_statements"}
JUDGED = PARSED | {"parse_error", "judge_error", "input_too_large"}


def metric_values(scores, metric):
    """{(question_id, condition): (status, value)} for one metric over judged units."""
    out = {}
    for record in scores["records"]:
        qid, condition = record["unit"][0], record["unit"][1]
        out[(qid.removesuffix("::mismatched-reference"), condition)] = (record[metric]["status"], record[metric]["value"])
    return out


def parse_rate(values):
    judged = [s for s, _ in values.values() if s in JUDGED]
    return {"judged": len(judged), "parsed": sum(s in PARSED for s in judged),
            "rate": sum(s in PARSED for s in judged) / len(judged) if judged else None,
            "statuses": {s: sum(x == s for x, _ in values.values()) for s in sorted({x for x, _ in values.values()})}}


def separation(values):
    """Share of questions whose positive scores strictly above its negative; a missing score counts as a miss."""
    qids = sorted({q for q, c in values if c == "positive"})
    wins = sum(1 for q in qids if values[(q, "positive")][1] is not None and values[(q, "negative")][1] is not None
               and values[(q, "positive")][1] > values[(q, "negative")][1])
    return {"pairs": len(qids), "positive_above_negative": wins, "share": wins / len(qids) if qids else None}


def agreement(a, b):
    """Share of units with |r1 - r2| <= 0.1; a unit missing a score in either run counts as disagreement."""
    keys = sorted(set(a) | set(b))
    ok = [k for k in keys if a.get(k, (None, None))[1] is not None and b.get(k, (None, None))[1] is not None
          and abs(a[k][1] - b[k][1]) <= THRESHOLDS["repeat_delta_max"] + 1e-9]
    both = [k for k in keys if a.get(k, (None, None))[1] is not None and b.get(k, (None, None))[1] is not None]
    return {"units": len(keys), "both_scored": len(both), "within_0_1": len(ok), "share": len(ok) / len(keys) if keys else None,
            "mean_abs_delta": (sum(abs(a[k][1] - b[k][1]) for k in both) / len(both)) if both else None}


def seconds_per_unit(scores):
    vals = [r["measurements"]["latency_seconds"]["value"] for r in scores["records"]
            if r["measurements"]["latency_seconds"]["status"] == "available"]
    return {"units_timed": len(vals), "mean": sum(vals) / len(vals) if vals else None,
            "median": sorted(vals)[len(vals) // 2] if vals else None}


def analyse(args):
    runs_dir = OUT / "runs"
    result = {"schema_version": SCHEMA + ".qualification", "thresholds": THRESHOLDS, "plan_sha256": sha((OUT / "inputs/plan.json").read_bytes()),
              "conventions": ["parse rate = (ok + no_statements) / judged units; fixed states (empty, not applicable) excluded",
                              "separation and repeat agreement count a unit without a score as a failure",
                              "repeats are independent uncached runs (-NoCache) with the same seed and temperature 0"],
              "metrics": {}, "timing": {}, "runs": {}}
    loaded = {}
    for name in SETS:
        for rep in (1, 2):
            path = runs_dir / f"{name}-r{rep}" / "scores.json"
            if path.exists():
                loaded[(name, rep)] = read_json(path)
                receipt = read_json(runs_dir / f"{name}-r{rep}" / "receipt.json")
                wall = read_json(runs_dir / f"{name}-r{rep}.wall.json")
                result["runs"][f"{name}-r{rep}"] = {"status": receipt["status"], "image": receipt["image"],
                                                    "source_commit": receipt["source_commit"],
                                                    "scores_sha256": sha(path.read_bytes()), **wall}
    plan = {("controls-response", "faithfulness"), ("controls-response", "factual_correctness"),
            ("controls-reference", "context_recall"), ("real-tier1", "faithfulness"),
            ("real-tier1", "context_recall"), ("real-tier1", "factual_correctness")}
    for name, metric in sorted(plan):
        runs = [metric_values(loaded[(name, r)], metric) for r in (1, 2) if (name, r) in loaded]
        if not runs:
            continue
        row = {"parse_rate_by_run": [parse_rate(v) for v in runs]}
        judged = sum(p["judged"] for p in row["parse_rate_by_run"])
        row["parse_rate"] = sum(p["parsed"] for p in row["parse_rate_by_run"]) / judged if judged else None
        if name.startswith("controls"):
            row["separation_by_run"] = [separation(v) for v in runs]
        if len(runs) == 2:
            row["repeat"] = agreement(*runs)
        checks = {"parse": row["parse_rate"] is not None and row["parse_rate"] >= THRESHOLDS["parse_rate_min"]}
        if "repeat" in row:
            checks["repeat"] = row["repeat"]["share"] >= THRESHOLDS["repeat_share_min"]
        if name.startswith("controls") and metric in ("factual_correctness", "context_recall"):
            checks["separation"] = all(s["share"] >= THRESHOLDS["separation_min"] for s in row["separation_by_run"])
        row["checks"] = checks
        row["pass"] = all(checks.values()) and len(runs) == 2
        result["metrics"][f"{name}/{metric}"] = row
    for (name, rep), scores in sorted(loaded.items()):
        result["timing"][f"{name}-r{rep}"] = seconds_per_unit(scores)
    real = [result["timing"][k]["mean"] for k in result["timing"] if k.startswith("real-tier1") and result["timing"][k]["mean"]]
    if real:
        per_unit = sum(real) / len(real)
        result["projection_sprint19"] = {"answers": 1616, "seconds_per_answer_all_three_metrics": per_unit,
                                         "hours": 1616 * per_unit / 3600,
                                         "basis": "mean per-unit latency on the 40 real 12b answers, all three metrics"}
    rs.write_json(EVIDENCE, result)
    print(json.dumps({k: {"parse_rate": v["parse_rate"], "checks": v["checks"], "pass": v["pass"]}
                      for k, v in result["metrics"].items()}, indent=1))
    print(json.dumps(result.get("projection_sprint19"), indent=1))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    j = sub.add_parser("judge")
    j.add_argument("--set", required=True, choices=sorted(SETS))
    j.add_argument("--repeat", required=True, type=int, choices=(1, 2))
    sub.add_parser("analyse")
    args = parser.parse_args(argv)
    {"build": build, "judge": judge, "analyse": analyse}[args.command](args)


if __name__ == "__main__":
    main()

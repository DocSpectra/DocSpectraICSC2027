"""Sprint C2: Docker tier-2 pooled, LLM-verified qrels on baseline O (docs_scope_v1, 1,218 pages).

Thin driver: stages ICSC inputs and calls RA's pooled-support operator through
git/RetrievalArena/scripts/run_support.py. All logic (windows, generators, pool rule,
verifier, qrels, review packet) lives in RA (docs/evaluations/pooled_support.md).

Stages:
  pool            stage O + 142 questions + the three Sprint 15 O runs; run `pool` in the pinned embedding image
  verify-pilot    10 seeded questions (authorization: author "yes" October 12, cap $0.30)
  verify-full     all questions, extending the pilot version (authorization: author "yes", cap $5)
  summarize       evidence JSON for the qrels candidate (no model calls)
  review-packet   70-item stratified packet (35 verifier-yes, 35 verifier-no)
  import-review   reviewer responses -> precision and miss rate
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTEXT = HERE.parent
ROOT = CONTEXT.parents[1]
RA = ROOT / "git/RetrievalArena"
OUT = ROOT / "artifacts/c2-pooled-qrels"
FIXTURE = ROOT / "artifacts/docker-corpus-variants-v1/runs/wc-15-fixture-v1"
O_CORPUS = FIXTURE / "exports/O/corpus"  # docs_scope_v1 export, corpus tree ebce0bce..., 1,218 pages
O_RUNS = {name: FIXTURE / f"retrievers/O/{name}/output/predictions.jsonl" for name in ("lexical", "embedding", "graph_expand")}
DATASET = ROOT / "artifacts/docker-cikm-2026-05-14/dataset"
SUPPORT = ROOT / "artifacts/orientation-spectra-2026-10-06-docs-scope/support.json"  # the 142 tier-2 questions
MODEL = ROOT / "artifacts/docker-cikm-2026-05-14/model"
EMBED_IMAGE = "sha256:eac5614e9d9e887bf4457d2bd40396a1a338b95e4b3cdd3a3b681eaf1d511569"  # Sprint 15 pin
VERIFY_IMAGE = "retrieval-arena/ragas:icsc-c1"  # anthropic 1.13.0 in the C1 lock
CONFIGS = CONTEXT / "configs/c2"
PILOT_SEED, PILOT_N = "c2-pilot-v1", 10
REVIEW_SEED, REVIEW_PER_STRATUM = "c2-review-v1", 35


def queries():
    qtext = {r["question_id"]: r["question"] for r in map(json.loads, (DATASET / "questions.jsonl").read_text(encoding="utf-8").splitlines()) if r}
    answers = {r["question_id"]: r["answer"] for r in map(json.loads, (DATASET / "answers.jsonl").read_text(encoding="utf-8").splitlines()) if r}
    ids = sorted(json.loads(SUPPORT.read_text(encoding="utf-8")))
    return [{"question_id": q, "question": qtext[q], "answer": answers[q]} for q in ids]


def write_jsonl(path, rows):
    with Path(path).open("w", encoding="utf-8", newline="\n") as s:
        for r in rows:
            s.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


def fresh(path):
    path = Path(path)
    if path.exists():
        sys.exit(f"exists: {path}")
    path.mkdir(parents=True)
    return path


def launch(command, image, input_root, output_root, args, model_dir=None):
    (input_root / "operator-args.json").write_text(json.dumps(args, indent=1), encoding="utf-8")
    argv = [sys.executable, str(RA / "scripts/run_support.py"), command, "--image", image,
            "--input-root", str(input_root), "--output-root", str(output_root), "--args", str(input_root / "operator-args.json")]
    if model_dir:
        argv += ["--model-dir", str(model_dir)]
    code = subprocess.run(argv).returncode
    receipt = json.loads((output_root / "receipt.json").read_text(encoding="utf-8"))
    print(json.dumps({k: receipt.get(k) for k in ("status", "error", "result", "source_commit", "source_dirty")}, indent=1))
    return code


def stage_pool(a):
    inp = fresh(OUT / "pool-input")
    shutil.copytree(O_CORPUS, inp / "corpus")
    write_jsonl(inp / "queries.jsonl", queries())
    (inp / "runs").mkdir()
    for name, path in O_RUNS.items():
        shutil.copyfile(path, inp / "runs" / f"{name}.jsonl")
    shutil.copyfile(CONFIGS / "pool_config.json", inp / "pool_config.json")
    args = {"config": "pool_config.json", "corpus": "corpus", "queries": "queries.jsonl", "run_root": "runs", "model_dir": "/models/embedding"}
    return launch("pool", EMBED_IMAGE, inp, fresh(OUT / "pool"), args, model_dir=MODEL)


def stage_verify(name, query_ids, prior):
    inp = fresh(OUT / f"{name}-input")
    shutil.copytree(OUT / "pool", inp / "pool", ignore=shutil.ignore_patterns("receipt.json"))
    write_jsonl(inp / "queries.jsonl", queries())
    config = json.loads((CONFIGS / "verifier_config.json").read_text(encoding="utf-8"))
    config["spend_cap_usd"] = {"verify-pilot": 0.30, "verify-full": 5.0}[name]  # pilot cap: author "yes" Oct 12
    (inp / "verifier_config.json").write_text(json.dumps(config, indent=1), encoding="utf-8")
    if query_ids is not None:
        (inp / "query_ids.json").write_text(json.dumps(query_ids), encoding="utf-8")
    if prior:
        shutil.copytree(OUT / prior, inp / "prior", ignore=shutil.ignore_patterns("receipt.json", "cost-ledger.jsonl", "verify_state.json"))
    args = {"config": "verifier_config.json", "pool": "pool", "queries": "queries.jsonl", "prior": "prior" if prior else None,
            "query_ids": "query_ids.json" if query_ids is not None else None, "limit": None, "resume": None}
    return launch("verify", VERIFY_IMAGE, inp, fresh(OUT / name), args)


RELEASE = ("release_notes", "previous_versions")
EVIDENCE = CONTEXT / "evidence/c2-pooled-qrels-docker-tier2-2026-10-12.json"


def sha256(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ledger_cost(path):
    events = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l]
    rep = [e for e in events if e["event"] == "reported"]
    return {"reported_calls": len(rep), "reported_usd": sum(e["cost_usd"] for e in rep),
            "failed_calls": sum(e["event"] == "failed" for e in events), "stopped": any(e["event"] == "stopped" for e in events),
            "input_tokens": sum(e["usage"]["input_tokens"] for e in rep), "output_tokens": sum(e["usage"]["output_tokens"] for e in rep)}


def summarize():
    """Evidence for the tier-2 qrels candidate: identity, coverage, distribution, release-notes share, cost."""
    from collections import Counter
    full, pilot = OUT / "verify-full", OUT / "verify-pilot"
    manifest = json.loads((full / "qrels_manifest.json").read_text(encoding="utf-8"))
    qrels = [json.loads(l) for l in (full / "qrels.jsonl").read_text(encoding="utf-8").splitlines() if l]
    decisions = [json.loads(l) for l in (full / "page_decisions.jsonl").read_text(encoding="utf-8").splitlines() if l]
    judgments = [json.loads(l) for l in (full / "judgments.jsonl").read_text(encoding="utf-8").splitlines() if l]
    old = json.loads(SUPPORT.read_text(encoding="utf-8"))
    pos = [r for r in qrels if r["relevance"] == 1]
    per_q = Counter(r["query_id"] for r in pos)
    kept = manifest["ordered_query_ids"]
    old_top = [d for q in old for d in old[q]]
    supported_sources = Counter(s for d in decisions if d["decision"] == "supported" for s in d["sources"])
    only_runs = sum(1 for d in decisions if d["decision"] == "supported" and all(s.startswith("run:") for s in d["sources"]))
    pool_manifest = json.loads((OUT / "pool/pool_manifest.json").read_text(encoding="utf-8"))
    vals = sorted(per_q[q] for q in kept)
    evidence = {
        "schema_version": "docspectra.icsc.c2_pooled_qrels.v1", "created": "2026-10-12",
        "label": manifest["label"] + "; author spot-check pending",
        "corpus": {"id": pool_manifest["corpus_id"], "pages": pool_manifest["corpus_pages"],
                   "corpus_inventory_sha256": pool_manifest["corpus_inventory_sha256"], "export": O_CORPUS.relative_to(ROOT).as_posix()},
        "pool": {"rule": pool_manifest["pool_rule_id"], "config": pool_manifest["config"], "pool_sha256": pool_manifest["pool_sha256"],
                 "windows_sha256": pool_manifest["windows_sha256"], "runs": pool_manifest["runs"], "summary": pool_manifest["summary"],
                 "embed_image": EMBED_IMAGE},
        "verifier": {**manifest["verifier"], "prompt_version": manifest["prompt_version"], "prompt_sha256": manifest["prompt_sha256"],
                     "settings_sha256": manifest["settings_sha256"], "image": VERIFY_IMAGE},
        "qrels": {"version": manifest["qrels_version"], "qrels_sha256": manifest["qrels_sha256"],
                  "judgments_sha256": manifest["judgments_sha256"], "page_decisions_sha256": manifest["page_decisions_sha256"],
                  "qrels_manifest_sha256": sha256(full / "qrels_manifest.json"),
                  "judgment_provenance_sha256": manifest["judgment_provenance_sha256"],
                  "pilot_qrels_manifest_sha256": sha256(pilot / "qrels_manifest.json"), "location": full.relative_to(ROOT).as_posix()},
        "coverage": {"questions": manifest["counts"]["queries_selected"], "kept_with_verified_page": len(kept),
                     "dropped_no_verified_page": len(manifest["counts"]["queries_dropped_no_verified_page"]),
                     "dropped_ids": manifest["counts"]["queries_dropped_no_verified_page"],
                     "decisions": manifest["counts"]["decisions"], "qrels_rows": len(qrels), "qrels_positive": len(pos)},
        "verified_pages_per_kept_question": {"min": vals[0] if vals else None, "median": vals[len(vals) // 2] if vals else None,
                                             "mean": sum(vals) / len(vals) if vals else None, "max": vals[-1] if vals else None,
                                             "histogram": dict(sorted(Counter(vals).items()))},
        "release_notes_share": {
            "verified_pages": sum(any(x in r["support_target_id"] for x in RELEASE) for r in pos) / len(pos) if pos else None,
            "old_label_top5_pages": sum(any(x in d for x in RELEASE) for d in old_top) / len(old_top),
            "questions_with_release_notes_verified": sum(1 for q in kept if any(r["query_id"] == q and any(x in r["support_target_id"] for x in RELEASE) for r in pos)),
            "old_questions_with_release_notes_in_top5": sum(1 for q in old if any(any(x in d for x in RELEASE) for d in old[q]))},
        "old_label_overlap": {"verified_pages_in_old_top5": sum(1 for r in pos if r["support_target_id"] in old.get(r["query_id"], [])),
                              "verified_pages": len(pos)},
        "supported_by_source": dict(supported_sources), "supported_only_from_runs": only_runs,
        "judgment_statuses": dict(Counter(j["status"] for j in judgments)),
        "window_verdicts": dict(Counter(j["verdict"] for j in judgments)),
        "cost": {"pilot": ledger_cost(pilot / "cost-ledger.jsonl"), "full_extension": ledger_cost(full / "cost-ledger.jsonl")},
        "receipts": {"pool": sha256(OUT / "pool/receipt.json"), "pilot": sha256(pilot / "receipt.json"), "full": sha256(full / "receipt.json")},
    }
    evidence["cost"]["total_usd"] = evidence["cost"]["pilot"]["reported_usd"] + evidence["cost"]["full_extension"]["reported_usd"]
    EVIDENCE.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: evidence[k] for k in ("coverage", "verified_pages_per_kept_question", "release_notes_share", "cost")}, indent=1))
    return 0


def pilot_ids():
    return sorted(random.Random(PILOT_SEED).sample([q["question_id"] for q in queries()], PILOT_N))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["pool", "verify-pilot", "verify-full", "summarize", "review-packet", "import-review"])
    ap.add_argument("--responses", type=Path, help="import-review: the reviewer's responses JSON")
    a = ap.parse_args()
    if a.stage == "pool":
        return stage_pool(a)
    if a.stage == "verify-pilot":
        return stage_verify("verify-pilot", pilot_ids(), None)
    if a.stage == "verify-full":
        return stage_verify("verify-full", None, "verify-pilot")
    if a.stage == "summarize":
        return summarize()
    if a.stage == "review-packet":
        inp = fresh(OUT / "review-input")
        shutil.copytree(OUT / "verify-full", inp / "qrels", ignore=shutil.ignore_patterns("receipt.json"))
        shutil.copytree(OUT / "pool", inp / "pool", ignore=shutil.ignore_patterns("receipt.json"))
        write_jsonl(inp / "queries.jsonl", queries())
        args = {"qrels": "qrels", "pool": "pool", "queries": "queries.jsonl", "per_stratum": REVIEW_PER_STRATUM,
                "seed": REVIEW_SEED, "packet_id": "c2-docker-tier2-spotcheck-v1"}
        return launch("review-packet", VERIFY_IMAGE, inp, fresh(OUT / "review-packet"), args)
    inp = fresh(OUT / "import-input")
    for name in ("review_packet.json", "review_key.json"):
        shutil.copyfile(OUT / "review-packet" / name, inp / name)
    shutil.copyfile(a.responses, inp / "responses.json")
    args = {"packet": "review_packet.json", "key": "review_key.json", "responses": "responses.json"}
    return launch("import-review", VERIFY_IMAGE, inp, fresh(OUT / "review-result"), args)


if __name__ == "__main__":
    raise SystemExit(main())

"""Offline ingestion/condition/trace preflight for the frozen documentation agent.

Accepts a provider-only local fixture, never an evaluation packet. No model call.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AGENT = ROOT / "git/context/scripts/complex_task_agent_loop_v12.py"
PROMPT = ROOT / "git/context/design/complex-task-agentic-operator-prompt-v12.txt"
CONDITIONS = ((1, False), (1, True), (6, False), (6, True))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(dataset: Path, probe: str = "fixture navigation") -> dict:
    spec = importlib.util.spec_from_file_location("frozen_agent", AGENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    docs = module.Documentation(dataset)
    graph = dataset / "graph_edges.csv"
    with graph.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != len({(r["source"], r["target"]) for r in rows}):
        raise ValueError("duplicate-graph-edge")
    for row in rows:
        if row["source"] not in docs.lines or row["target"] not in docs.lines:
            raise ValueError("graph-endpoint-not-in-corpus")
    file_hashes = {p.stem: sha(p) for p in sorted((dataset / "corpus").glob("*.md"))}
    if file_hashes != docs.file_hashes:
        raise ValueError("agent-file-hash-mismatch")
    # Use only a neutral term, never a frozen evaluation question.
    found = docs.search(probe, max_files=6)
    if not found["hits"]:
        raise ValueError("fixture-search-empty")
    first = found["hits"][0]["doc_id"]
    read = docs.read(first, 1, 1)
    if read["file_sha256"] != file_hashes[first]:
        raise ValueError("read-file-hash-mismatch")
    if not read["lines"] or read["lines"][0]["line"] != 1:
        raise ValueError("citation-line-remap-failed")
    if read["lines"][0]["text"] != docs.lines[first][0][:2400]:
        raise ValueError("citation-text-remap-failed")
    matrix = [{"search_budget": searches, "follow_links": follow,
               "read_budget": 12, "model_call_budget": 20,
               "prompt_sha256": sha(PROMPT), "agent_code_sha256": sha(AGENT),
               "corpus_hashes_sha256": hashlib.sha256(json.dumps(file_hashes, sort_keys=True).encode()).hexdigest(),
               "graph_sha256": docs.graph_sha256}
              for searches, follow in CONDITIONS]
    if len({(x["search_budget"], x["follow_links"]) for x in matrix}) != 4:
        raise ValueError("condition-join-collision")
    # Replaying a synthetic observation against the same bytes is deterministic.
    replay = docs.read(first, 1, 1)
    if read != replay:
        raise ValueError("trace-observation-replay-drift")
    return {"schema_version": "docspectra.icsc.docker_variant_agent_preflight.v1",
            "status": "offline_fixture_passed", "dataset": str(dataset.resolve()),
            "condition_matrix": matrix, "synthetic_search_hit": first,
            "citation_remap": {"doc_id": first, "line": 1, "file_sha256": file_hashes[first]},
            "trace_replay": "exact_observation_match", "model_calls": 0,
            "grading_export_contract": {"blinded_key": ["question_id", "opaque_variant_id", "search_budget", "follow_links", "replicate"],
                                        "grader_fields": ["answer", "citations", "core_sufficiency", "caveats", "correctness", "unsupported_claims", "citation_support"],
                                        "private_key_location": "outside_editor_workspace"}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe", default="fixture navigation")
    args = parser.parse_args()
    report = run(args.dataset, args.probe)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "model_calls": 0, "receipt": str(args.output)}))


if __name__ == "__main__":
    main()

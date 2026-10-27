"""Sprint 24: retrospective LT retrieval trace audit over saved Sprint 19 artifacts. Zero model calls.

Stages (run from any directory; every write is write-once and refuses an existing output):
  plan      freeze the post hoc audit plan (evidence/lt-trace-audit-24-plan-v1-<date>.json)
  witness   guarded host process: reproduce the Sprint 20 evidence hash and its historical follow/landing values with
            the pinned helpers, and export the historical edge classes (mapped baseline vs editor-added) per variant
  prepare   build the RA operator input root (traces, exact variant corpora and graphs, edge classes, latest pooled
            labels, Sprint 22 graph bundles, the run's system message)
  run       RA `audit-trace-provenance` in the pinned verification image (network none)
  analyze   ICSC populations, reconciliation, chain tables, case cards, magnetic-view join -> tracked evidence
  verify    recompute the analysis byte for byte and re-hash every pinned input (preservation check)

Context owns the ICSC mapping, populations, policies and interpretation; the reusable trace parsing, joins and replay
are RA `trace_provenance_audit.v1`. The `witness` stage imports the Sprint 15/19/20 modules as libraries in a process
whose sockets and subprocess spawning are disabled and whose credential variables are removed; only their pure
context-building and metric functions are called (as the Sprint 20 and 21 reproductions did).
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
CONTEXT = ROOT / "git/context"
RA = ROOT / "git/RetrievalArena"
RUN = ROOT / "artifacts/docker-corpus-variants-v1/runs/wc-v1"
OUT = ROOT / "artifacts/lt-trace-audit-24-v1"
DATE = "2026-10-25"
EVIDENCE = CONTEXT / "evidence"
INVENTORY = EVIDENCE / "lt-trace-audit-24-planning-inventory-v1-2026-10-25.json"
PLAN = EVIDENCE / f"lt-trace-audit-24-plan-v1-{DATE}.json"
AMENDMENTS = (EVIDENCE / f"lt-trace-audit-24-plan-amendment-1-{DATE}.json",)
RESULT = EVIDENCE / f"lt-trace-audit-24-v1-{DATE}.json"
CARDS = EVIDENCE / f"lt-trace-audit-24-cards-v1-{DATE}.json"
TABLES = CONTEXT / f"reports/{DATE}-lt-retrieval-trace-audit-tables.md"
S20 = EVIDENCE / "wc-analysis-20-v1-2026-10-24.json"
REJUDGE = EVIDENCE / "wc-rejudge-19-result-v1-2026-10-24.json"
ANSWER_PLAN = EVIDENCE / "wc-answers-19-plan-v1-2026-10-22.json"
TIER1 = EVIDENCE / "wc-tier1-tasks-v2-2026-10-18.json"
KEY = RUN / "host-private/answers-19-key.json"         # opaque arm map; an experiment key, not a credential
LOOP = CONTEXT / "scripts/complex_task_agent_loop.py"
PROMPT = CONTEXT / "design/complex-task-agentic-operator-prompt-v12.txt"
BUNDLES = ROOT / "artifacts/normalized-magnetic-22-v1/inputs/wc/bundles"
S22_RECEIPTS = ROOT / "artifacts/normalized-magnetic-22-v1/runs/wc"
RA_COMMIT = "9b0f84f49d5dd8a5d6050fd5e41e6429adab4869"
RA_TREE = "3a31dfd54afef64d7526258ad00fe68bd9ad8d10"
IMAGE_RECEIPT = OUT / "image/verification-image-9b0f84f.json"
WITNESS = OUT / "witness"
OPERATOR_INPUT = OUT / "operator-input"
OPERATOR_OUTPUT = OUT / "operator-output"
ARMS = ("O", "S", "LT", "R0")
EDIT_ARMS = ("LT", "R0")
HIST = {"follows": {"O": 67, "S": 52, "LT": 76, "R0": 68}, "added": {"LT": 39, "R0": 20},
        "landing_mean_common105": {"O": 0.11428571428571428, "LT": 0.17142857142857143,
                                   "R0": 0.12380952380952381, "S": 0.09523809523809523}}
CASE_STRATA = {
    "a": "accepted added-edge follow with first page text exposure (no earlier returned lines or snippet line), a "
         "`supported` same-variant direct label for the destination, and a final-answer citation of the destination "
         "whose cited lines intersect the lines this follow returned",
    "b": "accepted added-edge follow whose destination page is not cited in the final answer",
    "c": "accepted follow with an attribution-breaking missing or ambiguous record: edge class unknown, replay not "
         "verified, file or text identity false, offered menu unknown, a tier-2 destination label of identity_mismatch, "
         "never_judged or undetermined, or a supported destination whose spans are all unmappable",
}
CASE_ORDER = "lexicographically first (tier, question_id, condition, replicate, call), per arm (LT, R0) and stratum"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pin(path):
    path = Path(path)
    data = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def jsonl(path):
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


def canonical_text(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, indent=1) + "\n"


def write_once(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    return pin(path)


def arm_of():
    return {oid: v["variant"] for oid, v in read_json(KEY)["variants"].items()}


def tool_schema_functions():
    """The function-calling tool instructions, read from the pinned loop source without importing it."""
    values = [node.value.value for node in ast.walk(ast.parse(LOOP.read_text(encoding="utf-8")))
              if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "tool_schema" for t in node.targets)
              and isinstance(node.value, ast.Constant)]
    if len(values) != 2 or not values[1].startswith("Call exactly one of the provided functions"):
        raise SystemExit("loop-tool-schema-not-found")
    return values[1]


def system_message():
    return PROMPT.read_text(encoding="utf-8") + "\n\n" + tool_schema_functions()


def replicate_of(path):
    return int(Path(path).stem.rsplit("-r", 1)[1])


# ------------------------------------------------------------------ plan

def excluded_attempts():
    rows = []
    for path in sorted((RUN / "answers-19/infrastructure-attempts").rglob("*")) + \
            sorted((RUN / "answers-19/superseded").rglob("*")):
        if path.is_file() and (path.suffix == ".json" or path.name.endswith(".events.jsonl")
                               or path.suffix == ".jsonl"):
            rows.append(pin(path))
    reasons = []
    for path in sorted((RUN / "answers-19").glob("commands-*.jsonl")):
        for r in jsonl(path):
            if r.get("event") in {"infrastructure_attempt_archive", "rate_limit_retry_archive"}:
                reasons.append({"log": path.name, "cell": r["cell"], "event": r["event"], "reason": r["reason"]})
    return {"files": rows, "archive_events": reasons}


def plan(_args):
    inv = read_json(INVENTORY)
    image = read_json(IMAGE_RECEIPT)
    if (image["source_commit"], image["source_tree"]) != (RA_COMMIT, RA_TREE):
        raise SystemExit("image-receipt-not-for-pinned-ra")
    labels = []
    for oid in sorted(arm_of()):
        base = RUN / "answers-19-pool" / oid
        labels += [pin(base / "pool-agents/pool.jsonl")] + [pin(base / "verify-escalated" / n) for n in (
            "page_decisions.jsonl", "judgments.jsonl", "qrels.jsonl", "qrels_manifest.json", "receipt.json")]
    bundles = [pin(BUNDLES / f"wc__{a}.json") for a in ARMS] + [pin(BUNDLES / f"wc__{a}__O-prime.json") for a in EDIT_ARMS]
    data = {
        "schema_version": "docspectra.icsc.lt_trace_audit_24.plan.v1",
        "created": DATE,
        "status": "post hoc retrospective analysis plan, frozen before the audit's chain summaries; not independent "
                  "confirmation or pre-registration. The audit question was chosen after the Sprint 19/20 results "
                  "were known.",
        "model_calls": 0, "network_calls": 0,
        "authority": "author request October 25 (Sprint 24 prompt); existing artifacts only",
        "pins": {
            "planning_inventory": pin(INVENTORY),
            "ra": {"commit": RA_COMMIT, "tree": RA_TREE, "module": "retrieval_arena/trace_provenance.py",
                   "module_sha256": sha(RA / "retrieval_arena/trace_provenance.py"),
                   "metric_version": "trace_provenance_audit.v1", "operator": "audit-trace-provenance"},
            "image_receipt": pin(IMAGE_RECEIPT), "image": image["image"],
            "driver": pin(Path(__file__)),
            "loop_source": pin(LOOP), "system_prompt": pin(PROMPT),
            "system_message_sha256": hashlib.sha256(system_message().encode("utf-8")).hexdigest(),
            "answer_plan": pin(ANSWER_PLAN), "rejudge": pin(REJUDGE), "s20": pin(S20), "tier1_tasks": pin(TIER1),
            "arm_key": pin(KEY), "labels_latest": labels, "sprint22_bundles": bundles,
            "pinned_historical_helpers": [pin(CONTEXT / "scripts" / n) for n in (
                "wc_analysis_20.py", "wc_answers_19.py", "wc_refinement_15.py", "wc_refine_17.py",
                "structure_forcing_12.py", "structure_forcing_12b.py", "lt_locality_13.py",
                "docker_corpus_variants.py", "restructuring_runs.py", "wc_freeze_16.py")],
        },
        "populations": {
            "active_traces": {"t1": inv["coverage"]["t1"]["trace_files"], "t2": inv["coverage"]["t2"]["trace_files"],
                              "source": "planning inventory trace_inventory (one row per active trace, bytes pinned)"},
            "t1": "15 tasks x 4 arms x {s1,s6} x {follow,no-follow} x 2 replicates; route and citation audit plus "
                  "tier-1 rubric page references by id (not exhaustive, never verified negatives); no tier-2 labels",
            "t2_descriptive": "all 111 FAQ questions x 4 arms x {follow, no-follow} x 1 replicate",
            "t2_comparability": "post-D-P5 common 105 (wc-rejudge-19-result common_set), labelled separately",
            "archived_excluded": excluded_attempts(),
            "keep_separate": ["tier", "search budget (s1/s6)", "follow condition", "arm", "replicate"],
        },
        "join_keys": {
            "trace": "run wc-v1, tier, opaque variant id, question id, question SHA-256 (declared = recorded = "
                     "recomputed), condition, replicate, trace SHA-256 (planning inventory)",
            "variant": "trace graph_sha256 = SHA-256 of the exported graph_edges.csv bytes; corpus_inventory_sha256 = "
                       "SHA-256 of the canonical {stem: file sha256}",
            "page": "returned file_sha256 = exported page bytes = pool page_sha256 = decision page_sha256",
            "edge_class": "historical Sprint 15 variant_context: added = variant edges minus mapped baseline edges "
                          "(O edges mapped through the derived page mapping); edges of the exported graph absent "
                          "from the table are unknown; new_page_incident = an endpoint outside the mapping image",
            "spectral_view": "Sprint 22 graph bundles wc/{O,S,LT,R0} (full and lwcc views by the unchanged RA "
                             "select_view) and LT/R0 O-prime (mapped baseline); node/arc-set comparison, both hash "
                             "namespaces reported; no eigensolve",
            "mismatch": "quarantined/unknown row with an explicit exclusion count",
        },
        "status_vocabulary": {
            "action": ["accepted", "executed_tool_error", "rejected", "indeterminate"],
            "edge_class": ["mapped_baseline", "added", "unknown", "not_an_edge"],
            "label": ["supported", "not_supported", "undetermined", "never_judged", "identity_mismatch",
                      "not_applicable"],
            "span_exposure": ["full", "partial", "none", "unmappable_only", "no_valid_span", "not_applicable"],
            "first_exposure": ["follow_only", "read_only", "mixed", "none"],
            "final_context": ["full", "partial", "none", "unknown"],
            "replay": ["verified", "unmatched", "not_reached"],
        },
        "definitions": {
            "novelty": {"listed_by_prior_search": "destination id in an earlier search's hits",
                        "first_returned_lines": "no earlier read/follow returned any line of the destination",
                        "first_page_text_exposure": "first_returned_lines and no earlier snippet line of it",
                        "new_lines_beyond_snippets": "returned lines not returned before and not shown as snippets"},
            "chain_t2_follow": ["planned follow-enabled traces", "traces with >=1 accepted follow",
                                "traces with >=1 accepted added-edge follow",
                                "... of which the follow is the destination's first page text exposure",
                                "... and the destination label is supported (same variant, page bytes joined)",
                                "... and a located supporting span lies in the lines that follow returned",
                                "... and the final answer cites the destination",
                                "... and the cited lines intersect the lines that follow returned"],
            "chain_units": "planned traces and unique question-destination pairs; follows are repeated observations "
                           "within a question, never independent replications",
            "opportunity": "per accepted follow decision, the share of offered menu arcs that are added; reported "
                           "beside the added share of uses, descriptively",
            "legacy_landing": "Sprint 20 follows_landing_on_support (all follow actions by name, verify-escalated "
                              "positive qrels, common 105, mean per question)",
            "success_landing": "accepted follows whose destination label is supported under the page-byte join, same "
                               "population and averaging",
        },
        "reconciliation_targets": {"s20_evidence_sha256": S20.with_suffix(".sha256").read_text(encoding="ascii").split()[0],
                                   **HIST, "note": "historical definition: wr.annotate_trace / navigation_metrics "
                                                   "count follow actions by name; reproduced with the pinned helpers "
                                                   "before success-qualified counts; old evidence is never rewritten"},
        "case_selection": {"strata": CASE_STRATA, "order": CASE_ORDER, "max_cards": 6,
                           "empty_stratum": "reported empty, never substituted",
                           "counterparts": "same question, condition and replicate in O and S; the arm's follow-off "
                                           "cell; shown without claiming they differ only in one link",
                           "nature": "outcome-stratified illustrations, not a representative sample or effect estimate"},
        "not_done": ["significance tests", "outcome-driven subgroup searches", "new semantic labels or scoring",
                     "O1 moved-content credit as direct support (named historical sensitivity only)",
                     "merging 09c automatic candidate-support labels with direct judgments",
                     "correlations between spectra and arm summaries"],
        "pre_freeze_observations": {
            "replay_probe": "a scratch reimplementation reproduced every recorded call_start and step history_sha256 "
                            "on the planning example trace (13 calls) and on 10 further traces with recorded "
                            "compactions (6 tier 1, 4 tier 2: the first in lexicographic order with a compaction "
                            "event); no chain summary was computed",
            "identity_smoke": "LT variant only: 4,666/4,666 pooled page hashes equal the exported file bytes; the "
                              "exported LT graph equals the Sprint 22 wc/LT bundle (1,823 nodes, 5,203 arcs, canonical "
                              "80548ca0...)",
            "ra_tests": "14 fixture tests on host; focused suite in image icsc-s24: 438 OK (skipped 2)",
        },
        "gap_matrix": "interpretive; authored after the results, from the recorded fields and coverage",
        "outputs": {"evidence": RESULT.relative_to(ROOT).as_posix(), "cards": CARDS.relative_to(ROOT).as_posix(),
                    "tables": TABLES.relative_to(ROOT).as_posix(),
                    "untracked": OUT.relative_to(ROOT).as_posix() + "/ (witness, operator input/output, receipts)"},
    }
    print(json.dumps(write_once(PLAN, canonical_text(data)), indent=1))


# ------------------------------------------------------------------ witness (guarded host process)

class _Blocked:
    def __init__(self, *_a, **_k):
        raise RuntimeError("sprint24-guard-subprocess")


def guard():
    import socket
    removed = sorted(k for k in os.environ if any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD")))
    for k in removed:
        os.environ.pop(k)

    def blocked(*_a, **_k):
        raise RuntimeError("sprint24-guard-network")
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    socket.getaddrinfo = blocked
    subprocess.Popen = _Blocked
    os.system = blocked
    return {"credential_variables_removed": len(removed), "sockets": "connect/connect_ex/create_connection/getaddrinfo "
            "raise", "subprocess": "Popen and os.system raise"}


def witness(_args):
    if WITNESS.exists():
        raise SystemExit("witness-exists")
    guard_record = guard()
    sys.path.insert(0, str(CONTEXT / "scripts"))
    import wc_analysis_20 as wa                       # noqa: E402  (pinned; library use only)
    wr, a19 = wa.wr, wa.a19
    cache = {}
    original = wa.variant_contexts

    def cached(ctx):
        if "v" not in cache:
            cache["v"] = original(ctx)
        return cache["v"]
    wa.variant_contexts = cached
    data = wa.compute()
    recomputed = hashlib.sha256(wa.canonical(data).encode("utf-8")).hexdigest()
    recorded = S20.with_suffix(".sha256").read_text(encoding="ascii").split()[0]
    ctx = wa.context()
    vctxs = cached(ctx)
    WITNESS.mkdir(parents=True)
    summary = {"guard": guard_record, "s20_recomputed_sha256": recomputed, "s20_recorded_sha256": recorded,
               "s20_reproduced": recomputed == recorded, "navigation_shares": data["navigation_shares"],
               "variants": {}}
    pos_esc = {}
    for oid, arm in sorted(ctx["arm_of"].items()):
        v = vctxs[oid]
        pos_esc[oid] = wa.qrels(oid, "verify-escalated")[0]
        mapped = set(map(tuple, v["o_mapped"]))
        added = set(map(tuple, v["added_edges"]))
        vedges = set(map(tuple, v["v_edges"]))
        receipt = v["receipt"]
        new_pages = set(receipt["added"]) if isinstance(receipt.get("added"), list) else None   # amendment 1
        rows = []
        for s, t in sorted(vedges):
            cls = "added" if (s, t) in added else "mapped_baseline" if (s, t) in mapped else "unclassified"
            rows.append({"source": s, "target": t, "class": cls,
                         "new_page_incident": None if new_pages is None else (s in new_pages or t in new_pages)})
        export = RUN / "answers-19-export/variants" / oid
        with (export / "graph_edges.csv").open(newline="", encoding="utf-8") as stream:
            export_edges = {(r["source"], r["target"]) for r in csv.DictReader(stream)}
        stems = {p.stem for p in (export / "corpus").glob("*.md")}
        path = WITNESS / f"edges-{oid}.jsonl"
        path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows if r["class"] != "unclassified"),
                        encoding="utf-8", newline="\n")
        legacy = []
        for tier in ("1", "2"):
            for tp_path in sorted((a19.OUT / f"t{tier}").glob(f"{oid}-*.json")):
                t = read_json(tp_path)
                f = wr.annotate_trace(t, v)
                row = {"tier": f"t{tier}", "trace": tp_path.name, "question_id": t["question_id"],
                       "follow_links": t["follow_links"],
                       "follows": [{"call": x["call"], "source": x["source"], "target": x["target"],
                                    "editor_added_edge": x["editor_added_edge"]} for x in f["follows"]]}
                if tier == "2" and t["follow_links"] and t["question_id"] in set(ctx["common105"]):
                    ids = sorted(pos_esc[oid][t["question_id"]])
                    nm = wr.navigation_metrics(t, ids, dict(v, equivalence={d: [d] for d in ids}))
                    row["primary_follows_landing_on_support"] = nm["follows_landing_on_support"]
                legacy.append(row)
        lpath = WITNESS / f"legacy-{oid}.jsonl"
        lpath.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in legacy), encoding="utf-8", newline="\n")
        land = [r["primary_follows_landing_on_support"] for r in legacy if "primary_follows_landing_on_support" in r]
        summary["variants"][oid] = {
            "arm": arm, "variant_nodes": len(v["nodes"]), "variant_edges": len(vedges), "mapped_baseline_edges": len(
                vedges & mapped), "added_edges": len(added), "unclassified": sum(r["class"] == "unclassified" for r in rows),
            "new_page_incident_added": sum(r["class"] == "added" and bool(r["new_page_incident"]) for r in rows),
            "new_pages": None if new_pages is None else len(new_pages),
            "new_page_incident_mapped": sum(r["class"] == "mapped_baseline" and bool(r["new_page_incident"]) for r in rows),
            "nodes_equal_export_stems": set(v["nodes"]) == stems,
            "edges_equal_export_graph": vedges == export_edges,
            "edges_only_variant_context": len(vedges - export_edges), "edges_only_export": len(export_edges - vedges),
            "legacy_follows_t2_follow": sum(len(r["follows"]) for r in legacy if r["tier"] == "t2" and r["follow_links"]),
            "legacy_added_t2_follow": sum(x["editor_added_edge"] for r in legacy if r["tier"] == "t2" and r["follow_links"]
                                          for x in r["follows"]),
            "legacy_landing_traces_common105": len(land),
            "legacy_landing_mean_common105": sum(land) / len(land) if land else None,
            "edges_file": pin(path), "legacy_file": pin(lpath)}
    (WITNESS / "witness.json").write_text(canonical_text(summary), encoding="utf-8", newline="\n")
    print(json.dumps({oid: {k: x for k, x in s.items() if not isinstance(x, dict)}
                      for oid, s in summary["variants"].items()}, indent=1))
    print("s20 reproduced:", summary["s20_reproduced"])


# ------------------------------------------------------------------ prepare and run

def inventory(root):
    rows = []
    for path in root.rglob("*"):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            data = path.read_bytes()
            rows.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)})
    rows.sort(key=lambda r: r["path"])               # container Python 3.12 orders POSIX paths by string
    return rows


def prepare(_args):
    if OPERATOR_INPUT.exists():
        raise SystemExit("operator-input-exists")
    inv = read_json(INVENTORY)
    arms = arm_of()
    oids = {a: o for o, a in arms.items()}
    questions = {q["question_id"]: q["question"] for t in ("1", "2") for q in read_json(ANSWER_PLAN)["questions"][t]}
    OPERATOR_INPUT.mkdir(parents=True)
    (OPERATOR_INPUT / "system.txt").write_bytes(system_message().encode("utf-8"))
    variants = []
    for oid in sorted(arms):
        arm = arms[oid]
        dst = OPERATOR_INPUT / "variants" / oid
        shutil.copytree(RUN / "answers-19-export/variants" / oid / "corpus", dst / "corpus")
        shutil.copy2(RUN / "answers-19-export/variants" / oid / "graph_edges.csv", dst / "graph_edges.csv")
        shutil.copy2(WITNESS / f"edges-{oid}.jsonl", dst / "edge_classes.jsonl")
        lab = OPERATOR_INPUT / "labels" / oid
        lab.mkdir(parents=True)
        base = RUN / "answers-19-pool" / oid
        shutil.copy2(base / "pool-agents/pool.jsonl", lab / "pool.jsonl")
        for n in ("page_decisions.jsonl", "judgments.jsonl", "qrels.jsonl"):
            shutil.copy2(base / "verify-escalated" / n, lab / n)
        views = [(f"wc/{arm}", f"wc__{arm}.json")] + ([(f"wc/{arm}/O-prime", f"wc__{arm}__O-prime.json")]
                                                      if arm in EDIT_ARMS else [])
        vdir = OPERATOR_INPUT / "views"
        vdir.mkdir(exist_ok=True)
        view_cfg = []
        for gid, name in views:
            shutil.copy2(BUNDLES / name, vdir / name)
            view_cfg += [{"view_id": f"{gid}#full", "bundle": f"views/{name}", "view": {"kind": "full"}},
                         {"view_id": f"{gid}#lwcc", "bundle": f"views/{name}", "view": {"kind": "lwcc"}}]
        variants.append({"variant_id": oid, "corpus": f"variants/{oid}/corpus", "graph": f"variants/{oid}/graph_edges.csv",
                         "edge_classes": f"variants/{oid}/edge_classes.jsonl",
                         "labels": {"pool": f"labels/{oid}/pool.jsonl", "page_decisions": f"labels/{oid}/page_decisions.jsonl",
                                    "judgments": f"labels/{oid}/judgments.jsonl", "qrels": f"labels/{oid}/qrels.jsonl"},
                         "views": view_cfg})
    cells = []
    for row in inv["trace_inventory"]:
        src = ROOT / row["path"]
        tdir = OPERATOR_INPUT / "traces" / row["tier"]
        tdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, tdir / src.name)
        events = src.with_name(src.stem + ".events.jsonl")
        shutil.copy2(events, tdir / events.name)
        q = questions[row["question_id"]]
        cells.append({"cell_id": src.stem, "tier": row["tier"], "variant_id": row["variant"],
                      "question_id": row["question_id"],
                      "question_sha256": hashlib.sha256(q.encode("utf-8")).hexdigest(),
                      "condition": row["condition"], "replicate": replicate_of(src),
                      "follow_links": row["condition"].endswith("-follow") and not row["condition"].endswith("no-follow"),
                      "trace": f"traces/{row['tier']}/{src.name}", "events": f"traces/{row['tier']}/{events.name}",
                      "trace_sha256": row["sha256"], "labels": row["tier"] == "t2"})
    config = {"schema_version": "retrieval_arena.trace_provenance_config.v1",
              "dialect": "docspectra_complex_task_agent_trace_v2", "system_message": "system.txt",
              "variants": variants, "cells": cells}
    (OPERATOR_INPUT / "config.json").write_bytes(json.dumps(config, sort_keys=True, indent=1).encode("utf-8") + b"\n")
    rows = inventory(OPERATOR_INPUT)
    blob = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    request = {"schema_version": "retrieval_arena.trace_provenance_operator_request.v1",
               "command": "audit-trace-provenance", "config_path": "config.json", "input_inventory": rows,
               "input_inventory_sha256": hashlib.sha256(blob).hexdigest()}
    (OPERATOR_INPUT / "operator-request.json").write_bytes(json.dumps(request, sort_keys=True).encode("utf-8") + b"\n")
    print(json.dumps({"cells": len(cells), "files": len(rows), "bytes": sum(r["size_bytes"] for r in rows),
                      "input_inventory_sha256": request["input_inventory_sha256"]}, indent=1))


def run(_args):
    if OPERATOR_OUTPUT.exists():
        raise SystemExit("operator-output-exists")
    OPERATOR_OUTPUT.mkdir(parents=True)
    (OPERATOR_OUTPUT / ".retrieval-arena-output-owner").write_bytes(b"retrieval_arena.output_root.v1\n")
    cmd = ["pwsh", "-NoProfile", "-File", str(RA / "scripts/run-verification.ps1"), "audit-trace-provenance",
           "-InputRoot", str(OPERATOR_INPUT), "-OutputRoot", str(OPERATOR_OUTPUT), "-Receipt", str(IMAGE_RECEIPT)]
    with (OUT / "operator-run.log").open("w", encoding="utf-8") as log:
        code = subprocess.run(cmd, cwd=RA, stdout=log, stderr=subprocess.STDOUT).returncode
    print(json.dumps({"exit": code, "receipt": read_json(OPERATOR_OUTPUT / "operator_receipt.v1.json")["status"]}))
    raise SystemExit(code)


# ------------------------------------------------------------------ analysis

def frac(a, b):
    return None if not b else a / b


def load_outputs():
    o = OPERATOR_OUTPUT
    return (jsonl(o / "trace_provenance.traces.jsonl"), jsonl(o / "trace_provenance.actions.jsonl"),
            jsonl(o / "trace_provenance.citations.jsonl"), read_json(o / "trace_provenance.variants.json"))


def group(row, arms):
    return f"{row['tier']}/{arms[row['variant_id']]}/{row['condition']}"


def rubric_pages():
    return {t["task_id"]: set(t.get("support_page_ids") or []) for t in read_json(TIER1)["tasks"]}


def coverage_tables(traces, actions, citations, arms, keep=None):
    """By tier/arm/condition. `keep`: optional set of question ids (tier 2 comparability view)."""
    def ok(r):
        return keep is None or r["tier"] != "t2" or r["question_id"] in keep
    out = defaultdict(lambda: defaultdict(Counter))
    for t in traces:
        if not ok(t):
            continue
        g = out[group(t, arms)]
        g["traces"]["planned"] += 1
        for f in ("identity_ok", "terminal", "replay", "final_context", "zero_follow"):
            g["traces"][f"{f}={t.get(f)}"] += 1
        g["traces"]["compacted"] += bool(t.get("compaction_retained_reads"))
    seen_pairs = defaultdict(set)
    for a in actions:
        if not ok(a):
            continue
        g = out[group(a, arms)]
        g["actions"][f"{a['action']}:{a['status']}"] += 1
        if a["action"] != "follow":
            continue
        if a["status"] != "accepted":
            g["follow_rejected_errors"][str(a.get("error"))] += 1
            continue
        f = g["follow_accepted"]
        f["n"] += 1
        for k in ("edge_class", "new_page_incident", "source_opened_before", "edge_in_menu", "menu_status",
                  "listed_by_prior_search", "first_returned_lines", "first_page_text_exposure", "label",
                  "span_exposure_this_call", "span_exposure_new_lines", "returned_lines_in_final_context",
                  "span_exposure_final_context", "file_identity", "text_identity", "recorded_newly_observed_agrees",
                  "legacy_qrels_positive"):
            if k in a:
                f[f"{k}={a[k]}"] += 1
        if a.get("edge_class") == "added":
            for k in ("first_page_text_exposure", "first_returned_lines", "label", "span_exposure_this_call",
                      "returned_lines_in_final_context", "new_page_incident"):
                if k in a:
                    g["added_follow"][f"{k}={a[k]}"] += 1
            g["added_follow"]["n"] += 1
        if a.get("menu_edges"):
            g["opportunity"]["decisions"] += 1
            g["opportunity_sum"]["menu_added_share_x1e6"] += round(1e6 * a["menu_added_edges"] / a["menu_edges"])
            g["opportunity_sum"]["menu_edges"] += a["menu_edges"]
            g["opportunity_sum"]["menu_added_edges"] += a["menu_added_edges"]
            g["opportunity_sum"]["menu_unknown_edges"] += a["menu_unknown_edges"]
        key = (a["question_id"], a["doc_id"])
        pairs = seen_pairs[group(a, arms)]
        if key not in pairs:
            pairs.add(key)
            g["unique_q_dest"]["pairs"] += 1
            g["unique_q_dest"][f"first_follow_edge_class={a.get('edge_class')}"] += 1
            g["unique_q_dest"][f"label={a.get('label')}"] += 1
    rub = rubric_pages()
    for c in citations:
        if not ok(c):
            continue
        g = out[group(c, arms)]
        for k in ("first_exposure", "page_reached_by_follow", "page_reached_by_read", "page_first_route",
                  "page_listed_by_search", "final_context", "label", "span_overlap_cited", "cited_lines_returned"):
            if k in c:
                g[f"citation_{c['kind']}"][f"{k}={c[k]}"] += 1
        g[f"citation_{c['kind']}"]["n"] += 1
        if c["kind"] == "answer" and "added" in c.get("first_exposing_follow_edge_classes", []):
            g["citation_answer"][f"added_edge_first_exposure_{c['first_exposure']}"] += 1
        if c["tier"] == "t1" and c["kind"] == "answer":
            g["citation_answer"][f"t1_rubric_page_by_id={c['doc_id'] in rub.get(c['question_id'], set())}"] += 1
    plain = {}
    for k, g in sorted(out.items()):
        plain[k] = {sec: dict(sorted(c.items())) for sec, c in sorted(g.items())}
        opp = plain[k].get("opportunity_sum")
        if opp:
            dec = plain[k]["opportunity"]["decisions"]
            plain[k]["opportunity_summary"] = {
                "follow_decisions_with_menu": dec,
                "mean_menu_added_share": opp["menu_added_share_x1e6"] / 1e6 / dec,
                "pooled_menu_added_share": frac(opp["menu_added_edges"], opp["menu_edges"]),
                "added_share_of_accepted_follows": frac(plain[k]["follow_accepted"].get("edge_class=added", 0),
                                                        plain[k]["follow_accepted"]["n"])}
    return plain


def chain(traces, actions, citations, arms, keep=None):
    """Tier-2 follow-condition chain, step by step, over planned traces and unique question-destination pairs."""
    cites = defaultdict(list)
    for c in citations:
        if c["kind"] == "answer":
            cites[c["cell_id"]].append(c)
    out = {}
    for arm in ARMS:
        cells = [t for t in traces if t["tier"] == "t2" and arms[t["variant_id"]] == arm
                 and t["condition"] == "s1-follow" and (keep is None or t["question_id"] in keep)]
        ids = {t["cell_id"] for t in cells}
        steps = Counter()
        pairs = defaultdict(set)
        by_cell = defaultdict(list)
        for a in actions:
            if a["cell_id"] in ids and a["action"] == "follow" and a["status"] == "accepted":
                by_cell[a["cell_id"]].append(a)
        for t in cells:
            steps["0_planned_traces"] += 1
            fs = by_cell.get(t["cell_id"], [])
            levels = []
            if fs:
                levels.append("1_any_accepted_follow")
            for f in fs:
                if f.get("edge_class") != "added":
                    continue
                q = (t["question_id"], f["doc_id"])
                reached = ["2_added_edge_follow"]
                if f.get("first_page_text_exposure"):
                    reached.append("3_first_page_text_exposure")
                    if f.get("label") == "supported":
                        reached.append("4_destination_supported")
                        if f.get("span_exposure_this_call") in ("full", "partial"):
                            reached.append("5_span_in_returned_lines")
                        shown = {n for a_, b_ in f["returned_ranges"] for n in range(a_, b_ + 1)}
                        dest_cites = [c for c in cites.get(t["cell_id"], []) if c["doc_id"] == f["doc_id"]]
                        if dest_cites:
                            reached.append("6_destination_cited")
                            if any(set(range(c["start_line"], c["end_line"] + 1)) & shown for c in dest_cites):
                                reached.append("7_cited_lines_from_this_follow")
                for r in reached:
                    pairs[r].add(q)
                levels.extend(reached)
            for r in set(levels):
                steps[r] += 1
        out[arm] = {"traces": dict(sorted(steps.items())), "unique_question_destination_pairs":
                    {k: len(v) for k, v in sorted(pairs.items())},
                    "note": "step 5 is reported beside, not inside, steps 6-7 (a citation can follow without a "
                            "located span)"}
    return out


def reconcile(traces, actions, arms, witness_summary, keep105):
    out = {"s20_evidence_reproduced": witness_summary["s20_reproduced"],
           "s20_recomputed_sha256": witness_summary["s20_recomputed_sha256"], "arms": {}}
    shares = witness_summary["navigation_shares"]
    for oid, arm in sorted(arms.items()):
        w = witness_summary["variants"][oid]
        rows = [a for a in actions if a["variant_id"] == oid and a["tier"] == "t2" and a["condition"] == "s1-follow"
                and a["action"] == "follow"]
        acc = [a for a in rows if a["status"] == "accepted"]
        landing = defaultdict(int)
        for a in acc:
            if a["question_id"] in keep105 and a.get("label") == "supported":
                landing[a["question_id"]] += 1
        traces105 = [t for t in traces if t["variant_id"] == oid and t["tier"] == "t2" and t["condition"] == "s1-follow"
                     and t["question_id"] in keep105]
        success_mean = sum(landing[t["question_id"]] for t in traces105) / len(traces105)
        out["arms"][arm] = {
            "historical_recorded": {"follows": HIST["follows"][arm], "added": HIST["added"].get(arm, 0),
                                    "landing_mean_common105": HIST["landing_mean_common105"][arm]},
            "s20_recomputed_navigation_share": shares.get(f"t2/{arm}/s1-follow"),
            "witness_legacy": {"follows": w["legacy_follows_t2_follow"], "added": w["legacy_added_t2_follow"],
                               "landing_mean_common105": w["legacy_landing_mean_common105"],
                               "landing_traces": w["legacy_landing_traces_common105"]},
            "audit_attempted_follows": len(rows),
            "audit_by_status": dict(Counter(a["status"] for a in rows)),
            "audit_rejected_errors": dict(Counter(a.get("error") for a in rows if a["status"] != "accepted")),
            "audit_attempted_added_by_historical_class": sum(a.get("edge_class") == "added" for a in rows),
            "audit_accepted_added": sum(a.get("edge_class") == "added" for a in acc),
            "audit_accepted_edge_classes": dict(Counter(a.get("edge_class") for a in acc)),
            "success_qualified_landing_mean_common105": success_mean,
            "legacy_vs_page_byte_label_on_accepted_follows": dict(Counter(
                f"qrels_positive={a.get('legacy_qrels_positive')}/label={a.get('label')}" for a in acc)),
        }
        r = out["arms"][arm]
        r["legacy_reproduced"] = (r["witness_legacy"]["follows"] == r["historical_recorded"]["follows"] and
                                  r["witness_legacy"]["added"] == r["historical_recorded"]["added"] and
                                  abs((r["witness_legacy"]["landing_mean_common105"] or 0) -
                                      r["historical_recorded"]["landing_mean_common105"]) < 1e-12)
        r["attempted_equals_legacy_count"] = len(rows) == r["witness_legacy"]["follows"]
    return out


def magnetic_join(actions, arms, variants):
    out = {}
    vrec = {v["variant_id"]: v for v in variants}
    for oid, arm in sorted(arms.items()):
        acc = [a for a in actions if a["variant_id"] == oid and a["action"] == "follow" and a["status"] == "accepted"]
        views = Counter()
        for a in acc:
            for vid, m in (a.get("views") or {}).items():
                views[f"{vid}|edge_class={a.get('edge_class')}|edge_in_view={m['edge_in_view']}"] += 1
                views[f"{vid}|edge_in_view={m['edge_in_view']}"] += 1
        out[arm] = {"accepted_follows_all_tiers": len(acc), "membership": dict(sorted(views.items())),
                    "graph_identity": vrec[oid]["graph_identity"],
                    "views": [{"view_id": v["view_id"], "graph_id": v["graph_id"],
                               "bundle_canonical_graph_sha256": v["bundle_canonical_graph_sha256"],
                               "nodes": v["selection"]["nodes"], "edges": v["selection"]["edges"],
                               "view_graph_sha256": v["selection"]["view_graph_sha256"]} for v in vrec[oid]["views"]],
                    "edge_classes": vrec[oid]["edge_classes"]}
    return out


def excerpt(lines, ranges, limit=12):
    out = []
    for a, b in ranges:
        for n in range(a, b + 1):
            if len(out) >= limit:
                return out
            out.append({"line": n, "text": lines[n - 1][:240] if 0 < n <= len(lines) else None})
    return out


def cards(traces, actions, citations, arms):
    by_cell = {t["cell_id"]: t for t in traces}
    cites = defaultdict(list)
    for c in citations:
        cites[c["cell_id"]].append(c)
    order = lambda a: (a["tier"], a["question_id"], a["condition"], a["replicate"], a["call"])   # noqa: E731
    acc = sorted((a for a in actions if a["action"] == "follow" and a["status"] == "accepted"), key=order)

    def answer_cites(a):
        return [c for c in cites.get(a["cell_id"], []) if c["kind"] == "answer" and c["doc_id"] == a["doc_id"]]

    def is_a(a):
        if a.get("edge_class") != "added" or not a.get("first_page_text_exposure") or a.get("label") != "supported":
            return False
        shown = {n for x, y in a["returned_ranges"] for n in range(x, y + 1)}
        return any(set(range(c["start_line"], c["end_line"] + 1)) & shown for c in answer_cites(a))

    def is_b(a):
        return a.get("edge_class") == "added" and not answer_cites(a)

    def reasons_c(a):
        r = []
        if a.get("edge_class") == "unknown":
            r.append("edge_class_unknown")
        if a.get("replay") != "verified":
            r.append("replay_not_verified")
        if a.get("file_identity") is False or a.get("text_identity") is False:
            r.append("page_identity_false")
        if a.get("menu_status") != "reconstructed":
            r.append("menu_unknown")
        if a["tier"] == "t2" and a.get("label") in ("identity_mismatch", "never_judged", "undetermined"):
            r.append(f"label_{a['label']}")
        if a.get("label") == "supported" and a.get("span_exposure_this_call") == "unmappable_only":
            r.append("spans_unmappable")
        return r
    rules = {"a": is_a, "b": is_b, "c": lambda a: bool(reasons_c(a))}
    stratum_counts = {arm: {s: sum(1 for a in acc if arms[a["variant_id"]] == arm and rules[s](a)) for s in rules}
                      for arm in EDIT_ARMS}
    selected = []
    for arm in EDIT_ARMS:
        for s, rule in rules.items():
            hit = next((a for a in acc if arms[a["variant_id"]] == arm and rule(a)), None)
            selected.append((arm, s, hit, reasons_c(hit) if hit and s == "c" else None))
    out = []
    corpora = {}
    for arm, s, a, reasons in selected:
        if a is None:
            out.append({"arm": arm, "stratum": s, "rule": CASE_STRATA[s], "empty": True})
            continue
        oid = a["variant_id"]
        if oid not in corpora:
            corpora[oid] = {p.stem: p.read_text(encoding="utf-8").splitlines()
                            for p in (RUN / "answers-19-export/variants" / oid / "corpus").glob("*.md")}
        lines = corpora[oid]
        trace = read_json(RUN / "answers-19" / a["tier"] / f"{a['cell_id']}.json")
        path = [{"call": x["call"], "action": x["action"], "status": x["status"], "doc_id": x.get("doc_id"),
                 "source_doc_id": x.get("source_doc_id"), "query": x.get("query"), "edge_class": x.get("edge_class"),
                 "returned_ranges": x.get("returned_ranges"), "first_page_text_exposure": x.get("first_page_text_exposure"),
                 "label": x.get("label"), "error": x.get("error")}
                for x in actions if x["cell_id"] == a["cell_id"]]
        dest_cites = [c for c in cites.get(a["cell_id"], []) if c["doc_id"] == a["doc_id"]]
        counterparts = []
        for t in traces:
            if (t["tier"], t["question_id"], t["replicate"]) != (a["tier"], a["question_id"], a["replicate"]):
                continue
            same_cond = t["condition"] == a["condition"]
            off = t["condition"] == a["condition"].replace("-follow", "-no-follow") and t["variant_id"] == oid
            if (same_cond and arms[t["variant_id"]] in ("O", "S")) or off:
                counterparts.append({"arm": arms[t["variant_id"]], "condition": t["condition"], "cell_id": t["cell_id"],
                                     "terminal": t["terminal"], "follows_accepted": t["follows_accepted"],
                                     "cited_pages": sorted({c["doc_id"] for c in cites.get(t["cell_id"], [])
                                                            if c["kind"] == "answer"}),
                                     "destination_cited": any(c["doc_id"] == a["doc_id"] for c in cites.get(t["cell_id"], [])
                                                              if c["kind"] == "answer"),
                                     "trace_sha256": t["trace_sha256"]})
        score = None
        if a["tier"] == "t2":
            sp = RUN / "scoring-19/ragas-t2" / oid / "run/scores.json"
            rec = next((r for r in read_json(sp)["records"] if r["unit"][0] == a["question_id"]
                        and r["unit"][1] == a["condition"]), None)
            score = {"source": pin(sp), "factual_correctness": rec["factual_correctness"] if rec else None,
                     "unit": rec["unit"] if rec else None}
        else:
            score = {"source": "artifacts/docker-corpus-variants-v1/runs/wc-v1/scoring-19/luna-t1 (tier-1 Luna rubric "
                               "scores; not joined by this audit)"}
        out.append({"arm": arm, "stratum": s, "rule": CASE_STRATA[s], "empty": False,
                    "cell_id": a["cell_id"], "tier": a["tier"], "question_id": a["question_id"],
                    "question": trace["question"], "condition": a["condition"], "replicate": a["replicate"],
                    "call": a["call"], "trace": pin(RUN / "answers-19" / a["tier"] / f"{a['cell_id']}.json"),
                    "attribution_breaks": reasons,
                    "follow": {k: a.get(k) for k in ("source_doc_id", "doc_id", "edge_class", "new_page_incident",
                                                     "source_opened_before", "menu_edges", "menu_added_edges",
                                                     "listed_by_prior_search", "snippet_lines_prior",
                                                     "first_page_text_exposure", "returned_ranges", "returned_text_sha256",
                                                     "file_sha256", "label", "legacy_qrels_positive", "spans",
                                                     "spans_mapped", "span_exposure_this_call",
                                                     "returned_lines_in_final_context", "span_exposure_final_context",
                                                     "views")},
                    "returned_excerpt": excerpt(lines.get(a["doc_id"], []), a["returned_ranges"]),
                    "destination_citations": [{k: c.get(k) for k in ("kind", "start_line", "end_line", "first_exposure",
                                                                     "final_context", "span_overlap_cited")}
                                              | {"excerpt": excerpt(lines.get(a["doc_id"], []),
                                                                    [[c["start_line"], c["end_line"]]], 6)}
                                              for c in dest_cites],
                    "event_path": path, "terminal": by_cell[a["cell_id"]]["terminal"],
                    "answer_text": (trace.get("answer") or {}).get("text"), "existing_score": score,
                    "counterparts": counterparts,
                    "caveat": "outcome-stratified illustration; exposure, label overlap and citation do not establish "
                              "reliance, necessity of the link or answer improvement; counterparts differ in more "
                              "than one link"})
    return {"strata": CASE_STRATA, "order": CASE_ORDER, "stratum_sizes": stratum_counts, "cards": out}


def compute():
    plan_pin = pin(PLAN)
    arms = arm_of()
    traces, actions, citations, variants = load_outputs()
    w = read_json(WITNESS / "witness.json")
    keep105 = set(read_json(REJUDGE)["common_set"]["question_ids"])
    receipt = read_json(OPERATOR_OUTPUT / "operator_receipt.v1.json")
    manifest = read_json(OPERATOR_OUTPUT / "trace_provenance_manifest.json")
    outputs = {p.name: pin(p) for p in sorted(OPERATOR_OUTPUT.iterdir()) if p.is_file() and not p.name.startswith(".")}
    quarantined = [t for t in traces if t.get("quarantined")]
    card_data = cards(traces, actions, citations, arms)
    result = {
        "schema_version": "docspectra.icsc.lt_trace_audit_24.v1", "created": DATE, "model_calls": 0,
        "network_calls": 0, "plan": plan_pin, "plan_amendments": [pin(a) for a in AMENDMENTS],
        "scope": "retrospective process audit of saved Sprint 19 traces; descriptive; no causal, reliance or benefit "
                 "claim; repeated observations within a question are not independent replications",
        "inputs": {"operator_input_inventory_sha256": read_json(OPERATOR_INPUT / "operator-request.json")[
            "input_inventory_sha256"], "witness": pin(WITNESS / "witness.json"),
            "witness_files": {oid: {"edges": v["edges_file"], "legacy": v["legacy_file"]}
                              for oid, v in w["variants"].items()}},
        "operator": {"receipt": {k: receipt[k] for k in ("status", "metric_version", "metric_code_sha256", "identity",
                                                         "isolation", "cell_count", "receipt_sha256", "outputs")},
                     "manifest": manifest, "outputs": outputs},
        "coverage": {"traces": len(traces), "quarantined": len(quarantined),
                     "by_tier": dict(Counter(t["tier"] for t in traces)),
                     "identity_ok": sum(bool(t.get("identity_ok")) for t in traces),
                     "identity_failures": dict(Counter(k for t in traces for k, v in (t.get("identity") or {}).items()
                                                       if not v)),
                     "replay": dict(Counter(t.get("replay") for t in traces)),
                     "final_context": dict(Counter(t.get("final_context") for t in traces)),
                     "terminals": dict(Counter(f"{t['tier']}:{t.get('terminal')}" for t in traces)),
                     "actions": len(actions), "citations": len(citations),
                     "citations_by_kind": dict(Counter(c["kind"] for c in citations))},
        "variants": {arms[v["variant_id"]]: {k: v[k] for k in ("variant_id", "documents", "graph_rows", "admitted_edges",
                                                                "graph_file_sha256", "corpus_inventory_sha256",
                                                                "edge_classes")}
                     for v in variants},
        "witness_edges": {w["variants"][oid]["arm"]: {k: x for k, x in w["variants"][oid].items()
                                                       if k not in ("edges_file", "legacy_file")}
                          for oid in sorted(w["variants"])},
        "reconciliation": reconcile(traces, actions, arms, w, keep105),
        "tables_all111": coverage_tables(traces, actions, citations, arms),
        "tables_common105": coverage_tables(traces, actions, citations, arms, keep=keep105),
        "chain_t2_follow_all111": chain(traces, actions, citations, arms),
        "chain_t2_follow_common105": chain(traces, actions, citations, arms, keep=keep105),
        "magnetic_view_join": magnetic_join(actions, arms, variants),
        "case_stratum_sizes": card_data["stratum_sizes"],
        "cards_file": CARDS.relative_to(ROOT).as_posix(),
    }
    return result, card_data


def tables_md(result, digest):
    lines = [f"# Sprint 24 LT trace audit: generated tables\n\nGenerated by `scripts/lt_trace_audit_24.py analyze` from "
             f"`evidence/{RESULT.name}` (SHA-256 `{digest}`). Counts are descriptive; follows are repeated observations "
             "within questions.\n"]
    lines.append("\n## Reconciliation with Sprint 20 (tier 2, follow condition)\n\n| arm | S20 follows | witness | "
                 "audit attempted | accepted | rejected | S20 added | witness | audit accepted added | S20 landing "
                 "(105) | witness | success-qualified |\n|---|---|---|---|---|---|---|---|---|---|---|---|\n")
    for arm, r in result["reconciliation"]["arms"].items():
        h, wl = r["historical_recorded"], r["witness_legacy"]
        lines.append(f"| {arm} | {h['follows']} | {wl['follows']} | {r['audit_attempted_follows']} | "
                     f"{r['audit_by_status'].get('accepted', 0)} | {r['audit_by_status'].get('rejected', 0)} | "
                     f"{h['added']} | {wl['added']} | {r['audit_accepted_added']} | {h['landing_mean_common105']:.4f} | "
                     f"{wl['landing_mean_common105']:.4f} | {r['success_qualified_landing_mean_common105']:.4f} |\n")
    for view in ("all111", "common105"):
        lines.append(f"\n## Tier-2 follow chain ({view}): traces (unique question-destination pairs)\n\n| step | " +
                     " | ".join(ARMS) + " |\n|---|" + "---|" * len(ARMS) + "\n")
        ch = result[f"chain_t2_follow_{view}"]
        keys = sorted({k for a in ARMS for k in ch[a]["traces"]})
        for k in keys:
            lines.append(f"| {k} | " + " | ".join(
                f"{ch[a]['traces'].get(k, 0)} ({ch[a]['unique_question_destination_pairs'].get(k, '-')})"
                for a in ARMS) + " |\n")
    lines.append("\n## Accepted follows by group (all 111)\n\n| group | accepted | added | mapped | unknown | first page "
                 "text exposure | supported | span in returned lines (full/partial) | returned lines all in final "
                 "context | mean menu added share | added share of uses |\n|---|---|---|---|---|---|---|---|---|---|---|\n")
    for g, t in result["tables_all111"].items():
        f = t.get("follow_accepted", {})
        if not f:
            continue
        o = t.get("opportunity_summary", {})
        lines.append(f"| {g} | {f.get('n', 0)} | {f.get('edge_class=added', 0)} | {f.get('edge_class=mapped_baseline', 0)} | "
                     f"{f.get('edge_class=unknown', 0)} | {f.get('first_page_text_exposure=True', 0)} | "
                     f"{f.get('label=supported', 0)} | {f.get('span_exposure_this_call=full', 0)}/"
                     f"{f.get('span_exposure_this_call=partial', 0)} | {f.get('returned_lines_in_final_context=all', 0)} | "
                     f"{o.get('mean_menu_added_share', 0):.3f} | {o.get('added_share_of_accepted_follows') or 0:.3f} |\n")
    lines.append("\n## Final-answer citations by group (all 111)\n\n| group | citations | first exposure follow only / "
                 "read only / mixed | page reached by follow | final context full | supported page |\n|---|---|---|---|---|---|\n")
    for g, t in result["tables_all111"].items():
        c = t.get("citation_answer", {})
        if not c:
            continue
        lines.append(f"| {g} | {c.get('n', 0)} | {c.get('first_exposure=follow_only', 0)} / {c.get('first_exposure=read_only', 0)} / "
                     f"{c.get('first_exposure=mixed', 0)} | {c.get('page_reached_by_follow=True', 0)} | "
                     f"{c.get('final_context=full', 0)} | {c.get('label=supported', '-')} |\n")
    return "".join(lines)


def analyze(_args):
    for p in (RESULT, CARDS, TABLES):
        if p.exists():
            raise SystemExit(f"exists:{p.name}")
    result, card_data = compute()
    card_pin = write_once(CARDS, canonical_text(card_data))
    result["cards"] = card_pin
    text = canonical_text(result)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    write_once(RESULT, text)
    write_once(TABLES, tables_md(result, digest))
    print(json.dumps({"evidence": RESULT.name, "sha256": digest, "cards_sha256": card_pin["sha256"],
                      "tables_sha256": sha(TABLES)}, indent=1))


def preservation():
    inv = read_json(INVENTORY)
    checks = Counter()
    bad = []
    for row in inv["trace_inventory"] + inv["artifacts"] + inv["sources"]:
        ok = (ROOT / row["path"]).is_file() and sha(ROOT / row["path"]) == row["sha256"]
        checks["ok" if ok else "changed"] += 1
        if not ok:
            bad.append(row["path"])
    p = read_json(PLAN)["pins"]
    for row in [p["planning_inventory"], p["loop_source"], p["system_prompt"], p["answer_plan"], p["rejudge"], p["s20"],
                p["tier1_tasks"], p["arm_key"], *p["labels_latest"], *p["sprint22_bundles"],
                *p["pinned_historical_helpers"]]:
        ok = sha(ROOT / row["path"]) == row["sha256"]
        checks["plan_ok" if ok else "plan_changed"] += 1
        if not ok:
            bad.append(row["path"])
    return {"counts": dict(checks), "changed": bad}


def verify(_args):
    result, card_data = compute()
    card_text = canonical_text(card_data)
    result["cards"] = {"path": CARDS.relative_to(ROOT).as_posix(), "bytes": len(card_text.encode("utf-8")),
                       "sha256": hashlib.sha256(card_text.encode("utf-8")).hexdigest()}
    text = canonical_text(result)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    same = (text == RESULT.read_text(encoding="utf-8") and card_text == CARDS.read_text(encoding="utf-8")
            and tables_md(result, digest) == TABLES.read_text(encoding="utf-8"))
    pres = preservation()
    print(json.dumps({"evidence_reproduced": same, "sha256": digest, "preservation": pres}, indent=1))
    if not same or pres["changed"]:
        raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="stage", required=True)
    for name, fn in (("plan", plan), ("witness", witness), ("prepare", prepare), ("run", run), ("analyze", analyze),
                     ("verify", verify)):
        sub.add_parser(name).set_defaults(fn=fn)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

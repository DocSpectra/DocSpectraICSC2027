"""Sprint 16 tier-1 generation (design D6a/D6b): 8 new complex tasks from areas the existing 7 do not cover.

The frozen September 28 protocol (design/cross-corpus-complex-task-generation-v1-2026-09-28.md) with its frozen prompt
and schema (design/sol-complex-task-prompt-v1.txt, sol-complex-task-schema-v1.json), adapted in two disclosed ways:
  * clusters are built per target area of the frozen survey partition (seed and fill pages from that area), on the
    scoped baseline O (docs_scope_v1, 1,218 pages) and its link graph;
  * the model runs as `codex exec` inside the qualified Codex editor container (no Codex on the host), gpt-6.1-sol at
    medium effort, source-only: a call whose events contain any tool item is rejected.
The machine gates are the protocol's own (select_cross_corpus_complex_tasks.py, replicated in check_task).

Stages:
  plan       target areas (uncovered by the existing 7, seeded order), 2 clusters each, one call packet per area
  generate   run the packets (<= 16 calls, 5M-token stop), resumable per packet
  select     machine gates; 1 task per area by cluster rank; refill from reserve areas; tier-1 set of 15
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import re
import sys
import threading
import unicodedata
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "sandbox"))
import wc_freeze_16 as f16  # noqa: E402
import prepare_cross_corpus_complex_task_packets as prep  # noqa: E402  (frozen ranking helpers)

wr, rr, sha, write_json, Refused = f16.wr, f16.rr, f16.sha, f16.write_json, f16.Refused
ROOT, CONTEXT = f16.ROOT, f16.CONTEXT
OUT = ROOT / "artifacts/wc-tier1-16"
O_EXPORT = ROOT / "artifacts/docker-corpus-variants-v1/runs/wc-15-fixture-v1/exports/O"
PROMPT = CONTEXT / "design/sol-complex-task-prompt-v1.txt"
SCHEMA = CONTEXT / "design/sol-complex-task-schema-v1.json"
QUESTIONS_11B = CONTEXT / "evidence/restructuring-pilot-11b-questions-2026-10-08.json"
INVENTORY = ROOT / "artifacts/cross-corpus-temporal-2026-09-28/complex-task-candidate-inventory-v1.json"
PANEL = CONTEXT / "evidence/cross-corpus-complex-task-panel-v1-2026-09-28.json"
OLD_QUESTIONS = CONTEXT / "evidence/cross-corpus-question-panel-v2-2026-09-28.json"
TIER1 = CONTEXT / f"evidence/wc-tier1-tasks-v1-{f16.DATE}.json"
SEED = "icsc2027-wc-tier1-clusters-v1"
MODEL, EFFORT = "gpt-6.1-sol", "medium"
NEW_TASKS, CLUSTERS_PER_AREA, MAX_CALLS, TOKEN_STOP, CALL_SECONDS = 8, 2, 16, 5_000_000, 900
CORPUS_ID = "docker_docs_scope_v1_O"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def words(value):
    return set(re.findall(r"[a-z0-9]{3,}", norm(value)))


# ------------------------------------------------------------------ existing tasks and area coverage

def existing_tasks():
    """The 5 existing 11b tasks and the 2 unselected Docker panel candidates, with their cited O pages."""
    q11 = rr.read_json(QUESTIONS_11B)["questions"]
    out = []
    for q in q11:
        pages = q["support_page_ids"] if isinstance(q["support_page_ids"], list) else ast.literal_eval(q["support_page_ids"])  # stored as a repr
        out.append({"source": "11b", "task_id": q["question_id"], "pages": sorted(set(pages) | {e["page_id"] for e in q["core_elements"]})})
    for r in rr.read_json(INVENTORY):
        if r["corpus_id"] == "docker_docs" and r["status"] == "passed_not_selected_capacity":
            out.append({"source": "panel-2026-09-28", "task_id": r["cluster_id"],
                        "pages": sorted(set(r["core_cited_pages"]) | set(r["robustness_cited_pages"]))})
    return out


def area_of_doc():
    freeze = f16.check_lock(f16.SURVEY_FREEZE)
    vm = rr.read_json(wr.V6)
    rel_to_doc = {p.rel_path: p.doc_id for p in rr.baseline_pages(wr.dcv.pinned(vm), vm)}
    mapping = {}
    for area, rels in freeze["areas"].items():
        for rel in rels:
            mapping[rel_to_doc[rel]] = area
    return freeze, mapping


# ------------------------------------------------------------------ clusters (frozen ranking, per area)

def o_pages():
    pages = {}
    for path in (O_EXPORT / "corpus").glob("*.md"):
        raw = path.read_text(encoding="utf-8")
        body = prep.text_body(raw)
        if len(body.strip()) < 700:
            continue
        heading = prep.title(raw, path.stem)
        b5 = body[:5000]
        pages[path.stem] = {"title": heading, "body": body, "page_sha256": sha(path),
                            "tokens": prep.tokens(heading + " " + path.stem.split("__")[0]),
                            "score": 2 * min(12, len(prep.ACTION.findall(b5))) + min(12, len(prep.CONDITION.findall(b5)))
                            + min(10, len(re.findall(r"(?m)^\s*(?:[-*]|\d+[.)])\s+", b5))) + min(len(body) // 1500, 8)}
    adj = defaultdict(set)
    with (O_EXPORT / "graph_edges.csv").open(encoding="utf-8", newline="") as handle:
        for edge in csv.DictReader(handle):
            a, b = edge["source"], edge["target"]
            if a in pages and b in pages and a != b:
                adj[a].add(b)
                adj[b].add(a)
    return pages, adj


def area_clusters(area, members, pages, adj, count=CLUSTERS_PER_AREA):
    """The protocol's seed ranking and 8-page fill, restricted to one area's pages."""
    local = [m for m in members if m in pages]
    order = sorted(local, key=lambda n: (-pages[n]["score"], -len(adj[n]), prep.digest(SEED + "|" + area + "|" + n)))
    selected = []
    for name in order:
        if len(selected) >= count:
            break
        overlap = max([len(pages[name]["tokens"] & pages[o]["tokens"]) / max(1, len(pages[name]["tokens"] | pages[o]["tokens"]))
                       for o in selected], default=0)
        if overlap < 0.6:
            selected.append(name)
    clusters = []
    for rank, seed in enumerate(selected, 1):
        dist, queue = {seed: 0}, deque([seed])
        while queue:
            cur = queue.popleft()
            if dist[cur] >= 2:
                continue
            for other in sorted(adj[cur]):
                if other in local and other not in dist:
                    dist[other] = dist[cur] + 1
                    queue.append(other)
        st = pages[seed]["tokens"]
        related = sorted((x for x in local if x != seed), key=lambda o: (
            0 if o in dist else 1, dist.get(o, 3), -len(st & pages[o]["tokens"]) / max(1, len(st | pages[o]["tokens"])),
            -pages[o]["score"], prep.digest(SEED + "|" + seed + "|" + o)))[:7]
        rows = [{"page_id": x, "page_sha256": pages[x]["page_sha256"], "title": pages[x]["title"],
                 "graph_undirected_hops_from_seed": dist.get(x), "excerpt": prep.excerpt(pages[x]["body"]),
                 "excerpt_source": "exact_page_substrings_with_literal_gap_marker"} for x in [seed] + related]
        clusters.append({"cluster_id": prep.digest(SEED + "|" + area + "|" + seed), "selection_rank": rank,
                         "seed_page_id": seed, "area_id": area, "pages": rows})
    return clusters


def plan(args):
    freeze, doc_area = area_of_doc()
    tasks = existing_tasks()
    covered = sorted({doc_area[p] for t in tasks for p in t["pages"] if p in doc_area})
    uncovered = [a for a in freeze["order"] if a not in covered]
    if len(uncovered) < NEW_TASKS:
        raise Refused(f"too-few-uncovered-areas:{len(uncovered)}")
    pages, adj = o_pages()
    members = defaultdict(list)
    for doc, area in doc_area.items():
        members[area].append(doc)
    OUT.mkdir(parents=True, exist_ok=True)
    calls = []
    for i, area in enumerate(uncovered[:MAX_CALLS]):
        clusters = area_clusters(area, members[area], pages, adj)
        if not clusters:
            continue
        packet = {"schema_version": "docspectra.icsc.complex_task_source_packet.v1", "corpus_id": CORPUS_ID,
                  "anchor_commit": "docs_scope_v1 baseline O (variant manifest v6)",
                  "dataset_inventory_sha256": rr.read_json(O_EXPORT / "export-receipt.json")["corpus_tree_sha256"],
                  "faq_answers_included": False, "prior_retrieval_or_answer_outputs_included": False,
                  "selection_seed": SEED, "area_id": area, "clusters": clusters}
        path = OUT / "packets" / f"{area}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        calls.append({"area_id": area, "priority": i + 1, "reserve": i >= NEW_TASKS, "packet": path.name, "sha256": sha(path),
                      "cluster_ids": [c["cluster_id"] for c in clusters]})
    manifest = {"schema_version": "docspectra.icsc.wc_tier1_plan.v1", "created": f16.DATE,
                "survey_freeze_sha256": sha(f16.SURVEY_FREEZE), "preregistration_sha256": sha(f16.PREREG),
                "existing_tasks": tasks, "covered_areas": covered, "uncovered_areas_in_order": uncovered,
                "prompt_sha256": sha(PROMPT), "schema_sha256": sha(SCHEMA), "model": MODEL, "effort": EFFORT,
                "limits": {"new_tasks": NEW_TASKS, "max_calls": MAX_CALLS, "token_stop": TOKEN_STOP, "call_seconds": CALL_SECONDS},
                "calls": calls}
    write_json(OUT / "plan.json", manifest)
    print(json.dumps({"covered": covered, "uncovered": len(uncovered), "calls_planned": len(calls),
                      "first": [c["area_id"] for c in calls[:NEW_TASKS]]}, indent=1))


# ------------------------------------------------------------------ calls in the Codex container

def call(packet_path, out):
    import editor_container  # noqa: PLC0415
    import editor_session  # noqa: PLC0415
    prompt = PROMPT.read_bytes() + packet_path.read_bytes()
    out.mkdir(parents=True)
    (out / "submitted-prompt.txt").write_bytes(prompt)
    container = editor_container.EditorContainer(str(uuid.uuid4()), "docspectra/codex-editor:v1", "docspectra/egress-proxy:v1", out)
    driver = editor_session.CodexDriver(model=MODEL, reasoning_effort=EFFORT, prompt=prompt, auth_path=wr.sf.AUTH_DEFAULT)
    lines, started = [], now()
    try:
        container.start({"README.txt": b"source-only task generation; no files to read\n"})
        container.put_file("/codex-home/config.toml", driver.config_bytes())
        container.put_file("/codex-home/auth.json", driver.auth_path.read_bytes())
        container.put_file("/codex-home/schema.json", SCHEMA.read_bytes())
        argv = ["sh", "-c", "cd /work && exec codex exec --json --skip-git-repo-check --ephemeral --ignore-rules --color never "
                            "--output-schema /codex-home/schema.json --output-last-message /tmp/response.json -"]
        code, stop = container.run(argv, prompt, lines.append, CALL_SECONDS, threading.Event())
        got = editor_container.docker("exec", container.editor, "cat", "/tmp/response.json", check=False)
        if got.returncode == 0:
            (out / "response.json").write_bytes(got.stdout)
    finally:
        container.teardown()
    (out / "stdout.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    events = [json.loads(x) for x in lines if x.startswith("{")]
    usage = [e.get("usage") for e in events if e.get("type") == "turn.completed"]
    tools = [e for e in events if e.get("type", "").startswith("item.") and e.get("item", {}).get("type") not in ("agent_message", "reasoning")]
    record = {"started_at_utc": started, "finished_at_utc": now(), "model": MODEL, "effort": EFFORT,
              "runtime": "codex exec in docspectra/codex-editor:v1 behind docspectra/egress-proxy:v1",
              "packet_sha256": sha(packet_path), "prompt_template_sha256": sha(PROMPT), "schema_sha256": sha(SCHEMA),
              "exit_code": code, "stop": stop, "usage": usage, "tool_items": len(tools),
              "response_sha256": sha(out / "response.json") if (out / "response.json").exists() else None,
              "stdout_sha256": sha(out / "stdout.jsonl")}
    write_json(out / "call-record.json", record)
    return record


def tokens_of(record):
    return sum((u or {}).get("input_tokens", 0) + (u or {}).get("output_tokens", 0) for u in record["usage"])


def generate(args):
    p = rr.read_json(OUT / "plan.json")
    wr.authorized(f16.RUN_ROOT, 1)
    spent, ran = 0, 0
    for c in sorted(p["calls"], key=lambda x: x["priority"]):
        if args.reserve is False and c["reserve"]:
            continue
        if args.areas and c["area_id"] not in args.areas:
            continue
        out = OUT / "responses" / c["area_id"]
        if (out / "call-record.json").exists():
            spent += tokens_of(rr.read_json(out / "call-record.json"))
            continue
        if ran >= MAX_CALLS or spent >= TOKEN_STOP:
            print(json.dumps({"stop": "call-or-token-limit", "spent": spent}))
            break
        rec = call(OUT / "packets" / c["packet"], out)
        spent += tokens_of(rec)
        ran += 1
        print(json.dumps({"area": c["area_id"], "exit": rec["exit_code"], "tokens": tokens_of(rec), "tool_items": rec["tool_items"],
                          "spent": spent}), flush=True)


# ------------------------------------------------------------------ machine gates and selection

def check_task(raw, cluster, old_questions):
    """The September 28 protocol gates (select_cross_corpus_complex_tasks.py), on O pages."""
    t, reasons = raw["task"], []
    page_map = {p["page_id"]: p for p in cluster["pages"]}

    def check_quote(e, label):
        pid, quote = e["page_id"], e["exact_quote"]
        if pid not in page_map:
            reasons.append(label + ":page_not_in_packet")
            return
        e["page_sha256"] = page_map[pid]["page_sha256"]
        if not (20 <= len(quote) <= 600):
            reasons.append(label + ":quote_length")
        elif len(quote) > 400:
            e["quote_length_warning"] = True
        excerpt = page_map[pid]["excerpt"]
        if quote not in excerpt:
            match = re.search(r"\s+".join(re.escape(x) for x in re.split(r"\s+", quote.strip())), excerpt)
            if match:
                e["model_quote_before_whitespace_canonicalization"] = quote
                e["exact_quote"] = quote = match.group(0)
                if len(quote) > 600:
                    reasons.append(label + ":canonical_quote_length")
            else:
                reasons.append(label + ":quote_not_in_excerpt")
        source = O_EXPORT / "corpus" / f"{pid}.md"
        if not source.is_file():
            reasons.append(label + ":page_absent")
        elif sha(source) != page_map[pid]["page_sha256"]:
            reasons.append(label + ":page_hash_drift")
        elif quote not in source.read_text(encoding="utf-8"):
            reasons.append(label + ":quote_not_in_anchor_page")
    ev = t["realism_evidence"]
    if len(ev) != 2 or {x["role"] for x in ev} != {"workflow_goal", "condition_or_consequence"}:
        reasons.append("realism_evidence_roles")
    if len({norm(x["exact_quote"]) for x in ev}) != len(ev):
        reasons.append("duplicate_realism_quote")
    for i, e in enumerate(ev):
        check_quote(e, f"realism{i + 1}")
    core, robust = t["core_elements"], t["robustness_elements"]
    if len(core) != 5 or [x["element_id"] for x in core] != [f"C{i}" for i in range(1, 6)]:
        reasons.append("core_count_or_ids")
    if len(robust) != 10 or [x["element_id"] for x in robust] != [f"R{i}" for i in range(1, 11)]:
        reasons.append("robust_count_or_ids")
    elements = core + robust
    for e in elements:
        check_quote(e, e["element_id"])
        if min(len(e["requirement"].strip()), len(e["acceptance"].strip()), len(e["tier_reason"].strip())) < 8:
            reasons.append(e["element_id"] + ":thin_element")
    if len({norm(e["requirement"]) for e in elements}) != len(elements):
        reasons.append("duplicate_requirement")
    if len({norm(e["exact_quote"]) for e in elements}) != len(elements):
        reasons.append("duplicate_element_quote")
    core_pages = {x["page_id"] for x in core}
    other_r = [x for x in robust if x["page_id"] not in core_pages]
    if len({x["page_id"] for x in elements}) < 3:
        reasons.append("fewer_than_three_source_pages")
    if len(other_r) < 5 or len({x["page_id"] for x in other_r}) < 2:
        reasons.append("insufficient_cross_page_robustness")
    if any(len(t[x].strip()) < 12 for x in ("question", "actor", "trigger", "goal", "observable_success", "realism_rationale")):
        reasons.append("thin_task_realism_field")
    if norm(t["question"]) in old_questions:
        reasons.append("duplicate_prior_question")
    if any(norm(t["question"]) == norm(e["acceptance"]) for e in elements):
        reasons.append("answer_in_task")
    return sorted(set(reasons)), core_pages, other_r


def select(args):
    p = rr.read_json(OUT / "plan.json")
    old_questions = {norm(r["question"]) for r in rr.read_json(OLD_QUESTIONS)["rows"]}
    existing_q = [norm(q["question"]) for q in rr.read_json(QUESTIONS_11B)["questions"]]
    rows, receipts, chosen = [], [], []
    for c in sorted(p["calls"], key=lambda x: x["priority"]):
        out = OUT / "responses" / c["area_id"]
        packet = rr.read_json(OUT / "packets" / c["packet"])
        if not (out / "call-record.json").exists() or not (out / "response.json").exists():
            rows += [{"area_id": c["area_id"], "cluster_id": x, "status": "not_generated"} for x in c["cluster_ids"]]
            continue
        rec = rr.read_json(out / "call-record.json")
        receipts.append({"area_id": c["area_id"], **{k: rec[k] for k in ("usage", "exit_code", "tool_items", "response_sha256",
                                                                         "started_at_utc", "finished_at_utc")}})
        if rec["tool_items"]:
            rows += [{"area_id": c["area_id"], "cluster_id": x, "status": "rejected_call_used_tools"} for x in c["cluster_ids"]]
            continue
        response = rr.read_json(out / "response.json")
        proposals = {x["cluster_id"]: x for x in response.get("candidates", [])}
        for cluster in packet["clusters"]:
            raw = proposals.get(cluster["cluster_id"])
            base = {"area_id": c["area_id"], "cluster_id": cluster["cluster_id"], "selection_rank": cluster["selection_rank"],
                    "seed_page_id": cluster["seed_page_id"]}
            if raw is None or raw.get("task") is None:
                rows.append({**base, "status": "model_rejected", "reasons": [raw.get("rejection_reason") if raw else "missing_candidate"]})
                continue
            reasons, core_pages, other_r = check_task(raw, cluster, old_questions)
            if any(norm(raw["task"]["question"]) == q for q in existing_q):
                reasons.append("duplicate_existing_tier1")
            rows.append({**base, "status": "source_checked_candidate" if not reasons else "rejected_by_machine_gate",
                         "reasons": reasons, "task": raw["task"], "core_cited_pages": sorted(core_pages),
                         "cross_page_R_count": len(other_r)})
    by_area = defaultdict(list)
    for r in rows:
        by_area[r["area_id"]].append(r)
    for c in sorted(p["calls"], key=lambda x: x["priority"]):
        if len(chosen) >= NEW_TASKS:
            break
        for r in sorted(by_area[c["area_id"]], key=lambda x: x.get("selection_rank", 99)):
            if r["status"] != "source_checked_candidate":
                continue
            q = norm(r["task"]["question"])
            if any(len(words(q) & words(x["task"]["question"])) / max(1, len(words(q) | words(x["task"]["question"]))) > 0.78 for x in chosen):
                r["status"] = "rejected_duplicate_selected_task"
                continue
            r["status"] = "selected_provisional"
            chosen.append(r)
            break
    for r in rows:
        if r["status"] == "source_checked_candidate":
            r["status"] = "passed_not_selected"
    write_json(OUT / "candidate-inventory.json", rows)
    # tier-1 set of 15 in the 11b shape: question, core elements (C1-C5), support pages
    tier1 = []
    for q in rr.read_json(QUESTIONS_11B)["questions"]:
        tier1.append({"task_id": q["question_id"], "source": "11b (2026-10-08)", "question": q["question"],
                      "core_elements": q["core_elements"], "support_page_ids": q["support_page_ids"] if isinstance(q["support_page_ids"], list)
                      else ast.literal_eval(q["support_page_ids"])})
    for r in rr.read_json(INVENTORY):
        if r["corpus_id"] == "docker_docs" and r["status"] == "passed_not_selected_capacity":
            tier1.append({"task_id": r["cluster_id"], "source": "panel 2026-09-28, passed not selected", "question": r["task"]["question"],
                          "core_elements": [{k: e[k] for k in ("element_id", "requirement", "acceptance", "page_id", "exact_quote")} for e in r["task"]["core_elements"]],
                          "support_page_ids": sorted(r["core_cited_pages"])})
    for r in chosen:
        tier1.append({"task_id": r["cluster_id"], "source": f"Sprint 16 generation, area {r['area_id']}", "area_id": r["area_id"],
                      "question": r["task"]["question"],
                      "core_elements": [{k: e[k] for k in ("element_id", "requirement", "acceptance", "page_id", "exact_quote")} for e in r["task"]["core_elements"]],
                      "support_page_ids": r["core_cited_pages"]})
    result = {"schema_version": "docspectra.icsc.wc_tier1_tasks.v1", "created": f16.DATE,
              "status": "machine-checked proposals pending the author's review of every core element (HR-19)",
              "preregistration_sha256": sha(f16.PREREG), "plan_sha256": sha(OUT / "plan.json"),
              "candidate_inventory_sha256": sha(OUT / "candidate-inventory.json"), "call_receipts": receipts,
              "tokens_total": sum(tokens_of(x) for x in receipts),
              "counts": {s: sum(1 for r in rows if r["status"] == s) for s in sorted({r["status"] for r in rows})},
              "tasks": tier1}
    write_json(TIER1, result)
    print(json.dumps({"selected_new": len(chosen), "tier1_total": len(tier1), "counts": result["counts"],
                      "tokens_total": result["tokens_total"]}, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="stage", required=True)
    sub.add_parser("plan").set_defaults(fn=plan)
    g = sub.add_parser("generate")
    g.add_argument("--areas", nargs="*")
    g.add_argument("--reserve", action="store_true", help="also run reserve areas (refill after model or gate rejections)")
    g.set_defaults(fn=generate)
    sub.add_parser("select").set_defaults(fn=select)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

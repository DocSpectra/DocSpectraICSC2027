"""Sprint 15: whole-corpus refinement infrastructure (design v1: D3, D5, D6a, D7, D8). Zero model calls.

The design (`design/whole-corpus-refinement-experiment-v1-2026-10-09.md`) is frozen; this driver implements it and
changes no decision. restructuring_runs (rr), structure_forcing_12 (sf), structure_forcing_12b (sfb) and
lt_locality_13 (ln) are imported as libraries and never edited.

Runtime (D3)
  chaining    every session runs through the unchanged rr.launch; the baseline copy is replaced, for that one call,
              by a copy of the arm's current admitted tree (seeded_copy). The session's baseline_copy_tree_sha256 is then
              the start tree's content id, and its first checkpoint names it as parent; the ledger refuses any session
              whose start id differs from the arm head.
  admission   plan v3 admission v2 (sfb.admission_record) on the cumulative tree against O, after every session.
  rollback    the tree before a session is its start tree; a non-admitted session leaves the arm head unchanged
              (ledger action "rollback", the rejected tree id and reasons recorded).
  resume      a pass stopped by a limit whose residual is admitted advances the head and gets one resume session: a new
              Codex session (new namespace and containers; the runtime never resumes a Codex thread) from that tree,
              prompted with the area prompt plus RESUME_PARAGRAPH. A resume is never resumed.
  ledger      host-private/wc-ledger.jsonl: launch and end rows (tokens, seconds, start and terminal ids, action, the
              pass diff); arm budgets (250M tokens, 3 days) are checked between sessions; a launch row without an end
              row refuses.
  queue       survey (LT stage) first; then areas in the seeded order, LT and R0 alternating which goes first; then one
              reconciliation per arm. Infrastructure stops halt the run.

Provenance (O1, proposed; the author confirms before the Sprint 16 freeze): see PROVENANCE_RULE.
Controls (D5): LN, PC and RN built per area from that area's edits and combined on the whole tree.
Tier 2 (D8): navigation metrics from 12b agent traces and RA fixed retrievers scored against credited support.

Stages: `pins` (write the O5 retriever pin record), `fixture` (zero model calls; scripted 3-area end to end).
`survey` and `refine` run a frozen run plan live; both refuse without the plan lock and the recorded authorization.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")  # as restructuring_runs and 09c

import argparse
import contextlib
import hashlib
import inspect
import itertools
import json
import math
import random
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import structure_forcing_12 as sf  # noqa: E402
import structure_forcing_12b as sfb  # noqa: E402
import lt_locality_13 as ln  # noqa: E402

rr, dcv = sf.rr, sf.dcv
Refused = sf.Refused
sha, sha_text, write_json = sf.sha, sf.sha_text, sf.write_json

DATE = "2026-10-10"
DESIGN = rr.CONTEXT / "design/whole-corpus-refinement-experiment-v1-2026-10-09.md"
EVIDENCE = sf.EVIDENCE
V6 = sf.V6
FIXTURE_RUN_ID = "wc-15-fixture-v1"
FIXTURE_EVIDENCE = EVIDENCE / f"wc-refinement-15-fixture-v1-{DATE}.json"
PINS_EVIDENCE = EVIDENCE / f"wc-refinement-15-retriever-pins-v1-{DATE}.json"
SURVEY_ARM = "WS"
EDIT_ARMS = ("LT", "R0")
KINDS = ("survey", "pass", "resume", "reconcile")
RECONCILE_AREA = "ALL"
rr.ARMS = tuple(dict.fromkeys(rr.ARMS + EDIT_ARMS + (SURVEY_ARM,)))  # as sf registers its arms at import
rr.EDITED = tuple(dict.fromkeys(rr.EDITED + EDIT_ARMS + (SURVEY_ARM,)))

# D3 limits and budgets (author, October 10)
SESSION_WALL_SECONDS = 5400
ARM_BUDGET = {"tokens": 250_000_000, "wall_seconds": 3 * 86400}
LIVE_LIMITS = {"model_calls": 1, "tool_calls": None, "tokens": None, "wall_seconds": SESSION_WALL_SECONDS, "edits": None}
LIVE_AREA_BOUNDS = (15, 25)
ORDER_SEED_LABEL = "icsc2027-wc-area-order"
BUDGET_RULE = ("an arm's tokens are the sum of editor_tokens and its seconds the sum of host seconds from launch to end "
               "over its sessions (the survey counts to LT); before each session the arm is checked; at or over either "
               "cap no new session of that arm starts and every planned item of it is recorded as skipped (unedited)")

# D5 salts (new per null type)
SALTS = {"LN": "icsc2027-wc-ln:", "PC": "icsc2027-wc-pc:", "RN": "icsc2027-wc-rn:"}
NULLS_PER_TYPE = 5

# ------------------------------------------------------------------ prompts (drafts; Sprint 16/17 freeze them by hash)

LIMITS_SENTENCE = ("You have one session of at most 90 minutes; the host enforces this limit and stops the session if "
                   "it is exceeded.")
AREA_PARAGRAPH = ("Your area is: {paths}. Edit only pages in this area, plus any new pages your target structure needs. "
                  "You may link to pages outside the area. You are done when every page in your area meets the target "
                  "structure; then stop and write your summary. Check elapsed time with the shell; stop editing by 80 "
                  "minutes.")
TRUNK_PARAGRAPH = ("The trunk inventory below was frozen by a survey of the whole documentation before any edit; every "
                   "area session receives the same inventory. Use it to decide which shared content belongs in trunk "
                   "pages and which trunk pages already exist or are planned:\n\n```json\n{inventory}\n```")
RESUME_PARAGRAPH = ("An earlier session on this area was stopped at its time limit. The working tree already contains "
                    "that session's edits, which the host has checked and accepted. Continue the same task from this "
                    "tree; do not undo the earlier edits unless the target structure requires it.")
RECONCILE_PARAGRAPH = ("This is the reconciliation session. Every area has had its session. Work across the whole "
                       "documentation: resolve duplication and conflicting structure between areas and repair the links "
                       "between them, toward the same target structure. Check elapsed time with the shell; stop editing "
                       "by 80 minutes.")
RECORD_INTRO = sfb.RECORD_INTRO
SESSION_RECORD_NOTE = ('Set "done" to true only if every page in your scope meets the target structure; otherwise set '
                       'it to false and give "not_done_reason". In "provenance", list every page you created or '
                       'rewrote, with the pages of the original documentation its content came from ("sources"); give '
                       'an empty list if the content is new.')
SURVEY_TASK = ("Survey the documentation and partition every page into between {lo} and {hi} areas. Each area is a "
               "group of related pages, small enough that every page in it can be brought to a target structure in one "
               "90-minute session. Every Markdown page must belong to exactly one area. Also draft a trunk inventory: "
               "topics whose content is repeated across many pages and could live in one shared page, with the pages "
               "that repeat it. Do not edit any file.")
SURVEY_RECORD_NOTE = ('Give one object per area, with "area_id" A01, A02, and so on. Each entry in "paths" is a '
                      'directory prefix or a page path; together the areas must cover every page exactly once; '
                      '"page_count" is the number of Markdown pages in the area.')


def session_record_template(arm, kind, area_id, paths):
    return ("{\n"
            '  "schema": "wc_session_record.v1",\n'
            f'  "arm": "{arm}",\n'
            f'  "kind": "{kind}",\n'
            f'  "area_id": "{area_id}",\n'
            f'  "area_paths": {json.dumps(list(paths))},\n'
            '  "pages_in_area": <integer>,\n'
            '  "pages_edited": ["<path>"],\n'
            '  "pages_created": ["<path>"],\n'
            '  "pages_removed": ["<path>"],\n'
            '  "done": <true or false>,\n'
            '  "not_done_reason": <null or "<reason>">,\n'
            '  "provenance": [{"page": "<path>", "sources": ["<path>"]}],\n'
            '  "preservation_concerns": ["<concern>"],\n'
            '  "elapsed_minutes_at_stop": <number>\n'
            "}")


SURVEY_TEMPLATE = ("{\n"
                   '  "schema": "wc_area_partition.v1",\n'
                   '  "areas": [{"area_id": "A01", "paths": ["<directory prefix or page path>"], "page_count": '
                   '<integer>, "rationale": "<text>"}],\n'
                   '  "trunk_inventory": [{"trunk_id": "T01", "topic": "<text>", "pages": ["<path>"], '
                   '"proposed_page": "<path>"}],\n'
                   '  "method": "<text>"\n'
                   "}")


def _paragraphs(base: bytes):
    text = base.decode("utf-8")
    if not text.endswith("\n") or "\n\n" not in text:
        raise Refused("base-prompt-shape")
    return text.rstrip("\n").split("\n\n")


def session_prompt(base: bytes, arm: str, kind: str, area_id: str, paths, trunk_inventory=None) -> bytes:
    """Base arm prompt + the limits sentence + the scope paragraph (area or reconcile), the trunk inventory (LT only),
    the resume paragraph (resume only) and the record template. The base prompt is frozen in Sprint 17."""
    if arm not in EDIT_ARMS or kind not in ("pass", "resume", "reconcile"):
        raise Refused(f"session-prompt-arm-or-kind:{arm}:{kind}")
    if arm == "R0" and trunk_inventory is not None:
        raise Refused("r0-never-sees-the-trunk-inventory")
    if arm == "LT" and trunk_inventory is None:
        raise Refused("lt-needs-the-trunk-inventory")
    if (kind == "reconcile") != (area_id == RECONCILE_AREA):
        raise Refused("reconcile-area-mismatch")
    paras = _paragraphs(base) + [LIMITS_SENTENCE]
    paras.append(RECONCILE_PARAGRAPH if kind == "reconcile" else AREA_PARAGRAPH.format(paths=", ".join(paths)))
    if trunk_inventory is not None:
        paras.append(TRUNK_PARAGRAPH.format(inventory=json.dumps(trunk_inventory, indent=1, sort_keys=True)))
    if kind == "resume":
        paras.append(RESUME_PARAGRAPH)
    paras.append(sfb.record_paragraph(session_record_template(arm, kind, area_id, paths), SESSION_RECORD_NOTE))
    return ("\n\n".join(paras) + "\n").encode("utf-8")


def survey_prompt(bounds=LIVE_AREA_BOUNDS) -> bytes:
    lo, hi = bounds
    text = "\n\n".join([
        "Role: documentation survey session, whole-corpus area partition and trunk inventory, prompt version 1.",
        "You work in a shell inside a container whose working directory is a frozen documentation tree of Markdown "
        "pages. " + SURVEY_TASK.format(lo=lo, hi=hi),
        "Do not consult the web, model memory as a factual source, or any evaluation materials. Evaluation questions, "
        "answers, labels, prior model outputs, grades, and task-effect feedback are outside this session. " +
        LIMITS_SENTENCE + " No one will answer questions during the session; work from the tree alone and finish with "
        "your summary.",
        sfb.record_paragraph(SURVEY_TEMPLATE, SURVEY_RECORD_NOTE)])
    return (text + "\n").encode("utf-8")


# ------------------------------------------------------------------ record schemas

def record_schemas(bounds=LIVE_AREA_BOUNDS):
    lo, hi = bounds
    path_list = {"type": "array", "items": {"type": "string", "minLength": 1}}
    partition = {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "wc_area_partition.v1",
        "type": "object", "additionalProperties": False, "required": ["schema", "areas", "trunk_inventory", "method"],
        "properties": {
            "schema": {"const": "wc_area_partition.v1"},
            "areas": {"type": "array", "minItems": lo, "maxItems": hi, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["area_id", "paths", "page_count", "rationale"],
                "properties": {"area_id": {"type": "string", "pattern": "^A[0-9]{2}$"},
                               "paths": dict(path_list, minItems=1), "page_count": {"type": "integer", "minimum": 1},
                               "rationale": {"type": "string"}}}},
            "trunk_inventory": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["trunk_id", "topic", "pages", "proposed_page"],
                "properties": {"trunk_id": {"type": "string"}, "topic": {"type": "string"}, "pages": path_list,
                               "proposed_page": {"type": "string"}}}},
            "method": {"type": "string"}}}
    session = {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "wc_session_record.v1",
        "type": "object", "additionalProperties": False,
        "required": ["schema", "arm", "kind", "area_id", "area_paths", "pages_in_area", "pages_edited",
                     "pages_created", "pages_removed", "done", "not_done_reason", "provenance",
                     "preservation_concerns", "elapsed_minutes_at_stop"],
        "properties": {
            "schema": {"const": "wc_session_record.v1"}, "arm": {"enum": list(EDIT_ARMS)},
            "kind": {"enum": ["pass", "resume", "reconcile"]},
            "area_id": {"type": "string", "pattern": "^(A[0-9]{2}|ALL)$"}, "area_paths": path_list,
            "pages_in_area": {"type": "integer", "minimum": 0}, "pages_edited": path_list,
            "pages_created": path_list, "pages_removed": path_list, "done": {"type": "boolean"},
            "not_done_reason": {"type": ["string", "null"]},
            "provenance": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["page", "sources"],
                "properties": {"page": {"type": "string", "minLength": 1}, "sources": path_list}}},
            "preservation_concerns": {"type": "array", "items": {"type": "string"}},
            "elapsed_minutes_at_stop": {"type": "number", "minimum": 0}}}
    return {"schema_version": "docspectra.icsc.wc_refinement_15.v1.record_schemas", "created": DATE,
            "area_bounds": [lo, hi],
            "schemas": {"wc_area_partition.v1": partition, "wc_session_record.v1": session},
            "extraction": "as Sprint 12b: exactly one ```json block in the last agent_message, Draft 2020-12",
            "outcomes": "record_invalid is reported and never affects admission or credit"}


def partition_host_checks(record, baseline_rels):
    """Every baseline page in exactly one area; distinct ids; no empty area (all gating); counts reported."""
    areas = record["areas"]
    ids = [a["area_id"] for a in areas]
    pages = {a["area_id"]: sorted(r for r in baseline_rels if sfb.in_area(r, a["paths"])) for a in areas}
    owner = defaultdict(list)
    for aid, rels in pages.items():
        for r in rels:
            owner[r].append(aid)
    overlap = sorted(r for r, o in owner.items() if len(o) > 1)
    uncovered = sorted(r for r in baseline_rels if r not in owner)
    rel_set = set(baseline_rels)
    trunk_missing = sorted({sfb.norm_path(p) for t in record.get("trunk_inventory", []) for p in t["pages"]}
                           - rel_set)
    problems = ([] if len(set(ids)) == len(ids) else ["area-ids-not-distinct"]) + \
        [f"empty-area:{a}" for a, rels in pages.items() if not rels] + \
        ([f"overlapping-pages:{len(overlap)}"] if overlap else []) + \
        ([f"uncovered-pages:{len(uncovered)}"] if uncovered else [])
    return {"passes": not problems, "problems": problems, "areas": len(areas),
            "host_page_count": {a: len(r) for a, r in pages.items()},
            "record_page_count": {a["area_id"]: a["page_count"] for a in areas},
            "overlap_examples": overlap[:10], "uncovered_examples": uncovered[:10],
            "trunk_pages_not_in_baseline": trunk_missing[:20], "trunk_pages_not_in_baseline_count": len(trunk_missing),
            "paths": {a["area_id"]: [sfb.norm_path(p) for p in a["paths"]] for a in areas}}


def area_order(area_ids, label=ORDER_SEED_LABEL):
    order = sorted(area_ids)
    random.Random(int(hashlib.sha256(label.encode()).hexdigest(), 16)).shuffle(order)
    return order


def planned_items(order, arms=EDIT_ARMS):
    """Seeded area order; LT and R0 alternate which goes first; then one reconciliation per arm."""
    items = []
    for i, area in enumerate(order):
        for arm in (arms if i % 2 == 0 else arms[::-1]):
            items.append({"arm": arm, "kind": "pass", "area": area})
    items += [{"arm": arm, "kind": "reconcile", "area": RECONCILE_AREA} for arm in arms]
    return items


# ------------------------------------------------------------------ chaining

@contextlib.contextmanager
def seeded_copy(start_tree):
    """For one rr.launch call: the 'baseline copy' is a copy of `start_tree` (an admitted tree), checked by content id."""
    if start_tree is None:
        yield None
        return
    start_tree = Path(start_tree)
    expected = dcv.tree_hash(dcv.inventory(start_tree))
    original = dcv.copy_baseline

    def copy(args):
        manifest = rr.read_json(args.manifest)
        dest = Path(args.dest).resolve()
        allowed = Path(manifest["output_root"]).resolve()
        if dest == allowed or allowed not in dest.parents or dest.exists():
            raise ValueError("output-collision-or-outside-root")
        shutil.copytree(start_tree, dest)
        if dcv.tree_hash(dcv.inventory(dest)) != expected:
            raise ValueError("seeded-copy-hash-mismatch")

    dcv.copy_baseline = copy
    try:
        yield expected
    finally:
        dcv.copy_baseline = original


def tree_id(tree):
    return dcv.tree_hash(dcv.inventory(Path(tree)))


def pass_diff(start_tree, end_tree):
    """Paths a session added, removed and changed, against its own start tree."""
    a, b = dcv.inventory(Path(start_tree)), dcv.inventory(Path(end_tree))
    return {"added": sorted(set(b) - set(a)), "removed": sorted(set(a) - set(b)),
            "changed": sorted(r for r in set(a) & set(b) if a[r] != b[r])}


def chained_launch(manifest_path, arm, replicate, start_tree, *, driver=None, live=False):
    """rr.launch from `start_tree` (None: the fresh baseline copy); refuses unless the session's recorded baseline copy
    is exactly the start tree."""
    with seeded_copy(start_tree) as expected:
        if live:
            if not os.environ.get(rr.AUTH_ENV) and sf.AUTH_DEFAULT.is_file():
                os.environ[rr.AUTH_ENV] = str(sf.AUTH_DEFAULT)
            state = rr.launch(manifest_path, arm, replicate, live=True)
        else:
            state = rr.launch(manifest_path, arm, replicate, driver=driver)
    if expected is not None and state["baseline_copy_tree_sha256"] != expected:
        raise Refused("chain-identity-broken:" + state["session_id"])
    return state


# ------------------------------------------------------------------ ledger

def ledger_path(root):
    return Path(root) / "host-private" / "wc-ledger.jsonl"


def read_ledger(root):
    path = ledger_path(root)
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def append_ledger(root, row):
    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = read_ledger(root)
    row = dict(row, seq=len(rows) + 1, time=time.time())
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def ledger_state(rows, arms=EDIT_ARMS):
    """Replay: per arm head (tree id and path), tokens, seconds, sessions; done items; pending resumes; open launch."""
    st = {arm: {"head": None, "head_path": None, "tokens": 0, "seconds": 0.0, "sessions": 0} for arm in arms}
    done, resumes_due, resumed, open_launch = set(), [], set(), None
    for r in rows:
        if r["event"] == "launch":
            open_launch = r
        elif r["event"] == "end":
            open_launch = None
            arm = "LT" if r["arm"] == SURVEY_ARM else r["arm"]
            st[arm]["tokens"] += int(r.get("tokens") or 0)
            st[arm]["seconds"] += float(r.get("seconds") or 0)
            st[arm]["sessions"] += 1
            if r["arm"] in arms and r.get("action") == "advance":
                st[arm]["head"], st[arm]["head_path"] = r["head_after"], r["head_after_path"]
            done.add((r["arm"], r["kind"], r["area"]))
            if r.get("resume_due"):
                resumes_due.append((r["arm"], r["area"]))
            if r["kind"] == "resume":
                resumed.add((r["arm"], r["area"]))
        elif r["event"] == "skip":
            done.add((r["arm"], r["kind"], r["area"]))
        elif r["event"] == "halt":
            st["halted"] = r["reason"]
    pending = [x for x in resumes_due if x not in resumed]
    return {"arms": st, "done": done, "pending_resumes": pending, "open_launch": open_launch,
            "halted": st.get("halted")}


def over_budget(arm_state, budget=ARM_BUDGET):
    if arm_state["tokens"] >= budget["tokens"]:
        return "tokens"
    if arm_state["seconds"] >= budget["wall_seconds"]:
        return "wall_seconds"
    return None


def next_item(rows, items, budget=ARM_BUDGET):
    """(item, skips): the next session to run, after recording budget skips; resumes go first."""
    state = ledger_state(rows)
    if state["open_launch"] is not None:
        raise Refused("session-without-end-row:" + state["open_launch"]["namespace"] + ": ask the author")
    if state["halted"]:
        raise Refused("run-halted:" + state["halted"])
    skips = []
    for arm, area in state["pending_resumes"]:
        why = over_budget(state["arms"][arm], budget)
        item = {"arm": arm, "kind": "resume", "area": area}
        if why:
            skips.append(dict(item, reason=f"arm-budget:{why}"))
            continue
        return item, skips
    for item in items:
        if (item["arm"], item["kind"], item["area"]) in state["done"]:
            continue
        why = over_budget(state["arms"][item["arm"]], budget)
        if why:
            skips.append(dict(item, reason=f"arm-budget:{why}"))
            continue
        return item, skips
    return None, skips


# ------------------------------------------------------------------ session evaluation

def page_ids(gm, vm, tree, rels):
    by_rel = {p.rel_path: p.doc_id for p in rr.tree_graph(gm, Path(tree), dcv.scope_of(vm))[0]}
    return {r: by_rel.get(r) for r in rels}


def evaluate_session(ns, start_tree, *, gm, vm, base, schemas, kind, area_paths):
    """Admission on the cumulative tree, the session's own diff and its record; returns the ledger fields."""
    ns = Path(ns)
    sfb.residual_terminal(ns, gm=gm, vm=vm)
    rec = sfb.admission_record(ns, base)
    tree, _, _, src, _ = sfb.terminal_view(ns)
    out = {"status": rec["status"], "truncated": rec["truncated"], "stopped_by": rec["stopped_by"],
           "infrastructure_stop": rec["infrastructure_stop"], "terminal_source": src,
           "tokens": rec.get("editor_tokens"), "editor_wall_seconds": rec.get("editor_wall_seconds"),
           "admitted_cumulative": rec["admitted"], "not_admitted_because": rec["not_admitted_because"],
           "review": rec.get("review")}
    diff = pass_diff(start_tree, tree) if tree is not None else None
    out["pass_diff"] = diff
    message = sfb.final_message(ns / "host/transcript.jsonl")
    status, record, why = sfb.extract_record(message, "wc_session_record.v1", schemas)
    out["record"] = {"status": status, "reason": why, "done": (record or {}).get("done") if status == "valid" else None}
    if diff is not None:
        out["record"]["check"] = sfb.session_record_check(record if status == "valid" else None, diff, area_paths)
        out["provenance_declared"] = (record or {}).get("provenance", []) if status == "valid" else []
        ids_end = page_ids(gm, vm, tree, diff["added"] + diff["changed"])
        ids_start = page_ids(gm, vm, start_tree, diff["removed"])
        out["pass_doc_ids"] = {"added": [ids_end[r] for r in diff["added"]],
                               "changed": [ids_end[r] for r in diff["changed"]],
                               "removed": [ids_start[r] for r in diff["removed"]]}
    empty = diff is not None and not any(diff.values())
    if rec["infrastructure_stop"]:
        out["action"] = "halt"
    elif rec["status"] == "no_op" or empty:
        out["action"] = "noop"
    elif rec["admitted"]:
        out["action"] = "advance"
    else:
        out["action"] = "rollback"
    out["resume_due"] = bool(kind == "pass" and out["action"] == "advance" and rec["truncated"])
    out["terminal_tree"] = str(tree) if tree is not None else None
    out["terminal_id"] = tree_id(tree) if tree is not None else None
    return out


# ------------------------------------------------------------------ manifests and the queue runner

def manifest_key(arm, kind, area):
    return f"{arm}-{kind}-{area}"


def session_manifest(es, run_id, binding, *, fixture, area, kind, edits=None, extra=None):
    """A run manifest for one session (rr.launch reads one prompt per arm, so each session has its own binding)."""
    if fixture:
        limits = dict(rr.FIXTURE_LIMITS) if edits is None else dict(rr.FIXTURE_LIMITS, edits=edits)
        data = rr.build_manifest(run_id=run_id, variant_manifest_path=V6, mode="fixture", fixture_limits=limits,
                                 created=DATE, templates_path=binding,
                                 runtime=es.runtime_block(model=None, reasoning_effort=None, driver="scripted-shell-v1"))
    else:
        data = rr.build_manifest(
            run_id=run_id, variant_manifest_path=V6, mode="live", created=DATE, templates_path=binding,
            runtime=sfb.runtime_block(es), model_identity="gpt-6.1-sol (Codex CLI 0.161.0, reasoning effort medium)",
            decided_limits=dict(LIVE_LIMITS),
            stopping={"budget_stop_terminal_eligible_for_grading": True, "residual_terminal_rule": sfb.RESIDUAL_RULE})
    data["allowed"]["scope"].update({a: "restructure" for a in EDIT_ARMS + (SURVEY_ARM,)})
    data["stopping"]["budget_stop_terminal_eligible_for_grading"] = True
    data["stopping"]["residual_terminal_rule"] = sfb.RESIDUAL_RULE
    data["area"] = area
    data["session_kind"] = kind
    data["admission"] = sfb.ADMISSION_RULE_V2
    data["wc"] = {"design": {"path": sfb.rel_ctx(DESIGN), "sha256": sha(DESIGN)}, "arm_budget": ARM_BUDGET,
                  "budget_rule": BUDGET_RULE, "chaining": "seeded_copy; ledger checks start id == arm head"}
    if not fixture:
        data["arm_plan"] = {"O": {"replicates": 1}, "S": {"replicates": 1}, SURVEY_ARM: {"replicates": 1},
                            **{a: {"replicates": "one session per area, one resume per limit stop, one reconciliation"}
                               for a in EDIT_ARMS},
                            "R0_v4_note": "R0 is the qualified R0-v4 prompt scoped per area", "L": {"replicates": 0}}
        data.update(extra or {})
    return data


def write_session_files(root, es, *, run_id, fixture, sessions, base_binding_prompts=None):
    """sessions: {key: (arm, kind, area dict, prompt bytes, edits or None)}. Writes prompts, bindings and manifests
    under <root>; returns {key: manifest path}."""
    out = {}
    for key, (arm, kind, area, prompt, edits) in sessions.items():
        ppath = root / "prompts" / f"{key}.txt"
        ppath.parent.mkdir(parents=True, exist_ok=True)
        if ppath.exists() and ppath.read_bytes() != prompt:
            raise Refused(f"prompt-file-differs:{ppath.name}")
        ppath.write_bytes(prompt)
        prompts = {arm: (ppath, f"{key}-v1")}
        prompts.update(base_binding_prompts or {})
        bpath = root / "bindings" / f"{key}.json"
        sf.write_or_same(bpath, sfb.templates_binding(prompts, f"wc session binding {key}"))
        mpath = root / "manifests" / f"{key}.json"
        if not mpath.exists():
            write_json(mpath, session_manifest(es, run_id, bpath, fixture=fixture, area=area, kind=kind, edits=edits))
        out[key] = mpath
    return out


def run_queue(ctx, items, *, driver_for=None, live=False, log=print, max_sessions=None):
    """Run the queue to its end (or `max_sessions`): chained launches, admission, rollback, resume, ledger, budgets."""
    root, gm, vm, base, schemas = ctx["root"], ctx["gm"], ctx["vm"], ctx["base"], ctx["schemas"]
    ran = 0
    while max_sessions is None or ran < max_sessions:
        rows = read_ledger(root)
        item, skips = next_item(rows, items, ctx.get("budget", ARM_BUDGET))
        for s in skips:
            append_ledger(root, dict(s, event="skip"))
        if item is None:
            return
        arm, kind, area = item["arm"], item["kind"], item["area"]
        state = ledger_state(read_ledger(root))["arms"][arm]
        replicate = sum(1 for r in read_ledger(root) if r["event"] == "launch" and r["arm"] == arm) + 1
        name = f"{arm}-r{replicate:02d}"
        start_path = state["head_path"]
        start_id = state["head"] or ctx["baseline_id"]
        start_tree = Path(start_path) if start_path else ctx["baseline_tree"]
        mkey = manifest_key(arm, kind, area)
        manifest = ctx["manifests"][mkey]
        append_ledger(root, {"event": "launch", "arm": arm, "kind": kind, "area": area, "namespace": name,
                             "manifest_sha256": sha(manifest), "start_id": start_id})
        t0 = time.time()
        launch_state = chained_launch(manifest, arm, replicate, start_path, live=live,
                                      driver=None if live else driver_for(arm, kind, area))
        if launch_state["baseline_copy_tree_sha256"] != start_id:
            raise Refused(f"chain-start-differs-from-head:{name}")
        area_paths = ctx["areas"].get(area, []) if area != RECONCILE_AREA else []
        ev = evaluate_session(root / name, start_tree, gm=gm, vm=vm, base=base, schemas=schemas, kind=kind,
                              area_paths=area_paths)
        row = {"event": "end", "arm": arm, "kind": kind, "area": area, "namespace": name,
               "seconds": round(time.time() - t0, 1), "start_id": start_id, "head_before": start_id, **ev}
        if ev["action"] == "advance":
            row.update(head_after=ev["terminal_id"], head_after_path=ev["terminal_tree"])
        else:
            row.update(head_after=start_id, head_after_path=start_path,
                       rejected_tree=ev["terminal_id"] if ev["action"] == "rollback" else None)
        append_ledger(root, row)
        log(json.dumps({name: {k: row.get(k) for k in ("kind", "area", "status", "action", "truncated",
                                                         "not_admitted_because", "resume_due")}}))
        ran += 1
        if ev["action"] == "halt":
            append_ledger(root, {"event": "halt", "arm": arm, "kind": kind, "area": area,
                                 "reason": f"infrastructure_stop:{name}"})
            raise Refused(f"infrastructure-stop:{name}: halted; ask the author")


def authorized(root, point):
    """Authorization file (host-private/authorization.json) must record the author's yes for this point."""
    path = Path(root) / "host-private" / "authorization.json"
    if not path.exists():
        raise Refused(f"no-author-authorization-recorded: point {point} needs an explicit yes before any live call")
    data = rr.read_json(path)
    entries = [e for e in data.get("entries", []) if e.get("point") == point and e.get("author_yes")]
    if not entries:
        raise Refused(f"authorization-point-{point}-not-recorded")
    return entries[-1]


# ------------------------------------------------------------------ survey

def evaluate_survey(ns, schemas, baseline_rels):
    state = rr.read_json(Path(ns) / "host/run-state.json")
    message = sfb.final_message(Path(ns) / "host/transcript.jsonl")
    status, record, why = sfb.extract_record(message, "wc_area_partition.v1", schemas)
    out = {"namespace": Path(ns).name, "status": state["status"], "read_only_kept": state["status"] == "no_op",
           "editor_tokens": state.get("editor_tokens"), "editor_wall_seconds": state.get("editor_wall_seconds"),
           "record_status": status, "record_reason": why, "record": record}
    out["host_checks"] = partition_host_checks(record, baseline_rels) if status == "valid" else None
    out["valid"] = bool(status == "valid" and out["host_checks"]["passes"] and out["read_only_kept"])
    return out


# ------------------------------------------------------------------ provenance (O1, proposed)

PROVENANCE_RULE = {
    "version": "wc_provenance_credit_v1_proposed",
    "status": "proposed in Sprint 15; the author confirms or changes it before the Sprint 16 freeze",
    "unit": "rr.content_units (normalized non-blank line, link targets stripped, lines without alphanumerics dropped)",
    "distinctive_unit": "at least 30 characters and present in at most 3 baseline pages",
    "new_units": "units of the variant page that its own baseline version (if any) does not hold",
    "host_alignment": "baseline page B shares at least 1 distinctive new unit with variant page P",
    "credit": "P is derived from B if the host alignment holds (declared or not); a declared source without host "
              "alignment is reported as unconfirmed and earns no credit",
    "equivalence": "support unit B on variant v = {B if B survives in v} + {B's survivor under moved_content_majority_v1"
                   " if B was removed} + {every added or changed page derived from B}",
    "scope": "computed once on each arm's final tree against O; declarations are pooled over the arm's sessions",
}
MIN_UNIT_CHARS = 30
MAX_UNIT_PAGES = 3


def unit_index(base_pages):
    units = {p.doc_id: rr.content_units(p.text) for p in base_pages}
    index = defaultdict(set)
    for d, us in units.items():
        for u in us:
            index[u].add(d)
    return units, index


def derived_sources(base_pages, var_pages, declared=None):
    """{variant doc id: {"derived": [baseline ids], "declared": [...], "unconfirmed": [...]}} for added/changed pages."""
    base = {p.doc_id: p for p in base_pages}
    rel_to_base = {p.rel_path: p.doc_id for p in base_pages}
    units, index = unit_index(base_pages)
    declared = declared or {}
    out = {}
    for p in var_pages:
        if p.doc_id in base and base[p.doc_id].text == p.text:
            continue
        mine = rr.content_units(p.text) - units.get(p.doc_id, set())
        hits = Counter()
        for u in mine:
            owners = index.get(u, ())
            if len(u) >= MIN_UNIT_CHARS and 0 < len(owners) <= MAX_UNIT_PAGES:
                for d in owners:
                    if d != p.doc_id:
                        hits[d] += 1
        decl = sorted({rel_to_base.get(sfb.norm_path(s)) or f"?{s}" for s in declared.get(p.rel_path, [])})
        out[p.doc_id] = {"rel_path": p.rel_path, "derived": sorted(hits), "shared_units": dict(hits),
                         "declared": decl, "unconfirmed": [d for d in decl if d not in hits]}
    return out


def pooled_declarations(rows, arm):
    out = defaultdict(set)
    for r in rows:
        if r.get("event") == "end" and r["arm"] == arm and r.get("action") == "advance":
            for item in r.get("provenance_declared") or []:
                out[sfb.norm_path(item["page"])].update(item["sources"])
    return {k: sorted(v) for k, v in out.items()}


def credited_equivalence(base_pages, var_pages, mapping_receipt, derived):
    """{baseline id: sorted variant ids credited for it} under PROVENANCE_RULE."""
    var_ids = {p.doc_id for p in var_pages}
    eq = {}
    for p in base_pages:
        d = p.doc_id
        ids = set()
        if d in var_ids:
            ids.add(d)
        elif d in mapping_receipt["mapping"]:
            ids.add(mapping_receipt["mapping"][d])
        eq[d] = ids
    for v, info in derived.items():
        for d in info["derived"]:
            if d in eq:
                eq[d].add(v)
    return {d: sorted(ids) for d, ids in eq.items()}


def host_provenance_check(declared, derived):
    """Per declared page: declared sources confirmed by the host alignment, unconfirmed, and host-only sources."""
    rows = []
    by_rel = {info["rel_path"]: info for info in derived.values()}
    for page, sources in sorted(declared.items()):
        info = by_rel.get(page)
        if info is None:
            rows.append({"page": page, "status": "page-not-added-or-changed"})
            continue
        rows.append({"page": page, "confirmed": [d for d in info["declared"] if d in info["derived"]],
                     "unconfirmed": info["unconfirmed"],
                     "host_only": [d for d in info["derived"] if d not in info["declared"]]})
    return rows


# ------------------------------------------------------------------ variants (final tree of an arm)

def variant_context(gm, vm, tree, *, ledger_rows=None, arm=None):
    """Everything later stages need about one variant tree: pages, graph, mapping, O', touched, credit, edit areas."""
    base_list = rr.baseline_pages(gm, vm)
    var_list, nodes, v_edges = rr.tree_graph(gm, Path(tree), dcv.scope_of(vm))
    receipt = rr.derive_mapping(base_list, var_list)
    o_edges = sf.canonical(gm.extract_markdown_edges(base_list))
    om = sf.mapped_edges(o_edges, receipt["mapping"], nodes)
    declared = pooled_declarations(ledger_rows or [], arm) if arm else {}
    derived = derived_sources(base_list, var_list, declared)
    eq = credited_equivalence(base_list, var_list, receipt, derived)
    ctx = {"tree": str(tree), "base_list": base_list, "var_list": var_list, "nodes": nodes,
           "v_edges": sf.canonical(v_edges), "o_mapped": om, "receipt": receipt, "touched": sf.touched_set(receipt),
           "derived": derived, "declared": declared, "equivalence": eq,
           "added_edges": sorted(set(sf.canonical(v_edges)) - set(om))}
    ctx["edit_area"] = edit_areas(ledger_rows or [], arm, var_list) if arm else {}
    return ctx


def edit_areas(rows, arm, var_list):
    """Variant doc id -> the area of the first admitted session of `arm` that added or changed it."""
    present = {p.doc_id for p in var_list}
    out = {}
    for r in rows:
        if r.get("event") == "end" and r["arm"] == arm and r.get("action") == "advance":
            for d in (r.get("pass_doc_ids") or {}).get("added", []) + (r.get("pass_doc_ids") or {}).get("changed", []):
                if d in present and d not in out:
                    out[d] = r["area"]
    return out


# ------------------------------------------------------------------ controls (D5)

def area_buckets(vctx):
    """Touched pages, added pages and edge edits per bucket (area, or ALL for reconciliation-only pages).

    Each touched page belongs to the first admitted session that added or changed it; a page touched only through a
    removal maps by its survivor. An added or removed edge belongs to its source page's bucket, else its target's;
    edges with neither endpoint touched are reported as unattributed and stay as in O'."""
    area_of = vctx["edit_area"]
    touched = set(vctx["touched"])
    receipt = vctx["receipt"]
    survivors = {receipt["mapping"][d] for d in receipt["removed"] if d in receipt["mapping"]}
    bucket_of = {}
    for t in sorted(touched):
        bucket_of[t] = area_of.get(t, "unattributed")
    unattributed_pages = sorted(t for t, b in bucket_of.items() if b == "unattributed")
    o, v = set(vctx["o_mapped"]), set(vctx["v_edges"])
    added, removed = sorted(v - o), sorted(o - v)
    buckets = defaultdict(lambda: {"T": set(), "added_pages": set(), "added_edges": [], "removed_edges": []})
    for t, b in bucket_of.items():
        buckets[b]["T"].add(t)
    for d in receipt["added"]:
        if d in bucket_of:
            buckets[bucket_of[d]]["added_pages"].add(d)
    unattributed_edges = []
    for kind, edges in (("added_edges", added), ("removed_edges", removed)):
        for s, t in edges:
            b = bucket_of.get(s) or bucket_of.get(t)
            if b is None:
                unattributed_edges.append([kind, s, t])
            else:
                buckets[b][kind].append((s, t))
    out = {}
    for b, x in buckets.items():
        out[b] = {"T": sorted(x["T"]), "added_pages": sorted(x["added_pages"]), "a": len(x["added_edges"]),
                  "r": len(x["removed_edges"]),
                  "f": sf.reciprocal_fraction(v, x["added_edges"]) if x["added_edges"] else 0.0,
                  "added_edges": sorted(x["added_edges"])}
    return out, {"edges": unattributed_edges, "pages": unattributed_pages,
                 "survivor_pages": sorted(survivors & touched)}


def null_seed(null_type, variant_id, bucket, k):
    return int(hashlib.sha256(f"{SALTS[null_type]}{variant_id}:{bucket}:{k}".encode()).hexdigest(), 16)


def pc_inbound(o_mapped, v_edges, new_pages, area_nodes, seed):
    """PC part of a bucket: each new page gets as many one-way inbound links as it has in v, from random pages of the
    area (never a new page, never a pair whose reverse exists); returns (edges, receipt)."""
    rng = random.Random(seed)
    v = set(v_edges)
    current = set(o_mapped)
    placed, plan = [], {}
    pool_base = sorted(set(area_nodes) - set(new_pages))
    for p in sorted(new_pages):
        need = sum(1 for s, t in v if t == p)
        pool = [u for u in pool_base if u != p and (u, p) not in current and (p, u) not in current]
        if need > len(pool):
            raise Refused(f"pc-inbound-pool-too-small:{p}:{need}>{len(pool)}")
        chosen = rng.sample(pool, need)
        for u in chosen:
            current.add((u, p))
            placed.append((u, p))
        plan[p] = need
    return placed, {"new_pages": len(new_pages), "inbound_per_page": plan, "placed": len(placed)}


def compose_null(null_type, vctx, buckets, variant_id, k, area_nodes):
    """One null graph on v's node set: O' plus, per bucket, that bucket's null edits (LN, PC or RN); combined."""
    om, nodes, v_edges = vctx["o_mapped"], vctx["nodes"], vctx["v_edges"]
    o = set(om)
    adds, removes, receipts = set(), set(), {}
    for b in sorted(buckets):
        x = buckets[b]
        T = x["T"]
        if not T or (x["a"] == 0 and x["r"] == 0):
            continue
        seed = null_seed(null_type, variant_id, b, k)
        a, r, f = x["a"], x["r"], x["f"]
        K = ln.locality_region(T, om, v_edges, x["added_pages"])
        try:
            edges, rec, rec_pc = _bucket_null(null_type, om, nodes, v_edges, x, T, K, a, r, f, seed,
                                              area_nodes.get(b, nodes))
        except Refused as error:  # the frozen LN rule refuses rather than falls back (Sprint 13)
            raise Refused(f"null-not-computable:{null_type}:{variant_id}:{b}:k{k}:{error}") from None
        es = set(edges)
        adds.update(es - o)
        removes.update(o - es)
        receipts[b] = {"a": x["a"], "r": x["r"], "f": x["f"], "T": len(T), "rule_receipt": rec, "pc": rec_pc}
    overlap_adds = sum(1 for e in adds if e in o)
    final = sorted((o - removes) | (adds - removes))  # a PC edge a later draw removed stays removed
    return final, {"null_type": null_type, "variant_id": variant_id, "k": k, "buckets": receipts,
                   "added_total": len(set(final) - o), "removed_total": len(o - set(final)),
                   "planned_added": sum(x["a"] for x in buckets.values()),
                   "planned_removed": sum(x["r"] for x in buckets.values()), "added_existing_edges": overlap_adds}


def _bucket_null(null_type, om, nodes, v_edges, x, T, K, a, r, f, seed, area_nodes):
    """One bucket's null edits as a full edge list over O' (plus, for PC, the placed inbound links)."""
    if null_type == "PC" and x["added_pages"]:
        # removals exactly as LN on O'; then the new pages' one-way inbound links; then LN additions for the rest
        removed_edges, rec_r = ln.locality_null(om, T, K, 0, r, f, seed)
        current = set(removed_edges)
        placed, rec_pc = pc_inbound(sorted(current), v_edges, x["added_pages"], area_nodes, seed + 1)
        current |= set(placed)
        k_recip = round(f * a) if a else 0
        a_rest = a - len(placed)
        f_rest = min(1.0, k_recip / a_rest) if a_rest > 0 else 0.0
        edges, rec_a = ln.locality_null(sorted(current), T, sorted(set(K) | set(x["added_pages"])), a_rest, 0,
                                        f_rest, seed + 2)
        return edges, {"removal": rec_r, "addition": rec_a, "a_rest": a_rest, "f_rest": f_rest}, rec_pc
    if null_type == "RN":
        edges, rec = sf.random_null(nodes, om, T, a, r, f, seed)
    else:  # LN, and PC for a bucket without new pages
        edges, rec = ln.locality_null(om, T, K, a, r, f, seed)
    return edges, rec, None


def area_node_sets(vctx, areas):
    """Variant doc ids per area (by rel path); ALL and unattributed: every node."""
    out = {a: sorted(p.doc_id for p in vctx["var_list"] if sfb.in_area(p.rel_path, paths)) for a, paths in areas.items()}
    out[RECONCILE_AREA] = list(vctx["nodes"])
    out["unattributed"] = list(vctx["nodes"])
    return out


# ------------------------------------------------------------------ spectra: pinned profile plus flow imbalance

FLOW_PIN = {"version": "cikm_magnetic_flow_imbalance_v1",
            "function": "docspectra_importer.graph_metrics.magnetic_laplacian_metrics(...)['flow_imbalance'], q 0.25, "
                        "binary edges, on the retrieval_no_faq_lwcc view",
            "status": "pinned as a reported descriptor for A3; Sprint 09 found the historical phase-flow construct "
                      "sign-unstable under near-zero edge signs (blocked as a scientific measure); descriptive only",
            "kept_outside": "the receipt's profile block, so profile_sha256 (the 8 pinned measures and the q sweep) is "
                            "byte-equal to rr.spectral_profile"}


def flow_function_sha(gm):
    return sha_text(inspect.getsource(gm.magnetic_laplacian_metrics))


def spectral_receipt(gm, nodes, edges, anchors, mapping, pin):
    """rr.spectral_profile unchanged, plus the pinned flow imbalance on the same LWCC as a separate block."""
    rec = rr.spectral_profile(nodes, edges, anchors, mapping, pin)
    o9, mp = rr.load_spectral()
    lw, le = sf.lwcc_of(o9, mp, nodes, edges)
    m = gm.magnetic_laplacian_metrics(lw, le, q=0.25)
    rec["flow_imbalance"] = {"value": m["flow_imbalance"], "pin": dict(FLOW_PIN, function_sha256=flow_function_sha(gm),
                                                                       graph_metrics_sha256=sha(Path(gm.__file__))),
                             "lwcc_nodes": len(lw), "lwcc_edges": len(le)}
    return rec


def subgraph_rows(o9, mp, S, graphs, o_mapped):
    rows = {}
    for gid, edges in graphs:
        ind = sf.induced(edges, S)
        added = sorted(set(edges) - set(o_mapped))
        rows[gid] = dict(sf.measure_vector(o9, mp, S, ind), added_edges=len(added),
                         added_inside_S=sum(1 for e in added if e in set(ind)))
    return rows


def sign_flip_test(diffs, two_sided=True):
    """Exact sign-flip permutation over areas of the mean paired difference (LT - R0 z); 2^n assignments."""
    import numpy as np
    d = np.asarray([x for x in diffs if x is not None], dtype=float)
    n = len(d)
    if n == 0:
        return {"n": 0, "p": None}
    obs = d.mean()
    hits, total = 0, 0
    chunk = 1 << min(n, 20)
    for start in range(0, 1 << n, chunk):
        masks = np.arange(start, min(start + chunk, 1 << n), dtype=np.int64)
        signs = 1 - 2 * ((masks[:, None] >> np.arange(n)) & 1)
        stats = (signs * d).mean(axis=1)
        if two_sided:
            hits += int(np.sum(np.abs(stats) >= abs(obs) - 1e-12))
        else:
            hits += int(np.sum(stats >= obs - 1e-12))
        total += len(masks)
    return {"n": n, "statistic_mean": float(obs), "p": hits / total, "assignments": total,
            "minimum_p": (2 if two_sided else 1) / total, "two_sided": two_sided}


# ------------------------------------------------------------------ answer traces (D7) and navigation metrics (D8)

def cited_ids(trace):
    ans = trace.get("answer") or {}
    return sorted({c["doc_id"] for c in ans.get("citations") or [] if isinstance(c, dict) and c.get("doc_id")})


def annotate_trace(trace, vctx):
    """Added trace fields (D7), computed from a 12b agent trace and the variant; the agent loop is unchanged."""
    added = set(map(tuple, vctx["added_edges"]))
    area = vctx["edit_area"]
    follows, opened = [], {}
    for step in trace.get("steps", []):
        act = step.get("action") or {}
        kind = act.get("action")
        if kind == "follow":
            edge = (act.get("source_doc_id"), act.get("doc_id"))
            follows.append({"call": step.get("call"), "source": edge[0], "target": edge[1],
                            "editor_added_edge": edge in added})
        if kind in ("read", "follow") and act.get("doc_id"):
            opened.setdefault(act["doc_id"], step.get("call"))
    cited = cited_ids(trace)
    pages = sorted(set(opened) | set(cited))
    return {"schema_version": "docspectra.icsc.wc_refinement_15.v1.trace_fields",
            "follows": follows,
            "pages": [{"doc_id": d, "read": d in opened, "cited": d in cited, "edited": d in area,
                       "edit_area": area.get(d)} for d in pages]}


def credited_support(support_ids, equivalence):
    return {b: set(equivalence.get(b, [])) for b in support_ids}


def navigation_metrics(trace, support_ids, vctx):
    """Tier-2 navigation measures for one trace against the credited support of its question on the variant."""
    eq = credited_support(support_ids, vctx["equivalence"])
    targets = set().union(*eq.values()) if eq else set()
    added = set(map(tuple, vctx["added_edges"]))
    searches, first = 0, None
    follows = follows_support = follows_added = 0
    reached = set()
    for step in trace.get("steps", []):
        act = step.get("action") or {}
        kind = act.get("action")
        if kind == "search":
            searches += 1
        if kind in ("read", "follow"):
            d = act.get("doc_id")
            if d in targets:
                reached.add(d)
                if first is None:
                    first = searches
            if kind == "follow":
                follows += 1
                follows_support += d in targets
                follows_added += (act.get("source_doc_id"), d) in added
    cited = set(cited_ids(trace))
    units_read = sum(1 for ids in eq.values() if ids & reached)
    return {"support_read": bool(reached), "support_cited": bool(cited & targets),
            "support_read_or_cited": bool(reached or (cited & targets)),
            "support_units": len(eq), "support_units_read": units_read,
            "searches_to_first_support": first, "searches": searches, "follows": follows,
            "follows_landing_on_support": follows_support, "follows_over_editor_added_edges": follows_added,
            "share_follows_over_editor_added_edges": (follows_added / follows) if follows else None}


# ------------------------------------------------------------------ fixed retrievers (D8, O5)

RA_COMMIT = "ffe09f1124fe7a08380af60dea82b36737ca2fe0"
MODEL_DIR = rr.ROOT / "artifacts/docker-cikm-2026-05-14/model"
MODEL_LOCK = rr.ROOT / "artifacts/docker-cikm-2026-05-14/provenance/embedding_models.lock.json"
RETRIEVERS = {
    "lexical": {"provider": "rag_lexical_topk", "image": "retrieval-arena/rag-lexical-topk:s024",
                "image_id": "sha256:f2f5a80146491273ce67ad4d0d6c54bf14dfc521f9668a62e470416744eb18f2",
                "runner_sha256": "b001af0f4eb426dd6c82a88d992ce7a5a18b63ca17237c00003333f7c8015e24",
                "config": {"top_k": 10, "max_context_chars_per_doc": 1500, "answer_policy": "head_of_document_v1"}},
    "embedding": {"provider": "rag_embedding_topk", "image": "retrieval-arena/rag-embedding-topk:latest",
                  "image_id": "sha256:eac5614e9d9e887bf4457d2bd40396a1a338b95e4b3cdd3a3b681eaf1d511569",
                  "runner_sha256": "3386415574830ee89bfdba402123fef3fe30202a2dbde1e262315d01661ec600",
                  "config": {"top_k": 10, "max_context_chars_per_doc": 1500,
                             "embedding_backend": "sentence_transformers_local",
                             "model_id": "sentence-transformers/all-MiniLM-L6-v2",
                             "model_revision": "c9745ed1d9f207416be6d2e6f8de32d1f16199bf",
                             "model_lock_path": "/model_provenance/embedding_models.lock.json",
                             "model_cache_path": "/models/minilm", "batch_size": 32},
                  "mounts": {"model": "/models/minilm", "lock": "/model_provenance"},
                  "runtime_versions": {"sentence_transformers": "5.5.0", "torch": "2.11.0+cu130",
                                       "transformers": "5.8.1"}},
    "graph_expand": {"provider": "rag_graph_ranked_expand", "image": "retrieval-arena/rag-graph-ranked-expand:s025",
                     "image_id": "sha256:f8ace4e954772119ed719159674e6a94ac5448a71ebd9a8ef29840c8fbe3697f",
                     "runner_sha256": "16bf70162a0fd8737e118c75f5889e1d64764a407f65b1d2dc3390899df0929c",
                     "config": {"seed_top_k": 6, "neighbor_budget": 4, "max_context_chars_per_doc": 1200,
                                "answer_policy": "head_of_document_v1"}},
}
RETRIEVER_NOTES = [
    "k = 10 for every retriever so that nDCG@10 has 10 ranked items; Recall@5 uses the first 5 (design D8)",
    "graph-expand is RA's rag_graph_ranked_expand (BM25 seeds + ranked one-hop neighbours, 6 + 4): rag_graph_expand "
    "returns at most 2 items whatever top_k is, so it cannot carry Recall@5 or nDCG@10",
    "the embedding image runs sentence-transformers 5.5.0 / transformers 5.8.1; the model lock records 5.4.1 / 5.7.0 "
    "from the May 2026 CIKM run; the snapshot bytes match the lock (checked at run time by the runner)",
    "scoring is in this driver (credited equivalence sets, PROVENANCE_RULE), not RA score_ranked: RA's qrels have one "
    "id per target and its pinned engine ir-measures==0.4.3 is not installed on this host; on O the two coincide "
    "(identity equivalence) and an RA cross-check on O is a follow-up",
    "runner bytes inside each image equal tests/<provider>/runner.py at RA " + RA_COMMIT,
]


def image_id(ref):
    out = subprocess.run(["docker", "image", "inspect", ref, "--format", "{{.Id}}"], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else None


def check_retriever_pins():
    problems = []
    for name, r in RETRIEVERS.items():
        if image_id(r["image"]) != r["image_id"]:
            problems.append(f"image-drift:{name}")
        src = rr.RA / "tests" / r["provider"] / "runner.py"
        if sha(src) != r["runner_sha256"]:
            problems.append(f"runner-source-drift:{name}")
    if not MODEL_DIR.is_dir() or not MODEL_LOCK.is_file():
        problems.append("embedding-model-or-lock-missing")
    return problems


def run_retriever(name, export_dir, questions, dest, *, timeout=1800):
    """Run one pinned RA retriever image offline on an exported variant; returns ranked doc ids per question."""
    r = RETRIEVERS[name]
    if image_id(r["image"]) != r["image_id"]:
        raise Refused(f"retriever-image-drift:{name}")
    dest = Path(dest)
    inp, out = dest / "input", dest / "output"
    if dest.exists():
        raise Refused(f"retriever-output-exists:{dest}")
    shutil.copytree(Path(export_dir) / "corpus", inp / "corpus")
    shutil.copyfile(Path(export_dir) / "graph_edges.csv", inp / "graph_edges.csv")
    with (inp / "questions.jsonl").open("w", encoding="utf-8", newline="\n") as s:
        for q in questions:
            s.write(json.dumps({"question_id": q["question_id"], "question": q["question"]}, sort_keys=True) + "\n")
    (inp / "answers.jsonl").write_text("", encoding="utf-8")
    (inp / "config.yaml").write_text(json.dumps(r["config"], sort_keys=True), encoding="utf-8")
    out.mkdir(parents=True)
    argv = ["docker", "run", "--rm", "--network", "none", "-v", f"{inp}:/input:ro", "-v", f"{out}:/output"]
    if name == "embedding":
        argv += ["-v", f"{MODEL_DIR}:/models/minilm:ro", "-v", f"{MODEL_LOCK.parent}:/model_provenance:ro"]
    argv.append(r["image_id"])
    t0 = time.time()
    res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        raise Refused(f"retriever-failed:{name}:{res.stderr[-400:]}")
    shutil.rmtree(inp / "corpus")
    ranked = {}
    for line in (out / "predictions.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        items = sorted(row["retrieved_context"], key=lambda c: c.get("rank", 0))
        ranked[row["question_id"]] = [c["doc_id"] for c in items]
    meta = rr.read_json(out / "metadata.json")
    return ranked, {"seconds": round(time.time() - t0, 1), "metadata": meta,
                    "predictions_sha256": sha(out / "predictions.jsonl")}


def ranked_scores(ranking, support_ids, equivalence, k_recall=5, k_ndcg=10):
    """Recall@5, nDCG@10 and RR (MRR item) against credited support units (each baseline support page is one unit,
    covered by any of its credited variant ids; a unit counts once). On O this is plain binary Recall/nDCG/RR."""
    eq = credited_support(support_ids, equivalence)
    units = list(eq)
    if not units:
        return {"recall@5": None, "ndcg@10": None, "rr": None}
    covered, gains = set(), []
    first = None
    for rank, doc in enumerate(ranking[:max(k_recall, k_ndcg)], 1):
        new = [u for u in units if u not in covered and doc in eq[u]]
        if new:
            covered.add(new[0])
            if first is None:
                first = rank
        gains.append(1.0 if new else 0.0)
    recall = sum(1 for u in units if any(d in eq[u] for d in ranking[:k_recall])) / len(units)
    dcg = sum(g / math.log2(i + 1) for i, g in enumerate(gains[:k_ndcg], 1))
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(len(units), k_ndcg) + 1))
    return {"recall@5": recall, "ndcg@10": dcg / ideal if ideal else None,
            "rr": (1 / first) if first and first <= k_ndcg else 0.0}


def macro(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


# ------------------------------------------------------------------ quote survival (D4)

def norm_quote(text):
    return " ".join(rr.LINK_TEXT.sub(r"\1", text).split())


def quote_elements(task):
    """Every {page_id, exact_quote} pair found anywhere in a task record (elements carry their own ids if present)."""
    out = []

    def walk(x, path):
        if isinstance(x, dict):
            if x.get("page_id") and x.get("exact_quote"):
                out.append({"element": x.get("element_id") or path, "page_id": x["page_id"], "quote": x["exact_quote"]})
            for k, v in x.items():
                walk(v, f"{path}/{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{path}[{i}]")
    walk(task, "")
    return out


def quote_survival(elements, vctx):
    texts = {p.doc_id: norm_quote(p.text) for p in vctx["var_list"]}
    rows = []
    for e in elements:
        q = norm_quote(e["quote"])
        credited = vctx["equivalence"].get(e["page_id"], [])
        rows.append({"element": e["element"], "page_id": e["page_id"],
                     "in_credited_page": any(q in texts.get(d, "") for d in credited),
                     "anywhere": any(q in t for t in texts.values())})
    return {"elements": len(rows), "in_credited_page": sum(r["in_credited_page"] for r in rows),
            "anywhere": sum(r["anywhere"] for r in rows), "rows": rows}


# ------------------------------------------------------------------ fixture (zero model calls)

FIXTURE_BOUNDS = (3, 25)
FIXTURE_AREA_PREFIXES = {"A01": "content/manuals/engine/network/", "A02": "content/manuals/engine/storage/"}
FIXTURE_STOP_EDITS = 3


def cover_prefixes(rels, taken):
    """Minimal directory prefixes (or single pages) covering every rel not under a `taken` prefix, never overlapping
    a taken one."""
    def blocked(prefix):
        return any(t.startswith(prefix) or prefix.startswith(t) for t in taken)
    out = []

    def place(prefix, members):
        if not blocked(prefix):
            out.append(prefix)
            return
        children = defaultdict(list)
        for r in members:
            rest = r[len(prefix):]
            if "/" in rest:
                children[prefix + rest.split("/", 1)[0] + "/"].append(r)
            else:
                out.append(r)
        for child, mem in sorted(children.items()):
            if any(child.startswith(t) for t in taken):
                continue
            place(child, mem)
    tops = defaultdict(list)
    for r in rels:
        if any(r.startswith(t) for t in taken):
            continue
        tops[r.split("/", 1)[0] + "/" if "/" in r else r].append(r)
    for top, mem in sorted(tops.items()):
        if top.endswith("/"):
            place(top, mem)
        else:
            out.append(top)
    return sorted(out)


def fixture_partition(rels):
    a3 = cover_prefixes(rels, list(FIXTURE_AREA_PREFIXES.values()))
    areas = [{"area_id": a, "paths": [p], "page_count": sum(1 for r in rels if r.startswith(p)), "rationale": "fixture"}
             for a, p in FIXTURE_AREA_PREFIXES.items()]
    areas.append({"area_id": "A03", "paths": a3, "page_count": sum(1 for r in rels if sfb.in_area(r, a3)),
                  "rationale": "fixture: every other page"})
    inventory = [{"trunk_id": "T01", "topic": "fixture: shared network procedures",
                  "pages": [r for r in rels if r.startswith(FIXTURE_AREA_PREFIXES["A01"])][:3],
                  "proposed_page": FIXTURE_AREA_PREFIXES["A01"] + "shared-network-procedures.md"}]
    return {"schema": "wc_area_partition.v1", "areas": areas, "trunk_inventory": inventory, "method": "fixture"}


def fixture_pages(tree_pages, prefix, n=8):
    pick = sorted(p.rel_path for p in tree_pages if p.rel_path.startswith(prefix)
                  and not Path(p.rel_path).name.startswith("_index"))
    if len(pick) < n:
        raise Refused(f"fixture-area-too-small:{prefix}")
    return pick[:n]


def rel_link(src, dst):
    return os.path.relpath(dst, os.path.dirname(src)).replace("\\", "/")


def distinctive_line(text):
    """A long prose line of a page (used to exercise provenance credit)."""
    for line in text.splitlines():
        s = line.strip()
        if len(s) >= 60 and s[0].isalpha() and "](" not in s and "|" not in s and not s.startswith(("title:", "description:", "keywords:")):
            return s
    raise Refused("fixture-no-distinctive-line")


def fixture_ops(arm, kind, area, tree_pages, partition, edges=()):
    """Scripted shell batches and the declared provenance for one fixture session."""
    by_rel = {p.rel_path: p.text for p in tree_pages}
    if kind == "reconcile":
        # a back-link from an A01 page to a page that already links to it (inside the LN locality region)
        rel_of = {p.doc_id: p.rel_path for p in tree_pages}
        es = set(edges)
        for a in fixture_pages(tree_pages, FIXTURE_AREA_PREFIXES["A01"]):
            ida = next(d for d, r in rel_of.items() if r == a)
            back = sorted(rel_of[s] for s, t in es if t == ida and (ida, s) not in es and s in rel_of)
            if back:
                b = back[0]
                text = by_rel[a].rstrip("\n") + f"\n\nSee also: [{Path(b).stem}]({rel_link(a, b)}).\n"
                return [[("write", a, text.encode())]], [], [a], []
        raise Refused("fixture-no-back-link-candidate")
    prefix = FIXTURE_AREA_PREFIXES.get(area, "content/manuals/compose/how-tos/")  # A03 holds compose/how-tos
    pick = fixture_pages(tree_pages, prefix)
    if arm == "LT":
        hub = prefix + ("shared-procedures.md" if kind == "pass" else "shared-procedures-2.md")
        if hub in by_rel:
            hub = prefix + "shared-procedures-3.md"
        quote = distinctive_line(by_rel[pick[1]])
        ops = [("write", hub, (f"---\ntitle: Shared procedures ({area})\n---\n\n{quote}\n").encode())]
        leaves = pick[2:6] if kind == "pass" else pick[6:8]
        for rel in leaves:
            ops.append(("write", rel, (by_rel[rel].rstrip("\n") + f"\n\nSee [shared procedures]({rel_link(rel, hub)}).\n").encode()))
        return [ops], [{"page": hub, "sources": [pick[1]]}], [r for _, r, _ in ops[1:]], [hub]
    pairs = [(pick[0], pick[1]), (pick[2], pick[3])] if kind == "pass" else [(pick[4], pick[5])]
    texts = {}
    for x, y in pairs:
        texts[x] = by_rel[x].rstrip("\n") + f"\n\nRelated: [{Path(y).stem}]({rel_link(x, y)}).\n"
        texts[y] = by_rel[y].rstrip("\n") + f"\n\nRelated: [{Path(x).stem}]({rel_link(y, x)}).\n"
    return [[("write", r, t.encode()) for r, t in texts.items()]], [], sorted(texts), []


def fixture_failure_ops(tree_pages, area):
    """Forced admission failure: delete a page whose content survives nowhere (unmapped removal)."""
    pick = fixture_pages(tree_pages, FIXTURE_AREA_PREFIXES.get(area, "content/manuals/compose/how-tos/"))
    return [[("delete", pick[7])]], [pick[7]]


def fixture_record(arm, kind, area, paths, edited, created, removed, provenance):
    return {"schema": "wc_session_record.v1", "arm": arm, "kind": kind, "area_id": area, "area_paths": paths,
            "pages_in_area": 0, "pages_edited": edited, "pages_created": created, "pages_removed": removed,
            "done": True, "not_done_reason": None, "provenance": provenance, "preservation_concerns": [],
            "elapsed_minutes_at_stop": 1}


def fixture_trace(vctx, support_ids):
    """A synthetic 12b-format trace: search, read a non-support page, search, follow an editor-added edge onto a
    credited support page, read it, answer citing it."""
    eq = vctx["equivalence"]
    added = vctx["added_edges"]
    targets = {d for b in support_ids for d in eq.get(b, [])}
    edge = next((e for e in added if e[1] in targets), added[0])
    other = next(d for d in vctx["nodes"] if d not in targets and d != edge[0])
    steps = [{"call": 1, "action": {"action": "search", "query": "fixture"}},
             {"call": 2, "action": {"action": "read", "doc_id": other, "start_line": 1, "line_count": 40}},
             {"call": 3, "action": {"action": "search", "query": "fixture 2"}},
             {"call": 4, "action": {"action": "read", "doc_id": edge[0], "start_line": 1, "line_count": 40}},
             {"call": 5, "action": {"action": "follow", "source_doc_id": edge[0], "doc_id": edge[1],
                                    "start_line": 1, "line_count": 40}},
             {"call": 6, "action": {"action": "answer", "text": "fixture",
                                    "citations": [{"doc_id": edge[1], "start_line": 1, "end_line": 3}]}}]
    return {"schema_version": "docspectra.icsc.complex_task_agent_trace.v2", "question_id": "fixture",
            "steps": steps, "terminal": "answer",
            "answer": {"text": "fixture", "citations": [{"doc_id": edge[1], "start_line": 1, "end_line": 3}]}}


def fixture(_args):
    """Scripted 3-area end to end: survey -> 3 chained passes per arm (one forced limit stop with resume, one forced
    admission failure with rollback) -> reconciliation; then LN/PC/RN nulls, spectra, retrievers, a synthetic trace."""
    if FIXTURE_EVIDENCE.exists():
        raise Refused("fixture-already-run")
    t_start = time.time()
    stage = {}
    es = rr.load_sandbox()
    vm = rr.read_json(V6)
    gm = dcv.pinned(vm)
    base = sfb.Baseline(gm, vm)
    root = sfb.sf_run_root(FIXTURE_RUN_ID)
    schemas = record_schemas(FIXTURE_BOUNDS)
    sf.write_or_same(root / "record-schemas.json", schemas)
    partition = fixture_partition(base.rels)
    # survey (WS, zero calls) on a fresh baseline copy; O and S controls under the survey manifest
    survey_key = manifest_key(SURVEY_ARM, "survey", "ALL")
    manifests = write_session_files(root, es, run_id=FIXTURE_RUN_ID, fixture=True, sessions={
        survey_key: (SURVEY_ARM, "survey", None, survey_prompt(FIXTURE_BOUNDS), None)})
    if not (root / "WS-r01").exists():
        chained_launch(manifests[survey_key], SURVEY_ARM, 1, None,
                       driver=sfb.RecordingScriptedDriver(es, [], sfb.fenced(partition)))
    survey = evaluate_survey(root / "WS-r01", schemas, base.rels)
    if not survey["valid"]:
        raise Refused("fixture-survey-invalid:" + json.dumps(survey["host_checks"] or survey["record_reason"])[:300])
    for arm in ("O", "S"):
        if not (root / f"{arm}-r01").exists():
            rr.launch(manifests[survey_key], arm, 1)
    if not read_ledger(root):
        append_ledger(root, {"event": "end", "arm": SURVEY_ARM, "kind": "survey", "area": RECONCILE_AREA,
                             "namespace": "WS-r01", "tokens": survey["editor_tokens"] or 0,
                             "seconds": survey["editor_wall_seconds"] or 0, "action": "survey",
                             "valid": survey["valid"]})
    stage["survey"] = round(time.time() - t_start, 1)
    areas = survey["host_checks"]["paths"]
    order = area_order(list(areas))
    items = planned_items(order)
    trunk = survey["record"]["trunk_inventory"]
    base_bytes = {"LT": sfb.v1_prompt_path("LT").read_bytes(), "R0": (rr.CONTEXT / "prompts/docker-editor-R0-v4.txt").read_bytes()}
    # scripted plan: the second LT pass is stopped by an edit limit (then resumed); the third R0 pass deletes a page
    stop_item = next(i for i in items if i["arm"] == "LT" and i["kind"] == "pass" and i["area"] == order[1])
    fail_item = next(i for i in items if i["arm"] == "R0" and i["kind"] == "pass" and i["area"] == order[2])
    sessions = {}
    for it in items + [{"arm": stop_item["arm"], "kind": "resume", "area": stop_item["area"]}]:
        arm, kind, area = it["arm"], it["kind"], it["area"]
        paths = areas.get(area, [])
        prompt = session_prompt(base_bytes[arm], arm, kind, area, paths, trunk if arm == "LT" else None)
        edits = FIXTURE_STOP_EDITS if it is stop_item else None
        sessions[manifest_key(arm, kind, area)] = (arm, kind, {"area_id": area, "paths": paths}, prompt, edits)
    survey_prompt_file = (root / "prompts" / f"{survey_key}.txt", f"{survey_key}-v1")  # every binding names WS
    manifests.update(write_session_files(root, es, run_id=FIXTURE_RUN_ID, fixture=True, sessions=sessions,
                                         base_binding_prompts={SURVEY_ARM: survey_prompt_file}))
    baseline_tree = root / "O-r01" / "editor" / "source"
    ctx = {"root": root, "gm": gm, "vm": vm, "base": base, "schemas": schemas, "manifests": manifests,
           "areas": areas, "baseline_id": tree_id(baseline_tree), "baseline_tree": baseline_tree}
    scripted = {}

    def driver_for(arm, kind, area):
        st = ledger_state(read_ledger(root))["arms"][arm]
        start = Path(st["head_path"]) if st["head_path"] else baseline_tree
        pages, _, start_edges = rr.tree_graph(gm, start, dcv.scope_of(vm))
        paths = areas.get(area, [])
        if arm == fail_item["arm"] and kind == "pass" and area == fail_item["area"]:
            batches, removed = fixture_failure_ops(pages, area)
            msg = sfb.fenced(fixture_record(arm, kind, area, paths, [], [], removed, []))
        else:
            batches, prov, edited, created = fixture_ops(arm, kind, area, pages, survey["record"], start_edges)
            msg = sfb.fenced(fixture_record(arm, kind, area, paths, edited, created, [], prov))
        if arm == stop_item["arm"] and kind == "pass" and area == stop_item["area"]:
            msg = None  # stopped sessions end without a final message
        scripted[manifest_key(arm, kind, area)] = [[op[:2] for op in b] for b in batches]
        return sfb.RecordingScriptedDriver(es, batches, msg)

    t1 = time.time()
    run_queue(ctx, items, driver_for=driver_for, log=lambda s: print(s, flush=True))
    stage["edit"] = round(time.time() - t1, 1)
    rows = read_ledger(root)
    ends = [r for r in rows if r["event"] == "end" and r["arm"] in EDIT_ARMS]
    chain_ok = all(r["start_id"] == r["head_before"] for r in ends)
    summary_sessions = [{k: r.get(k) for k in ("namespace", "arm", "kind", "area", "status", "action", "truncated",
                                                "stopped_by", "resume_due", "not_admitted_because", "start_id",
                                                "head_after", "seconds")} for r in ends]
    # variants
    t1 = time.time()
    finals = {arm: ledger_state(rows)["arms"][arm]["head_path"] for arm in EDIT_ARMS}
    vctx = {arm: variant_context(gm, vm, finals[arm], ledger_rows=rows, arm=arm) for arm in EDIT_ARMS}
    o_ctx = variant_context(gm, vm, baseline_tree)
    controls, null_graphs = {}, {}
    for arm in EDIT_ARMS:
        buckets, unattributed = area_buckets(vctx[arm])
        nodes_by_area = area_node_sets(vctx[arm], areas)
        controls[arm] = {"buckets": {b: {k: (v if k != "added_edges" else len(v)) for k, v in x.items()
                                         if k not in ("T",)} | {"T": len(x["T"])} for b, x in buckets.items()},
                         "unattributed_edges": unattributed, "nulls": {}}
        for t in ("LN", "PC", "RN"):
            for k in range(1, NULLS_PER_TYPE + 1):
                gid = f"{arm}/{t.lower()}-{k}"
                path = root / "controls" / f"{arm}__{t.lower()}-{k}.json"
                if not path.exists():
                    try:
                        edges, rec = compose_null(t, vctx[arm], buckets, f"{FIXTURE_RUN_ID}/{arm}", k, nodes_by_area)
                    except Refused as error:
                        controls[arm]["nulls"][gid] = {"not_computable": str(error)}
                        continue
                    write_json(path, {"graph_id": gid, "receipt": rec, "nodes": vctx[arm]["nodes"], "edges": edges})
                g = rr.read_json(path)
                null_graphs[gid] = [tuple(e) for e in g["edges"]]
                controls[arm]["nulls"][gid] = {k2: g["receipt"][k2] for k2 in ("added_total", "removed_total",
                                                                               "planned_added", "planned_removed",
                                                                               "added_existing_edges")}
                if t == "PC":
                    pcs = {b: x["pc"] for b, x in g["receipt"]["buckets"].items() if x["pc"]}
                    controls[arm]["nulls"][gid]["pc_new_pages"] = sum(x["new_pages"] for x in pcs.values())
                    controls[arm]["nulls"][gid]["pc_inbound_placed"] = sum(x["placed"] for x in pcs.values())
        controls[arm]["arm_new_pages"] = len(vctx[arm]["receipt"]["added"])
        controls[arm]["arm_new_page_inbound"] = sum(1 for s, t in vctx[arm]["v_edges"]
                                                    if t in set(vctx[arm]["receipt"]["added"]))
    stage["controls"] = round(time.time() - t1, 1)
    # spectra
    t1 = time.time()
    manifest = rr.read_json(manifests[survey_key])
    anchors, pin = manifest["c_pin"]["anchors"], manifest["spectral_pin"]

    def receipt(gid, nodes, edges, mapping=None):
        path = root / "spectra" / "full" / f"{gid.replace('/', '__')}.json"
        if not path.exists():
            write_json(path, spectral_receipt(gm, nodes, edges, anchors, mapping, pin))
        return rr.read_json(path)
    o_rec = receipt("O", o_ctx["nodes"], o_ctx["v_edges"])
    ref_12b = rr.read_json(sfb.sf_run_root(sfb.RUN_ID) / "O-r01/host/receipts/spectral-profile.json")
    o_launch = rr.read_json(root / "O-r01/host/receipts/spectral-profile.json")
    o_equal = {"profile_sha256": o_rec["profile_sha256"], "equals_12b_O": o_rec["profile_sha256"] == ref_12b["profile_sha256"],
               "equals_this_run_O_launch_receipt": o_rec["profile_sha256"] == o_launch["profile_sha256"],
               "f_reference": rr.f_reference_check(o_rec), "flow_imbalance_O": o_rec["flow_imbalance"]["value"]}
    full = {}
    o9, mp = rr.load_spectral()
    for arm in EDIT_ARMS:
        v = vctx[arm]
        mapping = v["receipt"]["mapping"]
        recs = {arm: receipt(arm, v["nodes"], v["v_edges"], mapping),
                f"{arm}/O-prime": receipt(f"{arm}/O-prime", v["nodes"], v["o_mapped"], mapping)}
        for gid, edges in null_graphs.items():
            if gid.startswith(arm + "/"):
                recs[gid] = receipt(gid, v["nodes"], edges, mapping)
        full[arm] = {g: {"lambda1_q0.25": r["profile"]["retrieval_no_faq_lwcc"]["spectra"]["0.25"]["lambda1"],
                         "normalized_lambda2": r["profile"]["retrieval_no_faq_lwcc"]["normalized_undirected_lambda2"],
                         "flow_imbalance": r["flow_imbalance"]["value"]} for g, r in recs.items()}
    # per-area subgraph view and the sign-flip form of the pre-registered statistic (LN, magnetic lambda1)
    per_area = {}
    for arm in EDIT_ARMS:
        v = vctx[arm]
        buckets, _ = area_buckets(v)
        for b, x in buckets.items():
            if b not in areas:
                continue
            lns = [(g, e) for g, e in null_graphs.items() if g.startswith(f"{arm}/ln-")]
            S = sf.subgraph_nodes_v2(x["T"], v["o_mapped"], v["v_edges"], x["added_pages"], [e for _, e in lns])
            rows_sub = subgraph_rows(o9, mp, S, [("O-prime", v["o_mapped"]), ("v", v["v_edges"])] + lns, v["o_mapped"])
            per_area.setdefault(b, {})[arm] = {"S": len(S), "d": sf.delta(rows_sub["v"], rows_sub["O-prime"]),
                                               "nulls": [sf.delta(rows_sub[g], rows_sub["O-prime"]) for g, _ in lns]}
    zrows = [r for a in per_area.values() for r in a.values()]
    pooled = sf.pooled_null_sd([r["nulls"] for r in zrows]) if zrows else {}
    diffs = {}
    for a, arms in per_area.items():
        for arm, r in arms.items():
            r["z"], _ = sf.standardize(r["d"], r["nulls"], pooled)
        if all(x in arms for x in EDIT_ARMS):
            zl, zr = arms["LT"]["z"].get("magnetic_lambda1_q0.25"), arms["R0"]["z"].get("magnetic_lambda1_q0.25")
            diffs[a] = (zl - zr) if None not in (zl, zr) else None
    test = sign_flip_test(list(diffs.values()))
    stage["spectra"] = round(time.time() - t1, 1)
    # tier 2: fixed retrievers and the synthetic trace
    t1 = time.time()
    support = rr.read_json(rr.ROOT / "artifacts/orientation-spectra-2026-10-06-docs-scope/support.json")
    qtext = {json.loads(x)["question_id"]: json.loads(x)["question"] for x in (
        rr.ROOT / "artifacts/docker-cikm-2026-05-14/dataset/questions.jsonl").read_text(encoding="utf-8").splitlines() if x}
    questions = [{"question_id": q, "question": qtext[q]} for q in sorted(support)]
    pin_problems = check_retriever_pins()
    retrieval = {}
    exports = {"O": baseline_tree, "LT": Path(finals["LT"]), "R0": Path(finals["R0"])}
    contexts = {"O": o_ctx, "LT": vctx["LT"], "R0": vctx["R0"]}
    for variant, tree in exports.items():
        exp = root / "exports" / variant
        if not exp.exists():
            rr.quiet(dcv.export_agent, SimpleNamespace(manifest=V6, candidate=tree, dest=exp))
        for name in RETRIEVERS:
            dest = root / "retrievers" / variant / name
            ranked_path = dest / "ranked.json"
            if not ranked_path.exists():
                ranked, meta = run_retriever(name, exp, questions, dest)
                write_json(ranked_path, {"ranked": ranked, "meta": meta})
            ranked = rr.read_json(ranked_path)["ranked"]
            per_q = [ranked_scores(ranked.get(q["question_id"], []), support[q["question_id"]],
                                   contexts[variant]["equivalence"]) for q in questions]
            retrieval[f"{variant}/{name}"] = {"recall@5": macro(per_q, "recall@5"), "ndcg@10": macro(per_q, "ndcg@10"),
                                              "mrr": macro(per_q, "rr"), "questions": len(per_q)}
    # synthetic question: its one support page is the source of an LT trunk page, credited through provenance
    credited = sorted(b for b, ids in vctx["LT"]["equivalence"].items() if len(ids) > 1)
    synth_support = credited[:1]
    trace = fixture_trace(vctx["LT"], synth_support)
    nav = navigation_metrics(trace, synth_support, vctx["LT"])
    qid = "synthetic:" + (synth_support[0] if synth_support else "none")
    fields = annotate_trace(trace, vctx["LT"])
    stage["tier2"] = round(time.time() - t1, 1)
    # provenance and quote survival
    prov = {arm: {"declared_pages": len(vctx[arm]["declared"]),
                  "host_check": host_provenance_check(vctx[arm]["declared"], vctx[arm]["derived"]),
                  "derived_pages": sum(1 for x in vctx[arm]["derived"].values() if x["derived"]),
                  "support_units_with_extra_credit": sum(1 for b, ids in vctx[arm]["equivalence"].items()
                                                         if len(ids) > 1)} for arm in EDIT_ARMS}
    lt_hub_rows = [r for r in rows if r.get("arm") == "LT" and r.get("action") == "advance"]
    elements = []
    for r in lt_hub_rows:
        for item in r.get("provenance_declared") or []:
            src_rel = sfb.norm_path(item["sources"][0])
            src = next(p for p in base.pages if p.rel_path == src_rel)
            elements.append({"element": f"fixture:{r['namespace']}", "page_id": src.doc_id,
                             "quote": distinctive_line(src.text)})
    quotes = {arm: quote_survival(elements, vctx[arm]) for arm in EDIT_ARMS}
    out = {"schema_version": "docspectra.icsc.wc_refinement_15.v1.fixture", "date": sf.today(), "model_calls": 0,
           "run_id": FIXTURE_RUN_ID, "design_sha256": sha(DESIGN), "code_sha256": sha(Path(__file__)),
           "survey": {k: survey[k] for k in ("status", "record_status", "valid", "read_only_kept")} |
                     {"areas": survey["host_checks"]["areas"], "host_page_count": survey["host_checks"]["host_page_count"]},
           "order": order, "items": items, "scripted": scripted, "sessions": summary_sessions,
           "chain_identity_ok": chain_ok,
           "counts": {"sessions": len(ends), "advanced": sum(r["action"] == "advance" for r in ends),
                      "rolled_back": sum(r["action"] == "rollback" for r in ends),
                      "resumed": sum(r["kind"] == "resume" for r in ends),
                      "limit_stops": sum(bool(r.get("truncated")) for r in ends),
                      "reconciled": sum(r["kind"] == "reconcile" for r in ends)},
           "arm_totals": {arm: {k: ledger_state(rows)["arms"][arm][k] for k in ("tokens", "seconds", "sessions")}
                          for arm in EDIT_ARMS},
           "O_09c_equality": o_equal, "controls": controls,
           "null_count_per_type": {t: sum(1 for g in null_graphs if f"/{t.lower()}-" in g) for t in ("LN", "PC", "RN")},
           "full_view": full, "per_area_subgraph": per_area, "sign_flip_on_fixture": test,
           "retrievers": {"pins": RETRIEVERS, "pin_problems": pin_problems, "notes": RETRIEVER_NOTES,
                          "scores": retrieval},
           "synthetic_trace": {"question_id": qid, "navigation": nav, "trace_fields": fields},
           "provenance_rule": PROVENANCE_RULE, "provenance": prov, "quote_survival": quotes,
           "stage_seconds": stage, "wall_seconds": round(time.time() - t_start, 1),
           "scope": "tooling check on scripted stand-in edits; zero model calls; not evidence about any arm"}
    digest = write_json(FIXTURE_EVIDENCE, out)
    print(json.dumps({"sha256": digest, "counts": out["counts"], "chain_identity_ok": chain_ok,
                      "O_09c_equality": {k: o_equal[k] for k in ("equals_12b_O", "equals_this_run_O_launch_receipt")},
                      "null_count_per_type": out["null_count_per_type"],
                      "pc": {arm: {"arm_new_pages": controls[arm]["arm_new_pages"],
                                   "arm_new_page_inbound": controls[arm]["arm_new_page_inbound"],
                                   "pc": {g: (x.get("pc_new_pages"), x.get("pc_inbound_placed"))
                                          for g, x in controls[arm]["nulls"].items() if "/pc-" in g}}
                             for arm in EDIT_ARMS},
                      "navigation": nav, "retrieval": retrieval, "sign_flip": test, "quote_survival":
                          {a: {k: q[k] for k in ("elements", "in_credited_page", "anywhere")} for a, q in quotes.items()},
                      "stage_seconds": stage, "wall_seconds": out["wall_seconds"]}, indent=1, default=str))


# ------------------------------------------------------------------ pins (O5) and live stages

def pins(_args):
    if PINS_EVIDENCE.exists():
        raise Refused("pins-already-written")
    problems = check_retriever_pins()
    if problems:
        raise Refused("retriever-pins:" + ",".join(problems))
    digest = write_json(PINS_EVIDENCE, {
        "schema_version": "docspectra.icsc.wc_refinement_15.v1.retriever_pins", "created": DATE, "ra_commit": RA_COMMIT,
        "retrievers": RETRIEVERS, "notes": RETRIEVER_NOTES,
        "embedding_model": {"dir": str(MODEL_DIR.relative_to(rr.ROOT)).replace("\\", "/"),
                            "lock": {"path": str(MODEL_LOCK.relative_to(rr.ROOT)).replace("\\", "/"),
                                     "sha256": sha(MODEL_LOCK)},
                            "model_safetensors_sha256": sha(MODEL_DIR / "model.safetensors")},
        "scoring": {"recall": "Recall@5", "ndcg": "nDCG@10 (log2(rank+1), binary gain, each support unit once)",
                    "mrr": "reciprocal rank of the first credited item within 10", "support": "credited equivalence "
                    "sets (PROVENANCE_RULE)", "label": "automatic support labels, not human-reviewed"},
        "decided": "O5 is pinned here as the design delegates it to Sprint 15 (section 3)"})
    print(json.dumps({"sha256": digest}, indent=1))


RUN_PLAN_KEYS = ("run_id", "kind", "areas", "order", "trunk_inventory", "manifests", "schemas", "budget")


def load_run_plan(path):
    """A frozen run plan (written by the Sprint 16 or 17 freeze step, with a .sha256 lock) and its hash gate."""
    path = Path(path)
    lock = path.with_suffix(".sha256")
    if not path.exists() or not lock.exists():
        raise Refused("run-plan-not-frozen")
    if sha(path) != lock.read_text(encoding="ascii").split()[0]:
        raise Refused("run-plan-hash-changed-since-freeze")
    plan = rr.read_json(path)
    missing = [k for k in RUN_PLAN_KEYS if k not in plan]
    if missing:
        raise Refused("run-plan-missing:" + ",".join(missing))
    for key, m in plan["manifests"].items():
        if sha(m["path"]) != m["sha256"]:
            raise Refused(f"session-manifest-changed-since-freeze:{key}")
    if sha(rr.ROOT / plan["schemas"]["path"]) != plan["schemas"]["sha256"]:
        raise Refused("record-schemas-changed-since-freeze")
    return plan


def refine(args):
    """Sprint 17: the LT and R0 sessions of a frozen run plan, live, after authorization point 2."""
    plan = load_run_plan(args.plan)
    root = sfb.sf_run_root(plan["run_id"])
    authorized(root, 2)
    vm = rr.read_json(V6)
    gm = dcv.pinned(vm)
    baseline_tree = root / "O-r01" / "editor" / "source"
    if not baseline_tree.is_dir():
        raise Refused("O-control-not-launched")
    ctx = {"root": root, "gm": gm, "vm": vm, "base": sfb.Baseline(gm, vm),
           "schemas": rr.read_json(rr.ROOT / plan["schemas"]["path"]),
           "manifests": {k: Path(m["path"]) for k, m in plan["manifests"].items()}, "areas": plan["areas"],
           "baseline_id": tree_id(baseline_tree), "baseline_tree": baseline_tree, "budget": plan["budget"]}
    run_queue(ctx, planned_items(plan["order"]), live=True, log=lambda s: print(s, flush=True),
              max_sessions=None if args.all else 1)


def survey(args):
    """Sprint 16: the WS survey session of a frozen run plan, live, after authorization point 1; at most one rerun."""
    plan = load_run_plan(args.plan)
    root = sfb.sf_run_root(plan["run_id"])
    authorized(root, 1)
    vm = rr.read_json(V6)
    base = rr.baseline_pages(dcv.pinned(vm), vm)
    schemas = rr.read_json(rr.ROOT / plan["schemas"]["path"])
    manifest = Path(plan["manifests"][manifest_key(SURVEY_ARM, "survey", "ALL")]["path"])
    attempts = []
    for rep in (1, 2):
        ns = root / f"{SURVEY_ARM}-r{rep:02d}"
        if not ns.exists():
            chained_launch(manifest, SURVEY_ARM, rep, None, live=True)
        result = evaluate_survey(ns, schemas, sorted(p.rel_path for p in base))
        attempts.append(result)
        print(json.dumps({k: result[k] for k in ("namespace", "status", "record_status", "record_reason", "valid",
                                                 "editor_tokens", "editor_wall_seconds")}, indent=1), flush=True)
        if result["valid"]:
            break
    path = root / "host-private" / "survey-attempts.json"
    write_json(path, {"attempts": attempts})
    print(json.dumps({"attempts_file": str(path), "valid": any(a["valid"] for a in attempts)}))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="stage", required=True)
    sub.add_parser("pins").set_defaults(fn=pins)
    sub.add_parser("fixture").set_defaults(fn=fixture)
    s = sub.add_parser("survey")
    s.add_argument("--plan", required=True)
    s.set_defaults(fn=survey)
    r = sub.add_parser("refine")
    r.add_argument("--plan", required=True)
    r.add_argument("--all", action="store_true", help="run the whole remaining queue")
    r.set_defaults(fn=refine)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

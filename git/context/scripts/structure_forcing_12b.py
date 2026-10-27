"""Sprint 12b: structure-forcing confirmatory rerun (run sf-12-v2, plan v3).

Run profile sf-12-v2 of the Sprint 12 study. structure_forcing_12 (profile sf-12-v1, plan v2) is imported as a
library and never edited; its v1 paths stay exactly as they were. Inside a 12b process, activate() rebinds a declared
list of sf names (PROFILE_BINDINGS) to this run, so sf's run-parameterized workers (controls, spectra, analysis,
answers, scorer, rubric) serve sf-12-v2. restructuring_runs and sandbox/editor_session are imported, never edited.

Plan v3 changes (decided by the author on 2026-10-09, before any 12b model call; see AMENDMENTS_V3):
  1 limits: 60 minutes and 30M tokens only; no path limit (manifest edits = null)
  2 truncated sessions pass through: the residual tree at a limit stop is the terminal tree, admitted by the same checks
  3 protected lines: set-based (every distinct stripped baseline protected line still present somewhere)
  4 unresolved links: keyed on the target resolved against its page, not on (page, target)
  5 MT wording: "on every page in your area" (settled by 6)
  6 area scoping: one arm-neutral area-selection session proposes 4 areas, frozen before the arms; realization r of
    every arm works in area r; every session ends with one schema-validated JSON record in its final message

Stages (each refuses to run twice; long stages resume per item):
  prepare            no model call: selection prompt, record schemas, base templates binding, base run manifest and
                     the pre-registration of plan v3's rules (frozen before the selection session)
  fixture            zero model calls: scripted selection and stand-ins under the v3 rules, one stopped by a
                     fixture-only edit limit (residual terminal), controls, spectra, analysis (run sf-12-fixture-v3)
  select             live, after the author's yes: the area-selection session; at most one rerun if invalid
  freeze             after a valid selection: 12 arm prompts, templates v5 and its 4 area bindings, 4 area run
                     manifests and plan v3; every later stage refuses on a changed plan, manifest or binding
  edit               --next [--all]: O and S, the seeded order (realization r in area r), infrastructure reruns,
                     then at most one replacement per arm in the area of its first non-admitted realization
  admit              admission v2, edit magnitude, records, per-area table, blind export, sham identity
  generate-controls / spectra / analyse-spectra     the frozen v2 analysis on the admitted endpoints
  answer / archive-infra / score / rubric / analyse-answers                  as Sprint 12, on this run's folders
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import posixpath
import random
import re
import secrets
import shutil
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import structure_forcing_12 as sf  # noqa: E402

rr, dcv = sf.rr, sf.dcv
Refused = sf.Refused
sha, sha_text, write_json, write_or_same = sf.sha, sf.sha_text, sf.write_json, sf.write_or_same

DATE = "2026-10-09"
RUN_ID = "sf-12-v2"
FIXTURE_RUN_ID = "sf-12-fixture-v3"
STRUCTURE_ARMS = sf.STRUCTURE_ARMS
SELECTION_ARM = "AS"
AREAS = ("A1", "A2", "A3", "A4")
REALIZATIONS, REPLACEMENTS_PER_ARM = sf.REALIZATIONS, sf.REPLACEMENTS_PER_ARM
SELECTION_ATTEMPTS = 2           # the selection session, plus one recorded rerun if its record is invalid
INFRA_RERUNS_MAX = 2             # per realization; beyond that the driver stops and asks the author
CONTEXT, EVIDENCE = sf.CONTEXT, sf.EVIDENCE
PROMPTS = CONTEXT / "prompts"
V6 = sf.V6
TEMPLATES_V4 = sf.TEMPLATES_V4
PLAN_V2 = sf.PLAN
SELECTION_PROMPT = PROMPTS / "docker-area-selection-v1.txt"
SCHEMAS = EVIDENCE / f"structure-forcing-12b-record-schemas-v1-{DATE}.json"
PREREG = EVIDENCE / f"structure-forcing-12b-preregistration-v1-{DATE}.json"
TEMPLATES_V5 = EVIDENCE / f"restructuring-arm-templates-v5-structure-forcing-12b-{DATE}.json"
SELECTION_EVIDENCE = EVIDENCE / f"structure-forcing-12b-area-selection-v1-{DATE}.json"
PLAN = EVIDENCE / f"structure-forcing-12-plan-v3-{DATE}.json"
FIXTURE_EVIDENCE = EVIDENCE / f"structure-forcing-12b-fixture-v3-{DATE}.json"
ADMISSION_GLOB = "structure-forcing-12b-admission-v1-*.json"
SPECTRA_GLOB = "structure-forcing-12b-spectra-v1-*.json"
ANSWERS_GLOB = "structure-forcing-12b-answers-v1-*.json"
ANSWERS = rr.ROOT / "artifacts/sf-12-v2-answers"
SCORER = rr.ROOT / "artifacts/sf-12-v2-scorer"
JUDGE = rr.ROOT / "artifacts/sf-12-v2-judge"
REVIEW_LABEL = "author-chat-2026-10-09-sprint-12b-usd60"
LIMITS = {"model_calls": 1, "tool_calls": None, "tokens": 30_000_000, "wall_seconds": 3600, "edits": None}
FIXTURE_STOP_EDITS = 4           # fixture-only path limit that stops the truncated stand-in

# ------------------------------------------------------------------ prompt texts (frozen in templates v5)

V1_LIMITS = ("You have one session of at most 60 minutes and may change at most 150 page paths; the host enforces "
             "these limits and stops the session if they are exceeded.")
V2_LIMITS = "You have one session of at most 60 minutes; the host enforces this limit and stops the session if it is exceeded."
MT_V1 = "a table of context links on every page,"
MT_V2 = "a table of context links on every page in your area,"
AREA_PARAGRAPH = ("Your area is: {paths}. Edit only pages in this area, plus any new pages your target structure needs. "
                  "You may link to pages outside the area. You are done when every page in your area meets the target "
                  "structure; then stop and write your summary. Check elapsed time with the shell; stop editing by 50 "
                  "minutes.")
RECORD_INTRO = ("End your final message with exactly one fenced JSON block (opened with ```json and closed with ```), "
                "filled in from this template; give paths relative to the working directory:")
ARM_RECORD_NOTE = ('Set "done" to true only if every page in your area meets the target structure; otherwise set it to '
                   'false and give "not_done_reason". List each edit in "edits" with its pages, its reason, and the '
                   'pages any passage was moved from.')
SELECTION_RECORD_NOTE = ('Give one object per area, with "area_id" A1, A2, A3 and A4. Each entry in "paths" is a '
                         'directory prefix or a page path; "page_count" is the number of Markdown pages in the area.')
SELECTION_TASK = ("Survey the documentation and choose four distinct areas, each a group of related pages where a "
                  "refactor of its structure may have the most impact on how readers navigate to, and rely on, the "
                  "information needed to answer real user questions. Choose areas that do not overlap, each small "
                  "enough that every page in it can be brought to a target structure within one 60-minute session. Do "
                  "not edit any file.")


def arm_record_template(arm, area_id, paths):
    return ("{\n"
            '  "schema": "edit_session_record.v1",\n'
            f'  "arm": "{arm}",\n'
            f'  "area_id": "{area_id}",\n'
            f'  "area_paths": {json.dumps(list(paths))},\n'
            '  "pages_in_area": <integer>,\n'
            '  "pages_edited": ["<path>"],\n'
            '  "pages_created": ["<path>"],\n'
            '  "pages_removed": ["<path>"],\n'
            '  "done": <true or false>,\n'
            '  "not_done_reason": <null or "<reason>">,\n'
            '  "edits": [{"pages": ["<path>"], "reason": "<reason>", "moved_from": ["<path>"]}],\n'
            '  "preservation_concerns": ["<concern>"],\n'
            '  "elapsed_minutes_at_stop": <number>\n'
            "}")


SELECTION_TEMPLATE = ("{\n"
                      '  "schema": "area_selection.v1",\n'
                      '  "areas": [{"area_id": "A1", "paths": ["<directory prefix or page path>"], "page_count": '
                      '<integer>, "rationale": "<text>", "navigation_problem": "<text>", "fits_one_session": <true or '
                      'false>}],\n'
                      '  "excluded_candidates": [{"paths": ["<path>"], "reason": "<text>"}],\n'
                      '  "method": "<text>"\n'
                      "}")


def record_paragraph(template, note):
    return f"{RECORD_INTRO}\n\n```json\n{template}\n```\n\n{note}"


def selection_prompt() -> bytes:
    text = "\n\n".join([
        "Role: documentation survey session, area selection, prompt version 1.",
        "You work in a shell inside a container whose working directory is a frozen documentation tree of Markdown "
        "pages. " + SELECTION_TASK,
        "Do not consult the web, model memory as a factual source, or any evaluation materials. Evaluation questions, "
        "answers, labels, prior model outputs, grades, and task-effect feedback are outside this session. " + V2_LIMITS +
        " No one will answer questions during the session; work from the tree alone and finish with your summary.",
        record_paragraph(SELECTION_TEMPLATE, SELECTION_RECORD_NOTE)])
    return (text + "\n").encode("utf-8")


def v1_prompt_path(arm):
    return PROMPTS / f"docker-editor-{arm}-v1.txt"


def v2_prompt_path(arm, area_id, root=PROMPTS):
    return Path(root) / f"docker-editor-{arm}-v2-{area_id}.txt"


def arm_prompt_v2(v1_bytes: bytes, arm: str, area_id: str, paths) -> bytes:
    """The arm's v1 prompt with the shared limits sentence, (MT) the area wording, the area paragraph after the target
    paragraph, and the record template at the end. Nothing else changes."""
    text = v1_bytes.decode("utf-8")
    if text.count(V1_LIMITS) != 1 or not text.endswith("\n") or text.count("\n\n") != 3:
        raise Refused(f"{arm}-v1-anchor-text-missing")
    text = text.replace(V1_LIMITS, V2_LIMITS)
    if arm == "MT":
        if text.count(MT_V1) != 1:
            raise Refused("mt-v1-target-wording-missing")
        text = text.replace(MT_V1, MT_V2)
    paragraphs = text.rstrip("\n").split("\n\n")
    if sf.TARGET_SENTENCE.split("{")[0] not in paragraphs[2]:
        raise Refused(f"{arm}-v1-target-paragraph-moved")
    area = AREA_PARAGRAPH.format(paths=", ".join(paths))
    paragraphs = paragraphs[:3] + [area] + paragraphs[3:] + [
        record_paragraph(arm_record_template(arm, area_id, paths), ARM_RECORD_NOTE)]
    return ("\n\n".join(paragraphs) + "\n").encode("utf-8")


# ------------------------------------------------------------------ record schemas (frozen before the selection)

def record_schemas():
    path_list = {"type": "array", "items": {"type": "string", "minLength": 1}}
    selection = {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "area_selection.v1",
        "type": "object", "additionalProperties": False,
        "required": ["schema", "areas", "excluded_candidates", "method"],
        "properties": {
            "schema": {"const": "area_selection.v1"},
            "areas": {"type": "array", "minItems": 4, "maxItems": 4, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["area_id", "paths", "page_count", "rationale", "navigation_problem", "fits_one_session"],
                "properties": {"area_id": {"enum": list(AREAS)}, "paths": dict(path_list, minItems=1),
                               "page_count": {"type": "integer", "minimum": 1}, "rationale": {"type": "string"},
                               "navigation_problem": {"type": "string"}, "fits_one_session": {"type": "boolean"}}}},
            "excluded_candidates": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["paths", "reason"],
                "properties": {"paths": path_list, "reason": {"type": "string"}}}},
            "method": {"type": "string"}}}
    session = {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "edit_session_record.v1",
        "type": "object", "additionalProperties": False,
        "required": ["schema", "arm", "area_id", "area_paths", "pages_in_area", "pages_edited", "pages_created",
                     "pages_removed", "done", "not_done_reason", "edits", "preservation_concerns",
                     "elapsed_minutes_at_stop"],
        "properties": {
            "schema": {"const": "edit_session_record.v1"}, "arm": {"enum": list(STRUCTURE_ARMS)},
            "area_id": {"enum": list(AREAS)}, "area_paths": path_list,
            "pages_in_area": {"type": "integer", "minimum": 0}, "pages_edited": path_list,
            "pages_created": path_list, "pages_removed": path_list, "done": {"type": "boolean"},
            "not_done_reason": {"type": ["string", "null"]},
            "edits": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["pages", "reason", "moved_from"],
                "properties": {"pages": path_list, "reason": {"type": "string"}, "moved_from": path_list}}},
            "preservation_concerns": {"type": "array", "items": {"type": "string"}},
            "elapsed_minutes_at_stop": {"type": "number", "minimum": 0}}}
    return {"schema_version": "docspectra.icsc.structure_forcing_12b.v1.record_schemas", "created": DATE,
            "schemas": {"area_selection.v1": selection, "edit_session_record.v1": session},
            "extraction": "the text of the last item.completed agent_message in the session's Codex transcript must "
                          "hold exactly one ```json fenced block; it is parsed as JSON and validated (Draft 2020-12) "
                          "against the schema its prompt names",
            "outcomes": {"valid": "parsed and schema-valid",
                         "record_invalid": "no final message, no block, more than one block, unparseable JSON, or "
                                           "schema errors (reason recorded); an outcome, never repaired, never "
                                           "affects admission"},
            "host_checks": {"selection": "4 distinct area ids A1-A4; every area holds at least one baseline page; no "
                                         "baseline page is in two areas; page_count compared with the host count "
                                         "(reported). Selection valid = record valid and these host checks pass",
                            "session": "pages_edited / pages_created / pages_removed against the terminal tree's "
                                       "review (changed / added / removed): undeclared and over-declared paths; "
                                       "changed or removed baseline pages outside the area (reported, not rejected)"}}


def final_message(transcript: Path):
    last = None
    if not Path(transcript).exists():
        return None
    for line in Path(transcript).read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if "codex_event" not in row:
            continue
        try:
            event = json.loads(row["codex_event"])
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            last = item.get("text")
    return last


FENCE = re.compile(r"```json[ \t]*\r?\n(.*?)\r?\n[ \t]*```", re.S)


def extract_record(message, schema_name, schemas):
    """(status, record|None, reason). status: valid | record_invalid."""
    import jsonschema
    if message is None:
        return "record_invalid", None, "no-final-message"
    blocks = FENCE.findall(message)
    if len(blocks) != 1:
        return "record_invalid", None, f"fenced-json-blocks:{len(blocks)}"
    try:
        record = json.loads(blocks[0])
    except ValueError as error:
        return "record_invalid", None, f"malformed-json:{str(error)[:120]}"
    validator = jsonschema.Draft202012Validator(schemas["schemas"][schema_name])
    errors = sorted(validator.iter_errors(record), key=lambda e: list(e.path))
    if errors:
        return "record_invalid", record, "schema:" + "; ".join(
            f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message[:120]}" for e in errors[:5])
    return "valid", record, None


def norm_path(p: str) -> str:
    p = p.strip().replace("\\", "/")
    for prefix in ("/work/", "work/"):
        if p.startswith(prefix):
            p = p[len(prefix):]
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def in_area(rel: str, paths) -> bool:
    for p in paths:
        p = norm_path(p)
        if p and (rel == p or rel.startswith(p.rstrip("/") + "/")):
            return True
    return False


def selection_host_checks(record, baseline_rels):
    areas = record["areas"]
    ids = [a["area_id"] for a in areas]
    pages = {a["area_id"]: sorted(r for r in baseline_rels if in_area(r, a["paths"])) for a in areas}
    owner = defaultdict(list)
    for aid, rels in pages.items():
        for r in rels:
            owner[r].append(aid)
    overlap = sorted(r for r, owners in owner.items() if len(owners) > 1)
    problems = ([] if sorted(ids) == list(AREAS) else ["area-ids-not-A1-A4-distinct"]) + \
        [f"empty-area:{aid}" for aid, rels in pages.items() if not rels] + \
        ([f"overlapping-pages:{len(overlap)}"] if overlap else [])
    return {"passes": not problems, "problems": problems,
            "host_page_count": {aid: len(rels) for aid, rels in pages.items()},
            "record_page_count": {a["area_id"]: a["page_count"] for a in areas},
            "overlap_examples": overlap[:10],
            "paths": {a["area_id"]: [norm_path(p) for p in a["paths"]] for a in areas}}


def session_record_check(record, review, area_paths):
    """Declared versus actual paths, and edits outside the area (reported, never rejected)."""
    actual = {"pages_edited": set(review["changed"]), "pages_created": set(review["added"]),
              "pages_removed": set(review["removed"])}
    out = {"outside_area": {"changed": sorted(r for r in review["changed"] if not in_area(r, area_paths)),
                            "removed": sorted(r for r in review["removed"] if not in_area(r, area_paths)),
                            "added_outside_area_paths": sorted(r for r in review["added"]
                                                               if not in_area(r, area_paths))}}
    if record is None:
        return dict(out, declared=None)
    for key, real in actual.items():
        declared = {norm_path(p) for p in record.get(key) or []}
        out[key] = {"actual": len(real), "declared": len(declared), "undeclared": sorted(real - declared)[:50],
                    "undeclared_count": len(real - declared), "declared_not_actual": sorted(declared - real)[:50],
                    "declared_not_actual_count": len(declared - real)}
    out["declared"] = True
    return out


# ------------------------------------------------------------------ admission v2 rules (pure; tested offline)

def protected_tokens(texts):
    return {t.strip() for t in dcv.PROTECTED.findall("\n".join(texts))}


def protected_missing_v3(base_texts, variant_texts):
    """Plan v3: distinct whitespace-stripped baseline protected lines absent from the whole variant."""
    return sorted(protected_tokens(base_texts) - protected_tokens(variant_texts))


def link_helpers():
    from docspectra_importer.retrieval_arena_export import _candidate_targets, _route_key  # on sys.path after pinning
    return _candidate_targets, _route_key


def unresolved_links(pages):
    """The exact dcv.review rule (unresolved internal links) over page objects with rel_path and text."""
    candidate_targets, route_key = link_helpers()
    rels = {p.rel_path for p in pages}
    routes = {route_key(x) for x in rels}
    return [{"page": p.rel_path, "target": t} for p in pages for t in dcv.LINK.findall(p.text)
            if not t.startswith(("http:", "https:", "mailto:", "#"))
            and not any(x in rels for x in candidate_targets(t, p.rel_path)) and route_key(t) not in routes]


def link_target_key(page: str, target: str, route_key=None) -> str:
    """Plan v3: the target resolved against its page (relative paths joined to the page's folder, root-relative paths
    as written), normalized by the importer's route key, with the #fragment kept."""
    if route_key is None:
        route_key = link_helpers()[1]
    u = urlparse(target)
    if u.scheme or u.netloc or not u.path:
        return target
    path = u.path.lstrip("/") if u.path.startswith("/") else posixpath.normpath(
        posixpath.join(posixpath.dirname(page), u.path))
    return route_key(path) + ("#" + u.fragment if u.fragment else "")


def introduced_unresolved_v3(variant_unresolved, baseline_unresolved, route_key=None):
    keys = {link_target_key(x["page"], x["target"], route_key) for x in baseline_unresolved}
    return [x for x in variant_unresolved if link_target_key(x["page"], x["target"], route_key) not in keys]


ADMISSION_RULE_V2 = {
    "version": "sf12_admission_v2",
    "rule": "a terminal tree exists (status completed, or budget_stop with the residual tree as terminal; not no_op, "
            "failed, c_rejected or preservation_flagged; a residual tree with no changed path is a no_op; no non-page, excluded-class or tree-violation flag); C v1 "
            "passed; no unmapped removal; preservation review: (a) every distinct whitespace-stripped protected line "
            "of the baseline is still present somewhere in the variant, and (b) 0 unresolved internal links whose "
            "target, resolved against its page (route key, #fragment kept), did not occur unresolved anywhere in the "
            "baseline; partial_unit_loss is reported, not gating. The v1 multiset count and the v1 (page, target) "
            "count are reported beside each",
    "truncated": "a session stopped by a limit (wall_seconds or tokens) is admitted or rejected by exactly these "
                 "checks on its residual tree; no minimum size; an admitted truncated session counts as admitted",
    "replacement": "one extra session per arm (replicate 5) if any of its 4 realizations is not admitted, run in the "
                   "area of the arm's first non-admitted realization in the seeded launch order; the outcome is "
                   "recorded. An infrastructure stop (usage or rate limit) is excluded and rerun as a fresh session "
                   "in the same area (replicate 6 upward, at most 2 per realization); it is not the replacement",
    "record": "the final-message JSON record is validated and checked against the tree diff; record_invalid, "
              "undeclared edits and edits outside the area are reported outcomes and never affect admission",
}

RESIDUAL_RULE = ("a session stopped by a limit (wall_seconds or tokens; not an infrastructure stop) takes the "
                 "residual checkpoint the runtime stores at the stop as its terminal tree; the 12b driver runs the "
                 "launch steps (tree_graph, derive_mapping, c_admission, spectral_profile, dcv.review, "
                 "support_mapping) on it into host/residual-terminal/ and never rewrites run-state.json")


# ------------------------------------------------------------------ residual terminal and namespace views

def residual_dir(ns):
    return Path(ns) / "host" / "residual-terminal"


def terminal_source(state):
    """Which tree is the session's terminal under plan v3: 'run_state', 'residual' or None (pure)."""
    infra = any(str(f).startswith("infrastructure_stop:") for f in state.get("flags", []))
    if infra:
        return None
    if state.get("status") == "budget_stop":
        return "residual" if state.get("budget_stop_residual") else None
    if state.get("terminal_tree"):
        return "run_state"
    return None


TREE_FLAGS = (":non_page_file:", ":excluded_class_path:", ":tree_violation:")


def residual_terminal(ns: Path, *, gm, vm):
    """Run launch's terminal steps on a limit stop's residual checkpoint; idempotent (reads the record if written)."""
    ns = Path(ns)
    out = residual_dir(ns)
    record_path = out / "record.json"
    if record_path.exists():
        return rr.read_json(record_path)
    state = rr.read_json(ns / "host/run-state.json")
    if terminal_source(state) != "residual":
        return None
    manifest = manifest_for_state(state)
    tree = ns / "host" / "checkpoints" / state["budget_stop_residual"] / "source"
    rec = {"schema_version": "docspectra.icsc.structure_forcing_12b.v1.residual_terminal", "rule": RESIDUAL_RULE,
           "status": state["status"], "stopped_by": state.get("editor_stop"), "residual_checkpoint":
           state["budget_stop_residual"], "terminal_tree": str(tree), "flags": [], "receipts": {}}
    if any(any(k in f for k in TREE_FLAGS) for f in state.get("flags", [])):
        rec["unprocessable"] = "tree-flagged-by-runtime"
        write_json(record_path, rec)
        return rec
    try:
        base_list = rr.baseline_pages(gm, vm)
        var_list, nodes, edges = rr.tree_graph(gm, tree, dcv.scope_of(vm))
    except (rr.Refused, ValueError) as error:
        rec["unprocessable"] = f"tree_graph:{error}"
        write_json(record_path, rec)
        return rec
    mapping = rr.derive_mapping(base_list, var_list)
    rec["receipts"]["mapping"] = write_json(out / "receipts/mapping.json", mapping)
    if mapping["unmapped_removals"]:
        rec["flags"].append("preservation_flagged:unmapped_removal")
    if mapping["partial_unit_loss"]:
        rec["flags"].append("preservation_review:partial_unit_loss")
    c = rr.c_admission(manifest, gm, base_list, nodes, edges, mapping["mapping"])
    rec["receipts"]["c_admission"] = write_json(out / "receipts/c-admission.json", c)
    rec["c_passed"] = c["result"]["passed"]
    spectral = rr.spectral_profile(nodes, edges, manifest["c_pin"]["anchors"], mapping["mapping"],
                                   manifest["spectral_pin"])
    rec["receipts"]["spectral_profile"] = write_json(out / "receipts/spectral-profile.json", spectral)
    rec["profile_sha256"] = spectral["profile_sha256"]
    rr.quiet(dcv.review, SimpleNamespace(manifest=Path(manifest["variant_manifest"]["path"]), candidate=tree,
                                         output=out / "review.json"))
    rec["receipts"]["preservation_review"] = dcv.file_hash(out / "review.json")
    rec["receipts"]["support_mapping"] = write_json(out / "support-mapping.json",
                                                    rr.support_mapping(mapping, var_list, base_list))
    rec["eligible_for_grading"] = bool(manifest["stopping"]["budget_stop_terminal_eligible_for_grading"] is True)
    write_json(record_path, rec)
    return rec


def manifest_for_state(state):
    """The run manifest a namespace was launched under, found by its recorded sha256 among the run's manifests."""
    root = run_root(state["run_id"])
    for path in sorted((root / "manifests").glob("*.json")):
        if sha(path) == state["manifest_sha256"]:
            return rr.read_json(path)
    raise Refused("launch-manifest-not-found:" + state["run_id"])


def manifest_file_for_state(state):
    root = run_root(state["run_id"])
    for path in sorted((root / "manifests").glob("*.json")):
        if sha(path) == state["manifest_sha256"]:
            return path
    raise Refused("launch-manifest-not-found:" + state["run_id"])


def terminal_view(ns):
    """(terminal tree, review path, mapping path, source, residual record) for a namespace under plan v3."""
    ns = Path(ns)
    state = rr.read_json(ns / "host/run-state.json")
    src = terminal_source(state)
    if src == "run_state":
        return (Path(state["terminal_tree"]), ns / "exports/preservation-review/review.json",
                ns / "host/receipts/mapping.json", src, None)
    if src == "residual":
        rec = rr.read_json(residual_dir(ns) / "record.json") if (residual_dir(ns) / "record.json").exists() else None
        if rec and not rec.get("unprocessable"):
            return (Path(rec["terminal_tree"]), residual_dir(ns) / "review.json",
                    residual_dir(ns) / "receipts/mapping.json", src, rec)
        return None, None, None, src, rec
    return None, None, None, None, None


def namespace_graphs(gm, vm, ns):
    """sf.namespace_graphs under plan v3: the terminal is the run-state tree, or the residual tree at a limit stop."""
    tree, _, mapping_path, _, _ = terminal_view(ns)
    if tree is None:
        raise Refused(f"no-terminal-tree:{Path(ns).name}")
    base = rr.baseline_pages(gm, vm)
    o_edges = sf.canonical(gm.extract_markdown_edges(base))
    _, nodes, v_edges = rr.tree_graph(gm, tree, dcv.scope_of(vm))
    receipt = rr.read_json(mapping_path)
    om = sf.mapped_edges(o_edges, receipt["mapping"], nodes)
    return om, nodes, sf.canonical(v_edges), receipt, sf.touched_set(receipt)


class Baseline:
    """Cached baseline pages, their protected tokens and unresolved links for admission v2."""

    def __init__(self, gm, vm):
        self.gm, self.vm = gm, vm
        self.pages = rr.baseline_pages(gm, vm)
        self.rels = sorted(p.rel_path for p in self.pages)
        self.texts = [p.text for p in self.pages]
        self.unresolved = unresolved_links(self.pages)


def admission_record(ns, base: Baseline, *, schemas=None, area_paths=None):
    """Admission v2 for one structure-arm namespace (plus the session's size, stop and record)."""
    ns = Path(ns)
    state = rr.read_json(ns / "host/run-state.json")
    rec = {k: state.get(k) for k in ("status", "flags", "terminal_checkpoint", "budget_stop_residual", "c_passed",
                                     "profile_sha256", "editor_model_calls", "editor_tool_calls", "editor_tokens",
                                     "editor_edits", "editor_wall_seconds", "editor_stop", "editor_errors",
                                     "editor_exit_code")}
    rec["infrastructure_stop"] = any(str(f).startswith("infrastructure_stop:") for f in state.get("flags", []))
    rec["truncated"] = state.get("status") == "budget_stop" and not rec["infrastructure_stop"]
    rec["stopped_by"] = state.get("editor_stop") if rec["truncated"] else None
    message = final_message(ns / "host/transcript.jsonl")
    rec["final_message"] = message is not None
    tree, review_path, _, src, residual = terminal_view(ns)
    rec["terminal_source"] = src
    if residual is not None:
        rec["residual"] = {k: residual.get(k) for k in ("c_passed", "flags", "unprocessable", "profile_sha256",
                                                        "eligible_for_grading")}
        rec["c_passed"] = residual.get("c_passed")
    reasons = []
    if tree is None or review_path is None or not Path(review_path).exists():
        reasons.append("no-terminal-tree" if src != "residual" else
                       "residual-" + str((residual or {}).get("unprocessable") or "not-processed"))
    else:
        review = rr.read_json(review_path)
        var_pages, _, _ = rr.tree_graph(base.gm, tree, dcv.scope_of(base.vm))
        missing = protected_missing_v3(base.texts, [p.text for p in var_pages])
        _, route_key = link_helpers()
        introduced = introduced_unresolved_v3(review["unresolved_internal_links"], base.unresolved, route_key)
        rec["review"] = {"added": len(review["added"]), "removed": len(review["removed"]),
                         "changed": len(review["changed"]),
                         "edges_added_raw": len(review["edges_added"]), "edges_removed_raw": len(review["edges_removed"]),
                         "protected_lines_missing_v3": len(missing), "protected_missing_examples": missing[:5],
                         "protected_lines_removed_multiset_v1": sf.pl2_normalized(review),
                         "introduced_unresolved_v3": len(introduced), "introduced_examples": introduced[:5],
                         "introduced_unresolved_page_target_v1": len(review["introduced_unresolved_internal_links"])}
        if review["baseline_unresolved_internal_link_count"] != len(base.unresolved):
            raise Refused("baseline-unresolved-count-differs-from-review")
        flags = list(state.get("flags", [])) + list((residual or {}).get("flags", []))
        status_ok = state["status"] == "completed" or (src == "residual" and state["status"] == "budget_stop")
        if not status_ok:
            reasons.append(f"status:{state['status']}")
        elif not (review["added"] or review["removed"] or review["changed"]):
            reasons.append("no-op")
        if any(any(k in f for k in TREE_FLAGS) for f in flags):
            reasons.append("tree-flag")
        if "preservation_flagged:unmapped_removal" in flags:
            reasons.append("unmapped-removal")
        if rec.get("c_passed") is not True:
            reasons.append("C-failed")
        if missing:
            reasons.append(f"protected-lines-missing:{len(missing)}")
        if introduced:
            reasons.append(f"introduced-unresolved:{len(introduced)}")
        if schemas is not None:
            status, record, why = extract_record(message, "edit_session_record.v1", schemas)
            rec["record"] = {"status": status, "reason": why,
                             "done": (record or {}).get("done") if status == "valid" else None,
                             "check": session_record_check(record if status == "valid" else None, review,
                                                           area_paths or [])}
    rec["not_admitted_because"] = reasons
    rec["admitted"] = not reasons
    return rec


# ------------------------------------------------------------------ manifests, bindings, plan

def run_root(run_id=RUN_ID):
    return sf.run_root(run_id)


def manifest_file(run_id=RUN_ID, area=None):
    root = run_root(run_id) / "manifests"
    return root / ("run-manifest.json" if area is None else f"run-manifest-{area}.json")


def binding_path(key, root=EVIDENCE):
    return Path(root) / f"restructuring-arm-templates-v5-12b-binding-{key}-{DATE}.json"


def rel_ctx(path: Path) -> str:
    return str(Path(path).resolve().relative_to(rr.ROOT)).replace("\\", "/")


def templates_binding(prompts: dict, note: str):
    """A launchable templates file: v4's arms (R0, L, O, S, and LT/MT/MR as given in `prompts`) plus AS."""
    v4 = rr.read_json(TEMPLATES_V4)
    arms = copy.deepcopy(v4["arms"])
    for arm, (path, version) in prompts.items():
        entry = arms.get(arm, {"editor": "survey" if arm == SELECTION_ARM else "structure",
                               "scope": "restructure"})
        entry = dict(entry, prompt={"path": rel_ctx(path), "sha256": sha(path), "version": version})
        arms[arm] = entry
    return {"schema_version": "docspectra.icsc.restructuring_run.v1.arm_templates", "created": DATE, "sprint": "12b",
            "arms": arms, "shared": dict(v4["shared"], binding=note,
                                         master=rel_ctx(TEMPLATES_V5) if TEMPLATES_V5.exists() else
                                         "templates v5 (written at the freeze)")}


def base_binding(root=EVIDENCE):
    return templates_binding({SELECTION_ARM: (SELECTION_PROMPT, "AS-v1")},
                             "base binding: AS is the area-selection prompt; LT/MT/MR keep the v4 (v1) prompts and "
                             "are never launched under this binding (the 12b edit stage launches them only under "
                             "their area binding)")


def runtime_block(es):
    return sf.runtime_block(es)


def live_manifest(es, run_id, binding, area):
    data = rr.build_manifest(
        run_id=run_id, variant_manifest_path=V6, mode="live", created=DATE, templates_path=binding,
        runtime=runtime_block(es), model_identity="gpt-6.1-sol (Codex CLI 0.161.0, reasoning effort medium)",
        decided_limits=dict(LIMITS),
        stopping={"budget_stop_terminal_eligible_for_grading": True, "residual_terminal_rule": RESIDUAL_RULE,
                  "author_decisions": "git/context/reviews/2026-10-08-author-decisions-editor-runtime.md; plan v3 "
                                      "(author, 2026-10-09): limits 60 minutes and 30M tokens only, no path limit, "
                                      "truncated sessions pass through"})
    data["arm_plan"] = {"O": {"replicates": 1}, "S": {"replicates": 1},
                        SELECTION_ARM: {"replicates": 1, "reruns_max": 1, "note": "area selection, read-only by "
                                                                                   "instruction; base manifest only"},
                        **{arm: {"replicates": REALIZATIONS, "replacements_max": REPLACEMENTS_PER_ARM}
                           for arm in STRUCTURE_ARMS},
                        "R0": {"replicates": 0, "note": "not run in Sprint 12b"},
                        "L": {"replicates": 0, "optional": True, "note": "not run in Sprint 12b"}}
    data["allowed"]["scope"].update({arm: "restructure" for arm in STRUCTURE_ARMS + (SELECTION_ARM,)})
    data["area"] = area
    data["admission"] = ADMISSION_RULE_V2
    data["cost_admission"] = {"authorization": "explicit author yes in chat before any live call (step 5), recorded "
                                               "in host-private/authorization.json",
                              "usd_cap": sf.HARD_STOP_USD, "editor_billing": "ChatGPT subscription usage (no USD)"}
    data["evaluation"] = {"question_set": {"path": str(sf.QUESTIONS_11B), "sha256": sha(sf.QUESTIONS_11B),
                                           "role": "frozen Sprint 12 question set (author choice (i)), disclosed"},
                          "answer_conditions": sf.answering_block()["conditions"],
                          "grading": "blind; opaque variant ids; key outside packet"}
    unresolved = rr.unresolved_paths(data)
    if unresolved:
        raise Refused("manifest-unresolved:" + ",".join(unresolved))
    return data


def fixture_manifest(es, run_id, binding, area, edits=None):
    limits = dict(rr.FIXTURE_LIMITS) if edits is None else dict(rr.FIXTURE_LIMITS, edits=edits)
    data = rr.build_manifest(run_id=run_id, variant_manifest_path=V6, mode="fixture", fixture_limits=limits,
                             created=DATE, templates_path=binding,
                             runtime=es.runtime_block(model=None, reasoning_effort=None, driver="scripted-shell-v1"))
    data["allowed"]["scope"].update({arm: "restructure" for arm in STRUCTURE_ARMS + (SELECTION_ARM,)})
    data["stopping"]["budget_stop_terminal_eligible_for_grading"] = True
    data["stopping"]["residual_terminal_rule"] = RESIDUAL_RULE
    data["area"] = area
    return data


AMENDMENTS_V3 = [
    {"change": 1, "field": "limits (run manifest) and the shared prompt sentence",
     "v2": "tokens 3,000,000; wall_seconds 3600; edits 150 page paths. Prompt: '" + V1_LIMITS + "'",
     "v3": "tokens 30,000,000; wall_seconds 3600; edits null (no limit; the per-session path count is still "
           "reported). Prompt: '" + V2_LIMITS + "'",
     "reason": "Sprint 12: all 5 MT sessions and 2 MR sessions were labelled budget_stop on the 3M cap (4.5-6.9M "
               "tokens); no session approached 60 minutes or 150 paths. Author (2026-10-09): time bounds the run, "
               "tokens bound the budget, and ending on its own is the definition-of-done signal; the nulls match each "
               "session's own edit size, so no path limit is needed",
     "decided_by": "author, 2026-10-09; not to be reopened"},
    {"change": 2, "field": "stopping / admission (truncated sessions)",
     "v2": "a session stopped by a limit has no terminal tree (budget_stop_terminal_eligible_for_grading: false)",
     "v3": RESIDUAL_RULE + "; budget_stop_terminal_eligible_for_grading: true; truncated, stopped_by, tokens and wall "
           "time are reported per session; no minimum size; infrastructure stops stay excluded and are rerun",
     "reason": "Sprint 12: 7 limit stops produced no endpoint; the author decided that a stopped session's work "
               "passes through the same checks",
     "decided_by": "author, 2026-10-09; not to be reopened"},
    {"change": 3, "field": "admission: protected lines",
     "v2": "0 protected lines removed corpus-wide (whitespace-stripped multiset)",
     "v3": "every distinct whitespace-stripped protected line of the baseline is still present somewhere in the "
           "variant (the multiset count is reported beside it)",
     "reason": "Sprint 12: every 'removed' protected line in LT r01, r03, r04 (77/77, 74/74, 82/82) was a duplicate "
               "still present elsewhere; LT consolidates repeated commands, which R0-v4 permits",
     "decided_by": "author, 2026-10-09 (agreed)"},
    {"change": 4, "field": "admission: unresolved links",
     "v2": "0 introduced unresolved internal links, keyed on (page, target)",
     "v3": "a link counts as introduced only if its target, resolved against its page (route key, #fragment kept), "
           "did not occur unresolved anywhere in the baseline (the (page, target) count is reported beside it)",
     "reason": "Sprint 12: the 'introduced' links in LT r01-r04 were already unresolvable in the baseline and moved "
               "to another page",
     "decided_by": "author, 2026-10-09 (agreed)"},
    {"change": 5, "field": "MT target wording",
     "v2": "a table of context links on every page, pointing to the procedures and concepts the page depends on",
     "v3": "a table of context links on every page in your area, pointing to the procedures and concepts the page "
           "depends on",
     "reason": "every page (1,218) is unreachable in one session; settled by change 6",
     "decided_by": "author, 2026-10-09 (via change 6)"},
    {"change": 6, "field": "area scoping and session records",
     "v2": "each session chooses its own scope and stopping point",
     "v3": "one arm-neutral area-selection session (prompts/docker-area-selection-v1.txt) proposes 4 areas, frozen "
           "before any arm session; realization r of every arm works in area r (shared area paragraph: '" +
           AREA_PARAGRAPH + "'); every session ends with one fenced JSON record validated against the frozen schemas; "
           "a replacement runs in the area of the session it replaces; a per-area (arm x area) table is added as a "
           "descriptive",
     "reason": "Sprint 12: realization size and topic varied with the model's choices (LT mostly manuals/engine, MR "
               "across engine, compose and build), confounding topic with arm",
     "decided_by": "author, 2026-10-09, option (2)"},
]

UNCHANGED_V3 = ("null rule and seed formula, subgraph rule with edit endpoints, pooled-SD standardization, measures, "
                "signatures, primary statistic and threshold, C v1, the answer plan, scorer and rubric, the ledgers "
                "and the $60 hard stop")

DISCLOSURES_V3 = [
    "plan v3's changes were made after Sprint 12's editing outcomes; each is motivated by an editing outcome that is "
    "neither spectral nor an answer; nothing was tuned on Sprint 12's spectra or answers",
    "token usage is known only at turn.completed and each Codex session is one turn: a token cap cannot stop a session "
    "mid-run; it labels a session that has already ended as budget_stop (Sprint 12's MT-r01 ended with exit 0 and a "
    "final summary at 6.1M tokens). Wall time is the only limit that interrupts a session; stopped_by, exit code and "
    "the final message are reported per session",
    "null seeds use the frozen formula on variant ids that repeat across runs (LT-r01, ...); the draws come from each "
    "endpoint's own pools, so the nulls differ; runs are never pooled",
    "the same five 11b questions; the areas are chosen without them and may not cover them",
]


def prereg_payload(base_manifest_sha, base_binding_sha):
    return {"schema_version": "docspectra.icsc.structure_forcing_12b.v1.preregistration", "created": DATE,
            "run_id": RUN_ID, "parent_plan": {"path": rel_ctx(PLAN_V2), "sha256": sha(PLAN_V2)},
            "scope": "plan v3's rules, frozen before the area-selection session; the areas, the 12 arm prompts and "
                     "plan v3 itself are frozen after a valid selection (freeze stage)",
            "amendments": AMENDMENTS_V3, "unchanged": UNCHANGED_V3, "admission": ADMISSION_RULE_V2,
            "limits": LIMITS, "selection": {
                "prompt": {"path": rel_ctx(SELECTION_PROMPT), "sha256": sha(SELECTION_PROMPT)},
                "valid": "record valid against area_selection.v1 and the host checks pass (4 distinct ids, no empty "
                         "area, no page in two areas); otherwise rerun once (AS-r02), recorded",
                "after": "the validated record is shown to the author, then the 4 areas are frozen in plan v3"},
            "record_schemas": {"path": rel_ctx(SCHEMAS), "sha256": sha(SCHEMAS)},
            "base_manifest": {"path": str(manifest_file()), "sha256": base_manifest_sha},
            "base_binding": {"path": rel_ctx(binding_path("base")), "sha256": base_binding_sha},
            "arm_prompt_rule": "LT/MT/MR v2 = v1 with the shared limits sentence, (MT) 'on every page in your area', "
                               "the area paragraph after the target paragraph and the record template at the end; "
                               "nothing else changes (tested)",
            "disclosures": DISCLOSURES_V3}


def check_prereg():
    lock = PREREG.with_suffix(".sha256")
    if not PREREG.exists() or not lock.exists():
        raise Refused("preregistration-not-frozen")
    if sha(PREREG) != lock.read_text(encoding="ascii").split()[0]:
        raise Refused("preregistration-hash-changed")
    pre = rr.read_json(PREREG)
    for key in ("record_schemas", "base_binding"):
        if sha(rr.ROOT / pre[key]["path"]) != pre[key]["sha256"]:
            raise Refused(f"{key}-hash-changed")
    if sha(pre["base_manifest"]["path"]) != pre["base_manifest"]["sha256"]:
        raise Refused("base-manifest-hash-changed")
    if sha(rr.ROOT / pre["selection"]["prompt"]["path"]) != pre["selection"]["prompt"]["sha256"]:
        raise Refused("selection-prompt-hash-changed")
    return pre


def prepare(_args):
    if PREREG.exists() or manifest_file().exists():
        raise Refused("already-prepared")
    activate()
    for path, data in ((SELECTION_PROMPT, selection_prompt()),):
        if path.exists() and path.read_bytes() != data:
            raise Refused(f"prompt-file-differs:{path.name}")
        path.write_bytes(data)
    write_or_same(SCHEMAS, record_schemas())
    b_sha = write_or_same(binding_path("base"), base_binding())
    es = rr.load_sandbox()
    m_sha = write_json(manifest_file(), live_manifest(es, RUN_ID, binding_path("base"), None))
    rr.validate_manifest(rr.read_json(manifest_file()), live=True)
    p_sha = write_json(PREREG, prereg_payload(m_sha, b_sha))
    PREREG.with_suffix(".sha256").write_text(p_sha + "  " + PREREG.name + "\n", encoding="ascii")
    print(json.dumps({"preregistration_sha256": p_sha, "base_manifest_sha256": m_sha, "base_binding_sha256": b_sha,
                      "selection_prompt_sha256": sha(SELECTION_PROMPT), "record_schemas_sha256": sha(SCHEMAS)},
                     indent=1))


def templates_v5(areas):
    arms = {}
    for arm in STRUCTURE_ARMS:
        v1 = v1_prompt_path(arm)
        arms[arm] = {"parent": {"path": rel_ctx(v1), "sha256": sha(v1), "version": f"{arm}-v1"},
                     "target_structure": sf.ARM_TARGETS[arm].replace("on every page", "on every page in your area")
                     if arm == "MT" else sf.ARM_TARGETS[arm],
                     "differences_from_parent": [f"'{V1_LIMITS}' -> '{V2_LIMITS}'",
                                                 "area paragraph inserted after the target paragraph: '" +
                                                 AREA_PARAGRAPH + "'",
                                                 "record paragraph appended: '" + RECORD_INTRO + "' + the "
                                                 "edit_session_record.v1 template (arm, area_id and area_paths filled) "
                                                 "+ '" + ARM_RECORD_NOTE + "'"] +
                     ([f"'{MT_V1}' -> '{MT_V2}'"] if arm == "MT" else []),
                     "prompts": {a: {"path": rel_ctx(v2_prompt_path(arm, a)), "sha256": sha(v2_prompt_path(arm, a)),
                                     "version": f"{arm}-v2-{a}"} for a in AREAS}}
    arms[SELECTION_ARM] = {"prompt": {"path": rel_ctx(SELECTION_PROMPT), "sha256": sha(SELECTION_PROMPT),
                                      "version": "AS-v1"}, "role": "area selection, arm-neutral, read-only by "
                                                                   "instruction"}
    return {"schema_version": "docspectra.icsc.restructuring_run.v1.arm_templates_master", "created": DATE,
            "sprint": "12b", "arms": arms, "areas": areas,
            "bindings": {k: rel_ctx(binding_path(k)) for k in ("base",) + AREAS},
            "supersedes": {"path": rel_ctx(TEMPLATES_V4), "sha256": sha(TEMPLATES_V4)},
            "content_rule": "templates contain no questions, rubrics, metric names or graph numbers",
            "launch": "rr.launch reads one prompt per arm, so each area has its own binding file and run manifest "
                      "(same run id sf-12-v2); a namespace's area is the manifest its run-state names"}


def freeze(_args):
    pre = check_prereg()
    activate()
    if PLAN.exists():
        raise Refused("already-frozen")
    sel = rr.read_json(SELECTION_EVIDENCE) if SELECTION_EVIDENCE.exists() else None
    if not sel or not sel.get("valid"):
        raise Refused("no-valid-area-selection")
    areas = {a: sel["areas"][a]["paths"] for a in AREAS}
    for arm in STRUCTURE_ARMS:
        for a in AREAS:
            path = v2_prompt_path(arm, a)
            data = arm_prompt_v2(v1_prompt_path(arm).read_bytes(), arm, a, areas[a])
            if path.exists() and path.read_bytes() != data:
                raise Refused(f"prompt-file-differs:{path.name}")
            path.write_bytes(data)
    t_sha = write_or_same(TEMPLATES_V5, templates_v5(sel["areas"]))
    es = rr.load_sandbox()
    manifests = {"base": {"path": str(manifest_file()), "sha256": pre["base_manifest"]["sha256"]}}
    bindings = {"base": {"path": rel_ctx(binding_path("base")), "sha256": pre["base_binding"]["sha256"]}}
    for a in AREAS:
        prompts = {arm: (v2_prompt_path(arm, a), f"{arm}-v2-{a}") for arm in STRUCTURE_ARMS}
        prompts[SELECTION_ARM] = (SELECTION_PROMPT, "AS-v1")
        b_sha = write_or_same(binding_path(a), templates_binding(prompts, f"area binding {a}"))
        bindings[a] = {"path": rel_ctx(binding_path(a)), "sha256": b_sha}
        m_sha = write_json(manifest_file(area=a), live_manifest(es, RUN_ID, binding_path(a), {
            "area_id": a, "paths": areas[a]}))
        rr.validate_manifest(rr.read_json(manifest_file(area=a)), live=True)
        manifests[a] = {"path": str(manifest_file(area=a)), "sha256": m_sha}
    plan = build_plan_v3(pre, sel, manifests, bindings, t_sha)
    digest = write_json(PLAN, plan)
    PLAN.with_suffix(".sha256").write_text(digest + "  " + PLAN.name + "\n", encoding="ascii")
    print(json.dumps({"plan_v3_sha256": digest, "templates_v5_sha256": t_sha,
                      "manifests": {k: v["sha256"] for k, v in manifests.items()},
                      "prompts": {f"{arm}-{a}": sha(v2_prompt_path(arm, a)) for arm in STRUCTURE_ARMS for a in AREAS}},
                     indent=1))


def build_plan_v3(pre, sel, manifests, bindings, t_sha):
    v2 = rr.read_json(PLAN_V2)
    plan = copy.deepcopy(v2)
    plan["schema_version"] = "docspectra.icsc.structure_forcing_12.v3.plan"
    plan["run_id"] = RUN_ID
    plan["created"] = DATE
    plan["supersedes"] = {"path": rel_ctx(PLAN_V2), "sha256": sha(PLAN_V2),
                          "decided_by": "author in chat, 2026-10-09, after Sprint 12 (sf-12-v1) and before any 12b "
                                        "model call except the area-selection session the plan itself requires",
                          "amendments": AMENDMENTS_V3, "unchanged": UNCHANGED_V3}
    plan["preregistration"] = {"path": rel_ctx(PREREG), "sha256": sha(PREREG)}
    plan["run_manifests"] = manifests
    plan["run_manifest"] = manifests["base"]
    plan["arm_templates"] = {"path": rel_ctx(TEMPLATES_V5), "sha256": t_sha, "bindings": bindings}
    plan["record_schemas"] = {"path": rel_ctx(SCHEMAS), "sha256": sha(SCHEMAS)}
    plan["areas"] = {"frozen": sel["areas"], "selection_evidence": {"path": rel_ctx(SELECTION_EVIDENCE),
                                                                    "sha256": sha(SELECTION_EVIDENCE)},
                     "rule": "realization r of every arm works in area A<r>; a replacement runs in the area of the "
                             "session it replaces; infrastructure reruns keep their area"}
    plan["limits"] = LIMITS
    plan["admission"] = ADMISSION_RULE_V2
    plan["arms"]["replacement"] = ADMISSION_RULE_V2["replacement"]
    order = [f"{arm}-r{i:02d}" for arm in STRUCTURE_ARMS for i in range(1, REALIZATIONS + 1)]
    random.Random(sf.EDIT_ORDER_SEED).shuffle(order)
    if order != v2["edit_order"]["order"]:
        raise Refused("edit-order-differs-from-v2")
    plan["edit_order"]["then"] = ("area selection first (base manifest), then O and S (base manifest), then this "
                                  "order (realization r under the area A<r> manifest); infrastructure reruns, then "
                                  "replacements after all 12")
    plan["descriptives"] = list(v2["descriptives"]) + [
        "edit-magnitude table with stopped_by, truncated, tokens, wall time, exit code and final message per session",
        "per-area table (arm x area): admitted, paths changed, record done",
        "record checks: record_invalid, undeclared and over-declared paths, edits outside the area"]
    plan["answers"]["answering"]["review_label"] = REVIEW_LABEL
    plan["disclosures"] = list(v2["disclosures"]) + DISCLOSURES_V3
    plan["data_sent_out"] = list(v2["data_sent_out"]) + [
        "the 1,218-page public Docker docs tree to Codex for the area-selection session"]
    return plan


def check_frozen(plan_path=None):
    plan_path = Path(plan_path or PLAN)
    lock = plan_path.with_suffix(".sha256")
    if not plan_path.exists() or not lock.exists():
        raise Refused("plan-v3-not-frozen")
    if sha(plan_path) != lock.read_text(encoding="ascii").split()[0]:
        raise Refused("plan-hash-changed-since-freeze")
    plan = rr.read_json(plan_path)
    for key, m in plan["run_manifests"].items():
        if sha(m["path"]) != m["sha256"]:
            raise Refused(f"run-manifest-hash-changed-since-freeze:{key}")
    if sha(rr.ROOT / plan["arm_templates"]["path"]) != plan["arm_templates"]["sha256"]:
        raise Refused("arm-templates-hash-changed-since-freeze")
    for key, b in plan["arm_templates"]["bindings"].items():
        if sha(rr.ROOT / b["path"]) != b["sha256"]:
            raise Refused(f"binding-hash-changed-since-freeze:{key}")
    for key in ("record_schemas", "preregistration"):
        if sha(rr.ROOT / plan[key]["path"]) != plan[key]["sha256"]:
            raise Refused(f"{key}-hash-changed-since-freeze")
    return plan


# ------------------------------------------------------------------ profile activation (sf as a library)

PROFILE_BINDINGS = ("RUN_ID", "PLAN", "ANSWERS", "SCORER", "JUDGE", "REVIEW_LABEL", "ADMISSION_RULE",
                    "run_root", "check_frozen", "latest_admission", "namespace_graphs", "authorized")
_saved = {}


def latest_admission():
    found = sorted(EVIDENCE.glob(ADMISSION_GLOB))
    if len(found) != 1:
        raise Refused("admission-missing-or-ambiguous")
    return rr.read_json(found[0])


def authorized():
    path = run_root() / "host-private/authorization.json"
    if not path.exists():
        raise Refused("no-author-authorization-recorded: step 5 needs an explicit yes before any live call")
    return rr.read_json(path)


def activate():
    """Bind profile sf-12-v2 into sf for this process (undone by deactivate); also lets launch know the AS arm."""
    if _saved:
        return
    values = {"RUN_ID": RUN_ID, "PLAN": PLAN, "ANSWERS": ANSWERS, "SCORER": SCORER, "JUDGE": JUDGE,
              "REVIEW_LABEL": REVIEW_LABEL, "ADMISSION_RULE": ADMISSION_RULE_V2,
              "run_root": lambda run_id=RUN_ID: sf_run_root(run_id), "check_frozen": check_frozen,
              "latest_admission": latest_admission, "namespace_graphs": namespace_graphs, "authorized": authorized}
    for name in PROFILE_BINDINGS:
        _saved[name] = getattr(sf, name)
        setattr(sf, name, values[name])
    _saved["rr.ARMS"], _saved["rr.EDITED"] = rr.ARMS, rr.EDITED
    rr.ARMS = tuple(dict.fromkeys(rr.ARMS + (SELECTION_ARM,)))
    rr.EDITED = tuple(dict.fromkeys(rr.EDITED + (SELECTION_ARM,)))


def deactivate():
    if not _saved:
        return
    rr.ARMS, rr.EDITED = _saved.pop("rr.ARMS"), _saved.pop("rr.EDITED")
    for name, value in _saved.items():
        setattr(sf, name, value)
    _saved.clear()


def sf_run_root(run_id):
    return Path(rr.read_json(V6)["output_root"]) / "runs" / run_id


@contextlib.contextmanager
def profile():
    activate()
    try:
        yield
    finally:
        deactivate()


# ------------------------------------------------------------------ selection

def launch_live(manifest, arm, replicate):
    import os
    if not os.environ.get(rr.AUTH_ENV) and sf.AUTH_DEFAULT.is_file():
        os.environ[rr.AUTH_ENV] = str(sf.AUTH_DEFAULT)
    return rr.launch(manifest, arm, replicate, live=True)


def evaluate_selection(ns, schemas, baseline_rels):
    state = rr.read_json(Path(ns) / "host/run-state.json")
    message = final_message(Path(ns) / "host/transcript.jsonl")
    status, record, why = extract_record(message, "area_selection.v1", schemas)
    out = {"namespace": Path(ns).name, "status": state["status"], "flags": state.get("flags", []),
           "editor_stop": state.get("editor_stop"), "editor_tokens": state.get("editor_tokens"),
           "editor_wall_seconds": state.get("editor_wall_seconds"), "editor_exit_code": state.get("editor_exit_code"),
           "read_only_kept": state["status"] == "no_op", "record_status": status, "record_reason": why,
           "record": record}
    out["host_checks"] = selection_host_checks(record, baseline_rels) if status == "valid" else None
    out["valid"] = status == "valid" and out["host_checks"]["passes"]
    return out


def select(_args):
    pre = check_prereg()
    activate()
    authorized()
    if SELECTION_EVIDENCE.exists():
        raise Refused("selection-already-recorded")
    schemas = rr.read_json(SCHEMAS)
    vm = rr.read_json(V6)
    base = rr.baseline_pages(dcv.pinned(vm), vm)
    rels = sorted(p.rel_path for p in base)
    root = run_root()
    attempts = []
    for rep in range(1, SELECTION_ATTEMPTS + 1):
        ns = root / f"{SELECTION_ARM}-r{rep:02d}"
        if not ns.exists():
            launch_live(manifest_file(), SELECTION_ARM, rep)
        result = evaluate_selection(ns, schemas, rels)
        attempts.append(result)
        print(json.dumps({k: result[k] for k in ("namespace", "status", "record_status", "record_reason", "valid",
                                                 "editor_tokens", "editor_wall_seconds")}, indent=1), flush=True)
        if result["valid"]:
            break
    valid = [a for a in attempts if a["valid"]]
    chosen = valid[0] if valid else None
    out = {"schema_version": "docspectra.icsc.structure_forcing_12b.v1.area_selection", "run_id": RUN_ID,
           "preregistration_sha256": sha(PREREG), "attempts": attempts, "valid": bool(chosen),
           "chosen": chosen["namespace"] if chosen else None,
           "areas": {a["area_id"]: {"paths": chosen["host_checks"]["paths"][a["area_id"]],
                                    "host_page_count": chosen["host_checks"]["host_page_count"][a["area_id"]],
                                    "record_page_count": a["page_count"], "rationale": a["rationale"],
                                    "navigation_problem": a["navigation_problem"],
                                    "fits_one_session": a["fits_one_session"]}
                     for a in chosen["record"]["areas"]} if chosen else None}
    digest = write_json(SELECTION_EVIDENCE, out)
    print(json.dumps({"sha256": digest, "valid": out["valid"], "areas": out["areas"]}, indent=1))


# ------------------------------------------------------------------ edit

def sessions_log(root):
    return Path(root) / "host-private" / "sessions.jsonl"


def read_sessions(root):
    path = sessions_log(root)
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def log_session(root, row):
    path = sessions_log(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(dict(row, time=time.time()), sort_keys=True) + "\n")


def is_infra(ns):
    state = rr.read_json(Path(ns) / "host/run-state.json")
    return any(str(f).startswith("infrastructure_stop:") for f in state.get("flags", []))


def chains(plan, root, sessions):
    """Per arm: [(role, area, [namespaces in rerun order])] for its 4 realizations, then its replacement."""
    sessions = [s for s in sessions if s.get("event") == "launch"]  # the log also holds "end" rows
    by_ns = {s["namespace"]: s for s in sessions}
    out = {}
    for arm in STRUCTURE_ARMS:
        rows = []
        for i in range(1, REALIZATIONS + 1):
            rows.append(("realization", f"A{i}", [f"{arm}-r{i:02d}"]))
        repl = f"{arm}-r{REALIZATIONS + 1:02d}"
        if repl in by_ns:
            rows.append(("replacement", by_ns[repl]["area"], [repl]))
        for s in sessions:
            if s["arm"] == arm and s["role"] == "rerun":
                for _, _, names in rows:
                    if names[-1] == s["of"]:
                        names.append(s["namespace"])
        out[arm] = rows
    return out


def edit_queue(plan, root, base):
    """Planned namespaces not yet launched (with their area), infrastructure reruns, then replacements."""
    sessions = read_sessions(root)
    todo = [(a, 1, None, "control", None) for a in ("O", "S") if not (root / f"{a}-r01").exists()]
    for v in plan["edit_order"]["order"]:
        arm, rep = v.split("-r")
        if not (root / v).exists():
            todo.append((arm, int(rep), f"A{int(rep)}", "realization", None))
    if todo:
        return todo
    for arm, rows in chains(plan, root, sessions).items():
        for role, area, names in rows:
            last = names[-1]
            if (root / last).exists() and is_infra(root / last):
                if len(names) - 1 >= INFRA_RERUNS_MAX:
                    raise Refused(f"infrastructure-reruns-exhausted:{last}: ask the author")
                nxt = max([REALIZATIONS + 1] + [int(p.name.split("-r")[1]) for p in root.glob(f"{arm}-r[0-9][0-9]")]) + 1
                todo.append((arm, nxt, area, "rerun", last))
    if todo:
        return todo
    for arm, rows in chains(plan, root, sessions).items():
        realizations = [r for r in rows if r[0] == "realization"]
        if any(r[0] == "replacement" for r in rows) or (root / f"{arm}-r{REALIZATIONS + 1:02d}").exists():
            continue
        launched_order = {v: i for i, v in enumerate(plan["edit_order"]["order"])}
        failed = [(launched_order.get(names[0], 99), area) for _, area, names in realizations
                  if not admission_record(root / names[-1], base)["admitted"]]
        if failed:
            todo.append((arm, REALIZATIONS + 1, min(failed)[1], "replacement", None))
    return todo


def edit(args):
    plan = check_frozen()
    activate()
    authorized()
    root = run_root()
    vm = rr.read_json(V6)
    gm = dcv.pinned(vm)
    base = Baseline(gm, vm)
    schemas = rr.read_json(SCHEMAS)
    while True:
        queue = edit_queue(plan, root, base)
        if not queue:
            print(json.dumps({"edit": "nothing-left"}))
            return
        arm, rep, area, role, of = queue[0]
        name = f"{arm}-r{rep:02d}"
        manifest = manifest_file() if area is None else manifest_file(area=area)
        log_session(root, {"namespace": name, "arm": arm, "replicate": rep, "area": area, "role": role, "of": of,
                           "manifest_sha256": sha(manifest), "event": "launch"})
        t0 = time.time()
        state = launch_live(manifest, arm, rep)
        ns = root / name
        if arm in STRUCTURE_ARMS:
            residual_terminal(ns, gm=gm, vm=vm)
            rec = admission_record(ns, base, schemas=schemas, area_paths=plan["areas"]["frozen"][area]["paths"])
            brief = {k: rec.get(k) for k in ("status", "admitted", "not_admitted_because", "truncated", "stopped_by",
                                             "editor_tokens", "editor_wall_seconds", "editor_exit_code",
                                             "final_message")}
            brief["record"] = (rec.get("record") or {}).get("status")
            brief["paths_changed"] = sum(rec["review"][k] for k in ("added", "removed", "changed")) \
                if rec.get("review") else None
        else:
            brief = {"status": state["status"]}
        log_session(root, {"namespace": name, "event": "end", "seconds": round(time.time() - t0, 1),
                           "status": state["status"]})
        print(json.dumps({name: dict(brief, area=area, role=role)}, indent=1), flush=True)
        if not args.all:
            return


# ------------------------------------------------------------------ admit

def endpoints_by_slot(plan, root, base):
    out = {"O": "O-r01", "S": "S-r01"}
    shortfall = {}
    for arm, rows in chains(plan, root, read_sessions(root)).items():
        admitted = [names[-1] for _, _, names in rows
                    if (root / names[-1]).exists() and admission_record(root / names[-1], base)["admitted"]]
        for i, name in enumerate(admitted[:REALIZATIONS], 1):
            out[f"{arm}-{i}"] = name
        shortfall[arm] = max(0, REALIZATIONS - len(admitted))
    return out, shortfall


def effective_state(ns):
    """run-state merged with the plan v3 terminal (residual at a limit stop)."""
    state = rr.read_json(Path(ns) / "host/run-state.json")
    tree, _, _, src, residual = terminal_view(ns)
    if src == "residual":
        state = dict(state, terminal_tree=str(tree) if tree else None,
                     terminal_checkpoint=state.get("budget_stop_residual"),
                     eligible_for_grading=bool(tree and residual and residual.get("eligible_for_grading")))
    return state


def grading_export(manifest_path, namespaces, dest, key_path):
    """rr.grading_export with the plan v3 terminal; the export and key format are identical."""
    manifest = rr.read_json(manifest_path)
    rr.validate_manifest(manifest, live=False)
    out_root = Path(manifest["output_namespace"]["root"]).resolve()
    dest, key_path = Path(dest).resolve(), Path(key_path).resolve()
    if out_root not in dest.parents or dest.exists() or dest in key_path.parents or key_path == dest:
        raise Refused("grading-export-path")
    rows = [s for s in (effective_state(ns) for ns in namespaces) if s["eligible_for_grading"]]
    order = list(range(len(rows)))
    secrets.SystemRandom().shuffle(order)
    ids = set()
    key = {"schema_version": rr.SCHEMA + ".grading_key", "packet": str(dest), "variants": {}}
    dest.mkdir(parents=True)
    for i in order:
        state = rows[i]
        opaque = "v-" + secrets.token_hex(6)
        while opaque in ids:
            opaque = "v-" + secrets.token_hex(6)
        ids.add(opaque)
        target = dest / "variants" / opaque
        rr.quiet(dcv.export_agent, SimpleNamespace(manifest=Path(manifest["variant_manifest"]["path"]),
                                                   candidate=Path(state["terminal_tree"]), dest=target))
        receipt = target / "export-receipt.json"
        private_receipt = key_path.parent / "export-receipts" / f"{opaque}.json"
        private_receipt.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(receipt), private_receipt)
        key["variants"][opaque] = {"arm": state["arm"], "replicate": state["replicate"], "run_id": state["run_id"],
                                   "status": state["status"], "terminal_checkpoint": state.get("terminal_checkpoint"),
                                   "corpus_tree_sha256": dcv.tree_hash(dcv.inventory(target / "corpus")),
                                   "graph_file_sha256": dcv.file_hash(target / "graph_edges.csv")}
    packet = {"schema_version": rr.SCHEMA + ".grading_packet", "variants": sorted(ids),
              "contains": ["corpus/<doc_id>.md", "graph_edges.csv"], "labels": "opaque; arm key held outside packet",
              "contains_evaluation_material": False}
    rr.write_new(dest / "packet.json", packet)
    key_sha = rr.write_new(key_path, key)
    return {"packet": str(dest), "variants": len(ids), "key_sha256": key_sha,
            "packet_sha256": dcv.file_hash(dest / "packet.json")}


def magnitude_row(ns, rec, gm, vm, area):
    row = {"area": area, "status": rec["status"], "admitted": rec["admitted"], "truncated": rec["truncated"],
           "stopped_by": rec["stopped_by"], "tokens": rec.get("editor_tokens"),
           "wall_seconds": rec.get("editor_wall_seconds"), "exit_code": rec.get("editor_exit_code"),
           "final_message": rec.get("final_message"),
           "record": (rec.get("record") or {}).get("status"), "record_done": (rec.get("record") or {}).get("done")}
    if rec.get("review") is None:
        return row
    om, nodes, ve, receipt, T = namespace_graphs(gm, vm, ns)
    mag = sf.edit_magnitude(om, ve, T)
    row.update(paths_changed=rec["review"]["added"] + rec["review"]["removed"] + rec["review"]["changed"],
               pages_added=len(receipt["added"]), pages_removed=len(receipt["removed"]),
               edges_added=mag["edges_added"], edges_removed=mag["edges_removed"],
               reciprocal_fraction_added=mag["reciprocal_fraction_added"], touched=len(T),
               unmapped_removals=len(receipt["unmapped_removals"]), partial_unit_loss=len(receipt["partial_unit_loss"]))
    check = (rec.get("record") or {}).get("check") or {}
    row["outside_area_changed_or_removed"] = len(check.get("outside_area", {}).get("changed", [])) + \
        len(check.get("outside_area", {}).get("removed", []))
    return row


def admit_run(run_id, plan, plan_path, evidence_path, *, gm, vm, area_paths, base_manifest, sessions_area):
    root = run_root(run_id)
    base = Baseline(gm, vm)
    schemas = rr.read_json(SCHEMAS)
    records, magnitude = {}, {}
    for p in sorted(root.glob("*-r[0-9][0-9]")):
        if p.name.split("-r")[0] not in STRUCTURE_ARMS:
            continue
        area = sessions_area.get(p.name)
        rec = admission_record(p, base, schemas=schemas, area_paths=area_paths.get(area, []))
        rec["area"] = area
        records[p.name] = rec
        magnitude[p.name] = magnitude_row(p, rec, gm, vm, area)
    per_area = defaultdict(dict)
    for name, row in magnitude.items():
        per_area[row["area"] or "-"][name] = {k: row.get(k) for k in ("admitted", "paths_changed", "record_done",
                                                                      "truncated", "stopped_by")}
    slots_map, shortfall = endpoints_by_slot(plan, root, base)
    namespaces = [root / slots_map[s] for s in sorted(slots_map)]
    dest, key_path = root / "grading-export", root / "host-private/grading-key.json"
    grading = grading_export(base_manifest, namespaces, dest, key_path)
    key = rr.read_json(key_path)
    by_ns = {f"{v['arm']}-r{v['replicate']:02d}": k for k, v in key["variants"].items()}
    sham = rr.tree_bytes(dest / "variants" / by_ns["O-r01"]) == rr.tree_bytes(dest / "variants" / by_ns["S-r01"])
    o_state = rr.read_json(root / "O-r01/host/run-state.json")
    f_ref = rr.f_reference_check(rr.read_json(root / "O-r01/host/receipts/spectral-profile.json"))
    out = {"schema_version": "docspectra.icsc.structure_forcing_12b.v1.admission", "run_id": run_id,
           "plan_sha256": sha(plan_path), "admission_rule": ADMISSION_RULE_V2,
           "records": records, "edit_magnitude": magnitude, "per_area": dict(per_area), "slots": slots_map,
           "shortfall": shortfall, "grading_export": grading, "sham_identity": sham, "O_status": o_state["status"],
           "f_reference": f_ref,
           "note": "opaque variant ids; the key stays in host-private and is read again only by analyse-answers"}
    return out, write_json(evidence_path, out)


def admit(_args):
    plan = check_frozen()
    activate()
    if list(EVIDENCE.glob(ADMISSION_GLOB)):
        raise Refused("already-admitted")
    vm = rr.read_json(V6)
    gm = dcv.pinned(vm)
    base = Baseline(gm, vm)
    if edit_queue(plan, run_root(), base):
        raise Refused("editing-not-finished")
    sessions_area = {s["namespace"]: s["area"] for s in read_sessions(run_root()) if s.get("event") == "launch"}
    area_paths = {a: plan["areas"]["frozen"][a]["paths"] for a in AREAS}
    path = EVIDENCE / f"structure-forcing-12b-admission-v1-{sf.today()}.json"
    out, digest = admit_run(RUN_ID, plan, PLAN, path, gm=gm, vm=vm, area_paths=area_paths,
                            base_manifest=manifest_file(), sessions_area=sessions_area)
    print(json.dumps({"sha256": digest, "sham_identity": out["sham_identity"], "shortfall": out["shortfall"],
                      "slots": out["slots"], "f_reference_exact": out["f_reference"]["lambda1_exact"] and
                      out["f_reference"]["normalized_lambda2_exact"]}, indent=1))


# ------------------------------------------------------------------ controls, spectra, analysis (frozen v2 code)

def generate_controls(_args):
    check_frozen()
    activate()
    admission = latest_admission()
    marker = run_root() / "controls/complete.json"
    if marker.exists():
        raise Refused("controls-already-generated")
    vm = rr.read_json(V6)
    write_json(marker, sf.controls_run(RUN_ID, admission, gm=dcv.pinned(vm), vm=vm))
    print(marker.read_text(encoding="utf-8"))


def spectra(_args):
    check_frozen()
    activate()
    admission = latest_admission()
    marker = run_root() / "spectra/complete.json"
    if marker.exists():
        raise Refused("spectra-already-complete")
    if not (run_root() / "controls/complete.json").exists():
        raise Refused("controls-not-generated")
    vm = rr.read_json(V6)
    checks = sf.spectra_run(RUN_ID, admission, gm=dcv.pinned(vm), vm=vm, log=lambda s: print(s, flush=True))
    write_json(marker, {"checks": checks})
    print(json.dumps(checks, indent=1))


def analyse_spectra(_args):
    check_frozen()
    activate()
    if list(EVIDENCE.glob(SPECTRA_GLOB)):
        raise Refused("spectra-already-analysed")
    admission = latest_admission()
    checks = rr.read_json(run_root() / "spectra/complete.json")["checks"]
    vm = rr.read_json(V6)
    res = sf.analyse_spectra_run(RUN_ID, admission, checks, gm=dcv.pinned(vm), vm=vm)
    res.update(schema_version="docspectra.icsc.structure_forcing_12b.v1.spectra", plan_sha256=sha(PLAN),
               admission_sha256=sha(sorted(EVIDENCE.glob(ADMISSION_GLOB))[0]), per_area=admission.get("per_area"))
    path = EVIDENCE / f"structure-forcing-12b-spectra-v1-{sf.today()}.json"
    digest = write_json(path, res)
    path.with_suffix(".md").write_text(sf.spectra_markdown(res, digest).replace("# Sprint 12 spectra",
                                                                                "# Sprint 12b spectra (sf-12-v2)"),
                                       encoding="utf-8")
    print(json.dumps({"sha256": digest, "primary": res["primary"],
                      "bd": {k: v["passes"] for k, v in checks.items()}}, indent=1))


# ------------------------------------------------------------------ answers (sf stages on this run's folders)

def answer(args):
    activate()
    sf.answer(args)


def archive_infra(args):
    activate()
    sf.archive_infra(args)


def score(args):
    activate()
    sf.score(args)


def rubric(args):
    activate()
    sf.rubric(args)


def analyse_answers(_args):
    import answer_quality_scorer as aqs
    import rubric_quote_match as rqm
    plan = check_frozen()
    activate()
    if list(EVIDENCE.glob(ANSWERS_GLOB)):
        raise Refused("answers-already-analysed")
    admission = latest_admission()
    passes = {p: sf.pass_scores(SCORER / f"pass-{p}") for p in ("A", "B")}
    key = rr.read_json(run_root() / "host-private/grading-key.json")  # unblinding happens here
    arm_of = {k: v["arm"] for k, v in key["variants"].items()}
    measures = list(aqs.MEASURES)
    cells, usage, terminals = {}, Counter(), Counter()
    for path in sf.answer_files():
        trace = rr.read_json(path)
        stem = path.stem
        variant = "-".join(stem.split("-")[:2])
        rep = int(stem.rsplit("-r", 1)[1])
        cond = stem.split("-", 3)[3].rsplit("-r", 1)[0]
        terminals[trace["terminal"]] += 1
        for k, src in (("prompt_tokens", "prompt_tokens_reported"), ("output_tokens", "output_tokens_reported"),
                       ("model_calls", "model_calls"), ("invalid_actions", "invalid_actions")):
            usage[k] += int(trace["usage"].get(src) or 0)
        per_pass = [passes[p].get(path.name) for p in passes]
        cells[(variant, trace["question_id"], cond, rep)] = {
            m: (sum(s[m] for s in per_pass if s) / sum(1 for s in per_pass if s)) if any(per_pass) else None
            for m in measures}
    rubric_scores = rqm.score_judge_root(JUDGE)
    rubric_cells = {k: {"rubric_relaxed": (rubric_scores.get(f"{k[0]}-{k[1][:12]}-{k[2]}-r{k[3]}.json") or {}).get(
        "rubric_v1_relaxed")} for k in cells}
    qids = plan["answers"]["questions"]["ids"]
    variant_arm = {v: arm_of[v] for v in {k[0] for k in cells}}
    out = {"schema_version": "docspectra.icsc.structure_forcing_12b.v1.answers", "plan_sha256": sha(PLAN),
           "scope": "exploratory; 5 questions; Luna self-grading; no snapshot; no human graders; scorer chosen on "
                    "the 11b answers; S - O is the yardstick", "terminals": dict(terminals),
           "answers": len(cells), "usage": dict(usage),
           "scorer_v2": sf.answer_tables(cells, variant_arm, qids, measures),
           "rubric_relaxed": sf.answer_tables(rubric_cells, variant_arm, qids, ["rubric_relaxed"]),
           "slots": admission["slots"]}
    path = EVIDENCE / f"structure-forcing-12b-answers-v1-{sf.today()}.json"
    print(json.dumps({"sha256": write_json(path, out), "terminals": dict(terminals), "usage": dict(usage)}, indent=1))


# ------------------------------------------------------------------ fixture (zero model calls)

class RecordingScriptedDriver:
    """The Sprint 11 scripted shell editor plus, if it is not stopped, a final agent message (fixture only)."""

    def __init__(self, es, batches, message=None):
        self.inner, self.message = es.ScriptedShellDriver(batches), message

    def identity(self):
        return dict(self.inner.identity(), final_message=self.message is not None)

    def execute(self, container, on_line, wall_seconds, stop):
        code, why = self.inner.execute(container, on_line, wall_seconds, stop)
        if code == 0 and not stop.is_set() and self.message is not None:
            on_line(json.dumps({"type": "item.completed", "item": {"id": "final", "type": "agent_message",
                                                                    "text": self.message}}))
        return code, why


def fenced(record) -> str:
    return "Summary of the session.\n\n```json\n" + json.dumps(record, indent=1) + "\n```\n"


def fixture_areas(rels):
    candidates = ["content/manuals/engine/network", "content/manuals/engine/storage", "content/manuals/compose/how-tos",
                  "content/manuals/build/building"]
    areas = []
    for i, p in enumerate(candidates, 1):
        n = sum(1 for r in rels if in_area(r, [p]))
        if not n:
            raise Refused(f"fixture-area-empty:{p}")
        areas.append({"area_id": f"A{i}", "paths": [p + "/"], "page_count": n, "rationale": "fixture",
                      "navigation_problem": "fixture", "fits_one_session": True})
    return {"schema": "area_selection.v1", "areas": areas, "excluded_candidates": [], "method": "fixture (scripted)"}


def fixture(_args):
    pre = check_prereg()
    activate()
    if FIXTURE_EVIDENCE.exists():
        raise Refused("fixture-already-run")
    es = rr.load_sandbox()
    vm = rr.read_json(V6)
    gm = dcv.pinned(vm)
    base = Baseline(gm, vm)
    schemas = rr.read_json(SCHEMAS)
    root = run_root(FIXTURE_RUN_ID)
    mdir = root / "manifests"
    fx_prompts = root / "fixture-prompts"
    t0 = time.time()
    # selection (scripted; zero model calls) and its record
    sel_record = fixture_areas(base.rels)
    b_base = fx_prompts / "binding-base.json"
    if not manifest_file(FIXTURE_RUN_ID).exists():
        write_json(b_base, base_binding())
        write_json(manifest_file(FIXTURE_RUN_ID), fixture_manifest(es, FIXTURE_RUN_ID, b_base, None))
    if not (root / "AS-r01").exists():
        rr.launch(manifest_file(FIXTURE_RUN_ID), SELECTION_ARM, 1,
                  driver=RecordingScriptedDriver(es, [], fenced(sel_record)))
    selection = evaluate_selection(root / "AS-r01", schemas, base.rels)
    if not selection["valid"]:
        raise Refused("fixture-selection-invalid:" + str(selection["record_reason"]))
    a1 = selection["host_checks"]["paths"]["A1"]
    # area A1 binding + manifest, and a fixture-only edit-limit manifest for the truncated stand-in
    prompts = {}
    for arm in STRUCTURE_ARMS:
        path = v2_prompt_path(arm, "A1", fx_prompts)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = arm_prompt_v2(v1_prompt_path(arm).read_bytes(), arm, "A1", a1)
        if not path.exists():
            path.write_bytes(data)
        prompts[arm] = (path, f"{arm}-v2-A1-fixture")
    prompts[SELECTION_ARM] = (SELECTION_PROMPT, "AS-v1")
    b_a1 = fx_prompts / "binding-A1.json"
    m_a1, m_stop = manifest_file(FIXTURE_RUN_ID, "A1"), manifest_file(FIXTURE_RUN_ID, "A1-stop")
    if not m_a1.exists():
        write_json(b_a1, templates_binding(prompts, "fixture area binding A1"))
        write_json(m_a1, fixture_manifest(es, FIXTURE_RUN_ID, b_a1, {"area_id": "A1", "paths": a1}))
        write_json(m_stop, fixture_manifest(es, FIXTURE_RUN_ID, b_a1, {"area_id": "A1", "paths": a1},
                                            edits=FIXTURE_STOP_EDITS))
    for arm in ("O", "S"):
        if not (root / f"{arm}-r01").exists():
            rr.launch(manifest_file(FIXTURE_RUN_ID), arm, 1)
    stand_ins, edits = {}, {}
    for arm in STRUCTURE_ARMS:
        batches, plan = sf.fixture_batches(gm, vm, arm)
        edits[arm] = plan
        touched = sorted({op[1] for b in batches for op in b})
        record = {"schema": "edit_session_record.v1", "arm": arm, "area_id": "A1", "area_paths": a1,
                  "pages_in_area": selection["host_checks"]["host_page_count"]["A1"],
                  "pages_edited": [r for r in touched if r in base.rels],
                  "pages_created": [r for r in touched if r not in base.rels], "pages_removed": [], "done": True,
                  "not_done_reason": None, "edits": [{"pages": touched, "reason": "fixture", "moved_from": []}],
                  "preservation_concerns": [], "elapsed_minutes_at_stop": 1}
        if arm == "MT":  # exercises the diff check: one declared page that was not edited
            record["pages_edited"] = record["pages_edited"] + [a1[0].rstrip("/") + "/not-edited.md"]
        message = None if arm == "MR" else fenced(record)  # MR stand-in: no record (record_invalid)
        stand_ins[f"{arm}-r01"] = (m_a1, arm, 1, batches, message)
    mr_batches, _ = sf.fixture_batches(gm, vm, "MR")
    stand_ins["MR-r02"] = (m_stop, "MR", 2, mr_batches, fenced({"schema": "edit_session_record.v1"}))
    for name, (mpath, arm, rep, batches, message) in stand_ins.items():
        if not (root / name).exists():
            rr.launch(mpath, arm, rep, driver=RecordingScriptedDriver(es, batches, message))
        residual_terminal(root / name, gm=gm, vm=vm)
    stage = {"edit": round(time.time() - t0, 1)}
    records = {n: admission_record(root / n, base, schemas=schemas, area_paths=a1) for n in stand_ins}
    magnitude = {n: magnitude_row(root / n, records[n], gm, vm, "A1") for n in stand_ins}
    slots = {"O": "O-r01", "S": "S-r01", "LT-1": "LT-r01", "MT-1": "MT-r01", "MR-1": "MR-r01", "MR-2": "MR-r02"}
    not_admitted = [n for n in slots.values() if n in records and not records[n]["admitted"]]
    if not_admitted:
        raise Refused("fixture-stand-in-not-admitted:" + ",".join(not_admitted))
    admission = {"slots": slots, "edit_magnitude": magnitude}
    t1 = time.time()
    controls = sf.controls_run(FIXTURE_RUN_ID, admission, gm=gm, vm=vm, log=lambda s: None)
    stage["generate-controls"] = round(time.time() - t1, 1)
    t1 = time.time()
    checks = sf.spectra_run(FIXTURE_RUN_ID, admission, gm=gm, vm=vm, log=lambda s: print(s, flush=True))
    stage["spectra"] = round(time.time() - t1, 1)
    t1 = time.time()
    res = sf.analyse_spectra_run(FIXTURE_RUN_ID, admission, checks, gm=gm, vm=vm)
    stage["analyse-spectra"] = round(time.time() - t1, 1)
    o_receipt = rr.read_json(root / "O-r01/host/receipts/spectral-profile.json")
    nulls = {n: [rr.read_json(root / "controls" / n / f"rn-{k}.json")["receipt"]
                 for k in range(1, sf.NULLS_PER_VARIANT + 1)] for n in slots.values() if n not in ("O-r01", "S-r01")}
    summary = {"schema_version": "docspectra.icsc.structure_forcing_12b.v1.fixture", "date": sf.today(),
               "model_calls": 0, "preregistration_sha256": sha(PREREG),
               "run_manifests": {p.name: sha(p) for p in sorted(mdir.glob("*.json"))},
               "selection": {k: selection[k] for k in ("status", "record_status", "valid", "host_checks")},
               "synthetic_edits": edits, "admission": records, "edit_magnitude": magnitude, "null_receipts": nulls,
               "controls": controls, "checks": checks, "primary_on_fixture": res["primary"],
               "full_view_statistic_on_fixture": res["full_view_statistic_descriptive"],
               "signature_tally_subgraph": res["views"]["subgraph"]["signature_tally"],
               "f_reference_check": rr.f_reference_check(o_receipt), "stage_seconds": stage,
               "scope": "tooling check on synthetic stand-in edits under the plan v3 rules; zero model calls; the "
                        "MR-r02 stand-in is stopped by a fixture-only edit limit to exercise the residual terminal; "
                        "not evidence about any arm"}
    digest = write_json(FIXTURE_EVIDENCE, summary)
    print(json.dumps({"sha256": digest, "selection_valid": selection["valid"],
                      "admission": {n: {k: r.get(k) for k in ("status", "admitted", "truncated", "stopped_by",
                                                              "terminal_source", "not_admitted_because")}
                                    | {"record": (r.get("record") or {}).get("status")} for n, r in records.items()},
                      "edit_magnitude": magnitude, "primary": res["primary"],
                      "bd": {k: (v["worst"], v["passes"]) for k, v in checks.items()},
                      "f_reference": summary["f_reference_check"], "stage_seconds": stage}, indent=1, default=str))


# ------------------------------------------------------------------ CLI

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="stage", required=True)
    for name in ("prepare", "fixture", "select", "freeze", "admit", "generate-controls", "spectra", "analyse-spectra",
                 "rubric", "analyse-answers"):
        sub.add_parser(name)
    e = sub.add_parser("edit")
    e.add_argument("--next", action="store_true", required=True)
    e.add_argument("--all", action="store_true", help="run the whole remaining queue")
    an = sub.add_parser("answer")
    an.add_argument("--replicate", type=int, choices=(1, 2))
    ai = sub.add_parser("archive-infra")
    ai.add_argument("cell")
    ai.add_argument("--reason", required=True)
    sc = sub.add_parser("score")
    sc.add_argument("--pass", dest="pass_", choices=("A", "B"), required=True)
    sc.add_argument("--worker", type=int, choices=(0, 1), required=True)
    args = parser.parse_args()
    {"prepare": prepare, "fixture": fixture, "select": select, "freeze": freeze, "edit": edit, "admit": admit,
     "generate-controls": generate_controls, "spectra": spectra, "analyse-spectra": analyse_spectra,
     "answer": answer, "archive-infra": archive_infra, "score": score, "rubric": rubric,
     "analyse-answers": analyse_answers}[args.stage](args)


if __name__ == "__main__":
    main()

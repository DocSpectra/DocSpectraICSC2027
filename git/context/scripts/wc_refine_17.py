"""Sprint 17: freeze the refine run plan, then run the LT and R0 sessions with the October 18 schedule rules.

Thin driver over scripts/wc_refinement_15.py and scripts/wc_freeze_16.py (both unchanged). Stages:

  check     the pre-registration lock and its pins (one documented exception, below), the survey freeze and the
            refine run plan if it exists.
  freeze    write every LT and R0 session prompt (pass, resume and reconcile), binding and live manifest, launch the
            O and S controls (zero model calls), seed the ledger with the survey row (the survey counts to LT) and
            freeze evidence/wc-run-plan-refine-v1-2026-10-18.json (+ .sha256). No model calls.
  run       live sessions from the frozen plan after authorization point 2 (recorded with
            `wc_freeze_16.py authorize --point 2 --plan <refine plan>`). Between sessions it applies the schedule
            checkpoint and writes the early projection; it never changes a frozen rule.
  decide    record the author's decision at the 48-hour checkpoint (continue or stop).
  status    per-area and per-arm summary of the ledger.

Pin exception: docs/wc-freeze-16.md gained the author's October 18 addendum after the pre-registration. The check
accepts it only if the pre-registration commit's bytes (which hash to the pin) are an exact prefix of the current file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wc_refinement_15 as wr  # noqa: E402
import wc_freeze_16 as wf  # noqa: E402

rr, sfb, sha, write_json, Refused = wr.rr, wr.sfb, wr.sha, wr.write_json, wr.Refused
ROOT, CONTEXT, EVIDENCE = rr.ROOT, rr.CONTEXT, wf.EVIDENCE
DATE = "2026-10-18"
RUN_ID, RUN_ROOT = wf.RUN_ID, wf.RUN_ROOT
REFINE_PLAN_V1 = EVIDENCE / f"wc-run-plan-refine-v1-{DATE}.json"   # superseded before any editing session (below)
REFINE_PLAN = EVIDENCE / f"wc-run-plan-refine-v2-{DATE}.json"
TIER1 = EVIDENCE / "wc-tier1-tasks-v2-2026-10-18.json"
PREREG_COMMIT = "e4d5ec4"
ADDENDUM_DOC = "docs/wc-freeze-16.md"
BASE_PROMPTS = {"LT": sfb.v1_prompt_path("LT"), "R0": CONTEXT / "prompts/docker-editor-R0-v4.txt"}
# v2 composition (author, October 18, before authorization 2): the base prompts' stale 60-minute / 150-path sentence is
# removed (12b's arm_prompt_v2 replaced it; wc_refinement_15.session_prompt only appends the 90-minute sentence), and
# R0, which has no target structure, gets neutral wording in the appended paragraphs. LT's appended text is unchanged.
STALE_LIMITS = " " + sfb.V1_LIMITS
R0_WORDING = [("meets the target structure", "meets the reorganization goal above"),
              ("plus any new pages your target structure needs", "plus any new pages your reorganization needs"),
              ("toward the same target structure", "toward the same reorganization goal"),
              ("unless the target structure requires it", "unless the reorganization goal requires it")]
CHECKPOINT_SECONDS = 48 * 3600
PROJECTION_AREAS = 3
CHECKPOINT_RULE = ("At 48 hours of wall time from the first editing session's launch, no new session starts; the session "
                   "in progress finishes and goes through admission. The run pauses and Claude reports areas completed "
                   "in both arms, the projected time to finish, resumes, rollbacks, halts and any subscription "
                   "throttling. The author decides: continue under the pre-registered limits, or stop (unstarted areas "
                   "recorded as unedited in both arms). Without a decision the run waits. No spectra, nulls, answers or "
                   "scores are computed before the decision (author, October 18).")
PROJECTION_RULE = ("After the first 3 areas of the seeded order are done in both arms (their pass sessions ended and no "
                   "resume pending), project total wall time from seconds per baseline page over those areas, scaled by "
                   "the remaining areas' page counts, per arm; reconciliation is shown at its 90-minute ceiling. "
                   "Information only, not a stopping rule (author, October 18).")


def ledger_rows():
    return wr.read_ledger(RUN_ROOT)


def voided(rows):
    return {q for r in rows if r["event"] == "void" for q in r["seqs"]}


def effective(rows):
    """The ledger without voided rows and void rows, with each `amend` row's fields applied to the end row it names
    (a session re-evaluated after an admission implementation fix); amend rows themselves drop out."""
    gone = voided(rows)
    patches = {}
    for r in rows:
        if r["event"] == "amend" and r.get("seq") not in gone:
            patches.setdefault(r["amends_seq"], {}).update(r["fields"])
        if r["event"] == "reopen" and r.get("seq") not in gone:  # the item runs again; tokens and seconds still count
            patches.setdefault(r["reopens_seq"], {}).update(kind=r["kind"] + "-superseded")
    return [dict(r, **patches.get(r.get("seq"), {})) for r in rows
            if r["event"] not in ("void", "amend", "reopen") and r.get("seq") not in gone]


# ------------------------------------------------------------------ admission fix: Docker's Hugo site URLs
# Found October 18 on R0 A21 (R0-r02): the frozen resolver joins a root-relative target to the repository root, but
# Docker's Hugo site serves content/manuals/<p> and content/<p> at /<p>/. Every site-URL link was therefore
# "unresolved" (1,058 in the baseline), and a correct new link to an existing page failed admission unless some
# baseline page already linked that exact target. Fix: an introduced unresolved link whose root-relative target maps
# to an existing page of the variant tree under that rule is resolved. Genuinely broken links still fail.
HUGO_PREFIXES = ("content/manuals/", "content/")
HUGO_FIX = ("root-relative link /<p>/ resolves if content/manuals/<p> or content/<p> exists as <p>.md, <p>/_index.md "
            "or <p>/index.md in the variant tree (fragment and query ignored); applied to introduced_unresolved_v3 "
            "only; the baseline set, C v1 and the reported v1 count are unchanged")
_tree_rels = {"rels": None}
_introduced_v3 = sfb.introduced_unresolved_v3


def hugo_target(target, rels):
    if not target.startswith("/") or target.startswith("//"):
        return None
    p = target.split("#")[0].split("?")[0].strip("/")
    if not p:
        return None
    for pre in HUGO_PREFIXES:
        for c in (f"{pre}{p}", f"{pre}{p}.md", f"{pre}{p}/_index.md", f"{pre}{p}/index.md"):
            if c in rels and c.endswith(".md"):
                return c
    return None


def introduced_unresolved_hugo(variant_unresolved, baseline_unresolved, route_key=None):
    out = _introduced_v3(variant_unresolved, baseline_unresolved, route_key)
    rels = _tree_rels["rels"]
    return out if rels is None else [x for x in out if hugo_target(x["target"], rels) is None]


_admission_record = sfb.admission_record


def admission_record_hugo(ns, base):
    tree = sfb.terminal_view(ns)[0]
    _tree_rels["rels"] = set(wr.dcv.inventory(Path(tree))) if tree is not None else None
    try:
        return _admission_record(ns, base)
    finally:
        _tree_rels["rels"] = None


sfb.introduced_unresolved_v3 = introduced_unresolved_hugo
sfb.admission_record = admission_record_hugo


# ------------------------------------------------------------------ admission fix: shell-prompt marker
# Found October 19 on LT A24 (LT-r14): the protected-line rule compares whitespace-stripped lines exactly, so a command
# kept on a consolidated page with or without its "$ " prompt marker (the baseline has both forms on different pages)
# counted as a lost fact. Fix: a baseline protected line is present if, after removing one leading "$" and the
# whitespace after it, it equals such a variant line. Nothing else changes (commands, flags, warnings compare exactly).
PROMPT_FIX = ('a baseline protected line counts as present if it equals a variant protected line once a leading "$" '
              "prompt marker and its following whitespace are removed from both, and (fix 5b, LT A18) runs of internal "
              "whitespace are collapsed to one space; (fix 5c, LT reconciliation) a code-fence opener whose only change is "
              "a completed closing attribute brace is present")
_protected_missing_v3 = sfb.protected_missing_v3


def _unprompt(line):
    return re.sub(r"\s+", " ", re.sub(r"^\$\s+", "", line))


# Fix 5c (LT reconciliation, LT-r30): the baseline has a code-fence opener with an unclosed attribute brace
# (```json {title="Example configuration"); an edit that closes the brace kept the block but counted as a lost line.
# A code-fence opener is present if a variant opener equals it plus the missing closing "}".
def _fence_repaired(line, present):
    return line.startswith("```") and line.count("{") > line.count("}") and _unprompt(line + "}") in present


def protected_missing_prompt(base_texts, variant_texts):
    missing = _protected_missing_v3(base_texts, variant_texts)
    if not missing:
        return missing
    present = {_unprompt(t) for t in sfb.protected_tokens(variant_texts)}
    return [t for t in missing if _unprompt(t) not in present and not _fence_repaired(t, present)]


sfb.protected_missing_v3 = protected_missing_prompt


# ------------------------------------------------------------------ admission fix: C2 evaluation set
# Found October 18 on LT A21 (LT-r03): C2 computes lambda2 on the subgraph induced by (baseline main component + new
# pages) and scores 0 when that subgraph is disconnected. An edit that links a page from outside the baseline main
# component into it, with a new page reached only through that page, then scores lambda2 = 0 although the variant's
# main component is connected (LT-r03: 0.0 v1, 0.01092 on the fixed set, threshold 0.00993). Fix: when C2 fails, lambda2
# is recomputed on the v1 set plus the baseline pages that joined the main component; C0, C1, C1b, the threshold and a
# genuinely low lambda2 (LT-r02: 0.00817 on a connected set) are unchanged. Both values are kept in the receipt.
C2_FIX = ("on a C2 failure, lambda2 is recomputed on the v1 evaluation set plus the baseline pages outside the baseline "
          "main component that the variant joined to its main component; C2 passes if that value meets the v1 threshold")
_c_admission = rr.c_admission


def c_admission_fixed(manifest, gm, base_page_list, variant_nodes, variant_edges, mapping):
    out = _c_admission(manifest, gm, base_page_list, variant_nodes, variant_edges, mapping)
    res = out["result"]
    if "C2-lambda2-below-tolerance" not in res["reasons"]:
        return out
    st = rr.load_struct()
    ns = st.canonical_nodes(variant_nodes)
    es = st.canonical_edges(ns, variant_edges)
    m = st.main_component(ns, es)
    base_nodes = sorted(p.doc_id for p in base_page_list)
    base_main = st.main_component(base_nodes, gm.extract_markdown_edges(base_page_list))
    image = lambda v: (mapping or {}).get(v, v)  # noqa: E731
    known = set(ns)
    v1_set = ({image(v) for v in base_main} | (known - set(base_nodes))) & known
    joined = {v for v in m if v in set(base_nodes) and v not in base_main}
    ev = v1_set | joined
    lam = st.normalized_lambda2(sorted(ev), [e for e in es if e[0] in ev and e[1] in ev])
    res = dict(res, lambda2_v1=res["lambda2"], lambda2=lam, c2_fix=C2_FIX, c2_joined_pages=len(joined))
    if lam >= res["lambda2_threshold"]:
        res["reasons"] = [r for r in res["reasons"] if r != "C2-lambda2-below-tolerance"]
        res["passed"] = not res["reasons"]
    return dict(out, result=res, c_fix="wc_refine_17.C2_FIX")


rr.c_admission = c_admission_fixed


# ------------------------------------------------------------------ author amendment: C1b for templated pages
# Author, October 19, after all area passes and before reconciliation (rule review): the link graph excludes Hugo
# templating by design, so a new page reached only through templating can never have a Markdown in-link. C1b does not
# count against (a) a new page under content/includes/ that some variant page references with an include shortcode,
# or (b) a new section _index.md whose folder holds at least one other page. Both arms. Every other C1b case, and C0,
# C1 and C2, are unchanged. Found on LT A10 (bake examples _index, 4 child pages) and LT A12 (an include used by
# services-storage.md and volume-attributes.md).
C1B_EXEMPTION = ("C1b ignores a new page under content/includes/ that a variant page references with an include "
                 "shortcode, and a new section _index.md whose folder holds another page (author, October 19)")
INCLUDE_REF = re.compile(r"\{\{[%<]\s*include\s+\"([^\"]+)\"")
_tree_pages = {"by_id": {}}
_tree_graph = rr.tree_graph


def tree_graph_recording(gm, tree, scope=wr.dcv.DEFAULT_SCOPE):
    out = _tree_graph(gm, tree, scope)
    _tree_pages["by_id"] = {p.doc_id: p for p in out[0]}
    return out


rr.tree_graph = tree_graph_recording


def templated_exempt(rel, rels, texts):
    """True if a new page with no Markdown in-link is reached through Hugo templating (C1B_EXEMPTION)."""
    if rel.startswith("content/includes/"):
        name = rel[len("content/includes/"):]
        refs = {m for t in texts for m in INCLUDE_REF.findall(t)}
        return name in refs or (name.endswith(".md") and name[:-3] in refs)
    if rel.endswith("/_index.md"):
        folder = rel[: -len("_index.md")]
        return any(r != rel and r.startswith(folder) for r in rels)
    return False


def c_admission_c1b(manifest, gm, base_page_list, variant_nodes, variant_edges, mapping):
    out = c_admission_fixed(manifest, gm, base_page_list, variant_nodes, variant_edges, mapping)
    res = out["result"]
    if "C1b-new-page-without-in-link" not in res["reasons"]:
        return out
    pages = _tree_pages["by_id"]
    st = rr.load_struct()
    ns = st.canonical_nodes(variant_nodes)
    es = st.canonical_edges(ns, variant_edges)
    new = sorted(set(ns) - {p.doc_id for p in base_page_list})
    has_in = {t for _, t in es}
    orphans = [v for v in new if v not in has_in]
    if not orphans or any(v not in pages for v in orphans):
        return out
    rels = {p.rel_path for p in pages.values()}
    texts = [p.text for p in pages.values()]
    exempt = [pages[v].rel_path for v in orphans if templated_exempt(pages[v].rel_path, rels, texts)]
    res = dict(res, c1b_exemption=C1B_EXEMPTION, c1b_exempted=exempt, c1b_orphans_all=len(orphans))
    if len(exempt) == len(orphans):
        res["reasons"] = [r for r in res["reasons"] if r != "C1b-new-page-without-in-link"]
        res["passed"] = not res["reasons"]
    return dict(out, result=res, c_fix=(out.get("c_fix") or "") + "+wc_refine_17.C1B_EXEMPTION")


rr.c_admission = c_admission_c1b


_ledger_state = wr.ledger_state
wr.ledger_state = lambda rows, arms=wr.EDIT_ARMS: _ledger_state(effective(rows), arms)  # next_item and run_queue too


def infrastructure_suspect(row):
    """A failed session in which the editor never worked: no transcript (Docker network or start error) or 0 tokens
    (e.g. the Codex login was refused)."""
    ns = RUN_ROOT / row["namespace"]
    if row.get("status") != "failed":
        return False
    if not (ns / "host" / "transcript.jsonl").exists():
        return True
    state = rr.read_json(ns / "host" / "run-state.json")
    return not state.get("editor_tokens")


def auth_refused(row):
    state_path = RUN_ROOT / row["namespace"] / "host" / "run-state.json"
    errors = rr.read_json(state_path).get("editor_errors") or [] if state_path.exists() else []
    return any("401" in str(e) or "unauthorized" in str(e).lower() for e in errors)


# ------------------------------------------------------------------ runtime fix: keep the Codex login fresh
# Found October 18 on R0 A19 (R0-r03): every session gets a fresh copy of the host auth.json; Codex refreshes the token
# inside the session's tmpfs and that refresh is discarded, so once the October 8 access token expired (October 18,
# 10:21 UTC) the stored refresh token was eventually refused (401, 0 tokens). Fix: after each Codex session, the
# container's /codex-home/auth.json is copied back over the host file when it is valid JSON with tokens and a newer
# last_refresh. The bytes are never printed or logged; one backup stays beside the host file (outside the workspace).
def _refresh_back(container, auth_path):
    try:
        got = sfb.rr.load_sandbox().editor_container.docker("exec", container.editor, "cat", "/codex-home/auth.json",
                                                             check=False)
        data = got.stdout if isinstance(got.stdout, (bytes, bytearray)) else str(got.stdout).encode("utf-8")
        new, old = json.loads(data), json.loads(Path(auth_path).read_bytes())
        if not new.get("tokens") or (new.get("last_refresh") or "") <= (old.get("last_refresh") or ""):
            return "unchanged"
        bak = Path(auth_path).with_suffix(".json.bak")
        bak.write_bytes(Path(auth_path).read_bytes())
        tmp = Path(auth_path).with_suffix(".json.tmp")
        tmp.write_bytes(data)
        import os
        os.replace(tmp, auth_path)
        return "refreshed"
    except Exception as error:  # never fatal; the next session reports a refused login if it matters
        return f"not-copied:{type(error).__name__}"


def _patch_codex_driver():
    es = rr.load_sandbox()
    if getattr(es.CodexDriver, "_wc17_refresh", False):
        return
    original = es.CodexDriver.execute

    def execute(self, container, on_line, wall_seconds, stop):
        try:
            return original(self, container, on_line, wall_seconds, stop)
        finally:
            print(f"AUTH-WRITEBACK {_refresh_back(container, self.auth_path)}", flush=True)

    es.CodexDriver.execute = execute
    es.CodexDriver._wc17_refresh = True


# ------------------------------------------------------------------ gates

def prereg_check():
    """The pre-registration lock and pins; the addendum to docs/wc-freeze-16.md is accepted only as an exact suffix."""
    data = wf.check_lock(wf.PREREG)
    bad, notes = [], {}
    for p, h in data["documents"].items():
        if sha(CONTEXT / p) == h:
            continue
        if p == ADDENDUM_DOC:
            pinned = subprocess.run(["git", "-C", str(CONTEXT), "show", f"{PREREG_COMMIT}:{p}"], capture_output=True,
                                    check=True).stdout
            current = (CONTEXT / p).read_bytes()
            if hashlib.sha256(pinned).hexdigest() == h and current.startswith(pinned):
                notes[p] = {"pinned_sha256": h, "pinned_bytes": len(pinned), "current_sha256": sha(CONTEXT / p),
                            "current_bytes": len(current), "change": "append-only author addendum, October 18"}
                continue
        bad.append(p)
    bad += [p for p, h in data["code"].items() if sha(CONTEXT / p) != h and p != "scripts/wc_freeze_16.py"]
    bad += [p for p, h in data["evidence"].items() if sha(CONTEXT / p) != h]
    if bad:
        raise Refused("preregistration-pins-changed:" + ",".join(bad))
    return {"preregistration_sha256": sha(wf.PREREG), "accepted_appends": notes}


def check(_args):
    out = prereg_check()
    wf.check_lock(wf.SURVEY_FREEZE)
    wf.check_lock(wf.TIER2_FREEZE)
    if TIER1.with_suffix(".sha256").exists():
        wf.check_lock(TIER1)
    if REFINE_PLAN.exists():
        wr.load_run_plan(REFINE_PLAN)
        out["refine_plan_sha256"] = sha(REFINE_PLAN)
    print(json.dumps(dict(out, survey_freeze="ok", tier2_freeze="ok", tier1="ok"), indent=1))


# ------------------------------------------------------------------ freeze

def live_extra():
    t2 = wf.TIER2_FREEZE
    return {"cost_admission": {"authorization": "explicit author yes in chat, recorded in host-private/authorization.json "
                                                "(point 2, Sprint 17)",
                               "usd_cap": 100, "editor_billing": "ChatGPT subscription usage (no USD)"},
            "evaluation": {"question_set": {"path": str(TIER1), "sha256": sha(TIER1),
                                            "role": "tier 1 (15 tasks, author-reviewed HR-19); tier 2 is "
                                                    f"{t2.name} ({sha(t2)})"},
                           "answer_conditions": "design D7: tier 1, 1 or 6 searches x follow on/off x 2 replicates; "
                                                "tier 2, 1 search x follow on/off x 1 replicate; variants O, S, LT, R0",
                           "grading": "pre-registered (D8, D10, D11, a04c70c5); Sprint 17 computes admission only"}}


def v2_base(arm):
    text = BASE_PROMPTS[arm].read_text(encoding="utf-8")
    if text.count(STALE_LIMITS) != 1:
        raise Refused(f"stale-limits-sentence-count:{arm}")
    return text.replace(STALE_LIMITS, "").encode("utf-8")


def v2_prompt(arm, kind, area, paths, trunk):
    text = wr.session_prompt(v2_base(arm), arm, kind, area, paths, trunk if arm == "LT" else None).decode("utf-8")
    if arm == "R0":
        for old, new in R0_WORDING:
            text = text.replace(old, new)
        if "target structure" in text:
            raise Refused("r0-prompt-still-names-a-target-structure")
    if "60 minutes" in text or "150 page paths" in text:
        raise Refused("stale-limits-in-composed-prompt")
    return text.encode("utf-8")


def session_specs(freeze, version=2):
    """{key: (arm, kind, area dict, prompt bytes, None)} for every pass, resume and reconcile session of both arms."""
    prefixes, trunk, order = freeze["area_prefixes"], freeze["trunk_inventory"], freeze["order"]
    base = {arm: p.read_bytes() for arm, p in BASE_PROMPTS.items()}
    items = wr.planned_items(order)
    items += [{"arm": i["arm"], "kind": "resume", "area": i["area"]} for i in items if i["kind"] == "pass"]
    specs = {}
    for it in items:
        arm, kind, area = it["arm"], it["kind"], it["area"]
        paths = prefixes.get(area, [])
        prompt = (v2_prompt(arm, kind, area, paths, trunk) if version == 2 else
                  wr.session_prompt(base[arm], arm, kind, area, paths, trunk if arm == "LT" else None))
        specs[wr.manifest_key(arm, kind, area)] = (arm, kind, {"area_id": area, "paths": paths}, prompt, None)
    return specs


def freeze(_args):
    """Plan v1 (superseded; kept for the record): refuses now that v1 exists."""
    REFINE_PLAN, session_specs_v = REFINE_PLAN_V1, (lambda f: session_specs(f, version=1))  # noqa: N806
    pre = prereg_check()
    if REFINE_PLAN.exists():
        raise Refused("refine-plan-already-frozen")
    if any((RUN_ROOT / f"{arm}-r01").exists() for arm in wr.EDIT_ARMS) or wr.ledger_path(RUN_ROOT).exists():
        raise Refused("an-edit-namespace-or-ledger-exists")
    survey = wf.check_lock(wf.SURVEY_FREEZE)
    if survey["order"] != wr.area_order(list(survey["areas"])):
        raise Refused("survey-order-differs-from-seeded-order")
    specs = session_specs_v(survey)
    survey_key = wf.SURVEY_KEY
    survey_prompt = (RUN_ROOT / "prompts" / f"{survey_key}.txt", f"{survey_key}-v1")
    original = wr.session_manifest
    wr.session_manifest = lambda *a, **k: original(*a, **dict(k, extra=live_extra()))
    try:
        manifests = wr.write_session_files(RUN_ROOT, rr.load_sandbox(), run_id=RUN_ID, fixture=False, sessions=specs,
                                           base_binding_prompts={wr.SURVEY_ARM: survey_prompt})
    finally:
        wr.session_manifest = original
    for key, path in manifests.items():
        rr.validate_manifest(rr.read_json(path), live=True)
    # controls: O (baseline copy) and S (sham replay) under the frozen survey manifest, as in the Sprint 15 fixture
    survey_manifest = Path(rr.read_json(wf.SURVEY_PLAN)["manifests"][survey_key]["path"])
    controls = {}
    for arm in ("O", "S"):
        if not (RUN_ROOT / f"{arm}-r01").exists():
            rr.launch(survey_manifest, arm, 1)
        st = rr.read_json(RUN_ROOT / f"{arm}-r01/host/run-state.json")
        controls[arm] = {"namespace": f"{arm}-r01", "status": st["status"],
                         "baseline_copy_tree_sha256": st["baseline_copy_tree_sha256"]}
    baseline_tree = RUN_ROOT / "O-r01" / "editor" / "source"
    attempt = [a for a in rr.read_json(RUN_ROOT / "host-private/survey-attempts.json")["attempts"] if a["valid"]][0]
    wr.append_ledger(RUN_ROOT, {"event": "end", "arm": wr.SURVEY_ARM, "kind": "survey", "area": wr.RECONCILE_AREA,
                                "namespace": attempt["namespace"], "tokens": attempt["editor_tokens"] or 0,
                                "seconds": attempt["editor_wall_seconds"] or 0, "action": "survey", "valid": True})
    schemas = rr.read_json(wf.SURVEY_PLAN)["schemas"]
    queue = wr.planned_items(survey["order"])
    plan = {"run_id": RUN_ID, "kind": "refine", "created": DATE,
            "areas": survey["area_prefixes"], "area_pages": survey["page_counts"], "order": survey["order"],
            "trunk_inventory": survey["trunk_inventory"], "trunk_inventory_sha256": survey["trunk_inventory_sha256"],
            "manifests": {k: {"path": str(p), "sha256": sha(p)} for k, p in manifests.items()},
            "schemas": schemas, "budget": wr.ARM_BUDGET, "session_wall_seconds": wr.SESSION_WALL_SECONDS,
            "budget_rule": wr.BUDGET_RULE, "queue": queue,
            "resume_rule": "a pass stopped by a limit whose residual is admitted gets one resume session; a resume is "
                           "never resumed; a non-admitted residual rolls the area back (recorded unedited)",
            "retry_rule": "no retry of an editing session; an infrastructure stop halts the run and goes to the author",
            "base_prompts": {arm: {"path": sfb.rel_ctx(p), "sha256": sha(p)} for arm, p in BASE_PROMPTS.items()},
            "prompt_files": {k: {"path": sfb.rel_ctx(RUN_ROOT / "prompts" / f"{k}.txt"),
                                 "sha256": sha(RUN_ROOT / "prompts" / f"{k}.txt")} for k in manifests},
            "preregistration": {"path": wf.PREREG.relative_to(ROOT).as_posix(), **pre},
            "survey_freeze": {"path": wf.SURVEY_FREEZE.relative_to(ROOT).as_posix(), "sha256": sha(wf.SURVEY_FREEZE)},
            "tier1": {"path": TIER1.relative_to(ROOT).as_posix(), "sha256": sha(TIER1)},
            "tier2_freeze": {"path": wf.TIER2_FREEZE.relative_to(ROOT).as_posix(), "sha256": sha(wf.TIER2_FREEZE)},
            "controls": controls, "baseline_tree_id": wr.tree_id(baseline_tree),
            "schedule": {"checkpoint_seconds": CHECKPOINT_SECONDS, "checkpoint_rule": CHECKPOINT_RULE,
                         "projection_areas": PROJECTION_AREAS, "projection_rule": PROJECTION_RULE},
            "ledger_seed": "the WS survey end row (tokens and editor seconds count to LT, per the budget rule)"}
    write_json(REFINE_PLAN, plan)
    wf.lock(REFINE_PLAN)
    wr.load_run_plan(REFINE_PLAN)
    print(json.dumps({"refine_plan_sha256": sha(REFINE_PLAN), "manifests": len(manifests), "queue": len(queue),
                      "controls": controls, "base_prompts": plan["base_prompts"]}, indent=1))


def freeze_v2(_args):
    """Plan v2: the v2 composition; supersedes v1 before any editing session. Reuses the O and S controls and the
    ledger seed; moves v1's LT/R0 prompt, binding and manifest files aside as *.v1-superseded.*."""
    pre = prereg_check()
    if REFINE_PLAN.exists():
        raise Refused("refine-plan-v2-already-frozen")
    v1 = wf.check_lock(REFINE_PLAN_V1)  # v1's own lock; its session files are moved aside below (rerunnable)
    rows = ledger_rows()
    if any(r["event"] == "launch" for r in rows) or any((RUN_ROOT / f"{a}-r01").exists() for a in wr.EDIT_ARMS):
        raise Refused("an-editing-session-has-run")
    survey = wf.check_lock(wf.SURVEY_FREEZE)
    for key in v1["manifests"]:
        for sub, ext in (("prompts", ".txt"), ("bindings", ".json"), ("manifests", ".json")):
            f = RUN_ROOT / sub / f"{key}{ext}"
            if f.exists():
                f.rename(f.with_name(f"{key}.v1-superseded{ext}"))
    specs = session_specs(survey, version=2)
    survey_prompt = (RUN_ROOT / "prompts" / f"{wf.SURVEY_KEY}.txt", f"{wf.SURVEY_KEY}-v1")
    original = wr.session_manifest
    wr.session_manifest = lambda *a, **k: original(*a, **dict(k, extra=live_extra()))
    try:
        manifests = wr.write_session_files(RUN_ROOT, rr.load_sandbox(), run_id=RUN_ID, fixture=False, sessions=specs,
                                           base_binding_prompts={wr.SURVEY_ARM: survey_prompt})
    finally:
        wr.session_manifest = original
    for path in manifests.values():
        rr.validate_manifest(rr.read_json(path), live=True)
    plan = dict(v1, created=DATE, preregistration={"path": wf.PREREG.relative_to(ROOT).as_posix(), **pre},
                manifests={k: {"path": str(p), "sha256": sha(p)} for k, p in manifests.items()},
                prompt_files={k: {"path": sfb.rel_ctx(RUN_ROOT / "prompts" / f"{k}.txt"),
                                  "sha256": sha(RUN_ROOT / "prompts" / f"{k}.txt")} for k in manifests},
                composition={"version": 2, "stale_limits_removed": sfb.V1_LIMITS, "r0_wording": R0_WORDING,
                             "decided": "author, October 18, before authorization 2 (no editing session had run)"},
                supersedes={"path": REFINE_PLAN_V1.relative_to(ROOT).as_posix(), "sha256": sha(REFINE_PLAN_V1),
                            "reason": "v1 prompts kept the base prompts' 60-minute / 150-path sentence beside the "
                                      "90-minute sentence, and told R0 to meet a target structure it is never given"})
    write_json(REFINE_PLAN, plan)
    wf.lock(REFINE_PLAN)
    wr.load_run_plan(REFINE_PLAN)
    print(json.dumps({"refine_plan_v2_sha256": sha(REFINE_PLAN), "manifests": len(manifests)}, indent=1))


# ------------------------------------------------------------------ schedule rules (pure)

def first_edit_launch(rows):
    rows = effective(rows)
    times = [r["time"] for r in rows if r["event"] == "launch" and r["arm"] in wr.EDIT_ARMS]
    return min(times) if times else None


def decision(root=RUN_ROOT):
    path = Path(root) / "host-private" / "checkpoint-decision.json"
    return rr.read_json(path) if path.exists() else None


def checkpoint_due(rows, now, dec, limit=CHECKPOINT_SECONDS):
    """True when the 48 h checkpoint blocks the next session (no author decision to continue)."""
    t0 = first_edit_launch(rows)
    if t0 is None or now - t0 < limit:
        return False
    return not (dec and dec.get("decision") == "continue")


def area_done(rows, area):
    """Both arms' pass ended and no resume pending for the area."""
    rows = effective(rows)
    st = wr.ledger_state(rows)
    ended = {(r["arm"], r["kind"]) for r in rows if r["event"] in ("end", "skip") and r.get("area") == area}
    if not all((arm, "pass") in ended for arm in wr.EDIT_ARMS):
        return False
    return not any(a == area for _, a in st["pending_resumes"])


def summary(rows, plan):
    """Per area and per arm: sessions, tokens, seconds, actions."""
    rows = effective(rows)
    per_area, per_arm = {}, {arm: {"sessions": 0, "tokens": 0, "seconds": 0.0, "advance": 0, "rollback": 0,
                                   "noop": 0, "resumes": 0, "skips": 0} for arm in wr.EDIT_ARMS}
    for r in rows:
        if r.get("arm") not in wr.EDIT_ARMS:
            continue
        a = per_area.setdefault(r["area"], {}).setdefault(r["arm"], [])
        if r["event"] == "end":
            a.append({k: r.get(k) for k in ("kind", "namespace", "action", "status", "truncated", "tokens", "seconds",
                                            "not_admitted_because", "resume_due")})
            s = per_arm[r["arm"]]
            s["sessions"] += 1
            s["tokens"] += int(r.get("tokens") or 0)
            s["seconds"] += float(r.get("seconds") or 0)
            s[r["action"]] = s.get(r["action"], 0) + 1
            s["resumes"] += r["kind"] == "resume"
        elif r["event"] == "skip":
            a.append({"kind": r["kind"], "skipped": r.get("reason")})
            per_arm[r["arm"]]["skips"] += 1
    survey_tokens = sum(int(r.get("tokens") or 0) for r in rows if r.get("arm") == wr.SURVEY_ARM and r["event"] == "end")
    done = [a for a in plan["order"] if area_done(rows, a)]
    return {"areas_done_both_arms": done, "per_arm": per_arm, "survey_tokens_counted_to_LT": survey_tokens,
            "per_area": per_area, "halted": wr.ledger_state(rows)["halted"]}


def projection(rows, plan, n=PROJECTION_AREAS):
    rows = effective(rows)
    order, pages = plan["order"], plan["area_pages"]
    first = order[:n]
    if not all(area_done(rows, a) for a in first):
        return None
    out = {"basis_areas": first, "basis_pages": sum(pages[a] for a in first), "per_arm": {}}
    remaining = [a for a in order if not area_done(rows, a)]
    total = 0.0
    for arm in wr.EDIT_ARMS:
        secs = sum(float(r.get("seconds") or 0) for r in rows
                   if r["event"] == "end" and r["arm"] == arm and r["area"] in first)
        rate = secs / out["basis_pages"]
        spent = sum(float(r.get("seconds") or 0) for r in rows if r["event"] == "end" and r["arm"] == arm)
        rest = rate * sum(pages[a] for a in remaining)
        recon = wr.SESSION_WALL_SECONDS
        out["per_arm"][arm] = {"seconds_per_page": round(rate, 1), "spent_seconds": round(spent),
                               "remaining_area_seconds": round(rest), "reconcile_ceiling_seconds": recon,
                               "projected_total_hours": round((spent + rest + recon) / 3600, 1)}
        total += spent + rest + recon
    out["projected_total_wall_hours_both_arms"] = round(total / 3600, 1)
    out["remaining_areas"] = len(remaining)
    out["rule"] = PROJECTION_RULE
    return out


# ------------------------------------------------------------------ run

def run_ctx(plan):
    vm = rr.read_json(wr.V6)
    gm = wr.dcv.pinned(vm)
    baseline_tree = RUN_ROOT / "O-r01" / "editor" / "source"
    if wr.tree_id(baseline_tree) != plan["baseline_tree_id"]:
        raise Refused("baseline-tree-differs-from-plan")
    return {"root": RUN_ROOT, "gm": gm, "vm": vm, "base": sfb.Baseline(gm, vm),
            "schemas": rr.read_json(ROOT / plan["schemas"]["path"]),
            "manifests": {k: Path(m["path"]) for k, m in plan["manifests"].items()}, "areas": plan["areas"],
            "baseline_id": plan["baseline_tree_id"], "baseline_tree": baseline_tree, "budget": plan["budget"]}


def run(args):
    plan = wr.load_run_plan(REFINE_PLAN)
    prereg_check()
    wr.authorized(RUN_ROOT, 2)
    ctx = run_ctx(plan)
    _patch_codex_driver()
    items = wr.planned_items(plan["order"])
    proj_path = RUN_ROOT / "host-private" / "projection.json"
    ran, infra_streak = 0, 0
    while args.max is None or ran < args.max:
        rows = ledger_rows()
        if checkpoint_due(rows, time.time(), decision()):
            report = {"checkpoint": "48h", "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "summary": summary(rows, plan), "projection": projection(rows, plan)}
            write_json(RUN_ROOT / "host-private" / "checkpoint-48h.json", report)
            print("CHECKPOINT-48H: paused; the author decides (wc_refine_17.py decide)", flush=True)
            return
        item, _ = wr.next_item(rows, items, plan["budget"])
        if item is None:
            print("QUEUE-COMPLETE", flush=True)
            return
        wr.run_queue(ctx, items, live=True, log=lambda s: print(s, flush=True), max_sessions=1)
        ran += 1
        rows = ledger_rows()
        last = [r for r in rows if r["event"] == "end"][-1]
        if infrastructure_suspect(last) and auth_refused(last):
            seqs = [r["seq"] for r in rows if r.get("namespace") == last["namespace"] and r["event"] in ("launch", "end")]
            wr.append_ledger(RUN_ROOT, {"event": "void", "seqs": seqs, "reason": f"auto: Codex login refused "
                                        f"(401, 0 tokens) in {last['namespace']}", "author_quote": "If you come across "
                                        "implementation errors we didn't predict it's fine to work through them"})
            wr.append_ledger(RUN_ROOT, {"event": "halt", "arm": last["arm"], "kind": last["kind"], "area": last["area"],
                                        "reason": "codex-login-refused: the author signs in again, then the halt is voided"})
            print(f"HALT codex-login-refused:{last['namespace']}", flush=True)
            return
        if infrastructure_suspect(last):
            # author, October 18: unpredicted infrastructure faults are fixed and rerun, not halted. The attempt never
            # reached the editor (no transcript), so it is voided and the same item runs again; 3 in a row halts.
            infra_streak += 1
            seqs = [r["seq"] for r in rows if r.get("namespace") == last["namespace"] and r["event"] in ("launch", "end")]
            wr.append_ledger(RUN_ROOT, {"event": "void", "seqs": seqs, "reason": f"auto: infrastructure attempt "
                                        f"{last['namespace']} (failed, no editor transcript)",
                                        "author_quote": "If you come across implementation errors we didn't predict "
                                                        "it's fine to work through them and fix them and start a run."})
            print(f"VOIDED infrastructure attempt {last['namespace']} (streak {infra_streak})", flush=True)
            if infra_streak >= 3:
                wr.append_ledger(RUN_ROOT, {"event": "halt", "arm": last["arm"], "kind": last["kind"],
                                            "area": last["area"], "reason": "3 infrastructure attempts in a row"})
                print("HALT infrastructure: 3 attempts in a row", flush=True)
                return
            ran -= 1
            time.sleep(60)
            continue
        infra_streak = 0
        if not proj_path.exists():
            p = projection(rows, plan)
            if p is not None:
                write_json(proj_path, p)
                print("PROJECTION-READY " + json.dumps(p), flush=True)
                if args.stop_at_projection:
                    return


def authorize(args):
    """Append the author's yes for point 2 (wc_freeze_16.authorize writes the file only once); earlier entries are kept."""
    import os
    path = RUN_ROOT / "host-private" / "authorization.json"
    data = rr.read_json(path)
    if any(e.get("point") == 2 for e in data["entries"]):
        raise Refused("authorization-point-2-already-recorded")
    before = [json.dumps(e, sort_keys=True) for e in data["entries"]]
    data["entries"].append({"point": 2, "author_yes": True, "quote": args.quote,
                            "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "preregistration_sha256": sha(wf.PREREG), "plan_sha256": sha(REFINE_PLAN),
                            "scope": args.scope})
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    if [json.dumps(e, sort_keys=True) for e in rr.read_json(tmp)["entries"][:-1]] != before:
        raise Refused("authorization-rewrite-changed-earlier-entries")
    os.replace(tmp, path)
    print(json.dumps(wr.authorized(RUN_ROOT, 2), indent=1))


def void(args):
    """Author-approved: mark ledger rows (an infrastructure attempt and its halt) as void; files are kept as evidence."""
    rows = ledger_rows()
    seqs = sorted(args.seq)
    known = {r["seq"]: r for r in rows}
    if any(q not in known or known[q]["event"] not in ("launch", "end", "halt") for q in seqs):
        raise Refused("void-unknown-or-unvoidable-seq")
    for q in seqs:
        if known[q]["event"] == "end" and not infrastructure_suspect(known[q]):
            raise Refused(f"void-only-infrastructure-attempts:{q}")
    print(json.dumps(wr.append_ledger(RUN_ROOT, {"event": "void", "seqs": seqs, "reason": args.reason,
                                                 "author_quote": args.quote}), indent=1))


def reevaluate(args):
    """Re-run admission (with the Hugo fix) on an ended session and append an `amend` row with the corrected outcome.
    Only allowed while the arm has run no later session, so the chain stays consistent."""
    plan = wr.load_run_plan(REFINE_PLAN)
    rows = ledger_rows()
    row = next(r for r in rows if r.get("seq") == args.seq and r["event"] == "end")
    later = [r for r in effective(rows) if r["event"] == "launch" and r["arm"] == row["arm"] and r["seq"] > args.seq]
    if later:
        raise Refused("arm-has-a-later-session")
    ctx = run_ctx(plan)
    advances = [r for r in effective(rows) if r["event"] == "end" and r["arm"] == row["arm"]
                and r["seq"] < args.seq and r.get("action") == "advance"]
    start_tree = Path(advances[-1]["head_after_path"]) if advances else ctx["baseline_tree"]
    if wr.tree_id(start_tree) != row["start_id"]:
        raise Refused("reevaluate-start-tree-differs")
    area_paths = plan["areas"].get(row["area"], []) if row["area"] != wr.RECONCILE_AREA else []
    ev = wr.evaluate_session(RUN_ROOT / row["namespace"], start_tree, gm=ctx["gm"], vm=ctx["vm"], base=ctx["base"],
                             schemas=ctx["schemas"], kind=row["kind"], area_paths=area_paths)
    fields = {k: ev[k] for k in ("action", "admitted_cumulative", "not_admitted_because", "review", "resume_due",
                                 "status")}
    if ev["action"] == "advance":
        fields.update(head_after=ev["terminal_id"], head_after_path=ev["terminal_tree"], rejected_tree=None)
    print(json.dumps(wr.append_ledger(RUN_ROOT, {"event": "amend", "amends_seq": args.seq, "fields": fields,
                                                 "was": {k: row.get(k) for k in fields}, "reason": args.reason}),
                     indent=1)[:3000])


def reopen(args):
    """Give an item one fresh session from its arm's current head, when its recorded rejection came only from admission
    defects fixed later and the arm has moved on (so it cannot be amended). Its tokens and seconds still count."""
    rows = ledger_rows()
    row = next(r for r in rows if r.get("seq") == args.seq and r["event"] == "end")
    if row.get("action") != "rollback" or row["kind"] != "pass":
        raise Refused("reopen-only-a-rolled-back-pass")
    if any(r["event"] == "reopen" and r["reopens_seq"] == args.seq for r in rows):
        raise Refused("already-reopened")
    print(json.dumps(wr.append_ledger(RUN_ROOT, {"event": "reopen", "reopens_seq": args.seq, "arm": row["arm"],
                                                 "kind": row["kind"], "area": row["area"], "reason": args.reason,
                                                 "author_quote": args.quote}), indent=1))


RECORDS = EVIDENCE / f"wc-refinement-run-v1-{DATE}.json"
NEW_PAGES = ROOT / "artifacts/wc-refine-17-records/new-or-rewritten-pages.json"


def _area_of(rel, prefixes):
    return next((a for a, paths in prefixes.items() if sfb.in_area(rel, paths)), None)


def records(_args):
    """Step 5 records from the final admitted trees and the ledger: per area and per arm, quote survival (tier 1), support
    overlap with edited pages (tier 1 and tier 2), and the D11b new-or-rewritten page lists. No model calls."""
    plan = wr.load_run_plan(REFINE_PLAN)
    rows = ledger_rows()
    eff = effective(rows)
    st = wr.ledger_state(rows)
    if st["halted"] or st["open_launch"] or wr.next_item(rows, wr.planned_items(plan["order"]), plan["budget"])[0]:
        raise Refused("queue-not-complete")
    if RECORDS.exists():
        raise Refused("records-already-written")
    vm = rr.read_json(wr.V6)
    gm = wr.dcv.pinned(vm)
    base_list = rr.baseline_pages(gm, vm)
    rel_of = {p.doc_id: p.rel_path for p in base_list}
    tier1 = rr.read_json(TIER1)
    t2_ids = set(rr.read_json(wf.TIER2_FREEZE)["question_ids"])
    qrels = [json.loads(x) for x in (ROOT / "artifacts/c2-pooled-qrels/verify-full/qrels.jsonl").read_text(
        encoding="utf-8").splitlines() if x]
    t2_pages = sorted({q["support_target_id"] for q in qrels if q["relevance"] > 0 and q["query_id"] in t2_ids})
    t1_pages = sorted({e["page_id"] for t in tier1["tasks"] for e in t["core_elements"]} |
                      {d for t in tier1["tasks"] for d in t.get("support_page_ids", [])})
    elements = [{"element": f"{t['task_id']}/{e['element_id']}/q{i}", "page_id": q["page_id"], "quote": q["quote"]}
                for t in tier1["tasks"] for e in t["core_elements"]
                for i, q in enumerate(e.get("supporting_quotes") or [])]
    out = {"schema_version": "docspectra.icsc.wc_refinement_run.v1", "created": DATE, "run_id": RUN_ID,
           "refine_plan_sha256": sha(REFINE_PLAN), "preregistration_sha256": sha(wf.PREREG),
           "ledger_sha256": sha(wr.ledger_path(RUN_ROOT)), "ledger_rows": len(rows),
           "fixes": {"HUGO_FIX": HUGO_FIX, "C2_FIX": C2_FIX, "PROMPT_FIX": PROMPT_FIX, "C1B_EXEMPTION": C1B_EXEMPTION},
           "deviations": "docs/manuscript-deviations-wc-refinement.md",
           "tier1": {"tasks": len(tier1["tasks"]), "quotes": len(elements), "support_pages": len(t1_pages)},
           "tier2": {"questions": len(t2_ids), "verified_pages": len(t2_pages)},
           "arms": {}, "areas": {}}
    new_pages = {}
    for arm in wr.EDIT_ARMS:
        head = st["arms"][arm]
        vctx = wr.variant_context(gm, vm, head["head_path"], ledger_rows=eff, arm=arm)
        rec = vctx["receipt"]
        touched = set(vctx["touched"]) | set(rec["mapping"])
        ends = [r for r in eff if r["event"] == "end" and r["arm"] == arm]
        admitted_areas = {r["area"] for r in ends if r["kind"] == "pass" and r["action"] == "advance"}
        qs = wr.quote_survival(elements, vctx)
        survey = [r for r in eff if r["event"] == "end" and r["arm"] == wr.SURVEY_ARM]
        out["arms"][arm] = {
            "final_tree_id": head["head"], "final_tree_path": head["head_path"],
            "final_admission": next(r["action"] for r in reversed(ends) if r["kind"] == "reconcile"),
            "sessions": len(ends), "tokens": sum(int(r.get("tokens") or 0) for r in ends),
            "survey_tokens_counted": sum(int(r.get("tokens") or 0) for r in survey) if arm == "LT" else 0,
            "session_seconds": round(sum(float(r.get("seconds") or 0) for r in ends), 1),
            "budget": plan["budget"],
            "pages_added": len(rec["added"]), "pages_changed": len(rec["changed"]),
            "pages_removed": len(rec["removed"]), "pages_merged": len(rec["mapping"]),
            "unmapped_removals": len(rec["unmapped_removals"]), "partial_unit_loss": len(rec["partial_unit_loss"]),
            "edges_added_vs_O": len(vctx["added_edges"]),
            "edges_removed_vs_O": len(set(vctx["o_mapped"]) - set(vctx["v_edges"])),
            "areas_admitted": sorted(admitted_areas), "areas_unedited": sorted(set(plan["order"]) - admitted_areas),
            "resumes": sum(r["kind"] == "resume" for r in ends),
            "rollbacks": sum(r["action"] == "rollback" for r in ends),
            "quote_survival": {k: qs[k] for k in ("elements", "in_credited_page", "anywhere")},
            "quote_survival_lost": [r for r in qs["rows"] if not r["anywhere"]],
            "support_touched": {"tier1": {"pages": len(t1_pages), "touched": sum(d in touched for d in t1_pages)},
                                "tier2": {"pages": len(t2_pages), "touched": sum(d in touched for d in t2_pages)}}}
        new_pages[arm] = {"added": sorted(rec["added"]), "rewritten": sorted(rec["changed"])}
        for area in plan["order"]:
            a_rows = [r for r in ends if r["area"] == area]
            final = [r for r in a_rows if r["kind"] == "pass"]
            last = final[-1] if final else {}
            prefixes = {area: plan["areas"][area]}
            t1a = [d for d in t1_pages if _area_of(rel_of.get(d, ""), prefixes) == area]
            t2a = [d for d in t2_pages if _area_of(rel_of.get(d, ""), prefixes) == area]
            edited = {d for d, a in vctx["edit_area"].items() if a == area}
            review = last.get("review") or {}
            out["areas"].setdefault(area, {"baseline_pages": plan["area_pages"][area]})[arm] = {
                "sessions": len(a_rows), "tokens": sum(int(r.get("tokens") or 0) for r in a_rows),
                "seconds": round(sum(float(r.get("seconds") or 0) for r in a_rows), 1),
                "admission": last.get("action"), "not_admitted_because": last.get("not_admitted_because"),
                "superseded_attempts": sum(r["kind"].endswith("-superseded") for r in a_rows),
                "rollbacks": sum(r["action"] == "rollback" for r in a_rows),
                "resumes": sum(r["kind"] == "resume" for r in a_rows),
                "pass_diff": {k: len(v) for k, v in (last.get("pass_diff") or {}).items()},
                "edges_added_raw": review.get("edges_added_raw"), "edges_removed_raw": review.get("edges_removed_raw"),
                "pages_edited_attributed": len(edited),
                "support_overlap": {"tier1": {"pages": len(t1a), "touched": sum(d in touched for d in t1a)},
                                    "tier2": {"pages": len(t2a), "touched": sum(d in touched for d in t2a)}}}
    NEW_PAGES.parent.mkdir(parents=True, exist_ok=True)
    NEW_PAGES.write_text(json.dumps(new_pages, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
    out["new_or_rewritten_pages"] = {"path": NEW_PAGES.relative_to(ROOT).as_posix(), "sha256": sha(NEW_PAGES),
                                     "counts": {a: {k: len(v) for k, v in d.items()} for a, d in new_pages.items()},
                                     "role": "D11b: Sprint 19 pool completion judges these pages directly"}
    write_json(RECORDS, out)
    wf.lock(RECORDS)
    skip = ("quote_survival_lost", "areas_admitted", "areas_unedited", "final_tree_path", "budget")
    print(json.dumps({"records_sha256": sha(RECORDS),
                      "arms": {a: {k: v for k, v in d.items() if k not in skip} for a, d in out["arms"].items()}},
                     indent=1))


def decide(args):
    """The author's checkpoint decision; "stop" records every unstarted item as skipped (unedited) in both arms."""
    path = RUN_ROOT / "host-private" / "checkpoint-decision.json"
    if args.decision == "stop":
        plan = wr.load_run_plan(REFINE_PLAN)
        items = wr.planned_items(plan["order"])
        while True:
            item, skips = wr.next_item(ledger_rows(), items, plan["budget"])
            for s in skips:
                wr.append_ledger(RUN_ROOT, dict(s, event="skip"))
            if item is None:
                break
            wr.append_ledger(RUN_ROOT, dict(item, event="skip", reason="author-stop-at-48h-checkpoint"))
    write_json(path, {"decision": args.decision, "quote": args.quote,
                      "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "refine_plan_sha256": sha(REFINE_PLAN)})
    print(path)


def status(_args):
    plan = rr.read_json(REFINE_PLAN)
    rows = ledger_rows()
    out = summary(rows, plan)
    if not _args.full:
        out.pop("per_area")
    out["projection"] = projection(rows, plan)
    t0 = first_edit_launch(rows)
    out["hours_since_first_edit_launch"] = round((time.time() - t0) / 3600, 2) if t0 else None
    print(json.dumps(out, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="stage", required=True)
    sub.add_parser("check").set_defaults(fn=check)
    sub.add_parser("freeze").set_defaults(fn=freeze)
    sub.add_parser("freeze-v2").set_defaults(fn=freeze_v2)
    r = sub.add_parser("run")
    r.add_argument("--max", type=int, default=None, help="at most this many sessions in this invocation")
    r.add_argument("--stop-at-projection", action="store_true")
    r.set_defaults(fn=run)
    a = sub.add_parser("authorize")
    a.add_argument("--quote", required=True)
    a.add_argument("--scope", required=True)
    a.set_defaults(fn=authorize)
    e = sub.add_parser("reevaluate")
    e.add_argument("--seq", type=int, required=True)
    e.add_argument("--reason", required=True)
    e.set_defaults(fn=reevaluate)
    o = sub.add_parser("reopen")
    o.add_argument("--seq", type=int, required=True)
    o.add_argument("--reason", required=True)
    o.add_argument("--quote", required=True)
    o.set_defaults(fn=reopen)
    sub.add_parser("records").set_defaults(fn=records)
    v = sub.add_parser("void")
    v.add_argument("--seq", type=int, nargs="+", required=True)
    v.add_argument("--reason", required=True)
    v.add_argument("--quote", required=True)
    v.set_defaults(fn=void)
    d = sub.add_parser("decide")
    d.add_argument("--decision", choices=["continue", "stop"], required=True)
    d.add_argument("--quote", required=True)
    d.set_defaults(fn=decide)
    s = sub.add_parser("status")
    s.add_argument("--full", action="store_true")
    s.set_defaults(fn=status)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

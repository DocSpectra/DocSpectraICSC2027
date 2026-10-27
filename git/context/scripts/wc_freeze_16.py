"""Sprint 16: pre-registration, survey run plan, freezes of the survey outputs and the tier-2 set.

Thin driver over scripts/wc_refinement_15.py (unchanged). Every value in the pre-registration is computed from the
files and constants it names, not restated. Stages:

  prereg          write evidence/wc-refinement-preregistration-v1-2026-10-14.json (+ .sha256 lock), the record
                  schemas, the live survey session files and the survey run plan (+ lock). No model calls.
  check           verify the pre-registration lock, the run-plan lock and every hash they pin.
  authorize       append the author's "yes" for authorization point 1 to host-private/authorization.json.
  freeze-survey   after the survey: freeze the area partition, area order and trunk inventory by hash.
  freeze-tier2    freeze the 111-question tier-2 set, the C2 qrels identity and the duplicate clusters by hash.
The survey itself runs through `python wc_refinement_15.py survey --plan <run plan>`.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wc_refinement_15 as wr  # noqa: E402

rr, sfb, sha, write_json, Refused = wr.rr, wr.sfb, wr.sha, wr.write_json, wr.Refused
ROOT, CONTEXT = rr.ROOT, rr.CONTEXT
DATE = "2026-10-14"
RUN_ID = "wc-v1"
RUN_ROOT = sfb.sf_run_root(RUN_ID)
EVIDENCE = CONTEXT / "evidence"
PREREG = EVIDENCE / f"wc-refinement-preregistration-v1-{DATE}.json"
SURVEY_PLAN_V1 = EVIDENCE / f"wc-run-plan-survey-v1-{DATE}.json"   # superseded: live manifest lacked cost/evaluation fields
SURVEY_PLAN = EVIDENCE / f"wc-run-plan-survey-v2-{DATE}.json"
SURVEY_FREEZE = EVIDENCE / f"wc-survey-freeze-v1-{DATE}.json"
TIER2_FREEZE = EVIDENCE / f"wc-tier2-freeze-v1-{DATE}.json"
RA = ROOT / "git/RetrievalArena"
C2 = ROOT / "artifacts/c2-pooled-qrels"
SURVEY_KEY = wr.manifest_key(wr.SURVEY_ARM, "survey", "ALL")

CODE = ["scripts/wc_refinement_15.py", "scripts/wc_freeze_16.py", "scripts/structure_forcing_12.py",
        "scripts/structure_forcing_12b.py", "scripts/lt_locality_13.py", "scripts/wc_ragas_15b.py",
        "scripts/wc_ragas_c3.py", "scripts/c2_pooled_qrels.py", "sandbox/editor_session.py",
        "prompts/docker-editor-R0-v4.txt"]
DOCS = ["design/whole-corpus-refinement-experiment-v1-2026-10-09.md", "design/whole-corpus-leaf-and-trunk-target-2026-10-09.md",
        "docs/wc-freeze-16.md", "docs/wc-review-value-map.md", "docs/wc-ragas-15b.md",
        "docs/wc-refinement-infrastructure-15.md", "docs/support-labels-literature-2026-10-11.md",
        "design/cross-corpus-complex-task-generation-v1-2026-09-28.md"]
EVIDENCE_PINS = ["evidence/wc-refinement-15-retriever-pins-v1-2026-10-10.json",
                 "evidence/wc-refinement-15-fixture-v1-2026-10-10.json",
                 "evidence/c2-pooled-qrels-docker-tier2-2026-10-12.json",
                 "evidence/wc-ragas-judge-qualification-15b-2026-10-11.json",
                 "evidence/wc-ragas-judge-qualification-c3-2026-10-12.json",
                 "evidence/restructuring-pilot-11b-questions-2026-10-08.json",
                 "evidence/cross-corpus-complex-task-panel-v1-2026-09-28.json",
                 "evidence/docker-corpus-variant-input-v6-docs-scope-2026-10-07.json"]


def git(repo, *argv):
    import subprocess
    return subprocess.run(["git", "-C", str(repo), *argv], capture_output=True, text=True, check=True).stdout.strip()


def hashes(paths, base=CONTEXT):
    return {p: sha(base / p) for p in paths}


def lock(path):
    Path(path).with_suffix(".sha256").write_text(f"{sha(path)}  {Path(path).name}\n", encoding="ascii")


def check_lock(path):
    lockp = Path(path).with_suffix(".sha256")
    if not Path(path).exists() or not lockp.exists():
        raise Refused(f"not-frozen:{Path(path).name}")
    if sha(path) != lockp.read_text(encoding="ascii").split()[0]:
        raise Refused(f"lock-mismatch:{Path(path).name}")
    return rr.read_json(path)


def preregistration():
    c2 = rr.read_json(EVIDENCE / "c2-pooled-qrels-docker-tier2-2026-10-12.json")
    c3 = rr.read_json(EVIDENCE / "wc-ragas-judge-qualification-c3-2026-10-12.json")
    ragas_cfg = ROOT / "artifacts/wc-ragas-c3/inputs/controls-response/config.json"
    provenance = dict(wr.PROVENANCE_RULE, status="confirmed by the author October 14 (docs/wc-freeze-16.md); a sensitivity "
                                                   "analysis, direct judgment primary (D11b)")
    return {
        "schema_version": "docspectra.icsc.wc_preregistration.v1", "created": DATE, "run_id": RUN_ID,
        "status": "frozen before any Sprint 16 model call; the survey and tier-1 generation follow authorization 1",
        "documents": hashes(DOCS), "code": hashes(CODE), "evidence": hashes(EVIDENCE_PINS),
        "retrieval_arena": {"commit": git(RA, "rev-parse", "HEAD"), "tree": git(RA, "rev-parse", "HEAD^{tree}"),
                            "tag": "ra-consolidation-2026-10", "origin_main_merge": "c9ac77a (PR DocSpectra/RetrievalArena#1)"},
        "editor": {"model": "gpt-6.1-sol, reasoning effort medium, Codex CLI 0.161.0 in the qualified container",
                   "session_wall_seconds": wr.SESSION_WALL_SECONDS, "live_limits": wr.LIVE_LIMITS,
                   "arm_budget": wr.ARM_BUDGET, "budget_rule": wr.BUDGET_RULE, "area_bounds": list(wr.LIVE_AREA_BOUNDS),
                   "area_order_seed_label": wr.ORDER_SEED_LABEL, "arms": list(wr.EDIT_ARMS),
                   "survey_arm": wr.SURVEY_ARM, "survey_prompt_sha256": wr.sha_text(wr.survey_prompt().decode("utf-8"))},
        "primary_test": {
            "id": "D8 sign-flip", "statistic": "per area: z of magnetic_lambda1_q0.25 against the LN null on the area "
                                               "subgraph view (pooled null SD as in wc_refinement_15), LT pass value minus R0 pass value",
            "measure_key": "magnetic_lambda1_q0.25", "view": "per-area subgraph", "null": "LN, the frozen Sprint 13 locality rule; "
            "buckets it cannot compute are reported as not computable and the area is dropped from the test, never imputed",
            "reference": "exact sign-flip permutation over areas of the mean difference (wc_refinement_15.sign_flip_test)",
            "sidedness": "two-sided (author, October 14)", "threshold": "p < 0.05",
            "minimum_attainable_p": "2 / 2^n for n areas with a computable difference",
            "dependence_disclosure": "areas are chained on one tree and share trunk pages, so they are not independent; "
                                     "the result is 'pre-registered within an exploratory run'",
            "everything_else": "descriptive"},
        "nulls": {"salts": wr.SALTS, "per_type_per_arm": wr.NULLS_PER_TYPE, "types": {"LN": "primary comparator",
                  "PC": "page-creation matched", "RN": "continuity only"}, "views": ["full (LWCC, 09c pin, merge mapping)", "per-area subgraph"]},
        "tier1": {"tasks": "15: the 5 existing 11b tasks, the 2 unselected Docker panel candidates, and 8 new tasks generated "
                           "after the survey from areas the existing 7 do not cover (D6a/D6b)",
                  "review": "the author reviews every core element before Sprint 17 (HR-19); until then they are proposals",
                  "answers": "4 conditions (1 or 6 searches x follow on/off) x 2 replicates on O, S, LT, R0 = 480",
                  "primary_score": "Luna scorer v2, k = 2 passes", "secondary_score": "boolean rubric judge (qwen3:14b, thinking off, relaxed quote match)",
                  "ragas": "none on tier 1 (FactualCorrectness omitted, author October 14)",
                  "quote_survival": "reported per refined corpus, not gating"},
        "tier2": {"questions": c2["coverage"]["kept_with_verified_page"], "dropped": c2["coverage"]["dropped_ids"],
                  "gold": {"qrels_sha256": c2["qrels"]["qrels_sha256"], "qrels_manifest_sha256": c2["qrels"]["qrels_manifest_sha256"],
                           "judgments_sha256": c2["qrels"]["judgments_sha256"], "prompt_version": c2["verifier"]["prompt_version"],
                           "prompt_sha256": c2["verifier"]["prompt_sha256"], "settings_sha256": c2["verifier"]["settings_sha256"],
                           "model": c2["verifier"]["model"], "pool_rule": c2["pool"]["rule"], "pool_sha256": c2["pool"]["pool_sha256"]},
                  "label": c2["label"], "spot_check": {k: c2["spot_check"][k] for k in ("precision", "miss_rate", "counts", "date")},
                  "pool_completion": "Sprint 19 judges every unjudged (query, page, page content) in the top 10 of every "
                                     "evaluated run on S/LT/R0 and every page a tier-2 trace read or cited, with the same "
                                     "frozen prompt and settings (support verify --prior); then scores",
                  "answers": "1 search, follow on and follow off, 1 replicate, on O, S, LT, R0",
                  "metrics": {"navigation": "support read or cited; searches to first support; follows landing on support; share of follows over editor-added edges",
                              "retrievers": "RA lexical, embedding, graph-expand (Sprint 15 pins), Recall@5, nDCG@10, MRR, score_ranked pooled"},
                  "old_labels": "the automatic top-5 labels are a historical comparison only"},
        "ragas": {"set": "tier 2: FactualCorrectness main; Faithfulness and ContextRecall descriptive with repeat variance; "
                         "no per-answer claims", "judge_config_sha256": sha(ragas_cfg),
                  "judge": c3["judge"], "c3_metrics": {k: {"pass": v["pass"], "checks": v["checks"]} for k, v in c3["metrics"].items()},
                  "comparison": "plain mean, paired bootstrap 95% CI; repeat shift 0.005-0.015 disclosed as the noise floor"},
        "tables": {"rule": "one per tier, never pooled: LT - O, R0 - O, LT - R0, with S - O beside every row",
                   "intervals": "bootstrap 95% clustered by question; duplicate-text pairs clustered together"},
        "provenance_credit": provenance,
        "hr03": {"size": "20 changed pages per arm", "sampling": "uniform, stratified by area", "reviewer": "author",
                 "stop_rule": "3 or more of an arm's 20 pages with a factual loss or error stops the run before answers"},
        "usd_stop": {"amount": 100, "from": DATE, "scope": "all remaining API spend of the project (D11d)"},
        "authorization_points": ["1: Sprint 16 survey and tier-1 generation", "2: Sprint 17 refinement",
                                 "3: Sprint 19 answers, scoring, pool completion"],
    }


def prereg(args):
    if PREREG.exists():
        raise Refused("preregistration-already-frozen")
    data = preregistration()
    write_json(PREREG, data)
    lock(PREREG)
    es = rr.load_sandbox()
    schemas = wr.record_schemas(wr.LIVE_AREA_BOUNDS)
    wr.sf.write_or_same(RUN_ROOT / "record-schemas.json", schemas)
    manifests = wr.write_session_files(RUN_ROOT, es, run_id=RUN_ID, fixture=False, sessions={
        SURVEY_KEY: (wr.SURVEY_ARM, "survey", None, wr.survey_prompt(), None)})
    plan = {"run_id": RUN_ID, "kind": "survey", "areas": None, "order": [], "trunk_inventory": None,
            "manifests": {k: {"path": str(p), "sha256": sha(p)} for k, p in manifests.items()},
            "schemas": {"path": (RUN_ROOT / "record-schemas.json").relative_to(ROOT).as_posix(),
                        "sha256": sha(RUN_ROOT / "record-schemas.json")},
            "budget": wr.ARM_BUDGET,
            "preregistration": {"path": PREREG.relative_to(ROOT).as_posix(), "sha256": sha(PREREG)},
            "survey_prompt": {"path": str(RUN_ROOT / "prompts" / f"{SURVEY_KEY}.txt"),
                              "sha256": sha(RUN_ROOT / "prompts" / f"{SURVEY_KEY}.txt")}}
    write_json(SURVEY_PLAN, plan)
    lock(SURVEY_PLAN)
    print(json.dumps({"preregistration_sha256": sha(PREREG), "survey_plan_sha256": sha(SURVEY_PLAN),
                      "survey_prompt_sha256": plan["survey_prompt"]["sha256"]}, indent=1))


def check(args):
    data = check_lock(PREREG)
    bad = [p for p, h in data["documents"].items() if sha(CONTEXT / p) != h]
    bad += [p for p, h in data["code"].items() if sha(CONTEXT / p) != h and p != "scripts/wc_freeze_16.py"]
    bad += [p for p, h in data["evidence"].items() if sha(CONTEXT / p) != h]
    if bad:
        raise Refused("preregistration-pins-changed:" + ",".join(bad))
    wr.load_run_plan(SURVEY_PLAN)
    print(json.dumps({"preregistration": "ok", "survey_plan": "ok", "preregistration_sha256": sha(PREREG)}))


def authorize(args):
    path = RUN_ROOT / "host-private" / "authorization.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = rr.read_json(path) if path.exists() else {"entries": []}
    data["entries"].append({"point": args.point, "author_yes": True, "quote": args.quote,
                            "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "preregistration_sha256": sha(PREREG), "plan_sha256": sha(Path(args.plan)),
                            "scope": args.scope})
    write_json(path, data)
    print(path)


def freeze_survey(args):
    """The valid survey attempt's partition, seeded area order and trunk inventory, frozen by hash (R0 gets the partition only)."""
    check_lock(PREREG)
    attempts = rr.read_json(RUN_ROOT / "host-private" / "survey-attempts.json")["attempts"]
    valid = [a for a in attempts if a["valid"]]
    if not valid:
        raise Refused("no-valid-survey-attempt")
    a = valid[0]
    prefixes = a["host_checks"]["paths"]
    vm = rr.read_json(wr.V6)
    rels = sorted(p.rel_path for p in rr.baseline_pages(wr.dcv.pinned(vm), vm))
    areas = {aid: sorted(r for r in rels if sfb.in_area(r, pref)) for aid, pref in prefixes.items()}
    order = wr.area_order(list(areas))
    trunk = a["record"]["trunk_inventory"]
    data = {"schema_version": "docspectra.icsc.wc_survey_freeze.v1", "created": DATE, "run_id": RUN_ID,
            "preregistration_sha256": sha(PREREG), "attempts": len(attempts), "namespace": a["namespace"],
            "survey_tokens": a["editor_tokens"], "survey_wall_seconds": a["editor_wall_seconds"],
            "read_only_kept": a["read_only_kept"], "areas": areas, "page_counts": {k: len(v) for k, v in areas.items()},
            "area_count": len(areas), "pages_covered": sum(len(v) for v in areas.values()),
            "pages_distinct": len({r for v in areas.values() for r in v}), "area_prefixes": prefixes, "order": order,
            "order_seed_label": wr.ORDER_SEED_LABEL, "trunk_inventory": trunk,
            "partition_sha256": wr.sha_text(json.dumps(areas, sort_keys=True)),
            "trunk_inventory_sha256": wr.sha_text(json.dumps(trunk, sort_keys=True)),
            "minimum_attainable_p_two_sided": 2 / 2 ** len(areas), "host_checks": a["host_checks"],
            "method": a["record"].get("method"), "rationales": {x["area_id"]: x["rationale"] for x in a["record"]["areas"]}}
    write_json(SURVEY_FREEZE, data)
    lock(SURVEY_FREEZE)
    print(json.dumps({k: data[k] for k in ("area_count", "pages_covered", "page_counts", "survey_tokens",
                                           "survey_wall_seconds", "minimum_attainable_p_two_sided")}, indent=1))
    print("trunk topics:", len(trunk))


def freeze_tier2(args):
    """The 111-question tier-2 set, its C2 qrels identity and the duplicate-text clusters, frozen by hash."""
    import hashlib
    check_lock(PREREG)
    manifest = rr.read_json(C2 / "verify-full/qrels_manifest.json")
    kept = manifest["ordered_query_ids"]
    questions = {r["question_id"]: r["question"] for r in map(json.loads, (ROOT / "artifacts/docker-cikm-2026-05-14/dataset/questions.jsonl").read_text(encoding="utf-8").splitlines()) if r}
    by_text = {}
    for q in kept:
        by_text.setdefault(" ".join(questions[q].lower().split()), []).append(q)
    clusters = sorted(v for v in by_text.values() if len(v) > 1)
    old = ROOT / "artifacts/orientation-spectra-2026-10-06-docs-scope/support.json"
    data = {"schema_version": "docspectra.icsc.wc_tier2_freeze.v1", "created": DATE,
            "preregistration_sha256": sha(PREREG), "questions": len(kept), "question_ids": kept,
            "question_ids_sha256": wr.sha_text(json.dumps(kept)), "dropped": manifest["counts"]["queries_dropped_no_verified_page"],
            "duplicate_text_clusters": clusters,
            "gold": {"qrels_path": "artifacts/c2-pooled-qrels/verify-full/qrels.jsonl", "qrels_sha256": manifest["qrels_sha256"],
                     "qrels_manifest_sha256": hashlib.sha256((C2 / "verify-full/qrels_manifest.json").read_bytes()).hexdigest(),
                     "page_decisions_sha256": manifest["page_decisions_sha256"], "judgments_sha256": manifest["judgments_sha256"],
                     "qrels_version": manifest["qrels_version"], "prompt_version": manifest["prompt_version"],
                     "prompt_sha256": manifest["prompt_sha256"], "settings_sha256": manifest["settings_sha256"],
                     "label": rr.read_json(EVIDENCE / "c2-pooled-qrels-docker-tier2-2026-10-12.json")["label"]},
            "old_labels_historical_only": {"path": old.relative_to(ROOT).as_posix(), "sha256": sha(old)}}
    write_json(TIER2_FREEZE, data)
    lock(TIER2_FREEZE)
    print(json.dumps({"questions": data["questions"], "dropped": len(data["dropped"]), "duplicate_clusters": clusters}, indent=1))


def live_extra():
    """The live-manifest fields Sprint 12b filled and the Sprint 15 survey path did not (restructuring_runs refuses without)."""
    q = CONTEXT / "evidence/restructuring-pilot-11b-questions-2026-10-08.json"
    return {"cost_admission": {"authorization": "explicit author yes in chat, recorded in host-private/authorization.json "
                                                "(point 1, October 14, 2026)",
                               "usd_cap": 100, "editor_billing": "ChatGPT subscription usage (no USD)"},
            "evaluation": {"question_set": {"path": str(q), "sha256": sha(q),
                                            "role": "existing 5 tier-1 tasks; the full tier-1 and tier-2 sets are frozen "
                                                    "later in Sprint 16 (pre-registration a04c70c5)"},
                           "answer_conditions": "design D7: tier 1, 1 or 6 searches x follow on/off x 2 replicates; "
                                                "tier 2, 1 search x follow on/off x 1 replicate; variants O, S, LT, R0",
                           "grading": "pre-registered (D8, D10, D11); the survey session is read-only and ungraded"}}


def survey_plan_v2(args):
    """Rebuild the live survey manifest with the live fields and freeze run plan v2 (no model call has happened)."""
    check_lock(PREREG)
    if SURVEY_PLAN.exists():
        raise Refused("survey-plan-v2-already-frozen")
    if (RUN_ROOT / f"{wr.SURVEY_ARM}-r01").exists():
        raise Refused("a-survey-namespace-exists")
    old = RUN_ROOT / "manifests" / f"{SURVEY_KEY}.json"
    if old.exists():
        old.rename(old.with_suffix(".v1-superseded.json"))
    original = wr.session_manifest
    wr.session_manifest = lambda *a, **k: original(*a, **dict(k, extra=live_extra()))
    try:
        manifests = wr.write_session_files(RUN_ROOT, rr.load_sandbox(), run_id=RUN_ID, fixture=False, sessions={
            SURVEY_KEY: (wr.SURVEY_ARM, "survey", None, wr.survey_prompt(), None)})
    finally:
        wr.session_manifest = original
    rr.validate_manifest(rr.read_json(manifests[SURVEY_KEY]), live=True)
    plan = rr.read_json(SURVEY_PLAN_V1)
    plan["manifests"] = {k: {"path": str(p), "sha256": sha(p)} for k, p in manifests.items()}
    plan["supersedes"] = {"path": SURVEY_PLAN_V1.relative_to(ROOT).as_posix(), "sha256": sha(SURVEY_PLAN_V1),
                          "reason": "the v1 live manifest lacked cost_admission and evaluation; refused before any model call"}
    write_json(SURVEY_PLAN, plan)
    lock(SURVEY_PLAN)
    print(json.dumps({"survey_plan_v2_sha256": sha(SURVEY_PLAN), "manifest_valid_live": True}, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="stage", required=True)
    sub.add_parser("prereg").set_defaults(fn=prereg)
    sub.add_parser("check").set_defaults(fn=check)
    a = sub.add_parser("authorize")
    a.add_argument("--point", type=int, required=True)
    a.add_argument("--quote", required=True)
    a.add_argument("--scope", required=True)
    a.add_argument("--plan", default=str(SURVEY_PLAN))
    a.set_defaults(fn=authorize)
    sub.add_parser("survey-plan-v2").set_defaults(fn=survey_plan_v2)
    sub.add_parser("freeze-survey").set_defaults(fn=freeze_survey)
    sub.add_parser("freeze-tier2").set_defaults(fn=freeze_tier2)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

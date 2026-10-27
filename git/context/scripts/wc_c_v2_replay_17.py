"""Replay every Sprint 17 C admission under RetrievalArena's C v2 (structural_connectivity_constraint.v2). No model calls.

Sprint 17's driver (scripts/wc_refine_17.py) applied two admission corrections to C v1 in context code: C2_FIX (the
C2 joined-pages recomputation, October 18) and C1B_EXEMPTION (author amendment AM1, October 19). Both now live in RA as
C v2 (retrieval_arena/graph/structural_target_v2.py). This stage shows C v2 reproduces the driver's decisions:

  for every session tree with a C receipt under the wc-v1 run root, it recomputes
    - C v2 with the Docker templated-page rule below (the driver's own constants),
    - the driver's final rule (wc_refine_17.c_admission_c1b, the code the run ended under),
  and compares C v2 with (a) the driver's final rule, field for field; (b) the recorded receipt and run-state c_passed;
  (c) the ledger replayed through wc_refine_17.effective: the C reasons of the session's end row and its accept or
  rollback action, recomputed with the C v2 verdict in place of the recorded one.

A row the author superseded by `reopen` (kind "pass-superseded") is reported in its own class: its recorded verdict was
made before a correction existed and is not part of the effective outcome. Nothing is changed to force a match; the
stage only reads trees and receipts and writes one evidence file. The runtime and Sprint 18 still use the driver.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wc_refine_17 as w  # noqa: E402  (installs the driver's final c_admission on rr)

wr, rr, sfb = w.wr, w.rr, w.sfb
DATE = "2026-10-19"
OUT = w.EVIDENCE / f"wc-c-v2-replay-v1-{DATE}.json"
STRUCT_V2 = rr.RA / "retrieval_arena/graph/structural_target_v2.py"
C_REASONS = ("status:c_rejected", "C-failed")


def load_v2():
    rr.load_struct()  # refuses on v1 drift and puts RA on sys.path
    from retrieval_arena.graph import structural_target_v2 as st2
    return st2


def docker_rule(st2):
    """Docker's Hugo templating, from the driver's own constants (C1B_EXEMPTION)."""
    return st2.TemplatedPageRule(include_root="content/includes/", include_reference=w.INCLUDE_REF.pattern,
                                 index_filename="_index.md", page_suffix=".md")


def git(*args):
    return subprocess.run(["git", "-C", str(rr.RA), *args], capture_output=True, text=True, check=True).stdout.strip()


def receipts_of(ns):
    """(c receipt path, tree, source) pairs: the launch receipt and, if any, the residual terminal's."""
    out = []
    state = rr.read_json(ns / "host/run-state.json")
    if (ns / "host/receipts/c-admission.json").exists() and state.get("terminal_tree"):
        out.append((ns / "host/receipts/c-admission.json", Path(state["terminal_tree"]), "launch"))
    res = sfb.residual_dir(ns) / "receipts/c-admission.json"
    if res.exists():
        out.append((res, Path(rr.read_json(sfb.residual_dir(ns) / "record.json")["terminal_tree"]), "residual"))
    return state, out


def v2_action(row, v2_passed, flags):
    """The end row's action with C v2's verdict in place of the recorded C verdict (same rules as admission_record)."""
    reasons = [r for r in (row.get("not_admitted_because") or []) if r not in C_REASONS]
    if not v2_passed:
        reasons += ["status:c_rejected", "C-failed"]
    elif "status:c_rejected" in (row.get("not_admitted_because") or []) and "preservation_flagged:unmapped_removal" in flags:
        reasons.append("status:preservation_flagged")
    if row.get("action") in ("halt", "noop", "survey") or row.get("action") is None:
        return row.get("action"), reasons
    return ("advance" if not reasons else "rollback"), reasons


def replay(args):
    st2 = load_v2()
    rule = docker_rule(st2)
    vm = rr.read_json(wr.V6)
    gm = wr.dcv.pinned(vm)
    base_list = rr.baseline_pages(gm, vm)
    rows = w.ledger_rows()
    eff = w.effective(rows)
    end_by_ns = {r["namespace"]: r for r in eff if r["event"] == "end" and r.get("namespace")}
    all_ns = {r["namespace"] for r in rows if r["event"] == "end" and r.get("namespace")}
    sessions, excluded = [], []
    for ns in sorted(p for p in w.RUN_ROOT.iterdir() if p.is_dir() and "-r" in p.name):
        state, recs = receipts_of(ns)
        if not recs:
            excluded.append({"namespace": ns.name, "reason": "no C receipt",
                             "ledger": "voided" if ns.name in all_ns and ns.name not in end_by_ns else
                             ("effective" if ns.name in end_by_ns else "not an editing session")})
            continue
        manifest = sfb.manifest_for_state(state)
        pin = manifest["c_pin"]
        base_config = rr.load_struct().StructuralConfig(
            **{k: tuple(v) if isinstance(v, list) else v for k, v in pin["config"].items()})
        config = st2.ConstraintV2Config(base=base_config, templated_pages=rule)
        st = rr.load_struct()
        base = st.baseline_summary(sorted(p.doc_id for p in base_list), gm.extract_markdown_edges(base_list),
                                   pin["anchors"], config=base_config)
        for path, tree, source in recs:
            receipt = rr.read_json(path)
            var_list, nodes, edges = rr.tree_graph(gm, tree, wr.dcv.scope_of(vm))  # also records pages for the driver
            mapping = receipt["mapping"]
            driver = w.c_admission_c1b(manifest, gm, base_list, nodes, edges, mapping)["result"]
            v2 = st2.connectivity_admission(nodes, edges, base, mapping=mapping, config=config,
                                            pages={p.doc_id: (p.rel_path, p.text) for p in var_list})
            rec = receipt["result"]
            row = end_by_ns.get(ns.name)
            c_passed = state.get("c_passed") if source == "launch" else rr.read_json(
                sfb.residual_dir(ns) / "record.json").get("c_passed")
            entry = {"namespace": ns.name, "receipt": source, "receipt_sha256": w.sha(path),
                     "tree_graph_sha256": st.graph_digest(nodes, edges), "receipt_graph_sha256": rec["graph_sha256"],
                     "recorded": {"passed": rec["passed"], "reasons": rec["reasons"], "lambda2": rec["lambda2"],
                                  "c_fix": receipt.get("c_fix"), "run_state_c_passed": c_passed},
                     "driver_final_rule": {"passed": driver["passed"], "reasons": driver["reasons"],
                                           "lambda2": driver["lambda2"], "c1b_exempted": driver.get("c1b_exempted")},
                     "c_v2": {"passed": v2["passed"], "reasons": v2["reasons"], "lambda2": v2["lambda2"],
                              "lambda2_v1": v2["lambda2_v1"], "c2_joined_pages": v2["c2_joined_pages"],
                              "c1b_exempted": v2["c1b_exempted"], "v1_reasons": v2["v1_reasons"],
                              "config_sha256": v2["config_sha256"], "base_config_sha256": v2["base_config_sha256"]}}
            entry["v2_equals_driver_final_rule"] = (v2["passed"], v2["reasons"], v2["lambda2"]) == (
                driver["passed"], driver["reasons"], driver["lambda2"]) and (v2["c1b_exempted"] or None) == (
                driver.get("c1b_exempted") or None)
            entry["tree_matches_receipt"] = entry["tree_graph_sha256"] == rec["graph_sha256"]
            if row is None:
                entry["ledger"] = None
                entry["class"] = "no effective ledger row"
            else:
                recorded_c_ok = not any(r in C_REASONS for r in (row.get("not_admitted_because") or []))
                action, reasons = v2_action(row, v2["passed"], state.get("flags", []))
                entry["ledger"] = {"seq": row["seq"], "arm": row["arm"], "kind": row["kind"], "area": row["area"],
                                   "action": row.get("action"), "not_admitted_because": row.get("not_admitted_because"),
                                   "recorded_c_passed": recorded_c_ok, "action_with_c_v2": action,
                                   "reasons_with_c_v2": reasons}
                entry["class"] = "superseded-by-reopen" if row["kind"].endswith("-superseded") else "effective"
            entry["verdict_matches"] = (rec["passed"] == v2["passed"] and c_passed == v2["passed"] and (
                entry["ledger"] is None or (entry["ledger"]["recorded_c_passed"] == v2["passed"]
                                            and entry["ledger"]["action_with_c_v2"] == entry["ledger"]["action"])))
            sessions.append(entry)
            print(ns.name, source, entry["class"], "v2", v2["passed"], "recorded", rec["passed"],
                  "match" if entry["verdict_matches"] else "DIFFERS",
                  "driver=" + str(entry["v2_equals_driver_final_rule"]), flush=True)
    eff_rows = [s for s in sessions if s["class"] != "superseded-by-reopen"]
    sup_rows = [s for s in sessions if s["class"] == "superseded-by-reopen"]
    reopen = {r["reopens_seq"]: r for r in rows if r["event"] == "reopen"}
    for s in sup_rows:
        r = reopen.get(s["ledger"]["seq"])
        s["reopen"] = {"seq": r["seq"], "reason": r["reason"], "author_quote": r.get("author_quote")} if r else None
        # the reopen premise: the recorded C rejection came only from a defect C v2 corrects
        s["c_v2_supports_reopen"] = bool(r) and s["c_v2"]["passed"] and not s["recorded"]["passed"]
    total = {
        "sessions_replayed": len(sessions),
        "v2_equals_driver_final_rule": sum(s["v2_equals_driver_final_rule"] for s in sessions),
        "tree_matches_receipt_graph": sum(s["tree_matches_receipt"] for s in sessions),
        "effective_rows": len(eff_rows),
        "effective_verdicts_match": sum(s["verdict_matches"] for s in eff_rows),
        "superseded_rows": len(sup_rows),
        "superseded_verdicts_match_recorded": sum(s["verdict_matches"] for s in sup_rows),
        "superseded_c_v2_supports_reopen": sum(s["c_v2_supports_reopen"] for s in sup_rows),
        "c_v2_passed": sum(s["c_v2"]["passed"] for s in sessions),
        "recorded_passed": sum(s["recorded"]["passed"] for s in sessions),
    }
    total["all_effective_verdicts_match"] = total["effective_verdicts_match"] == len(eff_rows)
    total["all_equal_driver_final_rule"] = total["v2_equals_driver_final_rule"] == len(sessions)
    out = {"schema_version": "docspectra.icsc.wc_c_v2_replay.v1", "created": DATE,
           "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "run_id": w.RUN_ID,
           "model_calls": 0,
           "c_v2": {"version": st2.CONSTRAINT_VERSION, "module": "retrieval_arena/graph/structural_target_v2.py",
                    "module_sha256": w.sha(STRUCT_V2), "ra_commit": git("rev-parse", "HEAD"),
                    "ra_branch": git("rev-parse", "--abbrev-ref", "HEAD"),
                    "ra_module_committed": git("status", "--porcelain", "--", str(STRUCT_V2)) == "",
                    "v1_code_sha256": rr.STRUCT_CODE_SHA256, "templated_rule": {
                        k: getattr(rule, k) for k in ("include_root", "include_reference", "index_filename",
                                                      "page_suffix")},
                    "config_sha256": sorted({s["c_v2"]["config_sha256"] for s in sessions}),
                    "base_config_sha256": sorted({s["c_v2"]["base_config_sha256"] for s in sessions})},
           "driver": {"script": "scripts/wc_refine_17.py", "script_sha256": w.sha(HERE / "wc_refine_17.py"),
                      "final_rule": "wc_refine_17.c_admission_c1b (C v1 + C2_FIX + C1B_EXEMPTION)",
                      "C2_FIX": w.C2_FIX, "C1B_EXEMPTION": w.C1B_EXEMPTION},
           "ledger": {"rows": len(rows), "effective_end_rows": len(end_by_ns),
                      "replay": "wc_refine_17.effective (voids dropped, amends applied, reopened rows marked superseded)"},
           "rule_of_comparison": ("verdict_matches: the recorded receipt's passed, the run-state c_passed and the ledger "
                                  "end row's C verdict all equal C v2's passed, and the end row's action recomputed "
                                  "with C v2's verdict equals the recorded (effective) action. v2_equals_driver_final_"
                                  "rule: passed, reasons, lambda2 and C1b exemptions equal the driver's final rule."),
           "excluded": excluded, "total": total, "sessions": sessions}
    if args.out.exists():
        raise w.Refused("replay-evidence-exists")
    w.write_json(args.out, out)
    print(json.dumps(total, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT, help="evidence path (default: %(default)s)")
    ap.set_defaults(func=replay)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

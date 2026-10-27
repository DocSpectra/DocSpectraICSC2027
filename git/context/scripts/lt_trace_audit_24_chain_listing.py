"""Sprint 24 addendum: per-trace listing of the tier-2 chain step-7 traces (LT, R0), author request after the results.

Reads only saved outputs: the RA operator output and input of Sprint 24 (actions, citations, traces, variant corpora)
and the Sprint 19 RAGAS scores. Zero model calls. The step-7 predicate is `lt_trace_audit_24.chain` (imported, not
re-implemented); the listing refuses unless its counts equal the tracked evidence. Write-once; `verify` recomputes.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lt_trace_audit_24 as s24  # noqa: E402

LISTING = s24.EVIDENCE / f"lt-trace-audit-24-chain-listing-v1-{s24.DATE}.json"
METRICS = ("factual_correctness", "faithfulness", "context_recall")


def ragas(oid):
    path = s24.RUN / "scoring-19/ragas-t2" / oid / "run/scores.json"
    recs = {(r["unit"][0], r["unit"][1]): {m: r[m] for m in METRICS} for r in s24.read_json(path)["records"]}
    return recs, s24.pin(path)


def build():
    arms = s24.arm_of()
    oids = {a: o for o, a in arms.items()}
    traces, actions, citations, _ = s24.load_outputs()
    evidence = s24.read_json(s24.RESULT)
    by_cell = {t["cell_id"]: t for t in traces}
    cites = defaultdict(list)
    for c in citations:
        if c["kind"] == "answer":
            cites[c["cell_id"]].append(c)
    stems = {oid: {p.stem for p in (s24.OPERATOR_INPUT / "variants" / oid / "corpus").glob("*.md")} for oid in arms}
    scores = {oid: ragas(oid) for oid in arms}
    out = {}
    for arm in s24.EDIT_ARMS:
        rows = []
        for t in sorted((t for t in traces if t["tier"] == "t2" and arms[t["variant_id"]] == arm
                         and t["condition"] == "s1-follow"), key=lambda t: t["question_id"]):
            hits = []
            for a in actions:
                if (a["cell_id"] != t["cell_id"] or a["action"] != "follow" or a["status"] != "accepted"
                        or a.get("edge_class") != "added" or not a.get("first_page_text_exposure")
                        or a.get("label") != "supported"):
                    continue
                shown = {n for x, y in a["returned_ranges"] for n in range(x, y + 1)}
                dest = [c for c in cites[t["cell_id"]] if c["doc_id"] == a["doc_id"]]
                linked = [c for c in dest if set(range(c["start_line"], c["end_line"] + 1)) & shown]
                if linked:
                    hits.append((a, dest, linked))
            if not hits:
                continue
            q = t["question_id"]
            dests = sorted({a["doc_id"] for a, _, _ in hits})
            counterparts = []
            for carm, cond in (("O", "s1-follow"), ("S", "s1-follow"), (arm, "s1-no-follow")):
                coid = oids[carm]
                ct = next(x for x in traces if x["variant_id"] == coid and x["tier"] == "t2"
                          and x["question_id"] == q and x["condition"] == cond)
                cc = cites[ct["cell_id"]]
                counterparts.append({
                    "arm": carm, "condition": cond, "cell_id": ct["cell_id"], "trace_sha256": ct["trace_sha256"],
                    "terminal": ct["terminal"], "follows_accepted": ct["follows_accepted"],
                    "destination_in_variant_corpus": {d: d in stems[coid] for d in dests},
                    "destination_cited": {d: any(c["doc_id"] == d for c in cc) for d in dests},
                    "cited_pages": len({c["doc_id"] for c in cc}),
                    "cited_supported_pages": len({c["doc_id"] for c in cc if c.get("label") == "supported"}),
                    "existing_ragas": scores[coid][0].get((q, cond))})
            trace = s24.read_json(s24.RUN / "answers-19/t2" / f"{t['cell_id']}.json")
            rows.append({
                "cell_id": t["cell_id"], "question_id": q, "question": trace["question"], "condition": t["condition"],
                "replicate": t["replicate"], "trace_sha256": t["trace_sha256"], "terminal": t["terminal"],
                "in_common105": q in set(s24.read_json(s24.REJUDGE)["common_set"]["question_ids"]),
                "follows_accepted": t["follows_accepted"], "follows_accepted_added": t["follows_accepted_added"],
                "qualifying_follows": [{
                    "call": a["call"], "source_doc_id": a["source_doc_id"], "doc_id": a["doc_id"],
                    "new_page_incident": a.get("new_page_incident"), "menu_edges": a.get("menu_edges"),
                    "menu_added_edges": a.get("menu_added_edges"), "listed_by_prior_search": a.get("listed_by_prior_search"),
                    "returned_ranges": a["returned_ranges"], "returned_text_sha256": a["returned_text_sha256"],
                    "span_exposure_this_call": a.get("span_exposure_this_call"),
                    "span_exposure_final_context": a.get("span_exposure_final_context"),
                    "destination_answer_citations": [{k: c.get(k) for k in (
                        "start_line", "end_line", "first_exposure", "final_context", "span_overlap_cited")}
                        | {"intersects_this_follow": c in linked} for c in dest]}
                    for a, dest, linked in hits],
                "answer_citations": len(cites[t["cell_id"]]),
                "answer_cited_pages": len({c["doc_id"] for c in cites[t["cell_id"]]}),
                "existing_ragas": scores[t["variant_id"]][0].get((q, t["condition"])),
                "counterparts": counterparts})
        expected = evidence["chain_t2_follow_all111"][arm]["traces"].get("7_cited_lines_from_this_follow", 0)
        if len(rows) != expected:
            raise SystemExit(f"listing-count-differs-from-evidence:{arm}:{len(rows)}!={expected}")
        out[arm] = rows
    return {
        "schema_version": "docspectra.icsc.lt_trace_audit_24.chain_listing.v1", "created": s24.DATE,
        "nature": "author-requested addendum after the results; a listing of the traces at chain step 7 (accepted "
                  "added-edge follow, first page text exposure, supported destination, answer citation intersecting "
                  "the follow's returned lines). Existing scores are joined, not recomputed. Counterparts differ in "
                  "more than one link (O and S lack the edited pages and links; the follow-off cell lacks follows). "
                  "Not a sample, an effect estimate or evidence of reliance.",
        "model_calls": 0, "audit_evidence": s24.pin(s24.RESULT), "plan": s24.pin(s24.PLAN),
        "sources": {"operator_output_manifest": s24.pin(s24.OPERATOR_OUTPUT / "trace_provenance_manifest.json"),
                    "ragas_scores": {arms[o]: scores[o][1] for o in sorted(arms)}, "script": s24.pin(Path(__file__))},
        "counts": {arm: len(out[arm]) for arm in out}, "traces": out}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=("write", "verify"))
    mode = ap.parse_args().mode
    text = s24.canonical_text(build())
    if mode == "write":
        print(json.dumps(s24.write_once(LISTING, text)))
    else:
        same = text == LISTING.read_text(encoding="utf-8")
        print(json.dumps({"reproduced": same, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}))
        if not same:
            raise SystemExit(1)


if __name__ == "__main__":
    main()

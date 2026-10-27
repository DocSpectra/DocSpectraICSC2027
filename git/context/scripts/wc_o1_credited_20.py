"""Sprint 20 follow-up (author, October 24): how the re-judged verifier decided O1's credited new pages. Zero model calls.

  python wc_o1_credited_20.py record     write the evidence (write-once)

For each edited arm and tier-2 question, the derived-only O1 pages (credited by a shared distinctive line, rule 3 of
PROVENANCE_RULE, not surviving or moved) that any fixed retriever put in its top 10, with the direct re-judged
decision on that page. The `not_supported` pairs are where O1 and direct judgment disagree; they are the population
for the author's spot-check (W-G4).
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wc_analysis_20 as w  # noqa: E402

RESULT = w.EVIDENCE / "wc-o1-credited-pages-20-v1-2026-10-24.json"


def compute():
    ctx = w.context()
    vctxs = w.variant_contexts(ctx)
    c2 = defaultdict(set)
    for r in w.jsonl(w.C2_QRELS):
        if r["relevance"] > 0:
            c2[r["query_id"]].add(r["support_target_id"])
    out = {"schema_version": "docspectra.icsc.wc_o1_credited_20.v1", "created": w.DATE, "model_calls": 0,
           "analysis_evidence_sha256": w.sha(w.RESULT), "code_sha256": w.sha(Path(__file__)), "arms": {}}
    for arm in ("LT", "R0"):
        oid = ctx["oid_of"][arm]
        v = vctxs[oid]
        pos, judged = w.qrels(oid, "verify-escalated")
        dec = w.decisions(oid, "verify-escalated")
        ranks = w.rankings(oid)
        pairs = []
        for q in ctx["all_q"]:
            full = set().union(*(set(v["equivalence"].get(b, [])) for b in c2[q])) if c2[q] else set()
            surv = set().union(*(set(v["surviving"].get(b, [])) for b in c2[q])) if c2[q] else set()
            derived = full - surv
            hit = sorted({d for r in w.wr.RETRIEVERS for d in ranks[r][q][:10] if d in derived})
            for d in hit:
                status = "supported" if d in pos[q] else "not_supported" if d in judged[q] else dec.get((q, d), "unjudged")
                pairs.append({"question_id": q, "doc_id": d, "decision": status,
                              "credited_for": sorted(b for b in c2[q] if d in v["equivalence"].get(b, [])),
                              "retrievers": sorted(r for r in w.wr.RETRIEVERS if d in ranks[r][q][:10])})
        out["arms"][arm] = {"variant": oid, "pairs": len(pairs), "decisions": dict(Counter(p["decision"] for p in pairs)),
                            "questions": len({p["question_id"] for p in pairs}),
                            "questions_with_a_supported_credited_page": len({p["question_id"] for p in pairs
                                                                             if p["decision"] == "supported"}),
                            "rows": pairs}
    return out


def record(_args=None):
    if RESULT.exists():
        raise w.Refused(f"evidence-exists:{RESULT.name}")
    RESULT.write_text(w.canonical(compute()), encoding="utf-8", newline="\n")
    digest = w.sha(RESULT)
    RESULT.with_suffix(".sha256").write_text(f"{digest}  {RESULT.name}\n", encoding="ascii", newline="\n")
    data = json.loads(RESULT.read_text(encoding="utf-8"))
    print(json.dumps({"evidence": RESULT.name, "sha256": digest,
                      "summary": {a: {k: x[k] for k in ("pairs", "decisions", "questions",
                                                        "questions_with_a_supported_credited_page")}
                                  for a, x in data["arms"].items()}}, indent=1))


if __name__ == "__main__":
    if sys.argv[1:] != ["record"]:
        raise SystemExit(__doc__)
    record()

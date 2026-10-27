"""Sprint 20 follow-up: HR-21, the author's blind check of LT's credited new pages (W-A4, author October 24).

  python wc_hr21_20.py build     sample the items, write the blinded page, the key (host-private) and the frozen plan
  python wc_hr21_20.py export --listing <ArtifactData list out_dir> --out <file>    collect the saved verdicts

Population (`evidence/wc-o1-credited-pages-20-v1-2026-10-24.json`): LT's O1 credited new pages (shared distinctive
line with a C2 gold page, not surviving or moved) that a fixed retriever put in a top 10. Sample, seeded: 16 of the 53
the re-judged verifier found `not_supported` (the disputed pages) and 4 of the 51 it found `supported` (controls, so the
reviewer cannot tell which is which). Each item shows the question, its historical FAQ answer, the credited page in
full, and the original answering page(s) it was credited for, with their shared lines marked.

Blinding: the verifier's decision and the variant id are never on the page; one seeded random order. Every item is
from the one edited variant, so the review is not blind to arm. Verdicts: answers / partly answers / does not answer /
unsure. Analysis rule (frozen in the plan before review): the share of disputed pages judged `answers` (Wilson 95%),
with `partly_answers` counted both ways as a bracket, says how far O1's credit holds where direct judgment rejected it;
the controls give agreement with the verifier's `supported`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wc_analysis_20 as w  # noqa: E402
import wc_hr20_19 as h20  # noqa: E402
import wc_ragas_15b as b15  # noqa: E402

a19, rr, wr = w.a19, w.rr, w.wr
ROOT = w.ROOT
TEMPLATE = HERE / "wc_hr21_20.template.html"
OUT = ROOT / "artifacts/wc-hr21-20"
KEY = OUT / "host-private" / "key.json"
POPULATION = w.EVIDENCE / "wc-o1-credited-pages-20-v1-2026-10-24.json"
PLAN = w.EVIDENCE / "wc-hr21-plan-v1-2026-10-24.json"
PACKET_ID = "wc-hr21-v1"
SEED_LABEL = "icsc2027-hr21-v1"
N_DISPUTED, N_CONTROL = 16, 4
ARM = "LT"
VERDICTS = ("answers", "partly_answers", "does_not_answer", "unsure")
RULE = ("share of the 16 disputed items judged `answers` (Wilson 95%); `partly_answers` counted as answers and as not "
        "(bracket); `unsure` excluded and counted. Controls: share of the 4 judged `answers` or `partly_answers`. If most "
        "disputed pages answer, O1's credit holds where direct judgment rejected it (the verifier misses moved answers); "
        "if few do, direct judgment holds. Agreement with the 53 is estimated, never claimed per page beyond the sample")


def seeded(label):
    return random.Random(int(hashlib.sha256(f"{SEED_LABEL}:{label}".encode()).hexdigest(), 16))


def build(_args):
    if KEY.exists() or PLAN.exists():
        raise w.Refused("hr21-packet-already-built")
    pop = rr.read_json(POPULATION)
    rows = pop["arms"][ARM]["rows"]
    oid = pop["arms"][ARM]["variant"]
    disputed = sorted((r for r in rows if r["decision"] == "not_supported"), key=lambda r: (r["question_id"], r["doc_id"]))
    control = sorted((r for r in rows if r["decision"] == "supported"), key=lambda r: (r["question_id"], r["doc_id"]))
    sample = seeded("disputed").sample(disputed, N_DISPUTED) + seeded("control").sample(control, N_CONTROL)
    seeded("order").shuffle(sample)
    plan19 = a19.load_plan()
    questions = {q["question_id"]: q["question"] for q in plan19["questions"]["2"]}
    refs = b15.tier2_references()
    vm = rr.read_json(wr.V6)
    base = {p.doc_id: p for p in rr.baseline_pages(wr.dcv.pinned(vm), vm)}
    corpus = a19.EXPORT / "variants" / oid / "corpus"
    items, key = [], {}
    for i, r in enumerate(sample, 1):
        iid = f"C{i:02d}"
        text = (corpus / f"{r['doc_id']}.md").read_text(encoding="utf-8")
        page_lines = {h20.norm(x) for x in text.splitlines() if h20.norm(x)}
        sources = []
        for b in r["credited_for"]:
            orig = base[b].text
            shared = sorted(l for l in {h20.norm(x) for x in orig.splitlines()} if len(l) >= h20.MIN_CARRY and l in page_lines)
            sources.append({"rel_path": base[b].rel_path, "text": orig, "shared_lines": shared})
        items.append({"id": iid, "question": questions[r["question_id"]], "reference": refs[r["question_id"]],
                      "page": {"name": r["doc_id"].rsplit("__", 1)[0], "new_page": r["doc_id"] not in base, "text": text,
                               "shared_lines": sorted({l for s in sources for l in s["shared_lines"]})},
                      "sources": sources})
        key[iid] = {"question_id": r["question_id"], "doc_id": r["doc_id"], "decision": r["decision"],
                    "role": "disputed" if r["decision"] == "not_supported" else "control"}
    packet = {"packet_id": PACKET_ID, "items": items, "verdicts": list(VERDICTS)}
    body = json.dumps(packet, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8").replace("/*PACKET_JSON*/null", body)
    (OUT / "review-page").mkdir(parents=True, exist_ok=True)
    (OUT / "review-page" / "index.html").write_text(page, encoding="utf-8", newline="\n")
    packet_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    KEY.parent.mkdir(parents=True, exist_ok=True)
    KEY.write_text(json.dumps({"packet_id": PACKET_ID, "packet_sha256": packet_sha, "seed_label": SEED_LABEL,
                               "variant": oid, "key": key}, indent=1), encoding="utf-8", newline="\n")
    plan = {"schema_version": "docspectra.icsc.wc_hr21_plan.v1", "created": w.DATE, "packet_id": PACKET_ID,
            "decision": "W-A4 spot-check (author, 2026-10-24)", "population_sha256": w.sha(POPULATION),
            "population": {"disputed": len(disputed), "control_pool": len(control)},
            "sample": {"disputed": N_DISPUTED, "control": N_CONTROL, "seed_label": SEED_LABEL,
                       "rule": "random.sample per stratum, seeded by sha256(seed_label:stratum); order seeded likewise"},
            "packet_sha256": packet_sha, "key_sha256": w.sha(KEY), "page_sha256": w.sha(OUT / "review-page" / "index.html"),
            "verdicts": list(VERDICTS), "analysis_rule": RULE,
            "blinding": "verifier decision and variant id hidden; all items from one edited variant (not blind to arm)",
            "model_calls": 0}
    PLAN.write_text(w.canonical(plan), encoding="utf-8", newline="\n")
    PLAN.with_suffix(".sha256").write_text(f"{w.sha(PLAN)}  {PLAN.name}\n", encoding="ascii", newline="\n")
    print(json.dumps({"page": str(OUT / "review-page" / "index.html"), "items": len(items),
                      "distinct_questions": len({k["question_id"] for k in key.values()}),
                      "new_pages": sum(it["page"]["new_page"] for it in items),
                      "items_without_shared_line": sum(not it["page"]["shared_lines"] for it in items),
                      "plan_sha256": w.sha(PLAN), "bytes": len(page.encode("utf-8"))}, indent=1))


def export(args):
    keyfile = json.loads(KEY.read_text(encoding="utf-8"))
    reviews, reviewer = {}, None
    for path in sorted(Path(args.listing).rglob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        b = doc["data"] if isinstance(doc.get("data"), dict) else doc
        if b.get("packet_id") != PACKET_ID:
            continue
        if "verdict" in b:
            reviews[b["item"]] = {k: b.get(k) for k in ("item", "verdict", "note", "at")}
        elif "name" in b:
            reviewer = {"name": b["name"], "at": b["at"]}
    out = {"schema_version": "docspectra.icsc.wc_hr21.v1", "packet_id": PACKET_ID, "packet_sha256": keyfile["packet_sha256"],
           "plan_sha256": w.sha(PLAN), "reviewer": reviewer, "items": len(keyfile["key"]), "reviewed": len(reviews),
           "counts": {v: sum(r["verdict"] == v for r in reviews.values()) for v in VERDICTS},
           "reviews": [dict(r, **keyfile["key"][r["item"]]) for r in sorted(reviews.values(), key=lambda r: r["item"])]}
    Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("reviewer", "items", "reviewed", "counts")}, indent=1))


def wilson(k, n, z=1.959963984540054):
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return {"k": k, "n": n, "estimate": p, "low": max(0.0, c - h), "high": min(1.0, c + h)}


def score(args):
    """The plan's analysis rule on the exported verdicts; write-once evidence."""
    out_path = w.EVIDENCE / "wc-hr21-result-v1-2026-10-24.json"
    if out_path.exists():
        raise w.Refused(f"evidence-exists:{out_path.name}")
    plan = rr.read_json(PLAN)
    verdicts = rr.read_json(Path(args.verdicts))
    if verdicts["packet_sha256"] != plan["packet_sha256"]:
        raise w.Refused("hr21-verdicts-for-another-packet")
    by = {role: [r for r in verdicts["reviews"] if r["role"] == role] for role in ("disputed", "control")}
    count = lambda rows, *vs: sum(r["verdict"] in vs for r in rows)
    d, c = by["disputed"], by["control"]
    usable = [r for r in d if r["verdict"] != "unsure"]
    data = {"schema_version": "docspectra.icsc.wc_hr21_result.v1", "created": w.DATE, "plan_sha256": w.sha(PLAN),
            "verdicts_sha256": w.sha(Path(args.verdicts)), "analysis_rule": plan["analysis_rule"],
            "reviewer": verdicts["reviewer"], "reviewed": verdicts["reviewed"], "items": verdicts["items"],
            "disputed": {"counts": {v: count(d, v) for v in VERDICTS}, "unsure_excluded": count(d, "unsure"),
                         "answers": wilson(count(usable, "answers"), len(usable)),
                         "answers_or_partly": wilson(count(usable, "answers", "partly_answers"), len(usable)),
                         "population": plan["population"]["disputed"]},
            "control": {"counts": {v: count(c, v) for v in VERDICTS},
                        "answers_or_partly": wilson(count(c, "answers", "partly_answers"), len(c)),
                        "answers": wilson(count(c, "answers"), len(c))}}
    out_path.write_text(w.canonical(data), encoding="utf-8", newline="\n")
    out_path.with_suffix(".sha256").write_text(f"{w.sha(out_path)}  {out_path.name}\n", encoding="ascii", newline="\n")
    print(json.dumps({"evidence": out_path.name, "sha256": w.sha(out_path), "disputed": data["disputed"],
                      "control": data["control"]}, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="stage", required=True)
    sub.add_parser("build").set_defaults(fn=build)
    s = sub.add_parser("score")
    s.add_argument("--verdicts", required=True)
    s.set_defaults(fn=score)
    e = sub.add_parser("export")
    e.add_argument("--listing", required=True)
    e.add_argument("--out", required=True)
    e.set_defaults(fn=export)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

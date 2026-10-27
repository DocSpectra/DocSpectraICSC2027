"""Sprint 19 follow-up: the HR-20 review packet (author, October 24) and the export of the author's verdicts.

  python wc_hr20_19.py build     find the lost questions, write the blinded page and the key (outside the page)
  python wc_hr20_19.py build --rebuild    rewrite the page; refuses unless every item keeps its id, so saved verdicts stay valid
  python wc_hr20_19.py export --listing <ArtifactData list out_dir> --out <file>    collect the saved verdicts

A lost question is one of the 111 tier-2 questions with no positive row in a variant's completed qrels v3
(answers-19-pool/<oid>/verify/qrels.jsonl). Each item is one lost (question, variant) pair. It shows the question, its historical
FAQ reference answer, and every C2 gold page (O's verified positives): the original text beside the edited text on
that variant, with removed and added lines marked. For each removed line of 30 or more characters, the variant pages
that now hold it are listed (up to 3, most lines first), so an answer that moved can be followed. Because a moved
answer is often rewritten rather than copied, every page the edited page links to that is new or changed on that
variant is also shown in full (up to 6), with its new lines marked.

Blinding: the variant is identified only by the opaque id in the key, never on the page; items are shown in one
seeded random order. Nothing reads the arm key. Haiku's decisions are not shown. Verdicts: still answers / answer
moved elsewhere / answer lost / unsure, with a note for any verdict but still answers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wc_answers_19 as a19  # noqa: E402
import wc_ragas_15b as b15  # noqa: E402

w17 = a19.w17
wr, rr = w17.wr, w17.rr
ROOT = rr.ROOT
TEMPLATE = HERE / "wc_hr20_19.template.html"
OUT = ROOT / "artifacts/wc-hr20-19"
KEY = OUT / "host-private" / "key.json"
POOL = a19.RUN_ROOT / "answers-19-pool"
PACKET_ID = "wc-hr20-v1"
SEED_LABEL = "icsc2027-hr20-v1"
MIN_CARRY = 30
MAX_ELSEWHERE = 3
MAX_LINKED = 6
LINK = re.compile(r"\]\(([^)\s]+)")
VERDICTS = ("still_answers", "answer_moved", "answer_lost", "unsure")


def norm(line):
    return " ".join(line.split())


def jsonl(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def stem(doc_id):
    return doc_id.rsplit("__", 1)[0]


def linked(rel_path, text, var_by_stem):
    """Variant pages the page links to, in link order: relative and site-absolute .md links, '_index' as 'index'."""
    out = []
    for target in LINK.findall(text):
        t = target.split("#", 1)[0]
        if not t or "://" in t or t.startswith("mailto:"):
            continue
        t = "content" + t if t.startswith("/") else posixpath.join(posixpath.dirname(rel_path), t)
        t = posixpath.normpath(t)
        t = t[:-3] if t.endswith(".md") else t.rstrip("/") + "/_index"
        s = t.replace("/_index", "/index").replace("/", "_").replace("-", "_").replace(".", "_")
        d = var_by_stem.get(s)
        if d and d not in out:
            out.append(d)
    return out


def build(args):
    old = None
    if KEY.exists():
        if not args.rebuild:
            raise w17.Refused("hr20-packet-already-built")
        old = json.loads(KEY.read_text(encoding="utf-8"))
    plan = a19.load_plan()
    questions = {q["question_id"]: q["question"] for q in plan["questions"]["2"]}
    refs = b15.tier2_references()
    vm = rr.read_json(wr.V6)
    base = {p.doc_id: p for p in rr.baseline_pages(wr.dcv.pinned(vm), vm)}
    items = []
    for vdir in sorted(POOL.glob("v-*")):
        oid = vdir.name
        prior = jsonl(vdir / "verify-input/prior/qrels.jsonl")
        gold = {}
        for r in prior:
            if r["relevance"] > 0:
                gold.setdefault(r["query_id"], []).append(r["support_target_id"])
        supported = {r["query_id"] for r in jsonl(vdir / "verify/qrels.jsonl") if r["relevance"] > 0}
        lost = sorted(q for q in questions if q not in supported)
        if not lost:
            continue
        corpus = a19.EXPORT / "variants" / oid / "corpus"
        var = {f.stem: f.read_text(encoding="utf-8") for f in sorted(corpus.glob("*.md"))}
        var_by_stem = {stem(d): d for d in var}
        holders = {}
        for d, t in var.items():
            for l in {norm(x) for x in t.splitlines()}:
                if len(l) >= MIN_CARRY:
                    holders.setdefault(l, set()).add(d)
        for q in lost:
            pages = []
            for d in sorted(gold[q]):
                orig = base[d].text
                edited = var.get(d)
                o_lines = {norm(x) for x in orig.splitlines() if norm(x)}
                e_lines = {norm(x) for x in (edited or "").splitlines() if norm(x)}
                removed = sorted(o_lines - e_lines)
                carried = {}
                for l in removed:
                    for h in holders.get(l, ()):
                        if h != d:
                            carried.setdefault(h, []).append(l)
                top = sorted(carried.items(), key=lambda x: (-len(x[1]), x[0]))[:MAX_ELSEWHERE]
                links = []
                for h in linked(base[d].rel_path, edited or "", var_by_stem):
                    if h == d or (h in base and base[h].text == var[h]):
                        continue
                    h_lines = {norm(x) for x in var[h].splitlines() if norm(x)}
                    b_lines = {norm(x) for x in base[h].text.splitlines() if norm(x)} if h in base else set()
                    links.append({"page": h, "new_page": h not in base, "text": var[h],
                                  "added_lines": sorted(h_lines - b_lines)})
                pages.append({"rel_path": base[d].rel_path, "original": orig, "edited": edited,
                              "removed_lines": removed, "added_lines": sorted(e_lines - o_lines),
                              "elsewhere": [{"page": h, "text": var[h], "carried_lines": sorted(ls)} for h, ls in top],
                              "linked": links[:MAX_LINKED]})
            items.append({"_variant": oid, "_question_id": q, "question": questions[q], "reference": refs[q],
                          "gold": pages})
    rng = random.Random(int(hashlib.sha256(f"{SEED_LABEL}:order".encode()).hexdigest(), 16))
    rng.shuffle(items)
    key, shown = {}, []
    for i, it in enumerate(items, 1):
        iid = f"Q{i:02d}"
        key[iid] = {"variant": it.pop("_variant"), "question_id": it.pop("_question_id"),
                    "gold": [g["rel_path"] for g in it["gold"]]}
        shown.append(dict(it, id=iid))
    if old is not None and old["key"] != key:
        raise w17.Refused("hr20-rebuild-changes-item-ids")
    packet = {"packet_id": PACKET_ID, "plan_sha256": w17.sha(a19.PLAN),
              "items": shown, "verdicts": list(VERDICTS)}
    body = json.dumps(packet, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8").replace("/*PACKET_JSON*/null", body)
    (OUT / "review-page").mkdir(parents=True, exist_ok=True)
    (OUT / "review-page" / "index.html").write_text(page, encoding="utf-8", newline="\n")
    KEY.parent.mkdir(parents=True, exist_ok=True)
    packet_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    earlier = (old.get("earlier_packet_sha256", []) + [old["packet_sha256"]]) if old else []
    KEY.write_text(json.dumps({"packet_id": PACKET_ID, "packet_sha256": packet_sha, "seed_label": SEED_LABEL,
                               "earlier_packet_sha256": earlier, "key": key}, indent=1), encoding="utf-8", newline="\n")
    per_variant = {}
    for k in key.values():
        per_variant[k["variant"]] = per_variant.get(k["variant"], 0) + 1
    print(json.dumps({"page": str(OUT / "review-page" / "index.html"), "items": len(shown),
                      "distinct_questions": len({k["question_id"] for k in key.values()}), "per_variant": per_variant,
                      "gold_pages": sum(len(it["gold"]) for it in shown),
                      "missing_edited": sum(g["edited"] is None for it in shown for g in it["gold"]),
                      "linked_pages": sum(len(g["linked"]) for it in shown for g in it["gold"]),
                      "packet_sha256": packet_sha, "bytes": len(page.encode("utf-8"))}, indent=1))


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
    out = {"schema_version": "docspectra.icsc.wc_hr20.v1", "packet_id": PACKET_ID,
           "packet_sha256": keyfile["packet_sha256"], "reviewer": reviewer,
           "items": len(keyfile["key"]), "reviewed": len(reviews),
           "counts": {v: sum(r["verdict"] == v for r in reviews.values()) for v in VERDICTS},
           "reviews": [dict(r, **keyfile["key"][r["item"]]) for r in sorted(reviews.values(), key=lambda r: r["item"])]}
    Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("reviewer", "items", "reviewed", "counts")}, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="stage", required=True)
    b = sub.add_parser("build")
    b.add_argument("--rebuild", action="store_true")
    b.set_defaults(fn=build)
    e = sub.add_parser("export")
    e.add_argument("--listing", required=True)
    e.add_argument("--out", required=True)
    e.set_defaults(fn=export)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

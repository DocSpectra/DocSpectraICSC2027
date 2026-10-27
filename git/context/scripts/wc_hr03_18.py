"""Sprint 18: the HR-03 factual-review packet (author, October 18 design) and the export of the author's verdicts.

  python wc_hr03_18.py build     sample 20 changed pages per arm, write the blinded page and the key (outside the page)
  python wc_hr03_18.py export --listing <ArtifactData list out_dir>    collect the saved verdicts and apply the stop rule

Sampling (D11e, O6): each arm's changed pages (added or rewritten in its final tree versus O) are grouped by the area
of the admitted session that first changed them (reconciliation-only pages form their own group, ALL). The 20 pages
are allocated across groups in proportion to their size (largest remainder, at least one per group while the total
allows), then drawn uniformly within each group with a seeded RNG. The 40 pages are shown in one seeded random order,
blinded to arm; the key (packet id -> arm, page, sources) is written outside the page.

Each page is shown beside its sources: its own original version when it existed, then up to 3 original pages its new
content came from (wc_refinement_15 provenance rule, host-confirmed sources by shared distinctive units, then declared
sources). Lines are marked: added (not in any source), and per source, removed (not on the variant page). One question
is asked; verdicts are no problem / factual loss / factual error / unsure, with a note for any verdict but no problem.
Stop rule (frozen): 3 or more of an arm's 20 pages with a factual loss or error stops the run before answers.
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
import wc_refine_17 as w17  # noqa: E402

wr, rr, sfb = w17.wr, w17.rr, w17.sfb
ROOT = rr.ROOT
TEMPLATE = HERE / "wc_hr03_18.template.html"
OUT = ROOT / "artifacts/wc-hr03-18"
KEY = OUT / "host-private" / "key.json"
PACKET_ID = "wc-hr03-v1"
SEED_LABEL = "icsc2027-hr03-v1"
PER_ARM = 20
MAX_SOURCES = 3
VERDICTS = ("no_problem", "factual_loss", "factual_error", "unsure")
STOP_AT = 3


def allocate(groups, n):
    total = sum(groups.values())
    if total <= n:
        return dict(groups)
    quota = {g: n * c / total for g, c in groups.items()}
    alloc = {g: int(q) for g, q in quota.items()}
    for g in sorted(groups, key=lambda g: (-(quota[g] - alloc[g]), g))[: n - sum(alloc.values())]:
        alloc[g] += 1
    return alloc


def norm(line):
    return " ".join(line.split())


def build(_args):
    if KEY.exists():
        raise w17.Refused("hr03-packet-already-built")
    plan = wr.load_run_plan(w17.REFINE_PLAN)
    records = w17.wf.check_lock(w17.RECORDS)
    rows = w17.effective(w17.ledger_rows())
    vm = rr.read_json(wr.V6)
    gm = wr.dcv.pinned(vm)
    base_list = rr.baseline_pages(gm, vm)
    base = {p.doc_id: p for p in base_list}
    items, key, allocation = [], {}, {}
    for arm in wr.EDIT_ARMS:
        vctx = wr.variant_context(gm, vm, records["arms"][arm]["final_tree_path"], ledger_rows=rows, arm=arm)
        var = {p.doc_id: p for p in vctx["var_list"]}
        changed = sorted(set(vctx["receipt"]["added"]) | set(vctx["receipt"]["changed"]))
        groups = {}
        for d in changed:
            groups.setdefault(vctx["edit_area"].get(d, wr.RECONCILE_AREA), []).append(d)
        alloc = allocate({g: len(v) for g, v in groups.items()}, PER_ARM)
        allocation[arm] = {"eligible": len(changed), "groups": {g: len(v) for g, v in sorted(groups.items())},
                           "allocated": dict(sorted(alloc.items()))}
        for g in sorted(alloc):
            rng = random.Random(int(hashlib.sha256(f"{SEED_LABEL}:{arm}:{g}".encode()).hexdigest(), 16))
            for d in sorted(rng.sample(sorted(groups[g]), alloc[g])):
                p = var[d]
                deriv = vctx["derived"].get(d, {})
                ranked = sorted(deriv.get("shared_units", {}).items(), key=lambda x: (-x[1], x[0]))
                srcs = ([d] if d in base else []) + [s for s, _ in ranked if s != d]
                srcs += [s for s in deriv.get("declared", []) if s in base and s not in srcs]
                srcs = srcs[: MAX_SOURCES + (1 if d in base else 0)]
                src_texts = [base[s].text for s in srcs]
                src_lines = {norm(x) for t in src_texts for x in t.splitlines() if norm(x)}
                var_lines = {norm(x) for x in p.text.splitlines() if norm(x)}
                items.append({"_arm": arm, "_doc": d, "_group": g, "rel_path": p.rel_path,
                              "kind": "rewritten" if d in base else "new",
                              "text": p.text, "added_lines": sorted({norm(x) for x in p.text.splitlines()
                                                                     if norm(x) and norm(x) not in src_lines}),
                              "sources": [{"rel_path": base[s].rel_path, "own": s == d, "text": base[s].text,
                                           "removed_lines": sorted({norm(x) for x in base[s].text.splitlines()
                                                                    if norm(x) and norm(x) not in var_lines})}
                                          for s in srcs]})
    rng = random.Random(int(hashlib.sha256(f"{SEED_LABEL}:order".encode()).hexdigest(), 16))
    rng.shuffle(items)
    pages = []
    for i, it in enumerate(items, 1):
        pid = f"P{i:02d}"
        key[pid] = {"arm": it.pop("_arm"), "doc_id": it.pop("_doc"), "group": it.pop("_group"), "rel_path": it["rel_path"],
                    "sources": [s["rel_path"] for s in it["sources"]]}
        pages.append(dict(it, id=pid))
    packet = {"packet_id": PACKET_ID, "records_sha256": w17.sha(w17.RECORDS), "pages": pages, "verdicts": list(VERDICTS)}
    body = json.dumps(packet, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8").replace("/*PACKET_JSON*/null", body)
    (OUT / "review-page").mkdir(parents=True, exist_ok=True)
    (OUT / "review-page" / "index.html").write_text(page, encoding="utf-8", newline="\n")
    KEY.parent.mkdir(parents=True, exist_ok=True)
    packet_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    KEY.write_text(json.dumps({"packet_id": PACKET_ID, "packet_sha256": packet_sha, "seed_label": SEED_LABEL,
                               "allocation": allocation, "key": key}, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps({"page": str(OUT / "review-page" / "index.html"), "pages": len(pages), "packet_sha256": packet_sha,
                      "allocation": allocation,
                      "kinds": {k: sum(1 for p in pages if p["kind"] == k) for k in ("new", "rewritten")},
                      "bytes": len(page.encode("utf-8"))}, indent=1))


def export(args):
    keyfile = json.loads(KEY.read_text(encoding="utf-8"))
    reviews, reviewer = {}, None
    for path in sorted(Path(args.listing).rglob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        b = doc["data"] if isinstance(doc.get("data"), dict) else doc
        if b.get("packet_id") != PACKET_ID:
            continue
        if "verdict" in b:
            reviews[b["page"]] = {k: b.get(k) for k in ("page", "verdict", "note", "at")}
        elif "name" in b:
            reviewer = {"name": b["name"], "at": b["at"]}
    per_arm = {}
    for arm in wr.EDIT_ARMS:
        mine = [p for p, k in keyfile["key"].items() if k["arm"] == arm]
        got = [reviews[p] for p in mine if p in reviews]
        bad = sum(r["verdict"] in ("factual_loss", "factual_error") for r in got)
        per_arm[arm] = {"pages": len(mine), "reviewed": len(got),
                        "counts": {v: sum(r["verdict"] == v for r in got) for v in VERDICTS},
                        "loss_or_error": bad, "stop": bad >= STOP_AT}
    out = {"schema_version": "docspectra.icsc.wc_hr03.v1", "packet_id": PACKET_ID,
           "packet_sha256": keyfile["packet_sha256"], "reviewer": reviewer, "stop_rule": f">= {STOP_AT} of {PER_ARM}",
           "per_arm": per_arm, "reviews": [dict(r, **keyfile["key"][r["page"]]) for r in sorted(reviews.values(),
                                                                                                  key=lambda r: r["page"])]}
    dest = Path(args.out)
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"reviewer": reviewer, "per_arm": per_arm}, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="stage", required=True)
    sub.add_parser("build").set_defaults(fn=build)
    e = sub.add_parser("export")
    e.add_argument("--listing", required=True)
    e.add_argument("--out", required=True)
    e.set_defaults(fn=export)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

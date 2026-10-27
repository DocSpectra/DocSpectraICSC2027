"""Deterministically choose source-only, multi-page workflow packets at CIKM anchors."""
import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ROSTER = ROOT / "git/context/evidence/cross-corpus-roster-recovered-v4-2026-09-28.json"
SEED = "icsc2027-complex-task-clusters-v1"
ACTION = re.compile(r"\b(install|configure|deploy|create|build|migrate|upgrade|run|test|verify|troubleshoot|debug|set up|enable|disable|connect|use|manage|choose|select|update|restore|monitor|secure|authenticate|publish)\b", re.I)
CONDITION = re.compile(r"\b(if|when|before|after|must|should|require|only|unless|warning|caution|note|fails?|error|cannot|instead)\b", re.I)
STOP = {"index", "content", "docs", "documentation", "guide", "reference", "pages", "the", "and", "for", "with", "using", "from"}

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def digest(value): return hashlib.sha256(value.encode("utf-8")).hexdigest()
def tokens(value): return {x for x in re.findall(r"[a-z0-9]{3,}", value.casefold()) if x not in STOP}
def text_body(text):
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end >= 0: return text[end+4:].lstrip()
    if text.startswith("+++\n"):
        end = text.find("\n+++", 4)
        if end >= 0: return text[end+4:].lstrip()
    return text
def title(text, name):
    m = re.search(r"(?m)^(?:title|linkTitle)\s*[:=]\s*[\"']?([^\n\"']+)", text[:500])
    if m: return m.group(1).strip()
    m = re.search(r"(?m)^#\s+(.+)$", text[:1500])
    return m.group(1).strip() if m else name.split("__")[0].replace("_", " ")
def excerpt(body, limit=3000):
    if len(body) <= limit: return body
    # Prefer an intact opening procedure and retain a later caution or verification window.
    lead = body[:2000]
    matches = [m.start() for m in re.finditer(r"(?im)^.{0,5}(?:warning|caution|verify|troubleshoot|important|prerequisite)", body[2000:])]
    if matches:
        start = max(2000, 2000 + matches[0] - 120)
        tail = body[start:start+850]
        return lead + "\n[...]\n" + tail
    return body[:limit]

def make_clusters(row, count):
    receipt = json.loads((ROOT / row["export_receipt_path"]).read_text(encoding="utf-8"))
    anchor = next(x for x in receipt["snapshots"] if x["commit"] == row["cikm_anchor_commit"])
    ds = ROOT / anchor["snapshot_path"] / "dataset"
    corpus = ds / "corpus"
    pages = {}
    for path in corpus.glob("*.md"):
        raw = path.read_text(encoding="utf-8")
        body = text_body(raw)
        if len(body.strip()) < 700: continue
        name = path.stem
        heading = title(raw, name)
        ts = tokens(heading + " " + name.split("__")[0])
        actions = min(12, len(ACTION.findall(body[:5000])))
        conditions = min(12, len(CONDITION.findall(body[:5000])))
        lists = min(10, len(re.findall(r"(?m)^\s*(?:[-*]|\d+[.)])\s+", body[:5000])))
        pages[name] = {"path": path, "title": heading, "body": body, "page_sha256": sha(path), "tokens": ts,
                       "score": 2*actions + conditions + lists + min(len(body)//1500, 8)}
    adj = defaultdict(set)
    with (ds / "graph_edges.csv").open(encoding="utf-8", newline="") as handle:
        for edge in csv.DictReader(handle):
            a, b = edge["source"], edge["target"]
            if a in pages and b in pages and a != b:
                adj[a].add(b); adj[b].add(a)
    order = sorted(pages, key=lambda name: (-pages[name]["score"], -len(adj[name]), digest(SEED + "|" + row["corpus_id"] + "|" + name)))
    selected = []
    for name in order:
        if len(selected) >= count: break
        overlap = max([len(pages[name]["tokens"] & pages[old]["tokens"]) / max(1, len(pages[name]["tokens"] | pages[old]["tokens"])) for old in selected], default=0)
        if overlap < 0.6 or len(order) < count * 3: selected.append(name)
    for name in order:
        if len(selected) >= count: break
        if name not in selected: selected.append(name)
    clusters = []
    for rank, seed in enumerate(selected, 1):
        distance = {seed: 0}
        queue = deque([seed])
        while queue:
            current = queue.popleft()
            if distance[current] >= 2: continue
            for other in sorted(adj[current]):
                if other not in distance:
                    distance[other] = distance[current] + 1
                    queue.append(other)
        st = pages[seed]["tokens"]
        def related(other):
            intersection = len(st & pages[other]["tokens"])
            union = max(1, len(st | pages[other]["tokens"]))
            return (0 if other in distance else 1, distance.get(other, 3), -intersection/union,
                    -pages[other]["score"], digest(SEED + "|" + seed + "|" + other))
        others = sorted((x for x in pages if x != seed), key=related)[:7]
        chosen = [seed] + others
        page_rows = [{"page_id": x, "page_sha256": pages[x]["page_sha256"], "title": pages[x]["title"],
                      "graph_undirected_hops_from_seed": distance.get(x), "excerpt": excerpt(pages[x]["body"]),
                      "excerpt_source": "exact_page_substrings_with_literal_gap_marker"} for x in chosen]
        clusters.append({"cluster_id": digest(SEED + "|" + row["corpus_id"] + "|" + seed), "selection_rank": rank,
                         "seed_page_id": seed, "pages": page_rows})
    return anchor, clusters

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-root", type=Path, required=True)
    ap.add_argument("--clusters-per-corpus", type=int, default=16)
    args = ap.parse_args()
    if args.output_root.exists(): raise RuntimeError("output-root-exists")
    roster = json.loads(ROSTER.read_text(encoding="utf-8"))
    args.output_root.mkdir(parents=True)
    inventory = []
    for row in roster["rows"]:
        if not row["paper_member"]: continue
        anchor, clusters = make_clusters(row, args.clusters_per_corpus)
        packet = {"schema_version": "docspectra.icsc.complex_task_source_packet.v1", "corpus_id": row["corpus_id"],
                  "anchor_commit": row["cikm_anchor_commit"], "dataset_inventory_sha256": anchor["dataset_inventory_sha256"],
                  "faq_answers_included": False, "prior_retrieval_or_answer_outputs_included": False,
                  "selection_seed": SEED, "clusters": clusters}
        path = args.output_root / f"{row['corpus_id']}.json"
        path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        inventory.append({"corpus_id": row["corpus_id"], "packet": path.name, "sha256": sha(path),
                          "cluster_count": len(clusters), "page_count_unique": len({p["page_id"] for c in clusters for p in c["pages"]})})
    manifest = {"schema_version": "docspectra.icsc.complex_task_packet_manifest.v1", "roster_sha256": sha(ROSTER),
                "selection_seed": SEED, "clusters_per_corpus": args.clusters_per_corpus, "rows": inventory}
    (args.output_root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"corpora": len(inventory), "clusters": sum(x["cluster_count"] for x in inventory), "manifest_sha256": sha(args.output_root / "manifest.json")}))

if __name__ == "__main__": main()

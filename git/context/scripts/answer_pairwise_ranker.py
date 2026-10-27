"""Blind pairwise answer ranking on localhost Ollama (ranker prompt v1, schema v1).

A separate channel from the boolean rubric judge: packets carry the question, the
rubric element text as reference notes and two answer texts under positions A/B.
They never carry variant ids, arms, file names, verdicts, scores or quote spans.
Each pair runs in both orders; `combine` (rule `pairwise_combine_v1`) turns the
two preferences into one outcome, and `summarize` builds per-cell rankings with
cycles reported, never resolved.

Layout under a run root:
  packets/<packet_id>.json        blind packets (what the model sees, rendered)
  packet-manifest.json            [{packet, sha256, packet_id}] in shuffled run order
  host-private/pair-key.json      packet_id -> cell, pair, order, answer ids
  receipt.json                    prompt/schema hashes, model, digest, options, seed, rule
  calls/<packet_id>/attempt-NN.*  prompt, raw, normalized and record per attempt
  calls/<packet_id>/completion.json   the valid attempt's record
  ranking-summary.json            written by `summarize` after the run
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import random
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

DESIGN = Path(__file__).resolve().parents[1] / "design"
PROMPT = DESIGN / "answer-pairwise-ranker-prompt-v1.txt"
SCHEMA = DESIGN / "answer-pairwise-ranker-schema-v1.json"
MODEL = "qwen3:14b"
EXPECTED_DIGEST = "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8"
URL = "http://127.0.0.1:11434"
OPTIONS = {"temperature": 0, "num_ctx": 32768}
COMBINE_RULE = "pairwise_combine_v1"
PACKET_SCHEMA = "docspectra.icsc.answer_pairwise_ranker.v1.packet"
PACKET_KEYS = {"schema_version", "packet_id", "question", "reference_notes", "answer_a", "answer_b"}
FORBIDDEN_KEYS = {"variant", "variant_id", "arm", "file", "file_name", "path", "verdict", "verdicts", "score",
                  "answer_span", "quote", "exact_quote", "answer_id", "unit_id", "condition", "cell"}


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ packets

def build_pairs(answers, questions, arms_of_interest, seed):
    """Blind pair packets for every pair of arms within each (question, cell), in both orders.

    answers: [{"answer_id", "question_id", "cell", "arm", "text"}]; `arm` may be an opaque id.
    questions: {question_id: {"question", "core_elements": [{"requirement", "acceptance"}, ...]}}.
    Returns (packets, key): packets in run order; key maps packet_id to the unblinding record.
    """
    rng = random.Random(seed)
    rank = {a: i for i, a in enumerate(arms_of_interest)}
    cells = {}
    for a in answers:
        if a["arm"] in rank:
            slot = cells.setdefault((a["question_id"], a["cell"]), {})
            if a["arm"] in slot:
                raise ValueError("duplicate-answer-for-arm-in-cell")
            slot[a["arm"]] = a
    packets, key, used = [], {}, set()

    def fresh():
        while True:
            label = "rk-" + "%016x" % rng.getrandbits(64)
            if label not in used:
                used.add(label)
                return label

    for (qid, cell) in sorted(cells):
        slot = cells[(qid, cell)]
        q = questions[qid]
        notes = [{"requirement": e["requirement"], "acceptance": e["acceptance"]} for e in q["core_elements"]]
        for x, y in itertools.combinations(sorted(slot, key=rank.get), 2):
            pair_id = fresh()
            for order, (first, second) in (("xy", (slot[x], slot[y])), ("yx", (slot[y], slot[x]))):
                pid = fresh()
                packets.append({"schema_version": PACKET_SCHEMA, "packet_id": pid, "question": q["question"],
                                "reference_notes": notes, "answer_a": first["text"], "answer_b": second["text"]})
                key[pid] = {"pair_id": pair_id, "question_id": qid, "cell": cell, "order": order,
                            "x": slot[x]["answer_id"], "y": slot[y]["answer_id"],
                            "a": first["answer_id"], "b": second["answer_id"]}
    rng.shuffle(packets)
    return packets, key


def check_blind(packet, forbidden_strings=()):
    """Raise unless the packet holds only the allowed keys and none of the forbidden strings."""
    def walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k in FORBIDDEN_KEYS:
                    raise ValueError("packet-forbidden-key:" + k)
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
    if set(packet) != PACKET_KEYS:
        raise ValueError("packet-keys:" + ",".join(sorted(set(packet) ^ PACKET_KEYS)))
    for note in packet["reference_notes"]:
        if set(note) != {"requirement", "acceptance"}:
            raise ValueError("reference-note-keys")
    walk(packet)
    text = json.dumps(packet, ensure_ascii=False)
    for s in forbidden_strings:
        if s and s in text:
            raise ValueError("packet-forbidden-string")


def write_packets(root: Path, packets, key, seed, forbidden_strings=()):
    """Write packets, the shuffled manifest, the private key and the run receipt; refuse to overwrite."""
    root = Path(root)
    if (root / "packet-manifest.json").exists():
        raise SystemExit("ranker-packets-already-written")
    manifest = []
    for p in packets:
        check_blind(p, forbidden_strings)
        path = root / "packets" / f"{p['packet_id']}.json"
        write_json(path, p)
        manifest.append({"packet": path.name, "sha256": sha(path), "packet_id": p["packet_id"]})
    write_json(root / "packet-manifest.json", manifest)
    write_json(root / "host-private/pair-key.json", key)
    receipt = {"schema_version": "docspectra.icsc.answer_pairwise_ranker.v1.receipt", "created_utc": now(),
               "prompt": PROMPT.name, "prompt_sha256": sha(PROMPT), "schema": SCHEMA.name, "schema_sha256": sha(SCHEMA),
               "model": MODEL, "expected_digest": EXPECTED_DIGEST, "endpoint": "localhost_ollama_api_chat",
               "options": {**OPTIONS, "think": False, "format": "schema"}, "seed": seed,
               "combine_rule": COMBINE_RULE, "packets": len(manifest),
               "manifest_sha256": sha(root / "packet-manifest.json"),
               "pair_key_sha256": sha(root / "host-private/pair-key.json")}
    write_json(root / "receipt.json", receipt)
    return receipt


def render(packet) -> str:
    notes = "\n".join(f"- {n['requirement']} (Accept if: {n['acceptance']})" for n in packet["reference_notes"])
    values = {"QUESTION": packet["question"], "REFERENCE_NOTES": notes,
              "ANSWER_A": packet["answer_a"], "ANSWER_B": packet["answer_b"]}
    # one pass, so placeholder-like text inside an answer is never substituted
    return re.sub(r"<<(QUESTION|REFERENCE_NOTES|ANSWER_A|ANSWER_B)>>", lambda m: values[m.group(1)],
                  PROMPT.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ transport

class OllamaTransport:
    def __init__(self, url=URL):
        self.url = url

    def digest(self, model):
        with urllib.request.urlopen(self.url + "/api/tags", timeout=10) as response:
            data = json.load(response)
        return next(m["digest"] for m in data["models"] if m["name"] == model)

    def chat(self, body) -> bytes:
        req = urllib.request.Request(self.url + "/api/chat", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=900) as response:
            return response.read()


def validate(data):
    jsonschema.validate(data, json.loads(SCHEMA.read_text(encoding="utf-8")))
    return data


def call(root: Path, entry, transport, retry=False):
    """One packet: skip if complete; otherwise one attempt with digest checks and receipts."""
    root = Path(root)
    packet_path = root / "packets" / entry["packet"]
    if sha(packet_path) != entry["sha256"]:
        raise RuntimeError("packet-hash-drift")
    out = root / "calls" / entry["packet_id"]
    out.mkdir(parents=True, exist_ok=True)
    completion = out / "completion.json"
    if completion.is_file() and read_json(completion)["valid"]:
        return {"packet": entry["packet"], "status": "already_valid"}
    attempt = 1 + sum(p.stem.removeprefix("attempt-").isdigit() for p in out.glob("attempt-*.json"))
    if attempt > 1 and not retry:
        return {"packet": entry["packet"], "status": "previous_failure"}
    packet = read_json(packet_path)
    prompt = render(packet)
    prefix = out / f"attempt-{attempt:02d}"
    prompt_path = prefix.with_suffix(".prompt.txt")
    prompt_path.write_text(prompt, encoding="utf-8")
    before = transport.digest(MODEL)
    if before != EXPECTED_DIGEST:
        raise RuntimeError("model-digest-drift-before")
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}], "stream": False,
            "format": json.loads(SCHEMA.read_text(encoding="utf-8")), "options": dict(OPTIONS),
            "think": False, "keep_alive": "5m"}
    started = now()
    raw_path, norm_path = prefix.with_suffix(".raw.json"), prefix.with_suffix(".normalized.json")
    raw, error, valid = None, None, False
    try:
        raw_bytes = transport.chat(body)
        raw_path.write_bytes(raw_bytes)
        raw = json.loads(raw_bytes)
        data = validate(json.loads(raw["message"]["content"]))
        write_json(norm_path, data)
        valid = True
    except Exception as exc:
        error = f"{type(exc).__name__}:{str(exc)[:300]}"
    after = transport.digest(MODEL)
    if after != EXPECTED_DIGEST:
        raise RuntimeError("model-digest-drift-after")
    record = {"packet": entry["packet"], "packet_id": entry["packet_id"], "attempt": attempt,
              "started_at_utc": started, "finished_at_utc": now(), "model": MODEL,
              "digest_before": before, "digest_after": after, "endpoint": "localhost_ollama_api_chat",
              "options": dict(OPTIONS), "think": False, "combine_rule": COMBINE_RULE,
              "prompt_sha256": sha(prompt_path), "prompt_template_sha256": sha(PROMPT), "schema_sha256": sha(SCHEMA),
              "packet_sha256": sha(packet_path), "raw_response_sha256": sha(raw_path) if raw_path.is_file() else None,
              "usage": {k: raw.get(k) for k in ("prompt_eval_count", "eval_count", "total_duration", "load_duration",
                                                "done_reason") if k in raw} if isinstance(raw, dict) else None,
              "normalized_response_sha256": sha(norm_path) if valid else None,
              "valid": valid, "validation_error": error}
    write_json(prefix.with_suffix(".json"), record)
    if valid:
        write_json(completion, record)
    return {"packet": entry["packet"], "status": "valid" if valid else "failed", "error": error,
            "usage": record["usage"]}


def run(root: Path, transport=None, limit=None, packet=None, retry=False, log=print):
    root = Path(root)
    receipt = read_json(root / "receipt.json")
    if receipt["prompt_sha256"] != sha(PROMPT) or receipt["schema_sha256"] != sha(SCHEMA):
        raise RuntimeError("ranker-prompt-or-schema-drift")
    if receipt["manifest_sha256"] != sha(root / "packet-manifest.json"):
        raise RuntimeError("ranker-manifest-drift")
    transport = transport or OllamaTransport()
    manifest = read_json(root / "packet-manifest.json")
    if packet:
        manifest = [x for x in manifest if x["packet_id"] == packet]
    if limit:
        manifest = manifest[:limit]
    results = []
    for entry in manifest:
        result = call(root, entry, transport, retry)
        results.append(result)
        log(json.dumps(result))
    return results


# ------------------------------------------------------------------ combine and rank

def combine(order_xy, order_yx):
    """`pairwise_combine_v1`: x was A in the first packet, y was A in the second.

    Returns {"outcome": "x" | "y" | "tie", "kind", "position_flip"}. The same winner in both orders
    is a win; a tie in both orders, a winner that changes with order (a position flip), or a winner
    in one order and a tie in the other is a tie.
    """
    first = {"A": "x", "B": "y", "tie": "tie"}[order_xy]
    second = {"A": "y", "B": "x", "tie": "tie"}[order_yx]
    if first == second == "tie":
        return {"outcome": "tie", "kind": "both_tie", "position_flip": False}
    if first == second:
        return {"outcome": first, "kind": "consistent_win", "position_flip": False}
    if "tie" in (first, second):
        return {"outcome": "tie", "kind": "half_tie", "position_flip": False}
    return {"outcome": "tie", "kind": "flip", "position_flip": True}


def find_cycles(items, beats):
    """All simple cycles in the strict-win graph (edges winner -> loser), each listed once."""
    cycles = []
    order = {x: i for i, x in enumerate(items)}

    def dfs(start, node, path, seen):
        for nxt in sorted(beats.get(node, ()), key=order.get):
            if nxt == start:
                cycles.append(list(path))
            elif nxt not in seen and order[nxt] > order[start]:
                dfs(start, nxt, path + [nxt], seen | {nxt})
    for s in items:
        dfs(s, s, [s], {s})
    return cycles


def rank_cell(items, outcomes):
    """items: answer ids; outcomes: [(x, y, outcome)]. Ranking by wins - losses, or None on a cycle."""
    beats = {}
    score = {i: 0 for i in items}
    for x, y, outcome in outcomes:
        if outcome == "x":
            beats.setdefault(x, set()).add(y)
            score[x] += 1
            score[y] -= 1
        elif outcome == "y":
            beats.setdefault(y, set()).add(x)
            score[y] += 1
            score[x] -= 1
    cycles = find_cycles(items, beats)
    ranking = None
    if not cycles:
        ranking = [sorted(g, key=items.index) for _, g in itertools.groupby(
            sorted(items, key=lambda i: -score[i]), key=lambda i: score[i])]
    return {"net_wins": score, "cycles": cycles, "ranking": ranking}


def summarize(root: Path, write=True):
    """Join both orders of every pair (private key) into pair outcomes and per-cell rankings."""
    root = Path(root)
    key = read_json(root / "host-private/pair-key.json")
    manifest = read_json(root / "packet-manifest.json")
    prefs, records = {}, {}
    for entry in manifest:
        done = root / "calls" / entry["packet_id"] / "completion.json"
        attempts = sorted((root / "calls" / entry["packet_id"]).glob("attempt-[0-9][0-9].json"))
        records[entry["packet_id"]] = [read_json(a) for a in attempts]
        if done.exists() and read_json(done)["valid"]:
            rec = read_json(done)
            prefs[entry["packet_id"]] = read_json(root / "calls" / entry["packet_id"] /
                                                  f"attempt-{rec['attempt']:02d}.normalized.json")
    pairs, cells = {}, {}
    for pid, k in key.items():
        pairs.setdefault(k["pair_id"], {"question_id": k["question_id"], "cell": k["cell"],
                                        "x": k["x"], "y": k["y"]})[k["order"]] = pid
    pair_rows = []
    for pair_id, p in sorted(pairs.items(), key=lambda kv: (kv[1]["question_id"], kv[1]["cell"], kv[1]["x"], kv[1]["y"])):
        a, b = prefs.get(p["xy"]), prefs.get(p["yx"])
        row = {"pair_id": pair_id, "question_id": p["question_id"], "cell": p["cell"], "x": p["x"], "y": p["y"],
               "packet_xy": p["xy"], "packet_yx": p["yx"],
               "preference_xy": a and a["preference"], "preference_yx": b and b["preference"],
               "decisive_xy": a and a["decisive_criterion"], "decisive_yx": b and b["decisive_criterion"]}
        row.update(combine(a["preference"], b["preference"]) if a and b else
                   {"outcome": None, "kind": "unavailable", "position_flip": None})
        pair_rows.append(row)
        cells.setdefault((p["question_id"], p["cell"]), []).append(row)
    cell_rows = []
    for (qid, cell), rows in sorted(cells.items()):
        items = []
        for r in rows:
            for i in (r["x"], r["y"]):
                if i not in items:
                    items.append(i)
        available = [(r["x"], r["y"], r["outcome"]) for r in rows if r["outcome"] is not None]
        cell_rows.append({"question_id": qid, "cell": cell, "items": items,
                          "pairs_available": len(available), "pairs": len(rows),
                          **(rank_cell(items, available) if len(available) == len(rows) else
                             {"net_wins": None, "cycles": None, "ranking": None})})
    attempts = [r for rs in records.values() for r in rs]
    usage = {"calls": len(attempts), "valid": sum(r["valid"] for r in attempts),
             "first_attempt_valid": sum(r["valid"] for r in attempts if r["attempt"] == 1),
             "packets": len(manifest), "packets_valid": len(prefs),
             "prompt_eval_count": sum((r["usage"] or {}).get("prompt_eval_count") or 0 for r in attempts),
             "eval_count": sum((r["usage"] or {}).get("eval_count") or 0 for r in attempts),
             "total_duration_ns": sum((r["usage"] or {}).get("total_duration") or 0 for r in attempts)}
    summary = {"schema_version": "docspectra.icsc.answer_pairwise_ranker.v1.summary", "combine_rule": COMBINE_RULE,
               "receipt_sha256": sha(root / "receipt.json"), "usage": usage,
               "position_flips": sum(bool(r["position_flip"]) for r in pair_rows),
               "cells_with_cycles": sum(bool(c["cycles"]) for c in cell_rows),
               "pairs": pair_rows, "cells": cell_rows}
    if write:
        write_json(root / "ranking-summary.json", summary)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--packet")
    ap.add_argument("--retry", action="store_true")
    ap.add_argument("--summarize", action="store_true")
    args = ap.parse_args()
    if args.summarize:
        s = summarize(args.root)
        print(json.dumps({"usage": s["usage"], "position_flips": s["position_flips"],
                          "cells_with_cycles": s["cells_with_cycles"]}, indent=1))
    else:
        run(args.root, limit=args.limit, packet=args.packet, retry=args.retry)


if __name__ == "__main__":
    main()

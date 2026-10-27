"""Blind pointwise answer quality scoring on localhost Ollama (scorer prompt v2, schema v2).

qwen sees one answer per call, with its question and the rubric element text as reference notes,
and returns `errors` then a note and a 1-5 score for each of five measures in priority order.
Packets never carry variant ids, arms, file names, verdicts, scores, spans or any other answer.
A deterministic ranking is derived in code from the scores (rule `lexicographic_margin_v1`),
within each cell, on opaque answer labels only.

Layout under a run root:
  packets/<label>.json            blind packets (what the model sees, rendered)
  packet-manifest.json            [{packet, sha256, packet_id}] in seeded run order
  host-private/answer-key.json    label -> answer id, question, cell, reference-note order
  receipt.json                    prompt/schema hashes, model, digest, options, seeds, rule
  calls/<label>/attempt-NN.*      prompt, raw, normalized and record per attempt
  calls/<label>/completion.json   the valid attempt's record
  scoring-summary.json            written by `summarize`: scores and blind per-cell rankings
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import random
import re
import sys
from pathlib import Path

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parent))
import answer_pairwise_ranker as apr  # noqa: E402  (transport, rank_cell, helpers; unchanged)

DESIGN = Path(__file__).resolve().parents[1] / "design"
PROMPT = DESIGN / "answer-quality-scorer-prompt-v2.txt"
SCHEMA = DESIGN / "answer-quality-scorer-schema-v2.json"
MODEL = apr.MODEL
EXPECTED_DIGEST = apr.EXPECTED_DIGEST
OPTIONS = dict(apr.OPTIONS)  # temperature 0, num_ctx 32768; think false is sent separately
MEASURES = ("correctness", "fit", "completeness", "caveats", "clarity")  # priority order
FIELD_ORDER = ("errors",) + MEASURES
RANK_RULE = "lexicographic_margin_v1"
MARGIN = 1
MAX_ATTEMPTS = 2  # one retry
PACKET_SCHEMA = "docspectra.icsc.answer_quality_scorer.v2.packet"
PACKET_KEYS = {"schema_version", "packet_id", "question", "reference_notes", "answer"}

sha, now, write_json, read_json = apr.sha, apr.now, apr.write_json, apr.read_json


# ------------------------------------------------------------------ packets

def notes_order(question_id, n, notes_seed):
    """Reference-note permutation for one question: identity without a seed, else a seeded
    non-identity shuffle (deterministic for a given seed and question)."""
    order = list(range(n))
    if notes_seed is None or n < 2:
        return order
    rng = random.Random(int(hashlib.sha256(f"{notes_seed}|notes|{question_id}".encode()).hexdigest(), 16))
    while order == list(range(n)):
        rng.shuffle(order)
    return order


def build_packets(answers, questions, seed, notes_seed=None):
    """One blind packet per answer.

    answers: [{"answer_id", "question_id", "cell", "text"}]; any other key (e.g. an arm) is ignored.
    questions: {question_id: {"question", "core_elements": [{"requirement", "acceptance"}, ...]}}.
    Labels and run order depend on `seed` only, so runs with the same seed share labels and order;
    `notes_seed` permutes the reference notes per question (the stability perturbation).
    """
    rng = random.Random(seed)
    ordered = sorted(answers, key=lambda a: a["answer_id"])
    if len({a["answer_id"] for a in ordered}) != len(ordered):
        raise ValueError("duplicate-answer-id")
    used, packets, key = set(), [], {}
    for a in ordered:
        while True:
            label = "aq-" + "%016x" % rng.getrandbits(64)
            if label not in used:
                used.add(label)
                break
        q = questions[a["question_id"]]
        elements = q["core_elements"]
        order = notes_order(a["question_id"], len(elements), notes_seed)
        notes = [{"requirement": elements[i]["requirement"], "acceptance": elements[i]["acceptance"]} for i in order]
        packets.append({"schema_version": PACKET_SCHEMA, "packet_id": label, "question": q["question"],
                        "reference_notes": notes, "answer": a["text"]})
        key[label] = {"answer_id": a["answer_id"], "question_id": a["question_id"], "cell": a["cell"],
                      "notes_order": order}
    rng.shuffle(packets)
    return packets, key


def check_blind(packet, forbidden_strings=()):
    """Raise unless the packet holds only the allowed keys and none of the forbidden strings."""
    if set(packet) != PACKET_KEYS:
        raise ValueError("packet-keys:" + ",".join(sorted(set(packet) ^ PACKET_KEYS)))
    for note in packet["reference_notes"]:
        if set(note) != {"requirement", "acceptance"}:
            raise ValueError("reference-note-keys")
    text = json.dumps(packet, ensure_ascii=False)
    for s in forbidden_strings:
        if s and s in text:
            raise ValueError("packet-forbidden-string")


def write_packets(root: Path, packets, key, seed, notes_seed=None, forbidden_strings=(), run_label=None):
    """Write packets, the manifest, the private key and the run receipt; refuse to overwrite."""
    root = Path(root)
    if (root / "packet-manifest.json").exists():
        raise SystemExit("scorer-packets-already-written")
    manifest = []
    for p in packets:
        check_blind(p, forbidden_strings)
        path = root / "packets" / f"{p['packet_id']}.json"
        write_json(path, p)
        manifest.append({"packet": path.name, "sha256": sha(path), "packet_id": p["packet_id"]})
    write_json(root / "packet-manifest.json", manifest)
    write_json(root / "host-private/answer-key.json", key)
    receipt = {"schema_version": "docspectra.icsc.answer_quality_scorer.v2.receipt", "created_utc": now(),
               "run": run_label, "prompt": PROMPT.name, "prompt_sha256": sha(PROMPT), "schema": SCHEMA.name,
               "schema_sha256": sha(SCHEMA), "module_sha256": sha(__file__), "model": MODEL,
               "expected_digest": EXPECTED_DIGEST, "endpoint": "localhost_ollama_api_chat",
               "options": {**OPTIONS, "think": False, "format": "schema"}, "seed": seed, "notes_seed": notes_seed,
               "rank_rule": RANK_RULE, "margin": MARGIN, "max_attempts": MAX_ATTEMPTS, "packets": len(manifest),
               "manifest_sha256": sha(root / "packet-manifest.json"),
               "answer_key_sha256": sha(root / "host-private/answer-key.json")}
    write_json(root / "receipt.json", receipt)
    return receipt


def render(packet) -> str:
    notes = "\n".join(f"- {n['requirement']} (Accept if: {n['acceptance']})" for n in packet["reference_notes"])
    values = {"QUESTION": packet["question"], "REFERENCE_NOTES": notes, "ANSWER": packet["answer"]}
    # one pass, so placeholder-like text inside an answer is never substituted
    return re.sub(r"<<(QUESTION|REFERENCE_NOTES|ANSWER)>>", lambda m: values[m.group(1)],
                  PROMPT.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ transport

def validate(data):
    jsonschema.validate(data, json.loads(SCHEMA.read_text(encoding="utf-8")))
    return data


def field_order(content: str):
    """Top-level keys, and each measure's keys, in the order the model wrote them."""
    seen = {}

    def hook(pairs):
        obj = dict(pairs)
        obj["__order__"] = [k for k, _ in pairs]
        return obj
    data = json.loads(content, object_pairs_hook=hook)
    seen["top"] = data["__order__"]
    seen["measures"] = {m: data[m]["__order__"] for m in MEASURES if isinstance(data.get(m), dict)}
    ok = tuple(seen["top"]) == FIELD_ORDER and all(v == ["note", "score"] for v in seen["measures"].values())
    return ok, seen


def call(root: Path, entry, transport, retry=False):
    """One packet: skip if complete; otherwise one attempt (at most MAX_ATTEMPTS) with digest checks."""
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
    if attempt > MAX_ATTEMPTS:
        return {"packet": entry["packet"], "status": "unavailable_after_retry"}
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
    raw, error, valid, order_ok, order = None, None, False, None, None
    try:
        raw_bytes = transport.chat(body)
        raw_path.write_bytes(raw_bytes)
        raw = json.loads(raw_bytes)
        content = raw["message"]["content"]
        data = validate(json.loads(content))
        order_ok, order = field_order(content)
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
              "options": dict(OPTIONS), "think": False, "rank_rule": RANK_RULE,
              "prompt_sha256": sha(prompt_path), "prompt_template_sha256": sha(PROMPT), "schema_sha256": sha(SCHEMA),
              "packet_sha256": sha(packet_path), "raw_response_sha256": sha(raw_path) if raw_path.is_file() else None,
              "usage": {k: raw.get(k) for k in ("prompt_eval_count", "eval_count", "total_duration", "load_duration",
                                                "done_reason") if k in raw} if isinstance(raw, dict) else None,
              "normalized_response_sha256": sha(norm_path) if valid else None,
              "field_order_ok": order_ok, "field_order": order, "valid": valid, "validation_error": error}
    write_json(prefix.with_suffix(".json"), record)
    if valid:
        write_json(completion, record)
    return {"packet": entry["packet"], "status": "valid" if valid else "failed", "error": error,
            "usage": record["usage"]}


def run(root: Path, transport=None, limit=None, packet=None, retry_pass=True, log=print):
    """A first pass over every packet, then (by default) one retry pass over the failed ones."""
    root = Path(root)
    receipt = read_json(root / "receipt.json")
    if receipt["prompt_sha256"] != sha(PROMPT) or receipt["schema_sha256"] != sha(SCHEMA):
        raise RuntimeError("scorer-prompt-or-schema-drift")
    if receipt["manifest_sha256"] != sha(root / "packet-manifest.json"):
        raise RuntimeError("scorer-manifest-drift")
    transport = transport or apr.OllamaTransport()
    manifest = read_json(root / "packet-manifest.json")
    if packet:
        manifest = [x for x in manifest if x["packet_id"] == packet]
    if limit:
        manifest = manifest[:limit]
    results = []
    for entry in manifest:
        result = call(root, entry, transport)
        results.append(result)
        log(json.dumps(result))
    if retry_pass:
        for entry in manifest:
            if not (root / "calls" / entry["packet_id"] / "completion.json").is_file():
                result = call(root, entry, transport, retry=True)
                results.append(result)
                log(json.dumps(result))
    return results


# ------------------------------------------------------------------ derived ranking

def compare(sa, sb, margin=MARGIN):
    """`lexicographic_margin_v1`: the first measure (priority order) where |a - b| >= margin decides;
    smaller differences fall through to the next measure; no deciding measure is a tie.
    Returns (outcome "a" | "b" | "tie", deciding measure or None)."""
    for m in MEASURES:
        d = sa[m] - sb[m]
        if abs(d) >= margin:
            return ("a" if d > 0 else "b"), m
    return "tie", None


def rank_cell_lexicographic(items, scores, margin=MARGIN):
    """items: answer labels in one cell; scores: {label: {measure: score}} (missing = unavailable).
    Every pair involving an unavailable answer is unavailable, and the cell then has no ranking."""
    pairs = []
    for x, y in itertools.combinations(items, 2):
        if x in scores and y in scores:
            outcome, measure = compare(scores[x], scores[y], margin)
            pairs.append({"x": x, "y": y, "outcome": {"a": "x", "b": "y", "tie": "tie"}[outcome],
                          "deciding_measure": measure})
        else:
            pairs.append({"x": x, "y": y, "outcome": None, "deciding_measure": None,
                          "unavailable": [i for i in (x, y) if i not in scores]})
    available = [(p["x"], p["y"], p["outcome"]) for p in pairs if p["outcome"] is not None]
    ranked = apr.rank_cell(items, available) if len(available) == len(pairs) else \
        {"net_wins": None, "cycles": None, "ranking": None}
    return {"pairs": pairs, **ranked}


def load_scores(root: Path):
    """{label: normalized output} for packets with a valid completion, plus every attempt record."""
    root = Path(root)
    outputs, records = {}, {}
    for entry in read_json(root / "packet-manifest.json"):
        d = root / "calls" / entry["packet_id"]
        records[entry["packet_id"]] = [read_json(a) for a in sorted(d.glob("attempt-[0-9][0-9].json"))]
        if (d / "completion.json").is_file():
            rec = read_json(d / "completion.json")
            if rec["valid"]:
                outputs[entry["packet_id"]] = read_json(d / f"attempt-{rec['attempt']:02d}.normalized.json")
    return outputs, records


def summarize(root: Path, write=True):
    """Scores and blind per-cell rankings, on labels only (no arm, variant or file name)."""
    root = Path(root)
    key = read_json(root / "host-private/answer-key.json")
    outputs, records = load_scores(root)
    scores = {label: {m: o[m]["score"] for m in MEASURES} for label, o in outputs.items()}
    cells = {}
    for label, k in key.items():
        cells.setdefault((k["question_id"], k["cell"]), []).append(label)
    cell_rows = []
    for (qid, cell), items in sorted(cells.items()):
        cell_rows.append({"question_id": qid, "cell": cell, "items": sorted(items),
                          **rank_cell_lexicographic(sorted(items), scores)})
    attempts = [r for rs in records.values() for r in rs]
    per_packet = [len(rs) for rs in records.values()]
    usage = {"calls": len(attempts), "valid": sum(r["valid"] for r in attempts),
             "first_attempt_valid": sum(r["valid"] for r in attempts if r["attempt"] == 1),
             "packets": len(records), "packets_valid": len(outputs),
             "max_attempts": max(per_packet) if per_packet else 0,
             "field_order_ok": sum(bool(r.get("field_order_ok")) for r in attempts if r["valid"]),
             "prompt_eval_count": sum((r["usage"] or {}).get("prompt_eval_count") or 0 for r in attempts),
             "eval_count": sum((r["usage"] or {}).get("eval_count") or 0 for r in attempts),
             "total_duration_ns": sum((r["usage"] or {}).get("total_duration") or 0 for r in attempts),
             "failures": [{"packet_id": r["packet_id"], "attempt": r["attempt"], "error": r["validation_error"]}
                          for r in attempts if not r["valid"]]}
    summary = {"schema_version": "docspectra.icsc.answer_quality_scorer.v2.summary", "rank_rule": RANK_RULE,
               "margin": MARGIN, "receipt_sha256": sha(root / "receipt.json"), "usage": usage,
               "unavailable": sorted(set(key) - set(scores)), "scores": scores,
               "cells_with_cycles": sum(bool(c["cycles"]) for c in cell_rows), "cells": cell_rows}
    if write:
        write_json(root / "scoring-summary.json", summary)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--summarize", action="store_true")
    args = ap.parse_args()
    if args.summarize:
        s = summarize(args.root)
        print(json.dumps({"usage": s["usage"], "unavailable": s["unavailable"]}, indent=1))
    else:
        run(args.root, limit=args.limit)


if __name__ == "__main__":
    main()

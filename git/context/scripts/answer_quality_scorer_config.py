"""Configurable model call for scorer prompt v2 / schema v2 (Sprint 11f: qwen thinking on).

Everything except the call is reused from `answer_quality_scorer` (11e, unchanged): blind packets,
single-pass rendering, schema validation, field order, `compare`, `rank_cell_lexicographic` and the blind
`summarize`. This module adds a call that takes the model, the `think` setting and the options from a
`ScorerConfig`, saves the thinking trace per attempt, and fails any attempt whose `done_reason` is not
`stop` (a truncated trace or output is a failed attempt, not a parse problem).

The on-disk layout matches 11e, so `aqs.summarize` and `aqs.load_scores` read these runs unchanged;
each attempt also writes `attempt-NN.thinking.txt` (when a trace is returned).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import answer_quality_scorer as aqs

apr = aqs.apr
sha, now, write_json, read_json = aqs.sha, aqs.now, aqs.write_json, aqs.read_json


@dataclass(frozen=True)
class ScorerConfig:
    model: str
    expected_digest: str
    think: bool
    options: dict = field(default_factory=dict)

    def as_dict(self):
        return {"model": self.model, "expected_digest": self.expected_digest, "think": self.think,
                "options": dict(self.options)}


def write_packets(root: Path, packets, key, seed, config: ScorerConfig, notes_seed=None, forbidden_strings=(),
                  run_label=None):
    """Same packets, manifest and private key as 11e; the receipt records this configuration."""
    root = Path(root)
    if (root / "packet-manifest.json").exists():
        raise SystemExit("scorer-packets-already-written")
    manifest = []
    for p in packets:
        aqs.check_blind(p, forbidden_strings)
        path = root / "packets" / f"{p['packet_id']}.json"
        write_json(path, p)
        manifest.append({"packet": path.name, "sha256": sha(path), "packet_id": p["packet_id"]})
    write_json(root / "packet-manifest.json", manifest)
    write_json(root / "host-private/answer-key.json", key)
    receipt = {"schema_version": "docspectra.icsc.answer_quality_scorer_config.v1.receipt", "created_utc": now(),
               "run": run_label, "prompt": aqs.PROMPT.name, "prompt_sha256": sha(aqs.PROMPT),
               "schema": aqs.SCHEMA.name, "schema_sha256": sha(aqs.SCHEMA), "module_sha256": sha(__file__),
               "base_module_sha256": sha(aqs.__file__), **config.as_dict(),
               "endpoint": "localhost_ollama_api_chat", "format": "schema", "seed": seed, "notes_seed": notes_seed,
               "rank_rule": aqs.RANK_RULE, "margin": aqs.MARGIN, "max_attempts": aqs.MAX_ATTEMPTS,
               "packets": len(manifest), "manifest_sha256": sha(root / "packet-manifest.json"),
               "answer_key_sha256": sha(root / "host-private/answer-key.json")}
    write_json(root / "receipt.json", receipt)
    return receipt


def request_body(prompt: str, config: ScorerConfig):
    return {"model": config.model, "messages": [{"role": "user", "content": prompt}], "stream": False,
            "format": json.loads(aqs.SCHEMA.read_text(encoding="utf-8")), "options": dict(config.options),
            "think": config.think, "keep_alive": "5m"}


def call(root: Path, entry, transport, config: ScorerConfig, retry=False):
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
    if attempt > aqs.MAX_ATTEMPTS:
        return {"packet": entry["packet"], "status": "unavailable_after_retry"}
    prompt = aqs.render(read_json(packet_path))
    prefix = out / f"attempt-{attempt:02d}"
    prompt_path = prefix.with_suffix(".prompt.txt")
    prompt_path.write_text(prompt, encoding="utf-8")
    before = transport.digest(config.model)
    if before != config.expected_digest:
        raise RuntimeError("model-digest-drift-before")
    started = now()
    raw_path, norm_path = prefix.with_suffix(".raw.json"), prefix.with_suffix(".normalized.json")
    think_path = prefix.with_suffix(".thinking.txt")
    raw, error, valid, order_ok, order, thinking = None, None, False, None, None, None
    try:
        raw_bytes = transport.chat(request_body(prompt, config))
        raw_path.write_bytes(raw_bytes)
        raw = json.loads(raw_bytes)
        thinking = raw["message"].get("thinking")
        if thinking is not None:
            think_path.write_text(thinking, encoding="utf-8")
        if raw.get("done_reason") != "stop":
            raise ValueError(f"done_reason:{raw.get('done_reason')}")
        content = raw["message"]["content"]
        data = aqs.validate(json.loads(content))
        order_ok, order = aqs.field_order(content)
        write_json(norm_path, data)
        valid = True
    except Exception as exc:
        error = f"{type(exc).__name__}:{str(exc)[:300]}"
    after = transport.digest(config.model)
    if after != config.expected_digest:
        raise RuntimeError("model-digest-drift-after")
    record = {"packet": entry["packet"], "packet_id": entry["packet_id"], "attempt": attempt,
              "started_at_utc": started, "finished_at_utc": now(), "model": config.model,
              "digest_before": before, "digest_after": after, "endpoint": "localhost_ollama_api_chat",
              "options": dict(config.options), "think": config.think, "rank_rule": aqs.RANK_RULE,
              "prompt_sha256": sha(prompt_path), "prompt_template_sha256": sha(aqs.PROMPT),
              "schema_sha256": sha(aqs.SCHEMA), "packet_sha256": sha(packet_path),
              "raw_response_sha256": sha(raw_path) if raw_path.is_file() else None,
              "usage": {k: raw.get(k) for k in ("prompt_eval_count", "eval_count", "total_duration", "load_duration",
                                                "done_reason") if k in raw} if isinstance(raw, dict) else None,
              "thinking_present": thinking is not None, "thinking_chars": len(thinking) if thinking else 0,
              "thinking_sha256": sha(think_path) if thinking is not None else None,
              "normalized_response_sha256": sha(norm_path) if valid else None,
              "field_order_ok": order_ok, "field_order": order, "valid": valid, "validation_error": error}
    write_json(prefix.with_suffix(".json"), record)
    if valid:
        write_json(completion, record)
    return {"packet": entry["packet"], "status": "valid" if valid else "failed", "error": error,
            "usage": record["usage"], "thinking_chars": record["thinking_chars"]}


def check_receipt(root: Path, config: ScorerConfig):
    receipt = read_json(Path(root) / "receipt.json")
    if receipt["prompt_sha256"] != sha(aqs.PROMPT) or receipt["schema_sha256"] != sha(aqs.SCHEMA):
        raise RuntimeError("scorer-prompt-or-schema-drift")
    if receipt["manifest_sha256"] != sha(Path(root) / "packet-manifest.json"):
        raise RuntimeError("scorer-manifest-drift")
    if {k: receipt.get(k) for k in config.as_dict()} != config.as_dict():
        raise RuntimeError("scorer-config-drift")
    return receipt


def run(root: Path, config: ScorerConfig, transport=None, limit=None, retry_pass=True, log=print):
    """A first pass over every packet, then (by default) one retry pass over the failed ones."""
    root = Path(root)
    check_receipt(root, config)
    transport = transport or apr.OllamaTransport()
    manifest = read_json(root / "packet-manifest.json")
    if limit:
        manifest = manifest[:limit]
    results = []
    for entry in manifest:
        result = call(root, entry, transport, config)
        results.append(result)
        log(json.dumps(result))
    if retry_pass:
        for entry in manifest:
            if not (root / "calls" / entry["packet_id"] / "completion.json").is_file():
                result = call(root, entry, transport, config, retry=True)
                results.append(result)
                log(json.dumps(result))
    return results


def thinking_stats(root: Path):
    """Per-attempt trace presence and length (characters; Ollama's eval_count covers trace and output)."""
    recs = [read_json(p) for p in sorted(Path(root).glob("calls/*/attempt-[0-9][0-9].json"))]
    chars = sorted(r.get("thinking_chars", 0) for r in recs)
    evals = sorted((r["usage"] or {}).get("eval_count") or 0 for r in recs)
    med = lambda v: (v[(len(v) - 1) // 2] + v[len(v) // 2]) / 2 if v else None  # noqa: E731
    return {"attempts": len(recs), "thinking_present": sum(bool(r.get("thinking_present")) for r in recs),
            "thinking_missing": [r["packet_id"] for r in recs if not r.get("thinking_present")],
            "done_reason": {d: sum((r["usage"] or {}).get("done_reason") == d for r in recs)
                            for d in sorted({(r["usage"] or {}).get("done_reason") for r in recs}, key=str)},
            "thinking_chars_median": med(chars), "thinking_chars_max": chars[-1] if chars else None,
            "eval_count_median": med(evals), "eval_count_max": evals[-1] if evals else None}

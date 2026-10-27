"""Scorer prompt v2 / schema v2 with gpt-6-luna as the scorer (Sprint 11h), through the OpenAI Responses API.

Everything except the call is reused from `answer_quality_scorer` (11e, unchanged): blind packets, single-pass
rendering, local schema validation, field order, `compare`, `rank_cell_lexicographic` and the blind `summarize`.
This module adds a Luna call:
  - strict structured output with schema v2 as is (probe, October 8: accepted, field order kept);
  - `CostLedger` (agent_model_provider, unchanged) reserves each call's worst case before the request and stops
    at the hard limit; a failed call keeps its reservation (treated as possibly billable);
  - a response whose status is not `completed` fails the attempt (like `done_reason: length` in 11f);
  - the response id, the model string the API reports, the service tier and the token usage (with reasoning
    tokens) are recorded per attempt; the API key is never written.

The on-disk layout matches 11e, so `aqs.summarize` and `aqs.load_scores` read these runs unchanged. Usage is
mapped onto the 11e fields: prompt_eval_count = input tokens, eval_count = output tokens (reasoning included),
total_duration = wall time of the request in ns.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import answer_quality_scorer as aqs
import agent_model_provider as amp

sha, now, write_json, read_json = aqs.sha, aqs.now, aqs.write_json, aqs.read_json
WORKSPACE = Path(__file__).resolve().parents[3]
SDK = WORKSPACE / ".tools" / "openai-sdk"
SDK_VERSION = "3.20.0"
SCHEMA_NAME = "answer_quality_scorer_v2"


@dataclass(frozen=True)
class LunaConfig:
    model: str = "gpt-6-luna"
    reasoning_effort: str = "medium"
    max_output_tokens: int = 4000
    input_cap: int = 8000          # reservation basis; a reported input above it is recorded as an anomaly
    limit_usd: float = 3.00        # author's hard stop for the whole sprint (one shared ledger)
    authorization: str = ""

    def as_dict(self):
        return {"model": self.model, "reasoning_effort": self.reasoning_effort,
                "max_output_tokens": self.max_output_tokens, "input_cap": self.input_cap,
                "limit_usd": self.limit_usd, "authorization": self.authorization,
                "store": False, "service_tier": "default", "format": "json_schema strict (schema v2 as is)"}


def reservation(config: LunaConfig) -> float:
    """The ledger's worst-case reservation for one call (input at the cache-write price, full output cap)."""
    L = amp.CostLedger
    return config.input_cap * max(L.INPUT, L.CACHE_WRITE) + config.max_output_tokens * L.OUTPUT


def request_body(prompt: str, config: LunaConfig):
    return {"model": config.model, "input": [{"role": "user", "content": prompt}],
            "text": {"format": {"type": "json_schema", "name": SCHEMA_NAME, "strict": True,
                                "schema": json.loads(aqs.SCHEMA.read_text(encoding="utf-8"))}},
            "reasoning": {"effort": config.reasoning_effort}, "max_output_tokens": config.max_output_tokens,
            "store": False, "service_tier": "default"}


class LunaTransport:
    """Wraps an OpenAI client (or a fake with `.responses.create`) and the shared cost ledger."""

    def __init__(self, config: LunaConfig, ledger_path: Path, client=None):
        if not config.authorization:
            raise RuntimeError("luna-authorization-label-required")
        self.config = config
        self.ledger = amp.CostLedger(ledger_path.parent, ledger_path, config.limit_usd)
        if client is None:
            if not os.environ.get("OPENAI_API_KEY"):
                raise RuntimeError("OPENAI_API_KEY-unavailable")
            if SDK.is_dir() and str(SDK) not in sys.path:
                sys.path.insert(0, str(SDK))
            import importlib.metadata
            from openai import OpenAI
            if importlib.metadata.version("openai") != SDK_VERSION:
                raise RuntimeError("openai-sdk-version-mismatch")
            client = OpenAI(max_retries=0, timeout=300)
        self.client = client

    def respond(self, body):
        """Reserve, call, record usage. Returns (response dict, reserved usd, reported usd, wall ns)."""
        reserved = self.ledger.admit(self.config.input_cap, self.config.max_output_tokens)
        t0 = time.perf_counter_ns()
        try:
            response = self.client.responses.create(**body)
        except Exception:
            self.ledger.record({"event": "failed_possible_billable", "projected_usd": reserved})
            raise
        wall = time.perf_counter_ns() - t0
        data = response.model_dump() if hasattr(response, "model_dump") else dict(response)
        u = data.get("usage") or {}
        cached = (u.get("input_tokens_details") or {}).get("cached_tokens") or 0
        inp, out = u.get("input_tokens"), u.get("output_tokens")
        reported = None if inp is None or out is None else \
            (inp - cached) * amp.CostLedger.INPUT + cached * amp.CostLedger.CACHED + out * amp.CostLedger.OUTPUT
        self.ledger.record({"event": "reported_usage", "response_id": data.get("id"), "usage": u,
                            "reported_usd": reported, "input_over_cap": (inp or 0) > self.config.input_cap})
        return data, reserved, reported, wall


def output_text(data):
    """Concatenated output_text parts of the message items; refusals are errors."""
    parts = []
    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue
        for c in item.get("content") or []:
            if c.get("type") == "refusal":
                raise ValueError("refusal")
            if c.get("type") == "output_text":
                parts.append(c.get("text") or "")
    if not parts:
        raise ValueError("no-output-text")
    return "".join(parts)


def write_packets(root: Path, packets, key, seed, config: LunaConfig, notes_seed=None, forbidden_strings=(),
                  run_label=None):
    """Same packets, manifest and private key as 11e; the receipt records the Luna configuration."""
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
    receipt = {"schema_version": "docspectra.icsc.answer_quality_scorer_luna.v1.receipt", "created_utc": now(),
               "run": run_label, "prompt": aqs.PROMPT.name, "prompt_sha256": sha(aqs.PROMPT),
               "schema": aqs.SCHEMA.name, "schema_sha256": sha(aqs.SCHEMA), "module_sha256": sha(__file__),
               "base_module_sha256": sha(aqs.__file__), **config.as_dict(),
               "endpoint": "openai_responses_api", "seed": seed, "notes_seed": notes_seed,
               "rank_rule": aqs.RANK_RULE, "margin": aqs.MARGIN, "max_attempts": aqs.MAX_ATTEMPTS,
               "packets": len(manifest), "manifest_sha256": sha(root / "packet-manifest.json"),
               "answer_key_sha256": sha(root / "host-private/answer-key.json")}
    write_json(root / "receipt.json", receipt)
    return receipt


def call(root: Path, entry, transport: LunaTransport, retry=False):
    """One packet: skip if complete; otherwise one attempt (at most MAX_ATTEMPTS)."""
    root = Path(root)
    config = transport.config
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
    started = now()
    raw_path, norm_path = prefix.with_suffix(".raw.json"), prefix.with_suffix(".normalized.json")
    data, error, valid, order_ok, order, reserved, reported, wall = None, None, False, None, None, None, None, None
    try:
        data, reserved, reported, wall = transport.respond(request_body(prompt, config))
        raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
        if data.get("status") != "completed":
            reason = (data.get("incomplete_details") or {}).get("reason")
            raise ValueError(f"status:{data.get('status')}:{reason}")
        content = output_text(data)
        parsed = aqs.validate(json.loads(content))
        order_ok, order = aqs.field_order(content)
        write_json(norm_path, parsed)
        valid = True
    except RuntimeError as exc:
        if str(exc) == "estimated-charge-stop":
            prompt_path.unlink()
            raise
        error = f"{type(exc).__name__}:{str(exc)[:300]}"
    except Exception as exc:
        error = f"{type(exc).__name__}:{str(exc)[:300]}"
    u = (data or {}).get("usage") or {}
    record = {"packet": entry["packet"], "packet_id": entry["packet_id"], "attempt": attempt,
              "started_at_utc": started, "finished_at_utc": now(), "model": config.model,
              "model_reported": (data or {}).get("model"), "response_id": (data or {}).get("id"),
              "service_tier": (data or {}).get("service_tier"), "response_status": (data or {}).get("status"),
              "endpoint": "openai_responses_api", "config": config.as_dict(), "rank_rule": aqs.RANK_RULE,
              "prompt_sha256": sha(prompt_path), "prompt_template_sha256": sha(aqs.PROMPT),
              "schema_sha256": sha(aqs.SCHEMA), "packet_sha256": sha(packet_path),
              "raw_response_sha256": sha(raw_path) if raw_path.is_file() else None,
              "usage": {"prompt_eval_count": u.get("input_tokens"), "eval_count": u.get("output_tokens"),
                        "reasoning_tokens": (u.get("output_tokens_details") or {}).get("reasoning_tokens"),
                        "cached_tokens": (u.get("input_tokens_details") or {}).get("cached_tokens"),
                        "total_duration": wall} if data is not None else None,
              "cost_reserved_usd": reserved, "cost_reported_usd": reported,
              "normalized_response_sha256": sha(norm_path) if valid else None,
              "field_order_ok": order_ok, "field_order": order, "valid": valid, "validation_error": error}
    write_json(prefix.with_suffix(".json"), record)
    if valid:
        write_json(completion, record)
    return {"packet": entry["packet"], "status": "valid" if valid else "failed", "error": error,
            "usage": record["usage"], "cost_reported_usd": reported}


def check_receipt(root: Path, config: LunaConfig):
    receipt = read_json(Path(root) / "receipt.json")
    if receipt["prompt_sha256"] != sha(aqs.PROMPT) or receipt["schema_sha256"] != sha(aqs.SCHEMA):
        raise RuntimeError("scorer-prompt-or-schema-drift")
    if receipt["manifest_sha256"] != sha(Path(root) / "packet-manifest.json"):
        raise RuntimeError("scorer-manifest-drift")
    if {k: receipt.get(k) for k in config.as_dict()} != config.as_dict():
        raise RuntimeError("scorer-config-drift")
    return receipt


def run(root: Path, transport: LunaTransport, limit=None, retry_pass=True, log=print):
    """A first pass over every packet, then (by default) one retry pass over the failed ones."""
    root = Path(root)
    check_receipt(root, transport.config)
    manifest = read_json(root / "packet-manifest.json")
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


def cost_stats(root: Path):
    recs = [read_json(p) for p in sorted(Path(root).glob("calls/*/attempt-[0-9][0-9].json"))]
    rep = [r["cost_reported_usd"] for r in recs if r.get("cost_reported_usd") is not None]
    reason = sorted((r["usage"] or {}).get("reasoning_tokens") or 0 for r in recs)
    med = lambda v: (v[(len(v) - 1) // 2] + v[len(v) // 2]) / 2 if v else None  # noqa: E731
    return {"attempts": len(recs), "reserved_usd": sum(r.get("cost_reserved_usd") or 0 for r in recs),
            "reported_usd": sum(rep), "reported_missing": len(recs) - len(rep),
            "models_reported": sorted({str(r.get("model_reported")) for r in recs}),
            "service_tiers": sorted({str(r.get("service_tier")) for r in recs}),
            "reasoning_tokens_median": med(reason), "reasoning_tokens_max": reason[-1] if reason else None}

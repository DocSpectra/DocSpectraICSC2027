"""ICSC documentation-loop bridge to the RA model-turn contract.

No retries, redirects, proxy routing, or corpus access are implemented here.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[3]
LOCAL_SDK = WORKSPACE / ".tools" / "openai-sdk"
if LOCAL_SDK.is_dir():
    sys.path.insert(0, str(LOCAL_SDK))
sys.path.insert(0, str(WORKSPACE / "git" / "RetrievalArena"))
from retrieval_arena.model_turn import ResponseTurn, TurnRequest, canonical, digest, request_payload  # noqa: E402


class LocalResponses:
    uses_functions = True
    def __init__(self, model: str, pinned_digest: str, output_dir: Path,
                 endpoint: str = "http://127.0.0.1:11434"):
        if endpoint != "http://127.0.0.1:11434":
            raise ValueError("local-endpoint-required")
        self.model, self.pinned_digest = model, pinned_digest
        self.output_dir = output_dir
        self.endpoint = endpoint
        self.events = output_dir / "provider-events.jsonl"
        self.turn = ResponseTurn(self.create, self.emit, self.write_raw)

    def request(self, route, data=None):
        if route not in {"/api/tags", "/v1/responses"}:
            raise ValueError("local-route-not-allowed")
        req = urllib.request.Request(self.endpoint + route, data=canonical(data) if data is not None else None,
                                     headers={"Content-Type": "application/json"},
                                     method="POST" if data is not None else "GET")
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, request, fp, code, msg, headers, newurl):
                raise RuntimeError("local-redirect-refused")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(req, timeout=300) as response:
            return json.load(response)

    def observed_digest(self):
        matches = [m for m in self.request("/api/tags")["models"] if m.get("name") == self.model]
        if len(matches) != 1 or matches[0].get("digest") != self.pinned_digest:
            raise RuntimeError("agent-model-identity-mismatch")
        return self.pinned_digest

    def create(self, **payload):
        return self.request("/v1/responses", payload)

    def emit(self, event):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    def write_raw(self, response):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / ("response-" + digest(response) + ".json")
        path.write_bytes(canonical(response))
        return str(path)

    def complete(self, messages, allowed_actions):
        self.observed_digest()
        cap = 1600 if "answer" in allowed_actions else 256
        try:
            action, metadata = self.turn.complete(TurnRequest(messages, allowed_actions, "ollama", self.model,
                self.endpoint + "/v1/responses", cap, {"temperature": 0}))
        finally:
            self.observed_digest()
        metadata["model_digest"] = self.pinned_digest
        metadata["prompt_eval_count"] = metadata["usage"]["input"]
        metadata["eval_count"] = metadata["usage"]["output"]
        return action, metadata


class CostLedger:
    """Conservative per-call admission; a failure reserves its full projection."""
    LIMIT = 40.00
    INPUT = 0.10 / 1_000_000
    CACHED = 0.01 / 1_000_000
    CACHE_WRITE = 0.125 / 1_000_000
    OUTPUT = 0.50 / 1_000_000

    def __init__(self, output_dir: Path, ledger_path: Path | None = None,
                 limit_usd: float = 0.50):
        if not 0 < limit_usd <= self.LIMIT:
            raise ValueError("invalid-cost-limit")
        self.limit_usd = limit_usd
        self.path = ledger_path if ledger_path is not None else output_dir / "cost-ledger.jsonl"
        self.reserved = 0.0
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event.get("event") == "reserved_before_request":
                    self.reserved += event["projected_usd"]

    def projection(self, input_cap: int, output_cap: int) -> float:
        if input_cap > 272000 or input_cap < 1 or output_cap < 1:
            raise RuntimeError("price-tier-unqualified")
        return input_cap * max(self.INPUT, self.CACHE_WRITE) + output_cap * self.OUTPUT

    def input_cap(self, campaign_calls: int, output_cap: int) -> int:
        if campaign_calls < 1 or output_cap < 1:
            raise ValueError("invalid-campaign-budget")
        available = self.limit_usd / campaign_calls - output_cap * self.OUTPUT
        cap = int(available / max(self.INPUT, self.CACHE_WRITE))
        if cap < 1:
            raise RuntimeError("estimated-charge-stop")
        return min(cap, 272000)

    def configured_input_cap(self, campaign_calls: int, output_cap: int,
                             requested_cap: int | None = None) -> int:
        affordable = self.input_cap(campaign_calls, output_cap)
        if requested_cap is None:
            return affordable
        if type(requested_cap) is not int or not 0 < requested_cap <= affordable:
            raise ValueError("invalid-requested-input-cap")
        return requested_cap

    def check_remaining(self, input_cap: int, output_cap: int, remaining_calls: int):
        if remaining_calls < 1 or self.reserved + self.projection(input_cap, output_cap) * remaining_calls > self.limit_usd + 1e-12:
            raise RuntimeError("estimated-charge-stop")

    def admit(self, input_cap: int, output_cap: int):
        # Cache writes can cost more than uncached input below the 272K tier change.
        projected = self.projection(input_cap, output_cap)
        if self.reserved + projected > self.limit_usd + 1e-12:
            raise RuntimeError("estimated-charge-stop")
        self.reserved += projected
        self.record({"event": "reserved_before_request", "projected_usd": projected,
                     "cumulative_reserved_usd": self.reserved})
        return projected

    def record(self, row):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


class OpenAIResponses(LocalResponses):
    def __init__(self, output_dir: Path, *, synthetic: bool = False, outbound_review_id: str | None = None,
                 ledger_path: Path | None = None, future_calls_after_episode: int = 0,
                 campaign_calls: int = 128, answer_cap: int = 1800,
                 estimated_charge_stop_usd: float = 0.50,
                 input_cap: int | None = None):
        if future_calls_after_episode < 0:
            raise ValueError("negative-future-calls")
        if answer_cap < 256:
            raise ValueError("invalid-answer-cap")
        if not synthetic and not outbound_review_id:
            raise RuntimeError("outbound-data-review-required")
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY-unavailable")
        try:
            import importlib.metadata
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("pinned-openai-sdk-unavailable") from exc
        if importlib.metadata.version("openai") != "3.20.0":
            raise RuntimeError("openai-sdk-version-mismatch")
        self.model = "gpt-6-luna"
        self.output_dir = output_dir
        self.endpoint = "https://api.openai.com"
        self.events = output_dir / "provider-events.jsonl"
        self.ledger = CostLedger(output_dir, ledger_path, estimated_charge_stop_usd)
        self.answer_cap = answer_cap
        self.max_input_tokens = self.ledger.configured_input_cap(campaign_calls, answer_cap, input_cap)
        self.context_tokens = 1_050_000
        self.future_calls_after_episode = future_calls_after_episode
        self.ledger.record({"event": "outbound_gate", "synthetic": synthetic,
                            "outbound_review_id": outbound_review_id})
        self.client = OpenAI(max_retries=0, timeout=300)
        self.turn = ResponseTurn(self.create, self.emit, self.write_raw)

    def observed_digest(self):
        return None

    def create(self, **payload):
        return self.client.responses.create(**payload)

    def count_input_tokens(self, messages, allowed_actions):
        cap = self.answer_cap if "answer" in allowed_actions else 256
        payload = request_payload(TurnRequest(messages, allowed_actions, "openai", self.model,
                                              self.endpoint + "/v1/responses", cap))
        count_payload = {key: payload[key] for key in
                         ("model", "input", "tools", "tool_choice", "reasoning", "parallel_tool_calls")}
        count = self.client.responses.input_tokens.count(**count_payload).input_tokens
        if type(count) is not int or count < 1:
            raise RuntimeError("input-token-count-unavailable")
        self.ledger.record({"event": "input_token_count", "request_sha256": digest(payload),
                            "input_tokens": count, "input_cap": self.max_input_tokens})
        return count

    def complete(self, messages, allowed_actions):
        cap = self.answer_cap if "answer" in allowed_actions else 256
        count = getattr(self, "admitted_input_tokens", None)
        if type(count) is not int or count > self.max_input_tokens or count + cap > self.context_tokens:
            raise RuntimeError("input-token-admission-required")
        self.ledger.check_remaining(self.max_input_tokens, self.answer_cap,
                                    getattr(self, "remaining_calls", 1) + self.future_calls_after_episode)
        projected = self.ledger.admit(self.max_input_tokens, cap)
        self.admitted_input_tokens = None
        try:
            action, metadata = self.turn.complete(TurnRequest(messages, allowed_actions, "openai", self.model,
                self.endpoint + "/v1/responses", cap))
        except Exception:
            self.ledger.record({"event": "failed_possible_billable", "projected_usd": projected})
            raise
        usage = metadata["usage"]
        if usage["input"] is None or usage["output"] is None or metadata["service_tier"] not in {"default", "standard"}:
            self.ledger.record({"event": "usage_or_tier_unavailable", "response_sha256": metadata["response_sha256"]})
            raise RuntimeError("usage-or-service-tier-unavailable")
        if usage["cache_write"] is None:
            # Some endpoints do not report cache writes separately. Keep this
            # missing field explicit; the conservative reservation still applies.
            pass
        self.ledger.record({"event": "reported_usage", "usage": usage,
                            "response_sha256": metadata["response_sha256"]})
        metadata["prompt_eval_count"] = usage["input"]
        metadata["eval_count"] = usage["output"]
        return action, metadata

"""Sprint 11: map a `codex exec --json` event stream onto the run manifest's limit keys.

Pure bookkeeping, no I/O. The event names follow the documented exec JSONL
stream (thread.started, turn.started, item.*, turn.completed with usage,
turn.failed, error). They are not yet checked against a live Codex 0.161.0
stream; the first authorized session is that check.

Mapping onto the Sprint 10 limit keys:
  model_calls  turns started (Codex reports turns, not individual requests)
  tool_calls   completed command, file-change, MCP and web-search items
  tokens       input + output tokens from turn.completed usage (cached input is
               part of input); known only at the end of a turn
  edits        net changed paths against the baseline, from host snapshots
  wall_seconds enforced by the host watchdog, not by events
A scripted editor declares checkpoint boundaries with turn.completed and emits
no turn.started, so it makes no model calls.
"""
from __future__ import annotations

import json
import re

TOOL_ITEMS = ("command_execution", "file_change", "mcp_tool_call", "web_search")
USAGE_LIMIT = re.compile(r"usage limit|rate limit|too many requests|\b429\b|quota", re.I)
TOOL_UNAVAILABLE = re.compile(r"\b(unavailable|disabled)\b", re.I)


class LimitReached(Exception):
    def __init__(self, key, value):
        super().__init__(f"{key}={value}")
        self.key, self.value = key, value


class EventCounter:
    def __init__(self, limits: dict):
        self.limits = limits
        self.model_calls = self.tool_calls = self.tokens = self.turns_completed = 0
        self.thread_id = None
        self.errors: list[str] = []
        self.infrastructure_stop = None
        self.usage: list[dict] = []
        self.unknown_lines = 0

    def _cap(self, key, value):
        cap = self.limits.get(key)
        if isinstance(cap, (int, float)) and not isinstance(cap, bool) and value > cap:
            raise LimitReached(key, value)

    def check(self, edits=None, elapsed=None):
        for key, value in (("model_calls", self.model_calls), ("tool_calls", self.tool_calls),
                           ("tokens", self.tokens), ("edits", edits), ("wall_seconds", elapsed)):
            if value is not None:
                self._cap(key, value)

    def feed_line(self, line: str):
        """Parse one stdout line; returns the event dict or None for a non-JSON line."""
        try:
            event = json.loads(line)
        except ValueError:
            self.unknown_lines += 1
            return None
        if not isinstance(event, dict):
            self.unknown_lines += 1
            return None
        return self.feed(event)

    def feed(self, event: dict):
        kind = event.get("type")
        if kind == "thread.started":
            self.thread_id = event.get("thread_id")
        elif kind == "turn.started":
            self.model_calls += 1
        elif kind == "item.completed":
            item = event.get("item") or {}
            kind_item = item.get("type") or item.get("item_type")
            if kind_item in TOOL_ITEMS:
                self.tool_calls += 1
            elif kind_item == "error":  # a Codex-side tool or runtime error reported as an item
                message = str(item.get("message") or "")
                self.errors.append(message[:500])
                if TOOL_UNAVAILABLE.search(message) and self.infrastructure_stop is None:
                    self.infrastructure_stop = "tool_unavailable"
        elif kind == "turn.completed":
            self.turns_completed += 1
            usage = event.get("usage") or {}
            self.usage.append(usage)
            self.tokens += int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
        elif kind in ("turn.failed", "error"):
            message = (event.get("error") or {}).get("message") if kind == "turn.failed" else event.get("message")
            message = str(message or "")
            self.errors.append(message[:500])
            if USAGE_LIMIT.search(message) and self.infrastructure_stop is None:
                self.infrastructure_stop = "usage_limit"
        self.check()
        return event

    def summary(self):
        return {"model_calls": self.model_calls, "tool_calls": self.tool_calls, "tokens": self.tokens,
                "turns_completed": self.turns_completed, "thread_id": self.thread_id, "errors": self.errors,
                "infrastructure_stop": self.infrastructure_stop, "usage": self.usage,
                "non_json_lines": self.unknown_lines}

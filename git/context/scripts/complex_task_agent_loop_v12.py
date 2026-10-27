"""ICSC development agent loop over a frozen documentation snapshot.

Only corpus pages, their links, and one question enter this process. Human labels,
rubrics, previous answers, and scores are deliberately absent from its API.
"""

from __future__ import annotations

import csv
import argparse
import hashlib
import json
import re
import math
from collections import Counter
import time
from datetime import datetime, timezone
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def line_ranges(numbers: set[int]) -> list[list[int]]:
    ranges: list[list[int]] = []
    for number in sorted(numbers):
        if ranges and number == ranges[-1][1] + 1:
            ranges[-1][1] = number
        else:
            ranges.append([number, number])
    return ranges


def search_terms(value: str) -> list[str]:
    stop = {"a", "an", "and", "at", "by", "do", "for", "from", "how", "i",
            "in", "of", "on", "or", "the", "to", "with"}
    words = re.findall(r"[a-z0-9]+", value.casefold())
    normalized = (word[:-3] if word.endswith("ing") and len(word) > 6 else
                  word[:-1] if word.endswith("s") and len(word) > 4 else word
                  for word in words if word not in stop)
    return list(normalized)


class Model(Protocol):
    def complete(self, messages: list[dict[str, str]], allowed_actions: list[str]) -> tuple[dict[str, Any], dict[str, Any]]: ...


class OllamaModel:
    """Local development adapter; verify the model digest around every call."""

    def __init__(self, name: str, digest: str, endpoint: str = "http://127.0.0.1:11434",
                 context_tokens: int = 8192, action_tokens: int = 256, answer_tokens: int = 1600,
                 num_gpu: int | None = None):
        self.name, self.digest, self.endpoint = name, digest, endpoint.rstrip("/")
        self.context_tokens, self.action_tokens, self.answer_tokens = context_tokens, action_tokens, answer_tokens
        self.num_gpu = num_gpu

    def _request(self, route: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else canonical(payload)
        request = urllib.request.Request(self.endpoint + route, data=data,
                                         headers={"Content-Type": "application/json"},
                                         method="GET" if data is None else "POST")
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read(1200).decode("utf-8", errors="replace")
            raise RuntimeError(f"ollama-http-{exc.code}:{detail}") from exc

    def observed_digest(self) -> str:
        matches = [model for model in self._request("/api/tags")["models"] if model["name"] == self.name]
        if len(matches) != 1 or matches[0]["digest"] != self.digest:
            raise RuntimeError("agent-model-identity-mismatch")
        return self.digest

    def complete(self, messages: list[dict[str, str]], allowed_actions: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
        self.observed_digest()
        cap = self.answer_tokens if "answer" in allowed_actions else self.action_tokens
        options = {"temperature": 0, "seed": 7, "num_ctx": self.context_tokens,
                   "num_predict": cap, "top_k": 1, "top_p": 1, "repeat_penalty": 1}
        if self.num_gpu is not None:
            options["num_gpu"] = self.num_gpu
        try:
            observation = json.loads(messages[-1]["content"].removeprefix("Observation: "))
            observed_ranges = observation.get("read_ranges", {})
            follow_edges = observation.get("follow_edges", {})
        except (ValueError, KeyError, TypeError):
            observed_ranges = {}
            follow_edges = {}
        citation_choices = []
        for doc_id, ranges in observed_ranges.items():
            for start, end in ranges:
                citation_choices.append({"type": "object", "properties": {
                    "doc_id": {"type": "string", "const": doc_id},
                    "start_line": {"type": "integer", "minimum": start, "maximum": end},
                    "end_line": {"type": "integer", "minimum": start, "maximum": end}},
                    "required": ["doc_id", "start_line", "end_line"],
                    "additionalProperties": False})
        citation_item = (citation_choices[0] if len(citation_choices) == 1 else
                         {"oneOf": citation_choices} if citation_choices else
                         {"type": "object"})
        fields = {"query": {"type": "string"}, "doc_id": {"type": "string"},
                  "source_doc_id": {"type": "string"}, "start_line": {"type": "integer"},
                  "line_count": {"type": "integer"}, "text": {"type": "string"},
                  "citations": {"type": "array", "items": citation_item, "minItems": 1}}
        keys = {"search": ["query"], "read": ["doc_id", "start_line", "line_count"],
                "follow": ["source_doc_id", "doc_id", "start_line", "line_count"],
                "note": ["text", "citations"], "answer": ["text", "citations"]}
        branches = []
        for name in allowed_actions:
            if name == "follow":
                for source, targets in follow_edges.items():
                    for target in targets:
                        branches.append({"type": "object", "properties": {
                            "action": {"type": "string", "const": "follow"},
                            "source_doc_id": {"type": "string", "const": source},
                            "doc_id": {"type": "string", "const": target},
                            "start_line": fields["start_line"], "line_count": fields["line_count"]},
                            "required": ["action", *keys[name]], "additionalProperties": False})
            else:
                branches.append({"type": "object", "properties": {"action": {"type": "string", "const": name},
                                **{key: fields[key] for key in keys[name]}},
                                "required": ["action", *keys[name]], "additionalProperties": False})
        action_format = branches[0] if len(branches) == 1 else {"oneOf": branches}
        response = self._request("/api/chat", {
            "model": self.name, "stream": False, "think": False, "format": action_format,
            "messages": messages, "options": options,
        })
        self.observed_digest()
        diagnostic = {"response_model": response.get("model"), "done": response.get("done"),
                      "done_reason": response.get("done_reason"), "error": response.get("error"),
                      "prompt_eval_count": response.get("prompt_eval_count"),
                      "eval_count": response.get("eval_count"), "num_predict": cap,
                      "response_sha256": sha_bytes(canonical(response)),
                      "response_keys": sorted(response),
                      "partial_content": response.get("message", {}).get("content")}
        if response.get("model") != self.name or response.get("done_reason") != "stop":
            raise RuntimeError("agent-model-response-incomplete-or-wrong-identity:" + json.dumps(diagnostic))
        raw = response.get("message", {}).get("content")
        if not isinstance(raw, str):
            raise RuntimeError("agent-model-response-content")
        try:
            action = json.loads(raw)
        except json.JSONDecodeError:
            action = {"action": "invalid", "raw": raw}
        return action, {"model": self.name, "model_digest": self.digest,
                        "done_reason": response.get("done_reason"), "num_predict": cap,
                        "raw_content": raw,
                        "raw_sha256": sha_bytes(raw.encode("utf-8")),
                        "prompt_eval_count": response.get("prompt_eval_count"),
                        "eval_count": response.get("eval_count"),
                        "total_duration_ns": response.get("total_duration")}


class Documentation:
    def __init__(self, dataset: Path):
        corpus_root = dataset / "corpus"
        graph_path = dataset / "graph_edges.csv"
        if not corpus_root.is_dir() or not graph_path.is_file():
            raise ValueError("dataset-missing-corpus-or-graph")
        paths = sorted(corpus_root.glob("*.md"))
        if not paths:
            raise ValueError("empty-corpus")
        self.lines = {p.stem: p.read_text(encoding="utf-8").splitlines() for p in paths}
        self.file_hashes = {p.stem: sha_bytes(p.read_bytes()) for p in paths}
        self.term_counts: dict[str, Counter[str]] = {}
        self.term_doc_freq: Counter[str] = Counter()
        self.doc_lengths: dict[str, int] = {}
        for doc_id, lines in self.lines.items():
            counts = Counter(search_terms("\n".join(lines) + "\n" + (doc_id.replace("_", " ") + " ") * 3))
            self.term_counts[doc_id] = counts
            self.doc_lengths[doc_id] = sum(counts.values())
            self.term_doc_freq.update(counts.keys())
        self.avg_doc_length = sum(self.doc_lengths.values()) / len(self.doc_lengths)
        self.graph_sha256 = sha_bytes(graph_path.read_bytes())
        self.links = {doc_id: set() for doc_id in self.lines}
        with graph_path.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                source, target = row["source"], row["target"]
                if source in self.lines and target in self.lines and source != target:
                    self.links[source].add(target)

    def search(self, query: str, *, max_files: int = 6, lines_per_file: int = 2) -> dict[str, Any]:
        words = list(dict.fromkeys(search_terms(query)))
        if not words or len(query) > 160:
            return {"query": query, "hits": [], "error": "empty-or-long-query"}
        doc_count = len(self.lines)
        idf = {word: math.log(1 + (doc_count - self.term_doc_freq[word] + 0.5) /
                             (self.term_doc_freq[word] + 0.5)) for word in words}
        scored = []
        for doc_id, counts in self.term_counts.items():
            length = self.doc_lengths[doc_id]
            score = sum(idf[word] * counts[word] * 2.2 /
                        (counts[word] + 1.2 * (0.25 + 0.75 * length / self.avg_doc_length))
                        for word in words if counts[word])
            if score > 0:
                scored.append((score, doc_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        hits = []
        for score, doc_id in scored[:max_files]:
            line_hits = []
            for i, value in enumerate(self.lines[doc_id], 1):
                found = sorted(set(search_terms(value)).intersection(words))
                if found:
                    line_hits.append((sum(idf[word] for word in found), i, found, value))
            line_hits.sort(key=lambda item: (-item[0], item[1]))
            hits.append({"doc_id": doc_id, "score": round(score, 6),
                         "matches": [{"line": i, "text": value[:160], "matched_terms": found}
                                     for _, i, found, value in line_hits[:lines_per_file]]})
        return {"query": query, "query_terms": words, "match_mode": "bm25_document_v1",
                "hits": hits, "matching_files": len(scored)}

    def read(self, doc_id: str, start_line: int, line_count: int, *, max_lines: int = 80,
             max_chars: int = 2400) -> dict[str, Any]:
        if doc_id not in self.lines or type(start_line) is not int or type(line_count) is not int:
            raise ValueError("invalid-read-target")
        if start_line < 1 or line_count < 1 or line_count > max_lines:
            raise ValueError("invalid-read-range")
        lines = self.lines[doc_id]
        if start_line > len(lines):
            raise ValueError("read-start-past-end")
        shown = []
        used = 0
        for number in range(start_line, min(len(lines), start_line + line_count - 1) + 1):
            content = lines[number - 1]
            remaining = max_chars - used
            if remaining <= 0:
                break
            shown_text = content[:remaining]
            shown.append({"line": number, "text": shown_text, "truncated_line": len(shown_text) < len(content)})
            used += len(shown_text)
            if len(shown_text) < len(content):
                break
        return {"doc_id": doc_id, "lines": shown, "total_lines": len(lines),
                "next_line": shown[-1]["line"] + 1 if shown[-1]["line"] < len(lines) else None,
                "outgoing_links": sorted(self.links[doc_id]), "file_sha256": self.file_hashes[doc_id]}


class AgentLoop:
    def __init__(self, docs: Documentation, model: Model, prompt: str, *, follow_links: bool,
                 search_budget: int = 4, read_budget: int = 8, call_budget: int = 16,
                 max_history_bytes: int = 60000, wall_seconds: int = 900,
                 note_budget: int = 4, max_note_chars: int = 1200,
                 min_distinct_searches: int = 1,
                 checkpoint=None):
        if min(search_budget, read_budget, call_budget, max_history_bytes) < 1:
            raise ValueError("nonpositive-budget")
        self.docs, self.model, self.prompt = docs, model, prompt
        self.follow_links = follow_links
        self.search_budget, self.read_budget, self.call_budget = search_budget, read_budget, call_budget
        self.max_history_bytes = max_history_bytes
        self.wall_seconds, self.checkpoint = wall_seconds, checkpoint
        self.note_budget, self.max_note_chars = note_budget, max_note_chars
        self.min_distinct_searches = min_distinct_searches
        if min_distinct_searches < 1 or min_distinct_searches > search_budget:
            raise ValueError("invalid-min-distinct-searches")

    def run(self, question_id: str, question: str) -> dict[str, Any]:
        tool_schema = ("Return exactly one compact JSON object per turn. No markdown or explanation. Actions: "
                       "search: {action, query}; read: {action, doc_id, start_line, line_count}; "
                       "follow: {action, source_doc_id, doc_id, start_line, line_count}; "
                       "note: {action, text, citations:[{doc_id,start_line,end_line}]}; "
                       "answer: {action, text, citations:[{doc_id,start_line,end_line}]}. "
                       "For read and follow, start_line must be at least 1 and line_count is 1 to 80; choose 20 to 60 lines. "
                       "Only use actions in allowed_actions. Search snippets are leads and cannot be cited. "
                       "Cite lines you actually read, using read_ranges in the observation. "
                       "Notes persist across turns. Use note to append a concise cited finding or answer draft. "
                       "For search/read/follow, return only the short action object and no analysis or answer text.")
        messages = [{"role": "system", "content": self.prompt + "\n\n" + tool_schema},
                    {"role": "user", "content": "Task ID: " + question_id + "\nTask: " + question +
                     "\nChoose one action. Your tools and remaining budgets are in each observation."}]
        searched: set[str] = set()
        searched_queries: set[str] = set()
        observed_lines: dict[str, set[int]] = {}
        searches = reads = invalid = 0
        steps = []
        notes = []
        answer = None
        terminal = None
        started = time.monotonic()
        for call in range(1, self.call_budget + 1):
            if time.monotonic() - started > self.wall_seconds:
                terminal = "wall_time_budget_exhausted"
                break
            allowed = []
            if searches < self.search_budget:
                allowed.append("search")
            if reads < self.read_budget and (searched or observed_lines):
                allowed.append("read")
            if self.follow_links and reads < self.read_budget and any(self.docs.links[doc] for doc in observed_lines):
                allowed.append("follow")
            if observed_lines and (len(searched_queries) >= self.min_distinct_searches or
                                   searches >= self.search_budget):
                allowed.append("answer")
            if observed_lines and len(notes) < self.note_budget:
                allowed.append("note")
            if not allowed:
                terminal = "tool_budget_exhausted_without_evidence"
                break
            observation = {"allowed_actions": allowed, "budget": {
                "searches_remaining": self.search_budget - searches,
                "distinct_searches_needed_for_answer": max(0, self.min_distinct_searches - len(searched_queries)),
                "reads_remaining": self.read_budget - reads,
                "model_calls_remaining": self.call_budget - call + 1},
                "search_hit_pages": sorted(searched), "read_pages": sorted(observed_lines),
                "read_ranges": {doc_id: line_ranges(lines) for doc_id, lines in observed_lines.items()},
                "follow_edges": ({doc_id: sorted(self.docs.links[doc_id]) for doc_id in observed_lines
                                  if self.docs.links[doc_id]} if self.follow_links else {}),
                "notes": notes}
            messages.append({"role": "user", "content": "Observation: " + json.dumps(observation, sort_keys=True)})
            if len(canonical(messages)) > self.max_history_bytes:
                # Discard redundant control/search chatter; retain every exact read/follow result.
                for index in range(2, len(messages) - 1):
                    content = messages[index]["content"]
                    if (content.startswith("Observation: ") or
                            content.startswith('Tool result: {"hits"') or
                            (content.startswith("Tool result: ") and '"hits":' in content) or
                            (messages[index]["role"] == "assistant" and '"action": "search"' in content)):
                        if self.checkpoint:
                            self.checkpoint({"event": "history_compaction", "call": call,
                                             "message_sha256": sha_bytes(content.encode("utf-8")),
                                             "message_kind": "search_or_control"})
                        messages[index] = {"role": messages[index]["role"], "content": "[Earlier search/control message compacted]"}
                    if len(canonical(messages)) <= self.max_history_bytes:
                        break
            if len(canonical(messages)) > self.max_history_bytes and notes:
                for index in range(2, len(messages) - 1):
                    content = messages[index]["content"]
                    if content.startswith("Tool result: ") and '"route":' in content:
                        if self.checkpoint:
                            self.checkpoint({"event": "history_compaction", "call": call,
                                             "message_sha256": sha_bytes(content.encode("utf-8")),
                                             "message_kind": "read_cached_with_notes"})
                        messages[index] = {"role": "user", "content": "[Earlier read is in the exact trace; cited notes persist.]"}
                    if len(canonical(messages)) <= self.max_history_bytes:
                        break
            if self.checkpoint:
                self.checkpoint({"event": "call_start", "call": call, "history_sha256": sha_bytes(canonical(messages))})
            if len(canonical(messages)) > self.max_history_bytes:
                terminal = "history_budget_exceeded"
                break
            try:
                action, metadata = self.model.complete(messages, allowed)
            except Exception as exc:
                detail = str(exc)
                terminal = ("model_length" if '"done_reason": "length"' in detail else
                            "model_resource_error" if any(x in detail.lower() for x in ("memory", "resource", "503", "500")) else
                            "model_incomplete" if '"done": false' in detail else
                            "model_api_error")
                steps.append({"call": call, "action": None, "result": {"error": f"{type(exc).__name__}:{exc}"},
                              "model": {}, "history_sha256": sha_bytes(canonical(messages))})
                if self.checkpoint:
                    self.checkpoint({"event": "call_end", "step": steps[-1], "terminal": terminal})
                break
            if not isinstance(action, dict):
                action = {"action": "invalid"}
            messages.append({"role": "assistant", "content": json.dumps(action, sort_keys=True, ensure_ascii=False)})
            name = action.get("action")
            result: dict[str, Any]
            try:
                if name not in allowed:
                    raise ValueError("action-not-allowed")
                expected = {"search": {"action", "query"},
                            "read": {"action", "doc_id", "start_line", "line_count"},
                            "follow": {"action", "source_doc_id", "doc_id", "start_line", "line_count"},
                            "note": {"action", "text", "citations"},
                            "answer": {"action", "text", "citations"}}
                if set(action) != expected[name]:
                    raise ValueError("action-shape")
                if name == "search":
                    query = action.get("query")
                    if not isinstance(query, str):
                        raise ValueError("search-query-required")
                    result = self.docs.search(query)
                    searches += 1
                    searched_queries.add(query.casefold().strip())
                    searched.update(hit["doc_id"] for hit in result["hits"])
                elif name in ("read", "follow"):
                    doc_id = action.get("doc_id")
                    if not isinstance(doc_id, str):
                        raise ValueError("read-doc-id-required")
                    if name == "read" and doc_id not in searched and doc_id not in observed_lines:
                        raise ValueError("page-not-offered-by-search")
                    if name == "follow":
                        source = action.get("source_doc_id")
                        if source not in observed_lines or doc_id not in self.docs.links[source]:
                            raise ValueError("link-not-offered-by-read-page")
                    result = self.docs.read(doc_id, action.get("start_line"), action.get("line_count"))
                    already = sum(line["line"] in observed_lines.get(doc_id, set()) for line in result["lines"])
                    reads += 1
                    observed_lines.setdefault(doc_id, set()).update(line["line"] for line in result["lines"])
                    result["previously_observed_lines"] = already
                    result["newly_observed_lines"] = len(result["lines"]) - already
                    result["route"] = name
                    if name == "follow":
                        result["source_doc_id"] = source
                elif name in ("note", "answer"):
                    if not observed_lines:
                        raise ValueError("answer-before-reading")
                    text = action.get("text")
                    citations = action.get("citations")
                    if not isinstance(text, str) or not text.strip() or not isinstance(citations, list) or not citations:
                        raise ValueError("answer-fields-required")
                    if name == "note" and len(text) > self.max_note_chars:
                        raise ValueError("note-too-long")
                    for citation in citations:
                        if not isinstance(citation, dict) or set(citation) != {"doc_id", "start_line", "end_line"}:
                            raise ValueError("citation-shape")
                        doc_id = citation["doc_id"]
                        start, end = citation["start_line"], citation["end_line"]
                        if (doc_id not in observed_lines or type(start) is not int or type(end) is not int
                                or start > end or any(line not in observed_lines[doc_id] for line in range(start, end + 1))):
                            raise ValueError("citation-not-observed")
                    if name == "note":
                        notes.append({"text": text, "citations": citations})
                        result = {"accepted": True, "note_index": len(notes),
                                  "citation_count": len(citations)}
                    else:
                        answer = {"text": text, "citations": citations}
                        result = {"accepted": True, "citation_count": len(citations)}
                        terminal = "answer"
                else:
                    raise ValueError("unknown-action")
            except (ValueError, TypeError, KeyError) as exc:
                invalid += 1
                result = {"error": str(exc)}
                if str(exc) == "citation-not-observed":
                    result["read_ranges"] = {doc_id: line_ranges(lines) for doc_id, lines in observed_lines.items()}
                if invalid >= 3:
                    terminal = "invalid_action_limit"
            steps.append({"call": call, "action": action, "result": result, "model": metadata,
                          "history_sha256": sha_bytes(canonical(messages))})
            if self.checkpoint:
                self.checkpoint({"event": "call_end", "step": steps[-1], "terminal": terminal})
            messages.append({"role": "user", "content": "Tool result: " + json.dumps(result, sort_keys=True, ensure_ascii=False)})
            if terminal:
                break
        if terminal is None:
            terminal = "model_call_budget_exhausted"
        return {"schema_version": "docspectra.icsc.complex_task_agent_trace.v2",
                "question_id": question_id, "question": question,
                "question_sha256": sha_bytes(question.encode("utf-8")),
                "prompt_sha256": sha_bytes(self.prompt.encode("utf-8")),
                "graph_sha256": self.docs.graph_sha256,
                "corpus_inventory_sha256": sha_bytes(canonical(self.docs.file_hashes)),
                "follow_links": self.follow_links,
                "budgets": {"searches": self.search_budget, "reads": self.read_budget,
                            "model_calls": self.call_budget, "history_bytes": self.max_history_bytes,
                            "wall_seconds": self.wall_seconds, "notes": self.note_budget,
                            "note_chars": self.max_note_chars,
                            "min_distinct_searches": self.min_distinct_searches},
                "steps": steps, "terminal": terminal, "answer": answer,
                "usage": {"searches": searches, "reads": reads, "model_calls": len(steps),
                          "invalid_actions": invalid, "notes": len(notes),
                          "distinct_searches": len(searched_queries),
                          "prompt_tokens_reported": sum((s["model"].get("prompt_eval_count") or 0) for s in steps),
                          "output_tokens_reported": sum((s["model"].get("eval_count") or 0) for s in steps),
                          "elapsed_seconds": round(time.monotonic() - started, 3)},
                "notes": notes}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one documentation-only agent episode")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--question-id", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gemma4:12b")
    parser.add_argument("--digest", required=True)
    parser.add_argument("--follow-links", action="store_true")
    parser.add_argument("--context-tokens", type=int, default=8192)
    parser.add_argument("--action-tokens", type=int, default=256)
    parser.add_argument("--answer-tokens", type=int, default=1600)
    parser.add_argument("--search-budget", type=int, default=4)
    parser.add_argument("--read-budget", type=int, default=8)
    parser.add_argument("--call-budget", type=int, default=16)
    parser.add_argument("--wall-seconds", type=int, default=900)
    parser.add_argument("--history-bytes", type=int, default=60000)
    parser.add_argument("--num-gpu", type=int)
    parser.add_argument("--note-budget", type=int, default=4)
    parser.add_argument("--note-chars", type=int, default=1200)
    parser.add_argument("--min-distinct-searches", type=int, default=1)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("output-already-exists")
    docs = Documentation(args.dataset)
    prompt = args.prompt.read_text(encoding="utf-8")
    model = OllamaModel(args.model, args.digest, context_tokens=args.context_tokens,
                        action_tokens=args.action_tokens, answer_tokens=args.answer_tokens,
                        num_gpu=args.num_gpu)
    event_path = args.output.with_suffix(".events.jsonl")
    if event_path.exists():
        raise SystemExit("event-log-already-exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def checkpoint(event):
        event["utc"] = datetime.now(timezone.utc).isoformat()
        with event_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            stream.flush()
    checkpoint({"event": "episode_start", "output": str(args.output.resolve()),
                "dataset": str(args.dataset.resolve()), "model": args.model, "digest": args.digest})
    try:
        model.observed_digest()
    except Exception as exc:
        checkpoint({"event": "episode_error", "error": f"{type(exc).__name__}:{exc}"})
        raise
    loop = AgentLoop(docs, model, prompt, follow_links=args.follow_links,
                     search_budget=args.search_budget, read_budget=args.read_budget,
                     call_budget=args.call_budget, max_history_bytes=args.history_bytes,
                     wall_seconds=args.wall_seconds, note_budget=args.note_budget,
                     max_note_chars=args.note_chars,
                     min_distinct_searches=args.min_distinct_searches, checkpoint=checkpoint)
    trace = loop.run(args.question_id, args.question)
    trace["dataset_path"] = str(args.dataset.resolve())
    trace["model_policy"] = {"backend": "ollama", "model": args.model, "digest": args.digest,
                             "temperature": 0, "seed": 7, "num_ctx": model.context_tokens,
                             "action_tokens": model.action_tokens, "answer_tokens": model.answer_tokens,
                             "num_gpu": model.num_gpu}
    args.output.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checkpoint({"event": "episode_end", "terminal": trace["terminal"],
                "trace_sha256": sha_bytes(args.output.read_bytes())})


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import posixpath
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class Page:
    source_path: Path
    rel_path: str
    doc_id: str
    text: str
    is_faq: bool
    link_targets: tuple[str, ...] = ()


def stable_doc_id(rel_path: str) -> str:
    """Create a stable RetrievalArena-safe document id from a relative path."""
    normalized = rel_path.replace("\\", "/").strip("/").lower()
    stem = re.sub(r"\.[a-z0-9]+$", "", normalized)
    stem = re.sub(r"[^a-z0-9]+", "_", stem).strip("_") or "doc"
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
    return f"{stem}__{digest}"


PAGE_SUFFIXES = {".md", ".markdown", ".mdx", ".rst", ".txt", ".html", ".htm"}
MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdx"}
RST_SUFFIXES = {".rst", ".txt"}
HTML_SUFFIXES = {".html", ".htm"}


def _decode_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _rst_to_markdown(text: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if line.strip() and len(next_line) >= max(3, len(line.strip())) and set(next_line) <= set("=-~^#*+"):
            level = "#" if next_line.startswith("=") else "##"
            output.append(f"{level} {line.strip()}")
            index += 2
            continue
        converted = re.sub(r":(?:doc|ref|source):`([^`<]+?)\s*<([^`>]+)>`", r"[\1](\2)", line)
        converted = re.sub(r":(?:doc|ref|source):`([^`]+)`", r"[\1](\1)", converted)
        converted = re.sub(r"`([^`<]+?)\s*<([^`>]+)>`_", r"[\1](\2)", converted)
        converted = re.sub(r"`([^`]+)`_", r"\1", converted)
        output.append(converted)
        index += 1
    return "\n".join(output).strip() + "\n"


def _rst_link_targets(text: str) -> tuple[str, ...]:
    targets: list[str] = []
    for pattern in (
        r":(?:doc|ref|source):`[^`<]+?<([^`>]+)>`",
        r":(?:doc|ref|source):`([^`]+)`",
        r"`[^`<]+?<([^`>]+)>`_",
        r"^\s*\.\.\s+_[^:]+:\s+(\S+)",
    ):
        targets.extend(match.group(1).strip() for match in re.finditer(pattern, text, flags=re.MULTILINE))
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        if not re.match(r"^\s*\.\.\s+toctree::", lines[index]):
            index += 1
            continue
        index += 1
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()
            if not stripped:
                index += 1
                continue
            if not line.startswith((" ", "\t")):
                break
            if stripped.startswith(":"):
                index += 1
                continue
            titled = re.search(r"<([^>]+)>", stripped)
            target = titled.group(1).strip() if titled else stripped.split()[0]
            if target and not target.startswith(("http://", "https://")):
                targets.append(target)
            index += 1
    return tuple(targets)


class _MarkdownishHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.current: list[str] = []
        self.heading_level: int | None = None
        self.skip_depth = 0
        self.link_stack: list[str | None] = []
        self.link_targets: list[str] = []
        self.title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value for key, value in attrs}
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "title":
            self._in_title = True
        if tag in {"nav", "header", "footer", "form"}:
            self.skip_depth += 1
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self.heading_level = int(tag[1])
            return
        if tag in {"p", "li", "dt", "dd", "pre", "blockquote"}:
            self._flush()
            if tag == "li":
                self.current.append("- ")
            return
        if tag == "br":
            self.current.append("\n")
        if tag == "a":
            href = attr.get("href")
            self.link_stack.append(href)
            if href:
                self.link_targets.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "nav", "header", "footer", "form"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag == "title":
            self._in_title = False
        if re.fullmatch(r"h[1-6]", tag):
            text = " ".join("".join(self.current).split())
            if text:
                level = "#" * max(1, self.heading_level or 2)
                self.blocks.append(f"{level} {text}")
            self.current = []
            self.heading_level = None
            return
        if tag in {"p", "li", "dt", "dd", "pre", "blockquote"}:
            self._flush()
        if tag == "a" and self.link_stack:
            self.link_stack.pop()

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        value = html.unescape(data)
        if self._in_title:
            title = " ".join(value.split())
            if title:
                self.title = title
        if not value.strip():
            self.current.append(" ")
            return
        href = self.link_stack[-1] if self.link_stack else None
        if href:
            self.current.append(f"[{value.strip()}]({href})")
        else:
            self.current.append(value)

    def _flush(self) -> None:
        text = " ".join("".join(self.current).split())
        if text:
            self.blocks.append(text)
        self.current = []

    def markdown(self) -> str:
        self._flush()
        text = "\n\n".join(block for block in self.blocks if block).strip()
        if self.title and (not text or not text.startswith("#")):
            text = f"# {self.title}\n\n{text}".strip()
        return text + "\n"


def _html_to_markdown(text: str) -> tuple[str, tuple[str, ...]]:
    parser = _MarkdownishHTMLParser()
    parser.feed(text)
    return parser.markdown(), tuple(parser.link_targets)


def read_page(path: Path, rel_path: str, docs_root: Path, *, is_faq: bool) -> Page:
    raw_text = _decode_text(path)
    suffix = path.suffix.lower()
    links: tuple[str, ...] = ()
    if suffix in MARKDOWN_SUFFIXES:
        text = raw_text
    elif suffix in RST_SUFFIXES:
        text = _rst_to_markdown(raw_text)
        links = _rst_link_targets(raw_text)
    elif suffix in HTML_SUFFIXES:
        text, links = _html_to_markdown(raw_text)
    else:
        text = raw_text
    return Page(path, rel_path, stable_doc_id(rel_path), text, is_faq, links)


def discover_markdown_pages(docs_root: Path, faq_patterns: list[str] | None = None) -> list[Page]:
    faq_patterns = faq_patterns or ["*faq*", "*FAQ*"]
    pages: list[Page] = []
    for path in sorted(docs_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in PAGE_SUFFIXES:
            continue
        if any(part.startswith("_") and part not in {"_sources"} for part in path.relative_to(docs_root).parts[:-1]):
            continue
        rel_path = path.relative_to(docs_root).as_posix()
        rel_lower = rel_path.lower()
        is_faq = any(Path(rel_path).match(pattern) for pattern in faq_patterns) or any(part == "faq" or "faq" in part for part in rel_lower.split("/"))
        pages.append(read_page(path, rel_path, docs_root, is_faq=is_faq))
    return pages


def _strip_front_matter(text: str) -> tuple[str, dict[str, str]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() not in {"---", "+++"}:
        return text, {}
    delimiter = lines[0].strip()
    metadata: dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == delimiter:
            body = "\n".join(lines[index + 1 :])
            return body, metadata
        match = re.match(r"^\s*([A-Za-z0-9_-]+)\s*=\s*[\"'](.+?)[\"']\s*$", line)
        if match:
            metadata[match.group(1)] = match.group(2).strip()
    return text, {}


def _question_from_faq_title(title: str) -> str | None:
    title = title.strip()
    if not title:
        return None
    lowered = title.lower()
    if "frequently asked" in lowered or "problem" in lowered:
        return None
    if "?" in title:
        return title
    if lowered.startswith("help"):
        return "How do I get help?"
    return f"What is {title}?"


def _extract_definition_list_answer(lines: list[str]) -> str:
    answer_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if answer_lines:
                break
            continue
        if stripped.startswith("$$$") or stripped.startswith("#"):
            break
        if stripped.startswith(":::"):
            continue
        stripped = re.sub(r"^:\s*", "", stripped)
        if stripped:
            answer_lines.append(stripped)
    return " ".join(answer_lines).strip()


def _candidate_targets(raw_target: str, source_rel: str) -> list[str]:
    parsed = urlparse(raw_target)
    if parsed.scheme or parsed.netloc:
        return []
    target = parsed.path
    if not target:
        return []
    base = Path(source_rel).parent
    candidate = Path(target.lstrip("/")) if target.startswith("/") else base / target
    candidate_posix = posixpath.normpath(candidate.as_posix()).lstrip("./")
    bases = [candidate_posix]
    if target.startswith("/"):
        parts = candidate_posix.split("/")
        first = parts[0].lower() if parts else ""
        route_prefix = first in {"content", "docs", "en"} or re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?|\{\{.*\}\}", first)
        if route_prefix and len(parts) > 1:
            bases.append("/".join(parts[1:]))

    candidates: list[str] = []
    for base_target in bases:
        base_path = Path(base_target)
        candidates.append(base_target)
        if base_path.name and base_path.suffix.lower() in {".html", ".htm"}:
            stem = re.sub(r"\.html?$", "", base_target, flags=re.IGNORECASE)
            candidates.extend(
                [
                    f"{stem}.md",
                    f"{stem}.mdx",
                    f"{stem}.rst",
                    posixpath.normpath(f"{stem}/index.md"),
                    posixpath.normpath(f"{stem}/_index.md"),
                ]
            )
        if base_path.name and base_path.suffix.lower() not in PAGE_SUFFIXES:
            candidates.extend(
                [
                    posixpath.normpath(f"{base_target}/index.md"),
                    posixpath.normpath(f"{base_target}/_index.md"),
                    posixpath.normpath(f"{base_target}/README.md"),
                    posixpath.normpath(f"{base_target}/index.html"),
                    f"{base_target}.md",
                    f"{base_target}.rst",
                    f"{base_target}.txt",
                    f"{base_target}.html",
                ]
            )
    normalized = list(dict.fromkeys(c.replace("\\", "/").lstrip("./") for c in candidates if c != "."))
    if target.startswith("/"):
        normalized.extend([f"content/{c}" for c in normalized])
    return normalized


def _route_key(path: str) -> str:
    normalized = urlparse(path).path.replace("\\", "/").strip("/")
    parts = [part for part in normalized.split("/") if part]
    if parts and parts[0].lower() in {"content", "docs", "en"}:
        parts = parts[1:]
    cleaned: list[str] = []
    for part in parts:
        part = re.sub(r"\.(?:md|mdx|markdown|rst|txt|html?|htm)$", "", part, flags=re.IGNORECASE)
        part = re.sub(r"^\d+-", "", part)
        if part in {"index", "_index"}:
            continue
        cleaned.append(part)
    return "/".join(cleaned)


def extract_markdown_edges(pages: list[Page]) -> list[tuple[str, str]]:
    by_rel = {p.rel_path: p for p in pages}
    by_route = {_route_key(p.rel_path): p for p in pages if _route_key(p.rel_path)}
    edges: set[tuple[str, str]] = set()
    for page in pages:
        raw_targets = list(page.link_targets)
        raw_targets.extend(re.findall(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", page.text))
        for raw_target in raw_targets:
            for candidate in _candidate_targets(raw_target, page.rel_path):
                if candidate in by_rel and by_rel[candidate].doc_id != page.doc_id:
                    edges.add((page.doc_id, by_rel[candidate].doc_id))
                    break
            else:
                route_key = _route_key(raw_target)
                target_page = by_route.get(route_key)
                if target_page and target_page.doc_id != page.doc_id:
                    edges.add((page.doc_id, target_page.doc_id))
    return sorted(edges)


def extract_faq_pairs(pages: list[Page], corpus_name: str) -> list[dict[str, str]]:
    """Extract simple Markdown FAQ pairs from FAQ pages.

    Supports headings like `## How do I install?` followed by paragraph text,
    simple top-level list questions, and topic-style FAQ pages whose
    front-matter title can be normalized into a question.
    """
    pairs: list[dict[str, str]] = []
    for page in pages:
        if not page.is_faq:
            continue
        body, metadata = _strip_front_matter(page.text)
        matches = list(re.finditer(r"^(#{1,6})\s+(.+?\?)(?:\s+\{\s*(?:#[^}\n]+|/\*.*?\*/)\s*\})?\s*$", body, flags=re.MULTILINE))
        for index, match in enumerate(matches, start=1):
            start = match.end()
            end = matches[index].start() if index < len(matches) else len(body)
            answer_lines = []
            for line in body[start:end].strip().splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    answer_lines.append(stripped)
            answer = " ".join(answer_lines).strip()
            if not answer:
                continue
            qid = f"{corpus_name}_faq_{len(pairs) + 1:04d}"
            pairs.append({"question_id": qid, "question": match.group(2).strip(), "answer": answer, "source_page": page.doc_id})
        for match in re.finditer(r"^(?P<indent>[ \t]*)-\s+(?P<question>[^:\n]+?\?)\s*$", body, flags=re.MULTILINE):
            if match.group("indent"):
                continue
            start = match.end()
            following = body[start:].splitlines()
            answer_lines = []
            for line in following:
                if not line.strip():
                    if answer_lines:
                        break
                    continue
                if re.match(r"^\S", line):
                    break
                stripped = re.sub(r"^\s*-\s*", "", line).strip()
                if stripped:
                    answer_lines.append(stripped)
            answer = " ".join(answer_lines).strip()
            if not answer:
                continue
            qid = f"{corpus_name}_faq_{len(pairs) + 1:04d}"
            pairs.append({"question_id": qid, "question": match.group("question").strip(), "answer": answer, "source_page": page.doc_id})
        elastic_matches = list(
            re.finditer(
                r"^\$\$\$[^$\n]+\$\$\$\s*(?:\*\*)?(?P<question>.+?\?)(?:\*\*)?\s*$",
                body,
                flags=re.MULTILINE,
            )
        )
        for index, match in enumerate(elastic_matches, start=1):
            start = match.end()
            end = elastic_matches[index].start() if index < len(elastic_matches) else len(body)
            answer = _extract_definition_list_answer(body[start:end].splitlines())
            if not answer:
                continue
            qid = f"{corpus_name}_faq_{len(pairs) + 1:04d}"
            pairs.append({"question_id": qid, "question": match.group("question").strip(), "answer": answer, "source_page": page.doc_id})
        title_question = _question_from_faq_title(metadata.get("title", ""))
        if title_question and title_question not in {pair["question"] for pair in pairs}:
            answer = " ".join(line.strip() for line in body.strip().splitlines() if line.strip() and not line.strip().startswith("#")).strip()
            if answer:
                qid = f"{corpus_name}_faq_{len(pairs) + 1:04d}"
                pairs.append({"question_id": qid, "question": title_question, "answer": answer, "source_page": page.doc_id})
    return pairs


def write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def export_retrieval_arena_dataset(docs_root: Path, output_dir: Path, corpus_name: str, *, faq_patterns: list[str] | None = None) -> dict:
    docs_root = docs_root.resolve()
    output_dir = output_dir.resolve()
    pages = discover_markdown_pages(docs_root, faq_patterns=faq_patterns)
    if not pages:
        raise ValueError(f"No Markdown pages found under {docs_root}")
    faq_pairs = extract_faq_pairs(pages, corpus_name)
    if not faq_pairs:
        raise ValueError(f"No FAQ question-answer pairs found under {docs_root}")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    corpus_dir = output_dir / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)

    for page in pages:
        if page.is_faq:
            continue
        (corpus_dir / f"{page.doc_id}.md").write_text(page.text, encoding="utf-8")

    questions = [{"question_id": p["question_id"], "question": p["question"]} for p in faq_pairs]
    answers = [{"question_id": p["question_id"], "answer": p["answer"]} for p in faq_pairs]
    write_jsonl(output_dir / "questions.jsonl", questions)
    write_jsonl(output_dir / "answers.jsonl", answers)

    edges = extract_markdown_edges([p for p in pages if not p.is_faq])
    with (output_dir / "graph_edges.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source", "target"])
        writer.writerows(edges)

    manifest = {
        "corpus_name": corpus_name,
        "docs_root": str(docs_root),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "num_source_pages": len(pages),
        "num_corpus_pages": sum(1 for p in pages if not p.is_faq),
        "num_faq_pages": sum(1 for p in pages if p.is_faq),
        "num_questions": len(questions),
        "num_graph_edges": len(edges),
        "format": "retrieval_arena_v1",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Markdown docs to a RetrievalArena dataset")
    parser.add_argument("--docs-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--corpus-name", required=True)
    parser.add_argument("--faq-pattern", action="append", default=None, help="Glob pattern for FAQ pages; may be repeated")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = export_retrieval_arena_dataset(Path(args.docs_root), Path(args.output_dir), args.corpus_name, faq_patterns=args.faq_pattern)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

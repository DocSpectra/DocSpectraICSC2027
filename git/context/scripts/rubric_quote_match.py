"""Three boolean-rubric scores from saved local-judge verdicts (no model calls).

  rubric_v1_strict   the frozen normalized verdicts (run_cross_corpus_r1r2_local.normalize), unchanged
  rubric_v1_relaxed  a raw `satisfied` verdict counts if its span passes `quote_match_relaxed_v1`
  rubric_v1_raw      the judge's raw verdicts, no span check

`quote_match_relaxed_v1`, applied to a span the strict check rejected (rules tried in order;
the first that admits is logged):
  normalization  NFKC; drop inline citation tags (`[... line(s) ...]`, `(... line(s) ...)`); strip
                 `*`, backticks and straight/curly quotes; collapse whitespace
  exact_normalized   normalized span is a substring of the normalized answer
  ellipsis_pieces    span contains `...` or the ellipsis character: every non-empty piece, normalized,
                     occurs in the normalized answer, in order. Ellipsis spans use only this rule.
  word_order_90      otherwise: at least 90% of the span's words occur in the answer in order
                     (longest common subsequence of normalized words over the whole answer)
Score = count of counted `satisfied` verdicts / number of elements. This module never rewrites
the frozen judge outputs; it reads them.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

RULE = "quote_match_relaxed_v1"
WORD_THRESHOLD = 0.90
_CITATION = re.compile(r"\[[^\[\]\n]*\blines?\b[^\[\]\n]*\]|\([^()\n]*\blines?\b[^()\n]*\)", re.IGNORECASE)
_STRIP = dict.fromkeys(map(ord, "*`\"'‘’‚‛“”„‟′″"), None)
_ELLIPSIS = re.compile(r"\.\.\.+|…")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _CITATION.sub(" ", text)
    text = text.translate(_STRIP)
    return " ".join(text.split())


def _lcs(a, b):
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def word_coverage(span: str, answer: str):
    """In-order word coverage: longest common subsequence of span words and answer words."""
    sw, aw = normalize(span).split(), normalize(answer).split()
    return (_lcs(sw, aw) if sw else 0), len(sw)


def relaxed_match(span, answer):
    """(admitted, rule, detail) for one asserted span against the saved answer text."""
    if not span or span == "null":
        return False, None, {"reason": "no-span"}
    if span in answer:
        return True, "exact", {}
    na = normalize(answer)
    if _ELLIPSIS.search(span):
        pieces = [normalize(p) for p in _ELLIPSIS.split(span)]
        pieces = [p for p in pieces if p]
        pos, found = 0, 0
        for p in pieces:
            k = na.find(p, pos)
            if k < 0:
                break
            found, pos = found + 1, k + len(p)
        ok = bool(pieces) and found == len(pieces)
        return ok, "ellipsis_pieces" if ok else None, {"pieces": len(pieces), "pieces_found_in_order": found}
    if normalize(span) in na:
        return True, "exact_normalized", {}
    hit, total = word_coverage(span, answer)
    ok = total > 0 and hit / total >= WORD_THRESHOLD
    return ok, "word_order_90" if ok else None, {"words": total, "words_in_order": hit}


def score_verdicts(raw_verdicts, strict_verdicts, answer, n_elements):
    """Three scores and the relaxed-match log for one answer's verdicts."""
    log = []
    relaxed = 0
    for v in raw_verdicts:
        if v["verdict"] != "satisfied":
            continue
        ok, rule, detail = relaxed_match(v.get("answer_span"), answer)
        relaxed += ok
        if rule != "exact":
            log.append({"element_id": v["element_id"], "admitted": ok, "rule": rule, **detail})
    return {"rubric_v1_strict": sum(v["verdict"] == "satisfied" for v in strict_verdicts) / n_elements,
            "rubric_v1_relaxed": relaxed / n_elements,
            "rubric_v1_raw": sum(v["verdict"] == "satisfied" for v in raw_verdicts) / n_elements,
            "relaxed_log": log, "rule": RULE}


def score_judge_root(judge_root: Path, transport_dir="local-qwen-v4"):
    """Per answer file: the three scores from a run_cross_corpus_r1r2_local-style judge root.

    Reads aliases.json (unit id -> answer files), packets/<uid>.json, and the completion's raw and
    normalized attempt. A unit without a valid completion scores None in all three.
    """
    judge_root = Path(judge_root)
    aliases = json.loads((judge_root / "aliases.json").read_text(encoding="utf-8"))
    out = {}
    for uid, files in sorted(aliases.items()):
        folder = judge_root / transport_dir / uid
        done = folder / "completion.json"
        scores = None
        if done.exists():
            record = json.loads(done.read_text(encoding="utf-8"))
            if record["valid"]:
                unit = json.loads((judge_root / "packets" / f"{uid}.json").read_text(encoding="utf-8"))["units"][0]
                raw = json.loads((folder / f"attempt-{record['attempt']:02d}.raw.json").read_text(encoding="utf-8"))
                raw_units = json.loads(raw["message"]["content"])["units"]
                norm_units = json.loads((folder / f"attempt-{record['attempt']:02d}.normalized.json")
                                        .read_text(encoding="utf-8"))["units"]
                scores = {"unit_id": uid, **score_verdicts(raw_units[0]["verdicts"], norm_units[0]["verdicts"],
                                                           unit["answer"], len(unit["elements"]))}
        for f in files:
            out[f] = scores
    return out

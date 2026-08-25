"""Robust parsing of structured LLM output.

The evolution loop's entire feedback signal is the evaluation score. If a
parse failure silently becomes ``score=50``, the Improver optimises against
noise: it patches problems that do not exist and burns rounds doing it.

This module therefore makes parse failure an *explicit, inspectable* outcome
rather than a plausible-looking default. Callers must decide what to do when
``ok`` is False — they cannot accidentally consume a fabricated score.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .types import Evaluation


# ---------------------------------------------------------------------------
# JSON block extraction
# ---------------------------------------------------------------------------

def extract_json_blocks(text: str) -> list[str]:
    """Return candidate JSON object substrings, outermost-first.

    Uses brace matching rather than a greedy ``\\{.*\\}`` regex, which
    mis-captures when a response contains prose around several JSON blocks
    (common with reasoning models that narrate before answering).

    String literals are tracked so that braces *inside* strings do not
    affect nesting depth.
    """
    blocks: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False

    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    blocks.append(text[start : i + 1])
                    start = -1

    return blocks


def _strip_code_fence(text: str) -> str:
    """Remove a surrounding ```json / ``` fence if present."""
    fenced = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    return fenced.group(1) if fenced else text


def repair_json(raw: str) -> str:
    """Apply targeted, conservative repairs to nearly-valid JSON.

    Deliberately does NOT do a blanket ``replace("'", '"')``: that corrupts
    any string containing an apostrophe, turning ``"can't parse"`` into
    ``"can"t parse"`` and guaranteeing a parse failure. Each repair here is
    narrow enough to be safe.
    """
    text = raw.strip()

    # Trailing commas before a closing brace/bracket.
    text = re.sub(r",(\s*[}\]])", r"\1", text)

    # Python literals that models sometimes emit instead of JSON ones.
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    text = re.sub(r"\bNone\b", "null", text)

    # Single-quoted *keys* only — key positions cannot contain apostrophes,
    # so this substitution is safe where a general one would not be.
    text = re.sub(r"([{,]\s*)'([^']+?)'(\s*:)", r'\1"\2"\3', text)

    return text


def parse_json_object(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Best-effort parse of a JSON object out of arbitrary LLM output.

    Returns ``(data, None)`` on success or ``(None, error)`` on failure.
    """
    if not text or not text.strip():
        return None, "empty response"

    candidates: list[str] = []
    unfenced = _strip_code_fence(text)
    if unfenced is not text:
        candidates.extend(extract_json_blocks(unfenced))
    candidates.extend(extract_json_blocks(text))

    if not candidates:
        return None, "no JSON object found in response"

    for raw in candidates:
        for attempt in (raw, repair_json(raw)):
            try:
                data = json.loads(attempt)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data, None

    return None, f"found {len(candidates)} JSON-like block(s) but none parsed"


# ---------------------------------------------------------------------------
# Evaluation parsing
# ---------------------------------------------------------------------------

@dataclass
class EvalParseResult:
    """Outcome of parsing an evaluation response.

    ``ok=False`` means *no usable signal was produced*. It is deliberately
    not representable as a score, so a caller cannot mistake a parse failure
    for a mediocre result.
    """

    ok: bool
    evaluation: Evaluation | None = None
    error: str | None = None
    raw: str = ""


def _coerce_issues(value: Any) -> list[str]:
    """Normalise the ``issues`` field, which models render inconsistently."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    return [str(value)]


def parse_evaluation(response: str) -> EvalParseResult:
    """Parse an evaluator response into an :class:`Evaluation`.

    A score outside 0-100, a non-numeric score, or a missing score are all
    treated as failures rather than being silently clamped: a model that
    cannot follow the output contract is not producing a trustworthy signal,
    and pretending otherwise pollutes the evolution history.
    """
    data, error = parse_json_object(response)
    if data is None:
        return EvalParseResult(ok=False, error=error, raw=response[:500])

    if "score" not in data:
        return EvalParseResult(
            ok=False,
            error=f"JSON parsed but has no 'score' key (keys: {sorted(data)[:8]})",
            raw=response[:500],
        )

    score = data["score"]
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return EvalParseResult(
            ok=False, error=f"score is not numeric: {score!r}", raw=response[:500]
        )
    if not 0 <= score <= 100:
        return EvalParseResult(
            ok=False, error=f"score {score} out of range 0-100", raw=response[:500]
        )

    dimensions = data.get("dimensions")
    if not isinstance(dimensions, dict):
        dimensions = {}
    else:
        dimensions = {
            str(k): float(v)
            for k, v in dimensions.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }

    return EvalParseResult(
        ok=True,
        evaluation=Evaluation(
            score=float(score),
            issues=_coerce_issues(data.get("issues")),
            root_cause=str(data.get("root_cause") or ""),
            suggested_changes=str(data.get("suggested_changes") or ""),
            dimensions=dimensions,
        ),
        raw=response[:500],
    )

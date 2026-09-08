"""Evolution history persistence and display.

Tracks every patch the Improver generates — accepted or rejected — in a
JSONL file at ``$PANDA_HOME/evolution_history.jsonl``. This is what makes
self-evolution visible to the user: they can see what the agent changed,
why, and whether it helped, without looking at git logs.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


def _history_path() -> Path:
    home = os.environ.get("PANDA_HOME", os.path.expanduser("~/.panda"))
    return Path(home) / "evolution_history.jsonl"


def record_evolution(
    *,
    source_file: str,
    root_cause: str,
    suggested_changes: str,
    patched: bool,
    explanation: str,
    test_output: str = "",
    attempts: int = 0,
    score: float = 0.0,
) -> None:
    """Append one evolution event to the history file.

    Called by the Improver after every patch attempt (accepted or rejected).
    Never raises — history recording must not break the evolution loop.
    """
    try:
        path = _history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "source_file": source_file,
            "root_cause": root_cause[:200],
            "suggested_changes": suggested_changes[:200],
            "patched": patched,
            "explanation": explanation[:400],
            "test_output": test_output[-300:],
            "attempts": attempts,
            "score": score,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_history() -> list[dict]:
    """Load the full evolution history, oldest first."""
    path = _history_path()
    if not path.exists():
        return []
    entries = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(json.loads(line))
    except Exception:
        pass
    return entries


def evolution_summary() -> str | None:
    """Return a one-line summary for the startup banner, or None.

    Example: 'PandaAgent v3 (7 evolutions, last: improved search_files) '
    """
    history = load_history()
    if not history:
        return None

    accepted = [e for e in history if e.get("patched")]
    total = len(history)
    accepted_count = len(accepted)

    if accepted_count == 0:
        return f" (0 evolutions kept, {total} attempted)"

    last = accepted[-1]
    last_file = last.get("source_file", "?")
    last_explanation = last.get("explanation", "")[:60]

    return (
        f" ({accepted_count} evolutions kept, {total} attempted, "
        f"last: {last_file} - {last_explanation})"
    )


def format_history_table() -> str:
    """Format the full history as a human-readable table for `panda history`."""
    history = load_history()
    if not history:
        return "No evolution history yet. The agent evolves as you use it."

    lines = []
    lines.append(f"{'#':>3}  {'Time':<20}  {'File':<14}  {'Status':<8}  {'Score':>5}  Reason")
    lines.append("-" * 90)

    for i, entry in enumerate(history, 1):
        time_str = entry.get("time", "?")[:19]
        source = entry.get("source_file", "?")[:14]
        patched = entry.get("patched", False)
        status = "✓ kept" if patched else "✗ reject"
        score = entry.get("score", 0)
        reason = entry.get("root_cause", "")[:40]
        lines.append(f"{i:>3}  {time_str:<20}  {source:<14}  {status:<8}  {score:>5.0f}  {reason}")

    accepted = [e for e in history if e.get("patched")]
    lines.append("")
    lines.append(f"Total: {len(history)} attempts, {len(accepted)} kept")

    if accepted:
        lines.append("")
        lines.append("Accepted improvements:")
        for e in accepted:
            lines.append(f"  • {e.get('source_file','?')}: {e.get('explanation','')[:100]}")

    return "\n".join(lines)

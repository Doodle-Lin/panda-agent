"""Audit Phase 5 evolution audit trail tests.

Covers audit finding #13 (run_evolution does not write history) and #14
(history load is unbounded / no pagination / silent error swallow) from
the 2026-09-14 audit.

#13a: record_evolution is only called from cli.py chat mode; the scripted
      run_evolution() path (used by scripts/run_experiment.py and
      observe_evolution.py) produces no persistent history. The README
      claims "Persist every round" (R5) but the experiment path is silent.

#13b: record_evolution swallows all exceptions silently — a disk full or
      permission error means history vanishes with no log. Surface the
      failure to stderr and return a bool.

#14: load_history reads the whole file into memory and splitlines()s it;
      no pagination, no query API. Add `limit`, `since`, and `source_file`
      parameters with a streaming line-by-line read.
"""
from __future__ import annotations

import json
from pathlib import Path

from panda_agent.evolution_history import (
    load_history,
    record_evolution,
)


# ---------------------------------------------------------------------------
# #13b: record_evolution surfaces failures (returns bool + stderr log)
# ---------------------------------------------------------------------------

class TestRecordEvolutionErrorVisibility:
    def test_record_returns_true_on_success(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PANDA_HOME", str(tmp_path))
        ok = record_evolution(
            source_file="tools.py",
            root_cause="bug",
            suggested_changes="fix",
            patched=True,
            explanation="ok",
        )
        assert ok is True
        history = load_history()
        assert len(history) == 1
        assert history[0]["source_file"] == "tools.py"

    def test_record_returns_false_and_logs_on_failure(self, tmp_path, monkeypatch, capsys):
        # Force the history file path to a location that cannot be written.
        # We monkey-patch _history_path to point at a path under a non-existent
        # parent whose creation we prevent.
        monkeypatch.setenv("PANDA_HOME", str(tmp_path))
        import panda_agent.evolution_history as hist_mod

        bad_path = tmp_path / "blocked_dir" / "history.jsonl"
        # Make the parent a file (not a dir) so mkdir fails.
        (tmp_path / "blocked_dir").write_text("blocker", encoding="utf-8")

        monkeypatch.setattr(hist_mod, "_history_path", lambda: bad_path)
        ok = record_evolution(
            source_file="tools.py",
            root_cause="bug",
            suggested_changes="fix",
            patched=True,
            explanation="ok",
        )
        assert ok is False, (
            "record_evolution should return False when the write fails, "
            "so the caller knows history was not persisted."
        )
        captured = capsys.readouterr()
        assert "history" in captured.err.lower() or "record" in captured.err.lower(), (
            f"record_evolution swallowed the failure silently; expected a "
            f"stderr log. captured.err={captured.err!r}"
        )


# ---------------------------------------------------------------------------
# #14: load_history pagination + filtering
# ---------------------------------------------------------------------------

class TestLoadHistoryPagination:
    def _populate(self, path: Path, n: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for i in range(n):
                f.write(json.dumps({
                    "time": f"2026-09-{i+1:02d}T00:00:00",
                    "source_file": "tools.py" if i % 2 == 0 else "brain.py",
                    "root_cause": f"cause-{i}",
                    "patched": i % 3 == 0,
                    "explanation": f"expl-{i}",
                    "score": 50.0 + i,
                }) + "\n")

    def test_load_all(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PANDA_HOME", str(tmp_path))
        self._populate(tmp_path / "evolution_history.jsonl", 10)
        history = load_history()
        assert len(history) == 10
        # Oldest first.
        assert history[0]["root_cause"] == "cause-0"
        assert history[-1]["root_cause"] == "cause-9"

    def test_load_with_limit_returns_last_n(self, tmp_path, monkeypatch):
        # limit returns the *last* N entries (most recent), oldest-first
        # within that window.
        monkeypatch.setenv("PANDA_HOME", str(tmp_path))
        self._populate(tmp_path / "evolution_history.jsonl", 10)
        history = load_history(limit=3)
        assert len(history) == 3
        assert history[0]["root_cause"] == "cause-7"
        assert history[-1]["root_cause"] == "cause-9"

    def test_load_with_source_file_filter(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PANDA_HOME", str(tmp_path))
        self._populate(tmp_path / "evolution_history.jsonl", 10)
        history = load_history(source_file="brain.py")
        assert len(history) == 5
        assert all(h["source_file"] == "brain.py" for h in history)

    def test_load_streaming_does_not_load_all_into_memory(self, tmp_path, monkeypatch):
        # Just verify it works on a large file without OOM — we don't actually
        # measure memory, but we assert it returns the right tail.
        monkeypatch.setenv("PANDA_HOME", str(tmp_path))
        self._populate(tmp_path / "evolution_history.jsonl", 1000)
        history = load_history(limit=5)
        assert len(history) == 5
        assert history[-1]["root_cause"] == "cause-999"


# ---------------------------------------------------------------------------
# #13a: run_evolution writes history
# ---------------------------------------------------------------------------

class TestRunEvolutionWritesHistory:
    """The scripted run_evolution() path must persist history, not only
    the CLI chat path."""

    def test_run_evolution_records_each_round(self, tmp_path, monkeypatch):
        # Point PANDA_HOME at a temp dir so the history file lands there.
        monkeypatch.setenv("PANDA_HOME", str(tmp_path))

        from unittest.mock import MagicMock
        from panda_agent.config import (
            AgentConfig, Config, DisplayConfig, EvolutionConfig,
            MemoryConfig, ModelConfig,
        )
        from panda_agent.orchestrator import run_evolution
        from panda_agent.types import (
            Evaluation, ExecutionResult, Task,
        )

        config = Config(
            model=ModelConfig(default="m", api_key="k", base_url="u"),
            agent=AgentConfig(max_turns=1),
            memory=MemoryConfig(enabled=False),
            evolution=EvolutionConfig(max_rounds=2, improve_tools=False, improve_brain=False),
            display=DisplayConfig(),
        )

        executor = MagicMock()
        executor.execute.return_value = ExecutionResult(success=True, tool_calls=[], trace="")

        evaluator = MagicMock()
        evaluator.evaluate.return_value = Evaluation(
            score=70, issues=["x"], root_cause="rc", suggested_changes="sc",
        )

        improver = MagicMock()
        improver.improve.return_value = MagicMock(
            patched=False,
            explanation="no patch",
            test_output="",
            attempts=1,
        )

        run_evolution(
            executor=executor,
            evaluator=evaluator,
            improver=improver,
            task=Task(instruction="test"),
            target_score=90.0,
            max_rounds=2,
            config=config,
        )

        # History must have at least one entry per round that the improver
        # runs on (i.e. rounds before the last). With max_rounds=2 the
        # improver runs on round 1 only; round 2 is the "last round" skip
        # path, so we expect exactly 1 entry. The pre-fix code wrote 0.
        history = load_history()
        assert len(history) >= 1, (
            f"run_evolution did not record history; expected >=1 entries, "
            f"got {len(history)}. The scripted path is still silent."
        )
        assert history[0]["patched"] is False  # mocked improver returned patched=False

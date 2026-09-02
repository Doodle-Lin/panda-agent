"""Tests for the reproducible experiment runner.

These drive ``run_experiment`` with a mock runner so the experiment is
end-to-end testable without an LLM. They pin the two properties that make
the report meaningful:

* the held-out split is scored before and after evolution, so the report
  carries a generalisation delta, not just a train-task score;
* the repo is left clean -- the evolvable sources are restored after the
  run even when the loop wrote patches.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from run_experiment import (  # type: ignore[import-not-found]
    ExperimentReport,
    _EVOLVABLE,
    _restore_evolvable,
    _snapshot_evolvable,
    run_experiment,
)
from panda_agent.benchmark import BenchmarkTask
from panda_agent.config import Config
from panda_agent.types import Task


def _config() -> Config:
    return Config()


def _mock_runner_factory(_config: Config):
    """A deterministic runner whose answer depends on tools.py's content.

    The runner reads the current on-disk tools.py and returns a marker so the
    held-out 'after' measurement reflects whatever the loop left on disk.
    This lets the test observe evolution effects without an LLM.
    """
    def _run(task: Task) -> str:
        # Return a stable string derived from the current tools.py so that
        # if the loop patches tools.py, the held-out score changes.
        tools = _EVOLVABLE[0]
        if tools.exists():
            text = tools.read_text(encoding="utf-8")
        else:
            text = ""
        # Deterministic answer: include the instruction so scorers can match,
        # plus a marker that changes when tools.py changes.
        return f"{task.instruction}\n[tools_len={len(text)}]"
    return _run


def _tasks(tmp_path: Path) -> tuple[list[BenchmarkTask], Path]:
    """Five benchmark tasks with exact_match scorers against tmp_path."""
    # Create a fixture file the 'apply_edit' task modifies.
    (tmp_path / "config.py").write_text("DEFAULT_PORT = 8080\n", encoding="utf-8")
    return [
        BenchmarkTask(
            id="read_port", instruction="report DEFAULT_PORT",
            scorer="exact_match", expected={"contains": ["8080"]}, weight=1.0,
        ),
        BenchmarkTask(
            id="search_todo", instruction="find TODO comments",
            scorer="exact_match", expected={"contains": ["TODO"]}, weight=1.0,
        ),
        BenchmarkTask(
            id="count", instruction="count lines",
            scorer="exact_match", expected={"contains": ["count"]}, weight=1.0,
        ),
        BenchmarkTask(
            id="apply_edit", instruction="change DEFAULT_PORT to 9090",
            scorer="file_state",
            expected={"file": "config.py", "contains": "DEFAULT_PORT = 9090",
                      "not_contains": "DEFAULT_PORT = 8080"},
            weight=2.0,
        ),
        BenchmarkTask(
            id="recover", instruction="read missing file",
            scorer="exact_match", expected={"contains": ["not"]}, weight=1.0,
        ),
    ], tmp_path


class TestRunExperiment:
    def test_report_has_train_and_test_splits(self, tmp_path):
        tasks, workspace = _tasks(tmp_path)
        report = run_experiment(
            _config(), tasks,
            train_ids=["read_port", "search_todo", "count"],
            test_ids=["apply_edit", "recover"],
            workspace=workspace,
            rounds=1,
            target_score=90.0,
            runner_factory=_mock_runner_factory,
        )
        assert isinstance(report, ExperimentReport)
        assert report.train_ids == ["read_port", "search_todo", "count"]
        assert report.test_ids == ["apply_edit", "recover"]
        # One train record per train task.
        assert len(report.train_runs) == 3
        # One test record per held-out task, with before/after scores.
        assert len(report.test_split) == 2
        for t in report.test_split:
            assert t.score_before >= 0.0
            assert t.score_after >= 0.0
        # Weighted numbers are present.
        assert report.test_weighted_before >= 0.0
        assert report.test_weighted_after >= 0.0
        assert isinstance(report.test_delta, float)

    def test_rejects_overlapping_split(self, tmp_path):
        tasks, workspace = _tasks(tmp_path)
        with pytest.raises(ValueError, match="disjoint"):
            run_experiment(
                _config(), tasks,
                train_ids=["read_port", "search_todo"],
                test_ids=["search_todo", "count"],
                workspace=workspace,
                runner_factory=_mock_runner_factory,
            )

    def test_rejects_unknown_task_id(self, tmp_path):
        tasks, workspace = _tasks(tmp_path)
        with pytest.raises(ValueError, match="unknown task ids"):
            run_experiment(
                _config(), tasks,
                train_ids=["read_port"],
                test_ids=["nonexistent"],
                workspace=workspace,
                runner_factory=_mock_runner_factory,
            )

    def test_repo_left_clean_after_run(self, tmp_path):
        """The evolvable sources must be restored to their pre-run state even
        if the loop wrote patches. Without this, an experiment run would
        silently leave tools.py / brain.py dirty."""
        tasks, workspace = _tasks(tmp_path)
        before = _snapshot_evolvable()
        run_experiment(
            _config(), tasks,
            train_ids=["read_port", "search_todo"],
            test_ids=["count"],
            workspace=workspace,
            rounds=2,
            runner_factory=_mock_runner_factory,
        )
        after = _snapshot_evolvable()
        assert before == after, "evolvable sources were not restored"

    def test_writes_report_files(self, tmp_path):
        tasks, workspace = _tasks(tmp_path)
        out_dir = tmp_path / "out"
        run_experiment(
            _config(), tasks,
            train_ids=["read_port"],
            test_ids=["search_todo"],
            workspace=workspace,
            rounds=1,
            runner_factory=_mock_runner_factory,
            out_dir=out_dir,
        )
        assert (out_dir / "report.json").exists()
        assert (out_dir / "report.md").exists()
        md = (out_dir / "report.md").read_text(encoding="utf-8")
        assert "Held-out generalisation" in md
        assert "Train runs" in md

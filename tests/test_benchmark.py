"""Tests for the regression benchmark gate.

The load-bearing test here is
``TestGate::test_patch_that_passes_tests_but_degrades_is_rejected``: it pins
the behaviour the whole R1 change exists to provide. If that test is ever
deleted or weakened, the evolution loop silently reverts to being open-loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from panda_agent.benchmark import (
    BenchmarkResult,
    BenchmarkTask,
    GateResult,
    TaskScore,
    check_no_regression,
    estimate_noise,
    load_tasks,
    run_benchmark,
    score_exact_match,
    score_file_state,
)
from panda_agent.types import Task


# ---------------------------------------------------------------------------
# Task loading / validation
# ---------------------------------------------------------------------------

class TestTaskLoading:
    def test_loads_valid_suite(self, tmp_path):
        p = tmp_path / "tasks.yaml"
        p.write_text(yaml.safe_dump([
            {"id": "a", "instruction": "do a", "scorer": "exact_match",
             "expected": {"contains": ["x"]}},
            {"id": "b", "instruction": "do b", "weight": 2.0},
        ]))
        tasks = load_tasks(p)
        assert [t.id for t in tasks] == ["a", "b"]
        assert tasks[1].weight == 2.0

    def test_missing_id_is_rejected(self, tmp_path):
        p = tmp_path / "t.yaml"
        p.write_text(yaml.safe_dump([{"instruction": "x"}]))
        with pytest.raises(ValueError, match="missing required"):
            load_tasks(p)

    def test_unknown_scorer_is_rejected(self, tmp_path):
        p = tmp_path / "t.yaml"
        p.write_text(yaml.safe_dump([{"id": "a", "instruction": "x", "scorer": "vibes"}]))
        with pytest.raises(ValueError, match="unknown scorer"):
            load_tasks(p)

    def test_llm_judge_requires_rubric(self, tmp_path):
        p = tmp_path / "t.yaml"
        p.write_text(yaml.safe_dump([{"id": "a", "instruction": "x", "scorer": "llm_judge"}]))
        with pytest.raises(ValueError, match="requires a 'rubric'"):
            load_tasks(p)

    def test_duplicate_ids_are_rejected(self, tmp_path):
        p = tmp_path / "t.yaml"
        p.write_text(yaml.safe_dump([
            {"id": "a", "instruction": "x"},
            {"id": "a", "instruction": "y"},
        ]))
        with pytest.raises(ValueError, match="duplicate task ids"):
            load_tasks(p)

    def test_non_positive_weight_is_rejected(self, tmp_path):
        p = tmp_path / "t.yaml"
        p.write_text(yaml.safe_dump([{"id": "a", "instruction": "x", "weight": 0}]))
        with pytest.raises(ValueError, match="weight must be positive"):
            load_tasks(p)


# ---------------------------------------------------------------------------
# Deterministic scorers
# ---------------------------------------------------------------------------

class TestExactMatch:
    def _task(self, **expected):
        return BenchmarkTask(id="t", instruction="i", expected=expected)

    def test_all_required_present(self, tmp_path):
        t = self._task(contains=["alpha", "beta"])
        assert score_exact_match(t, "Alpha and BETA", tmp_path) == 100.0

    def test_partial_credit(self, tmp_path):
        t = self._task(contains=["alpha", "beta"])
        assert score_exact_match(t, "only alpha", tmp_path) == 50.0

    def test_forbidden_string_zeroes_the_score(self, tmp_path):
        t = self._task(contains=["alpha"], not_contains=["error"])
        assert score_exact_match(t, "alpha but also error", tmp_path) == 0.0

    def test_empty_answer_scores_zero(self, tmp_path):
        assert score_exact_match(self._task(), "", tmp_path) == 0.0


class TestFileState:
    def test_scores_the_effect_not_the_claim(self, tmp_path):
        """An agent can report success without changing anything."""
        (tmp_path / "config.py").write_text("port = 8000\n")
        t = BenchmarkTask(
            id="t", instruction="change port", scorer="file_state",
            expected={"file": "config.py", "contains": "port = 9000",
                      "not_contains": "port = 8000"},
        )
        assert score_file_state(t, "Done! Changed the port.", tmp_path) == 0.0

        (tmp_path / "config.py").write_text("port = 9000\n")
        assert score_file_state(t, "Done!", tmp_path) == 100.0

    def test_missing_file_scores_zero(self, tmp_path):
        t = BenchmarkTask(id="t", instruction="i", scorer="file_state",
                          expected={"file": "nope.py", "contains": "x"})
        assert score_file_state(t, "done", tmp_path) == 0.0

    def test_path_escape_is_rejected(self, tmp_path):
        t = BenchmarkTask(id="t", instruction="i", scorer="file_state",
                          expected={"file": "../../etc/passwd", "contains": "root"})
        with pytest.raises(ValueError, match="escapes the workspace"):
            score_file_state(t, "done", tmp_path)

    def test_bare_string_expectation_is_not_iterated_per_character(self, tmp_path):
        """Regression: ``contains: "port = 9000"`` as a YAML scalar was
        iterated character by character, so a file containing any of
        ``p o r t = 9 0`` scored partial credit and the check was vacuous."""
        (tmp_path / "config.py").write_text("port = 8000\n")
        t = BenchmarkTask(id="t", instruction="i", scorer="file_state",
                          expected={"file": "config.py", "contains": "port = 9000"})
        assert score_file_state(t, "done", tmp_path) == 0.0

    def test_bare_string_expectation_matches_when_present(self, tmp_path):
        (tmp_path / "config.py").write_text("port = 9000\n")
        t = BenchmarkTask(id="t", instruction="i", scorer="file_state",
                          expected={"file": "config.py", "contains": "port = 9000"})
        assert score_file_state(t, "done", tmp_path) == 100.0


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class TestRunBenchmark:
    def test_weighted_mean(self, tmp_path):
        tasks = [
            BenchmarkTask(id="a", instruction="i", expected={"contains": ["x"]}, weight=1.0),
            BenchmarkTask(id="b", instruction="i", expected={"contains": ["y"]}, weight=3.0),
        ]
        # a scores 0, b scores 100 -> weighted = (0*1 + 100*3) / 4 = 75
        r = run_benchmark(tasks, lambda t: "y", tmp_path)
        assert r.weighted_score == 75.0

    def test_runner_exception_scores_zero_but_stays_usable(self, tmp_path):
        """A crashing agent is a real result: it scores zero, it is not
        an unmeasurable one."""
        def boom(task: Task) -> str:
            raise RuntimeError("agent died")

        r = run_benchmark([BenchmarkTask(id="a", instruction="i")], boom, tmp_path)
        assert r.scores[0].score == 0.0
        assert r.scores[0].usable is True
        assert "agent died" in r.scores[0].detail

    def test_scorer_exception_is_unusable_not_zero(self, tmp_path):
        """A broken measurement must not masquerade as a bad score."""
        tasks = [BenchmarkTask(id="a", instruction="i", scorer="file_state",
                               expected={"file": "../escape", "contains": "x"})]
        r = run_benchmark(tasks, lambda t: "done", tmp_path)
        assert r.scores[0].usable is False
        assert r.complete is False

    def test_unusable_scores_excluded_from_mean(self, tmp_path):
        r = BenchmarkResult(scores=[
            TaskScore("a", 100.0, 1.0, usable=True),
            TaskScore("b", 0.0, 1.0, usable=False),
        ])
        assert r.weighted_score == 100.0
        assert r.complete is False


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def _result(**scores: float) -> BenchmarkResult:
    return BenchmarkResult(scores=[TaskScore(k, v, 1.0) for k, v in scores.items()])


class TestGate:
    def test_patch_that_passes_tests_but_degrades_is_rejected(self):
        """The reason R1 exists.

        Unit tests cannot see this: the code is valid and imports fine, but
        the agent got worse at its actual job. Before this gate, such a patch
        was accepted and kept.
        """
        before = _result(find_file=90.0, search_cite=80.0)
        after = _result(find_file=75.0, search_cite=80.0)

        gate = check_no_regression(before, after, tolerance=2.0)

        assert gate.accepted is False
        assert "regression" in gate.reason
        assert "find_file" in gate.reason
        assert gate.per_task["find_file"] == -15.0

    def test_improvement_is_accepted(self):
        gate = check_no_regression(_result(a=60.0), _result(a=85.0), tolerance=2.0)
        assert gate.accepted
        assert gate.delta == 25.0

    def test_noise_within_tolerance_is_accepted(self):
        """Rejecting every decrease would discard good patches on sampling
        noise alone."""
        gate = check_no_regression(_result(a=80.0), _result(a=78.5), tolerance=2.0)
        assert gate.accepted

    def test_decrease_beyond_tolerance_is_rejected(self):
        gate = check_no_regression(_result(a=80.0), _result(a=77.0), tolerance=2.0)
        assert gate.accepted is False

    def test_incomplete_benchmark_is_never_accepted(self):
        """The unusable task may be the one the patch broke."""
        after = BenchmarkResult(scores=[
            TaskScore("a", 90.0, 1.0, usable=True),
            TaskScore("b", 0.0, 1.0, usable=False, detail="judge unreachable"),
        ])
        gate = check_no_regression(_result(a=80.0, b=80.0), after, tolerance=2.0)
        assert gate.accepted is False
        assert "incomplete" in gate.reason

    def test_regressions_lists_only_degraded_tasks(self):
        before = _result(a=80.0, b=80.0, c=80.0)
        after = _result(a=90.0, b=80.0, c=60.0)
        assert list(after.regressions(before, threshold=2.0)) == ["c"]

    def test_diff_is_ordered_worst_first(self):
        before = _result(a=80.0, b=80.0, c=80.0)
        after = _result(a=85.0, b=50.0, c=70.0)
        assert list(after.diff(before)) == ["b", "c", "a"]


class TestNoiseEstimation:
    def test_returns_mean_and_stdev(self, tmp_path):
        tasks = [BenchmarkTask(id="a", instruction="i", expected={"contains": ["x"]})]
        calls = iter(["x", "", "x"])   # 100, 0, 100
        mean, stdev = estimate_noise(tasks, lambda t: next(calls), tmp_path, runs=3)
        assert mean == pytest.approx(66.67, abs=0.01)
        assert stdev > 0

    def test_deterministic_runner_has_zero_variance(self, tmp_path):
        tasks = [BenchmarkTask(id="a", instruction="i", expected={"contains": ["x"]})]
        mean, stdev = estimate_noise(tasks, lambda t: "x", tmp_path, runs=3)
        assert mean == 100.0
        assert stdev == 0.0

    def test_single_run_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="at least 2 runs"):
            estimate_noise([], lambda t: "", tmp_path, runs=1)

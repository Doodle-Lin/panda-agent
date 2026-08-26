"""Integration tests for the evolution loop.

orchestrator.py is the largest module in the project and had no test coverage,
which is where the risk concentrates: it applies patches, decides what to keep,
and writes to source files on disk.

The load-bearing case is
``TestImproverGates::test_patch_passing_tests_but_regressing_is_reverted``.
It exercises the whole point of the regression gate through the real
Improver code path rather than the gate function in isolation.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch as mock_patch

import pytest

from panda_agent.benchmark import BenchmarkResult, TaskScore
from panda_agent.config import Config
from panda_agent.orchestrator import Evaluator, Improver, run_evolution
from panda_agent.types import (
    Evaluation,
    ExecutionResult,
    ImprovementResult,
    Task,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GOOD_PATCH = """\
PATCH_START
```python
def helper(x):
    return x * 2
```
PATCH_END
EXPLANATION: double instead of identity
"""

BROKEN_PATCH = """\
PATCH_START
```python
def helper(x
    return x
```
PATCH_END
EXPLANATION: this does not parse
"""

MISSING_TARGET_PATCH = """\
PATCH_START
```python
def not_in_source(x):
    return x
```
PATCH_END
EXPLANATION: targets a function that does not exist
"""


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A throwaway project with a patchable source file and a test suite."""
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    target = src / "tools.py"
    target.write_text("def helper(x):\n    return x\n", encoding="utf-8")

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_helper.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
        "from pkg.tools import helper\n"
        "def test_helper_returns_a_number():\n"
        "    assert isinstance(helper(2), int)\n",
        encoding="utf-8",
    )
    return tmp_path, target, tests


def _improver(config, sandbox_paths, llm_response):
    """Build an Improver wired to the sandbox with a stubbed LLM."""
    root, target, tests = sandbox_paths
    imp = Improver(config)
    imp.project_root = root
    imp.test_path = tests
    return imp


def _result(**scores: float) -> BenchmarkResult:
    return BenchmarkResult(scores=[TaskScore(k, v, 1.0) for k, v in scores.items()])


@pytest.fixture
def config():
    return Config()


# ---------------------------------------------------------------------------
# Improver gates
# ---------------------------------------------------------------------------

class TestImproverGates:
    def test_improve_prompt_formats_without_keyerror(self, sandbox, config):
        """Regression: the prompt template contained a literal ``{code_here}``
        placeholder that ``str.format`` tried to substitute, so every call to
        _improve_file raised KeyError. The Improver could never have run --
        which is what zero coverage on this module concealed.
        """
        root, target, tests = sandbox
        imp = _improver(config, sandbox, GOOD_PATCH)

        with mock_patch("panda_agent.orchestrator.call_llm", return_value="NO_CHANGE"):
            r = imp._improve_file(target, Evaluation(score=50.0), ["helper"])

        # The point is that it returned at all rather than raising.
        assert isinstance(r, ImprovementResult)

    def test_good_patch_is_kept_and_backup_removed(self, sandbox, config):
        root, target, tests = sandbox
        imp = _improver(config, sandbox, GOOD_PATCH)

        with mock_patch("panda_agent.orchestrator.call_llm", return_value=GOOD_PATCH):
            r = imp._improve_file(target, Evaluation(score=50.0), ["helper"])

        assert r.patched is True
        assert r.tests_passed is True
        assert "return x * 2" in target.read_text()
        assert not target.with_suffix(".py.bak").exists(), "backup must be cleaned up"

    def test_unparseable_patch_never_touches_the_file(self, sandbox, config):
        root, target, tests = sandbox
        original = target.read_text()
        imp = _improver(config, sandbox, BROKEN_PATCH)

        with mock_patch("panda_agent.orchestrator.call_llm", return_value=BROKEN_PATCH):
            r = imp._improve_file(target, Evaluation(score=50.0), ["helper"])

        assert r.patched is False
        assert target.read_text() == original, "invalid patch must not reach disk"

    def test_patch_targeting_missing_function_is_reported(self, sandbox, config):
        root, target, tests = sandbox
        original = target.read_text()
        imp = _improver(config, sandbox, MISSING_TARGET_PATCH)

        with mock_patch(
            "panda_agent.orchestrator.call_llm", return_value=MISSING_TARGET_PATCH
        ):
            r = imp._improve_file(target, Evaluation(score=50.0), ["helper"])

        assert r.patched is False
        assert target.read_text() == original

    def test_patch_failing_tests_is_reverted(self, sandbox, config):
        """A patch that breaks the suite must leave the file as it was."""
        root, target, tests = sandbox
        original = target.read_text()

        # Returns a string, so isinstance(..., int) fails.
        type_breaking = GOOD_PATCH.replace("return x * 2", 'return str(x)')
        imp = _improver(config, sandbox, type_breaking)

        with mock_patch("panda_agent.orchestrator.call_llm", return_value=type_breaking):
            r = imp._improve_file(target, Evaluation(score=50.0), ["helper"])

        assert r.patched is False
        assert target.read_text() == original
        assert not target.with_suffix(".py.bak").exists()

    def test_patch_passing_tests_but_regressing_is_reverted(self, sandbox, config):
        """The case unit tests cannot see.

        The patch is valid Python and the suite stays green, but measured task
        performance dropped. Before the regression gate this patch was kept.
        """
        root, target, tests = sandbox
        original = target.read_text()

        imp = _improver(config, sandbox, GOOD_PATCH)
        imp.baseline = _result(task_a=90.0, task_b=80.0)
        imp.benchmark_gate = lambda: _result(task_a=60.0, task_b=80.0)
        imp.tolerance = 2.0

        with mock_patch("panda_agent.orchestrator.call_llm", return_value=GOOD_PATCH):
            r = imp._improve_file(target, Evaluation(score=50.0), ["helper"])

        assert r.patched is False, "a regressing patch must not be kept"
        assert target.read_text() == original, "file must be restored"
        assert imp.last_reject_reason and "regression" in imp.last_reject_reason
        assert "task_a" in imp.last_reject_reason

    def test_patch_improving_benchmark_is_kept(self, sandbox, config):
        root, target, tests = sandbox
        imp = _improver(config, sandbox, GOOD_PATCH)
        imp.baseline = _result(task_a=60.0)
        imp.benchmark_gate = lambda: _result(task_a=85.0)

        with mock_patch("panda_agent.orchestrator.call_llm", return_value=GOOD_PATCH):
            r = imp._improve_file(target, Evaluation(score=50.0), ["helper"])

        assert r.patched is True
        assert "+25.0" in r.diff, "score delta should be recorded"

    def test_noise_within_tolerance_does_not_reject(self, sandbox, config):
        root, target, tests = sandbox
        imp = _improver(config, sandbox, GOOD_PATCH)
        imp.baseline = _result(task_a=80.0)
        imp.benchmark_gate = lambda: _result(task_a=78.5)
        imp.tolerance = 2.0

        with mock_patch("panda_agent.orchestrator.call_llm", return_value=GOOD_PATCH):
            r = imp._improve_file(target, Evaluation(score=50.0), ["helper"])

        assert r.patched is True

    def test_gate_is_skipped_when_not_configured(self, sandbox, config):
        """Backwards compatibility: no baseline means unit tests alone."""
        root, target, tests = sandbox
        imp = _improver(config, sandbox, GOOD_PATCH)
        assert imp.benchmark_gate is None

        with mock_patch("panda_agent.orchestrator.call_llm", return_value=GOOD_PATCH):
            r = imp._improve_file(target, Evaluation(score=50.0), ["helper"])

        assert r.patched is True


# ---------------------------------------------------------------------------
# Evaluator signal handling
# ---------------------------------------------------------------------------

class TestEvaluatorSignal:
    def test_unparseable_response_returns_none_after_retry(self, config):
        ev = Evaluator(config)
        with mock_patch(
            "panda_agent.orchestrator.call_llm", return_value="I cannot evaluate."
        ) as m:
            out = ev.evaluate(Task(instruction="x"), ExecutionResult())

        assert out is None, "must not fabricate a score"
        assert m.call_count == 2, "should retry once with an explicit instruction"
        assert ev.last_error

    def test_retry_succeeds_on_second_attempt(self, config):
        ev = Evaluator(config)
        responses = iter(["garbage", '{"score": 77}'])
        with mock_patch(
            "panda_agent.orchestrator.call_llm", side_effect=lambda *a, **k: next(responses)
        ):
            out = ev.evaluate(Task(instruction="x"), ExecutionResult())

        assert out is not None
        assert out.score == 77.0

    def test_clean_response_does_not_retry(self, config):
        ev = Evaluator(config)
        with mock_patch(
            "panda_agent.orchestrator.call_llm", return_value='{"score": 91}'
        ) as m:
            out = ev.evaluate(Task(instruction="x"), ExecutionResult())

        assert out.score == 91.0
        assert m.call_count == 1


# ---------------------------------------------------------------------------
# Loop-level behaviour
# ---------------------------------------------------------------------------

class _StubExecutor:
    def __init__(self):
        self.calls = 0

    def execute(self, task):
        self.calls += 1
        return ExecutionResult(success=True)


class _ScriptedEvaluator:
    """Yields a fixed sequence of scores; None means 'no signal'."""

    def __init__(self, scores):
        self.scores = list(scores)
        self.last_error = "scripted failure"

    def evaluate(self, task, result):
        if not self.scores:
            return None
        s = self.scores.pop(0)
        return None if s is None else Evaluation(score=float(s))


class _NoopImprover:
    def __init__(self):
        self.calls = 0

    def improve(self, evaluation):
        self.calls += 1
        return ImprovementResult(patched=False)


class TestRunEvolution:
    def test_stops_when_target_reached(self):
        ex, imp = _StubExecutor(), _NoopImprover()
        r = run_evolution(
            ex, _ScriptedEvaluator([95, 20, 20]), imp,
            Task(instruction="x"), target_score=90.0, max_rounds=3,
        )
        assert r.target_reached is True
        assert ex.calls == 1, "should stop immediately, not keep going"
        assert imp.calls == 0

    def test_missing_signal_skips_improvement_that_round(self):
        """A round with no parseable evaluation must not drive a patch."""
        ex, imp = _StubExecutor(), _NoopImprover()
        r = run_evolution(
            ex, _ScriptedEvaluator([None, 40]), imp,
            Task(instruction="x"), target_score=90.0, max_rounds=2,
        )
        assert imp.calls == 0, "no signal in round 1, last round skips improving"
        assert r.rounds[0].evaluation is None

    def test_reports_best_not_last_score(self):
        r = run_evolution(
            _StubExecutor(), _ScriptedEvaluator([85, 30, 40]), _NoopImprover(),
            Task(instruction="x"), target_score=99.0, max_rounds=3,
        )
        assert r.final_score == 85.0

    def test_records_every_round(self):
        r = run_evolution(
            _StubExecutor(), _ScriptedEvaluator([10, 20, 30]), _NoopImprover(),
            Task(instruction="x"), target_score=99.0, max_rounds=3,
        )
        assert [rr.round_num for rr in r.rounds] == [1, 2, 3]
        assert [rr.evaluation.score for rr in r.rounds] == [10.0, 20.0, 30.0]

    def test_emits_events(self):
        events = []
        run_evolution(
            _StubExecutor(), _ScriptedEvaluator([95]), _NoopImprover(),
            Task(instruction="x"), target_score=90.0, max_rounds=1,
            on_event=events.append,
        )
        types = [e.type for e in events]
        assert "executor_start" in types
        assert "evaluator_done" in types
        assert "target_reached" in types

    def test_emits_evaluator_error_when_signal_missing(self):
        events = []
        run_evolution(
            _StubExecutor(), _ScriptedEvaluator([None]), _NoopImprover(),
            Task(instruction="x"), target_score=90.0, max_rounds=1,
            on_event=events.append,
        )
        assert "evaluator_error" in [e.type for e in events]

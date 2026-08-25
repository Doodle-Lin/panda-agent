"""Tests for the PandaAgent framework core —types, orchestrator, improver.

Uses mock agents to verify the loop logic without real API calls.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from panda_agent.types import (
    Task,
    ExecutionResult,
    Evaluation,
    ImprovementResult,
    RoundResult,
    EvolutionResult,
    Event,
)
from panda_agent.executor import Executor
from panda_agent.evaluator import Evaluator
from panda_agent.improver import Improver, _extract_patch, _replace_function
from panda_agent.orchestrator import run_evolution
from panda_agent.llm import LLMConfig, call_llm


# ---------------------------------------------------------------------------
# Mock agents for testing
# ---------------------------------------------------------------------------

class MockExecutor(Executor):
    def __init__(self, output_path="/tmp/mock_output.png"):
        self._output = output_path
        self.call_count = 0

    def execute(self, task: Task) -> ExecutionResult:
        self.call_count += 1
        return ExecutionResult(
            output_path=self._output,
            tool_calls=[{"name": "mock_tool", "args": {}}],
        )


class MockEvaluator(Evaluator):
    def __init__(self, scores: list[float]):
        self._scores = scores
        self._idx = 0

    def evaluate(self, task: Task, result: ExecutionResult) -> Evaluation:
        score = self._scores[min(self._idx, len(self._scores) - 1)]
        self._idx += 1
        return Evaluation(
            score=score,
            issues=["mock issue"],
            root_cause="mock root cause",
            suggested_changes="mock suggestion",
        )


class MockImprover(Improver):
    def __init__(self, patched_results: list[ImprovementResult]):
        self._results = patched_results
        self._idx = 0

    @property
    def target_source_path(self) -> Path:
        return Path("/tmp/mock_tools.py")

    @property
    def test_path(self) -> Path:
        return Path("/tmp/mock_test.py")

    @property
    def project_root(self) -> Path:
        return Path("/tmp")

    @property
    def llm_config(self) -> LLMConfig:
        return LLMConfig(base_url="http://test/v1", api_key="test", model="test")

    @property
    def max_retries(self) -> int:
        return 1

    def improve(self, evaluation: Evaluation) -> ImprovementResult:
        result = self._results[min(self._idx, len(self._results) - 1)]
        self._idx += 1
        return result


# ---------------------------------------------------------------------------
# Type tests
# ---------------------------------------------------------------------------

class TestTypes:
    def test_task_defaults(self):
        t = Task(input_path="/img.jpg", instruction="blur background")
        assert t.metadata == {}

    def test_execution_result_defaults(self):
        r = ExecutionResult(output_path="/out.jpg")
        assert r.success is True
        assert r.error is None
        assert r.tool_calls == []

    def test_evaluation_defaults(self):
        e = Evaluation(score=85)
        assert e.issues == []
        assert e.root_cause == ""
        assert e.dimensions == {}

    def test_improvement_result_defaults(self):
        r = ImprovementResult()
        assert r.patched is False
        assert r.reverted is False
        assert r.attempts == 0

    def test_event_creation(self):
        ev = Event(type="test", message="hello", round=1)
        assert ev.type == "test"
        assert ev.data == {}

    def test_round_result(self):
        r = RoundResult(round_num=1)
        assert r.execution is None
        assert r.evaluation is None

    def test_evolution_result_defaults(self):
        r = EvolutionResult()
        assert r.rounds == []
        assert r.final_score == 0.0
        assert r.total_patches == 0


# ---------------------------------------------------------------------------
# Orchestrator tests
# ---------------------------------------------------------------------------

class TestOrchestrator:
    def test_target_reached_first_round(self):
        """Score >= target on round 1 →stop immediately."""
        executor = MockExecutor()
        evaluator = MockEvaluator([95.0])
        improver = MockImprover([])

        result = run_evolution(
            executor, evaluator, improver,
            Task(input_path="/img.jpg", instruction="test"),
            target_score=95.0,
            max_rounds=3,
        )

        assert result.target_reached is True
        assert len(result.rounds) == 1
        assert result.final_score == 95.0
        assert result.total_patches == 0

    def test_max_rounds_exhausted(self):
        """Score never reaches target →run all rounds."""
        executor = MockExecutor()
        evaluator = MockEvaluator([80.0, 80.0, 80.0])
        improver = MockImprover([
            ImprovementResult(patched=False, tests_passed=True, attempts=1),
            ImprovementResult(patched=False, tests_passed=True, attempts=1),
        ])

        result = run_evolution(
            executor, evaluator, improver,
            Task(input_path="/img.jpg", instruction="test"),
            target_score=95.0,
            max_rounds=3,
        )

        assert result.target_reached is False
        assert len(result.rounds) == 3
        assert result.final_score == 80.0

    def test_improvement_applied(self):
        """Improver patches code →patches counted."""
        executor = MockExecutor()
        evaluator = MockEvaluator([80.0, 95.0])
        improver = MockImprover([
            ImprovementResult(patched=True, tests_passed=True, attempts=2),
        ])

        result = run_evolution(
            executor, evaluator, improver,
            Task(input_path="/img.jpg", instruction="test"),
            target_score=95.0,
            max_rounds=3,
        )

        assert result.total_patches == 1
        assert result.target_reached is True
        assert len(result.rounds) == 2

    def test_improver_skipped_last_round(self):
        """Last round →no improvement attempt."""
        executor = MockExecutor()
        evaluator = MockEvaluator([80.0, 80.0, 80.0])
        improver = MockImprover([
            ImprovementResult(patched=False, tests_passed=True, attempts=1),
            ImprovementResult(patched=False, tests_passed=True, attempts=1),
        ])

        result = run_evolution(
            executor, evaluator, improver,
            Task(input_path="/img.jpg", instruction="test"),
            target_score=95.0,
            max_rounds=3,
        )

        # Improver should only be called on rounds 1 and 2 (not 3)
        assert improver._idx == 2

    def test_events_emitted(self):
        """Events are emitted for each step."""
        events = []
        executor = MockExecutor()
        evaluator = MockEvaluator([95.0])
        improver = MockImprover([])

        run_evolution(
            executor, evaluator, improver,
            Task(input_path="/img.jpg", instruction="test"),
            target_score=95.0,
            max_rounds=3,
            on_event=lambda ev: events.append(ev),
        )

        types = [e.type for e in events]
        assert "executor_start" in types
        assert "executor_done" in types
        assert "evaluator_start" in types
        assert "evaluator_done" in types
        assert "target_reached" in types

    def test_improver_exception_handled(self):
        """Improver raising exception →loop continues."""
        class CrashingImprover(MockImprover):
            def improve(self, evaluation):
                raise RuntimeError("crash")

        executor = MockExecutor()
        evaluator = MockEvaluator([80.0, 80.0])
        improver = CrashingImprover([])

        result = run_evolution(
            executor, evaluator, improver,
            Task(input_path="/img.jpg", instruction="test"),
            target_score=95.0,
            max_rounds=2,
        )

        assert result.target_reached is False
        assert len(result.rounds) == 2

    def test_best_score_tracked(self):
        """Best score across rounds is tracked."""
        executor = MockExecutor()
        evaluator = MockEvaluator([70.0, 85.0, 75.0])
        improver = MockImprover([
            ImprovementResult(patched=False, tests_passed=True, attempts=1),
            ImprovementResult(patched=False, tests_passed=True, attempts=1),
        ])

        result = run_evolution(
            executor, evaluator, improver,
            Task(input_path="/img.jpg", instruction="test"),
            target_score=95.0,
            max_rounds=3,
        )

        assert result.final_score == 85.0


# ---------------------------------------------------------------------------
# Improver utility tests
# ---------------------------------------------------------------------------

class TestImproverUtils:
    def test_extract_patch_with_markers(self):
        response = """Some text
PATCH_START
```python
def foo():
    return 42
```
PATCH_END
EXPLANATION: changed foo
"""
        patch = _extract_patch(response)
        assert "def foo" in patch
        assert "return 42" in patch

    def test_extract_patch_no_markers(self):
        response = "NO_CHANGE"
        assert _extract_patch(response) == ""

    def test_replace_function(self):
        source = """def foo():
    return 1

def bar():
    return 2
"""
        new_code = "def foo():\n    return 42\n"
        result = _replace_function(source, new_code)
        assert "return 42" in result
        assert "def bar" in result

    def test_replace_function_not_found(self):
        source = "def foo():\n    return 1\n"
        new_code = "def baz():\n    return 2\n"
        result = _replace_function(source, new_code)
        assert result == source  # unchanged


# ---------------------------------------------------------------------------
# LLM caller tests (mocked)
# ---------------------------------------------------------------------------

class TestLLM:
    def test_llm_config_defaults(self):
        config = LLMConfig(
            base_url="http://test/v1",
            api_key="test-key",
            model="test-model",
        )
        assert config.max_tokens == 8192
        assert config.temperature == 0.2

    @patch("panda_agent.llm.requests.post")
    def test_call_llm_content(self, mock_post):
        """Non-reasoning model: content has output."""
        mock_resp = MagicMock()
        mock_resp.iter_lines.return_value = [
            'data: {"choices":[{"delta":{"content":"hello"}}]}',
            'data: {"choices":[{"delta":{"content":" world"}}]}',
            "data: [DONE]",
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        config = LLMConfig(base_url="http://test/v1", api_key="k", model="m")
        result = call_llm("test prompt", config)
        assert result == "hello world"

    @patch("panda_agent.llm.requests.post")
    def test_call_llm_reasoning_fallback(self, mock_post):
        """Reasoning model: content empty, reasoning_content has output."""
        mock_resp = MagicMock()
        mock_resp.iter_lines.return_value = [
            'data: {"choices":[{"delta":{"content":"","reasoning_content":"thinking..."}}]}',
            'data: {"choices":[{"delta":{"content":"","reasoning_content":" answer"}}]}',
            "data: [DONE]",
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        config = LLMConfig(base_url="http://test/v1", api_key="k", model="m")
        result = call_llm("test prompt", config)
        assert "thinking" in result
        assert "answer" in result

    @patch("panda_agent.llm.requests.post")
    def test_call_llm_timeout(self, mock_post):
        """Timeout →returns NO_CHANGE error."""
        import requests as req
        mock_post.side_effect = req.Timeout("timeout")

        config = LLMConfig(base_url="http://test/v1", api_key="k", model="m")
        result = call_llm("test", config)
        assert "NO_CHANGE" in result
        assert "timeout" in result.lower()

"""Tests for self-evolution: Evaluator, Learner, Improver, run_evolution.

Uses mocks for LLM calls — no real API needed.
"""

import json
import pytest
from unittest.mock import MagicMock, patch, call
from pathlib import Path

from panda_agent.config import Config
from panda_agent.types import (
    Task, ExecutionResult, Evaluation, ImprovementResult,
    RoundResult, EvolutionResult, Event,
    ExecutionTrace, TurnRecord, ErrorRecord, LearningResult,
)
from panda_agent.orchestrator import (
    Evaluator, Learner, Improver, run_evolution,
    _extract_patch, _replace_function, _try_fix_syntax,
)


# ---------------------------------------------------------------------------
# Evaluator tests (1-3)
# ---------------------------------------------------------------------------

class TestEvaluator:
    """Test Evaluator LLM response parsing."""

    @patch("panda_agent.orchestrator.call_llm")
    def test_evaluator_parse_json(self, mock_llm):
        """Test 1: Evaluator parses plain JSON response and extracts all fields."""
        mock_llm.return_value = (
            '{"score": 85, "issues": ["no tool calls"], '
            '"root_cause": "prompt", "suggested_changes": "add instruction"}'
        )
        config = Config()
        evaluator = Evaluator(config)
        task = Task(instruction="test task")
        result = ExecutionResult(success=True, tool_calls=[], error=None)

        evaluation = evaluator.evaluate(task, result)

        assert evaluation.score == 85
        assert evaluation.issues == ["no tool calls"]
        assert evaluation.root_cause == "prompt"
        assert evaluation.suggested_changes == "add instruction"

    @patch("panda_agent.orchestrator.call_llm")
    def test_evaluator_parse_json_in_code_block(self, mock_llm):
        """Test 2: Evaluator parses JSON wrapped in ```json code block."""
        mock_llm.return_value = '```json\n{"score": 70, "issues": []}\n```'
        config = Config()
        evaluator = Evaluator(config)
        task = Task(instruction="test task")
        result = ExecutionResult(success=True, tool_calls=[])

        evaluation = evaluator.evaluate(task, result)

        assert evaluation.score == 70
        assert evaluation.issues == []

    @patch("panda_agent.orchestrator.call_llm")
    def test_evaluator_llm_error(self, mock_llm):
        """Test 3: Evaluator returns default score=50 on LLM error."""
        mock_llm.return_value = "ERROR: timeout"
        config = Config()
        evaluator = Evaluator(config)
        task = Task(instruction="test task")
        result = ExecutionResult(success=False, error="some error")

        evaluation = evaluator.evaluate(task, result)

        assert evaluation.score == 50
        assert "LLM call failed" in evaluation.issues


# ---------------------------------------------------------------------------
# Learner tests (4-9)
# ---------------------------------------------------------------------------

class TestLearner:
    """Test Learner lesson extraction, memory writing, and pattern tracking."""

    @patch("panda_agent.orchestrator.call_llm")
    def test_learner_extracts_lessons(self, mock_llm):
        """Test 4: Learner extracts lessons from LLM response."""
        mock_llm.return_value = (
            '{"lessons": ["use ls -la"], "recurring_errors": [], '
            '"is_structural": false}'
        )
        config = Config()
        config.memory.enabled = False
        learner = Learner(config)
        task = Task(instruction="list files on desktop")
        result = ExecutionResult(success=True, tool_calls=[{"name": "run_command"}])
        evaluation = Evaluation(score=80)

        learning = learner.learn(task, result, evaluation)

        assert len(learning.lessons) > 0
        assert "use ls -la" in learning.lessons

    @patch("panda_agent.orchestrator.call_llm")
    def test_learner_writes_to_memory(self, mock_llm):
        """Test 5: Learner writes lessons to memory and sets memory_written=True."""
        mock_llm.return_value = (
            '{"lessons": ["lesson1", "lesson2"], "recurring_errors": [], '
            '"is_structural": false}'
        )
        config = Config()
        config.memory.enabled = False
        learner = Learner(config)
        # Inject mock memory client
        mock_memory = MagicMock()
        learner.memory = mock_memory
        task = Task(instruction="test task")
        result = ExecutionResult(success=True, tool_calls=[])
        evaluation = Evaluation(score=80)

        learning = learner.learn(task, result, evaluation)

        assert learning.memory_written is True
        mock_memory.write.assert_called()

    @patch("panda_agent.orchestrator.call_llm")
    def test_learner_tracks_error_patterns_3x(self, mock_llm):
        """Test 6: After 3 calls with same recurring_errors, trigger_evolution=True."""
        mock_llm.return_value = json.dumps({
            "lessons": [],
            "recurring_errors": ["same_error"],
            "is_structural": True,
            "structural_reason": "brain.py prompt issue",
        })
        config = Config()
        config.memory.enabled = False
        learner = Learner(config)
        task = Task(instruction="test task")
        result = ExecutionResult(success=False, error="fail")
        evaluation = Evaluation(score=50)  # < 70

        learning = None
        for _ in range(3):
            learning = learner.learn(task, result, evaluation)

        assert learning.trigger_evolution is True

    @patch("panda_agent.orchestrator.call_llm")
    def test_learner_no_trigger_below_3(self, mock_llm):
        """Test 7: After 2 calls with same pattern, trigger_evolution=False."""
        mock_llm.return_value = json.dumps({
            "lessons": [],
            "recurring_errors": ["same_error"],
            "is_structural": True,
            "structural_reason": "brain.py prompt issue",
        })
        config = Config()
        config.memory.enabled = False
        learner = Learner(config)
        task = Task(instruction="test task")
        result = ExecutionResult(success=False, error="fail")
        evaluation = Evaluation(score=50)

        learning = None
        for _ in range(2):
            learning = learner.learn(task, result, evaluation)

        assert learning.trigger_evolution is False

    @patch("panda_agent.orchestrator.call_llm")
    def test_learner_structural_triggers(self, mock_llm):
        """Test 8: Structural issue with score<70 triggers after 3 occurrences."""
        mock_llm.return_value = json.dumps({
            "lessons": [],
            "recurring_errors": [],
            "is_structural": True,
            "structural_reason": "tools.py missing function",
        })
        config = Config()
        config.memory.enabled = False
        learner = Learner(config)
        task = Task(instruction="test task")
        result = ExecutionResult(success=False, error="fail")
        evaluation = Evaluation(score=65)  # < 70

        learning = None
        for _ in range(3):
            learning = learner.learn(task, result, evaluation)

        assert learning.trigger_evolution is True
        assert "tools.py" in learning.trigger_reason or "structural" in learning.trigger_reason.lower()

    @patch("panda_agent.orchestrator.call_llm")
    def test_learner_parse_fallback(self, mock_llm):
        """Test 9: Non-JSON LLM response doesn't crash, returns empty lessons."""
        mock_llm.return_value = "This is not JSON at all, just plain text."
        config = Config()
        config.memory.enabled = False
        learner = Learner(config)
        task = Task(instruction="test task")
        result = ExecutionResult(success=True, tool_calls=[])
        evaluation = Evaluation(score=80)

        # Should not crash
        learning = learner.learn(task, result, evaluation)

        assert learning.lessons == []
        assert learning.trigger_evolution is False


# ---------------------------------------------------------------------------
# Improver tests (10)
# ---------------------------------------------------------------------------

class TestImprover:
    """Test Improver NO_CHANGE handling."""

    @patch("panda_agent.orchestrator.shutil.copy2")
    @patch("panda_agent.orchestrator.call_llm")
    def test_improver_no_change(self, mock_llm, mock_copy):
        """Test 10: LLM returns NO_CHANGE → patched=False."""
        mock_llm.return_value = "NO_CHANGE"
        config = Config()
        improver = Improver(config)
        evaluation = Evaluation(
            score=50, issues=["test issue"],
            root_cause="test cause", suggested_changes="fix it",
        )

        # Use mock path to avoid touching real source files
        mock_path = MagicMock()
        mock_path.name = "test.py"
        mock_path.read_text.return_value = "def foo():\n    pass\n"

        with patch("panda_agent.orchestrator._extract_relevant",
                   return_value="def foo():\n    pass\n"):
            result = improver._improve_file(mock_path, evaluation, ["foo"])

        assert result.patched is False


# ---------------------------------------------------------------------------
# _extract_patch tests (11)
# ---------------------------------------------------------------------------

class TestExtractPatch:
    """Test _extract_patch supports 5 response formats."""

    def test_extract_patch_5_formats(self):
        """Test 11: _extract_patch handles 5 different LLM response formats."""
        # Format 1: PATCH_START with python code fence
        r1 = "PATCH_START\n```python\ndef foo():\n    return 42\n```\nPATCH_END"
        p1 = _extract_patch(r1)
        assert "def foo" in p1
        assert "return 42" in p1

        # Format 2: PATCH_START ... PATCH_END (no code fence)
        r2 = "PATCH_START\ndef foo():\n    return 42\nPATCH_END"
        p2 = _extract_patch(r2)
        assert "def foo" in p2
        assert "return 42" in p2

        # Format 3: python code fence without PATCH markers
        r3 = "```python\ndef foo():\n    return 42\n```"
        p3 = _extract_patch(r3)
        assert "def foo" in p3
        assert "return 42" in p3

        # Format 4: generic code fence with def
        r4 = "```\ndef foo():\n    return 42\n```"
        p4 = _extract_patch(r4)
        assert "def foo" in p4
        assert "return 42" in p4

        # Format 5: raw function definition
        r5 = "def foo():\n    return 42\n\nEXPLANATION: changed return value"
        p5 = _extract_patch(r5)
        assert "def foo" in p5
        assert "return 42" in p5


# ---------------------------------------------------------------------------
# _replace_function tests (12-13)
# ---------------------------------------------------------------------------

class TestReplaceFunction:
    """Test _replace_function replaces function definitions."""

    def test_replace_function(self):
        """Test 12: Replace a single function in source."""
        source = "def old():\n    return 1\n\ndef other():\n    return 2\n"
        new_code = "def old():\n    return 42\n"
        result = _replace_function(source, new_code)

        assert "return 42" in result
        assert "return 1" not in result
        assert "def other" in result
        assert "return 2" in result

    def test_replace_function_multiple(self):
        """Test 13: Replace multiple functions in one pass."""
        source = "def func_a():\n    return 1\n\ndef func_b():\n    return 2\n"
        new_code = "def func_a():\n    return 10\n\ndef func_b():\n    return 20\n"
        result = _replace_function(source, new_code)

        assert "return 10" in result
        assert "return 20" in result
        assert "return 1\n" not in result
        assert "return 2\n" not in result


# ---------------------------------------------------------------------------
# _try_fix_syntax tests (14-16)
# ---------------------------------------------------------------------------

class TestTryFixSyntax:
    """Test _try_fix_syntax auto-fixes common LLM syntax errors."""

    def test_try_fix_syntax_chinese_quotes(self):
        """Test 14: Chinese quotes (U+201C/U+201D) → ASCII quotes."""
        code = 'x = \u201chello\u201d'  # Chinese double quotes
        error = SyntaxError("invalid syntax")
        fixed = _try_fix_syntax(code, error)

        assert fixed != ""
        assert '\u201c' not in fixed
        assert '\u201d' not in fixed
        assert '"' in fixed
        assert "hello" in fixed

    def test_try_fix_syntax_unterminated_string(self):
        """Test 15: Unterminated string literal gets closing quote added."""
        code = 'x = "hello'  # Missing closing quote
        error = SyntaxError("unterminated string literal")
        error.lineno = 1
        fixed = _try_fix_syntax(code, error)

        assert fixed != ""
        # After fix, quote count should be even
        assert fixed.count('"') % 2 == 0
        assert "hello" in fixed

    def test_try_fix_syntax_eof(self):
        """Test 16: Unexpected EOF — missing brackets get closed."""
        code = "def foo():\n    x = (1 + 2"  # Missing closing paren
        error = SyntaxError("unexpected EOF while parsing")
        fixed = _try_fix_syntax(code, error)

        assert fixed != ""
        assert fixed.count("(") == fixed.count(")")


# ---------------------------------------------------------------------------
# run_evolution tests (17-18)
# ---------------------------------------------------------------------------

class TestRunEvolution:
    """Test run_evolution stopping conditions."""

    @patch("panda_agent.orchestrator.call_llm")
    def test_run_evolution_stops_on_target(self, mock_llm):
        """Test 17: Evolution stops after 1 round when score exceeds target."""
        mock_executor = MagicMock()
        mock_executor.execute.return_value = ExecutionResult(
            success=True, tool_calls=[{"name": "read_file"}],
        )

        mock_evaluator = MagicMock()
        mock_evaluator.evaluate.return_value = Evaluation(score=95)  # > target=90

        mock_learner = MagicMock()
        mock_learner.learn.return_value = LearningResult(lessons=["lesson1"])

        mock_improver = MagicMock()

        task = Task(instruction="test task")
        result = run_evolution(
            mock_executor, mock_evaluator, mock_learner, mock_improver,
            task, target_score=90, max_rounds=5, config=Config(),
        )

        assert len(result.rounds) == 1  # Only 1 round
        assert result.target_reached is True
        assert result.final_score == 95

    def test_run_evolution_stops_on_stale(self):
        """Test 18: Evolution stops after 3 consecutive stale rounds."""
        mock_executor = MagicMock()
        mock_executor.execute.return_value = ExecutionResult(
            success=True, tool_calls=[],
        )

        mock_evaluator = MagicMock()
        mock_evaluator.evaluate.return_value = Evaluation(score=50)  # Same every round

        mock_learner = MagicMock()
        mock_learner.learn.return_value = LearningResult()

        mock_improver = MagicMock()
        mock_improver.improve.return_value = ImprovementResult(patched=False)

        task = Task(instruction="test task")
        result = run_evolution(
            mock_executor, mock_evaluator, mock_learner, mock_improver,
            task, target_score=90, max_rounds=10, config=Config(),
        )

        # Round 1: best=50, stale=0
        # Round 2: stale=1
        # Round 3: stale=2
        # Round 4: stale=3 → stop
        assert len(result.rounds) == 4
        assert result.target_reached is False
        assert result.final_score == 50


# ---------------------------------------------------------------------------
# ExecutionTrace tests (19)
# ---------------------------------------------------------------------------

class TestExecutionTrace:
    """Test ExecutionTrace add_error and add_repair methods."""

    def test_execution_trace_methods(self):
        """Test 19: add_error and add_repair methods work correctly."""
        trace = ExecutionTrace()

        trace.add_error("FileNotFoundError: missing.txt")
        trace.add_error("PermissionError: access denied")

        trace.add_repair("retry with absolute path")
        trace.add_repair("fallback to read_file tool")

        assert len(trace.errors) == 2
        assert "FileNotFoundError: missing.txt" in trace.errors
        assert "PermissionError: access denied" in trace.errors

        assert len(trace.self_repairs) == 2
        assert "retry with absolute path" in trace.self_repairs
        assert "fallback to read_file tool" in trace.self_repairs


# ---------------------------------------------------------------------------
# RoundResult / EvolutionResult structure tests (20)
# ---------------------------------------------------------------------------

class TestResultStructure:
    """Test RoundResult and EvolutionResult data structures."""

    def test_round_result_structure(self):
        """Test 20: RoundResult and EvolutionResult have correct fields and defaults."""
        # RoundResult
        rr = RoundResult(round_num=1)
        assert rr.round_num == 1
        assert rr.execution is None
        assert rr.evaluation is None
        assert rr.improvement is None
        assert rr.learning is None

        # RoundResult with data
        rr2 = RoundResult(round_num=2)
        rr2.execution = ExecutionResult(success=True)
        rr2.evaluation = Evaluation(score=85)
        rr2.learning = LearningResult(lessons=["test"])
        rr2.improvement = ImprovementResult(patched=True)
        assert rr2.round_num == 2
        assert rr2.execution.success is True
        assert rr2.evaluation.score == 85
        assert rr2.learning.lessons == ["test"]
        assert rr2.improvement.patched is True

        # EvolutionResult
        er = EvolutionResult()
        assert er.rounds == []
        assert er.final_score == 0.0
        assert er.total_patches == 0
        assert er.total_lessons == 0
        assert er.target_reached is False

        # EvolutionResult with data
        er2 = EvolutionResult()
        er2.rounds.append(rr)
        er2.final_score = 85.0
        er2.total_patches = 2
        er2.total_lessons = 5
        er2.target_reached = True
        assert len(er2.rounds) == 1
        assert er2.rounds[0].round_num == 1
        assert er2.final_score == 85.0
        assert er2.total_patches == 2
        assert er2.total_lessons == 5
        assert er2.target_reached is True

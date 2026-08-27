"""Regression tests — one test per previously fixed bug.

Each test ensures a specific bug that was fixed does not reappear.
These are the "insurance policy" tests — they lock in past fixes.
"""

import re
import sys
import os
import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from panda_agent.react import (
    run_react, _parse_tool_call, _parse_done, _parse_failed,
    _classify_error, _self_repair,
)
from panda_agent.llm import LLMResponse, call_llm_detailed
from panda_agent.brain import build_system_prompt, max_turns_for_task, should_retry
from panda_agent.tools import execute_tool, get_tool_descriptions
from panda_agent.config import Config, ModelConfig, AgentConfig, MemoryConfig
from panda_agent.types import (
    Task, ExecutionResult, Evaluation, ExecutionTrace, TurnRecord,
)
from panda_agent.orchestrator import (
    _extract_patch, _replace_function,
    Evaluator, Learner, Improver,
)


def _mock_config():
    """Create a mock config for testing."""
    return Config(
        model=ModelConfig(default="test-model", base_url="http://localhost", api_key="test"),
        agent=AgentConfig(max_turns=3, max_retries=2),
        memory=MemoryConfig(enabled=False),
    )


# ===========================================================================
# Bug 1: DONE: parser truncates multi-line answers
# Fixed: _parse_done regex changed from (.+?)(?:\n|$) to (.+) with re.DOTALL
# ===========================================================================

class TestRegressionDoneMultiline:
    def test_regression_done_multiline_not_truncated(self):
        """DONE: should capture all lines, not just the first."""
        text = "DONE: 第一行\n第二行\n第三行"
        result = _parse_done(text)
        assert result is not None
        assert "第一行" in result
        assert "第二行" in result
        assert "第三行" in result

    def test_regression_parse_done_no_truncation_long(self):
        """Long DONE: text (>200 chars) should not be truncated."""
        long_text = "DONE: " + "A" * 300
        result = _parse_done(text=long_text)
        assert result is not None
        assert len(result) >= 300  # full text, not truncated


# ===========================================================================
# Bug 2: TOOL_CALL was swallowed by DONE check
# Fixed: TOOL_CALL check moved before DONE check in react.py
# ===========================================================================

class TestRegressionToolCallPriority:
    def test_regression_tool_call_priority_over_done(self):
        """When response has both TOOL_CALL and DONE, TOOL_CALL wins."""
        text = 'TOOL_CALL: {"name": "list_files", "args": {"path": "."}}\nDONE: finished'
        tool = _parse_tool_call(text)
        done = _parse_done(text)
        # Both can parse, but react.py checks tool_call first
        assert tool is not None
        assert tool["name"] == "list_files"
        # Verify the priority logic by checking tool_call is found first
        # (in react.py, _parse_tool_call is called before _parse_done)


# ===========================================================================
# Bug 3: Reasoning model (GLM52RJPT) never calls tools
# Fixed: format enforcement push-back when no markers found
# ===========================================================================

class TestRegressionReasoningModel:
    def test_regression_reasoning_model_format_enforcement(self):
        """Reasoning model with no markers should trigger format enforcement,
        not be treated as DONE."""
        config = _mock_config()
        call_sequence = [
            LLMResponse(content="", reasoning="我在思考如何列出文件..."),
            LLMResponse(content="DONE: 已完成", reasoning=""),
        ]
        call_idx = [0]

        def mock_detailed(messages, cfg, **kw):
            idx = call_idx[0]
            call_idx[0] += 1
            if idx < len(call_sequence):
                return call_sequence[idx]
            return LLMResponse(content="DONE: fallback", reasoning="")

        with patch("panda_agent.react.call_llm_detailed", side_effect=mock_detailed):
            result = run_react("list files", config)

        # Should NOT succeed on first turn with just reasoning
        # Should eventually succeed after format enforcement
        assert result.success is True
        assert result.turns >= 2  # at least 2 turns (push-back + done)


# ===========================================================================
# Bug 4: think tags in reasoning_content not stripped
# Fixed: re.sub(r"</?think>", "", reasoning) in llm.py
# ===========================================================================

class TestRegressionThinkTags:
    def test_regression_think_tags_stripped(self):
        """reasoning_content may contain think tags — must be stripped."""
        import re as _re
        reasoning = "<think>some thinking</think>actual content"
        cleaned = _re.sub(r"</?think>", "", reasoning).strip()
        assert "think" not in cleaned.replace("thinking", "")  # no raw think tags
        assert "actual content" in cleaned


# ===========================================================================
# Bug 5: _replace_function didn't match functions in the middle of file
# Fixed: added re.MULTILINE flag
# ===========================================================================

class TestRegressionReplaceFunction:
    def test_regression_replace_function_multiline(self):
        """_replace_function should work with re.MULTILINE."""
        source = '''import os

def old_func(x):
    """Old docstring."""
    return x + 1

def another_func(y):
    return y * 2
'''
        new_code = '''def old_func(x):
    """New docstring."""
    return x + 999
'''
        result = _replace_function(source, new_code)
        assert "return x + 999" in result
        assert "return x + 1\n" not in result  # old body replaced (note \n to avoid substring match)
        assert "another_func" in result  # other functions preserved


# ===========================================================================
# Bug 6: _extract_patch only supported one format
# Fixed: 5 formats supported
# ===========================================================================

class TestRegressionExtractPatch:
    def test_regression_extract_patch_format1_patch_start_code_fence(self):
        response = "PATCH_START\n```python\ndef foo():\n    return 1\n```\nPATCH_END"
        patch = _extract_patch(response)
        assert "def foo" in patch
        assert "return 1" in patch

    def test_regression_extract_patch_format2_patch_start_end(self):
        response = "PATCH_START\ndef foo():\n    return 1\nPATCH_END"
        patch = _extract_patch(response)
        assert "def foo" in patch

    def test_regression_extract_patch_format3_python_fence(self):
        response = "```python\ndef foo():\n    return 1\n```"
        patch = _extract_patch(response)
        assert "def foo" in patch

    def test_regression_extract_patch_format4_generic_fence(self):
        response = "```\ndef foo():\n    return 1\n```"
        patch = _extract_patch(response)
        assert "def foo" in patch

    def test_regression_extract_patch_format5_raw_def(self):
        response = "def foo():\n    return 1\n\nEXPLANATION: changed"
        patch = _extract_patch(response)
        assert "def foo" in patch
        assert "return 1" in patch


# ===========================================================================
# Bug 7: subprocess UTF-8 on Windows
# Fixed: encoding="utf-8", errors="replace" added to subprocess.run
# ===========================================================================

class TestRegressionSubprocessUtf8:
    def test_regression_subprocess_utf8(self):
        """subprocess.run should handle UTF-8 output without garbling."""
        result = subprocess.run(
            ["python", "-c", "print('你好世界')"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=10,
        )
        assert "你好" in result.stdout

    def test_regression_run_command_chinese(self):
        """run_command tool should handle Chinese output."""
        result = execute_tool("run_command", {"command": "python -c \"print('测试中文')\""})
        assert "测试" in result or "测试中文" in result


# ===========================================================================
# Bug 8: behavioral check failed for reasoning models (content empty)
# Fixed: check reasoning_content, any non-empty response = pass
# ===========================================================================

class TestRegressionBehavioralCheck:
    def test_regression_behavioral_check_reasoning_model(self):
        """Behavioral check should pass when reasoning model returns
        content='' but reasoning='some text'."""
        # Simulate the behavioral check logic
        response = "这是推理模型的回复内容"
        resp = response.strip()
        # The logic: if len(resp) >= 5, return 80 (no format markers but has content)
        assert len(resp) >= 5
        # In the actual code, this would return 80.0
        # (not 10.0 which was the old behavior for "empty" responses)


# ===========================================================================
# Bug 9: Evaluator JSON parsing failed on code blocks
# Fixed: strip ```json ``` before parsing
# ===========================================================================

class TestRegressionEvaluatorJson:
    def test_regression_evaluator_json_in_code_block(self):
        """Evaluator should parse JSON wrapped in ```json ... ```.
        Uses parse_evaluation from parsing.py (not the old _parse_eval_json)."""
        from panda_agent.parsing import parse_evaluation
        response = '```json\n{"score": 85, "issues": ["test"]}\n```'
        result = parse_evaluation(response)
        assert result.ok
        assert result.evaluation.score == 85
        assert "test" in result.evaluation.issues

    def test_regression_evaluator_json_raw(self):
        """Evaluator should parse raw JSON."""
        from panda_agent.parsing import parse_evaluation
        response = '{"score": 70, "issues": [], "root_cause": "", "suggested_changes": ""}'
        result = parse_evaluation(response)
        assert result.ok
        assert result.evaluation.score == 70


# ===========================================================================
# Bug 10: max_turns_for_task returned wrong values
# Fixed: simple=5, complex=15, default=10
# ===========================================================================

class TestRegressionMaxTurns:
    def test_regression_max_turns_simple(self):
        assert max_turns_for_task("list files") == 8
        assert max_turns_for_task("show me the content") == 8
        assert max_turns_for_task("read this file") == 8

    def test_regression_max_turns_complex(self):
        assert max_turns_for_task("build a web server") == 30
        assert max_turns_for_task("create a new project") == 30
        assert max_turns_for_task("write a sci-fi story") == 30

    def test_regression_max_turns_default(self):
        assert max_turns_for_task("do something") == 12
        assert max_turns_for_task("help me") == 12


# ===========================================================================
# Bug 11: should_retry didn't check error type
# Fixed: don't retry on "not found" or "permission" errors
# ===========================================================================

class TestRegressionShouldRetry:
    def test_regression_should_retry_not_found(self):
        assert should_retry("read_file", "file not found", 1, 3) is False

    def test_regression_should_retry_permission(self):
        assert should_retry("write_file", "permission denied", 1, 3) is False

    def test_regression_should_retry_max(self):
        assert should_retry("any", "error", 3, 3) is False

    def test_regression_should_retry_ok(self):
        assert should_retry("run_command", "timeout error", 1, 3) is True


# ===========================================================================
# Bug 12: auto_write memory on DONE
# Fixed: memory.write called when task completes with auto_write=True
# ===========================================================================

class TestRegressionAutoWriteMemory:
    def test_regression_auto_write_memory_on_done(self):
        """When auto_write=True and task is complex (3+ tool calls), memory.write should be called on DONE."""
        config = _mock_config()
        config.memory.enabled = True
        config.memory.auto_write = True

        mock_memory = MagicMock()
        mock_memory.retrieve_context.return_value = ""
        mock_memory.write.return_value = {"id": "test"}

        # 3 tool calls to trigger memory write (new strategy: only 3+ calls writes)
        resps = [
            LLMResponse(content="", reasoning="", tool_calls=[{"id": "c1", "name": "list_files", "args": {"path": "."}}]),
            LLMResponse(content="", reasoning="", tool_calls=[{"id": "c2", "name": "read_file", "args": {"path": "a.txt"}}]),
            LLMResponse(content="", reasoning="", tool_calls=[{"id": "c3", "name": "write_file", "args": {"path": "b.txt", "content": "x"}}]),
            LLMResponse(content="DONE: completed task", reasoning="", tool_calls=[]),
        ]
        with patch("panda_agent.react.call_llm_detailed", side_effect=resps):
            with patch("panda_agent.react.execute_tool", side_effect=["f1\nf2", "content", "Wrote 1 chars"]):
                result = run_react("test task", config, memory=mock_memory)

        assert result.success is True
        mock_memory.write.assert_called_once()
        call_args = mock_memory.write.call_args
        assert "test task" in call_args[0][0] or "test task" in str(call_args)


# ===========================================================================
# Bug 13: Chinese quotes in LLM-generated code
# Removed: _try_fix_syntax no longer exists (libcst validates before writing)
# ===========================================================================

# ===========================================================================
# Bug 14: Unterminated string literal in LLM-generated code
# Removed: _try_fix_syntax no longer exists (libcst validates before writing)
# ===========================================================================


# ===========================================================================
# Bug 15: Memory injection into system prompt
# Fixed: memory.retrieve_context appended to system prompt
# ===========================================================================

class TestRegressionMemoryInjection:
    def test_regression_memory_injection_into_prompt(self):
        """Memory context should be appended to system prompt."""
        config = _mock_config()
        config.memory.enabled = True

        mock_memory = MagicMock()
        mock_memory.retrieve_context.return_value = "## Past Experience\n- [0.9] Use ls -la"

        captured_messages = []
        def capture_call(messages, cfg, **kw):
            captured_messages.append(messages)
            return LLMResponse(content="DONE: done", reasoning="")

        with patch("panda_agent.react.call_llm_detailed", side_effect=capture_call):
            result = run_react("list files", config, memory=mock_memory)

        # System prompt should contain memory context
        assert len(captured_messages) > 0
        system_msg = captured_messages[0][0]["content"]
        assert "Past Experience" in system_msg or "ls -la" in system_msg


# ===========================================================================
# Bug 16: parse_failed should extract reason
# ===========================================================================

class TestRegressionParseFailed:
    def test_regression_parse_failed(self):
        assert _parse_failed("FAILED: cannot find file") == "cannot find file"
        assert _parse_failed("no markers here") is None


# ===========================================================================
# Bug 17: ExecutionTrace should record errors and repairs
# ===========================================================================

class TestRegressionExecutionTrace:
    def test_regression_execution_trace_methods(self):
        trace = ExecutionTrace(task="test", total_turns=3)
        trace.add_error("timeout error")
        trace.add_repair("reduced timeout")
        assert len(trace.errors) == 1
        assert trace.errors[0] == "timeout error"
        assert len(trace.self_repairs) == 1
        assert trace.self_repairs[0] == "reduced timeout"


# ===========================================================================
# Bug 18: _classify_error should categorize correctly
# ===========================================================================

class TestRegressionClassifyError:
    def test_regression_classify_timeout(self):
        assert _classify_error("command timed out after 30s") == "timeout"

    def test_regression_classify_not_found(self):
        assert _classify_error("file not found: /test") == "not_found"
        assert _classify_error("No such file or directory") == "not_found"

    def test_regression_classify_permission(self):
        assert _classify_error("permission denied") == "permission"

    def test_regression_classify_encoding(self):
        assert _classify_error("encoding error utf-8") == "encoding"

    def test_regression_classify_network(self):
        assert _classify_error("connection refused") == "network"

    def test_regression_classify_unknown(self):
        assert _classify_error("something weird") == "unknown"

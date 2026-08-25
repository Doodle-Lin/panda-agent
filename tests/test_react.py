"""ReAct loop integration tests — mock LLM, no real API calls.

Tests cover:
- TOOL_CALL → DONE flow
- Immediate DONE / FAILED
- Multi-line DONE parsing
- Reasoning model format enforcement
- Level 1 self-repair (timeout, not_found, encoding)
- Max turns, empty responses, execution trace
- Memory injection, callbacks, single-quote JSON parsing
"""

import os
import pytest
from unittest.mock import MagicMock, patch, call

from panda_agent.config import Config, ModelConfig, AgentConfig, MemoryConfig
from panda_agent.llm import LLMResponse
from panda_agent.react import (
    run_react,
    _parse_tool_call,
    _parse_done,
    _parse_failed,
    _classify_error,
    _self_repair,
)
from panda_agent.types import ExecutionTrace


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_config(max_turns: int = 10, memory_enabled: bool = False, auto_write: bool = False) -> Config:
    """Build a test Config with mock model settings — no real API."""
    cfg = Config()
    cfg.model = ModelConfig(
        default="test",
        base_url="http://localhost",
        api_key="test",
        max_tokens=4096,
    )
    cfg.agent = AgentConfig(max_turns=max_turns, max_retries=3)
    cfg.memory = MemoryConfig(enabled=memory_enabled, auto_write=auto_write)
    return cfg


def _make_llm_response(content: str = "", reasoning: str = "", error: str = "") -> LLMResponse:
    """Convenience to build an LLMResponse."""
    return LLMResponse(content=content, reasoning=reasoning, error=error)


class _FakeLLM:
    """Returns pre-sequeued LLMResponse objects, one per call.

    Usage:
        fake = _FakeLLM([
            _make_llm_response(content='TOOL_CALL: {...}'),
            _make_llm_response(content='DONE: done'),
        ])
        with patch('panda_agent.react.call_llm_detailed', side_effect=fake):
            ...
    """

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, messages, config, **kwargs):
        self.calls.append(messages)
        if self.responses:
            return self.responses.pop(0)
        # Default: return empty to end loop gracefully
        return _make_llm_response(content="", reasoning="")


# ---------------------------------------------------------------------------
# 1. test_tool_call_then_done
# ---------------------------------------------------------------------------

class TestToolCallThenDone:
    """TOOL_CALL on turn 1, DONE on turn 2."""

    def test_tool_call_then_done(self, tmp_path):
        responses = [
            _make_llm_response(content='TOOL_CALL:{"name":"list_files","args":{"path":"."}}'),
            _make_llm_response(content="DONE: found files"),
        ]
        fake = _FakeLLM(responses)
        config = _make_config(max_turns=5)

        with patch("panda_agent.react.call_llm_detailed", side_effect=fake):
            result = run_react("list files in current directory", config)

        assert result.success is True
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "list_files"
        assert result.answer == "found files"
        assert result.turns == 2


# ---------------------------------------------------------------------------
# 2. test_done_immediately
# ---------------------------------------------------------------------------

class TestDoneImmediately:
    def test_done_immediately(self):
        responses = [_make_llm_response(content="DONE: hello world")]
        fake = _FakeLLM(responses)
        config = _make_config(max_turns=5)

        with patch("panda_agent.react.call_llm_detailed", side_effect=fake):
            result = run_react("say hello", config)

        assert result.success is True
        assert "hello" in result.answer
        assert result.turns == 1


# ---------------------------------------------------------------------------
# 3. test_done_multiline
# ---------------------------------------------------------------------------

class TestDoneMultiline:
    def test_done_multiline(self):
        text = "DONE: 第一行\n第二行\n第三行"
        responses = [_make_llm_response(content=text)]
        fake = _FakeLLM(responses)
        config = _make_config(max_turns=5)

        with patch("panda_agent.react.call_llm_detailed", side_effect=fake):
            result = run_react("show multiline answer", config)

        assert result.success is True
        assert "第一行" in result.answer
        assert "第二行" in result.answer
        assert "第三行" in result.answer


# ---------------------------------------------------------------------------
# 4. test_failed
# ---------------------------------------------------------------------------

class TestFailed:
    def test_failed(self):
        responses = [_make_llm_response(content="FAILED: cannot find file")]
        fake = _FakeLLM(responses)
        config = _make_config(max_turns=5)

        with patch("panda_agent.react.call_llm_detailed", side_effect=fake):
            result = run_react("read missing file", config)

        assert result.success is False
        assert "cannot find" in result.error


# ---------------------------------------------------------------------------
# 5. test_reasoning_model_no_markers
# ---------------------------------------------------------------------------

class TestReasoningModelNoMarkers:
    """Simulate GLM52RJPT: content empty, reasoning has content but no markers.

    Turn 1: reasoning without markers → format enforcement pushes back.
    Turn 2: DONE in content → success.
    """

    def test_reasoning_model_no_markers(self):
        responses = [
            # Turn 1: reasoning model outputs thinking but no action markers
            _make_llm_response(content="", reasoning="I need to list the files first."),
            # Turn 2: after format enforcement, produces DONE
            _make_llm_response(content="DONE: answer"),
        ]
        fake = _FakeLLM(responses)
        config = _make_config(max_turns=5)

        with patch("panda_agent.react.call_llm_detailed", side_effect=fake):
            result = run_react("list files", config)

        assert result.success is True
        # The first turn should have triggered format enforcement
        # (messages appended pushing back for action format)
        assert len(fake.calls) >= 2


# ---------------------------------------------------------------------------
# 6. test_self_repair_timeout
# ---------------------------------------------------------------------------

class TestSelfRepairTimeout:
    """execute_tool returns timeout error → _self_repair reduces timeout."""

    def test_self_repair_timeout(self):
        responses = [
            _make_llm_response(
                content='TOOL_CALL:{"name":"run_command","args":{"command":"sleep 100","timeout":30}}'
            ),
            _make_llm_response(content="DONE: task done"),
        ]
        fake = _FakeLLM(responses)
        config = _make_config(max_turns=5)

        # Track _self_repair calls
        repair_calls = []
        original_repair = _self_repair

        def tracking_repair(error, tool_name, tool_args, cfg):
            result = original_repair(error, tool_name, tool_args, cfg)
            repair_calls.append((error, tool_name, tool_args, result))
            return result

        # First execute_tool returns timeout, second succeeds
        tool_results = [
            "Error: command timed out after 30s",
            "Command executed successfully",
        ]
        tool_call_count = [0]

        def fake_execute_tool(name, args):
            idx = tool_call_count[0]
            tool_call_count[0] += 1
            return tool_results[idx] if idx < len(tool_results) else "OK"

        with patch("panda_agent.react.call_llm_detailed", side_effect=fake), \
             patch("panda_agent.react.execute_tool", side_effect=fake_execute_tool), \
             patch("panda_agent.react._self_repair", side_effect=tracking_repair):

            result = run_react("run a long command", config)

        # _self_repair was called
        assert len(repair_calls) == 1
        error, tool_name, tool_args, repair_result = repair_calls[0]
        assert "timed out" in error.lower() or "timeout" in error.lower()

        # Repair result: (new_name, new_args, strategy)
        new_name, new_args, strategy = repair_result
        # Timeout should be halved: 30 // 2 = 15
        assert new_args["timeout"] == 15
        assert "timeout" in strategy.lower() or "reduced" in strategy.lower()

        # The repair allowed the tool to succeed on second attempt
        assert result.success is True


# ---------------------------------------------------------------------------
# 7. test_self_repair_not_found
# ---------------------------------------------------------------------------

class TestSelfRepairNotFound:
    """execute_tool returns file not found with ~ path → _self_repair expanduser."""

    def test_self_repair_not_found(self):
        responses = [
            _make_llm_response(
                content='TOOL_CALL:{"name":"read_file","args":{"path":"~/Desktop/test.txt"}}'
            ),
            _make_llm_response(content="DONE: file read"),
        ]
        fake = _FakeLLM(responses)
        config = _make_config(max_turns=5)

        repair_calls = []
        original_repair = _self_repair

        def tracking_repair(error, tool_name, tool_args, cfg):
            result = original_repair(error, tool_name, tool_args, cfg)
            repair_calls.append((error, tool_name, tool_args, result))
            return result

        tool_results = [
            "Error: file not found: ~/Desktop/test.txt",
            "File content here",
        ]
        tool_call_count = [0]

        def fake_execute_tool(name, args):
            idx = tool_call_count[0]
            tool_call_count[0] += 1
            return tool_results[idx] if idx < len(tool_results) else "OK"

        with patch("panda_agent.react.call_llm_detailed", side_effect=fake), \
             patch("panda_agent.react.execute_tool", side_effect=fake_execute_tool), \
             patch("panda_agent.react._self_repair", side_effect=tracking_repair):

            result = run_react("read file from desktop", config)

        assert len(repair_calls) == 1
        error, tool_name, tool_args, repair_result = repair_calls[0]
        assert "not found" in error.lower()

        new_name, new_args, strategy = repair_result
        # Path should be expanded (no ~ anymore)
        assert "~" not in new_args.get("path", "")
        assert "expanded" in strategy.lower() or "expand" in strategy.lower()


# ---------------------------------------------------------------------------
# 8. test_self_repair_encoding
# ---------------------------------------------------------------------------

class TestSelfRepairEncoding:
    """execute_tool returns encoding error → _self_repair adds 'chcp 65001'."""

    def test_self_repair_encoding(self):
        responses = [
            _make_llm_response(
                content='TOOL_CALL:{"name":"run_command","args":{"command":"echo hello"}}'
            ),
            _make_llm_response(content="DONE: command ran"),
        ]
        fake = _FakeLLM(responses)
        config = _make_config(max_turns=5)

        repair_calls = []
        original_repair = _self_repair

        def tracking_repair(error, tool_name, tool_args, cfg):
            result = original_repair(error, tool_name, tool_args, cfg)
            repair_calls.append((error, tool_name, tool_args, result))
            return result

        tool_results = [
            "Error: encoding error",
            "hello",
        ]
        tool_call_count = [0]

        def fake_execute_tool(name, args):
            idx = tool_call_count[0]
            tool_call_count[0] += 1
            return tool_results[idx] if idx < len(tool_results) else "OK"

        with patch("panda_agent.react.call_llm_detailed", side_effect=fake), \
             patch("panda_agent.react.execute_tool", side_effect=fake_execute_tool), \
             patch("panda_agent.react._self_repair", side_effect=tracking_repair):

            result = run_react("run echo command", config)

        assert len(repair_calls) == 1
        error, tool_name, tool_args, repair_result = repair_calls[0]
        assert "encoding" in error.lower()

        new_name, new_args, strategy = repair_result
        assert new_name == "run_command"
        assert "chcp 65001" in new_args.get("command", "")
        assert "chcp" in strategy.lower() or "encoding" in strategy.lower()

        assert result.success is True


# ---------------------------------------------------------------------------
# 9. test_max_turns_exceeded
# ---------------------------------------------------------------------------

class TestMaxTurnsExceeded:
    """LLM returns reasoning without markers every turn → max turns exceeded."""

    def test_max_turns_exceeded(self):
        max_turns = 3
        # Every turn returns reasoning without markers
        responses = [
            _make_llm_response(content="", reasoning="Thinking about the task...")
            for _ in range(max_turns + 5)  # extra to be safe (incl. salvage attempt)
        ]
        fake = _FakeLLM(responses)
        config = _make_config(max_turns=max_turns)

        with patch("panda_agent.react.call_llm_detailed", side_effect=fake):
            result = run_react("zzz unknown task", config)  # no keywords → default turns

        assert result.success is False
        assert "max turns" in result.error.lower()


# ---------------------------------------------------------------------------
# 10. test_empty_response
# ---------------------------------------------------------------------------

class TestEmptyResponse:
    """LLM returns empty content and empty reasoning → should not crash."""

    def test_empty_response(self):
        max_turns = 3
        # All empty responses
        responses = [
            _make_llm_response(content="", reasoning="")
            for _ in range(max_turns + 5)
        ]
        fake = _FakeLLM(responses)
        config = _make_config(max_turns=max_turns)

        with patch("panda_agent.react.call_llm_detailed", side_effect=fake):
            result = run_react("zzz some task", config)  # no keywords

        # Should not crash and should exhaust max_turns
        assert result.success is False
        assert "max turns" in result.error.lower()


# ---------------------------------------------------------------------------
# 11. test_tool_execution_error_recovered
# ---------------------------------------------------------------------------

class TestToolExecutionErrorRecovered:
    """TOOL_CALL fails, self-repair changes strategy, retry succeeds."""

    def test_tool_execution_error_recovered(self):
        responses = [
            _make_llm_response(
                content='TOOL_CALL:{"name":"run_command","args":{"command":"echo test"}}'
            ),
            _make_llm_response(content="DONE: completed after repair"),
        ]
        fake = _FakeLLM(responses)
        config = _make_config(max_turns=5)

        tool_results = [
            "Error: encoding error",
            "test output",
        ]
        tool_call_count = [0]

        def fake_execute_tool(name, args):
            idx = tool_call_count[0]
            tool_call_count[0] += 1
            return tool_results[idx] if idx < len(tool_results) else "OK"

        with patch("panda_agent.react.call_llm_detailed", side_effect=fake), \
             patch("panda_agent.react.execute_tool", side_effect=fake_execute_tool):

            result = run_react("run echo test", config)

        assert result.success is True
        assert result.trace is not None
        assert len(result.trace.self_repairs) > 0


# ---------------------------------------------------------------------------
# 12. test_execution_trace_populated
# ---------------------------------------------------------------------------

class TestExecutionTracePopulated:
    """Normal execution: trace.turns non-empty, total_turns > 0, final_success matches."""

    def test_execution_trace_populated(self):
        responses = [
            _make_llm_response(content='TOOL_CALL:{"name":"list_files","args":{"path":"."}}'),
            _make_llm_response(content="DONE: listed files"),
        ]
        fake = _FakeLLM(responses)
        config = _make_config(max_turns=5)

        with patch("panda_agent.react.call_llm_detailed", side_effect=fake):
            result = run_react("list files", config)

        assert result.trace is not None
        assert len(result.trace.turns) > 0
        assert result.trace.total_turns > 0
        assert result.trace.final_success == result.success


# ---------------------------------------------------------------------------
# 13. test_memory_injection
# ---------------------------------------------------------------------------

class TestMemoryInjection:
    """Mock memory.retrieve_context returns context → system prompt includes it."""

    def test_memory_injection(self):
        responses = [_make_llm_response(content="DONE: done with memory")]
        fake = _FakeLLM(responses)
        config = _make_config(max_turns=5, memory_enabled=True)

        mock_memory = MagicMock()
        mock_memory.retrieve_context.return_value = "## Past Experience\n- [0.9] Use ls -la"

        with patch("panda_agent.react.call_llm_detailed", side_effect=fake):
            result = run_react("list files", config, memory=mock_memory)

        assert result.success is True
        # Verify retrieve_context was called
        mock_memory.retrieve_context.assert_called_once()
        # Verify the system prompt (first message) contains the memory context
        first_call_messages = fake.calls[0]
        system_msg = first_call_messages[0]["content"]
        assert "Past Experience" in system_msg
        assert "ls -la" in system_msg


# ---------------------------------------------------------------------------
# 14. test_callbacks_fired
# ---------------------------------------------------------------------------

class TestCallbacksFired:
    """on_event and on_reasoning callbacks are called during execution."""

    def test_callbacks_fired(self):
        responses = [
            _make_llm_response(
                content="DONE: task done",
                reasoning="I should just say done.",
            ),
        ]
        fake = _FakeLLM(responses)
        config = _make_config(max_turns=5)

        events = []
        reasoning_calls = []

        def on_event(event_type, message):
            events.append((event_type, message))

        def on_reasoning(turn_label, reasoning_text):
            reasoning_calls.append((turn_label, reasoning_text))

        with patch("panda_agent.react.call_llm_detailed", side_effect=fake):
            result = run_react(
                "do a task",
                config,
                on_event=on_event,
                on_reasoning=on_reasoning,
            )

        assert result.success is True
        # on_event should have been called at least once
        assert len(events) > 0
        # on_reasoning should have been called since we provided reasoning
        assert len(reasoning_calls) > 0
        # Verify reasoning callback got the reasoning content
        assert reasoning_calls[0][1] == "I should just say done."


# ---------------------------------------------------------------------------
# 15. test_parse_tool_call_json_with_single_quotes
# ---------------------------------------------------------------------------

class TestParseToolCallSingleQuotes:
    """Single-quote JSON should also be parsed successfully."""

    def test_parse_tool_call_json_with_single_quotes(self):
        text = "TOOL_CALL: {'name': 'list_files', 'args': {'path': '.'}}"
        result = _parse_tool_call(text)
        assert result is not None
        assert result["name"] == "list_files"
        assert result["args"]["path"] == "."

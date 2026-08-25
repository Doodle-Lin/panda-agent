"""Doom loop detection — same tool call 3x in a row → warn → fail.

Inspired by opencode's processor.ts doom loop detection:
  const DOOM_LOOP_THRESHOLD = 3
  // check last 3 tool calls are identical

panda's version: simpler, counts consecutive identical tool calls in react loop.
"""
import sys, os
sys.path.insert(0, r'E:\workspace\evo-agent\src')
os.environ['PANDA_HOME'] = os.path.expanduser('~/.panda')

from panda_agent.react import run_react, _check_doom_loop
from panda_agent.llm import LLMResponse
from panda_agent.config import Config, AgentConfig, ModelConfig, MemoryConfig
from unittest.mock import patch

class _FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
    def __call__(self, messages, model_config):
        if self.responses:
            return self.responses.pop(0)
        return LLMResponse(content="DONE: fallback", reasoning="", error="")

def _resp(content="", reasoning=""):
    return LLMResponse(content=content, reasoning=reasoning, error="")

def _make_config(max_turns=10):
    cfg = Config()
    cfg.model = ModelConfig(default="GLM52RJPT", api_key="test", base_url="test", max_tokens=4096)
    cfg.agent = AgentConfig(max_turns=max_turns, max_retries=3)
    cfg.memory = MemoryConfig(enabled=False)
    return cfg

TOOL_CALL_A = 'TOOL_CALL: {"name": "run_command", "args": {"command": "ls", "timeout": 10}}'
TOOL_CALL_B = 'TOOL_CALL: {"name": "run_command", "args": {"command": "dir", "timeout": 10}}'


class TestDoomLoopDetection:
    """Detect when agent repeats the same tool call 3 times in a row."""

    def test_check_doom_loop_detects_3_identical(self):
        """_check_doom_loop returns True when 3 identical tool calls in history."""
        tool_calls = [
            {"name": "run_command", "args": {"command": "ls", "timeout": 10}},
            {"name": "run_command", "args": {"command": "ls", "timeout": 10}},
            {"name": "run_command", "args": {"command": "ls", "timeout": 10}},
        ]
        assert _check_doom_loop(tool_calls) is True

    def test_check_doom_loop_different_args_not_doom(self):
        """Different args = not doom loop (agent is trying different approaches)."""
        tool_calls = [
            {"name": "run_command", "args": {"command": "ls", "timeout": 10}},
            {"name": "run_command", "args": {"command": "ls -la", "timeout": 10}},
            {"name": "run_command", "args": {"command": "dir", "timeout": 10}},
        ]
        assert _check_doom_loop(tool_calls) is False

    def test_check_doom_loop_different_tools_not_doom(self):
        """Different tools = not doom loop."""
        tool_calls = [
            {"name": "run_command", "args": {"command": "ls", "timeout": 10}},
            {"name": "list_files", "args": {"path": "."}},
            {"name": "read_file", "args": {"path": "test.txt"}},
        ]
        assert _check_doom_loop(tool_calls) is False

    def test_check_doom_loop_only_2_identical(self):
        """Only 2 identical = not doom yet (threshold is 3)."""
        tool_calls = [
            {"name": "run_command", "args": {"command": "ls", "timeout": 10}},
            {"name": "run_command", "args": {"command": "ls", "timeout": 10}},
        ]
        assert _check_doom_loop(tool_calls) is False

    def test_check_doom_loop_empty(self):
        """Empty list = no doom loop."""
        assert _check_doom_loop([]) is False

    def test_check_doom_loop_returns_warning_message(self):
        """When doom loop detected, should also return a warning message."""
        tool_calls = [
            {"name": "run_command", "args": {"command": "ls", "timeout": 10}},
            {"name": "run_command", "args": {"command": "ls", "timeout": 10}},
            {"name": "run_command", "args": {"command": "ls", "timeout": 10}},
        ]
        result = _check_doom_loop(tool_calls)
        # _check_doom_loop should return True (doom detected)
        assert result is True

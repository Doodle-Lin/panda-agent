"""Test: memory auto-write strategy — only write valuable experiences.

Current behavior: writes every task completion → noise.
New behavior: only write when:
1. Self-repair succeeded (tool failed then recovered)
2. Multi-step task (3+ tool calls)
3. Error recovery (doom loop avoided)
4. NOT for simple 1-step tasks (just reading a file, listing dirs)
5. NOT for chat/greeting (no tool calls)
"""
import sys, os, tempfile
sys.path.insert(0, r'E:\workspace\evo-agent\src')
os.environ['PANDA_HOME'] = os.path.expanduser('~/.panda')

from unittest.mock import patch, MagicMock
from panda_agent.react import run_react
from panda_agent.llm import LLMResponse
from panda_agent.config import Config, ModelConfig, AgentConfig, MemoryConfig, EvolutionConfig, DisplayConfig
from panda_agent.memory import MemoryClient


def make_config():
    return Config(
        model=ModelConfig(default="GLM52RJPT", api_key="k", base_url="u", max_tokens=8192),
        agent=AgentConfig(max_turns=5),
        memory=MemoryConfig(enabled=True, auto_write=True),
        evolution=EvolutionConfig(),
        display=DisplayConfig(),
    )


class TestMemoryAutoWriteStrategy:
    """Memory should only auto-write valuable experiences, not every task."""

    def test_no_write_for_simple_task(self):
        """Simple 1-step task (just DONE, no tool calls) should NOT write memory."""
        config = make_config()
        mock_mem = MagicMock(spec=MemoryClient)
        mock_mem.is_available.return_value = True
        mock_mem.retrieve_context.return_value = ""

        done_resp = LLMResponse(content="DONE: Hello!", reasoning="", tool_calls=[])
        with patch("panda_agent.react.call_llm_detailed", return_value=done_resp):
            result = run_react("hello", config, memory=mock_mem)

        # Should NOT have written to memory
        mock_mem.write.assert_not_called()

    def test_write_for_multi_step_task(self):
        """Multi-step task (3+ tool calls) SHOULD write memory."""
        config = make_config()
        mock_mem = MagicMock(spec=MemoryClient)
        mock_mem.is_available.return_value = True
        mock_mem.retrieve_context.return_value = ""

        # Simulate 3 tool calls then DONE
        resps = [
            LLMResponse(content="", reasoning="", tool_calls=[{"id": "c1", "name": "list_files", "args": {"path": "."}}]),
            LLMResponse(content="", reasoning="", tool_calls=[{"id": "c2", "name": "read_file", "args": {"path": "test.txt"}}]),
            LLMResponse(content="", reasoning="", tool_calls=[{"id": "c3", "name": "write_file", "args": {"path": "out.txt", "content": "done"}}]),
            LLMResponse(content="DONE: completed", reasoning="", tool_calls=[]),
        ]
        with patch("panda_agent.react.call_llm_detailed", side_effect=resps):
            with patch("panda_agent.react.execute_tool", side_effect=["file1\nfile2", "content", "Wrote 7 chars"]):
                result = run_react("build something", config, memory=mock_mem)

        # SHOULD have written to memory
        mock_mem.write.assert_called_once()

    def test_write_for_self_repair(self):
        """Task where self-repair kicked in SHOULD write memory."""
        config = make_config()
        mock_mem = MagicMock(spec=MemoryClient)
        mock_mem.is_available.return_value = True
        mock_mem.retrieve_context.return_value = ""

        # First call: tool returns error, self-repair kicks in
        # Second call: DONE
        resps = [
            LLMResponse(content="", reasoning="", tool_calls=[{"id": "c1", "name": "read_file", "args": {"path": "~/nonexistent.txt"}}]),
            LLMResponse(content="DONE: found it", reasoning="", tool_calls=[]),
        ]
        with patch("panda_agent.react.call_llm_detailed", side_effect=resps):
            with patch("panda_agent.react.execute_tool", side_effect=["Error: file not found: ~/nonexistent.txt", "file content"]):
                result = run_react("read ~/nonexistent.txt", config, memory=mock_mem)

        # Self-repair means valuable experience -> write
        mock_mem.write.assert_called_once()

    def test_no_write_for_single_tool_call(self):
        """Single tool call (e.g., just listing files) should NOT write memory."""
        config = make_config()
        mock_mem = MagicMock(spec=MemoryClient)
        mock_mem.is_available.return_value = True
        mock_mem.retrieve_context.return_value = ""

        resps = [
            LLMResponse(content="", reasoning="", tool_calls=[{"id": "c1", "name": "list_files", "args": {"path": "."}}]),
            LLMResponse(content="DONE: here are the files", reasoning="", tool_calls=[]),
        ]
        with patch("panda_agent.react.call_llm_detailed", side_effect=resps):
            with patch("panda_agent.react.execute_tool", return_value="file1\nfile2"):
                result = run_react("list files", config, memory=mock_mem)

        # Single tool call, no errors -> NOT valuable enough
        mock_mem.write.assert_not_called()

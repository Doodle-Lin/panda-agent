"""Test: agent must use write_file tool to create files, not just DONE.

This was the root cause of 'write novel to desktop' failing —
LLM said DONE without actually calling write_file.
"""
import sys, os
sys.path.insert(0, r'E:\workspace\evo-agent\src')
os.environ['PANDA_HOME'] = os.path.expanduser('~/.panda')

from panda_agent.react import run_react
from panda_agent.llm import LLMResponse
from panda_agent.config import Config, AgentConfig, ModelConfig, MemoryConfig
from unittest.mock import patch, MagicMock

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


class TestWriteFileMustBeCalled:
    """Agent must call write_file tool when user asks to write a file.

    The root cause of 'write novel to desktop' failing was:
    - LLM said DONE without actually calling write_file
    - System prompt didn't explicitly require using write_file to create files

    We test the system prompt contains the required instruction,
    since mock LLM can't test real model behavior.
    """

    def test_system_prompt_requires_write_file_for_file_creation(self):
        """System prompt must instruct: to create files, MUST use write_file tool."""
        from panda_agent.brain import build_system_prompt
        from panda_agent.tools import get_tool_descriptions

        prompt = build_system_prompt(get_tool_descriptions())

        # Must have a rule that explicitly says: use write_file to create files
        # Check for a sentence that connects "write_file" with "create"/"write" and "MUST"/"never DONE"
        lines = prompt.split('\n')
        has_explicit_rule = False
        for line in lines:
            line_lower = line.lower()
            if "write_file" in line_lower and (
                "must" in line_lower or "never" in line_lower or "always" in line_lower
            ):
                has_explicit_rule = True
                break

        assert has_explicit_rule, (
            "System prompt must have an explicit rule like: "
            "'7. To create or write files, you MUST use the write_file tool. "
            "Never output DONE: for a file-writing task without calling write_file first.'\n"
            f"Current prompt:\n{prompt}"
        )

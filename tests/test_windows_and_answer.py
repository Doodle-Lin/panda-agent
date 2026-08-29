"""Tests for Windows path detection and TOOL_CALL stripping in answer."""
from panda_agent.brain import build_system_prompt
from panda_agent.tools import get_tool_descriptions


class TestWindowsPathDetection:
    """System prompt must instruct agent to use Windows paths on Windows."""

    def test_prompt_mentions_windows_paths(self):
        prompt = build_system_prompt(get_tool_descriptions())
        assert "Windows" in prompt, "Prompt must mention Windows"
        assert "Desktop" in prompt or "desktop" in prompt.lower(), \
            "Prompt must mention Desktop path"
        # Must explicitly say NOT to use ~/Desktop
        assert "~" in prompt, "Prompt must warn against ~ path"
        assert "C:\\" in prompt or "C:\\\\" in prompt, "Prompt must show Windows path example"

    def test_prompt_mentions_dir_not_ls(self):
        """On Windows, should use 'dir' not 'ls'."""
        prompt = build_system_prompt(get_tool_descriptions())
        assert "dir" in prompt.lower(), "Prompt must mention 'dir' for Windows"


class TestToolCallStrippedFromAnswer:
    """Answer should not contain raw TOOL_CALL text."""

    def test_tool_call_stripped_from_fallback_answer(self):
        """When fallback answer contains TOOL_CALL, it should be stripped."""
        from panda_agent.react import run_react
        from panda_agent.llm import LLMResponse
        from panda_agent.config import Config, AgentConfig, ModelConfig, MemoryConfig
        from unittest.mock import patch

        # LLM outputs TOOL_CALL text mixed with answer
        responses = [
            LLMResponse(
                content='TOOL_CALL: {"name": "write_file", "args": {"path": "test.txt", "content": "hello"}}\nLet me verify the file was created.',
                reasoning="",
                error="",
            ),
            LLMResponse(
                content='DONE: The file test.txt has been created successfully.',
                reasoning="",
                error="",
            ),
        ]

        class _FakeLLM:
            def __init__(self, responses):
                self.responses = list(responses)
            def __call__(self, *args, **kwargs):
                if self.responses:
                    return self.responses.pop(0)
                return LLMResponse(content="DONE: fallback", reasoning="", error="")

        config = Config()
        config.model = ModelConfig(default="GLM52RJPT", api_key="test", base_url="test", max_tokens=4096)
        config.agent = AgentConfig(max_turns=10, max_retries=3)
        config.memory = MemoryConfig(enabled=False)

        fake = _FakeLLM(responses)
        with patch("panda_agent.react.call_llm_detailed", side_effect=fake), \
             patch("panda_agent.react.max_turns_for_task", return_value=10):
            result = run_react("write a file", config)

        # Answer should not contain TOOL_CALL text
        assert "TOOL_CALL" not in result.answer, \
            f"Answer should not contain TOOL_CALL text, got: {result.answer[:200]}"

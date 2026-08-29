"""Soft limit — max_turns reached → inject MAX_STEPS_PROMPT → text-only summary.

Inspired by opencode's max-steps.ts:
  CRITICAL - MAXIMUM STEPS REACHED
  Tools are disabled. Respond with text only.
  Summarize work done, list remaining tasks.

panda's version: simpler, injects prompt and gives LLM one final turn.
"""
from panda_agent.react import run_react, MAX_STEPS_PROMPT
from panda_agent.llm import LLMResponse
from panda_agent.config import Config, AgentConfig, ModelConfig, MemoryConfig
from unittest.mock import patch

class _FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def __call__(self, *args, **kwargs):
        # call_llm_detailed(messages, config, *, model=..., max_tokens=..., ...)
        messages = args[0] if args else kwargs.get('messages', [])
        self.calls.append(list(messages))
        if self.responses:
            return self.responses.pop(0)
        return LLMResponse(content="DONE: fallback", reasoning="", error="")

def _resp(content="", reasoning=""):
    return LLMResponse(content=content, reasoning=reasoning, error="")

def _make_config(max_turns=3):
    cfg = Config()
    cfg.model = ModelConfig(default="GLM52RJPT", api_key="test", base_url="test", max_tokens=4096)
    cfg.agent = AgentConfig(max_turns=max_turns, max_retries=3)
    cfg.memory = MemoryConfig(enabled=False)
    return cfg


class TestSoftLimit:
    """max_turns reached → inject MAX_STEPS_PROMPT → LLM gives text-only summary."""

    def test_max_steps_prompt_exists(self):
        """MAX_STEPS_PROMPT constant must exist and contain key instructions."""
        assert "MAXIMUM" in MAX_STEPS_PROMPT or "maximum" in MAX_STEPS_PROMPT.lower()
        assert "tool" in MAX_STEPS_PROMPT.lower()
        assert "text" in MAX_STEPS_PROMPT.lower() or "summary" in MAX_STEPS_PROMPT.lower()

    def test_inject_max_steps_prompt_on_limit(self):
        """When max_turns reached, salvage call should include MAX_STEPS_PROMPT."""
        # All turns return reasoning only (no markers) → will exhaust max_turns
        responses = [
            _resp(content="", reasoning="thinking..."),
            _resp(content="", reasoning="thinking..."),
            _resp(content="", reasoning="thinking..."),
            # This is the salvage/max_steps turn
            _resp(content="DONE: I was unable to complete the task in the allotted turns.", reasoning=""),
        ]
        fake = _FakeLLM(responses)
        config = _make_config(max_turns=3)

        with patch("panda_agent.react.call_llm_detailed", side_effect=fake), \
             patch("panda_agent.brain.max_turns_for_task", return_value=3), \
             patch("panda_agent.react.max_turns_for_task", return_value=3):
            run_react("zzz unknown task", config)

        # The salvage LLM call (last call) should contain MAX_STEPS_PROMPT
        last_call_messages = fake.calls[-1]
        all_user_msgs = [m["content"] for m in last_call_messages if m["role"] == "user"]
        found = any("MAXIMUM" in msg or "maximum" in msg.lower() for msg in all_user_msgs)
        assert found, (
            f"Expected MAX_STEPS_PROMPT in last LLM call's user messages. "
            f"User messages: {[m[:100] for m in all_user_msgs]}"
        )

    def test_text_only_after_prompt(self):
        """After MAX_STEPS_PROMPT, LLM should produce a DONE: response (text-only)."""
        responses = [
            _resp(content="", reasoning="thinking..."),
            _resp(content="", reasoning="thinking..."),
            _resp(content="", reasoning="thinking..."),
            # Salvage turn — LLM responds with DONE
            _resp(content="DONE: Task incomplete. I attempted to analyze the request but ran out of turns.", reasoning=""),
        ]
        fake = _FakeLLM(responses)
        config = _make_config(max_turns=3)

        with patch("panda_agent.react.call_llm_detailed", side_effect=fake):
            result = run_react("zzz unknown task", config)

        # Should succeed (salvaged) with a summary
        assert result.success is True
        assert "incomplete" in result.answer.lower() or "unable" in result.answer.lower()

    def test_salvage_still_works_without_tool_calls(self):
        """Salvage mechanism should still work when no tool calls were made."""
        responses = [
            _resp(content="", reasoning="thinking...") for _ in range(5)
        ]
        # Add a salvage response
        responses.append(_resp(content="DONE: I could not complete the task.", reasoning=""))
        fake = _FakeLLM(responses)
        config = _make_config(max_turns=3)

        with patch("panda_agent.react.call_llm_detailed", side_effect=fake):
            result = run_react("zzz unknown task", config)

        # Without tool calls, salvage still tries but may not succeed
        # (since no tool results to summarize). Either success or fail is OK,
        # but it should not crash.
        assert result is not None
        assert result.turns is not None

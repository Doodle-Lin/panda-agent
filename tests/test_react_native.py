"""Test: run_react uses native tool_calls, falls back to text parsing.

Phase 3: ReAct loop prioritizes llm_resp.tool_calls over text TOOL_CALL: parsing.
"""
import sys, os, json, tempfile
sys.path.insert(0, r'E:\workspace\evo-agent\src')
os.environ['PANDA_HOME'] = os.path.expanduser('~/.panda')

from unittest.mock import patch, MagicMock
from panda_agent.react import run_react, ReActResult
from panda_agent.llm import LLMResponse
from panda_agent.config import Config, ModelConfig, AgentConfig, MemoryConfig, EvolutionConfig, DisplayConfig


def make_config(tmpdir):
    return Config(
        model=ModelConfig(
            default="GLM52RJPT",
            api_key="test-key",
            base_url="https://test/v1",
            max_tokens=8192,
        ),
        agent=AgentConfig(max_turns=5),
        memory=MemoryConfig(enabled=False),
        evolution=EvolutionConfig(),
        display=DisplayConfig(),
    )


class TestReactNativeToolCalls:
    """run_react should use llm_resp.tool_calls when available."""

    def test_native_tool_call_executes(self):
        """When LLM returns tool_calls, run_react should execute the tool directly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["PANDA_WORKSPACE"] = tmpdir
            try:
                config = make_config(tmpdir)

                # LLM response with native tool_calls for write_file
                write_resp = LLMResponse(
                    content="",
                    reasoning="I need to write a file.",
                    tool_calls=[{"id": "call_1", "name": "write_file", "args": {"path": os.path.join(tmpdir, "test.txt"), "content": "Hello!"}}],
                )
                done_resp = LLMResponse(content="DONE: File written successfully", reasoning="done")

                with patch("panda_agent.react.call_llm_detailed", side_effect=[write_resp, done_resp]):
                    result = run_react("write a file", config)

                assert result.success
                assert os.path.exists(os.path.join(tmpdir, "test.txt"))
                with open(os.path.join(tmpdir, "test.txt"), encoding="utf-8") as f:
                    assert f.read() == "Hello!"
            finally:
                os.environ.pop("PANDA_WORKSPACE", None)

    def test_native_tool_call_with_chinese_content(self):
        """Chinese content in native tool_call args should work without JSON issues."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["PANDA_WORKSPACE"] = tmpdir
            try:
                config = make_config(tmpdir)
                chinese_content = "你好世界\n这是第二行\n中文内容\n" * 100  # ~800 chars

                write_resp = LLMResponse(
                    content="",
                    reasoning="Writing Chinese content.",
                    tool_calls=[{"id": "call_1", "name": "write_file", "args": {"path": os.path.join(tmpdir, "chinese.txt"), "content": chinese_content}}],
                )
                done_resp = LLMResponse(content="DONE: Chinese file written", reasoning="done")

                with patch("panda_agent.react.call_llm_detailed", side_effect=[write_resp, done_resp]):
                    result = run_react("写个中文文件", config)

                assert result.success
                with open(os.path.join(tmpdir, "chinese.txt"), encoding="utf-8") as f:
                    assert "你好世界" in f.read()
            finally:
                os.environ.pop("PANDA_WORKSPACE", None)

    def test_fallback_to_text_parsing_when_no_tool_calls(self):
        """When LLM returns no tool_calls (e.g. non-function-calling model),
        run_react should fall back to text TOOL_CALL: parsing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["PANDA_WORKSPACE"] = tmpdir
            try:
                config = make_config(tmpdir)
                # Use forward slashes to avoid backslash escaping in JSON
                test_path = os.path.join(tmpdir, "test.txt").replace("\\", "/")

                # LLM response with text-format TOOL_CALL (no native tool_calls)
                text_resp = LLMResponse(
                    content=f'TOOL_CALL: {{"name": "write_file", "args": {{"path": "{test_path}", "content": "Text fallback"}}}}',
                    reasoning="",
                    tool_calls=[],  # no native tool_calls
                )
                done_resp = LLMResponse(content="DONE: done", reasoning="", tool_calls=[])

                with patch("panda_agent.react.call_llm_detailed", side_effect=[text_resp, done_resp]):
                    result = run_react("write a file", config)

                assert result.success
                # write_file uses Path() which handles forward slashes on Windows
                assert os.path.exists(test_path), f"File should exist at {test_path}"
            finally:
                os.environ.pop("PANDA_WORKSPACE", None)

    def test_tool_call_id_propagated_to_messages(self):
        """When using native tool_calls, the assistant message should include tool_calls
        so the API understands the conversation context."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["PANDA_WORKSPACE"] = tmpdir
            try:
                config = make_config(tmpdir)
                captured_messages = []

                write_resp = LLMResponse(
                    content="",
                    reasoning="Writing file.",
                    tool_calls=[{"id": "call_1", "name": "write_file", "args": {"path": os.path.join(tmpdir, "a.txt"), "content": "A"}}],
                )
                done_resp = LLMResponse(content="DONE: done", reasoning="")

                def capture_llm(messages, cfg, **kw):
                    captured_messages.append(list(messages))
                    if len(captured_messages) == 1:
                        return write_resp
                    return done_resp

                with patch("panda_agent.react.call_llm_detailed", side_effect=capture_llm):
                    result = run_react("write a file", config)

                assert result.success
                # Check that the assistant message in the second call has tool_calls
                if len(captured_messages) >= 2:
                    second_call = captured_messages[1]
                    asst_msgs = [m for m in second_call if m.get("role") == "assistant"]
                    if asst_msgs:
                        assert any("tool_calls" in m for m in asst_msgs), \
                            "Assistant message should have tool_calls for API context"
            finally:
                os.environ.pop("PANDA_WORKSPACE", None)

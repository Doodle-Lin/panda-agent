"""Test: call_llm_detailed supports native function calling.

Phase 2: LLMResponse has tool_calls field, call_llm_detailed sends tools param.
"""
import sys, os, json
sys.path.insert(0, r'E:\workspace\evo-agent\src')
os.environ['PANDA_HOME'] = os.path.expanduser('~/.panda')

from panda_agent.llm import LLMResponse, call_llm_detailed
from panda_agent.config import ModelConfig


class TestLLMResponseToolCalls:
    """LLMResponse must have tool_calls field."""

    def test_tool_calls_field_exists(self):
        resp = LLMResponse()
        assert hasattr(resp, "tool_calls")
        assert resp.tool_calls == []  # default empty list

    def test_tool_calls_populated(self):
        resp = LLMResponse(tool_calls=[{"id": "call_1", "name": "write_file", "args": {}}])
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0]["name"] == "write_file"


class TestCallLLMWithTools:
    """call_llm_detailed must accept tools parameter and parse tool_calls from response."""

    def test_call_with_tools_param(self):
        """call_llm_detailed should accept a tools parameter without error."""
        # We can't call real API in unit test, but we can verify
        # the function signature accepts tools
        import inspect
        sig = inspect.signature(call_llm_detailed)
        params = sig.parameters
        assert "tools" in params, "call_llm_detailed must accept tools parameter"

    def test_parse_tool_calls_from_stream(self):
        """When streaming response contains tool_calls in delta, LLMResponse.tool_calls should be populated."""
        # Simulate a streaming response with tool_calls
        # We mock the requests.post to return chunks with tool_calls
        from unittest.mock import patch, MagicMock

        # Build mock streaming response
        chunks = [
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_abc", "function": {"name": "write_file", "arguments": ""}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\"path\":"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": " \"test.txt\", \"content\": \"hello\"}"}}]}}]},
            {"choices": [{"delta": {}}]},
        ]
        lines = [f"data: {json.dumps(c)}" for c in chunks] + ["data: [DONE]"]

        mock_resp = MagicMock()
        mock_resp.iter_lines.return_value = lines
        mock_resp.raise_for_status = MagicMock()

        config = ModelConfig(
            default="GLM52RJPT",
            api_key="test-key",
            base_url="https://test/v1",
            max_tokens=8192,
        )

        with patch("requests.post", return_value=mock_resp):
            tools = [{"type": "function", "function": {"name": "write_file", "description": "test", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}}]
            resp = call_llm_detailed([{"role": "user", "content": "write a file"}], config, tools=tools)

        assert resp.tool_calls is not None
        assert len(resp.tool_calls) >= 1
        tc = resp.tool_calls[0]
        assert tc["name"] == "write_file"
        args = tc["args"]
        assert args["path"] == "test.txt"
        assert args["content"] == "hello"

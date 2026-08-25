"""Test: _parse_tool_call handles unescaped newlines in JSON content.

Root cause: LLM outputs TOOL_CALL JSON with raw newlines (0x0A) inside
string values. JSON spec requires \n escape. json.loads fails.

Fix: After json.loads fails, try escaping raw control chars then re-parse.
"""
import sys, os
sys.path.insert(0, r'E:\workspace\evo-agent\src')
os.environ['PANDA_HOME'] = os.path.expanduser('~/.panda')

from panda_agent.react import _parse_tool_call


class TestParseToolCallUnescapedNewlines:
    """_parse_tool_call must handle LLM output with raw newlines in JSON."""

    def test_parse_with_escaped_newlines(self):
        """Normal case: \\n escape in JSON string — should parse fine."""
        text = 'TOOL_CALL: {"name": "write_file", "args": {"path": "test.txt", "content": "Line1\\nLine2\\nLine3"}}'
        result = _parse_tool_call(text)
        assert result is not None
        assert result["name"] == "write_file"
        assert result["args"]["content"] == "Line1\nLine2\nLine3"

    def test_parse_with_raw_newlines(self):
        """LLM outputs actual newline chars (0x0A) inside JSON string value.

        This is what GLM52RJPT actually does — it writes real newlines
        in the content parameter instead of \\n escapes.
        """
        # Build text with actual newlines (not escaped)
        text = 'TOOL_CALL: {"name": "write_file", "args": {"path": "test.txt", "content": "Line1\nLine2\nLine3"}}'
        result = _parse_tool_call(text)
        assert result is not None, "Should parse even with raw newlines"
        assert result["name"] == "write_file"
        assert "Line1" in result["args"]["content"]
        assert "Line2" in result["args"]["content"]

    def test_parse_with_long_chinese_content_raw_newlines(self):
        """5000-char Chinese story with raw newlines — the real bug scenario."""
        story = "最后一班列车\n\n第一段\n\n第二段\n\n第三段\n\n（全文完）"
        text = f'TOOL_CALL: {{"name": "write_file", "args": {{"path": "C:\\\\Users\\\\test.txt", "content": "{story}"}}}}'
        result = _parse_tool_call(text)
        assert result is not None, "Should parse Chinese content with raw newlines"
        assert result["name"] == "write_file"
        content = result["args"]["content"]
        assert "最后一班列车" in content
        assert "第一段" in content
        assert "（全文完）" in content

    def test_parse_with_tabs_and_raw_newlines(self):
        """Tabs and newlines mixed in content."""
        text = 'TOOL_CALL: {"name": "write_file", "args": {"path": "test.txt", "content": "Col1\tCol2\nRow2"}}'
        result = _parse_tool_call(text)
        assert result is not None, "Should parse with tabs and newlines"
        assert "Col1" in result["args"]["content"]

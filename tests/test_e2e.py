"""End-to-end tests with real LLM (GLM52RJPT).

These tests call the real API and verify agent behavior end-to-end.
Marked as slow — run with: python -m pytest tests/test_e2e.py -v -m slow

Prerequisites:
- Valid API key in ~/.panda/config.yaml
- Network access to an OpenAI-compatible API endpoint
- Temp directory for file creation tests
"""
import sys, os, tempfile, shutil, time
sys.path.insert(0, r'E:\workspace\evo-agent\src')
os.environ['PANDA_HOME'] = os.path.expanduser('~/.panda')

import pytest

pytestmark = pytest.mark.slow

from panda_agent.config import load_config
from panda_agent.react import run_react, _compress_messages, _estimate_tokens

# Skip all if no API key
try:
    _cfg = load_config()
    has_api = bool(_cfg.model.api_key) and "test" not in _cfg.model.api_key
except Exception:
    has_api = False

skip_no_api = pytest.mark.skipif(not has_api, reason="No valid API key")


@skip_no_api
class TestE2EWriteFile:
    """E2E: agent writes a file to disk via write_file tool."""

    def test_write_short_file_to_disk(self, tmp_path):
        """Agent should write a short file and the file must exist on disk."""
        target = tmp_path / "hello.txt"
        task = f"Write a file at {target} containing the text 'Hello World'. Use the write_file tool."
        config = load_config()

        result = run_react(task, config)

        assert result.success, f"Agent failed: {result.error}"
        assert target.exists(), f"File not created at {target}"
        content = target.read_text(encoding="utf-8")
        assert "Hello" in content, f"File content unexpected: {content[:200]}"

    def test_write_long_file_to_disk(self, tmp_path):
        """Agent should write a ~500 word story to disk in one shot."""
        target = tmp_path / "story.txt"
        task = (
            f"Write a short science fiction story (about 500 words) and save it to {target}. "
            f"Generate the full story content first, then use write_file tool to save it. "
            f"Do NOT write in pieces — generate all content then save once."
        )
        config = load_config()

        result = run_react(task, config)

        assert result.success, f"Agent failed: {result.error}"
        assert target.exists(), f"File not created at {target}"
        content = target.read_text(encoding="utf-8")
        # Should be at least 200 chars (500 words ≈ 2500 chars, but allow flexibility)
        assert len(content) > 200, f"Story too short ({len(content)} chars): {content[:200]}"
        # Should have used write_file tool
        write_calls = [tc for tc in result.tool_calls if tc.get("name") == "write_file"]
        assert len(write_calls) >= 1, f"write_file not called. Tools used: {[tc['name'] for tc in result.tool_calls]}"


@skip_no_api
class TestE2EContextCompression:
    """E2E: context compression during a multi-turn task."""

    def test_multi_turn_does_not_lose_context(self, tmp_path):
        """After several tool calls, agent should still remember the original task."""
        target = tmp_path / "result.txt"
        task = (
            f"1. List files in current directory using run_command with 'dir'\n"
            f"2. Write the result to {target} using write_file\n"
            f"3. Verify the file exists by reading it with read_file\n"
            f"Complete all 3 steps."
        )
        config = load_config()

        result = run_react(task, config)

        assert result.success, f"Agent failed: {result.error}"
        assert target.exists(), f"File not created"
        # Agent should have used at least 2 tools
        assert len(result.tool_calls) >= 2, f"Expected ≥2 tool calls, got {len(result.tool_calls)}"


@skip_no_api
class TestE2ESoftLimit:
    """E2E: soft limit triggers gracefully when task is too complex."""

    def test_complex_task_salvages_partial_results(self, tmp_path):
        """A deliberately complex task should trigger salvage, not crash."""
        # This task is complex enough to use many turns
        task = (
            f"Search the web for information about quantum computing, "
            f"summarize the key concepts, write a 2000 word essay about it, "
            f"and save it to {tmp_path / 'essay.txt'}. "
            f"Also create a separate summary file at {tmp_path / 'summary.txt'}."
        )
        config = load_config()
        # Lower max_turns to trigger soft limit faster
        config.agent.max_turns = 5

        result = run_react(task, config)

        # Should not crash — either success (salvaged) or fail with meaningful error
        assert result is not None
        assert result.turns is not None
        # Even if failed, should have some tool calls or a salvage attempt
        # The key assertion: no crash, graceful handling
        if not result.success:
            assert result.error, "Failed but no error message"
            assert "Max turns" in result.error or "doom" in result.error.lower() or result.answer, \
                f"Unexpected failure: {result.error}"


@skip_no_api
class TestE2EChineseInput:
    """E2E: Chinese input should work correctly (original bug that started this project)."""

    def test_chinese_task_completes(self, tmp_path):
        """Agent should handle Chinese task description and complete it."""
        target = tmp_path / "中文测试.txt"
        task = f"用write_file工具在{target}写入一行中文：'你好世界，这是测试文件。'"
        config = load_config()

        result = run_react(task, config)

        assert result.success, f"Agent failed on Chinese input: {result.error}"
        assert target.exists(), f"File not created"
        content = target.read_text(encoding="utf-8")
        assert "你好" in content or "测试" in content, f"Chinese content missing: {content[:200]}"

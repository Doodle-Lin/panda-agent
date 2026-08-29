"""Context compression — truncate old tool results when messages get too long.

Inspired by:
- opencode compaction.ts: select recent N turns, summarize old ones
- Hermes context_compressor.py: prune_tool_results_only() — replace old
  tool outputs with "[Old tool result content cleared]"

panda's version: simple token estimation + old tool result truncation.
No LLM-based summarization (too expensive for a CLI tool). Instead:
- Estimate tokens per message (chars / 4)
- When total exceeds threshold, truncate oldest tool results
- Keep system prompt + recent 6 turns intact
"""
from panda_agent.react import _estimate_tokens, _compress_messages


class TestTokenEstimation:
    def test_estimate_tokens_basic(self):
        """_estimate_tokens returns ~chars/4 for text."""
        text = "a" * 100
        assert _estimate_tokens(text) == 25

    def test_estimate_tokens_empty(self):
        """Empty string = 0 tokens."""
        assert _estimate_tokens("") == 0

    def test_estimate_tokens_unicode(self):
        """Chinese chars should count more (1 char ≈ 1-2 tokens)."""
        # Chinese text: 4 chars should be more than 1 token
        text = "你好世界"
        assert _estimate_tokens(text) >= 1


class TestCompressMessages:
    def test_compress_preserves_system_prompt(self):
        """System prompt must never be compressed."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant. " * 100},
            {"role": "user", "content": "Do task"},
            {"role": "assistant", "content": "TOOL_CALL: ..."},
            {"role": "user", "content": "Tool result: " + "x" * 5000},
            {"role": "assistant", "content": "DONE: finished"},
        ]
        result = _compress_messages(messages, threshold=500)
        # System prompt must be unchanged
        assert result[0]["content"] == messages[0]["content"]

    def test_compress_preserves_recent_turns(self):
        """Recent 6 turns (3 user + 3 assistant) should be preserved intact."""
        messages = [
            {"role": "system", "content": "system"},
        ]
        # Add 10 turns of conversation
        for i in range(10):
            messages.append({"role": "assistant", "content": f"Turn {i} response " + "y" * 1000})
            messages.append({"role": "user", "content": f"Turn {i} tool result " + "x" * 5000})

        result = _compress_messages(messages, threshold=100, preserve_recent=6)

        # Last 6 messages should be unchanged
        for i in range(6):
            assert result[-(i+1)]["content"] == messages[-(i+1)]["content"]

    def test_compress_truncates_old_tool_results(self):
        """Old tool results should be truncated when threshold exceeded."""
        messages = [
            {"role": "system", "content": "system"},
            {"role": "assistant", "content": "TOOL_CALL: something"},
            {"role": "user", "content": "Tool result: " + "x" * 5000},
            {"role": "assistant", "content": "DONE: finished"},
        ]
        result = _compress_messages(messages, threshold=200, preserve_recent=1)

        # Old tool result (index 2) should be truncated — only last 1 preserved
        # So index 0,1,2 are compressible
        old_msg = result[2]
        assert "Tool result" in old_msg["content"]
        # Should be much shorter than original 5000 chars
        assert len(old_msg["content"]) < 500

    def test_compress_reduces_total_tokens(self):
        """After compression, total estimated tokens should be significantly less."""
        messages = [
            {"role": "system", "content": "system"},
        ]
        for i in range(10):
            messages.append({"role": "assistant", "content": f"resp {i} " + "y" * 2000})
            messages.append({"role": "user", "content": f"Tool result: {i} " + "x" * 8000})

        original_tokens = sum(_estimate_tokens(m["content"]) for m in messages)
        result = _compress_messages(messages, threshold=1000, preserve_recent=6)
        compressed_tokens = sum(_estimate_tokens(m["content"]) for m in result)

        assert compressed_tokens < original_tokens * 0.5  # at least 50% reduction

    def test_compress_noop_when_under_threshold(self):
        """When total tokens < threshold, no compression should happen."""
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "short message"},
            {"role": "assistant", "content": "DONE: done"},
        ]
        result = _compress_messages(messages, threshold=10000, preserve_recent=6)
        assert result == messages  # unchanged

"""Test: patch_file with fuzzy matching — tolerate whitespace differences.

Current patch_file does exact str.replace — fails on:
- Leading/trailing whitespace differences
- Indentation differences (tabs vs spaces)
- Extra blank lines

Fuzzy matching should try:
1. Exact match (current behavior)
2. Strip leading/trailing whitespace on both sides
3. Normalize line endings
4. Tab-to-space normalization
"""
import sys, os, tempfile
sys.path.insert(0, r'E:\workspace\evo-agent\src')
os.environ['PANDA_HOME'] = os.path.expanduser('~/.panda')

from panda_agent.tools import _tool_patch_file


class TestPatchFileFuzzy:
    """patch_file should tolerate minor whitespace differences."""

    def _write_temp(self, content):
        import tempfile as _tf, os as _os
        d = _tf.mkdtemp()
        # Set PANDA_WORKSPACE so safe_path allows the temp dir
        _os.environ["PANDA_WORKSPACE"] = d
        f = _os.path.join(d, "test_file.py")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(content)
        return f

    def test_exact_match(self):
        """Exact match should work (baseline)."""
        path = self._write_temp("def hello():\n    return 'world'\n")
        result = _tool_patch_file(path, "return 'world'", "return 'hello'")
        assert "Patched" in result
        with open(path, encoding='utf-8') as f:
            assert "return 'hello'" in f.read()
        os.unlink(path)

    def test_trailing_whitespace_tolerated(self):
        """old_string with trailing space should match content without it."""
        path = self._write_temp("def hello():\n    return 'world'\n")
        # old_string has trailing space, content doesn't
        result = _tool_patch_file(path, "return 'world' ", "return 'hello'")
        assert "Patched" in result, f"Should tolerate trailing whitespace. Got: {result}"
        os.unlink(path)

    def test_leading_whitespace_tolerated(self):
        """old_string with leading space should match content without it."""
        path = self._write_temp("x = 1\n")
        # old_string has leading space, content doesn't
        result = _tool_patch_file(path, " x = 1", "x = 2")
        assert "Patched" in result, f"Should tolerate leading whitespace. Got: {result}"
        os.unlink(path)

    def test_tab_space_normalization(self):
        """Tabs in old_string should match spaces in content and vice versa."""
        path = self._write_temp("def f():\n    return 1\n")  # 4 spaces
        # old_string uses tab
        result = _tool_patch_file(path, "\treturn 1", "    return 2")
        assert "Patched" in result, f"Should normalize tabs to spaces. Got: {result}"
        os.unlink(path)

    def test_line_ending_normalization(self):
        """CRLF in content should match LF in old_string."""
        import os as _os, tempfile as _tf
        d = _tf.mkdtemp()
        _os.environ["PANDA_WORKSPACE"] = d
        path = _os.path.join(d, "test_crlf.py")
        with open(path, "wb") as f:
            f.write(b"def hello():\r\n    return 'world'\r\n")
        # old_string uses LF, content uses CRLF
        result = _tool_patch_file(path, "return 'world'", "return 'hello'")
        assert "Patched" in result, f"Should normalize line endings. Got: {result}"
        _os.unlink(path)

    def test_still_fails_on_genuine_mismatch(self):
        """Genuinely different content should still fail."""
        path = self._write_temp("x = 1\n")
        result = _tool_patch_file(path, "y = 2", "y = 3")
        assert "Error" in result or "not found" in result
        os.unlink(path)

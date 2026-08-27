"""Tests for execution boundaries.

The first two classes replay attacks that were verified to work against the
pre-existing code, so they fail loudly if the boundary is ever removed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from panda_agent.security import (
    DEFAULT_ALLOWED_COMMANDS,
    SecurityError,
    allowed_commands,
    parse_command,
    resolve_path,
    safe_path,
    sanitized_env,
    unsafe_mode,
    workspace_root,
)
from panda_agent.tools import execute_tool


# ---------------------------------------------------------------------------
# Command injection
# ---------------------------------------------------------------------------

class TestCommandInjection:
    """``shell=True`` made every one of these executable."""

    @pytest.mark.parametrize("command", [
        "echo SAFE; echo INJECTED",       # verified working before the fix
        "echo a && echo b",
        "echo a || echo b",
        "echo x | wc -c",                 # verified working before the fix
        "echo x > /tmp/panda_pwned",
        "echo $(whoami)",
        "echo `whoami`",
        "cat /etc/passwd & ",
        "python -c 'import os' ; rm -rf /",
    ])
    def test_metacharacters_are_rejected(self, command):
        with pytest.raises(SecurityError, match="metacharacter"):
            parse_command(command)

    def test_injection_through_the_tool_is_blocked(self):
        """End-to-end: the exact payload that previously ran both halves."""
        out = execute_tool("run_command", {"command": "echo SAFE; echo INJECTED"})
        assert "INJECTED" not in out
        assert "Error" in out

    def test_allowlisted_command_still_works(self):
        out = execute_tool("run_command", {"command": "echo hello"})
        assert "hello" in out

    def test_command_with_arguments_works(self):
        # Use "python" (not "python3") — it's on the allowlist and resolves
        # to the current interpreter on all platforms.
        out = execute_tool("run_command", {"command": "python --version"})
        assert "Python" in out

    def test_quoted_argument_is_preserved(self):
        argv = parse_command('echo "hello world"')
        assert argv == ["echo", "hello world"]


class TestCommandAllowlist:
    @pytest.mark.parametrize("program", ["curl", "wget", "ssh", "nc", "sudo", "rm", "chmod"])
    def test_dangerous_commands_are_not_allowed(self, program):
        assert program not in DEFAULT_ALLOWED_COMMANDS
        with pytest.raises(SecurityError, match="not allowed"):
            parse_command(f"{program} something")

    @pytest.mark.parametrize("program", ["python3", "pytest", "git", "ls", "grep"])
    def test_needed_commands_are_allowed(self, program):
        assert parse_command(f"{program} --help")[0] == program

    def test_absolute_path_is_checked_by_basename(self):
        """``/usr/bin/curl`` must not slip past a name-based allowlist."""
        with pytest.raises(SecurityError, match="not allowed"):
            parse_command("/usr/bin/curl http://example.com")
        assert parse_command("/usr/bin/git status")[0] == "/usr/bin/git"

    def test_empty_command_is_rejected(self):
        with pytest.raises(SecurityError, match="empty"):
            parse_command("   ")

    def test_allowlist_is_extendable_via_env(self, monkeypatch):
        with pytest.raises(SecurityError):
            parse_command("cowsay moo")
        monkeypatch.setenv("PANDA_ALLOWED_COMMANDS", "cowsay")
        assert "cowsay" in allowed_commands()
        assert parse_command("cowsay moo")[0] == "cowsay"


# ---------------------------------------------------------------------------
# Path traversal
# ---------------------------------------------------------------------------

class TestPathTraversal:
    def test_parent_traversal_is_rejected(self, tmp_path):
        with pytest.raises(SecurityError, match="escapes the workspace"):
            resolve_path("../../../etc/passwd", root=tmp_path)

    def test_absolute_path_outside_workspace_is_rejected(self, tmp_path):
        with pytest.raises(SecurityError, match="escapes the workspace"):
            resolve_path("/etc/passwd", root=tmp_path)

    def test_disguised_traversal_is_rejected(self, tmp_path):
        """Resolution must happen before the containment check."""
        with pytest.raises(SecurityError, match="escapes the workspace"):
            resolve_path("subdir/../../outside.txt", root=tmp_path)

    def test_path_inside_workspace_is_allowed(self, tmp_path):
        assert resolve_path("sub/file.txt", root=tmp_path) == tmp_path / "sub" / "file.txt"

    def test_workspace_root_itself_is_allowed(self, tmp_path):
        assert resolve_path(".", root=tmp_path) == tmp_path.resolve()

    def test_empty_path_is_rejected(self, tmp_path):
        with pytest.raises(SecurityError, match="empty path"):
            resolve_path("", root=tmp_path)

    def test_symlink_escape_is_rejected(self, tmp_path):
        outside = tmp_path.parent / "outside_target"
        outside.mkdir(exist_ok=True)
        try:
            (tmp_path / "link").symlink_to(outside)
        except OSError:
            pytest.skip("symlinks require admin privileges on Windows")
        with pytest.raises(SecurityError, match="escapes the workspace"):
            resolve_path("link/secret.txt", root=tmp_path)

    def test_read_outside_workspace_is_blocked_through_the_tool(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PANDA_WORKSPACE", str(tmp_path))
        out = execute_tool("read_file", {"path": "/etc/hosts"})
        assert "escapes the workspace" in out

    def test_write_outside_workspace_is_blocked_through_the_tool(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PANDA_WORKSPACE", str(tmp_path))
        target = tmp_path.parent / "should_not_exist.txt"
        out = execute_tool("write_file", {"path": str(target), "content": "x"})
        assert "escapes the workspace" in out
        assert not target.exists()

    def test_file_tools_work_inside_the_workspace(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PANDA_WORKSPACE", str(tmp_path))
        assert "Wrote" in execute_tool(
            "write_file", {"path": "note.txt", "content": "hello"}
        )
        assert "hello" in execute_tool("read_file", {"path": "note.txt"})


# ---------------------------------------------------------------------------
# Escape hatch
# ---------------------------------------------------------------------------

class TestUnsafeMode:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("PANDA_UNSAFE", raising=False)
        assert unsafe_mode() is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
    def test_recognised_values(self, monkeypatch, value):
        monkeypatch.setenv("PANDA_UNSAFE", value)
        assert unsafe_mode() is True

    def test_safe_path_bypasses_containment_when_unsafe(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PANDA_UNSAFE", "1")
        assert safe_path("/etc/passwd") == Path("/etc/passwd")

    def test_safe_path_enforces_by_default(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PANDA_UNSAFE", raising=False)
        monkeypatch.setenv("PANDA_WORKSPACE", str(tmp_path))
        with pytest.raises(SecurityError):
            safe_path("/etc/passwd")


# ---------------------------------------------------------------------------
# Environment scrubbing
# ---------------------------------------------------------------------------

class TestSanitizedEnv:
    @pytest.mark.parametrize("name", [
        "OPENAI_API_KEY", "PANDA_API_KEY", "ANTHROPIC_AUTH_TOKEN",
        "AWS_SECRET_ACCESS_KEY", "DB_PASSWORD", "GH_TOKEN",
        "SESSION_COOKIE", "PRIVATE_KEY",
    ])
    def test_credential_shaped_variables_are_removed(self, monkeypatch, name):
        monkeypatch.setenv(name, "sensitive")
        assert name not in sanitized_env()

    def test_ordinary_variables_survive(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("HOME", "/home/x")
        env = sanitized_env()
        assert env.get("PATH") == "/usr/bin"
        assert env.get("HOME") == "/home/x"

    def test_subprocess_does_not_receive_credentials(self, monkeypatch):
        monkeypatch.setenv("PANDA_API_KEY", "sk-should-not-leak")
        out = execute_tool("run_command", {"command": "python3 -c 'import os; print(os.environ.get(\"PANDA_API_KEY\"))'"})
        # The quoting makes this a single argv element; either it is rejected
        # for metacharacters or it runs without the key present.
        assert "sk-should-not-leak" not in out


# ---------------------------------------------------------------------------
# search_files no longer builds a program from its arguments
# ---------------------------------------------------------------------------

class TestSearchFiles:
    def test_finds_matches_with_line_numbers(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PANDA_WORKSPACE", str(tmp_path))
        (tmp_path / "a.py").write_text("x = 1\n# TODO: fix\ny = 2\n")
        out = execute_tool("search_files", {"path": ".", "pattern": "TODO"})
        assert "a.py:2" in out

    def test_invalid_regex_is_reported_not_raised(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PANDA_WORKSPACE", str(tmp_path))
        out = execute_tool("search_files", {"path": ".", "pattern": "([unclosed"})
        assert "invalid regex" in out

    def test_quote_in_pattern_is_not_code(self, tmp_path, monkeypatch):
        """The old implementation embedded the pattern in Python source."""
        monkeypatch.setenv("PANDA_WORKSPACE", str(tmp_path))
        (tmp_path / "a.py").write_text("value = 'quoted'\n")
        out = execute_tool("search_files", {"path": ".", "pattern": "'quoted'"})
        assert "a.py:1" in out

    def test_no_matches_message(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PANDA_WORKSPACE", str(tmp_path))
        (tmp_path / "a.py").write_text("nothing here\n")
        assert "No matches" in execute_tool(
            "search_files", {"path": ".", "pattern": "ZZZ"}
        )

    def test_traversal_in_search_path_is_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PANDA_WORKSPACE", str(tmp_path))
        out = execute_tool("search_files", {"path": "../..", "pattern": "x"})
        assert "escapes the workspace" in out

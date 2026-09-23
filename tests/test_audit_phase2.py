"""Audit Phase 2 security hardening tests.

Covers audit findings #5, #6, #7 from the 2026-09-14 audit.

#5: resolve_path uses .resolve() which follows symlinks. A workspace-internal
    symlink pointing outside the workspace could read out. The current
    implementation already rejects this case via the `root not in
    resolved.parents` check, but the only existing test
    (`test_symlink_escape_is_rejected`) requires admin privileges on Windows
    to create a real symlink. We add a mock-based test that does not need
    admin, so the case is exercised on every platform.

#6: `pip install <pkg>` / `uv ... install` is arbitrary code execution under
    the allowlist (pip/uv are permitted, and `pip install` runs `setup.py`
    or build hooks). Block `install`/`download` subcommands of pip/uv by
    default; allow when `PANDA_ALLOW_INSTALL=1` is set so the user can
    opt in deliberately.

#7: `_SHELL_METACHARACTERS` included `*`, `?`, `!`, which are glob/regex
    chars, not shell operators. Under `shell=False` they are harmless, and
    rejecting them broke legitimate uses like `grep "TODO.*FIXME"` (when
    unquoted, which the agent may emit). Narrow the set to actual shell
    operators: `;`, `&`, `|`, `` ` ``, `$`, `(`, `)`, `<`, `>`, `{`, `}`,
    `[`, `]`, newline, carriage return.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from panda_agent.security import (
    SecurityError,
    parse_command,
    resolve_path,
)


# ---------------------------------------------------------------------------
# #5: symlink escape (mock-based so it runs without admin on Windows)
# ---------------------------------------------------------------------------

class TestSymlinkEscapeMocked:
    """A workspace-internal symlink that resolves outside must be rejected.

    The existing `test_symlink_escape_is_rejected` in test_security.py uses
    a real symlink, which on Windows requires admin privileges. This mock
    variant simulates the same escape so the case is covered on every
    platform: the pre-fix code would resolve the candidate through the
    symlink to the outside target and accept it.
    """

    def test_symlink_resolving_outside_is_rejected(self, tmp_path, monkeypatch):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("leaked", encoding="utf-8")

        # Simulate a symlink: workspace/link -> outside. Path.resolve()
        # would follow the symlink and return the outside target. We
        # patch resolve so 'workspace/link/secret.txt' resolves to
        # outside/secret.txt.
        real_resolve = Path.resolve

        def fake_resolve(self, *args, **kwargs):
            s = str(self)
            if s.endswith(str(Path("link") / "secret.txt")) and str(workspace) in s:
                return outside / "secret.txt"
            return real_resolve(self, *args, **kwargs)

        with patch.object(Path, "resolve", fake_resolve):
            with pytest.raises(SecurityError, match="escapes the workspace"):
                resolve_path("link/secret.txt", root=workspace)


# ---------------------------------------------------------------------------
# #6: pip/uv install gated by default
# ---------------------------------------------------------------------------

class TestPipInstallGate:
    """``pip install <pkg>`` is arbitrary code execution; block by default."""

    @pytest.mark.parametrize("command", [
        "pip install evil-pkg",
        "pip install --upgrade evil-pkg",
        "uv pip install evil-pkg",
        "uv install evil-pkg",
        "uv pip download evil-pkg",
        "pip download evil-pkg",
    ])
    def test_install_subcommand_blocked_by_default(self, command, monkeypatch):
        monkeypatch.delenv("PANDA_ALLOW_INSTALL", raising=False)
        with pytest.raises(SecurityError, match="blocked by default"):
            parse_command(command)

    @pytest.mark.parametrize("command", [
        "pip install evil-pkg",
        "uv pip install evil-pkg",
        "uv install evil-pkg",
    ])
    def test_install_subcommand_allowed_when_env_set(self, command, monkeypatch):
        monkeypatch.setenv("PANDA_ALLOW_INSTALL", "1")
        # Should NOT raise: user explicitly opted in.
        argv = parse_command(command)
        assert argv[0] in ("pip", "uv")

    @pytest.mark.parametrize("command", [
        "pip list",
        "pip show numpy",
        "pip --version",
        "uv pip list",
        "uv --version",
    ])
    def test_non_install_pip_subcommands_allowed_by_default(self, command, monkeypatch):
        monkeypatch.delenv("PANDA_ALLOW_INSTALL", raising=False)
        # Should not raise — these are read-only pip/uv operations.
        argv = parse_command(command)
        assert argv[0] in ("pip", "uv")


# ---------------------------------------------------------------------------
# #7: glob/regex chars allowed under shell=False
# ---------------------------------------------------------------------------

class TestMetacharacterNarrowing:
    """Glob/regex chars `*` `?` `!` are safe under shell=False; allow them."""

    @pytest.mark.parametrize("command", [
        # Unquoted glob/regex chars in the argument position. Pre-fix these
        # were rejected because `*`, `?`, `!` were in _SHELL_METACHARACTERS.
        # Under shell=False they are literal — there is no shell to expand
        # them — so they are safe.
        r'grep "TODO.*FIXME" src/',
        'grep "TODO?FIXME" src/',
        'find . -name "*.py"',
        'git log --oneline --since="2 weeks ago"',
    ])
    def test_glob_chars_allowed(self, command):
        # Must not raise — these are legitimate uses under shell=False.
        argv = parse_command(command)
        assert argv[0] in ("grep", "find", "git")

    @pytest.mark.parametrize("command", [
        "echo SAFE; echo INJECTED",
        "echo a && echo b",
        "echo x | wc -c",
        "echo x > /tmp/panda_pwned",
        "echo $(whoami)",
        "echo `whoami`",
        "cat /etc/passwd & ",
        "python -c 'import os' ; rm -rf /",
    ])
    def test_real_shell_operators_still_rejected(self, command):
        with pytest.raises(SecurityError, match="metacharacter"):
            parse_command(command)

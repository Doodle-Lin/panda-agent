"""Execution boundaries for LLM-directed tool calls.

The threat model is not a malicious user -- it is that the agent's next action
is chosen by a language model whose input includes file contents and command
output. Anything the agent reads can influence what it does next, so tool
implementations cannot assume their arguments are benign.

Two concrete holes this closes, both verified against the pre-existing code:

**Shell injection.** ``run_command`` used ``shell=True``, so
``echo SAFE; echo INJECTED`` executed both halves. Metacharacters, pipes,
redirection and command substitution were all available to whatever the model
emitted.

**Path traversal.** The file tools called ``Path(path)`` directly with no
containment check, so a relative path with ``..`` segments reached anywhere the
process could read or write.

Enforcement is opt-out rather than opt-in: ``PANDA_UNSAFE=1`` restores the old
behaviour for users who genuinely need it, but the default is bounded.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path


class SecurityError(Exception):
    """Raised when a tool call violates an execution boundary."""


# ---------------------------------------------------------------------------
# Command allowlist
# ---------------------------------------------------------------------------

#: Commands the agent may invoke. Deliberately conservative: read-only
#: inspection plus the interpreters and VCS operations the evolution loop
#: actually needs. Anything absent is a deliberate decision, not an oversight.
DEFAULT_ALLOWED_COMMANDS: frozenset[str] = frozenset({
    # Interpreters / test runners
    "python", "python3", "pytest", "ruff", "mypy",
    # Read-only inspection
    "ls", "cat", "head", "tail", "wc", "grep", "rg", "find", "diff", "file",
    "stat", "du", "tree", "sort", "uniq", "cut", "basename", "dirname",
    # OS detection (needed by the agent to adapt paths/commands)
    "ver", "uname", "whoami", "hostname",
    # Version control
    "git",
    # Package management (needed to install a dependency a patch introduces)
    "pip", "uv",
    # Trivially safe
    "echo", "pwd", "true", "false", "date",
})

#: Shell metacharacters that indicate an attempt to chain, pipe, redirect or
#: substitute. Their presence means the caller expected a shell, and running
#: the string through one is exactly what this module prevents.
_SHELL_METACHARACTERS = frozenset(";|&<>`$(){}[]!*?\n\r")


def unsafe_mode() -> bool:
    """True when the operator has explicitly disabled enforcement."""
    return os.environ.get("PANDA_UNSAFE", "").strip().lower() in {"1", "true", "yes"}


def allowed_commands() -> frozenset[str]:
    """The active allowlist, extendable via ``PANDA_ALLOWED_COMMANDS``."""
    extra = os.environ.get("PANDA_ALLOWED_COMMANDS", "")
    if not extra.strip():
        return DEFAULT_ALLOWED_COMMANDS
    names = {n.strip() for n in extra.replace(",", " ").split() if n.strip()}
    return DEFAULT_ALLOWED_COMMANDS | names


def parse_command(command: str) -> list[str]:
    """Validate a command string and return an argv list.

    Raises :class:`SecurityError` when the command is not on the allowlist or
    contains shell metacharacters **outside of quoted arguments**. Returning
    argv (rather than a string) is what lets the caller drop ``shell=True``
    entirely -- with no shell in the picture, injection has nowhere to happen.

    ``|`` inside a quoted argument (e.g. ``grep -E "TODO|FIXME"``) is a
    regex alternation, not a shell pipe -- it is safe because there is no
    shell to interpret it. Only metacharacters that appear in the
    **unquoted** part of the command string are blocked.
    """
    if not command or not command.strip():
        raise SecurityError("empty command")

    # Parse into tokens first. shlex separates quoted from unquoted parts.
    try:
        argv = shlex.split(command)
    except ValueError as e:
        raise SecurityError(f"could not parse command: {e}") from e

    if not argv:
        raise SecurityError("empty command")

    # Re-join the unquoted portions and check for metacharacters there.
    # The quoted portions are safe: they are passed as literal argv
    # elements, never interpreted by a shell.
    # We reconstruct the "unquoted surface" by checking the original
    # command string character by character, tracking quote state.
    unquoted_chars: list[str] = []
    in_single = False
    in_double = False
    for ch in command:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            unquoted_chars.append(ch)
    unquoted_surface = "".join(unquoted_chars)

    found = _SHELL_METACHARACTERS & set(unquoted_surface)
    if found:
        raise SecurityError(
            f"command contains shell metacharacters {sorted(found)}. "
            "Chaining, pipes, redirection and substitution are not available; "
            "run one command per call, or write a script and execute it. "
            "(Metacharacters inside quoted arguments are allowed — they "
            "are passed as literal text, not interpreted by a shell.)"
        )

    program = Path(argv[0]).name
    permitted = allowed_commands()
    if program not in permitted:
        raise SecurityError(
            f"command '{program}' is not allowed. "
            f"Permitted: {', '.join(sorted(permitted))}. "
            "Extend with PANDA_ALLOWED_COMMANDS if this is intended."
        )

    return argv


# ---------------------------------------------------------------------------
# Filesystem containment
# ---------------------------------------------------------------------------

def workspace_root() -> Path:
    """The directory tool file access is confined to."""
    return Path(os.environ.get("PANDA_WORKSPACE", os.getcwd())).resolve()


def resolve_path(path: str, root: Path | None = None) -> Path:
    """Resolve ``path`` inside the workspace, rejecting escapes.

    Resolution happens before the containment check so that ``..`` segments
    and symlinks are normalised first -- checking the raw string would let
    ``a/../../etc/passwd`` through.
    """
    if not path or not str(path).strip():
        raise SecurityError("empty path")

    root = (root or workspace_root()).resolve()
    candidate = Path(path)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()

    if resolved != root and root not in resolved.parents:
        raise SecurityError(
            f"path escapes the workspace: {path!r} resolves outside {root}. "
            "Set PANDA_WORKSPACE to widen the boundary deliberately."
        )
    return resolved


def safe_path(path: str, root: Path | None = None) -> Path:
    """Like :func:`resolve_path`, but a no-op under ``PANDA_UNSAFE=1``."""
    if unsafe_mode():
        return Path(path)
    return resolve_path(path, root)


# ---------------------------------------------------------------------------
# Environment scrubbing
# ---------------------------------------------------------------------------

#: Substrings marking variables that should not reach a subprocess. Matching on
#: fragments rather than exact names catches provider-prefixed variants such as
#: ``OPENAI_API_KEY`` and ``ANTHROPIC_AUTH_TOKEN`` without enumerating them.
_SENSITIVE_FRAGMENTS = (
    "KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL",
    "SESSION", "COOKIE", "PRIVATE",
)


def sanitized_env() -> dict[str, str]:
    """A copy of the environment with credential-shaped variables removed.

    A subprocess started on the model's instruction has no need for the API
    keys of the process that started it, and a command that echoes its
    environment should not be a credential disclosure.
    """
    return {
        k: v
        for k, v in os.environ.items()
        if not any(frag in k.upper() for frag in _SENSITIVE_FRAGMENTS)
    }

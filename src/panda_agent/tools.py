"""Built-in tools for PandaAgent.

Each tool is a simple function that takes args and returns a string.
Tools are deterministic and testable — no LLM calls inside tools.

The Improver can patch this file to improve tool implementations.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any

from .security import SecurityError, parse_command, safe_path, sanitized_env


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOLS: dict[str, dict[str, Any]] = {}

def register(name: str, description: str, params: dict, handler):
    """Register a tool."""
    TOOLS[name] = {
        "name": name,
        "description": description,
        "params": params,
        "handler": handler,
    }


# ---------------------------------------------------------------------------
# Built-in tools
# ---------------------------------------------------------------------------

def _tool_read_file(path: str, **kw) -> str:
    """Read a file and return its contents."""
    try:
        p = safe_path(path)
    except SecurityError as e:
        return f"Error: {e}"
    if not p.exists():
        return f"Error: file not found: {path}"
    if p.is_dir():
        return f"Error: path is a directory, not a file: {path}"
    try:
        # utf-8-sig handles BOM-prefixed files (common on Windows) while
        # being a strict superset of utf-8 for files without a BOM.
        # Proposed by the self-evolution loop's Improver during observation
        # run 5 (docs/runs/2026-09-04_real_baseline_attempt.md).
        content = p.read_text(encoding="utf-8-sig", errors="replace")
        if len(content) > 50000:
            return content[:50000] + "\n...[truncated]"
        return content
    except Exception as e:
        return f"Error reading file: {e}"


def _tool_write_file(path: str, content: str, **kw) -> str:
    """Write content to a file (creates parent dirs)."""
    try:
        p = safe_path(path)
    except SecurityError as e:
        return f"Error: {e}"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


def _tool_search_files(path: str, pattern: str, **kw) -> str:
    """Search file contents with a regex, reporting file and line number.

    Runs in-process. The previous implementation interpolated ``path`` and
    ``pattern`` into a Python source string and executed it via ``python -c``,
    which made tool arguments part of a program -- an injection surface with no
    upside, since the search needs no subprocess at all.
    """
    try:
        root = safe_path(path)
    except SecurityError as e:
        return f"Error: {e}"

    if not root.exists():
        return f"Error: path not found: {path}"

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"Error: invalid regex {pattern!r}: {e}"

    skip = ("__pycache__", ".git", ".venv", "node_modules", ".mypy_cache")
    matches: list[str] = []
    limit = 500

    targets = [root] if root.is_file() else root.rglob("*")
    for f in targets:
        if len(matches) >= limit:
            matches.append(f"...[stopped at {limit} matches]")
            break
        # Match skip dirs against path components, not substrings of the
        # full path string. The old `any(s in str(f) for s in skip)` would
        # skip `.github` because `.git` is a substring of it.
        # Proposed by the self-evolution loop's Improver during observation
        # run 4 (docs/runs/2026-09-04_observation_clean_attempt.md).
        if not f.is_file() or any(part in skip for part in f.parts):
            continue
        try:
            lines = f.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            if regex.search(line):
                matches.append(f"{f}:{i}: {line.strip()[:120]}")
                if len(matches) >= limit:
                    break

    return "\n".join(matches) if matches else "No matches found"


def _tool_list_files(path: str = ".", **kw) -> str:
    """List files in a directory."""
    try:
        p = safe_path(path)
    except SecurityError as e:
        return f"Error: {e}"
    try:
        if not p.exists():
            return f"Error: path not found: {path}"
        entries = []
        for entry in sorted(p.iterdir()):
            prefix = "DIR " if entry.is_dir() else "FILE"
            entries.append(f"{prefix} {entry.name}")
        return "\n".join(entries) if entries else "Empty directory"
    except Exception as e:
        return f"Error listing files: {e}"


def _tool_run_command(command: str, timeout: int = 60, **kw) -> str:
    """Run an allowlisted command and return its output.

    Executes via argv with no shell, so metacharacters cannot chain, pipe or
    substitute -- previously ``echo SAFE; echo INJECTED`` ran both halves.
    Credential-shaped environment variables are stripped from the child.
    """
    try:
        argv = parse_command(command)
    except SecurityError as e:
        return f"Error: {e}"

    try:
        result = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=sanitized_env(),
        )
        output = result.stdout + result.stderr
        if len(output) > 20000:
            output = output[:20000] + "\n...[truncated]"
        return output.strip() if output.strip() else "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        return f"Error running command: {e}"


def _tool_patch_file(path: str, old_string: str, new_string: str, **kw) -> str:
    """Find and replace text in a file with fuzzy matching.

    Tries exact match first, then falls back to:
    1. Strip leading/trailing whitespace on both sides
    2. Tab→space normalization (4 spaces)
    3. Line ending normalization (CRLF→LF)
    """
    try:
        p = safe_path(path)
    except SecurityError as e:
        return f"Error: {e}"
    try:
        if not p.exists():
            return f"Error: file not found: {path}"
        content = p.read_text(encoding="utf-8")

        # Try exact match first
        if old_string in content:
            new_content = content.replace(old_string, new_string, 1)
            p.write_text(new_content, encoding="utf-8")
            return f"Patched {path}: replaced {len(old_string)} chars with {len(new_string)} chars"

        # Fuzzy strategy 1: strip whitespace per line on both sides
        old_stripped = "\n".join(line.strip() for line in old_string.split("\n"))
        content_stripped_lines = [line.strip() for line in content.split("\n")]
        content_stripped = "\n".join(content_stripped_lines)
        if old_stripped in content_stripped:
            idx = content_stripped.index(old_stripped)
            new_content_stripped = content_stripped[:idx] + new_string + content_stripped[idx + len(old_stripped):]
            p.write_text(new_content_stripped, encoding="utf-8")
            return f"Patched {path}: fuzzy match (whitespace), {len(old_string)}->{len(new_string)} chars"

        # Fuzzy strategy 2: tab→space normalization (4 spaces)
        old_tabs = old_string.replace("\t", "    ")
        content_tabs = content.replace("\t", "    ")
        if old_tabs in content_tabs:
            new_content = content_tabs.replace(old_tabs, new_string, 1)
            p.write_text(new_content, encoding="utf-8")
            return f"Patched {path}: fuzzy match (tab→space), {len(old_string)}→{len(new_string)} chars"

        # Fuzzy strategy 3: line ending normalization (CRLF -> LF)
        crlf = chr(13) + chr(10)
        old_lf = old_string.replace(crlf, chr(10))
        content_lf = content.replace(crlf, chr(10))
        if old_lf in content_lf:
            new_content = content_lf.replace(old_lf, new_string, 1)
            p.write_text(new_content, encoding="utf-8")
            return f"Patched {path}: fuzzy match (line endings), {len(old_string)}→{len(new_string)} chars"

        return f"Error: old_string not found in {path} (tried exact + 3 fuzzy strategies)"
    except Exception as e:
        return f"Error patching file: {e}"


def _tool_memory_retrieve(query: str, **kw) -> str:
    """Retrieve knowledge from graph memory."""
    try:
        from .memory import MemoryClient
        client = MemoryClient()
        results = client.retrieve(query, top_k=5)
        if not results:
            return "No relevant memory found"
        lines = []
        for r in results:
            score = r.get("score", 0)
            content = r.get("content", "")[:200]
            lines.append(f"[{score:.2f}] {content}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving memory: {e}"


def _tool_memory_write(content: str, title: str = "", **kw) -> str:
    """Write knowledge to graph memory."""
    try:
        from .memory import MemoryClient
        client = MemoryClient()
        result = client.write(content, title=title)
        return f"Memory written: {result}"
    except Exception as e:
        return f"Error writing memory: {e}"


# ---------------------------------------------------------------------------
# Register all tools
# ---------------------------------------------------------------------------

register("read_file", "Read file contents", {"path": "str"}, _tool_read_file)
register("write_file", "Write/create a file", {"path": "str", "content": "str"}, _tool_write_file)
register("search_files", "Search file contents with regex", {"path": "str", "pattern": "str"}, _tool_search_files)
register("list_files", "List directory contents", {"path": "str"}, _tool_list_files)
register("run_command", "Execute a shell command", {"command": "str", "timeout": "int"}, _tool_run_command)
register("patch_file", "Find-and-replace in a file", {"path": "str", "old_string": "str", "new_string": "str"}, _tool_patch_file)
register("memory_retrieve", "Retrieve knowledge from graph memory", {"query": "str"}, _tool_memory_retrieve)
register("memory_write", "Write knowledge to graph memory", {"content": "str", "title": "str"}, _tool_memory_write)


def get_tool_descriptions() -> str:
    """Return formatted tool descriptions for the system prompt."""
    lines = []
    for name, tool in TOOLS.items():
        params = ", ".join(f"{k}: {v}" for k, v in tool["params"].items())
        lines.append(f"- {name}({params}): {tool['description']}")
    return "\n".join(lines)


# Python type → JSON Schema type mapping
_TYPE_MAP = {"str": "string", "int": "integer", "bool": "boolean", "float": "number"}


def get_tool_schemas() -> list[dict]:
    """Return OpenAI-compatible tool schemas for native function calling.

    Format:
        [{"type": "function", "function": {
            "name": "...", "description": "...",
            "parameters": {"type": "object", "properties": {...}, "required": [...]}
        }}]
    """
    schemas = []
    for name, tool in TOOLS.items():
        properties = {}
        required = []
        for param_name, param_type in tool["params"].items():
            json_type = _TYPE_MAP.get(param_type, "string")
            properties[param_name] = {"type": json_type}
            # Heuristic: first param is required, rest optional
            # (matches existing tool definitions where first arg is the primary input)
            if param_name in ("path", "command", "content", "query", "pattern"):
                required.append(param_name)

        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": tool["description"],
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        })
    return schemas


def execute_tool(name: str, args: dict) -> str:
    """Execute a tool by name with given args."""
    if name not in TOOLS:
        return f"Error: unknown tool '{name}'"
    handler = TOOLS[name]["handler"]
    try:
        return handler(**args)
    except TypeError as e:
        return f"Error: invalid args for '{name}': {e}"
    except Exception as e:
        return f"Error in tool '{name}': {e}"

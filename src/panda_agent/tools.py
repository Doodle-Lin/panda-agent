"""Built-in tools for PandaAgent.

Each tool is a simple function that takes args and returns a string.
Tools are deterministic and testable — no LLM calls inside tools.

The Improver can patch this file to improve tool implementations.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any


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
    p = Path(path)
    if not p.exists():
        return f"Error: file not found: {path}"
    if p.is_dir():
        return f"Error: path is a directory, not a file: {path}"
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
        if len(content) > 50000:
            return content[:50000] + "\n...[truncated]"
        return content
    except Exception as e:
        return f"Error reading file: {e}"


def _tool_write_file(path: str, content: str, **kw) -> str:
    """Write content to a file (creates parent dirs)."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


def _tool_search_files(path: str, pattern: str, **kw) -> str:
    """Search file contents with regex."""
    try:
        import subprocess as sp
        result = sp.run(
            ["python", "-c", f"""
import sys, re, os
from pathlib import Path
root = Path({path!r})
pat = re.compile({pattern!r})
for f in root.rglob('*'):
    if f.is_file() and '__pycache__' not in str(f) and '.git' not in str(f):
        try:
            for i, line in enumerate(f.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
                if pat.search(line):
                    print(f"{{f}}:{{i}}: {{line.strip()[:120]}}")
        except: pass
"""],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout.strip()
        return output if output else "No matches found"
    except Exception as e:
        return f"Error searching: {e}"


def _tool_list_files(path: str = ".", **kw) -> str:
    """List files in a directory."""
    try:
        p = Path(path)
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
    """Run a shell command and return output."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
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
    """Find and replace text in a file."""
    try:
        p = Path(path)
        if not p.exists():
            return f"Error: file not found: {path}"
        content = p.read_text(encoding="utf-8")
        if old_string not in content:
            return f"Error: old_string not found in {path}"
        new_content = content.replace(old_string, new_string, 1)
        p.write_text(new_content, encoding="utf-8")
        return f"Patched {path}: replaced {len(old_string)} chars with {len(new_string)} chars"
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

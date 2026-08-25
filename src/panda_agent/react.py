"""ReAct agent loop — manual tool calling via text parsing.

Works with any LLM (no native function calling needed).
Parses TOOL_CALL / DONE / FAILED markers from LLM output.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from typing import Callable

from .brain import build_system_prompt, max_turns_for_task, should_retry
from .config import Config
from .llm import call_llm
from .tools import TOOLS, execute_tool, get_tool_descriptions
from .memory import MemoryClient


@dataclass
class ReActResult:
    """Result of a ReAct loop run."""
    success: bool = False
    answer: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    turns: int = 0
    error: str = ""


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_tool_call(text: str) -> dict | None:
    """Extract TOOL_CALL JSON from LLM response."""
    # Match TOOL_CALL: {...} — use greedy match to get full JSON
    m = re.search(r"TOOL_CALL:\s*(\{.*\})", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        try:
            return json.loads(m.group(1).replace("'", '"'))
        except:
            return None


def _parse_done(text: str) -> str | None:
    """Extract DONE message from LLM response."""
    m = re.search(r"DONE:\s*(.+?)(?:\n|$)", text, re.DOTALL)
    return m.group(1).strip() if m else None


def _parse_failed(text: str) -> str | None:
    """Extract FAILED message from LLM response."""
    m = re.search(r"FAILED:\s*(.+?)(?:\n|$)", text, re.DOTALL)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------
# ReAct loop
# ---------------------------------------------------------------------------

def run_react(
    task: str,
    config: Config,
    *,
    on_event: Callable[[str, str], None] | None = None,
    memory: MemoryClient | None = None,
) -> ReActResult:
    """Run the ReAct loop to complete a task.

    Args:
        task: Natural-language task description.
        config: PandaAgent configuration.
        on_event: Optional callback(event_type, message) for TUI.
        memory: Optional memory client for context injection.

    Returns:
        ReActResult with success, answer, tool_calls, turns.
    """
    def _emit(et, msg):
        if on_event:
            on_event(et, msg)

    # Build system prompt with tool descriptions
    tool_descs = get_tool_descriptions()
    system = build_system_prompt(tool_descs)

    # Inject memory context if available
    if memory and config.memory.enabled:
        ctx = memory.retrieve_context(task, top_k=3)
        if ctx:
            system += "\n\n" + ctx

    # Build message history
    messages = [{"role": "system", "content": system}]
    messages.append({"role": "user", "content": task})

    # Determine max turns
    max_turns = config.agent.max_turns or max_turns_for_task(task)
    tool_calls = []
    result = ReActResult()

    for turn in range(1, max_turns + 1):
        _emit("llm_start", f"Turn {turn}/{max_turns}")

        # Call LLM
        response = call_llm(messages, config.model)

        if not response or response.startswith("ERROR:"):
            _emit("llm_error", response[:200])
            result.error = response
            result.turns = turn
            return result

        # TOOL_CALL takes priority over DONE — if the LLM wants to call
        # a tool, execute it first; only treat as done if no tool call.
        tool_call = _parse_tool_call(response)
        if tool_call:
            tool_name = tool_call.get("name", "")
            tool_args = tool_call.get("args", {})

            _emit("tool_call", f"{tool_name}({tool_args})")

            # Execute tool
            tool_result = execute_tool(tool_name, tool_args)
            _emit("tool_result", tool_result[:200])

            tool_calls.append({"name": tool_name, "args": tool_args, "result": tool_result})

            # Append to conversation
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": f"Tool result:\n{tool_result}"})
            continue

        # Check for DONE (only if no tool call)
        done = _parse_done(response)
        if done:
            _emit("done", done)
            result.success = True
            result.answer = done
            result.tool_calls = tool_calls
            result.turns = turn
            # Auto-write to memory if enabled
            if memory and config.memory.auto_write:
                memory.write(f"Task: {task}\nResult: {done}", title=task[:50])
            return result

        # Check for FAILED
        failed = _parse_failed(response)
        if failed:
            _emit("failed", failed)
            result.error = failed
            result.tool_calls = tool_calls
            result.turns = turn
            return result

        # No tool call, no DONE, no FAILED.
        # Reasoning models (GLM52RJPT) output thinking in reasoning_content
        # without TOOL_CALL:/DONE: markers. Push back and ask for format.
        stripped = response.strip()
        has_format = any(m in stripped for m in ("TOOL_CALL:", "DONE:", "FAILED:"))
        if not has_format and len(stripped) > 5:
            # Reasoning model produced thought but no action marker.
            # Ask it to commit to an action in the required format.
            _emit("llm_thinking", f"Reasoning: {stripped[:80]}... → requesting format")
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": (
                    "Based on your reasoning above, commit to ONE action:\n"
                    '- Call a tool: TOOL_CALL: {"name": "...", "args": {...}}\n'
                    "- Finish: DONE: <summary>\n"
                    "- Give up: FAILED: <reason>\n"
                    "Output ONLY the action, nothing else."
                )
            })
            continue

        # Response too short or starts with Continue — prompt to continue
        if len(stripped) <= 20 or stripped.startswith("Continue"):
            _emit("llm_thinking", response[:100])
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": "Continue. Call a tool or say DONE."})
            continue

        # Fallback: treat as done (non-reasoning model with substantive content)
        _emit("done", stripped[:200])
        result.success = True
        result.answer = stripped
        result.tool_calls = tool_calls
        result.turns = turn
        if memory and config.memory.auto_write:
            memory.write(f"Task: {task}\nResult: {stripped[:200]}", title=task[:50])
        return result

    # Max turns exceeded
    _emit("max_turns", f"Reached max turns ({max_turns})")
    result.error = f"Max turns ({max_turns}) exceeded"
    result.tool_calls = tool_calls
    result.turns = max_turns
    return result

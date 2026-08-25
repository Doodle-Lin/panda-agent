"""ReAct agent loop — manual tool calling via text parsing.

Works with any LLM (no native function calling needed).
Parses TOOL_CALL / DONE / FAILED markers from LLM output.

Key features:
- Reasoning/content separation for reasoning models (GLM52RJPT)
- ExecutionTrace: records every turn, error, and self-repair
- Level 1 Self-Repair: runtime error → adaptive recovery (no code changes)
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from typing import Callable

from .brain import build_system_prompt, max_turns_for_task, should_retry
from .config import Config
from .llm import call_llm_detailed, LLMResponse
from .tools import TOOLS, execute_tool, get_tool_descriptions
from .memory import MemoryClient
from .types import ExecutionTrace, TurnRecord


# ---------------------------------------------------------------------------
# Max steps prompt — injected when max_turns is reached (soft limit)
# Inspired by opencode's max-steps.ts
# ---------------------------------------------------------------------------

MAX_STEPS_PROMPT = (
    "CRITICAL — MAXIMUM STEPS REACHED. "
    "You have reached the maximum number of turns for this task. "
    "Tools are now disabled. Respond with text ONLY (no tool calls).\n\n"
    "Your response MUST include:\n"
    "1. What you have accomplished so far\n"
    "2. Any remaining tasks that were not completed\n"
    "3. Output DONE: <your summary> to finish.\n\n"
    "Do NOT attempt any tool calls. Respond with DONE: and your summary."
)


# ---------------------------------------------------------------------------
# Context compression — truncate old tool results when messages get too long
# Inspired by opencode's compaction.ts + Hermes's prune_tool_results_only()
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    """Cheap token estimation: chars / 4 (English) or chars * 0.5 (CJK heavy).

    Reference: Hermes uses len(text) // 4 as _approx_tokens.
    """
    return max(1, len(text) // 4) if text else 0


def _compress_messages(
    messages: list[dict],
    threshold: int = 20000,
    preserve_recent: int = 6,
) -> list[dict]:
    """Compress messages by truncating old tool results.

    Strategy (simplified from opencode/Hermes):
    1. If total estimated tokens < threshold → return unchanged
    2. Preserve system prompt + last `preserve_recent` messages
    3. For messages in between: truncate user messages containing "Tool result"
       to first 200 chars + "[truncated]"
    """
    total_tokens = sum(_estimate_tokens(m.get("content", "")) for m in messages)
    if total_tokens < threshold:
        return list(messages)

    result = list(messages)
    # Always preserve system prompt (index 0) and last preserve_recent messages
    compressible_end = len(result) - preserve_recent

    for i in range(1, compressible_end):
        msg = result[i]
        if msg["role"] == "user" and "Tool result" in msg.get("content", ""):
            content = msg["content"]
            if len(content) > 300:
                result[i] = {
                    "role": "user",
                    "content": content[:200] + "\n[...truncated for context management...]",
                }

    return result


@dataclass
class ReActResult:
    """Result of a ReAct loop run."""
    success: bool = False
    answer: str = ""
    reasoning: str = ""  # Final reasoning that led to the answer
    tool_calls: list[dict] = field(default_factory=list)
    turns: int = 0
    error: str = ""
    trace: ExecutionTrace | None = None


# ---------------------------------------------------------------------------
# Doom loop detection — same tool call 3x in a row = stuck
# ---------------------------------------------------------------------------

def _check_doom_loop(tool_calls: list[dict]) -> bool:
    """Return True if the last 3 tool calls are identical (same name + same args).

    Inspired by opencode's processor.ts DOOM_LOOP_THRESHOLD = 3.
    Different args = agent trying different approaches = NOT doom loop.
    """
    if len(tool_calls) < 3:
        return False
    last3 = tool_calls[-3:]
    first = last3[0]
    return all(
        tc["name"] == first["name"] and tc["args"] == first["args"]
        for tc in last3
    )


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_tool_call(text: str) -> dict | None:
    """Extract TOOL_CALL JSON from LLM response."""
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
    """Extract DONE message from LLM response.
    
    Captures everything after 'DONE:' until end of text (not just first line).
    This allows multi-line answers like capability lists.
    """
    m = re.search(r"DONE:\s*(.+)", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return None


def _parse_failed(text: str) -> str | None:
    """Extract FAILED message from LLM response."""
    m = re.search(r"FAILED:\s*(.+?)(?:\n|$)", text, re.DOTALL)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------
# Level 1: Self-Repair strategies (runtime, no code changes)
# ---------------------------------------------------------------------------

def _classify_error(error: str) -> str:
    """Classify an error to pick the right repair strategy."""
    error_lower = error.lower()
    if "timeout" in error_lower or "timed out" in error_lower:
        return "timeout"
    if "not found" in error_lower or "no such file" in error_lower or "cannot find" in error_lower:
        return "not_found"
    if "permission" in error_lower or "denied" in error_lower:
        return "permission"
    if "json" in error_lower or "parse" in error_lower:
        return "parse_error"
    if "encoding" in error_lower or "unicode" in error_lower:
        return "encoding"
    if "connection" in error_lower or "network" in error_lower:
        return "network"
    return "unknown"


def _self_repair(error: str, tool_name: str, tool_args: dict, config: Config) -> tuple[str, dict, str]:
    """Level 1 self-repair: adapt tool call to recover from runtime errors.

    Returns (new_tool_name, new_tool_args, repair_strategy_description).
    Does NOT modify any source code — only adjusts the current tool call.
    """
    error_type = _classify_error(error)

    if error_type == "timeout":
        # Reduce timeout or split the task
        new_args = dict(tool_args)
        if "timeout" in new_args:
            new_args["timeout"] = max(5, new_args.get("timeout", 30) // 2)
            return tool_name, new_args, "reduced timeout"
        return tool_name, tool_args, "timeout — no auto-fix, will retry"

    if error_type == "not_found":
        # Try expanding user path, or switch to search_files
        if "path" in tool_args:
            path = tool_args["path"]
            if "~" in path or "Desktop" in path or "桌面" in path:
                import os
                new_args = dict(tool_args)
                new_args["path"] = os.path.expanduser(path.replace("桌面", "Desktop"))
                return tool_name, new_args, f"expanded path: {path} → {new_args['path']}"
            # Try search_files instead
            return "search_files", {"pattern": "*", "path": "."}, "switched to search_files"
        return tool_name, tool_args, "not_found — no path to fix"

    if error_type == "encoding":
        # For run_command, try with explicit encoding
        if tool_name == "run_command":
            new_args = dict(tool_args)
            cmd = new_args.get("command", "")
            if "chcp" not in cmd:
                new_args["command"] = f"chcp 65001 >nul && {cmd}"
                return tool_name, new_args, "added chcp 65001 for UTF-8"
        return tool_name, tool_args, "encoding — no auto-fix available"

    # unknown error type — can't auto-repair
    return tool_name, tool_args, f"no auto-fix for {error_type}"


# ---------------------------------------------------------------------------
# ReAct loop
# ---------------------------------------------------------------------------

def run_react(
    task: str,
    config: Config,
    *,
    on_event: Callable[[str, str], None] | None = None,
    on_reasoning: Callable[[str, str], None] | None = None,
    memory: MemoryClient | None = None,
) -> ReActResult:
    """Run the ReAct loop to complete a task.

    Args:
        task: Natural-language task description.
        config: PandaAgent configuration.
        on_event: Callback(event_type, message) for TUI — action events.
        on_reasoning: Callback(turn_label, reasoning_text) for TUI —
            reasoning/thinking display. Separate from on_event so the TUI
            can render thinking in dim italic small font.
        memory: Optional memory client for context injection.

    Returns:
        ReActResult with success, answer, reasoning, tool_calls, turns, trace.
    """
    def _emit(et, msg):
        if on_event:
            on_event(et, msg)

    def _emit_reasoning(label, text):
        if on_reasoning:
            on_reasoning(label, text)

    # Build system prompt with tool descriptions
    tool_descs = get_tool_descriptions()
    system = build_system_prompt(tool_descs)

    # Inject memory context if available
    if memory and config.memory.enabled:
        ctx = memory.retrieve_context(task, top_k=3)
        if ctx:
            system += "\n\n" + ctx

    messages = [{"role": "system", "content": system}]
    messages.append({"role": "user", "content": task})

    # Use the LARGER of config max_turns and task-based estimate.
    # This ensures complex tasks (write, build, create) get enough turns
    # even when config has a small default.
    task_turns = max_turns_for_task(task)
    config_turns = config.agent.max_turns or 10
    max_turns = max(task_turns, config_turns)
    tool_calls = []
    result = ReActResult()
    trace = ExecutionTrace(task=task, total_turns=max_turns)

    for turn in range(1, max_turns + 1):
        _emit("llm_start", f"Turn {turn}/{max_turns}")

        # Context compression: truncate old tool results when messages get too long
        messages = _compress_messages(messages, threshold=20000, preserve_recent=6)

        # Call LLM with detailed response (reasoning + content separated)
        llm_resp = call_llm_detailed(messages, config.model)

        if llm_resp.is_error:
            _emit("llm_error", llm_resp.error[:200])
            result.error = llm_resp.error
            result.turns = turn
            trace.final_success = False
            trace.add_error(llm_resp.error)
            result.trace = trace
            return result

        # Get the response text (content or reasoning fallback)
        response = llm_resp.text

        # Display reasoning if available (dim italic small font in TUI)
        if llm_resp.reasoning and llm_resp.reasoning.strip():
            reasoning_display = llm_resp.reasoning[:500]
            if len(llm_resp.reasoning) > 500:
                reasoning_display += "..."
            _emit_reasoning(f"Turn {turn}", reasoning_display)

        if not response or not response.strip():
            _emit("llm_error", "Empty response")
            trace.add_error("Empty LLM response")
            messages.append({"role": "assistant", "content": ""})
            messages.append({"role": "user", "content": "Your response was empty. Please call a tool or say DONE."})
            continue

        turn_record = TurnRecord(turn=turn, reasoning=llm_resp.reasoning[:200])

        # TOOL_CALL takes priority over DONE
        tool_call = _parse_tool_call(response)
        if tool_call:
            tool_name = tool_call.get("name", "")
            tool_args = tool_call.get("args", {})

            _emit("tool_call", f"{tool_name}({tool_args})")
            turn_record.action = "TOOL_CALL"
            turn_record.action_args = {"name": tool_name, "args": tool_args}

            # Execute tool
            tool_result = execute_tool(tool_name, tool_args)

            # === Level 1: Self-Repair on tool error ===
            if tool_result.startswith("Error") or tool_result.startswith("ERROR"):
                trace.add_error(tool_result)
                new_name, new_args, strategy = _self_repair(tool_result, tool_name, tool_args, config)

                if (new_name, new_args) != (tool_name, tool_args):
                    _emit("self_repair", f"  ↳ Self-repair: {strategy}")
                    turn_record.self_repaired = True
                    turn_record.repair_strategy = strategy

                    # Retry with repaired call
                    tool_result = execute_tool(new_name, new_args)
                    _emit("tool_call", f"  ↳ {new_name}({new_args})")

                    if not (tool_result.startswith("Error") or tool_result.startswith("ERROR")):
                        _emit("tool_result", f"  ↳ {tool_result[:200]}")
                        tool_calls.append({"name": new_name, "args": new_args, "result": tool_result})
                        trace.add_repair(f"Turn {turn}: {strategy} → recovered")
                    else:
                        _emit("tool_result", f"  ↳ Still failing: {tool_result[:200]}")
                        tool_calls.append({"name": tool_name, "args": tool_args, "result": tool_result})
                        trace.add_repair(f"Turn {turn}: {strategy} → still failing")
                else:
                    _emit("tool_result", tool_result[:200])
                    tool_calls.append({"name": tool_name, "args": tool_args, "result": tool_result})
            else:
                _emit("tool_result", tool_result[:200])
                tool_calls.append({"name": tool_name, "args": tool_args, "result": tool_result})

            turn_record.tool_result = tool_result[:200]
            trace.turns.append(turn_record)

            # === Doom loop detection ===
            simple_calls = [{"name": tc["name"], "args": tc["args"]} for tc in tool_calls]
            if _check_doom_loop(simple_calls):
                _emit("doom_loop", "  ⚠ Detected repeated tool calls — injecting warning")
                trace.add_error(f"Turn {turn}: doom loop — same call 3x")
                # Inject warning prompt, give LLM one more chance
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": (
                        "WARNING: You have called the same tool with the same arguments 3 times in a row. "
                        "This is not making progress. Try a completely different approach or tool. "
                        "If you are stuck, output DONE: or FAILED: with an explanation."
                    ),
                })
                # Check again after this turn — if still repeating, fail
                if len(simple_calls) >= 4 and _check_doom_loop(simple_calls[-3:]):
                    _emit("failed", "Doom loop — agent stuck repeating same tool call")
                    result.error = "Doom loop: repeated same tool call 3x after warning"
                    result.tool_calls = tool_calls
                    result.turns = turn
                    trace.final_success = False
                    result.trace = trace
                    return result
                continue

            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": f"Tool result:\n{tool_result}"})
            continue

        # Check for DONE (only if no tool call)
        done = _parse_done(response)
        if done:
            _emit("done", done)
            result.success = True
            result.answer = done
            result.reasoning = llm_resp.reasoning
            result.tool_calls = tool_calls
            result.turns = turn
            trace.final_success = True
            trace.turns.append(turn_record)
            if memory and config.memory.auto_write:
                memory.write(f"Task: {task}\nResult: {done}", title=task[:50])
            result.trace = trace
            return result

        # Check for FAILED
        failed = _parse_failed(response)
        if failed:
            _emit("failed", failed)
            result.error = failed
            result.tool_calls = tool_calls
            result.turns = turn
            trace.add_error(f"FAILED: {failed}")
            trace.turns.append(turn_record)
            result.trace = trace
            return result

        # No tool call, no DONE, no FAILED.
        # Reasoning models output thinking without markers. Push back.
        stripped = response.strip()
        has_format = any(m in stripped for m in ("TOOL_CALL:", "DONE:", "FAILED:"))
        if not has_format and len(stripped) > 5:
            _emit("llm_thinking", f"  Reasoning → requesting action format")
            turn_record.action = "REASONING"
            trace.turns.append(turn_record)
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

        # Response too short or starts with Continue
        if len(stripped) <= 20 or stripped.startswith("Continue"):
            _emit("llm_thinking", response[:100])
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": "Continue. Call a tool or say DONE."})
            continue

        # Fallback: treat as done (non-reasoning model with substantive content)
        # But strip any TOOL_CALL text from the answer — it's not user-facing content
        clean_answer = re.sub(r"TOOL_CALL:\s*\{.*?\}", "", stripped, flags=re.DOTALL).strip()
        if clean_answer:
            stripped = clean_answer
        _emit("done", stripped[:200])  # event display still truncated for terminal
        result.success = True
        result.answer = stripped  # full answer, not truncated
        result.reasoning = llm_resp.reasoning
        result.tool_calls = tool_calls
        result.turns = turn
        trace.final_success = True
        trace.turns.append(turn_record)
        if memory and config.memory.auto_write:
            memory.write(f"Task: {task}\nResult: {stripped[:200]}", title=task[:50])
        result.trace = trace
        return result

    # Max turns exceeded — inject MAX_STEPS_PROMPT (soft limit, not hard cutoff)
    _emit("max_turns", f"Reached max turns ({max_turns}), injecting MAX_STEPS_PROMPT...")

    # Always try salvage — even without tool calls, LLM may have useful reasoning
    _emit("llm_start", f"Turn {max_turns + 1} (salvage)")
    salvage_messages = list(messages)
    salvage_messages.append({
        "role": "user",
        "content": MAX_STEPS_PROMPT,
    })
    llm_resp = call_llm_detailed(salvage_messages, config.model)
    if not llm_resp.is_error:
        response = llm_resp.text
        done = _parse_done(response)
        if done:
            _emit("done", done[:200])
            result.success = True
            result.answer = done
            result.reasoning = llm_resp.reasoning
            result.tool_calls = tool_calls
            result.turns = max_turns
            trace.final_success = True
            trace.add_error(f"Max turns ({max_turns}) exceeded but salvaged")
            result.trace = trace
            return result

    # Could not salvage
    _emit("failed", f"Max turns ({max_turns}) exceeded, could not complete task")
    result.error = f"Max turns ({max_turns}) exceeded"
    result.tool_calls = tool_calls
    result.turns = max_turns
    trace.final_success = False
    trace.add_error(f"Max turns ({max_turns}) exceeded — task may need more turns")
    result.trace = trace
    return result

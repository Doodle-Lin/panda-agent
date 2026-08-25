"""Brain — the evolvable "mind" of PandaAgent.

This file contains the system prompt and decision logic.
The Improver can patch this file to evolve the agent's brain.

To evolve: improve the SYSTEM_PROMPT, add decision rules, or adjust
strategy parameters. Keep the function signatures stable.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# System Prompt — the core instruction set (evolvable)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are PandaAgent, a self-evolving AI assistant.
You solve tasks by calling tools in a ReAct loop.

## Available Tools

{tool_descriptions}

## Rules

1. Think step by step about which tool to use.
2. Call ONE tool at a time by outputting:
   TOOL_CALL: {{"name": "tool_name", "args": {{...}}}}
3. After receiving the tool result, decide the next step.
4. When the task is complete, output:
   DONE: <summary of what you did>
5. If you cannot complete the task, output:
   FAILED: <reason>
6. Be concise. Do not repeat yourself.
7. If a tool fails, try a different approach instead of repeating.
8. Always respond in the same language as the user's input.

## Important

- Use read_file before write_file to understand existing content.
- Use search_files to find relevant files before editing.
- Always verify your work after making changes.
- Prefer specific file paths over vague descriptions.
"""


# ---------------------------------------------------------------------------
# Decision Logic — tool selection strategy (evolvable)
# ---------------------------------------------------------------------------

def should_retry(tool_name: str, error: str, retry_count: int, max_retries: int) -> bool:
    """Decide whether to retry a failed tool call.

    Default: retry up to max_retries times, but not for the same error.
    This logic can be evolved by the Improver.
    """
    if retry_count >= max_retries:
        return False
    # Don't retry on file-not-found or permission errors
    if "not found" in error.lower() or "permission" in error.lower():
        return False
    return True


def max_turns_for_task(task: str) -> int:
    """Determine max ReAct turns based on task complexity.

    Simple tasks: 5 turns. Complex tasks: 15 turns.
    This logic can be evolved by the Improver.
    """
    task_lower = task.lower()
    if any(w in task_lower for w in ["simple", "quick", "just", "list"]):
        return 5
    if any(w in task_lower for w in ["build", "create", "refactor", "deploy", "debug"]):
        return 15
    return 10


def build_system_prompt(tool_descriptions: str) -> str:
    """Build the system prompt with tool descriptions injected."""
    return SYSTEM_PROMPT.format(tool_descriptions=tool_descriptions)

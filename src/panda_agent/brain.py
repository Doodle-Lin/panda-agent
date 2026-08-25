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
9. If no tool is needed (e.g. greeting, question, chat), immediately output:
   DONE: <your reply>
   Do NOT introduce yourself or list capabilities unless asked.

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

    Simple tasks (e.g., list, summarize, read, show): 5 turns.
    Complex tasks (e.g., build, create, refactor, deploy): 15 turns.
    Default: 10 turns.
    This logic can be evolved by the Improver.
    """
    task_lower = task.lower()
    simple_keywords = [
        "simple", "quick", "just", "list", "summarize", "summary",
        "read", "show", "describe", "explain", "what", "tell",
    ]
    complex_keywords = [
        "build", "create", "refactor", "deploy", "debug", "analyze",
        "implement", "fix", "write", "develop", "design", "integrate",
    ]

    if any(w in task_lower for w in simple_keywords):
        return 5
    if any(w in task_lower for w in complex_keywords):
        return 15
    return 10


def build_system_prompt(tool_descriptions: str) -> str:
    """Build the system prompt with tool descriptions injected.
    
    Includes explicit instructions to ensure the agent:
    - Uses tools to retrieve information when the task requires it
    - Includes actual results in the final answer, not just 'completed'
    - Verifies task completion before reporting success
    """
    base_prompt = SYSTEM_PROMPT.format(tool_descriptions=tool_descriptions)
    additional_instructions = """

CRITICAL RULES FOR TASK COMPLETION:
1. You MUST use available tools to retrieve any information requested by the user. Never answer from memory or assumptions when tools can provide the actual data.
2. Your final answer MUST contain the actual requested information (e.g., file names, contents, results). Never respond with just "completed", "done", or similar without including the substantive results.
3. Before marking a task as complete, verify that you have actually performed the required operations and have the results to share.
4. If a task asks you to list, show, or retrieve something, you MUST call the appropriate tool first and then present the results in your final answer.
5. A task is NOT complete until you have both executed the necessary tool calls AND presented the retrieved information in your final response.
"""
    return base_prompt + additional_instructions


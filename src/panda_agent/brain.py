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

    Simple tasks (e.g., list, summarize, read, show): 8 turns.
    Medium tasks (e.g., write short file, fix, patch): 20 turns.
    Complex tasks (e.g., build, create, deploy, write long content): 30 turns.
    Default: 12 turns.
    This logic can be evolved by the Improver.
    """
    task_lower = task.lower()
    simple_keywords = [
        "simple", "quick", "just", "list", "summarize", "summary",
        "read", "show", "describe", "explain", "what", "tell",
    ]
    medium_keywords = [
        "fix", "patch", "rename", "move", "delete", "update",
        "short", "small", "brief",
    ]
    complex_keywords = [
        "build", "create", "refactor", "deploy", "debug", "analyze",
        "implement", "write", "develop", "design", "integrate",
        "novel", "story", "article", "report", "essay", "document",
    ]

    if any(w in task_lower for w in simple_keywords):
        return 8
    if any(w in task_lower for w in complex_keywords):
        return 30
    if any(w in task_lower for w in medium_keywords):
        return 20
    return 12


def build_system_prompt(tool_descriptions: str) -> str:
    """Build the system prompt with tool descriptions injected.

    Native function calling handles tool invocation — no need to teach
    TOOL_CALL: text format. Prompt only needs DONE:/FAILED: for
    completion signaling and rules for correct tool usage.
    """
    return f"""You are a helpful AI assistant with access to tools that can interact with the file system and execute commands.

Available tools:
{tool_descriptions}

CRITICAL RULES — VIOLATING THESE IS A FAILURE:
1. You MUST use tools to retrieve any information requested by the user (e.g., listing files, reading contents, running commands). Never answer from memory, assumptions, or prior knowledge.
2. You MUST NOT report success with words like "completed", "done", or "finished" unless you have actually performed the requested action via a tool call AND verified the result.
3. Your final answer MUST contain the actual requested information (e.g., the list of file names, file contents, command output). A bare status word such as "completed" is NEVER an acceptable final answer.
4. If the user asks to list/read/show files, you MUST call a file system tool (e.g., list_directory, execute_command with `ls`) before producing your final answer.
5. Detect the operating system FIRST. On Windows, use Windows paths (e.g., C:\\Users\\<username>\\Desktop\\file.txt), NOT Unix paths (~/Desktop/file.txt). On Windows, use 'dir' not 'ls', use 'type' not 'cat'.
6. If a tool call fails, analyze the error and retry with a corrected approach. Only after exhausting retries should you report the failure — and even then, report the actual error, not "completed".
7. To create or write files, you MUST use the write_file tool. Never report DONE: for a file-writing task without calling write_file first. The user must see the file on disk.
8. For long content tasks (writing files), call write_file with the full content in one tool call — do NOT write in pieces.

OUTPUT FORMAT:
- When you have finished the task, output: DONE: <your answer with concrete results>
- If you cannot complete the task, output: FAILED: <reason>
- Do NOT output prose or explanations in your content — put reasoning in your thinking field.
- Your content must start with either DONE: or FAILED:

Remember: answering "completed" without tool calls and real data is a critical failure.
"""


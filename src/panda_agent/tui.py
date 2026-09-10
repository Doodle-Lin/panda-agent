"""TUI renderer — Rich-based terminal UI for PandaAgent.

Design principles (inspired by Claude CLI / Hermes):
- User input and agent answers are the visual focus (bold, boxed)
- Reasoning/thinking is collapsed to one line ("thinking...")
- Tool calls are compact one-liners: ⚡ tool_name(key_arg=value)
- Tool results are folded: first 80 chars + [N more]
- Process noise is dim and minimal — not the main attraction
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel


class TUI:
    """Terminal UI renderer using Rich."""

    def __init__(self, color: str = "auto"):
        self.console = Console(force_terminal=color != "off")

    def banner(self):
        """Print the PandaAgent banner with evolution status."""
        from .evolution_history import evolution_summary
        evo_info = evolution_summary() or ""
        self.console.print(
            Panel(
                f"[bold green]🐼 PandaAgent[/] — Self-Evolving Agent{evo_info}\n"
                "[dim]Type your task, or 'exit' to quit[/]",
                border_style="green",
                padding=(0, 2),
            )
        )

    def user_input(self) -> str:
        """Get user input — visually prominent with a prompt indicator."""
        self.console.print()
        return self.console.input("[bold cyan]You >[/] ")

    def reasoning(self, turn_label: str, text: str):
        """Display reasoning as a single compact line with a keyword summary.

        Not the full text (floods terminal), not just 'thinking...'
        (useless). Show one line: what the agent is thinking ABOUT.

        Example: 'thinking... checking python packages' instead of
        500 chars of reasoning or just 'thinking...'.
        """
        # Extract a short summary from the reasoning text:
        # take the first meaningful sentence/phrase, max 60 chars
        summary = text.strip().replace("\n", " ")
        # Remove common prefixes that add no info
        for prefix in (
            "Let me ", "Let's ", "I need to ", "I should ",
            "I'll ", "I will ", "Now ", "First, ", "Next, ",
            "The user ", "The agent ",
        ):
            if summary.startswith(prefix):
                summary = summary[len(prefix):]
                break
        summary = summary[:60]
        if len(text.strip()) > 60:
            summary = summary.rstrip() + "..."
        self.console.print(f"  [dim italic]💭 {summary}[/]")

    def event(self, event_type: str, message: str):
        """Display a ReAct event — compact, hierarchical, not noisy."""
        if event_type == "llm_start":
            # Don't print turn numbers — they're noise. The tool calls
            # and results show what's happening; turn counters add clutter.
            pass
        elif event_type == "llm_thinking":
            pass  # handled by reasoning()
        elif event_type == "llm_error":
            pass  # errors handled via tool_result "Error..." or the failed event
        elif event_type == "tool_call":
            # Only show file operations (user cares about files being
            # created/modified). Suppress run_command/read_file/etc —
            # they're internal plumbing the user doesn't need to see.
            compact = self._compact_tool_call(message)
            if compact.startswith("write_file") or compact.startswith("patch_file"):
                self.console.print(f"  [yellow]⚡ {compact}[/]")
            # else: suppress — don't print read/search/run_command calls
        elif event_type == "self_repair":
            self.console.print(f"  [magenta]↳ {message[:80]}[/]")
        elif event_type == "tool_result":
            # Only show errors and file write confirmations.
            # Successful command outputs are internal noise.
            if message.startswith("Error") or message.startswith("ERROR"):
                self.console.print(f"  [red]→ {message[:100]}[/]")
            elif message.startswith("Wrote ") or message.startswith("Patched "):
                self.console.print(f"  [green]→ {message[:80]}[/]")
            # else: suppress all other results
        elif event_type == "done":
            self.console.print("  [green]✓ Done[/]")
        elif event_type == "failed":
            self.console.print(
                Panel(message, title="[red]Failed[/]", border_style="red", padding=(0, 2))
            )
        elif event_type == "max_turns":
            self.console.print(f"  [yellow]⚠ {message}[/]")
        elif event_type == "memory_used":
            self.console.print(f"  [cyan]💾 {message}[/]")
        elif event_type == "doom_loop":
            self.console.print(f"  [bold red]⚠ {message[:100]}[/]")
        elif event_type == "memory_tidy":
            self.console.print(f"  [dim]{message[:60]}[/]")
        # === Evolve events — compact ===
        elif event_type == "executor_start":
            self.console.print(f"\n  [dim]▶ {message}[/]")
        elif event_type == "executor_done":
            self.console.print(f"  [green]{message}[/]")
        elif event_type == "learner_detail":
            self.console.print(f"  [dim]💡 {message[:80]}[/]")
        elif event_type == "learner_trigger":
            self.console.print(f"  [bold yellow]⚠ {message[:100]}[/]")
        elif event_type == "improver_done":
            if "✓" in message or "Evolution" in message:
                self.console.print(f"  [green]{message[:120]}[/]")
            else:
                self.console.print(f"  [red]{message[:120]}[/]")
        elif event_type == "improver_detail":
            self.console.print(f"  [dim]{message[:80]}[/]")
        elif event_type == "complete":
            self.console.print(f"\n  [bold green]{message}[/]")
        # === Suppress noisy events ===
        elif event_type in (
            "executor_tools", "learner_start", "learner_done",
            "evaluator_start", "evaluator_done", "score_trend",
            "eval_issue", "target_reached", "stale_stop",
            "improver_start", "improver_error", "round_end",
        ):
            # These are evolve-internal events that clutter the output.
            # Only show them if something important happened (handled above).
            pass
        else:
            self.console.print(f"  [dim]{event_type}: {message[:60]}[/]")

    def _compact_tool_call(self, message: str) -> str:
        """Extract a compact one-line summary of a tool call.

        Input format: 'tool_name({'key': 'value', ...})'
        Output: 'tool_name(key=value)'
        """
        # The message is typically: run_command({'command': 'ls -la', 'timeout': 10})
        # or: write_file({'path': 'C:/Users/...', 'content': '...long...'})
        # Extract tool name and first 1-2 args for a compact display.
        import re

        # Try to parse "tool_name({...})"
        m = re.match(r"(\w+)\((\{.*\})\)", message)
        if not m:
            # Fallback: just truncate the raw message
            return message[:80]

        tool_name = m.group(1)
        try:
            import json
            args = json.loads(m.group(2))
            # Show 1-2 key args, truncate values
            parts = []
            for i, (k, v) in enumerate(args.items()):
                if i >= 2:
                    break
                v_str = str(v)
                if len(v_str) > 40:
                    v_str = v_str[:37] + "..."
                parts.append(f"{k}={v_str}")
            if len(args) > 2:
                parts.append(f"...+{len(args)-2}")
            return f"{tool_name}({', '.join(parts)})"
        except Exception:
            return f"{tool_name}(...)"

    def answer(self, text: str):
        """Display the final answer — the visual focal point."""
        self.console.print(
            Panel(
                text,
                title="[bold green]✓ Answer[/]",
                border_style="green",
                padding=(1, 2),
            )
        )

    def error(self, text: str):
        """Display an error."""
        self.console.print(f"[red]Error: {text}[/]")

    def info(self, text: str):
        """Display info text."""
        self.console.print(f"[dim]{text}[/]")

    def print(self, text: str):
        """Print raw text."""
        self.console.print(text)

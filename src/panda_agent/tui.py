"""TUI renderer — Rich-based terminal UI for PandaAgent.

Display layers:
- Reasoning (thinking): dim italic, indented, smaller visual weight
- Actions (tool calls, results): normal weight, color-coded
- Answers (DONE): bold, boxed
- Evolve events: structured progress display
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt


class TUI:
    """Terminal UI renderer using Rich."""

    def __init__(self, color: str = "auto"):
        self.console = Console(force_terminal=color != "off")

    def banner(self):
        """Print the PandaAgent banner."""
        self.console.print(
            Panel(
                "[bold green]PandaAgent[/] — Self-Evolving Agent\n"
                "[dim]Type your task, or 'exit' to quit[/]",
                border_style="green",
                padding=(0, 2),
            )
        )

    def user_input(self) -> str:
        """Get user input."""
        return Prompt.ask("[bold cyan]You[/]")

    def reasoning(self, turn_label: str, text: str):
        """Display reasoning/thinking process in dim italic — visually smaller.

        This is the model's thinking, distinct from its actions.
        Uses dim + italic + indent to create visual hierarchy:
        thinking is background context, actions are foreground.
        """
        # Truncate very long reasoning for terminal readability
        display = text if len(text) <= 600 else text[:600] + " ..."
        # Indent each line for visual nesting
        lines = display.splitlines()
        for line in lines:
            self.console.print(f"    [dim italic]{line}[/]")

    def event(self, event_type: str, message: str):
        """Display a ReAct event."""
        if event_type == "llm_start":
            self.console.print(f"  [dim]{message}[/]")
        elif event_type == "llm_thinking":
            self.console.print(f"  [dim italic]{message}[/]")
        elif event_type == "llm_error":
            self.console.print(f"  [red]{message}[/]")
        elif event_type == "tool_call":
            self.console.print(f"  [yellow]>>>{message}[/]")
        elif event_type == "self_repair":
            self.console.print(f"  [bold magenta]{message}[/]")
        elif event_type == "tool_result":
            # Fold long tool results — show first 150 chars + [N more]
            if len(message) > 180:
                self.console.print(f"  [blue]{message[:150]} [dim]...[{len(message)-150} more][/][/]")
            else:
                self.console.print(f"  [blue]{message}[/]")
        elif event_type == "done":
            self.console.print("  [green]✓ Done[/]")
        elif event_type == "failed":
            self.console.print(
                Panel(message, title="[red]Failed[/]", border_style="red")
            )
        elif event_type == "max_turns":
            self.console.print(f"  [yellow]{message}[/]")
        elif event_type == "memory_used":
            self.console.print(f"  [cyan]{message}[/]")
        elif event_type == "doom_loop":
            self.console.print(f"  [bold red]{message}[/]")
        elif event_type == "memory_tidy":
            self.console.print(f"  [dim]{message}[/]")
        # === Evolve events ===
        elif event_type == "executor_start":
            self.console.print(f"\n[bold cyan]{message}[/]")
        elif event_type == "executor_tools":
            self.console.print(f"  [dim]🔧 {message}[/]")
        elif event_type == "executor_done":
            self.console.print(f"  [green]{message}[/]")
        elif event_type == "learner_start":
            self.console.print(f"  [dim]{message}[/]")
        elif event_type == "learner_done":
            self.console.print(f"  [cyan]{message}[/]")
        elif event_type == "learner_detail":
            self.console.print(f"  [dim italic]{message}[/]")
        elif event_type == "learner_trigger":
            self.console.print(f"  [bold yellow]{message}[/]")
        elif event_type == "evaluator_start":
            self.console.print(f"  [dim]{message}[/]")
        elif event_type == "evaluator_done":
            self.console.print(f"  [bold yellow]{message}[/]")
        elif event_type == "score_trend":
            self.console.print(f"  [dim]{message}[/]")
        elif event_type == "eval_issue":
            self.console.print(f"  [yellow]{message}[/]")
        elif event_type == "target_reached":
            self.console.print(f"  [bold green]{message}[/]")
        elif event_type == "stale_stop":
            self.console.print(f"  [yellow]{message}[/]")
        elif event_type == "improver_start":
            self.console.print(f"  [dim]{message}[/]")
        elif event_type == "improver_done":
            if "✓" in message:
                self.console.print(f"  [green]{message}[/]")
            else:
                self.console.print(f"  [red]{message}[/]")
        elif event_type == "improver_detail":
            self.console.print(f"  [dim italic]{message}[/]")
        elif event_type == "improver_error":
            self.console.print(f"  [red]{message}[/]")
        elif event_type == "round_end":
            self.console.print(f"  [dim]{message}[/]")
        elif event_type == "complete":
            self.console.print(f"\n[bold green]{message}[/]")
        else:
            self.console.print(f"  [dim]{event_type}: {message}[/]")

    def answer(self, text: str):
        """Display the final answer."""
        self.console.print(
            Panel(text, title="[bold green]Answer[/]", border_style="green")
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

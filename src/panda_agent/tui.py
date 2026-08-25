"""TUI renderer — Rich-based terminal UI for PandaAgent.

Provides a clean, colorful interface for the ReAct loop:
- User prompts
- LLM thinking indicators
- Tool call/result display
- Final answers
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.prompt import Prompt
from rich.text import Text
from rich.live import Live
from rich.spinner import Spinner


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
        elif event_type == "tool_result":
            self.console.print(f"  [blue]{message}[/]")
        elif event_type == "done":
            self.console.print(
                Panel(message, title="[green]Done[/]", border_style="green")
            )
        elif event_type == "failed":
            self.console.print(
                Panel(message, title="[red]Failed[/]", border_style="red")
            )
        elif event_type == "max_turns":
            self.console.print(f"  [yellow]{message}[/]")
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

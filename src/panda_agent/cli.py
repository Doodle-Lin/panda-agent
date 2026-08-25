"""CLI entry point for PandaAgent.

Usage:
    panda                    # Interactive TUI chat (default)
    panda chat -q "task"     # One-shot query
    panda config             # Show config
    panda config init        # Create default config
    panda config set K V     # Set a config value
    panda evolve -t "task"   # Run self-evolution loop
    panda memory search "q"  # Search graph memory
    panda memory add "text"  # Write to graph memory
    panda tools              # List available tools
"""

from __future__ import annotations

import argparse
import sys

from .config import load_config, save_config, config_path, default_config_yaml, Config
from .tui import TUI
from .tools import TOOLS, get_tool_descriptions
from .react import run_react
from .memory import MemoryClient


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="panda",
        description="PandaAgent — Self-Evolving Agent",
    )
    sub = parser.add_subparsers(dest="command")

    # chat (default)
    chat = sub.add_parser("chat", help="Interactive or one-shot chat")
    chat.add_argument("-q", "--query", type=str, default="", help="One-shot query")
    chat.add_argument("-m", "--model", type=str, default="", help="Override model")

    # config
    cfg = sub.add_parser("config", help="Configuration management")
    cfg.add_argument("action", nargs="?", default="show", choices=["show", "init", "set", "path"])
    cfg.add_argument("key", nargs="?", default="", help="Config key (for set)")
    cfg.add_argument("value", nargs="?", default="", help="Config value (for set)")

    # evolve (optional — forced training mode)
    ev = sub.add_parser("evolve", help="Forced evolution training (optional; learning happens automatically in chat)")
    ev.add_argument("-t", "--task", type=str, required=True, help="Task to train on")
    ev.add_argument("--target", type=float, default=90.0, help="Target score (default: 90)")
    ev.add_argument("--rounds", type=int, default=20, help="Max rounds (default: 20)")

    # memory
    mem = sub.add_parser("memory", help="Graph memory operations")
    mem.add_argument("action", choices=["search", "add", "stats"], help="Memory action")
    mem.add_argument("query", nargs="?", default="", help="Search query or content to add")
    mem.add_argument("--title", type=str, default="", help="Title for add")

    # tools
    sub.add_parser("tools", help="List available tools")

    args = parser.parse_args()

    # Default to chat if no command
    if not args.command:
        args.command = "chat"
        args.query = ""
        args.model = ""

    if args.command == "chat":
        cmd_chat(args)
    elif args.command == "config":
        cmd_config(args)
    elif args.command == "evolve":
        cmd_evolve(args)
    elif args.command == "memory":
        cmd_memory(args)
    elif args.command == "tools":
        cmd_tools()


def cmd_chat(args):
    """Handle chat command.

    Self-evolution is embedded in daily usage:
    - Every task completion triggers Learner (Level 2) in background
    - Recurring structural issues trigger Improver (Level 3) automatically
    - User sees a subtle learning indicator, not a separate command
    """
    from .orchestrator import Learner, Improver
    from .types import Task, ExecutionResult

    config = load_config()
    tui = TUI(color=config.display.color)
    tui.banner()

    if args.model:
        config.model.default = args.model

    memory = MemoryClient(url=config.memory.graph_url) if config.memory.enabled else None
    learner = Learner(config)
    improver = Improver(config)

    def on_event(et, msg):
        tui.event(et, msg)

    def on_reasoning(label, text):
        tui.reasoning(label, text)

    def _learn_after_task(user_input: str, result) -> None:
        """Silently learn from the task that just completed.

        Called after every task in chat mode. Runs Learner (Level 2)
        and triggers Improver (Level 3) if evidence is sufficient.
        """
        try:
            # Build ExecutionResult from ReActResult
            exec_result = ExecutionResult(
                tool_calls=result.tool_calls,
                success=result.success,
                error=result.error,
                trace=result.trace,
            )
            task = Task(instruction=user_input)

            # Level 2: Learn
            # Quick self-evaluation (don't call LLM for scoring — use heuristics)
            from .types import Evaluation
            if result.success and result.tool_calls:
                score = 80.0
            elif result.success:
                score = 60.0
            else:
                score = 20.0
            issues = []
            if not result.tool_calls and result.success:
                issues.append("Task completed without using any tools")
            if result.error:
                issues.append(f"Error: {result.error[:100]}")
            evaluation = Evaluation(score=score, issues=issues)

            learning = learner.learn(task, exec_result, evaluation)

            if learning.lessons:
                tui.event("learner_detail", f"💡 Learned: {learning.lessons[0][:100]}")

            # Level 3: Trigger improvement if enough evidence
            if learning.trigger_evolution:
                tui.event("learner_trigger", f"⚠ Auto-evolving: {learning.trigger_reason[:100]}")
                improvement = improver.improve(evaluation, evidence=learning.trigger_reason)
                if improvement.patched:
                    tui.event("improver_done", f"✓ Auto-patched: {improvement.explanation[:100]}")
                else:
                    tui.event("improver_detail", f"Patch attempt: {improvement.explanation[:100]}")
        except Exception as e:
            # Learning should never crash the chat
            pass

    if args.query:
        # One-shot mode
        result = run_react(args.query, config, on_event=on_event, on_reasoning=on_reasoning, memory=memory)
        if result.success:
            tui.answer(result.answer)
        else:
            tui.error(result.error or "Task failed")
        _learn_after_task(args.query, result)
        return

    # Interactive mode
    while True:
        try:
            user_input = tui.user_input()
            if user_input.lower() in ("exit", "quit", "/q"):
                tui.info("Goodbye!")
                break
            if not user_input.strip():
                continue

            result = run_react(user_input, config, on_event=on_event, on_reasoning=on_reasoning, memory=memory)
            if result.success:
                tui.answer(result.answer)
            else:
                tui.error(result.error or "Task failed")

            # Learn from every task — this is the self-evolution in daily use
            _learn_after_task(user_input, result)
        except (KeyboardInterrupt, EOFError):
            tui.info("\nGoodbye!")
            break


def cmd_config(args):
    """Handle config command."""
    if args.action == "init":
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(default_config_yaml(), encoding="utf-8")
        print(f"Config created at {path}")
        print("Edit it to set your API key and model.")
    elif args.action == "show":
        config = load_config()
        print(f"Config path: {config._path or config_path()}")
        print(f"Model: {config.model.default}")
        print(f"Base URL: {config.model.base_url}")
        print(f"API key: {'***' if config.model.api_key else '(not set)'}")
        print(f"Max tokens: {config.model.max_tokens}")
        print(f"Memory: {'enabled' if config.memory.enabled else 'disabled'}")
        print(f"  Graph URL: {config.memory.graph_url}")
        print(f"Evolution: target={config.evolution.target_score}, rounds={config.evolution.max_rounds}")
    elif args.action == "path":
        print(config_path())
    elif args.action == "set":
        if not args.key or not args.value:
            print("Usage: panda config set <key> <value>")
            print("Example: panda config set model.default gpt-4o")
            return
        config = load_config()
        # Navigate dot-notation key
        parts = args.key.split(".")
        obj = config
        for p in parts[:-1]:
            obj = getattr(obj, p)
        # Try to convert value
        val = args.value
        try:
            val = int(val)
        except ValueError:
            try:
                val = float(val)
            except ValueError:
                if val.lower() in ("true", "yes"):
                    val = True
                elif val.lower() in ("false", "no"):
                    val = False
        setattr(obj, parts[-1], val)
        save_config(config)
        print(f"Set {args.key} = {val}")


def cmd_evolve(args):
    """Handle evolve command."""
    from .orchestrator import run_evolution, Executor
    from .types import Task

    config = load_config()
    tui = TUI(color=config.display.color)
    tui.banner()

    def on_event(ev):
        tui.event(ev.type, ev.message)

    # Build executor with reasoning callback so evolve mode
    # also shows the model's thinking process.
    executor = Executor(config)

    # Monkey-patch executor.execute to forward reasoning to TUI
    _orig_execute = executor.execute
    def execute_with_reasoning(task):
        from .react import run_react
        result = run_react(
            task.instruction,
            config,
            on_event=None,
            on_reasoning=lambda label, text: tui.reasoning(label, text),
            memory=executor.memory,
        )
        from .types import ExecutionResult
        return ExecutionResult(
            output_path="",
            tool_calls=result.tool_calls,
            success=result.success,
            error=result.error,
            trace=result.trace,
        )
    executor.execute = execute_with_reasoning

    result = run_evolution(
        executor=executor,
        evaluator=None,
        learner=None,
        improver=None,
        task=Task(input_path="", instruction=args.task),
        target_score=args.target,
        max_rounds=args.rounds,
        on_event=on_event,
    )

    tui.print(f"\nRounds: {len(result.rounds)}, Score: {result.final_score}, "
              f"Patches: {result.total_patches}, Lessons: {result.total_lessons}")


def cmd_memory(args):
    """Handle memory command."""
    config = load_config()
    client = MemoryClient(url=config.memory.graph_url)

    if args.action == "search":
        if not args.query:
            print("Usage: panda memory search <query>")
            return
        results = client.retrieve(args.query, top_k=10)
        if not results:
            print("No results (graph memory may not be running)")
            return
        for r in results:
            score = r.get("score", 0)
            content = r.get("content", "")[:200]
            print(f"  [{score:.2f}] {content}")
    elif args.action == "add":
        if not args.query:
            print("Usage: panda memory add <content>")
            return
        result = client.write(args.query, title=args.title)
        print(f"Written: {result}")
    elif args.action == "stats":
        stats = client.stats()
        print(stats)


def cmd_tools():
    """Handle tools command."""
    print("Available tools:")
    print(get_tool_descriptions())


if __name__ == "__main__":
    main()

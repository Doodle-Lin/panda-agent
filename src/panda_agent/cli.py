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
import os

from .config import load_config, save_config, config_path, default_config_yaml
from .tui import TUI
from .tools import get_tool_descriptions
from .react import run_react
from .memory import MemoryClient


# ---------------------------------------------------------------------------
# Slash command parsing (/memory, /help, /stats, /clear)
# ---------------------------------------------------------------------------

def _is_slash_command(text: str) -> bool:
    """Check if input starts with / (slash command)."""
    return text.strip().startswith("/")


def _parse_slash_command(text: str) -> tuple[str, str]:
    """Parse '/cmd args' → ('cmd', 'args'). Returns (command, rest)."""
    stripped = text.strip()[1:]  # remove leading /
    parts = stripped.split(None, 1)
    cmd = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""
    # Normalize aliases
    if cmd in ("mem",):
        cmd = "memory"
    return cmd, rest


def _cmd_memory_tidy(tui: TUI, memory: MemoryClient, llm_callback) -> None:
    """Review and refine all memory nodes using LLM.

    For each node, LLM chooses one of:
    - refine: extract valuable essence, replace verbose content with concise knowledge
    - merge: combine with another node (content is duplicated or overlapping)
    - delete: noise/trivial/outdated, no value
    - keep: already good as-is

    Called when user types /memory in chat.
    """
    if memory is None or not memory.is_available():
        tui.info("Memory not available")
        return

    nodes = memory.list_all()
    if not nodes:
        tui.info("Memory is empty — nothing to tidy")
        return

    tui.info(f"Reviewing {len(nodes)} memory nodes...")

    import json
    # Send FULL content (not truncated) — LLM needs to see everything to refine
    node_summary = []
    for n in nodes:
        node_summary.append({
            "id": n["id"],
            "title": n["title"],
            "content": n["content"],  # full content, not truncated
            "source": n["source"],
            "node_type": n["node_type"],
        })

    review_prompt = (
        "你是一个记忆整理专家。审查以下图式记忆节点，对每个节点选择一个操作：\n\n"
        "操作类型：\n"
        "- refine: 内容冗长/是对话记录/包含大量叙述 → 提取核心知识（技术栈、关键参数、定义、原理、经验教训），"
        "用简洁的结构化文本替换原内容。保留所有具体数字、路径、命令、参数。\n"
        "- merge: 与另一个节点内容重复或高度重叠 → 合并为一个，指定保留的id和合并后的内容。\n"
        "- delete: 噪声/琐碎/过时/无价值 → 删除。\n"
        "- keep: 已经简洁有用 → 保持不变。\n\n"
        "refine 示例：\n"
        "  原始: '用户问了个问题，我试了好几种方法，先用了ls，然后用了dir，发现是Windows系统...' (300字对话记录)\n"
        "  提炼: 'panda agent 在 Windows 上使用 dir 而非 ls。OS 检测: echo %OS% 返回 Windows_NT。' (30字知识)\n\n"
        "返回 JSON 数组:\n"
        '[{"id": "...", "action": "refine"|"merge"|"delete"|"keep", "new_content": "...(refine/merge时必填)", "merge_into": "...(merge时必填，目标id)", "reason": "..."}]\n\n'
        "关键原则：\n"
        "1. refine 时保留所有具体数字、路径、命令、参数、错误信息\n"
        "2. 去掉对话叙述（'用户问了'、'我尝试了'、'然后发现'），只留知识点\n"
        "3. 技术栈/环境信息 → 用 key: value 格式\n"
        "4. 经验教训 → 用因果句式（'X 导致 Y，应该 Z'）\n"
        "5. 定义/原理 → 用简洁陈述句\n\n"
        f"待审查节点 ({len(node_summary)} 个):\n{json.dumps(node_summary, ensure_ascii=False, indent=2)}"
    )

    # Call LLM for review
    from .llm import call_llm_detailed
    config = load_config()
    messages = [
        {"role": "system", "content": "你是记忆整理专家。只返回 JSON 数组，不要其他文字。"},
        {"role": "user", "content": review_prompt},
    ]

    try:
        resp = call_llm_detailed(messages, config.model)
        response_text = resp.content or resp.reasoning or ""
        if not response_text:
            tui.info("LLM returned no response — skipping tidy")
            return

        # Parse JSON array from response
        import re
        json_match = re.search(r"\[.*\]", response_text, re.DOTALL)
        if not json_match:
            tui.info("Could not parse LLM response — skipping tidy")
            return

        decisions = json.loads(json_match.group(0))
        kept = 0
        refined = 0
        merged = 0
        deleted = 0

        for d in decisions:
            nid = d.get("id", "")
            action = d.get("action", "keep")
            reason = d.get("reason", "")
            new_content = d.get("new_content", "")

            if action == "refine" and nid and new_content:
                # Update node content with refined version
                current = next((node for node in nodes if node["id"] == nid), None)
                if current and memory.update_by_id(nid, content=new_content):
                    old_len = len(current.get("content", ""))
                    new_len = len(new_content)
                    refined += 1
                    tui.event("memory_tidy",
                              f"  ✏ Refined: {nid[:8]}... ({old_len}→{new_len} chars) — {reason[:60]}")

            elif action == "merge" and nid:
                merge_into = d.get("merge_into", "")
                if merge_into and merge_into != nid:
                    # Append content to target, then delete source
                    source_node = next((node for node in nodes if node["id"] == nid), None)
                    target_node = next(
                        (node for node in nodes if node["id"] == merge_into), None
                    )
                    if source_node and target_node:
                        target_content = target_node.get("content", "")
                        merged_content = target_content + "\n\n" + new_content if new_content else target_content
                        if (
                            memory.update_by_id(merge_into, content=merged_content)
                            and memory.delete_by_id(nid)
                        ):
                            merged += 1
                            tui.event("memory_tidy",
                                      f"  🔗 Merged: {nid[:8]}... → {merge_into[:8]}... — {reason[:60]}")

            elif action == "delete" and nid:
                if memory.delete_by_id(nid):
                    deleted += 1
                    tui.event("memory_tidy", f"  🗑 Deleted: {nid[:8]}... — {reason[:60]}")

            else:
                kept += 1

        tui.info(f"Done: {kept} kept, {refined} refined, {merged} merged, {deleted} deleted. "
                 f"Memory now has {len(nodes) - deleted} nodes.")

    except Exception as e:
        tui.info(f"Memory tidy failed: {e}")


def _handle_slash_command(text: str, tui: TUI, memory: MemoryClient, config) -> bool:
    """Handle slash commands in chat mode. Returns True if handled."""

    cmd, rest = _parse_slash_command(text)

    if cmd in ("memory", "mem"):
        _cmd_memory_tidy(tui, memory, None)
        return True

    if cmd == "stats":
        if memory and memory.is_available():
            stats = memory.stats()
            tui.info(f"Memory: {stats.get('node_count', 0)} nodes, {stats.get('edge_count', 0)} edges")
        else:
            tui.info("Memory not available")
        return True

    if cmd == "help":
        tui.info("Commands: /memory (整理记忆:提炼/合并/删除), /stats (记忆统计), /history (查看历史), /help, /clear, exit")
        return True

    if cmd == "history":
        panda_home = os.environ.get("PANDA_HOME", os.path.expanduser("~/.panda"))
        sessions_dir = os.path.join(panda_home, "sessions")
        if not os.path.exists(sessions_dir):
            tui.info("No session history yet")
            return True
        import json as _json
        files = sorted(os.listdir(sessions_dir), reverse=True)[:5]
        for sf in files:
            path = os.path.join(sessions_dir, sf)
            try:
                lines = open(path, encoding="utf-8").readlines()
                tui.info(f"  {sf} ({len(lines)} messages)")
                for line in lines[-3:]:  # last 3 messages
                    entry = _json.loads(line)
                    user = entry.get("user", "")[:60]
                    success = "OK" if entry.get("success") else "FAIL"
                    tui.info(f"    [{success}] {user}")
            except Exception:
                pass
        return True

    if cmd == "clear":
        os.system("cls" if os.name == "nt" else "clear")
        return True

    # Unknown slash command
    tui.info(f"Unknown command: /{cmd}. Type /help for available commands.")
    return True


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

    # history
    sub.add_parser("history", help="View evolution history")

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
    elif args.command == "history":
        cmd_history(args)
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

    memory = MemoryClient.from_config(config.memory) if config.memory.enabled else None
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
                improvement = improver.improve(evaluation)
                if improvement.patched:
                    tui.event("improver_done", f"✓ Auto-patched: {improvement.explanation[:100]}")
                else:
                    tui.event("improver_detail", f"Patch attempt: {improvement.explanation[:100]}")
        except Exception:
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
    import os
    import json
    from datetime import datetime

    # Session history — persisted to ~/.panda/sessions/
    panda_home = os.environ.get("PANDA_HOME", os.path.expanduser("~/.panda"))
    sessions_dir = os.path.join(panda_home, "sessions")
    os.makedirs(sessions_dir, exist_ok=True)
    session_file = os.path.join(sessions_dir, f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl")
    session_entries = []

    def _save_session(user_msg: str, answer: str, success: bool, result_obj=None):
        entry = {
            "time": datetime.now().isoformat(),
            "user": user_msg,
            "answer": answer[:500],
            "success": success,
            "turns": getattr(result_obj, "turns", 0),
            "tool_calls": len(getattr(result_obj, "tool_calls", [])),
        }
        session_entries.append(entry)
        try:
            with open(session_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    while True:
        try:
            user_input = tui.user_input()
            if user_input.lower() in ("exit", "quit", "/q"):
                tui.info("Goodbye!")
                break
            if not user_input.strip():
                continue

            # Handle slash commands (/memory, /stats, /help, /clear)
            if _is_slash_command(user_input):
                _handle_slash_command(user_input, tui, memory, config)
                continue

            result = run_react(user_input, config, on_event=on_event, on_reasoning=on_reasoning, memory=memory)
            if result.success:
                tui.answer(result.answer)
            else:
                tui.error(result.error or "Task failed")

            # Save to session history
            _save_session(user_input, result.answer or result.error or "", result.success, result)

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
    client = MemoryClient.from_config(config.memory)

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


def cmd_history(args):
    """Handle history command — view evolution audit trail."""
    import json
    panda_home = os.environ.get("PANDA_HOME", os.path.expanduser("~/.panda"))
    history_file = os.path.join(panda_home, "evolution_history.jsonl")

    if not os.path.exists(history_file):
        print("No evolution history found. Run 'panda evolve -t <task>' first.")
        return

    print(f"{'Round':>5}  {'Score':>5}  {'Bench':>6}  {'Patched':>12}  {'Status':>8}  Reason")
    print("-" * 80)

    with open(history_file, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                rnd = entry.get("round", "?")
                score = entry.get("score", 0)
                bench_delta = entry.get("benchmark_delta", "")
                patched = entry.get("patched_file", "-")
                status = entry.get("accepted", False)
                status_str = "kept" if status else "reject"
                reason = entry.get("reason", "")[:40]
                bench_str = f"{bench_delta:+.1f}" if isinstance(bench_delta, (int, float)) else str(bench_delta)
                print(f"{rnd:>5}  {score:>5.0f}  {bench_str:>6}  {patched:>12}  {status_str:>8}  {reason}")
            except (json.JSONDecodeError, KeyError):
                continue


if __name__ == "__main__":
    main()

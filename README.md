# PandaAgent — Self-Evolving AI Agent

An AI agent that gets better with use. Three-layer self-evolution architecture
embedded in daily tasks — not a separate training mode.

## What makes it different

```
Level 1 (runtime):     Tool fails → auto-repair (path expansion, encoding fix, etc.)
                       ↓ visible: "↳ Self-repair: expanded path"
Level 2 (post-task):   Learner analyzes execution → extracts structured lessons
                       → writes to graph memory → error patterns persisted
                       ↓ visible: "💡 Learned: On Windows, use dir %USERPROFILE%\Desktop"
Level 3 (structural):  Same structural issue ×3 → Improver auto-patches source code
                       → pytest validates → behavior check → score must improve or rollback
                       ↓ visible: "⚠ Auto-evolving: Structural issue seen 3 times"
```

Self-evolution is **embedded in daily use**, not a separate command.
Every task completion triggers Learner. Evidence accumulates across restarts.

## Features

- **Native function calling** — OpenAI-compatible tool_calls, no fragile JSON parsing
- **Embedded graph memory** — NetworkX + sentence-transformers + PageRank, no external service
- **Context compression** — old tool results truncated when context grows large
- **Doom loop detection** — 3 identical tool calls → warn → fail
- **Soft limit** — MAX_STEPS_PROMPT lets agent summarize instead of hard cutoff
- **Multi-model fallback** — primary model fails → fallback model auto-retry
- **Session history** — all conversations saved to `~/.panda/sessions/`
- **Slash commands** — `/memory` (tidy graph memory), `/stats`, `/history`, `/help`

## Quick Start

```bash
pip install -e .
panda                    # Interactive TUI chat
panda chat -q "task"     # One-shot query
panda config            # Show config
panda tools             # List available tools
```

## Configuration

`~/.panda/config.yaml`:

```yaml
model:
  default: GLM52RJPT          # primary model
  fallback: GLM-5.2           # fallback if primary fails
  base_url: https://your-api/v1
  api_key: your-key
  max_tokens: 8192

agent:
  max_turns: 10
  max_retries: 3

memory:
  enabled: true
  auto_write: true            # auto-write valuable experiences to graph memory

evolution:
  target_score: 90
  max_rounds: 3
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  CLI / TUI (cli.py, tui.py)                              │
│  ┌─ /memory  ── /stats  ── /history  ── /help ── /clear │
│  └─ session history → ~/.panda/sessions/                │
├─────────────────────────────────────────────────────────┤
│  ReAct Loop (react.py)                                   │
│  ┌─ native FC (tool_calls from API)                     │
│  │  └─ text fallback (TOOL_CALL: {json} parsing)        │
│  ├─ Level 1: self-repair on tool error                  │
│  ├─ doom loop detection (3 identical calls → fail)      │
│  ├─ context compression (truncate old tool results)     │
│  └─ soft limit (MAX_STEPS_PROMPT instead of hard cutoff) │
├─────────────────────────────────────────────────────────┤
│  3-Agent Evolution (orchestrator.py)                     │
│  ┌─ Executor  → runs task via ReAct loop               │
│  ├─ Evaluator → LLM scores execution 0-100             │
│  ├─ Learner   → extracts lessons → graph memory        │
│  │              tracks error patterns (persisted)       │
│  └─ Improver  → patches source code (≥3 evidence)      │
│                gate: pytest + behavior + score ↑ or     │
│                rollback                                 │
├─────────────────────────────────────────────────────────┤
│  Embedded Graph Memory (memory.py)                      │
│  ┌─ GraphEngine (NetworkX + embeddings + PageRank)     │
│  ├─ retrieve_context() → injected into system prompt   │
│  └─ /memory command → LLM refines/merges/deletes       │
└─────────────────────────────────────────────────────────┘
```

## Available Tools

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents |
| `write_file` | Write/create a file |
| `search_files` | Search file contents with regex |
| `list_files` | List directory contents |
| `run_command` | Execute shell command |
| `patch_file` | Find-and-replace with fuzzy matching |
| `memory_retrieve` | Retrieve knowledge from graph memory |
| `memory_write` | Write knowledge to graph memory |

## Testing

```bash
# Unit tests (no API calls)
pytest tests/ -m "not slow"

# E2E tests (real LLM, needs API key)
pytest tests/ -m slow

# All tests
pytest tests/
```

## Project Structure

```
src/panda_agent/
  brain.py          # System prompt + task complexity estimation
  react.py          # ReAct loop: native FC, self-repair, doom loop, compression
  llm.py            # Streaming LLM caller + multi-model fallback
  tools.py          # 8 built-in tools with OpenAI-compatible schemas
  memory.py         # Embedded graph memory (GraphEngine wrapper)
  orchestrator.py   # 3-agent evolution: Executor, Evaluator, Learner, Improver
  cli.py            # CLI entry point + slash commands
  tui.py            # Rich-based terminal UI
  config.py         # YAML config loader
  types.py          # Dataclasses: ExecutionTrace, LLMResponse, etc.
```

## Design Principles

- **Evolution embedded in daily use** — no separate training mode
- **Native function calling first** — text protocol as fallback only
- **Safety first** — failed patches always reverted, pytest must pass
- **Model-agnostic** — works with reasoning models (GLM52RJPT) and standard models
- **No external services** — graph memory embedded in-process

## License

MIT

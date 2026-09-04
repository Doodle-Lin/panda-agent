# PandaAgent — Self-Evolving Agent Framework

## WHAT

A CLI agent that can execute tasks, remember across sessions, and
**evolve its own brain** — not just its tools, but its prompts,
decision logic, and strategies — through a 3-agent self-supervision loop.

```
                    User Task
                        |
                        v
+----------+    +-----------+    +----------+
| Executor  |--- >| Evaluator  |--- >| Improver  |
| (执行)    |    | (评估)     |    | (改进)    |
+----------+    +-----------+    +----------+
     ^                               |
     |    improved tools AND brain    |
     +-------------------------------+
```

## WHY

Existing agents (Aider, OpenCode, Hermes) are powerful but static:
their prompts, tool-selection logic, and strategies are hand-written
once and never improved by the agent itself. PandaAgent closes this gap.

The 3-agent loop:
1. **Executor** runs the task using tools + brain (prompt/strategy).
2. **Evaluator** inspects the result, scores it, reports issues.
3. **Improver** patches **tool code** AND **brain code** (prompts,
   decision rules, strategies), runs tests, keeps/reverts.

## Architecture

### CLI Layer

```
panda chat                    # Interactive TUI chat (default)
panda chat -q "task"          # One-shot query
panda config                  # Show/edit config
panda config set model.name X # Set config value
panda evolve --task "..."     # Run self-evolution loop
panda memory search "query"   # Search graph memory
panda memory add "knowledge"  # Write to graph memory
panda tools list              # List available tools
```

### Config (YAML)

```yaml
# ~/.panda/config.yaml
model:
  default: GLM-5.2           # Brain model (non-reasoning for tool calls)
  code_model: GLM52RJPT      # Code generation (reasoning model)
  vlm_model: Qwen3-VL-235B   # Vision model (optional)
  base_url: https://your-api/v1
  api_key: ${PANDA_API_KEY}   # From env, never hardcoded
  max_tokens: 8192

agent:
  max_turns: 10               # Max ReAct iterations per task
  max_retries: 3              # Improver retry attempts

memory:
  enabled: true
  graph_url: embedded://         # Embedded SQLite graph (no external service)
  auto_write: true            # Auto-save learned knowledge

evolution:
  target_score: 90
  max_rounds: 3
  improve_brain: true          # Allow Improver to edit brain.py
  improve_tools: true          # Allow Improver to edit tools.py

display:
  tui: true                    # Rich TUI rendering
  show_reasoning: false        # Show LLM reasoning (if available)
  color: auto
```

### Module Structure

```
panda-agent/
├── pyproject.toml
├── README.md
├── specs/
│   └── spec.md
├── src/
│   └── panda_agent/
│       ├── __init__.py
│       ├── cli.py              # CLI entry point (argparse)
│       ├── config.py           # YAML config loader
│       ├── llm.py              # Streaming LLM caller (reasoning fallback)
│       ├── brain.py            # System prompt + decision logic (evolvable)
│       ├── tools.py            # Built-in tools (evolvable)
│       ├── react.py            # ReAct agent loop
│       ├── tui.py              # Rich-based TUI renderer
│       ├── memory.py           # Graph memory client (retrieve + write)
│       ├── executor.py         # Executor agent
│       ├── evaluator.py        # Evaluator agent
│       ├── improver.py         # Improver agent (patches tools + brain)
│       ├── orchestrator.py     # Evolution loop driver
│       └── types.py            # Shared data types
├── tests/
│   ├── test_config.py
│   ├── test_llm.py
│   ├── test_react.py
│   ├── test_tools.py
│   ├── test_memory.py
│   ├── test_orchestrator.py
│   └── test_improver.py
└── plugins/
    └── photo_edit/             # Example plugin
```

### Brain — The Evolvable "Mind"

```python
# brain.py — this file is itself the evolution target
SYSTEM_PROMPT = """You are PandaAgent, a self-evolving AI assistant.
You solve tasks by calling tools. Think step by step.
Output TOOL_CALL: {json} to call a tool, or DONE: text to finish."""

def select_tool(task: str, tools: list, history: list) -> dict:
    """Decide which tool to call next. This logic can be evolved."""
    # Default: let the LLM decide via ReAct
    return {"strategy": "react"}
```

The Improver can patch `brain.py` to improve:
- The system prompt (better instructions → better tool selection)
- Decision functions (smarter routing logic)
- Strategy parameters (temperature, max_turns, retry rules)

### Tools — Built-in

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents |
| `write_file` | Write/create files |
| `search_files` | Search file contents (regex) |
| `patch_file` | Find-and-replace in files |
| `run_command` | Execute shell commands |
| `list_files` | List directory contents |
| `web_search` | Search the web (optional) |

### Memory — Graph Integration

```python
# Before acting: retrieve relevant knowledge
knowledge = memory.retrieve("how to deploy vLLM")
# → [{"content": "vLLM needs CUDA 12+", "score": 0.85}, ...]

# After learning: write new knowledge
memory.write("GLM52RJPT needs max_tokens >= 16384 for reasoning")
# → auto-links to related nodes in the graph
```

### ReAct Loop

```
1. Build system prompt (from brain.py) + memory context
2. Call LLM with task + tool descriptions + history
3. Parse response for TOOL_CALL or DONE
4. If TOOL_CALL: execute tool, append result, goto 2
5. If DONE: return result
6. If max_turns exceeded: stop
```

### Self-Evolution Loop

```
for round in 1..max_rounds:
    1. Executor: run task with current brain + tools
    2. Evaluator: score the result (0-100) + issues + root_cause
    3. If score >= target: stop, success
    4. Improver:
       a. Read evaluation + relevant source (tools.py and/or brain.py)
       b. Generate patch via code_model (GLM52RJPT)
       c. Apply patch, run tests
       d. If tests pass: keep; if fail: feed error back, retry
       e. If all retries fail: revert
    5. Repeat with improved brain/tools
```

## Design Principles

1. **Brain is evolvable** — Improver can edit brain.py, not just tools.py
2. **Code is the target** — patches source files, tests validate
3. **Error-feedback retry** — failed patches feed errors to LLM
4. **Safety first** — failed patches always reverted
5. **Model-agnostic** — any LLM, reasoning models supported
6. **Memory is associative** — graph-based, not just vector search
7. **CLI first** — TUI is an enhancement, not a dependency
8. **Plugin architecture** — domain-specific logic is injectable

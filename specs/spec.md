# EvoAgent — Generic 3-Agent Self-Evolution Framework

## WHAT

A framework where three agents form a closed loop to iteratively improve
their own tool code through real-world execution, evaluation, and
LLM-driven code patching.

```
┌──────────┐    ┌───────────┐    ┌──────────┐
│ Executor  │───→│ Evaluator  │───→│ Improver  │
│ (执行)    │    │ (评估)     │    │ (改进)    │
└──────────┘    └───────────┘    └──────────┘
     ↑                                │
     └──────── improved tools ────────┘
```

## WHY

LLM-based agents are fragile: their tool implementations are written once
and never improved. When a tool produces suboptimal results, a human must
manually debug and patch it. EvoAgent automates this loop:

1. **Executor** runs tools to accomplish a task.
2. **Evaluator** inspects the result, scores it, and reports issues with
   root-cause analysis and suggested changes.
3. **Improver** reads the evaluation, generates a code patch for the
   responsible tool, runs tests, and keeps the patch only if tests pass.
4. The loop repeats with improved tools until the target score is reached
   or max rounds are exhausted.

## Core Abstractions

### Executor
```python
class Executor(Protocol):
    def execute(self, task: Task) -> ExecutionResult
```
- Runs a task using available tools.
- Returns the output artifact + metadata (which tools were called, params).

### Evaluator
```python
class Evaluator(Protocol):
    def evaluate(self, task: Task, result: ExecutionResult) -> Evaluation
```
- Inspects original + result.
- Returns: score (0-100), issues, root_cause, suggested_changes.

### Improver
```python
class Improver(Protocol):
    def improve(self, evaluation: Evaluation) -> ImprovementResult
```
- Reads evaluation + relevant source code.
- Generates patch, applies, runs tests.
- Returns: patched (bool), reverted (bool), diff, test_result.

### Orchestrator
```python
def run_evolution(
    executor, evaluator, improver,
    task: Task,
    target_score: float = 95.0,
    max_rounds: int = 3,
) -> EvolutionResult
```
- Drives the loop: execute → evaluate → improve → repeat.
- Stops when target_score reached or max_rounds hit.
- Emits events for UI/logging.

## Design Principles

1. **Plugins, not hardcoding** — Executor/Evaluator/Improver are injectable.
   The framework knows nothing about image editing.
2. **Code is the evolution target** — Improver patches *tool source files*,
   not prompts or parameters. Tests are the safety net.
3. **Error-feedback retry** — When a patch fails tests, the error message
   goes back to the LLM for a smarter retry (not a blind retry).
4. **Safety first** — Failed patches are always reverted. The loop never
   breaks working code.
5. **Model-agnostic** — Any LLM can power the Improver. Reasoning models
   (e.g. GLM52RJPT) produce better patches but need reasoning_content
   fallback.

## Framework Structure

```
evo-agent/
├── pyproject.toml
├── README.md
├── specs/
│   └── spec.md              ← this file
├── src/
│   └── evo_agent/
│       ├── __init__.py
│       ├── types.py         ← Task, ExecutionResult, Evaluation, etc.
│       ├── executor.py      ← Executor protocol + base impl
│       ├── evaluator.py     ← Evaluator protocol + base impl
│       ├── improver.py      ← Improver protocol + base impl
│       ├── orchestrator.py  ← run_evolution() loop
│       └── llm.py           ← streaming LLM caller with reasoning fallback
├── plugins/
│   └── photo_edit/          ← first concrete plugin
│       ├── __init__.py
│       ├── executor.py
│       ├── evaluator.py
│       └── improver.py
└── tests/
    ├── test_types.py
    ├── test_orchestrator.py
    ├── test_improver.py
    └── plugins/
        └── test_photo_edit.py
```

## Convergence Criteria

- `target_score` reached → stop, success.
- `max_rounds` exhausted → stop, report best score.
- All improvement attempts in a round fail → stop, no progress.

## Event System

The Orchestrator emits events for real-time monitoring:
```python
@dataclass
class Event:
    type: str        # "executor_start", "qa_done", "improver_done", ...
    message: str
    round: int
    data: dict
```

## Non-Goals (v1)

- Not evolving prompts or agent logic (only tool code).
- Not cross-session memory (each run starts fresh).
- Not multi-task optimization (one task per run).

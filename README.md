# 🐼 PandaAgent

**A self-evolving agent framework where the agent rewrites its own code — and proves the rewrite actually helped.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#project-status)

Most "self-improving agent" projects let an LLM patch its own source and call it
evolution. The hard part isn't generating the patch — it's **knowing whether the
patch made things better or worse**. PandaAgent is built around that question.

```
                    ┌─────────────────────────────────────┐
                    │         EVOLUTION LOOP              │
                    └─────────────────────────────────────┘

   ┌──────────┐      ┌───────────┐      ┌──────────┐
   │ Executor │─────>│ Evaluator │─────>│ Improver │
   │  ReAct   │      │  score/   │      │  patch   │
   │  + tools │      │  diagnose │      │  + verify│
   └──────────┘      └───────────┘      └──────────┘
        ▲                                     │
        │                                      │ patches
        │            ┌──────────────┐          │
        └────────────│ tools.py     │<─────────┘
          re-run     │ brain.py     │
          to verify  └──────────────┘
                     "hands" & "mind"
```

Three agents form a closed loop:

| Agent | Role | What it touches |
|---|---|---|
| **Executor** | Runs the task in a ReAct loop | Reads `brain.py` (prompt/strategy) + `tools.py` (capabilities) |
| **Evaluator** | Scores the result 0–100, diagnoses root cause | Read-only |
| **Improver** | Generates a patch, verifies it, keeps or reverts | Writes `tools.py` / `brain.py` |

What makes this different: the agent evolves **both its "hands" (tools) and its
"mind" (system prompt + decision logic)**, and every patch must survive a
verification gate before it's kept.

---

## Project Status

**Alpha — working prototype, not production-ready.** Being upfront about this
because "self-evolving" invites inflated expectations.

| Component | Status | Notes |
|---|---|---|
| ReAct loop + tool execution | ✅ Working | 6 built-in tools |
| 3-agent evolution loop | ✅ Working | Executor → Evaluator → Improver |
| Patch generation + revert | ✅ Working | Auto-backup, revert on test failure |
| Brain evolution | ✅ Working | Patches prompt + decision logic |
| CLI + TUI | ✅ Working | `panda run`, `panda evolve` |
| Graph memory | 🟡 Optional | Requires external server; degrades gracefully |
| **Patch verification** | 🔴 **Weak** | Unit tests only — does not re-run the task. See [Known Limitations](#known-limitations) |
| **Sandboxing** | 🔴 **None** | `run_command` executes arbitrary shell. See [Security](#security) |
| Test coverage of the loop | 🔴 Missing | `orchestrator.py` (420 lines) has no tests |

Read [Known Limitations](#known-limitations) before running this on anything
you care about.

---

## Why This Exists

The interesting claim in self-evolving agents is *"the agent gets better at its
job over time."* Almost every implementation fails to substantiate it, because
they conflate two very different things:

1. **The patch is syntactically valid and tests pass** ← easy, most projects stop here
2. **The agent actually performs better on the task** ← hard, this is what matters

PandaAgent's design goal is to close gap #2. The current implementation does #1
and has the architecture for #2 (see [Roadmap](#roadmap)) — the honest state of
things is that the verification gate is the project's main open problem, and it's
being worked on in the open rather than papered over.

---

## Quick Start

### Install

```bash
git clone https://github.com/Doodle-Lin/panda-agent.git
cd panda-agent
pip install -e ".[test]"
pytest tests/          # verify install
```

### Configure

```bash
panda config init      # writes ~/.panda/config.yaml
```

```yaml
# ~/.panda/config.yaml
model:
  default: "your-model-name"
  base_url: "http://localhost:8000/v1"   # any OpenAI-compatible endpoint
  api_key: ${PANDA_API_KEY}              # env var expansion supported
  max_tokens: 8192

agent:
  max_turns: 10
  max_retries: 3

memory:
  enabled: false                          # graph memory is opt-in
  graph_url: "http://127.0.0.1:9121"

evolution:
  improve_tools: true
  improve_brain: true
```

```bash
export PANDA_API_KEY="sk-..."
panda config show      # api_key is masked in output
```

Works with any OpenAI-compatible endpoint: vLLM, SGLang, Ollama, LM Studio,
OpenAI, DeepSeek, Qwen. Reasoning models are handled via `reasoning_content`
fallback.

### Run a task (no evolution)

```bash
panda run "list all Python files in src/ and report the largest one"
```

### Run the evolution loop

```bash
panda evolve "search the codebase for TODO comments and summarize them" \
    --target-score 90 \
    --max-rounds 3
```

What happens each round:

```
Round 1  Executor  → runs the task via ReAct
         Evaluator → scores 65/100, root cause: "search_files has no
                     line-number output, agent couldn't cite locations"
         Improver  → patches _tool_search_files in tools.py
                     runs pytest → passed → patch kept

Round 2  Executor  → re-runs with the patched tool
         Evaluator → scores 92/100 → target reached, stop
```

### Python API

```python
from panda_agent import run_evolution, Task

result = run_evolution(
    executor=None,      # None = use built-in defaults
    evaluator=None,
    improver=None,
    task=Task(instruction="refactor the config loader to support env vars"),
    target_score=90.0,
    max_rounds=3,
)

print(f"final score: {result.final_score}")
print(f"patches kept: {result.total_patches}")
for r in result.rounds:
    print(f"  round {r.round_num}: {r.evaluation.score:.0f} — {r.evaluation.root_cause}")
```

All three agents are injectable — pass your own `Executor` / `Evaluator` /
`Improver` to target a different domain (see [Extending](#extending)).

---

## How Evolution Actually Works

Concretely, per round:

**1. Execute** — `Executor` runs a ReAct loop: LLM sees the system prompt from
`brain.py` plus tool descriptions from `tools.py`, emits
`TOOL_CALL: {"name": ..., "args": {...}}`, gets the result appended to the
conversation, repeats until `DONE:` / `FAILED:` or turn limit.

**2. Evaluate** — `Evaluator` sends the task + tool call trace to an LLM and asks
for JSON: `{"score": 0-100, "issues": [...], "root_cause": "...", "suggested_changes": "..."}`.

**3. Improve** — `Improver`:
- Backs up the target file (`tools.py` or `brain.py`) to `.py.bak`
- Extracts only the functions relevant to the evaluation (keyword matching) to
  keep the prompt small
- Asks a code-capable LLM for a patch in `PATCH_START ... PATCH_END` format
- Applies the patch by replacing the function definition
- **Runs `pytest tests/`** — if it passes, keep; if not, restore from backup and
  retry with the test error fed back to the LLM (up to `max_retries`)

**4. Loop** — until `score >= target_score` or `max_rounds` exhausted.

### What's evolvable

`tools.py` — the agent's **hands**:

| Tool | Purpose |
|---|---|
| `read_file` | Read file contents |
| `write_file` | Write/create files |
| `search_files` | Regex search across files |
| `list_files` | Directory listing |
| `patch_file` | Find-and-replace in a file |
| `run_command` | Execute a shell command ⚠️ [see Security](#security) |
| `memory_retrieve` | Query graph memory (if enabled) |

`brain.py` — the agent's **mind**:

- `SYSTEM_PROMPT` — the core instruction set
- `should_retry(tool, error, count, max)` — retry policy
- `max_turns_for_task(task)` — complexity-based turn budgeting

Function signatures are kept stable so the Improver can rewrite bodies without
breaking callers.

---

## Graph Memory

Optional associative memory backed by an external graph server. Uses embedding
similarity plus Personalized PageRank diffusion, so retrieval surfaces
*related* knowledge, not just lexically similar text.

```yaml
memory:
  enabled: true
  graph_url: "http://127.0.0.1:9121"
  auto_write: true      # persist task outcomes automatically
```

```python
from panda_agent.memory import MemoryClient

mem = MemoryClient()
if mem.is_available():
    mem.write("vLLM needs --enforce-eager for older GPUs", title="vllm-tip")
    ctx = mem.retrieve_context("why is vllm slow to start", top_k=3)
    # → formatted markdown block for LLM injection
```

**Design note:** every method degrades to a no-op if the server is down —
`retrieve` returns `[]`, `write` returns `{"error": ...}`, nothing raises. Memory
is an enhancement, never a hard dependency. This is deliberate: an agent
framework that dies because a side-car service is unreachable is not usable.

**Current limitation:** the graph server is a separate project and not bundled
here, which makes this feature hard to try. Bundling a minimal
reference implementation is on the [Roadmap](#roadmap).

---

## Extending

Point the loop at your own domain by implementing the three roles:

```python
from panda_agent.types import Task, ExecutionResult, Evaluation

class ImageExecutor:
    def execute(self, task: Task) -> ExecutionResult:
        # run your pipeline
        return ExecutionResult(output_path="out.jpg", success=True, tool_calls=[])

class ImageEvaluator:
    def evaluate(self, task: Task, result: ExecutionResult) -> Evaluation:
        score = my_quality_metric(result.output_path)   # e.g. a VLM judge
        return Evaluation(
            score=score,
            issues=["background blur is uneven"],
            root_cause="gaussian kernel size is hardcoded",
            suggested_changes="make kernel size adaptive to image resolution",
        )

run_evolution(
    executor=ImageExecutor(),
    evaluator=ImageEvaluator(),
    improver=None,          # built-in Improver patches tools.py
    task=Task(input_path="in.jpg", instruction="blur the background"),
)
```

See `plugins/photo_edit/` for a complete worked example.

**Evaluator design is where the leverage is.** A vague evaluator produces vague
`root_cause` strings, which produce useless patches. The more concrete and
diagnostic your evaluation, the better the evolution. Prefer objective metrics
over LLM opinion where you can get them.

---

## Security

**⚠️ This framework executes LLM-generated code and shell commands with no
sandboxing. Do not run it on a machine you care about, on production data, or
with credentials in the environment.**

Specifically:

1. **`run_command` uses `shell=True`** ([`tools.py:106`](src/panda_agent/tools.py))
   with no allowlist. An LLM — or anything that can influence the LLM's output —
   can execute arbitrary commands with your user's full privileges.

2. **The Improver rewrites source files in place.** It patches `tools.py` and
   `brain.py` inside the installed package directory.

3. **The verification gate is bypassable in principle.** Patches are validated by
   running `pytest tests/`, but the agent also has `patch_file` and
   `run_command`, and the tests live in the same repository. Nothing structurally
   prevents an agent from weakening a test rather than fixing the code.

4. **No prompt-injection defense.** File contents and command output are fed
   straight back into the conversation. A file containing adversarial
   instructions can hijack the loop.

**Recommended usage until sandboxing lands:** run inside a container or VM, with
a scoped-down working directory, no credentials in the environment, and a
read-only mount for anything you don't want touched.

Hardening this is the top [Roadmap](#roadmap) item. Security reports welcome via
GitHub issues.

---

## Known Limitations

Stated plainly, because these determine whether the project is useful to you:

### 🔴 The evolution loop is open-loop

The Improver's gate is *"do the unit tests still pass?"* — **not** *"did the agent
get better at the task?"* A patch can pass all tests and still make the agent
worse, and nothing catches that. Worse, `run_evolution` tracks `best_score` but
never rolls back to the best-scoring version of the code — so the final state is
whatever the last patch left behind, not the best one found.

This is the single most important gap between this project and a genuinely
self-improving system. See Roadmap R1.

### 🔴 No sandbox

See [Security](#security).

### 🔴 The core loop is untested

`orchestrator.py` is the largest file (420 lines) and has zero test coverage.
`tests/test_framework.py` covers types, config, brain, tools, ReAct parsing, LLM,
and memory — but not the evolution loop or patch application, which is where the
risk concentrates.

### 🟡 Patch application is regex-based

[`_replace_function`](src/panda_agent/orchestrator.py) matches
`^def name\(...\)` with a regex and substitutes until the next top-level `def`.
This silently fails or corrupts on: methods inside classes, decorated functions,
nested functions, and async defs. An AST-based rewrite is the correct approach.

### 🟡 LLM output parsing is brittle

The ReAct loop has a heuristic fallback: if the response has no `TOOL_CALL` and
no `DONE:`, but is longer than 20 characters and doesn't start with "Continue",
it's treated as a final answer ([`react.py:145-157`](src/panda_agent/react.py)).
This was added because some models don't emit the `DONE:` prefix. It's a
pragmatic patch over a real problem, but it will misfire — a model thinking out
loud gets misread as a finished answer.

Similarly, the Evaluator extracts JSON with `re.search(r'\{.*\}')` and does
`.replace("'", '"')` to coerce single quotes — which corrupts any JSON string
containing an apostrophe. Parse failure silently yields `score = 50`, which
pollutes the evolution signal.

### 🟡 Docs/code drift

Earlier versions of this README described an Improver you were expected to
implement yourself. In the current code the Improver is built-in and specifically
patches `tools.py` / `brain.py`. This README has been rewritten to match the
code as of v0.2; if you find a remaining discrepancy, that's a bug — please file
an issue.

---

## Roadmap

Ordered by impact on making this genuinely useful.

### R1 — Close the evolution loop 🔴

The core fix. Make the verification gate measure *task performance*, not just
test passage:

- Add a **regression task suite** — a set of tasks with known-good expected
  outcomes, run before and after every patch
- Accept a patch only if **the score on that suite does not decrease**
- **Keep the best-scoring version**, not the last one — snapshot code per round,
  restore the best at the end
- Log score deltas per patch so evolution becomes auditable

Without this, "self-evolution" is unfalsifiable. With it, the project has a real
claim.

### R2 — Sandbox execution 🔴

- Replace `shell=True` with an allowlist plus argument-list invocation
- Run patches and commands in a container or a `seccomp`/`nsjail`-style jail
- Restrict filesystem writes to an explicit workspace root
- Make the test-verification step run in a **separate checkout**, so an agent
  cannot weaken the tests that gate it

### R3 — Test the loop 🔴

- Unit tests for `_replace_function`, `_extract_patch`, `_extract_relevant`
- Integration test for `run_evolution` with a mocked LLM: assert patch-kept and
  patch-reverted paths both work
- **Adversarial test: a patch that weakens a test must be rejected**

### R4 — AST-based patching 🟡

Replace the regex function-replacement with `ast` / `libcst`. Correctly handles
methods, decorators, async, and nested definitions. Validate the patched module
parses *before* writing to disk.

### R5 — Structured LLM output 🟡

- Use JSON mode / structured output where the backend supports it
- Replace the `re.search(r'\{.*\}')` + quote-swap hack with a real parser
- Distinguish "parse failed" from "score is 50" instead of collapsing both

### R6 — Bundle a reference graph-memory server 🟡

Ship a minimal embedding + PageRank implementation so `memory.enabled: true`
works out of the box instead of requiring an unbundled service.

### R7 — Observability 🟢

- Persist every round: task, score, patch diff, test output, score delta
- `panda history` to inspect the evolution trail
- Export traces for analysis

### R8 — Packaging 🟢

- PyPI release
- Pin dependency lower bounds properly
- CI: pytest + ruff + mypy on PRs

---

## Contributing

Contributions welcome, particularly on R1–R3 — those are the difference between a
demo and a tool.

```bash
git clone https://github.com/Doodle-Lin/panda-agent.git
cd panda-agent
pip install -e ".[test]"
pytest tests/
```

Guidelines:

- **Tests with behavior changes.** Especially anything touching the evolution
  loop or patch application.
- **Don't weaken the verification gate** to make a patch land. If the gate is
  wrong, fix the gate deliberately and say so.
- **Keep memory optional.** Graceful degradation when the graph server is absent
  is a hard requirement.
- **Match docs to code.** If behavior changes, update this README in the same PR.

Good first issues: R4 (AST patching) and R5 (structured output) are
self-contained and high-value.

---

## Design Principles

- **Verification over generation.** Generating a patch is easy; proving it helped
  is the product.
- **Evolve mind and hands.** Prompt and decision logic are as patchable as tools.
- **Fail closed.** A failed patch always reverts. A missing side-car service
  degrades, never crashes.
- **Injectable everything.** Executor/Evaluator/Improver are swappable for any
  domain.
- **Model-agnostic.** Any OpenAI-compatible endpoint; reasoning models supported
  via `reasoning_content` fallback.
- **Honest about state.** Limitations are documented, not hidden.

---

## License

MIT — see [LICENSE](LICENSE).

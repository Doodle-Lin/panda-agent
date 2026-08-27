# 🐼 PandaAgent

**An agent that rewrites its own tools to get better at your task — and only
keeps a rewrite that measurably helped.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-198%20passing-brightgreen.svg)](#tests)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#project-status)

**English** · [简体中文](README.zh-CN.md)

Point it at a task. It runs the task, scores how it did, finds what limited it,
rewrites that part of itself, and re-runs to check the rewrite actually helped.
If it didn't, the change is reverted.

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

**Alpha — working prototype, not production-ready.**

| Component | Status | Notes |
|---|---|---|
| ReAct loop + tool execution | ✅ Working | 8 registered tools: 6 task tools + 2 memory tools |
| 3-agent evolution loop | ✅ Working | Executor → Evaluator → Improver |
| Patch application | ✅ Working | libcst CST rewriting, auto-backup, revert on failure |
| Brain evolution | ✅ Working | Patches prompt + decision logic |
| CLI + TUI | ✅ Working | `panda`, `panda chat -q`, `panda evolve -t` |
| **Regression gate** | ✅ Working | Optional gate rejects measured task regressions |
| **Execution boundaries** | ✅ Working | Command allowlist + workspace containment |
| Graph memory | 🟡 Optional | Requires external server; degrades gracefully |
| Evolution history → memory | 🔴 Not built | Improver does not learn from past patch outcomes |
| OS-level sandbox | 🟡 Partial | Allowlist + path containment, but no kernel isolation. See [Security](#security) |

**Test suite: 198 pytest cases** across parsing, patching, benchmarking, the
orchestrator, and the security boundary.

Read [Known Limitations](#known-limitations) before running this on anything
you care about.

---

## Why This Exists

Let an LLM edit its own source and you will get a patch. It will look
reasonable. The hard question is the one nobody answers: did it help?

"The tests still pass" doesn't answer it. Tests tell you the code isn't
*broken* — an agent can pass every test and still have gotten worse at the job.
A patch that drops line numbers from search results is perfectly valid Python
and quietly makes the agent worse at citing sources.

So this project keeps a set of tasks with known-good answers and runs them
before and after every patch. Score dropped? The patch goes back. That's the
whole idea — the difference between an agent that rewrites itself and one that
*improves* itself.

Give it no task suite and you're back to the tests-only gate, same as everyone
else.

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
panda config init      # writes ~/.panda/config.yaml (or $PANDA_HOME/config.yaml)
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
  enabled: true                           # unavailable service is handled gracefully
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
panda chat -q "list all Python files in src/ and report the largest one"
```

### Run the evolution loop

```bash
panda evolve -t "search the codebase for TODO comments and summarize them" \
    --target 90 \
    --rounds 3
```

When the loop finishes you get one summary line:

```text
Rounds: {n}, Score: {score}, Patches: {n}
```

### Python API

```python
from panda_agent.orchestrator import run_evolution
from panda_agent.types import Task

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
    if r.evaluation:
        print(f"  round {r.round_num}: {r.evaluation.score:.0f} — {r.evaluation.root_cause}")
```

All three agents are injectable — pass your own `Executor` / `Evaluator` /
`Improver` to target a different domain (see [Extending](#extending)).

### Gate patches on measured performance

The Improver always checks the unit tests. To also reject patches that make the
agent worse at representative tasks, configure a baseline and benchmark gate
with a task suite such as:

```yaml
# benchmarks/tasks.yaml
- id: search_with_locations
  instruction: Find every TODO comment under fixtures/sample_project/ and list them with their file name and line number.
  scorer: exact_match
  expected:
    contains: ["config.py", "handlers.py", "7", "11"]
    not_contains: ["no TODO"]
  weight: 1.5

- id: apply_edit
  instruction: In fixtures/sample_project/config.py, change DEFAULT_PORT from 8080 to 9090. Change nothing else.
  scorer: file_state          # scores the file, not what the agent claims
  expected:
    file: fixtures/sample_project/config.py
    contains: "DEFAULT_PORT = 9090"
    not_contains: "DEFAULT_PORT = 8080"
  weight: 2.0
```

Set `Improver.baseline`, `Improver.benchmark_gate`, and a tolerance before the
loop; the full wiring and tolerance guidance are in the [benchmark walkthrough](docs/benchmark.md).
A patch that passes `pytest` but drops the weighted score beyond the tolerance
is reverted, with the reason fed into the next attempt. The documented
experiment records the measured `100 → 89.3` regression.

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
- Applies the patch by replacing the definition via libcst, validating that the
  result parses **before** writing to disk
- **Gate 1 — `pytest tests/`.** Answers *"is the code broken?"* If it fails,
  restore from backup and retry with the error fed back to the LLM.
- **Gate 2 — regression benchmark.** Answers *"did the agent get worse?"* Runs
  the task suite and rejects the patch if the weighted score drops beyond
  tolerance, feeding the reason back so the next attempt isn't blind. Skipped
  when no suite is configured.

**4. Loop** — until `score >= target_score` or `max_rounds` exhausted.

Gate 2 is the one that makes the loop falsifiable. A patch can be valid Python
that passes every unit test while making the agent measurably worse — verified
with an agent that merely stopped emitting line numbers in search results:
weighted score 100 → 89.3, now rejected.

### What's evolvable

`tools.py` — the agent's **hands**:

| Tool | Purpose |
|---|---|
| `read_file` | Read file contents |
| `write_file` | Write/create files |
| `search_files` | Regex search across files |
| `list_files` | Directory listing |
| `patch_file` | Find-and-replace in a file |
| `run_command` | Execute an allowlisted command, no shell — [see Security](#security) |
| `memory_retrieve` | Query graph memory (if enabled) |
| `memory_write` | Write to graph memory (if enabled) |

`brain.py` — the agent's **mind**:

- `SYSTEM_PROMPT` — the core instruction set
- `should_retry(tool, error, count, max)` — retry policy
- `max_turns_for_task(task)` — complexity-based turn budgeting

Function signatures are kept stable so the Improver can rewrite bodies without
breaking callers.

---

## Graph Memory

Optional associative memory backed by an external graph server. `MemoryClient`
uses embedding similarity plus Personalized PageRank diffusion to retrieve
related knowledge.

```yaml
memory:
  enabled: true
  graph_url: "http://127.0.0.1:9121"
  auto_write: true      # persist task outcomes automatically
```

If the server is unavailable, `retrieve` returns `[]` and `write` returns an
error dictionary; memory remains optional. The graph server is not bundled;
bundling a reference implementation is on the [Roadmap](#roadmap).

---

## Security

The threat model is not a malicious operator. The agent's next action is chosen
by a model whose context includes file contents and command output, so anything
it reads can influence what it runs next. Tool arguments are untrusted input.

### What is enforced

**Commands run through an allowlist, without a shell.** `run_command` parses the
string to an argv list and rejects shell metacharacters, then executes with
`shell=False`. There is no shell to inject into.

```bash
run_command "pytest tests/ -q"              # allowed
run_command "echo SAFE; echo INJECTED"      # rejected: metacharacter ';'
run_command "curl http://example.com"       # rejected: 'curl' not on allowlist
```

**File access is confined to a workspace root.** Paths resolve before the
containment check, so `..` traversal and symlinked escapes are both caught.

```bash
read_file "src/panda_agent/tools.py"        # allowed
read_file "/etc/passwd"                     # rejected: escapes the workspace
read_file "a/../../../etc/passwd"           # rejected: escapes the workspace
```

**Subprocesses get a scrubbed environment.** Variables whose names look like
credentials (`*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`, …) are removed, so a
command that dumps its environment is not a credential disclosure.

| Variable | Purpose |
|---|---|
| `PANDA_WORKSPACE` | Directory file tools are confined to (default: cwd) |
| `PANDA_ALLOWED_COMMANDS` | Extra commands to permit, space/comma separated |
| `PANDA_UNSAFE=1` | Disable path containment checks — for isolated environments only |

### What is not

**No OS-level isolation.** The allowlist bounds *which* programs run, not what
they can do once running. `python3` is permitted because the loop needs it, and
`python3 -c '...'` can do anything Python can. For untrusted tasks, run the
whole thing in a container or VM — the allowlist is defense in depth, not a jail.

**The test gate is bypassable in principle.** Patches are validated by running
`pytest tests/`, but the agent has `patch_file` and the tests live in the same
repository. Nothing structurally prevents weakening a test instead of fixing the
code. The regression benchmark makes this harder — a weakened test does not
improve benchmark score — but the correct fix is verifying in a separate
checkout. See Roadmap R2.

**No prompt-injection defense.** File contents and command output feed straight
back into the conversation. A file containing adversarial instructions can still
steer the loop; the boundaries limit the damage, they don't detect the attempt.

Security reports welcome via GitHub issues.

---

## Known Limitations

These limitations matter when deciding where to use the project:

### 🔴 The Improver does not learn from its own history

Every improvement attempt starts from zero. The loop knows that round 3 scored
72, but not that a similar patch was already tried and rejected in round 1. The
outcome of each patch — accepted, rejected, and why — is discarded.

This is the largest remaining gap, and the most interesting one: graph memory
already exists for task context and tool access, but patch outcomes are not
recorded there. Feeding those outcomes into it would let the Improver accumulate
meta-knowledge about *how to modify this codebase*, not just how to do the task.
See Roadmap R1.

### 🟡 The benchmark gate needs a suite to be meaningful

The gate is only as good as the tasks you give it. The bundled
suite in `benchmarks/` is a starting point, not a benchmark — five tasks against
a toy fixture. A real deployment needs tasks representative of your workload
and a tolerance chosen for that workload.

### 🟡 No OS-level sandbox

The allowlist and workspace containment bound the obvious holes, but `python3`
is necessarily permitted and can do anything Python can. See
[Security](#security).

### 🟡 The ReAct termination heuristic can misfire

If a response has no `TOOL_CALL` and no `DONE:`, is longer than 20 characters,
and doesn't start with "Continue", it's treated as a final answer
([`react.py`](src/panda_agent/react.py)). This exists because some models omit
the `DONE:` prefix, but a model thinking out loud gets misread as finished.
Structured output would remove the guesswork.

### 🟡 Graph memory needs an external server

`memory.enabled: true` expects a side-car service. Absent it, the framework
degrades gracefully rather than failing — but the feature is effectively off
until you supply one. See Roadmap R4.

---

## Roadmap

Ordered by impact on making this genuinely useful.

### R1 — Feed evolution history into graph memory 🔴

The most interesting remaining work. Every patch outcome is currently thrown
away; storing it turns the loop from *"try things"* into *"try things you
haven't already failed at"*:

- Persist each attempt as a node: target function, patch diff, score delta,
  accepted/rejected, rejection reason
- Query it before generating: give the Improver the outcomes of past attempts on
  the same function
- Surface the accumulated knowledge, e.g.
  `✅ accepted (+6.2): added line numbers to search_files output`
  `❌ rejected (-8.1): switched to JSON output, harder for the LLM to parse`

This is what makes the system self-improving rather than merely self-modifying:
it learns how to modify itself, not just how to do the task.

### R2 — Verify patches in a separate checkout 🔴

The agent can currently reach the tests that gate it. Copying the tree to a
scratch directory, applying the patch there, and running the suite against that
copy makes weakening the gate structurally impossible rather than merely
discouraged.

### R3 — OS-level isolation 🟡

The allowlist bounds which programs run; it cannot bound what `python3` does
once running. Container or `seccomp`/`nsjail` execution for the command tool and
the test runner.

### R4 — Bundle a reference graph-memory server 🟡

Ship a minimal embedding + PageRank implementation so `memory.enabled: true`
works out of the box instead of requiring an unbundled service.

### R5 — Observability 🟢

- Persist every round: task, score, patch diff, test output, benchmark delta
- `panda history` to inspect the evolution trail
- Export traces for analysis

### R6 — Packaging 🟢

- PyPI release
- CI: pytest + ruff + mypy on PRs

---

## Extending

All three roles are injectable: provide an `Executor`, `Evaluator`, and
`Improver` for your domain. See `plugins/photo_edit/` for a complete worked
example.

**Evaluator design is where the leverage is.** A vague evaluator produces vague
`root_cause` strings, which produce useless patches. The more concrete and
diagnostic your evaluation, the better the evolution. Prefer objective metrics
over LLM opinion where you can get them.

---

## Tests

```bash
pytest tests/ -q          # 198 pytest cases
```

| File | Test functions | Covers |
|---|---|---|
| `test_framework.py` | 32 | types, config, brain, tools, ReAct parsing, LLM, memory |
| `test_security.py` | 32 | command injection, path traversal, environment scrubbing |
| `test_benchmark.py` | 29 | scorers, weighting, gate decisions, noise estimation |
| `test_parsing.py` | 28 | JSON extraction, apostrophes, parse-failure semantics |
| `test_patching.py` | 22 | decorators, async, nested, methods, constant replacement |
| `test_orchestrator.py` | 20 | patch kept / reverted / rejected on regression, retries, scoring |

The table counts test functions; parametrized tests bring the collected total to
198 cases.

The suite includes regression coverage for behaviour that was once broken,
notably `test_patch_that_passes_tests_but_degrades_is_rejected`.

---

Recent changes and the reasoning behind them: [CHANGELOG.md](CHANGELOG.md).

---

## Contributing

Contributions welcome, particularly on R1 and R2 — those are the difference
between a demo and a tool.

```bash
git clone https://github.com/Doodle-Lin/panda-agent.git
cd panda-agent
pip install -e ".[test]"
pytest tests/          # 198 cases, ~2s
```

Guidelines:

- **Tests with behavior changes.** Especially anything touching the evolution
  loop or patch application.
- **Don't weaken the verification gate** to make a patch land. If the gate is
  wrong, fix the gate deliberately and say so.
- **Don't delete a regression test to make a change pass.** Several tests exist
  specifically to pin behaviour that was once broken — notably
  `test_patch_that_passes_tests_but_degrades_is_rejected`, which is the whole
  reason the benchmark gate exists. If one blocks you, that is information.
- **Keep memory optional.** Graceful degradation when the graph server is absent
  is a hard requirement.
- **Match docs to code.** If behavior changes, update this README in the same PR.

Good first issues: R4 (bundle a reference memory server) and R5 (observability)
are self-contained. R1 is the interesting one if you want to work on what makes
this project distinctive.

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

MIT.

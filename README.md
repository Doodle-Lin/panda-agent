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
| Patch application | ✅ Working | libcst CST rewriting, auto-backup, revert on failure |
| Brain evolution | ✅ Working | Patches prompt + decision logic |
| CLI + TUI | ✅ Working | `panda run`, `panda evolve` |
| **Regression gate** | ✅ Working | Patches must not degrade measured task performance |
| **Execution boundaries** | ✅ Working | Command allowlist + workspace containment |
| Graph memory | 🟡 Optional | Requires external server; degrades gracefully |
| Evolution history → memory | 🔴 Not built | Improver does not learn from past patch outcomes |
| OS-level sandbox | 🟡 Partial | Allowlist + path containment, but no kernel isolation. See [Security](#security) |

**Test suite: 197 cases** across parsing, patching, benchmarking, the
orchestrator, and the security boundary.

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

### Gate patches on measured performance

By default the Improver only checks that unit tests still pass. To also require
that the agent didn't get *worse*, give it a task suite:

```yaml
# benchmarks/tasks.yaml
- id: search_with_locations
  instruction: Find every TODO under src/ and list it with file and line number.
  scorer: exact_match
  expected:
    contains: ["config.py", "7"]
  weight: 1.5

- id: apply_edit
  instruction: In config.py, change DEFAULT_PORT from 8080 to 9090.
  scorer: file_state          # scores the file, not what the agent claims
  expected:
    file: config.py
    contains: "DEFAULT_PORT = 9090"
    not_contains: "DEFAULT_PORT = 8080"
  weight: 2.0
```

```python
from pathlib import Path
from panda_agent.benchmark import load_tasks, run_benchmark, estimate_noise
from panda_agent.orchestrator import Improver

tasks = load_tasks(Path("benchmarks/tasks.yaml"))
workspace = Path("benchmarks")
runner = lambda task: my_executor.execute(task).output

# The agent is stochastic, so set tolerance from measured variance rather
# than guessing: 2 * stdev is a reasonable starting point.
mean, stdev = estimate_noise(tasks, runner, workspace, runs=3)

improver = Improver(config)
improver.baseline = run_benchmark(tasks, runner, workspace)
improver.benchmark_gate = lambda: run_benchmark(tasks, runner, workspace)
improver.tolerance = max(2.0, 2 * stdev)
```

A patch that passes `pytest` but drops the benchmark score beyond `tolerance` is
now reverted, and the reason is fed into the next attempt's prompt.

**Scorer choice matters.** `exact_match` and `file_state` are deterministic and
reproducible; `llm_judge` is not, and its noise propagates straight into the
accept/reject decision. Use it only for genuinely open-ended tasks.

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
| `PANDA_UNSAFE=1` | Disable enforcement entirely — for isolated environments only |

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

Stated plainly, because these determine whether the project is useful to you:

### 🔴 The Improver does not learn from its own history

Every improvement attempt starts from zero. The loop knows that round 3 scored
72, but not that a similar patch was already tried and rejected in round 1. The
outcome of each patch — accepted, rejected, and why — is discarded.

This is the largest remaining gap, and the most interesting one: graph memory
already exists but only serves the Executor. Feeding patch outcomes into it
would let the Improver accumulate meta-knowledge about *how to modify this
codebase*, not just how to do the task. See Roadmap R1.

### 🟡 The benchmark gate needs a suite to be meaningful

`check_no_regression` is only as good as the tasks you give it. The bundled
suite in `benchmarks/` is a starting point, not a benchmark — five tasks against
a toy fixture. A real deployment needs tasks representative of your workload,
and `estimate_noise` run to set the tolerance from measured variance rather than
the 2.0 default.

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

## Recent Changes

Work completed against the earlier roadmap, with the evidence that motivated it:

| Change | Why |
|---|---|
| **Regression gate** (`benchmark.py`) | The old gate asked "do tests pass?", not "did the agent improve?". Verified: an agent that merely drops line numbers from search output scores 100 → 89.3 and is now rejected, while passing every unit test. |
| **libcst patching** (`patching.py`) | The regex replacement failed on decorated, `async`, nested and class-scoped definitions. Worst case, a function body containing the text `\ndef ` truncated the file into a syntax error *before* pytest could catch it. |
| **Strict evaluation parsing** (`parsing.py`) | A parse failure silently became `score = 50`, so the Improver optimised against noise. Failures now retry once, then report no signal. |
| **Execution boundaries** (`security.py`) | `shell=True` was verified exploitable: `echo SAFE; echo INJECTED` ran both halves. File tools accepted `..` traversal. |
| **Improver prompt fix** | `_IMPROVE_PROMPT` contained a literal `{code_here}` that `str.format` treated as a field, so *every* call raised `KeyError` — the core mechanism had never run. Found by writing the first test for it. |
| **Loop test coverage** | `orchestrator.py` went from zero tests to 19, which is how the prompt bug surfaced. |

Test suite: 19 → 197 cases.

---

## Contributing

Contributions welcome, particularly on R1 and R2 — those are the difference
between a demo and a tool.

```bash
git clone https://github.com/Doodle-Lin/panda-agent.git
cd panda-agent
pip install -e ".[test]"
pytest tests/          # 197 cases, ~2s
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

MIT — see [LICENSE](LICENSE).

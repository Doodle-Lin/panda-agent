# Regression benchmark

The loop's unit-test gate answers *"is the code broken?"*. The benchmark gate
answers *"did the agent get worse at its job?"* A patch can be valid Python
that passes every test while making the agent measurably worse — verified with
an agent that stopped emitting line numbers in search results: weighted score
100 → 89.3, now rejected.

This document walks through wiring the gate up. For a quick look at what it
buys you, see [the README](../README.md#gate-patches-on-measured-performance).

## Writing a task suite

Each task has a deterministic scorer. `exact_match` checks the agent's answer
for required/forbidden strings; `file_state` inspects a file on disk after the
agent runs. Use `llm_judge` only when neither works — its noise goes straight
into the accept/reject decision.

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

`file_state` is the scorer to reach for on any task that modifies files: an
agent can claim success without having changed anything, and the scorer looks
at the actual effect.

## Wiring the gate into the Improver

The `Improver` exposes three attributes for this: `baseline`, `benchmark_gate`,
and `tolerance`. Set them before running the loop.

```python
from pathlib import Path
from panda_agent.benchmark import load_tasks, run_benchmark, estimate_noise
from panda_agent.orchestrator import Improver

tasks = load_tasks(Path("benchmarks/tasks.yaml"))
workspace = Path("benchmarks")
# The runner returns the text the scorer sees. ExecutionResult has no `.output`
# field -- adapt whatever your executor produces into a string here.
runner = lambda task: my_executor.execute(task).output_path

# The agent is stochastic, so set tolerance from measured variance rather
# than guessing: 2 * stdev is a reasonable starting point.
mean, stdev = estimate_noise(tasks, runner, workspace, runs=3)

improver = Improver(config)
improver.baseline = run_benchmark(tasks, runner, workspace)
improver.benchmark_gate = lambda: run_benchmark(tasks, runner, workspace)
improver.tolerance = max(2.0, 2 * stdev)
```

A patch that passes `pytest` but drops the weighted benchmark score beyond
`tolerance` is reverted, and the reason is fed into the next attempt's prompt
so it is not a blind retry.

## Choosing scorers

| Scorer | Deterministic? | Use for |
|---|---|---|
| `exact_match` | yes | Answers with a determinate string |
| `file_state` | yes | Tasks that modify files |
| `llm_judge` | no | Genuinely open-ended tasks |

`exact_match` and `file_state` are reproducible; `llm_judge` is not, and its
variance propagates into every gate decision. The bundled suite in
`benchmarks/` is five tasks against a toy fixture — a starting point, not a
benchmark. A real deployment needs tasks representative of your workload, and
`estimate_noise` run to set the tolerance from measured variance rather than
the 2.0 default.

## How a decision is made

`check_no_regression(before, after, tolerance)` returns a `GateResult`:

- `accepted` is `False` if any task is unusable (an incomplete benchmark is
  never accepted — the missing task may be the one the patch broke).
- `accepted` is `False` if the weighted mean drops by more than `tolerance`.
- `reason` carries the per-task deltas, worst first, so the Improver can feed
  the diagnosis back to the model.

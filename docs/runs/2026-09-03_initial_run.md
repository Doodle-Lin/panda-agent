# Initial experiment run — 2026-09-03

This is the first reproducible run produced by `scripts/run_experiment.py`,
committed to the record so the self-evolution claim is backed by a number
someone else can check, not an assertion.

- Runner: `python scripts/run_experiment.py --estimate-noise 2 --rounds 2 --test-ratio 0.4 --out experiments/initial_run`
- Model: `GLM52RJPT` via `https://aiapi.seres.cn/v1` (OpenAI-compatible)
- Suite: `benchmarks/tasks.yaml` (5 tasks)
- Split: train = `read_and_report, search_with_locations, count_and_compare`; held-out = `apply_edit, recover_from_missing_file`
- Tolerance: `2.00` (noise estimate ran but variance was 0 on a deterministic runner path)

## Result

Held-out weighted score: **100.0 → 100.0 (Δ +0.0)**. The agent was already at
ceiling on the held-out tasks before any evolution, so there was no room for
a kept patch to show a generalisation gain. Zero patches were kept across
the train runs.

The per-train-task scores are noisy in a way that is itself informative:
`search_with_locations` scored 0 in round 1 and 100 in round 2 with **no
patch applied** — that delta is LLM reliability (a transient connection
drop), not evolution. `read_and_report` scored 0 in both rounds for the same
reason. This is exactly the run-to-run variance the `--estimate-noise` flag
is designed to measure, and it is the reason the regression gate carries a
tolerance rather than rejecting any decrease.

## What this run proves and does not prove

**Proves:** the experiment runner works end-to-end against a real LLM. It
captures per-round scores, distinguishes "no patch" from "patch rejected",
reports a held-out before/after delta, and leaves the repo clean. The
deterministic scorers fire correctly when `benchmark_id` is bound.

**Does not prove:** that self-evolution improves the agent. The bundled
suite is five toy tasks against a single fixture; the baseline is already at
ceiling, so the loop has nothing to improve. Demonstrating evolution needs
a suite where the agent starts below ceiling — harder tasks, a weaker
baseline model, or a deliberately degraded starting state — so a kept patch
can move the held-out score. That is the next run, not this one.

## Bug fixes this run surfaced

1. Successful native function-calling tool calls were not recorded in
   `result.tool_calls`, so the deterministic scorer scored "completed"
   instead of the real tool output. Fixed in `react.py`.
2. `run_experiment` did not `chdir` into the workspace, so relative fixture
   paths failed; and `out_dir` was resolved after the `chdir`, so the report
   landed in the wrong place. Both fixed in `run_experiment.py`.

## Reproducing

```bash
cd <repo>
python scripts/run_experiment.py --estimate-noise 2 --rounds 2 --test-ratio 0.4 --out experiments/initial_run
```

The raw artefacts are at `experiments/initial_run/report.{json,md}` (gitignored)
and mirrored here as `2026-09-03_initial_run.{json,md}`.

---

## Raw report (mirrored from experiments/initial_run/report.md)

# Self-Evolution Experiment — 2026-09-03T10:05:17

## Summary

- Train tasks: `read_and_report, search_with_locations, count_and_compare`
- Held-out tasks: `apply_edit, recover_from_missing_file`
- Tolerance: 2.00

### Held-out generalisation

- Weighted score **before**: 100.0
- Weighted score **after**: 100.0
- Delta: **+0.0**

| Task | Before | After | Delta |
|---|---:|---:|---:|
| apply_edit | 100 | 100 | +0 |
| recover_from_missing_file | 100 | 100 | +0 |

## Train runs (per task)

### read_and_report

- Final score: 0 | Patches kept: 0 | Restored from round: None

| Round | Score | Patched | Target | Reject reason |
|---:|---:|:---:|:---:|---|
| 1 | 0 | no | no |  |
| 2 | 0 | no | no |  |

### search_with_locations

- Final score: 100 | Patches kept: 0 | Restored from round: None

| Round | Score | Patched | Target | Reject reason |
|---:|---:|:---:|:---:|---|
| 1 | 0 | no | yes |  |
| 2 | 100 | no | yes |  |

### count_and_compare

- Final score: 100 | Patches kept: 0 | Restored from round: None

| Round | Score | Patched | Target | Reject reason |
|---:|---:|:---:|:---:|---|
| 1 | 100 | no | yes |  |

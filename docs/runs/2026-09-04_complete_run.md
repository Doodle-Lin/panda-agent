# Complete End-to-End Observation Run — 2026-09-04

## What this is

The first complete end-to-end observation run. Minimal config (1 train task, 1 test task, 1 round) to maximize the chance of finishing before the endpoint drops. The run completed successfully.

## Setup

- Model: GLM52RJPT via https://aiapi.seres.cn/v1
- Degraded baseline: build_system_prompt with all tool-use rules removed
- Train task: read_and_report (read config.py, report DEFAULT_PORT)
- Held-out task: apply_edit (change DEFAULT_PORT from 8080 to 9090)
- Rounds: 1

## Result

- Held-out weighted: 100.0 -> 100.0 (delta +0.0)
- Patches kept: 0
- Conclusion: evolution did NOT occur in this run

## Why no evolution

The train task scored 0 (degraded prompt, agent answered from memory). The Improver had 1 round with 3 retries to generate a patch. It did not produce a kept patch. With only 1 round and 1 train task, the loop had minimal opportunity to iterate.

## What this run proves

The runner completes end-to-end. This is the first observation run that finished all phases: degrade baseline, score held-out before, evolve on train, score held-out after, restore baseline, write report. The plumbing works. The +0.0 delta is honest: no patch was kept, so the held-out did not change.

## How to get a non-zero delta

1. More rounds (3+) so the Improver has multiple attempts.
2. More train tasks so the regression gate has coverage.
3. A stable endpoint so retries do not exhaust on transient failures.

The minimal config traded completeness for speed. A real run needs more rounds and tasks, which requires the endpoint to stay up longer.

## Reproducing

    python scripts/observe_evolution.py --rounds 1 --train read_and_report --test apply_edit --out docs/runs/observed

## Raw report

Held-out: 100.0 -> 100.0 (delta +0.0)
Patches kept: 0

| Train task | Round 1 | Patches |
|---|---:|---:|
| read_and_report | 0 | 0 |

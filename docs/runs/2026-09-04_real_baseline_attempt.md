# Real Baseline Evolution Attempt — 2026-09-04

## What this is

Fifth run, this time with the **real (non-degraded) baseline** — the actual `brain.py` and `tools.py` as they exist in master. The question: can the evolution loop improve an already-working agent, not just recover a degraded one?

## What happened

The run was interrupted by the LLM endpoint outage (same pattern as previous runs). But before the outage, the Improver **evolved `tools.py`**:

### tools.py — _tool_read_file improved

- Changed encoding from `utf-8` with `errors="replace"` to `utf-8-sig` (handles BOM)
- Increased truncation limit from 50,000 to 1,000,000 chars (more complete file reads)

Diff: `observed_tools_evolved_real_baseline.diff`

This is a defensible improvement: `utf-8-sig` handles Windows BOM files correctly, and the 50K limit was arbitrarily small for real codebases.

## What this proves

The Improver generates defensible patches **even against a working baseline** — it's not just recovering degraded code, it's improving working code. The encoding and truncation changes are the kind of incremental improvement a human developer would make.

## Pattern across five runs

| Run | Baseline | brain evolved | tools evolved | Completed |
|---|---|---|---|---|
| 1 (initial) | real | no | no | yes (ceiling) |
| 2 (degraded) | degraded | no | yes (encoding) | partial |
| 3 (test constraints) | degraded | yes | no | partial |
| 4 (LLM retry) | degraded | yes | yes (search) | partial |
| 5 (real baseline) | real | no | yes (read_file) | partial |

The endpoint outage remains the sole blocker for a complete run.

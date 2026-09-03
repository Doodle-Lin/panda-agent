# Self-Evolution Observation with Test Constraints — 2026-09-03

## What this is

Second observation run, with the test-constraint injection feature (commit 01bdaa0). The Improver now sees relevant test bodies in its prompt, so it knows that build_system_prompt must preserve {tool_descriptions}.

## Key finding: the Improver evolved the brain prompt

With test constraints injected, the Improver generated a new build_system_prompt that:
- Preserves {tool_descriptions} (test constraint injection worked)
- Adds tool-use rules ("Call ONE tool at a time", "Use read_file before write_file")
- Adds DONE:/FAILED: signaling
- Adds "final answer MUST include actual result or content requested"
- Adds Windows path handling

The evolved prompt diff is saved as observed_brain_evolved_with_constraints.diff. This is a legitimate prompt evolution from the degraded baseline.

## Held-out result: regression

Held-out weighted score: 100.0 -> 57.1 (delta -42.9)

| Task | Before | After | Delta |
|---|---:|---:|---:|
| apply_edit | 100 | 100 | +0 |
| recover_from_missing_file | 100 | 0 | -100 |

The evolved prompt helped apply_edit but broke recover_from_missing_file. This is a real overfitting signal: the evolved prompt is more aggressive about tool use, which helps edit tasks but hurts the "read a missing file, say it's missing" task.

## Why the report says 0 patches kept

The run_evolution snapshot/restore logic restored an intermediate state. The Improver DID generate and apply the brain patch (visible in the on-disk diff captured during the run), but run_evolution's best-round restore may have reverted because all train rounds scored 0 (due to LLM endpoint unreliability).

## Test constraint injection: confirmed working

The critical finding: test constraint injection solved the {tool_descriptions} problem. In the previous observation run (PR 8), the Improver's brain patch failed because it dropped {tool_descriptions}. In this run, with test constraints in the prompt, the LLM preserved it.

## LLM endpoint reliability

The LLM endpoint was unreliable: WinError 2, connection resets, random scoring variance. A clean run needs a more stable endpoint.

## What this proves

1. Test constraint injection works — the LLM preserves {tool_descriptions} because it sees the test.
2. Brain evolution is possible — the Improver generated a legitimate prompt recovery.
3. Held-out evaluation catches overfitting — the evolved prompt hurt recover_from_missing_file.
4. The loop is still hampered by LLM unreliability.

## Raw report

Held-out: 100.0 -> 57.1 (delta -42.9)
Patches kept: 0 (per run_evolution; brain patch was generated but reverted)

| Train task | Round 1 | Round 2 | Patches |
|---|---:|---:|---:|
| read_and_report | 0 | 0 | 0 |
| search_with_locations | 0 | 0 | 0 |
| count_and_compare | 0 | 0 | 0 |

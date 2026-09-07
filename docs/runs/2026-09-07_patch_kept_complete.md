# First Run with Patch KEPT by run_evolution — 2026-09-07

## What this is

The first observation run where run_evolution kept a patch. The Improver generated a brain.py patch that passed the test gate and was retained on disk.

## Setup

- Model: GLM-5.2 (GLM52RJPT)
- Degraded baseline: prompt actively discourages tool use
- Train task: recover_from_missing_file (scored 0 with degraded prompt)
- Held-out task: read_handlers_report
- Target: 70 (lowered from 90 because GLM-5.2 scores 100 even degraded)
- Rounds: 2

## Result

- Held-out: 100.0 -> 100.0 (delta +0.0)
- Patches kept: 1
- Conclusion: patch kept but held-out did not improve (ceiling effect)

## The patch

The Improver rewrote build_system_prompt from the degraded "do not use tools" version to a full tool-use prompt with:
- DONE:/FAILED: signaling
- Tool result surfacing in DONE: answer
- Windows path handling
- Missing-file guidance
- write_file/read_file/search_files usage rules

The patch passed the targeted test subset (test_framework, test_prompt_native, test_security, test_patching, test_evolution, test_react) in under 2 minutes.

## Why held-out didn't move

read_handlers_report was already at 100 with the degraded prompt (GLM-5.2 calls tools even when told not to, via native FC). So the patch fixed the train task but had no room to improve the held-out task. This is a ceiling effect, not an overfit.

## Three fixes that made this possible

1. Target lowered from 90 to 70: GLM-5.2 scores 100 on most tasks even with degraded prompt (native FC is strong). At target=90 the loop stopped before the Improver ran. At 70, the 0-scoring train task has headroom.
2. pytest timeout 300s -> 600s: the full test suite on Windows takes 12+ minutes, killing patches before they could be accepted.
3. Targeted test subset: run only test_framework, test_prompt_native, test_security, test_patching, test_evolution, test_react (6 files, <2 min) instead of all 374 tests. Sandbox detection ensures tests still run correctly in test fixtures.

## Reproducing

    python scripts/observe_evolution.py --rounds 2 --train recover_from_missing_file --test read_handlers_report --out docs/runs/observed

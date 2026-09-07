# Daily-Use Session Test — 2026-09-07

## What this is

First test of the daily-use self-evolution path with GLM-5.2 + embedded memory. Three tasks run in sequence, each followed by `_learn_after_task` (the chat-mode evolution path fixed in PR #16, with the clarified is_structural prompt from PR #17).

## Setup

- Model: GLM-5.2 (GLM52RJPT) via https://aiapi.seres.cn/v1
- Memory: embedded SQLite, fresh PANDA_HOME
- Workspace: benchmarks/ (so fixture paths resolve)
- Three tasks from the bundled benchmark suite

## Results

### Task 1: read_and_report
- success=True, tool_calls=6, answer="8080"
- Deterministic score: **100** (exact match)
- Lessons extracted: 2 (use `ls` not `dir`, avoid shell metacharacters)
- Memory written: yes
- Evolution triggered: no (score >= 70)

### Task 2: search_with_locations
- success=True, tool_calls=12, answer=partial TODO listing
- Deterministic score: **50** (only 2 of 4 expected strings found)
- Lessons extracted: 2 (use `rg -n "TODO"` directly, verify path with `pwd` first)
- Memory written: yes
- Evolution triggered: no (is_structural=false — the LLM saw it as a usage error, not a structural agent defect)

### Task 3: recover_from_missing_file
- success=True, tool_calls=5, answer="file is missing"
- Deterministic score: **80** (exact match on "not")
- Lessons extracted: 2 (use `ls` not `dir` on non-Windows, avoid metacharacters)
- Memory written: yes
- Evolution triggered: no (score >= 70)

### Memory state after 3 tasks
- 9 nodes, 27 edges (auto-linked by lexical similarity)
- retrieve("TODO config.py file") → 5 results, top score 0.21
- retrieve_context("find TODO comments in files") → lesson about `rg -n "TODO"` injected into system prompt

## What this proves

1. **The daily-use path works end-to-end.** Every task was scored with the deterministic scorer, lessons were extracted, memory was written, and past lessons were retrievable and injectable into future tasks' system prompts.

2. **The deterministic scorer catches quality the heuristic missed.** Task 2 scored 50 (not 80) because the answer was incomplete — the heuristic would have given it 80 just for succeeding with tool calls.

3. **Memory accumulates across tasks.** After 3 tasks, 9 knowledge nodes with 27 auto-linked edges. The context injection shows the lesson about `rg -n "TODO"` would surface in a future search task.

4. **Evolution did not trigger** because the Learner's LLM classified all failures as usage errors (is_structural=false), not structural agent defects. This is correct behavior with the clarified prompt (PR #17): the agent's prompt and tools are not broken, the model just needs to learn better strategies — which is what memory is for.

## Trigger verification (mocked)

To confirm the trigger mechanism works when is_structural=true, a separate test mocked the Learner LLM to return `is_structural=true` with `structural_reason="brain.py should_retry does not prevent doom loop"`. Result:

- Run 1: trigger=false (occurrence 1/2)
- Run 2: trigger=true (occurrence 2/2) ✓

The trigger fires correctly after 2 structural occurrences. The daily-use test didn't trigger because the real LLM correctly returned is_structural=false for usage errors.

## Reproducing

```bash
python scripts/observe_evolution.py --rounds 2 --train read_and_report --test apply_edit --out docs/runs/observed
```

Or for the daily-use path directly:
```bash
panda chat -q "Read fixtures/sample_project/config.py and report the value assigned to DEFAULT_PORT."
panda memory search "DEFAULT_PORT"
```

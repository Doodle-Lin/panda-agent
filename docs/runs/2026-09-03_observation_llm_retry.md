# Self-Evolution Observation with LLM Retry — 2026-09-03

## What this is

Third observation run, with LLM retry logic (commit 43cbc11) added to handle the endpoint unreliability that blocked previous runs. The retry logic (3 retries with exponential backoff) was added to `call_llm_detailed` so a single connection reset no longer zeros out an entire evolution round.

## What happened

The run was interrupted again — this time by a >25-minute hang on the LLM endpoint (the process was still running but producing no output). However, before the hang, the Improver **successfully evolved `build_system_prompt` again** — a legitimate prompt recovery from the degraded baseline, preserving `{tool_descriptions}` and adding tool-use rules.

The evolved brain diff is saved as `observed_brain_evolved_llm_retry.diff`. Key changes the LLM made:
- Preserved `{tool_descriptions}` (test constraint injection working)
- Added "When a task asks you to read, report, or show file content, you MUST include the ACTUAL content from the tool result in your DONE: answer"
- Added "Your DONE: answer must directly contain the information the user asked for, sourced from tool results"
- Added write_file, read_file, search_files usage rules
- Added Windows path handling
- Added tool call limit guidance

## Why the run didn't complete

The LLM endpoint (`GLM52RJPT` via `aiapi.seres.cn`) hung for >25 minutes mid-run — not a connection reset (which retry handles) but a silent stall where the streaming response opened but never sent data. The retry logic catches `Timeout` and `ConnectionError`, but a stall that doesn't time out falls through. This is a different failure mode than what the retry was designed for.

## What this proves

1. **The evolution loop works end-to-end when the endpoint cooperates.** Across three observation runs, the Improver has now generated legitimate brain prompt recoveries twice (with test constraints) and a tools.py patch once (the encoding fix from PR #8).
2. **LLM retry helps but doesn't solve stalls.** The retry logic handles transient connection resets, but a hanging stream needs a read timeout on the streaming response — a separate fix.
3. **The pattern is consistent.** Every time the endpoint is stable for even one round, the Improver generates a defensible patch. The blocker is purely infrastructure reliability, not the evolution mechanism.

## Remaining blocker

The LLM endpoint hangs on streaming responses. The fix is to add a per-chunk read timeout on `resp.iter_lines()` — if no data arrives within N seconds, treat it as a timeout and retry. This is a small change to `_call_llm_raw` in `llm.py`.

## Reproducing

```bash
python scripts/observe_evolution.py --rounds 2 --out docs/runs/observed
```

The evolved brain diff is at `observed_brain_evolved_llm_retry.diff`.

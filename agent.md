# Agent Rules: PandaAgent

> **Rule:** Every step must have checkable output. No output = not done.

## Environment

- Language: Python 3.12+
- Package manager: pip (or uv), `pip install -e ".[test]"`
- Test command: `python -m pytest tests/ -q -m "not slow" --tb=short`
- Full test command: `python -m pytest tests/ -q --tb=short` (includes slow/e2e tests)
- Lint command: `python -m ruff check src/ tests/` (after Phase 1 setup)
- Build command: N/A (pure Python, `pip install -e .` for editable)
- Working directory: E:\workspace\evo-agent

## Core Principles — What Must Not Break

PandaAgent is a **self-evolving agent framework**. Two design pillars are non-negotiable:

1. **Self-evolution must be falsifiable.** Every patch must survive a verification gate
   (unit tests + optional regression benchmark) before being kept. A patch that "looks
   correct" but makes the agent measurably worse at representative tasks MUST be reverted.
   Never weaken the gate to make a patch pass — the gate IS the product.

2. **Graph memory is the self-improvement engine.** Memory stores task outcomes and
   (future) patch history so the Improver learns from its own successes and failures.
   Memory is optional (degrades gracefully), but when available it must be structured
   and queryable. Never write raw conversation dumps to memory — write actionable
   knowledge nodes.

## Workflow

> Each step requires pasting output. Skip any step = harness broken.

1. Run `python -m pytest tests/ -q -m "not slow" --tb=no` → paste output.
   Not green → fix first, no new features.
2. Write test for next task → run → verify RED → paste output.
   Not red → test is wrong (or a false-positive: see Pitfall #14/#21).
3. Write minimal implementation → run → verify GREEN → paste output.
4. Run `python -m ruff check src/ tests/` → paste output.
5. `git diff` → show user → commit only after confirmation.
6. For LLM-dependent features: run e2e tests with `python -m pytest tests/ -m slow --tb=short`.
   Mock tests passing ≠ real API working (see Pitfall #26).

## Prohibitions

- Do NOT modify: `tests/test_patching.py`, `tests/test_parsing.py` (golden tests that
  guard the patching/parsing contracts — change the implementation, not these tests)
- Do NOT weaken or delete tests to make a patch pass. The test suite IS the gate.
- Do NOT install new dependencies without asking — especially not for the core
  package (requests, rich, pyyaml, libcst are the only hard deps)
- Do NOT use `print()` for debugging in production code — use `logging` or
  `sys.stderr` (existing pattern in orchestrator.py)
- Do NOT skip the RED phase (test must fail before implementation)
- Do NOT self-review — use requesting-code-review skill or delegate to Claude
- Do NOT hardcode absolute paths (see memory.py `_GRAPH_MEMORY_DIR` — this is a known
  bug to fix, not a pattern to follow)
- Do NOT change function signatures in tools.py/brain.py — the Improver patches
  function bodies, signatures must stay stable for callers

## Failure Recovery

- After 3 failed fix attempts on same issue: STOP
- Invoke systematic-debugging skill
- Report to user: current state, what was tried, what the hypothesis is
- Do NOT attempt fix #4 without architecture review
- For test_security failures: check if the test imports match our security.py
  interface (origin used `resolve_path`/`workspace_root`, our branch may differ)

## Code Style

- Type hints required on all public functions (Python 3.12+ style: `str | None`)
- Docstrings: Google style for public API, none for private helpers
- Commit message: conventional commits (`feat:`, `fix:`, `test:`, `refactor:`, `docs:`, `chore:`)
- Max file size: 500 LoC preferred, 1000 LoC hard limit
- Naming: snake_case for functions/variables, PascalCase for classes
- Imports: `from __future__ import annotations` at top of every module

## Git

- Branch: `feature/<branch-name>` for new work, `fix/<name>` for bug fixes
- Every task = one commit (or one logical group)
- Commit message: `type(scope): description` (e.g., `feat(memory): persist patch outcomes to graph memory`)
- Merge to main only after independent review passes
- Before pushing to GitHub: `git log -p -S "api_key\|internal\|your-api-endpoint.com" | head -50` —
  no API keys, internal domains, or personal emails in history

## Architecture Context (for the agent working on this codebase)

```
src/panda_agent/
├── orchestrator.py   # 3-agent loop: Executor → Evaluator → Learner → Improver
├── react.py          # ReAct loop: native FC + text fallback, self-repair, doom detection
├── tools.py          # 8 tools: read/write/search/list/run/patch + memory retrieve/write
├── brain.py          # System prompt + decision logic (should_retry, max_turns_for_task)
├── security.py       # Command allowlist + path containment + env scrubbing
├── patching.py       # libcst CST rewriting for patch application
├── parsing.py        # Robust JSON extraction (bracket matching, not greedy regex)
├── benchmark.py      # Regression task suite: exact_match, file_state, llm_judge scorers
├── memory.py        # Embedded graph memory (wraps graph_memory engine) — NEEDS FIX: hardcoded path
├── llm.py           # Streaming LLM caller (reasoning + native FC support)
├── config.py         # YAML config + env var expansion
├── cli.py            # panda chat / evolve / config / memory / tools
├── tui.py            # Rich-based TUI
└── types.py          # Dataclasses: Task, Evaluation, ExecutionTrace, etc.
```

Three-layer evolution:
- Level 1 (runtime): Self-Repair in react.py — adapt tool calls on error (automatic)
- Level 2 (post-task): Learner — extract lessons from ExecutionTrace → memory + error_counts
- Level 3 (structural): Improver — patch brain.py/tools.py, only when evidence ≥3 occurrences

## Known Issues (fix in Phase order, don't fix ad hoc)

| # | Issue | Phase |
|---|-------|-------|
| 1 | test_security 10 failures — interface mismatch | Phase 1 |
| 2 | Evaluator still uses `_parse_eval_json` (fabricates score=50) | Phase 2 |
| 3 | Improver missing `benchmark_gate`/`baseline`/`tolerance`/`last_reject_reason` | Phase 2 |
| 4 | No snapshot+restore of best-scoring code | Phase 2 |
| 5 | memory.py hardcodes `${PANDA_HOME}/graph_memory` path | Phase 5 |
| 6 | Evolution history (patch outcomes) not written to graph memory | Phase 3 |
| 7 | No `panda history` command (evolution not auditable) | Phase 6 |
| 8 | No CI/lint/PyPI pipeline | Phase 6 |

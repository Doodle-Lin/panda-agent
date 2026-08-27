# Tasks: PandaAgent — 高质量开源自进化Agent框架

> **Rule:** Every spec acceptance criterion maps to exactly one test. No orphan specs.

## Spec → Test Traceability

| Spec Line | Acceptance Criterion | Test File | Test Name | Status | Story | Parallelizable |
|-----------|----------------------|-----------|-----------|--------|-------|----------------|
| US1.1 | pytest non-slow → 0 failed 0 errors | (collection) | test_full_suite_non_slow | 🟡 GREEN | US1 | — |
| US1.2 | test_security all pass | tests/test_security.py | (60 tests) | 🔴 RED | US1 | [P] |
| US1.3 | tools.py exports get_tool_schemas | tests/test_framework.py | test_tools_import | 🟡 GREEN | US1 | — |
| US1.4 | pyproject.toml UTF-8 no BOM | tests/test_framework.py | test_pyproject_encoding | 🔴 RED | US1 | [P] |
| US1.5 | ruff check → 0 errors | (CLI) | ruff_check | 🔴 RED | US1 | — |
| US2.1 | unparseable eval → returns None | tests/test_orchestrator.py | test_unparseable_returns_none | 🔴 RED | US2 | [P] |
| US2.2 | regression patch → reverted | tests/test_orchestrator.py | test_regression_reverted | 🟡 GREEN | US2 | — |
| US2.3 | disk code = best round | tests/test_orchestrator.py | test_restores_best_round | 🔴 RED | US2 | — |
| US2.4 | restored_from_round set | tests/test_orchestrator.py | test_restored_from_round | 🔴 RED | US2 | [P] |
| US2.5 | benchmark_gate=None → skip | tests/test_orchestrator.py | test_gate_skipped | 🟡 GREEN | US2 | — |
| US3.1 | accepted patch → memory node | tests/test_evolution.py | test_patch_accepted_memory | 🔴 RED | US3 | [P] |
| US3.2 | rejected patch → memory node | tests/test_evolution.py | test_patch_rejected_memory | 🔴 RED | US3 | [P] |
| US3.3 | improver prompt has history | tests/test_orchestrator.py | test_improver_prompt_has_history | 🔴 RED | US3 | — |
| US3.4 | memory unavailable → no crash | tests/test_evolution.py | test_memory_unavailable | 🟡 GREEN | US3 | [P] |
| US4.1 | no hardcoded E:\ path | tests/test_memory.py | test_no_hardcoded_path | 🔴 RED | US4 | [P] |
| US4.2 | graph_memory missing → False | tests/test_memory.py | test_graph_memory_missing | 🔴 RED | US4 | [P] |
| US4.3 | memory search works when available | tests/test_memory.py | test_memory_search | 🟡 GREEN | US4 | — |
| US5.1 | verification in worktree | tests/test_orchestrator.py | test_worktree_verification | 🔴 RED | US5 | — |
| US5.2 | weakened test rejected | tests/test_orchestrator.py | test_weakened_test_rejected | 🔴 RED | US5 | — |
| US6.1 | panda history shows trail | tests/test_cli.py | test_history_command | 🔴 RED | US6 | [P] |
| US6.2 | pip install works | (manual) | pip_install_test | 🔴 RED | US6 | — |
| US6.3 | CI pytest + ruff on PR | .github/workflows/ci.yml | ci_pipeline | 🔴 RED | US6 | [P] |

**Status legend:** 🔴 RED (test written, failing) → 🟡 GREEN (test passing) → ✅ DONE (reviewed, committed)

## Task Execution Order

> Organize by User Story priority (P1 first). Each story is a Phase. Insert Checkpoints between phases.

### Phase 1: Setup — test baseline + tooling (sequential)

- [ ] Task 0: Fix test_security 10 failures (interface alignment)
  - File: `tests/test_security.py`, `src/panda_agent/security.py`
  - Issue: origin test imports `resolve_path`, `workspace_root`, `unsafe_mode`, `allowed_commands` — verify our security.py exports all of these
  - Run: `pytest tests/test_security.py -q --tb=short` → paste output
  - Fix: align interfaces (add missing exports or fix test expectations)
  - COMMIT: `fix(security): align test interface with security.py exports`

- [ ] Task 1: Add ruff configuration
  - File: `pyproject.toml` (add `[tool.ruff]` section)
  - Run: `ruff check src/ tests/` → paste output → fix any errors
  - COMMIT: `chore: add ruff linting configuration`

- [ ] Task 2: Add .gitignore (remove .claude/, note.txt, __pycache__ from tracking)
  - File: `.gitignore`
  - COMMIT: `chore: add .gitignore`

**Checkpoint**: `pytest tests/ -q -m "not slow"` → all green. P1 US1 done.

### Phase 2: Core evolution loop reliability (US2, sequential)

**Goal**: Evaluator doesn't fabricate scores, Improver has regression gate, best-round restore works.
**Prerequisite**: Phase 1 checkpoint passed.

- [ ] Task 3: Merge origin Evaluator improvements
  - Replace `_parse_eval_json` with `parse_evaluation` from parsing.py
  - `evaluate()` returns `None` on parse failure (not `Evaluation(score=50)`)
  - RED: write `test_unparseable_returns_none` → run → verify fail
  - GREEN: implement → run → verify pass
  - COMMIT: `fix(evaluator): return None on parse failure instead of fabricating score=50`

- [ ] Task 4: Merge origin Improver improvements
  - Add `benchmark_gate`, `baseline`, `tolerance`, `last_reject_reason` attributes
  - Gate 2: benchmark regression check after pytest
  - RED: write `test_regression_reverted` (already exists, verify it passes)
  - GREEN: merge from origin orchestrator → verify pass
  - COMMIT: `feat(improver): add regression benchmark gate`

- [ ] Task 5: Merge origin snapshot+restore
  - `snapshots` dict in `run_evolution`, restore best round on exit
  - `restored_from_round` field in `EvolutionResult`
  - RED: write `test_restores_best_round` → verify fail → implement → verify pass
  - COMMIT: `feat(orchestrator): restore best-scoring code state at loop end`

**Checkpoint**: `pytest tests/test_orchestrator.py -q -m "not slow"` → all green. US2 done.

### Phase 3: Evolution history → graph memory (US3, the core differentiation)

**Goal**: Every patch outcome written to memory; Improver reads history before generating.
**Prerequisite**: Phase 2 checkpoint passed.

- [ ] Task 6: Write patch outcomes to memory [P with Task 7]
  - After accept: write `node_type="patch_accepted"` with target file, root_cause, score delta, explanation
  - After reject: write `node_type="patch_rejected"` with rejection reason
  - RED: write `test_patch_accepted_memory` and `test_patch_rejected_memory`
  - GREEN: implement in orchestrator.py
  - COMMIT: `feat(memory): persist patch outcomes to graph memory`

- [ ] Task 7: Inject history into Improver prompt [depends on Task 6]
  - Before generating patch, query memory for past outcomes on similar root_cause
  - Add "## 历史经验" section to improve prompt with accepted/rejected examples
  - RED: write `test_improver_prompt_has_history`
  - GREEN: implement in orchestrator.py `_improve_file`
  - COMMIT: `feat(improver): query past patch outcomes from memory before generating`

**Checkpoint**: Run `panda evolve` with memory enabled → check `panda memory search "patch accepted"` returns results. US3 done.

### Phase 4: Independent checkout verification (US5, safety hardening)

**Goal**: Patches verified in git worktree at HEAD — Agent can't weaken tests.
**Prerequisite**: Phase 2 checkpoint passed (can work in parallel with Phase 3).

- [ ] Task 8: Worktree verification
  - `git worktree add --detach tmp HEAD` → copy patched source → run gates → cleanup
  - RED: write `test_worktree_verification` and `test_weakened_test_rejected`
  - GREEN: implement in orchestrator.py
  - COMMIT: `feat(security): verify patches in isolated git worktree`

### Phase 5: Memory portability (US4, open-source usability)

**Goal**: memory.py doesn't hardcode paths, graph_memory optional.
**Prerequisite**: Phase 1 checkpoint passed (can work in parallel with Phase 3-4).

- [ ] Task 9: Fix memory.py hardcoded path [P]
  - Replace `_GRAPH_MEMORY_DIR = Path(r"${PANDA_HOME}/graph_memory")` with configurable path
  - Search `PANDA_HOME/graph_memory/`, then `sys.path`, then fail gracefully
  - RED: write `test_no_hardcoded_path`
  - GREEN: implement
  - COMMIT: `fix(memory): remove hardcoded graph_memory path, make portable`

- [ ] Task 10: Graceful degradation when graph_memory missing [P with Task 9]
  - `MemoryClient.__init__` returns `is_available() == False` without raising
  - RED: write `test_graph_memory_missing`
  - GREEN: implement
  - COMMIT: `fix(memory): graceful degradation when graph_memory not installed`

### Phase 6: Observability + packaging (US6, after all stories)

- [ ] Task 11: `panda history` command [P]
  - Read `~/.panda/evolution_history.jsonl`, format as table
  - RED: write `test_history_command`
  - GREEN: implement in cli.py
  - COMMIT: `feat(cli): add panda history command for evolution audit trail`

- [ ] Task 12: CI pipeline [P]
  - `.github/workflows/ci.yml`: pytest + ruff on PR
  - COMMIT: `ci: add pytest + ruff pipeline for PRs`

- [ ] Task 13: PyPI packaging
  - `pyproject.toml` → add `[project.urls]`, classifiers, README as long-description
  - `python -m build` → `twine check` → verify
  - COMMIT: `chore: prepare PyPI packaging metadata`

- [ ] Task 14: Full test suite + e2e validation
  - Run: `pytest tests/ -q --tb=short` (including slow/e2e)
  - Run: `ruff check src/ tests/`
  - Verify: all green, no warnings
  - COMMIT: `test: full suite green — ready for release`

## Parallel Groups

- Group A: Phase 1 Tasks 0-2 (sequential, setup)
- Group B: Phase 3 Tasks 6-7 (sequential, US3)
- Group C: Phase 4 Task 8 (independent, can start after Phase 2)
- Group D: Phase 5 Tasks 9-10 (independent, can start after Phase 1)
- Group E: Phase 6 Tasks 11-13 (independent, can start after Phase 2)

## delegate_task Usage

For [P] tasks, fan out to subagents:

```
delegate_task(tasks=[
    {"goal": "Task 9: fix memory.py hardcoded path. Replace _GRAPH_MEMORY_DIR with configurable search. RED→GREEN→commit.", "context": "Follow agent.md rules. spec US4."},
    {"goal": "Task 10: graceful degradation when graph_memory missing. MemoryClient returns is_available()=False. RED→GREEN→commit.", "context": "Follow agent.md rules. spec US4."},
])
```

## Completion Criteria

- [ ] All tasks GREEN
- [ ] `pytest tests/ -q -m "not slow"` → 0 failed
- [ ] `ruff check src/ tests/` → 0 errors
- [ ] No hardcoded `E:\` paths in any source file
- [ ] Evaluator returns `None` on parse failure (not `Evaluation(score=50)`)
- [ ] Improver has `benchmark_gate` + `baseline` + `tolerance` attributes
- [ ] `run_evolution` restores best-round code and sets `restored_from_round`
- [ ] Patch outcomes written to memory as structured nodes
- [ ] Improver prompt includes history when available
- [ ] All commits are conventional
- [ ] `git diff main` shows only spec-related changes

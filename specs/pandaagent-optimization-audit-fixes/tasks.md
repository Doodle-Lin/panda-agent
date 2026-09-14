# Tasks: PandaAgent Optimization Audit Fixes

> **Rule:** Every audit finding maps to exactly one task with RED→GREEN→commit.

## Audit Source

Audit conducted 2026-09-14 against branch `claude/optimization-audit-fixes` off `master` (6216ecc).
14 findings from code audit, plus repo-hygiene cleanup. See `audit_findings.md` for full detail.

## Task Execution Order

> Phased. Phase N prerequisite: Phase N-1 checkpoint passed. Parallel groups noted with [P].

### Phase 0 — Repo hygiene + branch baseline (sequential)

- [x] Task 0.1: Create branch `claude/optimization-audit-fixes` from origin/master
  - DONE — branch created locally, not pushed until checkpoints pass

- [ ] Task 0.2: Remove stray root files [P-pre]
  - Files: `generate_gradient.py`, `gradient.png`, `_make_dir.py`, `_make_result.py`, `_read_result.py`, `_read_result_helper.py`, `_write_helper.py`, `_write_result_helper.py`, `test.txt`, `~/` (literal dir)
  - Verify: no `src/` or `tests/` code imports any of them (grep)
  - Add `.gitignore` entries for `_*.py`, `~/`, `gradient.png`, `test.txt`
  - COMMIT: `chore: remove scratch files leaked to repo root`

- [ ] Task 0.3: Baseline pytest + ruff
  - **RECORDED BASELINE (2026-09-14, branch off master 6216ecc):**
    - Collection: 375 tests collected (6 deselected slow)
    - Fast subset (test_security + test_parsing + test_framework): 125 passed, 1 skipped
    - `ruff check src tests scripts`: All checks passed
  - Full `pytest tests/ -m "not slow"` not run end-to-end in this session (slow on Windows); fast subset confirms baseline green. Subagents will run full suite before each commit.
  - No commit; record numbers above as the regression baseline

**Checkpoint 0**: branch clean, baseline numbers recorded.

### Phase 1 — High-priority correctness (sequential, one hotspot at a time)

- [x] Task 1.1: CLI path wires the regression gate (audit #1)
  - DONE: commit e5a5975 — `fix(cli): wire regression benchmark gate into Improver when suite configured`
  - Added `evolution.benchmark_suite` + `evolution.benchmark_tolerance` to config
  - `_wire_regression_gate()` in cli.py loads suite, builds baseline, attaches gate callback
  - Wired in both `cmd_chat` (Improver construction) and `cmd_evolve` (pre-built improver passed to run_evolution)
  - RED→GREEN: tests/test_cli.py::test_cli_wires_benchmark_gate_when_suite_configured

- [x] Task 1.2: worktree verification fails closed (audit #2)
  - DONE: commit 31ae73c — `fix(improver): fail closed when worktree isolation requested but unavailable`
  - `_verify_in_worktree` now returns (False, reason) on creation failure when use_worktree=True
  - RED→GREEN: tests/test_audit_phase1.py::TestAuditPhase1FailClosed

- [x] Task 1.3: patch-then-test wrapped in try/finally (audit #3)
  - DONE: commit 806bd71 — `fix(improver): guarantee patch revert on test-run interruption + gate security.py evolution`
  - try/except BaseException around write→worktree→pytest→benchmark block; restores from backup before re-raising
  - RED→GREEN: tests/test_audit_phase1.py::TestAuditPhase1InterruptSafety

- [x] Task 1.4: security.py improvement gated by config (audit #4)
  - DONE: commit 806bd71 (bundled with 1.3)
  - `evolution.improve_security` (default False) gates security.py patching in `improve()`
  - RED→GREEN: tests/test_audit_phase1.py::TestAuditPhase1SecurityGate (both disabled and enabled paths)

- [x] Task 1.5: ruff lint cleanup
  - DONE: commit c7c2e25 — `test(audit-phase1): clean up ruff lint in audit + CLI wiring tests`

**Checkpoint 1**: `pytest tests/test_evolution_audit.py tests/test_cli.py -q` green.

### Phase 2 — Security hardening (sequential, security.py is single owner)

- [ ] Task 2.1: symlink escape blocked (audit #5)
  - File: `src/panda_agent/security.py:164`
  - Issue: `resolve_path` follows symlinks; workspace-internal symlink to `/etc` reads out
  - Plan:
    - Use `Path.resolve(strict=False)` only after a containment pre-check on the unresolved path
    - Reject if any path component is a symlink pointing outside the workspace root
    - Walk component-by-component with `os.lstat`, detect symlinks, resolve their targets, check containment
  - RED: `tests/test_security.py::test_symlink_escape_rejected`
  - GREEN: implement
  - COMMIT: `fix(security): reject symlinked paths that escape workspace root`

- [ ] Task 2.2: pip/uv install subcommand restricted (audit #6)
  - File: `src/panda_agent/security.py:41-55`
  - Issue: `pip install <pkg>` is arbitrary code execution
  - Plan:
    - Keep `pip`/`uv` in allowlist but reject `install`/`download` subcommands unless `PANDA_ALLOW_INSTALL=1`
    - Document in README that package install is gated
  - RED: `tests/test_security.py::test_pip_install_rejected_by_default`
  - GREEN: implement
  - COMMIT: `fix(security): reject pip/uv install subcommands by default`

- [ ] Task 2.3: shell metacharacter list tightened (audit #7)
  - File: `src/panda_agent/security.py:60`
  - Issue: `*` `?` `!` rejected under `shell=False` but they're glob/regex chars
  - Plan:
    - Reduce `_SHELL_METACHARACTERS` to `;`, `&`, `|`, `` ` ``, `$()`, `<>`, newline
    - Allow `*` `?` `!` when the argument is quoted (already supported) OR when shell=False
  - RED: `tests/test_security.py::test_glob_chars_allowed_unquoted_under_shell_false`
  - GREEN: implement
  - COMMIT: `fix(security): narrow shell metacharacter list to actual shell operators`

**Checkpoint 2**: `pytest tests/test_security.py -q` green.

### Phase 3 — Memory robustness (sequential, memory.py single owner)

- [ ] Task 3.1: schema versioning + migrations (audit #10)
  - File: `src/panda_agent/memory.py:246-284`
  - Issue: `_create_schema` only migrates `source`; new columns on old DBs INSERT-fail
  - Plan:
    - Add `schema_version` table; bump on every column add
    - `_migrate(old_v, new_v)` runs `ALTER TABLE` per missing column
    - Version every column added since initial schema
  - RED: `tests/test_memory.py::test_old_schema_migrates_to_current`
  - GREEN: implement
  - COMMIT: `fix(memory): add schema versioning and per-column migrations`

- [ ] Task 3.2: write_if_novel indexed (audit #10)
  - File: `src/panda_agent/memory.py:332`
  - Issue: O(N) scan per write; no index on `node_type`
  - Plan:
    - `CREATE INDEX idx_nodes_type ON nodes(node_type)`
    - Filter by node_type in SQL, not Python
  - RED: `tests/test_memory.py::test_write_if_novel_scales_with_index` (timing: 1000 writes < 2x of 100 writes)
  - GREEN: implement
  - COMMIT: `perf(memory): index node_type for write_if_novel filtering`

- [ ] Task 3.3: concurrent write lock (audit #10)
  - File: `src/panda_agent/memory.py:593-616`
  - Issue: `np.savez` not atomic; concurrent writes race
  - Plan:
    - Add `threading.Lock` in `EmbeddedMemory` singleton
    - Guard `write`, `_save_embeddings` with the lock
    - Write to `.tmp` then atomic rename
  - RED: `tests/test_memory.py::test_concurrent_writes_no_corruption`
  - GREEN: implement
  - COMMIT: `fix(memory): lock and atomic-rename embeddings to prevent write races`

- [ ] Task 3.4: dead `dir()` check removed (audit #10)
  - File: `src/panda_agent/memory.py:410,415`
  - Issue: `'new_emb' not in dir()` is dead code, should be `locals()`
  - Plan: replace with explicit `new_emb: np.ndarray | None = None` at function top
  - COMMIT: `refactor(memory): remove fragile dir() check in write path`

### Phase 4 — Skill system (sequential)

- [ ] Task 4.1: matching scoring + ranking (audit #11)
  - File: `src/panda_agent/skill_system.py:46-51`
  - Issue: bidirectional substring matches anything
  - Plan:
    - Score = (len(trigger) / len(text)) if trigger in text else 0
    - Drop reverse-direction match entirely
    - Only inject skills with score >= 0.4; sort by score desc; cap at 3
  - RED: `tests/test_skill_system.py::test_short_trigger_does_not_match_long_text`
  - GREEN: implement
  - COMMIT: `fix(skill): rank matches by coverage, drop bidirectional substring`

- [ ] Task 4.2: robust YAML frontmatter parser (audit #11)
  - File: `src/panda_agent/skill_system.py:68-100`
  - Issue: hand-rolled parser breaks on CRLF, comments, nested lists
  - Plan:
    - Use `yaml.safe_load` for frontmatter (PyYAML is in deps)
    - Fall back to current parser only if yaml import fails
    - Log parse failures to stderr, don't silently return None
  - RED: `tests/test_skill_system.py::test_crlf_frontmatter_parses`
  - GREEN: implement
  - COMMIT: `fix(skill): use yaml.safe_load for frontmatter, log parse failures`

- [ ] Task 4.3: auto-generation post-task check (audit #12)
  - File: `src/panda_agent/orchestrator.py`, `src/panda_agent/skill_system.py`
  - Issue: skill auto-gen is LLM-discretion; no verification a skill file was written
  - Plan:
    - After task completion with 5+ tool calls, check if a new skill file appeared under `$PANDA_HOME/skills/`
    - If not, log a warning (don't force — LLM may have legitimately skipped)
    - Add `skill_auto_generated: bool` to EvolutionResult / chat result
  - RED: `tests/test_skill_system.py::test_skill_generation_detected_post_task`
  - GREEN: implement
  - COMMIT: `feat(skill): detect and report auto-generated skills after task`

### Phase 5 — Evolution audit trail (sequential)

- [ ] Task 5.1: run_evolution writes history (audit #13)
  - File: `src/panda_agent/orchestrator.py`, `src/panda_agent/evolution_history.py`
  - Issue: `record_evolution` only called from CLI; `run_evolution()` (scripts path) silent
  - Plan:
    - Call `record_evolution` at the end of every `run_evolution` round with score, patch summary, accept/reject
    - Wrap in try/except; on failure log to stderr, don't crash the loop
  - RED: `tests/test_evolution_audit.py::test_run_evolution_writes_history`
  - GREEN: implement
  - COMMIT: `fix(orchestrator): persist evolution history from scripted run_evolution`

- [ ] Task 5.2: history pagination + query (audit #14)
  - File: `src/panda_agent/evolution_history.py`
  - Issue: full-file load, no pagination, no query API
  - Plan:
    - `load_history(limit=None, since=None, file=None)` with streaming line-by-line read
    - `format_history_table` accepts a list (already paginated)
    - CLI `panda history --limit N --file tools.py` passes through
  - RED: `tests/test_evolution_audit.py::test_history_limit_and_file_filter`
  - GREEN: implement
  - COMMIT: `feat(history): pagination and file-filtered query for evolution trail`

- [ ] Task 5.3: record_evolution error visibility (audit #13)
  - File: `src/panda_agent/evolution_history.py:54-55,68-69`
  - Issue: silent exception swallowing; disk-full → history vanishes
  - Plan: log to stderr before swallowing; return bool from record_evolution
  - RED: `tests/test_evolution_audit.py::test_record_evolution_logs_on_failure`
  - GREEN: implement
  - COMMIT: `fix(history): surface record_evolution failures to stderr`

### Phase 6 — React robustness (sequential)

- [ ] Task 6.1: doom-loop salvage waits one turn (audit #8)
  - File: `src/panda_agent/react.py:553,660`
  - Issue: salvage fires on first detection; doom warning never tested
  - Plan:
    - On first doom detection: inject warning, continue loop
    - On second consecutive doom detection (same pattern): invoke salvage
    - Track `doom_strike` counter
  - RED: `tests/test_react.py::test_doom_loop_salvage_waits_one_turn`
  - GREEN: implement
  - COMMIT: `fix(react): give doom-loop warning one turn before salvage`

- [ ] Task 6.2: empty-content native FC doesn't burn turns (audit #8b)
  - File: `src/panda_agent/react.py:594-597`
  - Issue: empty content with prior tool_calls appends "Continue..." and increments turn — infinite loop on persistent empty
  - Plan:
    - Track consecutive empty-content responses; after 2, emit DONE with diagnostic
    - Don't increment turn on empty content (it's a model stall, not progress)
  - RED: `tests/test_react_native.py::test_empty_content_does_not_loop_forever`
  - GREEN: implement
  - COMMIT: `fix(react): break loop on repeated empty native-FC responses`

- [ ] Task 6.3: benchmark exact_match empty-contains (audit #9)
  - File: `src/panda_agent/benchmark.py:108-119`
  - Issue: empty contains + non-empty text → 100.0; agent says "ok" gets full marks
  - Plan: if `expected.contains` is empty, require `expected.text` to match exactly; if neither set, score 0
  - RED: `tests/test_benchmark.py::test_empty_contains_no_full_mark`
  - GREEN: implement
  - COMMIT: `fix(benchmark): reject empty contains+text giving full marks`

### Phase 7 — Docs sync (sequential, README single owner)

- [ ] Task 7.1: README R4 status updated (audit #10 stale)
  - File: `README.md:511-516`, `README.zh-CN.md`
  - Issue: README says "lexical, not vector semantic search"; code has embedding when sentence_transformers installed
  - Plan: update R4 section + Known Limitations to reflect embedding-when-available
  - COMMIT: `docs: update R4 — semantic embedding available when sentence_transformers installed`

- [ ] Task 7.2: README documents new config flags
  - File: `README.md`, `README.zh-CN.md`
  - Add `evolution.improve_security`, `evolution.benchmark_suite`, `evolution.benchmark_tolerance` to config table
  - Add `PANDA_ALLOW_INSTALL` to env var table
  - COMMIT: `docs: document improve_security, benchmark_suite, PANDA_ALLOW_INSTALL`

## Parallel Groups

- Phase 0.2 [P-pre]: cleanup can run any time before Phase 1 commits
- Phase 1 tasks are sequential (orchestrator.py + cli.py are hotspots)
- Phase 2 sequential (security.py single owner)
- Phase 3 sequential (memory.py single owner)
- Phase 4 sequential (skill_system.py single owner)
- Phase 5 sequential (evolution_history.py + orchestrator.py)
- Phase 6 sequential (react.py + benchmark.py)
- Phase 7 sequential (README)

## Completion Criteria

- [ ] All tasks GREEN
- [ ] `pytest tests/ -q -m "not slow"` ≥ baseline count (no new failures)
- [ ] `ruff check src tests scripts` ≥ baseline (no new errors)
- [ ] No stray root files committed
- [ ] All commits are Conventional
- [ ] `git diff master` shows only audit-fix-related changes

## Subagent Dispatch Plan

For each Phase, dispatch a `general-purpose` subagent with:
- Isolated worktree under `.claude/worktrees/phase-N`
- Explicit file ownership list (no overlap with other in-flight phase)
- RED→GREEN→commit cycle required
- Report back: SHA, changed paths, pytest output, residual risks

Phases run sequentially at the team level (one phase's checkpoint must pass before the next starts) to avoid orchestrator.py / README coordination conflicts. Within a phase, tasks are sequential per the file-ownership rule.

# Harness: PandaAgent Optimization Audit Fixes

> This harness document defines how a team of subagents will execute
> `specs/pandaagent-optimization-audit-fixes/{spec.md, tasks.md}` in parallel
> without violating the repository's collaboration protocol
> ([AGENTS.md](../../AGENTS.md), [docs/COLLABORATION_HARNESS.md](../../docs/COLLABORATION_HARNESS.md)).

## 1. Branch & worktree model

- **Integration branch**: `claude/optimization-audit-fixes` (already created from
  `master` @ 6216ecc). All phase commits land here.
- **Phase sub-branches**: each phase spawns `claude/audit-fix-phase-N` off the
  current integration branch tip. One phase = one sub-branch.
- **Worktrees**: each phase subagent works in an isolated worktree under
  `.claude/worktrees/audit-fix-phase-N/`. The main session stays on the
  integration branch.
- **No work on master**: enforced by harness; subagents that try to commit on
  `master` will be rejected by `scripts/harness.py`.

## 2. Path ownership matrix

Cross-cutting hotspots (one owner at a time per [AGENTS.md](../../AGENTS.md) §5):

| File | Owner phase | Conflict risk |
|---|---|---|
| `src/panda_agent/orchestrator.py` | P1 (T1.2, T1.3), P5 (T5.1) | sequential within phases |
| `src/panda_agent/security.py` | P2 only | none — single phase |
| `src/panda_agent/memory.py` | P3 only | none |
| `src/panda_agent/skill_system.py` | P4 only | none |
| `src/panda_agent/react.py` | P6 only | none |
| `src/panda_agent/cli.py` | P1 (T1.1), P5 (T5.2 CLI flag) | sequential within P1→P5 |
| `README.md` + `README.zh-CN.md` | P7 only | none |
| `tests/test_evolution_audit.py` | P1, P5 | sequential; P1 creates, P5 extends |

Phases run **sequentially at the team level**: phase N+1 starts only after
phase N's checkpoint passes and is merged into the integration branch. This
avoids orchestrator.py / cli.py / README coordination conflicts.

Within a phase, tasks are sequential per the file-ownership rule.

## 3. Subagent dispatch contract

Each phase dispatch is a single `Agent` call with `subagent_type=general-purpose`
and `isolation=worktree`. The prompt must contain:

1. **Phase ID** and branch name `claude/audit-fix-phase-N`
2. **File ownership list** (exact paths the agent may edit)
3. **Read-only paths** (paths the agent may consult but not edit)
4. **Task list** copied verbatim from `tasks.md` Phase N
5. **Acceptance criteria** copied from `spec.md` US-AN for that phase
6. **Verification commands** the agent must run before reporting back:
   ```bash
   python -m pytest tests/ -q -m "not slow" --tb=short
   python -m ruff check src tests scripts
   ```
7. **Commit discipline**:
   - Conventional Commits only (e.g. `fix(improver): fail closed when worktree ...`)
   - One commit per task; small and bisectable
   - No merge commits; rebase onto integration branch tip before push
   - Never amend a pushed commit
8. **Report format** (the agent must return):
   - List of commits with SHA + subject + changed paths
   - Final `pytest` summary line
   - Final `ruff` result
   - Residual risks / known issues
   - Files it needed to read beyond ownership (informational)

## 4. Checkpoint protocol

After each phase subagent reports back, the main session:

1. **Verifies the report** — `git log claude/audit-fix-phase-N` shows the
   expected commits; `git diff master...claude/audit-fix-phase-N --stat` shows
   only owned paths changed.
2. **Re-runs the gate** in the integration worktree:
   ```bash
   python -m pytest tests/ -q -m "not slow" --tb=short
   python -m ruff check src tests scripts
   ```
3. **Squash-merges or fast-forwards** the phase branch into
   `claude/optimization-audit-fixes`. Prefer fast-forward (rebase phase branch
   onto integration tip first) to keep history linear per [AGENTS.md](../../AGENTS.md) §8.
4. **Updates `tasks.md`** — mark phase tasks ✅ DONE.
5. **Commits the tasks.md update** as `docs(spec): mark phase N complete`.

If checkpoint fails (tests red or ruff red), the phase subagent is re-dispatched
with the failure output; the phase branch is reset and re-tried. A phase is
never merged red.

## 5. Phase-by-phase dispatch plan

### Phase 0 — Repo hygiene (main session does this directly, no subagent)

Files to remove (untracked scratch, no `git rm` needed — just `rm`):
- `generate_gradient.py`, `gradient.png`
- `_make_dir.py`, `_make_result.py`, `_read_result.py`, `_read_result_helper.py`,
  `_write_helper.py`, `_write_result_helper.py`
- `test.txt`
- `~/` literal directory

`.gitignore` additions:
```
# scratch / agent output leakage
_*.py
~/
gradient.png
test.txt
.zcode/
```

Commit: `chore: remove scratch files leaked to repo root + tighten .gitignore`

### Phase 1 — High-priority correctness

**Subagent prompt skeleton:**

```
Phase 1 — High-priority correctness audit fixes.

Branch: claude/audit-fix-phase-1 (already created from claude/optimization-audit-fixes)
Worktree: .claude/worktrees/audit-fix-phase-1

Owns (may edit):
- src/panda_agent/cli.py
- src/panda_agent/config.py
- src/panda_agent/orchestrator.py
- src/panda_agent/types.py (only if needed for EvolutionResult fields)
- tests/test_cli.py
- tests/test_evolution_audit.py
- tests/test_config.py (if exists)

Reads (may consult, may not edit):
- src/panda_agent/improver.py
- src/panda_agent/benchmark.py
- src/panda_agent/security.py (do NOT edit in this phase)
- benchmarks/tasks.yaml
- specs/pandaagent-optimization-audit-fixes/spec.md (US-A1..A4)
- specs/pandaagent-optimization-audit-fixes/tasks.md (Phase 1 tasks)

Tasks (execute in order, RED→GREEN→commit each):

Task 1.1 — CLI wires benchmark gate
  Spec: US-A1
  Issue: cli.py:321 constructs Improver(config) without benchmark_gate/baseline.
  Plan:
    - Add config fields: evolution.benchmark_suite (path str, default ""),
      evolution.benchmark_tolerance (float, default 5.0)
    - When CLI starts an evolution run, if benchmark_suite path is set and
      file exists, build BenchmarkSuite and pass gate+baseline to Improver.
    - If path not set or file missing, log warning to stderr and skip gate.
  RED first: tests/test_cli.py::test_cli_wires_benchmark_gate_when_suite_configured
  GREEN: implement
  Commit: fix(cli): wire regression benchmark gate into Improver when suite configured

Task 1.2 — worktree fail-closed
  Spec: US-A2
  Issue: orchestrator.py:843-876 _verify_in_worktree returns (True, "worktree
         creation failed, skipping isolation") on failure — fail-open.
  Plan:
    - When use_worktree=True and creation fails: return (False, reason).
    - Caller reverts the patch.
    - When use_worktree=False: keep pass-through.
  RED: tests/test_evolution_audit.py::test_worktree_creation_failure_reverts_patch
  GREEN: implement
  Commit: fix(improver): fail closed when worktree isolation requested but unavailable

Task 1.3 — try/finally around patch+test
  Spec: US-A3
  Issue: orchestrator.py:996-1071, write_text before pytest; interrupt leaks.
  Plan:
    - Wrap write→test→accept/revert in try/except/finally.
    - On any exception (incl. KeyboardInterrupt), restore from backup_path if exists.
    - Guard shutil.copy2 with backup_path.exists().
  RED: tests/test_evolution_audit.py::test_interrupted_patch_restored
  GREEN: implement
  Commit: fix(improver): guarantee patch revert on test-run interruption

Task 1.4 — security.py improvement gated
  Spec: US-A4
  Issue: orchestrator.py:899, security.py patched unconditionally.
  Plan:
    - Add evolution.improve_security (bool, default false) to config.
    - Only attempt security patch when True.
  RED: tests/test_evolution_audit.py::test_security_not_patched_when_disabled
  GREEN: implement
  Commit: feat(config): gate security.py evolution behind improve_security flag

Verification before reporting back:
  python -m pytest tests/ -q -m "not slow" --tb=short
  python -m ruff check src tests scripts

Both must pass. Report commits, pytest summary, ruff result, residual risks.
Do not push; the main session will fast-forward the phase branch into the
integration branch after review.
```

### Phase 2-7 — same skeleton, different ownership/tasks

See `tasks.md` for the per-phase task list and `spec.md` for acceptance
criteria. The same dispatch contract applies.

### Phase 7 — Docs sync (single agent, README + zh-CN)

This phase owns only `README.md` and `README.zh-CN.md`. It must:
- Update R4 to mention embedding-when-available
- Add config fields `improve_security`, `benchmark_suite`, `benchmark_tolerance`
- Add env var `PANDA_ALLOW_INSTALL`
- Cross-reference `specs/pandaagent-optimization-audit-fixes/spec.md` is NOT
  required in README (specs are internal)

## 6. Failure & recovery

- **Phase subagent reports red**: re-dispatch with the failure output appended
  to the prompt; reset phase branch to integration tip first.
- **Phase subagent edits a non-owned file**: reject the report, reset the
  phase branch, re-dispatch with explicit ownership warning.
- **Phase subagent pushes or amends**: explicitly disallow in prompt; if
  detected, reset and re-dispatch.
- **Integration branch diverges from master during the run**: do NOT rebase
  the integration branch; only rebase phase branches. The integration branch
  stays linear off master until the final PR.
- **A task turns out to be larger than expected**: split it; the subagent
  reports partial completion + remaining work; main session re-dispatches a
  follow-up.

## 7. Final integration

After Phase 7 checkpoint passes:

1. Rebase `claude/optimization-audit-fixes` onto `origin/master` (fetch first).
2. Run full gate:
   ```bash
   python scripts/harness.py doctor --fetch
   python -m pytest tests/ -q --tb=short
   python -m ruff check src tests scripts
   python scripts/harness.py verify
   ```
3. Push the integration branch:
   ```bash
   git push -u origin claude/optimization-audit-fixes
   ```
4. Open a PR against `master` with:
   - Summary: "Closes 14 audit findings across security, evolution loop,
     memory, skills, react, docs"
   - Test plan: link to per-phase checkpoint outputs
   - Reviewer note: "Per-phase commits are bisectable; revert any single
     phase by reverting its merge commit."

## 8. Audit trail

This harness run is itself auditable:

- `specs/pandaagent-optimization-audit-fixes/spec.md` — what & why
- `specs/pandaagent-optimization-audit-fixes/tasks.md` — task-by-task traceability
- `specs/pandaagent-optimization-audit-fixes/HARNESS.md` (this file) — how
- Each phase branch's commit log — the actual work
- The final PR — review gate

A future contributor can replay the run by checking out the integration
branch and reading these three docs in order.

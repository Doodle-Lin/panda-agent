# Local/remote collaboration harness

This protocol lets local Codex work, remote agents, and human contributors work
in parallel without silently overwriting each other. It is deliberately strict:
when lineage, ownership, or verification is ambiguous, integration stops.

## What the harness protects against

- two actors editing the same files from different snapshots;
- accidental commits or direct pushes on `master`;
- stale task branches overwriting newer remote work;
- force-rewritten upstream history being merged as if it were a normal update;
- merge commits hiding an unreviewed conflict resolution;
- non-Conventional, non-bisectable handoffs;
- a green feature test masking a red repository baseline.

It has three layers:

1. `AGENTS.md` defines rules every actor must read.
2. `scripts/harness.py` and `.githooks/` enforce local invariants.
3. A GitHub ruleset enforces protected-branch invariants that local hooks can
   never guarantee.

## Actor and worktree model

Each active task has exactly one owner, branch, and worktree.

| Item | Rule | Example |
|---|---|---|
| Actor | Stable short name | `codex`, `remote`, `alice` |
| Branch | `<actor>/<task>` | `codex/embedded-memory` |
| Worktree | Separate directory | `../panda-agent-codex-memory` |
| Base | Fetched `origin/master` | commit SHA recorded in task |
| Handoff | Committed state only | SHA + checks + risks |

Create a worktree from the remote base, not from another actor's checkout:

```bash
git fetch --prune origin
git worktree add ../panda-agent-codex-memory \
  -b codex/embedded-memory origin/master
```

Never run two actors in the same working directory. Never use a stash as a
handoff mechanism: stashes have weak ownership, no reviewable message contract,
and are easy to apply to the wrong base.

## Path ownership

Before implementation, the task owner publishes an intended path set in the
issue, pull request, or orchestration prompt. Use directories when ownership is
broad and exact files when it is narrow.

Example:

```text
Owner: codex/embedded-memory
Base: 066ec96
Owns: src/panda_agent/memory.py, tests/test_memory.py, pyproject.toml
Reads: src/panda_agent/orchestrator.py, src/panda_agent/react.py
```

`README.md`, `README.zh-CN.md`, `pyproject.toml`, `src/panda_agent/types.py`, and
`src/panda_agent/orchestrator.py` are coordination hotspots. Only one active
task may own a hotspot. A second task either waits or consumes the first task's
commit as a dependency.

Path ownership prevents semantic conflicts that Git cannot detect. Git can
merge two edits to different lines while still producing a broken design.

## Start-of-task gate

Run:

```bash
python scripts/harness.py install     # once per clone
python scripts/harness.py doctor --fetch
```

The doctor rejects:

- protected or detached development branches;
- dirty or unmerged working trees;
- missing or unrelated `origin/master` history;
- branches that do not contain the current remote base;
- diverged remote task branches;
- merge commits or malformed commit messages in the task range.

Record the base SHA in the task description. It is the review boundary and the
reference used to distinguish upstream changes from task changes.

## Synchronization protocol

Use explicit fetch and rebase. The installed `pull.ff=only` setting makes an
accidental merge pull fail.

```bash
git fetch --prune origin
python scripts/harness.py doctor       # reports stale base and overlap
git rebase origin/master
python scripts/harness.py doctor
```

If the doctor reports overlapping local/upstream paths, stop and inspect both
diffs before rebasing. The file owner resolves the overlap; a second actor does
not guess at the intended composition.

After rebasing a previously pushed task branch, announce the new tip and use:

```bash
git push --force-with-lease origin HEAD
```

`--force-with-lease` is allowed only for task branches. It refuses to overwrite
a remote tip the local actor has not seen.

## Upstream rewrite protocol

A missing merge base, or a base commit that is no longer in `origin/master`, is
not an ordinary conflict. It indicates rewritten or unrelated history.

1. Stop all mutations and fetch once.
2. Preserve the old task tip under a clearly named backup branch.
3. Create a new task branch from the rewritten `origin/master`.
4. Compare functionality and tests, not just textual diffs.
5. Cherry-pick only commits that remain necessary, one at a time.
6. Never use `--allow-unrelated-histories`, a blind merge, or a destructive
   reset to conceal the rewrite.

Example:

```bash
git branch backup/codex-memory-before-rewrite codex/embedded-memory
git switch -c codex/embedded-memory-v2 origin/master
git cherry-pick <reviewed-sha>
```

## Commit and handoff gate

Every commit must be independently understandable and use:

```text
type(scope): imperative description
```

Allowed types are `build`, `chore`, `ci`, `docs`, `feat`, `fix`, `perf`,
`refactor`, `revert`, `style`, and `test`.

Before handoff:

```bash
python scripts/harness.py verify
```

The handoff payload is:

```text
Branch and commit SHA:
Base SHA:
Owned/changed paths:
Behavioral result:
Exact verification output:
Known risks or skipped checks:
```

The receiver fetches the exact SHA, runs the harness in a separate worktree,
and reviews the commit range. Do not transfer uncommitted files manually.

## GitHub ruleset required for `master`

Local hooks can be bypassed. Configure a repository ruleset for `master` with:

- pull requests required;
- approvals dismissed after new commits;
- conversation resolution required;
- required status checks for non-slow tests, lint, and harness doctor;
- branch required to be up to date before merge;
- linear history required;
- force pushes and deletions blocked;
- administrators and automation included, with no standing bypass role.

### Solo-maintainer mode (current)

When exactly one person has write access, require zero approvals and disable
"approval of the most recent push." The owner still opens a pull request and
may merge it only after the required `quality` check passes. This keeps the
reviewable change record, green CI gate, linear history, and server-side
protection against accidental direct or force pushes without creating an
impossible self-approval requirement.

### Multi-contributor mode

Before granting another person or agent write access, change the ruleset to
require at least one independent approval and approval of the most recent
push. Keep stale-approval dismissal and conversation resolution enabled. Do
not grant a permanent administrator or automation bypass; use a narrowly
audited emergency role only when operationally necessary.

Until those server rules exist, the repository is protected by convention and
local hooks, not by a hard remote boundary.

## Conflict-resolution rules

1. Re-run `doctor --fetch`; do not resolve against stale remote refs.
2. Identify the owner for every conflicted path.
3. The owner resolves; the other actor supplies intent and tests.
4. Never resolve generated files, lockfiles, schemas, or tests with blanket
   “ours”/“theirs”. Recreate them from the resolved source of truth.
5. Run focused tests, then the complete harness.
6. Commit the resolution separately when it contains non-trivial judgment.

## Baseline failures

Feature work does not proceed on a red base. If a fresh upstream checkout is
already red:

1. capture the exact failing command and output;
2. confirm it in an isolated worktree;
3. create a dedicated baseline-repair branch and commit;
4. rebase feature branches on that repair only after review.

“The failure was already there” is useful diagnosis, not permission to add
another unverified change.

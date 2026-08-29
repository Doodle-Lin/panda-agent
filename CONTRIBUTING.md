# Contributing to PandaAgent

PandaAgent accepts changes through isolated task branches and pull requests.
Direct development or pushes on `master` are intentionally blocked by the
repository harness.

## Set up once

```bash
python -m pip install -e ".[dev]"
python scripts/harness.py install
```

The installer enables repository-local hooks and safe Git defaults. It does
not modify global Git configuration.

## Start a task

```bash
git fetch --prune origin
git worktree add ../panda-agent-codex-my-task \
  -b codex/my-task origin/master
cd ../panda-agent-codex-my-task
python scripts/harness.py doctor
```

Declare the files you intend to edit in the issue, pull request, or agent task.
Do not overlap another active task's path set. When overlap is unavoidable,
make one task depend on the other's commit instead of editing concurrently.

## Finish a task

```bash
git fetch --prune origin
git rebase origin/master
python scripts/harness.py verify
git push -u origin HEAD
```

Open a pull request and hand off the commit SHA, changed paths, verification
output, and known risks. See
[`docs/COLLABORATION_HARNESS.md`](docs/COLLABORATION_HARNESS.md) for conflict,
force-push, ownership, and recovery procedures.

While the repository has one write-capable maintainer, the `master` ruleset
uses solo-maintainer mode: the pull request author may merge after the required
`quality` check passes. Before adding another write-capable collaborator,
switch the ruleset to the documented multi-contributor approval gate.

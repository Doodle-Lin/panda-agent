# PandaAgent Repository Harness

These rules apply to every human or agent working anywhere in this repository.
The canonical collaboration protocol is
[`docs/COLLABORATION_HARNESS.md`](docs/COLLABORATION_HARNESS.md).

## Non-negotiable rules

1. Never develop directly on `master` or `main`. One actor, one task branch,
   one worktree. Branches use `<actor>/<task>`, for example
   `codex/embedded-memory` or `remote/evaluator-consistency`.
2. Start from a freshly fetched `origin/master`. Before handoff, integration,
   or push, the task branch must contain the current `origin/master` and must
   have no merge commits.
3. Never use force push on `master`. Force-push a task branch only with
   `--force-with-lease` after announcing the rewritten commit range.
4. A force-rewritten or unrelated upstream history is a stop condition. Keep
   the old branch as a backup, create a new branch from the new upstream, and
   cherry-pick only reviewed commits. Do not merge unrelated histories.
5. Before parallel work starts, declare the task's intended path set. Two
   actors must not edit the same owned file concurrently. Cross-cutting files
   such as `README*`, `pyproject.toml`, `src/panda_agent/orchestrator.py`, and
   `src/panda_agent/types.py` have one owner at a time.
6. Handoffs consist of commit SHA(s), changed-path list, verification output,
   and remaining risks. Never hand off a stash or an uncommitted working tree.
7. Every behavioral change is test-first where practical and leaves the suite
   at least as green as the branch baseline. Never weaken a gate or delete a
   regression test to make a change pass.
8. Commits are small, bisectable, and Conventional Commits. Do not mix a remote
   sync, refactor, feature, and documentation rewrite in one commit.
9. Run `python scripts/harness.py doctor --fetch` before work and
   `python scripts/harness.py verify` before handoff. A non-zero result blocks
   integration.
10. Local hooks are not a security boundary. The GitHub ruleset described in
    the collaboration protocol is required to prevent direct pushes and force
    rewrites on the server.

## Product invariants

- Self-evolution is falsifiable: candidate changes must pass original tests and
  any configured behavior benchmark before promotion.
- Candidate verification fails closed. Missing isolation is a rejection, not a
  reason to accept a patch.
- Graph memory works after a fresh clone without a private sibling repository
  or unpublished service.
- Memory records structured, actionable outcomes; it does not store raw chat
  dumps indiscriminately.
- Source, tests, configuration, and verification harness are treated as one
  trust boundary. Candidate code cannot rewrite its own acceptance criteria.

## Required commands

```bash
python scripts/harness.py install
python scripts/harness.py doctor --fetch
python -m pytest tests/ -q -m "not slow" --tb=short
python -m ruff check src tests scripts
python scripts/harness.py verify
```

If an upstream defect makes the baseline red, stop feature work, record the
exact pre-existing failures, and repair the baseline in a dedicated commit.

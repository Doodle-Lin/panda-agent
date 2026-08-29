#!/usr/bin/env python3
"""Executable collaboration harness for PandaAgent contributors."""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTECTED_BRANCHES = {"main", "master"}
CONVENTIONAL_SUBJECT = re.compile(
    r"^(build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)"
    r"(?:\([a-z0-9._/-]+\))?!?: .{1,72}$"
)
CONFLICT_MARKER = re.compile(r"^(?:<<<<<<< |=======|>>>>>>> )", re.MULTILINE)


class GitError(RuntimeError):
    """Raised when a required Git operation fails."""


def git(*args: str, check: bool = True, input_text: str | None = None) -> str:
    """Run Git in the repository and return stripped stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        input=input_text,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise GitError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def ref_exists(ref: str) -> bool:
    """Return whether a Git revision resolves."""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def is_ancestor(older: str, newer: str) -> bool:
    """Return whether ``older`` is an ancestor of ``newer``."""
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", older, newer],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def changed_paths(revision_range: str) -> set[str]:
    """Return paths changed in a Git revision range."""
    output = git("diff", "--name-only", revision_range)
    return {line for line in output.splitlines() if line}


def conventional_subject(subject: str) -> bool:
    """Validate one Conventional Commit subject."""
    return bool(CONVENTIONAL_SUBJECT.fullmatch(subject.strip()))


def protected_push_refs(update_text: str) -> list[str]:
    """Extract protected destination refs from pre-push hook input."""
    updates = [line.split() for line in update_text.splitlines() if line.strip()]
    return [
        fields[2]
        for fields in updates
        if len(fields) >= 4 and fields[2] in {"refs/heads/main", "refs/heads/master"}
    ]


def conflict_marker_paths(staged: bool = True) -> list[str]:
    """Return text files containing unresolved conflict markers."""
    args = ["diff"]
    if staged:
        args.append("--cached")
    args.extend(["--name-only", "--diff-filter=ACMR"])
    paths = [line for line in git(*args).splitlines() if line]
    conflicts: list[str] = []
    for path in paths:
        try:
            raw = git("show", f":{path}") if staged else (ROOT / path).read_text()
        except (GitError, OSError, UnicodeDecodeError):
            continue
        if CONFLICT_MARKER.search(raw):
            conflicts.append(path)
    return conflicts


@dataclass
class Report:
    """Structured doctor result."""

    branch: str = ""
    base: str = ""
    base_sha: str = ""
    head_sha: str = ""
    issues: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def inspect_repository(
    base: str,
    *,
    allow_dirty: bool = False,
    allow_default: bool = False,
    allow_detached: bool = False,
) -> Report:
    """Inspect branch lineage, cleanliness, and commit policy."""
    report = Report(base=base)
    try:
        report.branch = git("branch", "--show-current")
        report.head_sha = git("rev-parse", "--short=12", "HEAD")
    except GitError as error:
        report.issues.append(str(error))
        return report

    if not report.branch:
        if not allow_detached:
            report.issues.append("detached HEAD is not an allowed development state")
    elif report.branch in PROTECTED_BRANCHES and not allow_default:
        report.issues.append(f"development on protected branch {report.branch!r} is forbidden")

    dirty = git("status", "--porcelain", "--untracked-files=all")
    if dirty and not allow_dirty:
        report.issues.append("working tree is dirty; commit or restore owned changes first")

    unmerged = git("diff", "--name-only", "--diff-filter=U")
    if unmerged:
        report.issues.append(f"unmerged paths: {', '.join(unmerged.splitlines())}")

    if not ref_exists(base):
        report.issues.append(f"base ref {base!r} is unavailable; fetch before continuing")
        return report
    report.base_sha = git("rev-parse", "--short=12", base)

    merge_base = git("merge-base", "HEAD", base, check=False)
    if not merge_base:
        report.issues.append(
            f"HEAD and {base} have no merge base; upstream history may have been rewritten"
        )
        return report

    if not is_ancestor(base, "HEAD"):
        local_paths = changed_paths(f"{merge_base}..HEAD")
        upstream_paths = changed_paths(f"{merge_base}..{base}")
        overlap = sorted(local_paths & upstream_paths)
        detail = f"; overlapping paths: {', '.join(overlap)}" if overlap else ""
        report.issues.append(f"branch does not contain current {base}{detail}")

    if is_ancestor(base, "HEAD"):
        merge_commits = git("rev-list", "--merges", f"{base}..HEAD")
        if merge_commits:
            report.issues.append("task range contains merge commits; rebase to linear history")
        subjects = git("log", "--format=%s", f"{base}..HEAD")
        invalid = [subject for subject in subjects.splitlines() if not conventional_subject(subject)]
        if invalid:
            report.issues.append(f"non-Conventional commit subjects: {invalid}")

    upstream = git(
        "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", check=False
    )
    if upstream and upstream != base and ref_exists(upstream) and not is_ancestor(upstream, "HEAD"):
        report.issues.append(
            f"remote task branch {upstream} is not contained in HEAD; fetch and reconcile first"
        )

    report.notes.append(f"branch={report.branch or '(detached)'} head={report.head_sha}")
    report.notes.append(f"base={base} base_sha={report.base_sha}")
    return report


def print_report(report: Report) -> None:
    """Print a concise doctor report."""
    for note in report.notes:
        print(f"[INFO] {note}")
    for issue in report.issues:
        print(f"[FAIL] {issue}")
    print("[PASS] repository collaboration state" if report.ok else "[BLOCK] harness failed")


def run_check(label: str, command: list[str]) -> bool:
    """Run one verification command with visible output."""
    print(f"\n[RUN] {label}: {' '.join(command)}")
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode == 0:
        print(f"[PASS] {label}")
        return True
    print(f"[FAIL] {label} exited {result.returncode}")
    return False


def install() -> int:
    """Install repository-local hooks and safe Git defaults."""
    settings = {
        "core.hooksPath": ".githooks",
        "pull.ff": "only",
        "fetch.prune": "true",
        "rerere.enabled": "true",
        "rebase.autoStash": "false",
        "push.default": "simple",
    }
    for key, value in settings.items():
        git("config", "--local", key, value)
        print(f"[SET] {key}={value}")
    print("[PASS] local collaboration harness installed")
    return 0


def doctor(args: argparse.Namespace) -> int:
    """Run the repository-state gate."""
    if args.fetch:
        print("[RUN] git fetch --prune origin")
        try:
            git("fetch", "--prune", "origin")
        except GitError as error:
            print(f"[FAIL] {error}")
            return 1
    report = inspect_repository(
        args.base,
        allow_dirty=args.allow_dirty,
        allow_default=args.allow_default,
        allow_detached=args.allow_detached,
    )
    print_report(report)
    return 0 if report.ok else 1


def verify(args: argparse.Namespace) -> int:
    """Run branch policy, compilation, tests, lint, and diff checks."""
    report = inspect_repository(args.base, allow_detached=args.allow_detached)
    print_report(report)
    if not report.ok:
        return 1

    pytest_args = [sys.executable, "-m", "pytest", "tests/", "-q"]
    if not args.full:
        pytest_args.extend(["-m", "not slow"])
    pytest_args.append("--tb=short")
    checks = [
        ("compile", [sys.executable, "-m", "compileall", "-q", "src", "tests", "scripts"]),
        ("tests", pytest_args),
        ("unstaged diff", ["git", "diff", "--check"]),
        ("staged diff", ["git", "diff", "--cached", "--check"]),
    ]
    passed = True
    if importlib.util.find_spec("ruff") is None:
        print("[FAIL] ruff is not installed; run: python -m pip install -e '.[dev]'")
        passed = False
    else:
        checks.insert(
            2,
            ("ruff", [sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"]),
        )
    for label, command in checks:
        if not run_check(label, command):
            passed = False
    return 0 if passed else 1


def hook_pre_commit() -> int:
    """Reject unsafe staged changes before a commit is created."""
    branch = git("branch", "--show-current")
    failures: list[str] = []
    if not branch or branch in PROTECTED_BRANCHES:
        failures.append("commits require a non-protected task branch")
    if git("diff", "--name-only", "--diff-filter=U"):
        failures.append("index contains unmerged paths")
    diff_check = subprocess.run(["git", "diff", "--cached", "--check"], cwd=ROOT)
    if diff_check.returncode != 0:
        failures.append("staged diff check failed")
    markers = conflict_marker_paths(staged=True)
    if markers:
        failures.append(f"staged conflict markers found in: {', '.join(markers)}")
    for failure in failures:
        print(f"[FAIL] {failure}")
    return 1 if failures else 0


def hook_commit_msg(path: str) -> int:
    """Enforce a Conventional Commit subject."""
    subject = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
    if conventional_subject(subject):
        return 0
    print(f"[FAIL] non-Conventional commit subject: {subject!r}")
    print("Expected: type(scope): imperative description")
    return 1


def hook_pre_push(args: argparse.Namespace) -> int:
    """Block protected-branch pushes and unverified task branches."""
    protected = protected_push_refs(sys.stdin.read())
    if protected:
        print(f"[FAIL] direct push to protected branch is forbidden: {protected}")
        return 1
    verify_args = argparse.Namespace(base=args.base, full=False, allow_detached=False)
    return verify(verify_args)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("install", help="install repository-local hooks and Git defaults")

    doctor_parser = subparsers.add_parser("doctor", help="inspect collaboration state")
    doctor_parser.add_argument("--base", default="origin/master")
    doctor_parser.add_argument("--fetch", action="store_true")
    doctor_parser.add_argument("--allow-dirty", action="store_true")
    doctor_parser.add_argument("--allow-default", action="store_true")
    doctor_parser.add_argument("--allow-detached", action="store_true")

    verify_parser = subparsers.add_parser("verify", help="run the handoff gate")
    verify_parser.add_argument("--base", default="origin/master")
    verify_parser.add_argument("--full", action="store_true")
    verify_parser.add_argument("--allow-detached", action="store_true")

    subparsers.add_parser("hook-pre-commit")
    commit_parser = subparsers.add_parser("hook-commit-msg")
    commit_parser.add_argument("path")
    push_parser = subparsers.add_parser("hook-pre-push")
    push_parser.add_argument("remote_name", nargs="?", default="origin")
    push_parser.add_argument("remote_url", nargs="?", default="")
    push_parser.add_argument("--base", default="origin/master")
    return parser


def main() -> int:
    """Run the requested harness action."""
    args = build_parser().parse_args()
    if args.command == "install":
        return install()
    if args.command == "doctor":
        return doctor(args)
    if args.command == "verify":
        return verify(args)
    if args.command == "hook-pre-commit":
        return hook_pre_commit()
    if args.command == "hook-commit-msg":
        return hook_commit_msg(args.path)
    if args.command == "hook-pre-push":
        return hook_pre_push(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

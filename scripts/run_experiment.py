#!/usr/bin/env python3
"""Reproducible self-evolution experiment runner.

This is the scaffolding R1 in the roadmap asks for: a single command that
runs the evolution loop over a benchmark task suite, captures per-round
evidence (score, patch diff, accept/reject reason), and emits a JSON +
markdown report. Without something like this, "the agent improved" stays an
assertion; with it, the claim is checkable.

It also evaluates a held-out test split before and after evolution, so the
report answers the question that actually matters for a self-evolving agent:
did a patch kept on the train tasks also help on tasks the loop never saw?

Design notes
------------
* **Falsifiable by construction.** Train and test tasks come from the same
  YAML suite with deterministic scorers, so a "score went up" claim is a
  number, not an opinion.
* **The repo is left clean.** The evolvable sources (``tools.py`` /
  ``brain.py``) are snapshotted before the run and restored after, so the
  experiment never leaves the working tree dirty.
* **Injectable runner.** ``run_experiment`` accepts a ``runner_factory`` so a
  test can drive it with a mock agent instead of a real LLM -- the same way
  ``benchmark.run_benchmark`` stays LLM-free.
* **No network required to import.** Importing this module never touches the
  LLM; only ``run_experiment`` does, through the injected runner.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

# Allow running as a script (python scripts/run_experiment.py) without an
# installed package by making the repo root importable.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from panda_agent.benchmark import (
    BenchmarkResult,
    BenchmarkTask,
    load_tasks,
    run_benchmark,
)
from panda_agent.config import Config, load_config
from panda_agent.orchestrator import (
    Evaluator,
    Executor,
    Improver,
    run_evolution,
)
from panda_agent.types import Task


# ---------------------------------------------------------------------------
# Evolvable-source snapshot / restore
# ---------------------------------------------------------------------------

_EVOLVABLE = [
    _REPO_ROOT / "src" / "panda_agent" / "tools.py",
    _REPO_ROOT / "src" / "panda_agent" / "brain.py",
]


def _snapshot_evolvable() -> dict[Path, str]:
    return {p: p.read_text(encoding="utf-8") for p in _EVOLVABLE if p.exists()}


def _restore_evolvable(snapshot: dict[Path, str]) -> None:
    for path, content in snapshot.items():
        path.write_text(content, encoding="utf-8")


def _git_diff_evolvable(before: dict[Path, str]) -> str:
    """Unified diff of the evolvable sources against ``before``.

    Uses difflib rather than shelling out to ``git`` so this also works in a
    non-repo directory.
    """
    import difflib

    chunks: list[str] = []
    for path in _EVOLVABLE:
        if not path.exists():
            continue
        old = before.get(path, "")
        new = path.read_text(encoding="utf-8")
        if old == new:
            continue
        diff = difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path.name}",
            tofile=f"b/{path.name}",
        )
        chunks.append("".join(diff))
    return "".join(chunks)


# ---------------------------------------------------------------------------
# Report types
# ---------------------------------------------------------------------------

@dataclass
class RoundRecord:
    round_num: int
    score: float
    patched: bool
    target_reached: bool
    explanation: str = ""
    test_output: str = ""
    reject_reason: str = ""
    diff: str = ""


@dataclass
class TrainRecord:
    task_id: str
    instruction: str
    rounds: list[RoundRecord] = field(default_factory=list)
    final_score: float = 0.0
    total_patches: int = 0
    restored_from_round: int | None = None


@dataclass
class TestSplitRecord:
    task_id: str
    instruction: str
    score_before: float
    score_after: float
    detail_before: str = ""
    detail_after: str = ""


@dataclass
class ExperimentReport:
    timestamp: str
    config_summary: dict[str, Any]
    train_ids: list[str]
    test_ids: list[str]
    train_runs: list[TrainRecord]
    test_split: list[TestSplitRecord]
    test_weighted_before: float
    test_weighted_after: float
    test_delta: float
    noise_mean: float = 0.0
    noise_stdev: float = 0.0
    tolerance: float = 0.0
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class Runner(Protocol):
    def __call__(self, task: Task) -> str: ...


_runner_type = Callable[[Task], str]


def make_react_runner(config: Config) -> _runner_type:
    """Build a runner that drives the real ReAct loop and returns its answer.

    The benchmark scorer sees the agent's final answer text. The ReAct loop
    returns a ``ReActResult`` whose ``answer`` is what the model emitted as
    ``DONE:``; that is the text a deterministic scorer checks against.
    """
    from panda_agent.react import run_react

    def _run(task: Task) -> str:
        result = run_react(task.instruction, config)
        return result.answer or result.error or ""
    return _run


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def _evaluate_split(
    tasks: list[BenchmarkTask],
    runner: _runner_type,
    workspace: Path,
    config: Config | None = None,
) -> BenchmarkResult:
    return run_benchmark(tasks, runner, workspace, config)


def _wire_gate(
    improver: Improver,
    gate_tasks: list[BenchmarkTask],
    runner: _runner_type,
    workspace: Path,
    config: Config,
    tolerance: float,
) -> None:
    """Wire the regression benchmark gate onto an Improver."""
    improver.baseline = run_benchmark(gate_tasks, runner, workspace, config)
    improver.benchmark_gate = lambda: run_benchmark(gate_tasks, runner, workspace, config)
    improver.tolerance = tolerance


def run_experiment(
    config: Config,
    benchmark_tasks: list[BenchmarkTask],
    *,
    train_ids: list[str],
    test_ids: list[str],
    workspace: Path,
    rounds: int = 3,
    target_score: float = 90.0,
    runner_factory: Callable[[Config], _runner_type] | None = None,
    tolerance: float | None = None,
    estimate_noise_runs: int = 0,
    out_dir: Path | None = None,
) -> ExperimentReport:
    """Run a reproducible evolution experiment.

    Train tasks drive the evolution loop; the held-out test tasks are scored
    before and after to measure generalisation. Patches accumulate across
    train tasks (each starts from whatever the previous left on disk). The
    regression gate uses the full train split as its suite, so a patch that
    helps the current task but hurts the others is rejected.

    The held-out "after" measurement is taken while the evolved code is still
    on disk, before the baseline snapshot is restored -- so the repo is left
    clean but the number reflects the exact code the loop produced.
    """
    by_id = {t.id: t for t in benchmark_tasks}
    missing = [i for i in train_ids + test_ids if i not in by_id]
    if missing:
        raise ValueError(f"unknown task ids: {missing}")
    overlap = set(train_ids) & set(test_ids)
    if overlap:
        raise ValueError(f"train/test split must be disjoint, overlap: {sorted(overlap)}")

    train_tasks = [by_id[i] for i in train_ids]
    test_tasks = [by_id[i] for i in test_ids]

    runner_factory = runner_factory or make_react_runner
    runner = runner_factory(config)

    # Run the agent from the workspace root so tools that resolve paths
    # relative to cwd (search_files, list_files, run_command) see the
    # fixtures the benchmark scorers check against. Restored on exit so a
    # run never leaves the caller in a different directory.
    _orig_cwd = os.getcwd()
    os.chdir(str(workspace))
    try:
        return _run_experiment_body(
            config, benchmark_tasks,
            train_tasks=train_tasks, test_tasks=test_tasks,
            train_ids=train_ids, test_ids=test_ids,
            workspace=workspace,
            rounds=rounds, target_score=target_score,
            runner=runner, tolerance=tolerance,
            estimate_noise_runs=estimate_noise_runs,
            out_dir=out_dir,
        )
    finally:
        os.chdir(_orig_cwd)


def _run_experiment_body(
    config: Config,
    benchmark_tasks: list[BenchmarkTask],
    *,
    train_tasks: list[BenchmarkTask],
    test_tasks: list[BenchmarkTask],
    train_ids: list[str],
    test_ids: list[str],
    workspace: Path,
    rounds: int,
    target_score: float,
    runner: _runner_type,
    tolerance: float | None,
    estimate_noise_runs: int,
    out_dir: Path | None,
) -> ExperimentReport:
    """Body of run_experiment, called after cwd has been set to workspace."""

    noise_mean = 0.0
    noise_stdev = 0.0
    if estimate_noise_runs and estimate_noise_runs >= 2:
        from panda_agent.benchmark import estimate_noise
        noise_mean, noise_stdev = estimate_noise(
            train_tasks, runner, workspace, config, runs=estimate_noise_runs
        )
    if tolerance is None:
        tolerance = max(2.0, 2 * noise_stdev) if noise_stdev > 0 else 2.0

    # Held-out baseline: score the test split BEFORE any evolution.
    test_before = _evaluate_split(test_tasks, runner, workspace, config)
    test_before_by_id = test_before.by_id()

    baseline_snapshot = _snapshot_evolvable()
    train_runs: list[TrainRecord] = []
    test_after: BenchmarkResult | None = None

    try:
        for task in train_tasks:
            pre_round_snapshot = _snapshot_evolvable()

            executor = Executor(config)
            evaluator = Evaluator(
                config,
                benchmark_tasks=train_tasks,
                workspace=workspace,
            )
            improver = Improver(config)
            _wire_gate(improver, train_tasks, runner, workspace, config, tolerance)

            evolution = run_evolution(
                executor=executor,
                evaluator=evaluator,
                improver=improver,
                task=Task(instruction=task.instruction, benchmark_id=task.id),
                target_score=target_score,
                max_rounds=rounds,
                on_event=lambda _ev: None,
                config=config,
            )

            # Capture the diff the loop actually applied (post-evolution minus
            # pre-evolution for this train task). Empty when nothing was kept.
            post_diff = _git_diff_evolvable(pre_round_snapshot)

            rounds_records: list[RoundRecord] = []
            for r in evolution.rounds:
                score = r.evaluation.score if r.evaluation else 0.0
                imp = r.improvement
                patched = bool(imp and imp.patched)
                explanation = imp.explanation if imp else ""
                test_output = imp.test_output if imp else ""
                reject_reason = ""
                if imp and not imp.patched and test_output:
                    reject_reason = test_output
                rounds_records.append(RoundRecord(
                    round_num=r.round_num,
                    score=score,
                    patched=patched,
                    target_reached=evolution.target_reached,
                    explanation=explanation,
                    test_output=test_output[-400:],
                    reject_reason=reject_reason[-400:],
                    diff=post_diff if r.round_num == evolution.rounds[-1].round_num else "",
                ))

            train_runs.append(TrainRecord(
                task_id=task.id,
                instruction=task.instruction,
                rounds=rounds_records,
                final_score=evolution.final_score,
                total_patches=evolution.total_patches,
                restored_from_round=evolution.restored_from_round,
            ))

        # Held-out "after": the on-disk code is now the evolved state (the
        # last train task's best round, as restored by run_evolution). Score
        # the test split against THIS code, before restoring the baseline.
        test_after = _evaluate_split(test_tasks, runner, workspace, config)
    finally:
        _restore_evolvable(baseline_snapshot)

    test_after_by_id = test_after.by_id()
    test_split_records: list[TestSplitRecord] = []
    for t in test_tasks:
        before = test_before_by_id.get(t.id)
        after = test_after_by_id.get(t.id)
        test_split_records.append(TestSplitRecord(
            task_id=t.id,
            instruction=t.instruction,
            score_before=before.score if before else 0.0,
            score_after=after.score if after else 0.0,
            detail_before=before.detail if before else "",
            detail_after=after.detail if after else "",
        ))

    report = ExperimentReport(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        config_summary={
            "model": config.model.default,
            "base_url": config.model.base_url,
            "max_turns": config.agent.max_turns,
            "max_retries": config.agent.max_retries,
            "rounds": rounds,
            "target_score": target_score,
        },
        train_ids=train_ids,
        test_ids=test_ids,
        train_runs=train_runs,
        test_split=test_split_records,
        test_weighted_before=test_before.weighted_score,
        test_weighted_after=test_after.weighted_score,
        test_delta=test_after.weighted_score - test_before.weighted_score,
        noise_mean=noise_mean,
        noise_stdev=noise_stdev,
        tolerance=tolerance,
    )

    if out_dir is not None:
        write_report(report, out_dir)
    return report


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_report(report: ExperimentReport, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(_to_jsonable(report), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (out_dir / "report.md").write_text(_to_markdown(report), encoding="utf-8")


def _to_jsonable(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _to_jsonable(v) for k, v in asdict(obj).items()}  # type: ignore[arg-type]
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj


def _to_markdown(report: ExperimentReport) -> str:
    lines: list[str] = []
    lines.append(f"# Self-Evolution Experiment — {report.timestamp}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Train tasks: `{', '.join(report.train_ids)}`")
    lines.append(f"- Held-out tasks: `{', '.join(report.test_ids)}`")
    lines.append(f"- Tolerance: {report.tolerance:.2f}")
    if report.noise_stdev > 0:
        lines.append(
            f"- Measured noise: mean={report.noise_mean:.1f}, "
            f"stdev={report.noise_stdev:.1f} (tolerance = 2*stdev)"
        )
    lines.append("")
    lines.append("### Held-out generalisation")
    lines.append("")
    lines.append(f"- Weighted score **before**: {report.test_weighted_before:.1f}")
    lines.append(f"- Weighted score **after**: {report.test_weighted_after:.1f}")
    delta = report.test_delta
    lines.append(f"- Delta: **{('+' if delta >= 0 else '')}{delta:.1f}**")
    lines.append("")
    lines.append("| Task | Before | After | Delta |")
    lines.append("|---|---:|---:|---:|")
    for t in report.test_split:
        d = t.score_after - t.score_before
        lines.append(
            f"| {t.task_id} | {t.score_before:.0f} | {t.score_after:.0f} | "
            f"{'+' if d >= 0 else ''}{d:.0f} |"
        )
    lines.append("")
    lines.append("## Train runs (per task)")
    lines.append("")
    for tr in report.train_runs:
        lines.append(f"### {tr.task_id}")
        lines.append("")
        lines.append(
            f"- Final score: {tr.final_score:.0f} | Patches kept: "
            f"{tr.total_patches} | Restored from round: {tr.restored_from_round}"
        )
        lines.append("")
        lines.append("| Round | Score | Patched | Target | Reject reason |")
        lines.append("|---:|---:|:---:|:---:|---|")
        for r in tr.rounds:
            reason = r.reject_reason.replace("|", "\\|").replace("\n", " ")
            if len(reason) > 80:
                reason = reason[:77] + "..."
            lines.append(
                f"| {r.round_num} | {r.score:.0f} | "
                f"{'yes' if r.patched else 'no'} | "
                f"{'yes' if r.target_reached else 'no'} | {reason} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_split(task_ids: list[str], test_ratio: float) -> tuple[list[str], list[str]]:
    """Naive split: last `test_ratio` of tasks (by YAML order) are held out."""
    n = len(task_ids)
    n_test = max(1, round(n * test_ratio))
    return task_ids[:-n_test] or task_ids[:1], task_ids[-n_test:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_experiment",
        description="Run a reproducible self-evolution experiment.",
    )
    parser.add_argument(
        "--tasks",
        type=Path,
        default=_REPO_ROOT / "benchmarks" / "tasks.yaml",
        help="Benchmark task suite YAML.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=_REPO_ROOT / "benchmarks",
        help="Workspace root for deterministic scorers (the fixtures dir).",
    )
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--target", type=float, default=90.0)
    parser.add_argument(
        "--train", nargs="*", default=None,
        help="Train task ids. If omitted, derived from --test-ratio.",
    )
    parser.add_argument(
        "--test", nargs="*", default=None,
        help="Held-out task ids. If omitted, derived from --test-ratio.",
    )
    parser.add_argument(
        "--test-ratio", type=float, default=0.4,
        help="Fraction of tasks to hold out when --train/--test are omitted.",
    )
    parser.add_argument(
        "--tolerance", type=float, default=None,
        help="Regression-gate tolerance. Defaults to 2.0 or 2*stdev.",
    )
    parser.add_argument(
        "--estimate-noise", type=int, default=0,
        help="If >1, measure run-to-run variance and set tolerance = 2*stdev.",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Output directory (default: experiments/<timestamp>).",
    )
    args = parser.parse_args(argv)

    config = load_config()
    if not config.model.api_key:
        print(
            "WARNING: no model API key configured. The real ReAct runner will "
            "fail; set PANDA_API_KEY or pass a config with model.api_key.",
            file=sys.stderr,
        )

    tasks = load_tasks(args.tasks)
    all_ids = [t.id for t in tasks]
    train_ids = args.train or []
    test_ids = args.test or []
    if not train_ids and not test_ids:
        train_ids, test_ids = _default_split(all_ids, args.test_ratio)
    elif not train_ids:
        train_ids = [i for i in all_ids if i not in test_ids]
    elif not test_ids:
        test_ids = [i for i in all_ids if i not in train_ids]

    out_dir = args.out or (_REPO_ROOT / "experiments" / datetime.now().strftime("%Y%m%d_%H%M%S"))
    # out_dir is resolved against the repo root before run_experiment chdir's
    # into the workspace, so the report lands in the repo's experiments/ dir
    # regardless of where the agent runs from.
    out_dir = out_dir.resolve()

    report = run_experiment(
        config,
        tasks,
        train_ids=train_ids,
        test_ids=test_ids,
        workspace=args.workspace,
        rounds=args.rounds,
        target_score=args.target,
        tolerance=args.tolerance,
        estimate_noise_runs=args.estimate_noise,
        out_dir=out_dir,
    )

    print(f"\nExperiment report written to {out_dir}")
    print(
        f"Held-out weighted score: {report.test_weighted_before:.1f} -> "
        f"{report.test_weighted_after:.1f} (delta {report.test_delta:+.1f})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

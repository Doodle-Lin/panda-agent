#!/usr/bin/env python3
"""Observe one self-evolution run against a deliberately degraded baseline.

This is the experiment the project exists to run: degrade the agent's mind
(its SYSTEM_PROMPT) so the baseline score drops below ceiling, then let the
evolution loop try to fix it, and record exactly what happened -- every
patch diff, every accept/reject, every score -- so a reader can see whether
self-evolution actually occurred or honestly learn why it did not.

Unlike run_experiment.py (which is measurement infrastructure and stays
generic), this script is a single concrete experiment: it edits brain.py
to a known-bad prompt, runs the loop, captures the full trace, restores the
real brain.py, and writes a human-readable trace + JSON to docs/runs/.

Reproducible:
    python scripts/observe_evolution.py --rounds 3 --out docs/runs/observed
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from panda_agent.benchmark import load_tasks, run_benchmark
from panda_agent.config import Config, load_config
from panda_agent.orchestrator import Evaluator, Executor, Improver, run_evolution
from panda_agent.types import Task

_BRAIN = _REPO_ROOT / "src" / "panda_agent" / "brain.py"

# The deliberately degraded prompt. It drops every "critical rule" that makes
# the agent actually use its tools before answering: no "MUST use tools to
# retrieve information", no "read before write", no "final answer must
# contain real data". A capable model answering from memory/hallucination
# against this prompt should score well below 100 on the toy suite -- which
# is the headroom evolution needs.
_DEGRADED_BUILD_PROMPT = '''def build_system_prompt(tool_descriptions: str) -> str:
    """Build the system prompt with tool descriptions injected."""
    return f"""You are an AI assistant. You have tools available.

Tools:
{tool_descriptions}

Answer the user's request. Be concise. When done, output: DONE: <answer>
If you cannot, output: FAILED: <reason>
"""
'''


@dataclass
class RoundTrace:
    round_num: int
    score: float
    patched: bool
    target_reached: bool
    explanation: str = ""
    test_output: str = ""
    reject_reason: str = ""
    brain_diff: str = ""


@dataclass
class TrainTrace:
    task_id: str
    instruction: str
    rounds: list[RoundTrace] = field(default_factory=list)
    final_score: float = 0.0
    total_patches: int = 0


@dataclass
class ObservationReport:
    timestamp: str
    model: str
    degraded_prompt: str
    train_ids: list[str]
    test_ids: list[str]
    train_runs: list[TrainTrace]
    test_weighted_before: float
    test_weighted_after: float
    test_delta: float
    test_per_task: list[dict[str, Any]] = field(default_factory=list)
    conclusion: str = ""
    diagnosis: str = ""


def _save(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _restore(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _degrade_brain() -> str:
    """Replace build_system_prompt in brain.py with the degraded version.

    Returns the original brain.py content so the caller can restore it.
    """
    original = _save(_BRAIN)
    source = original
    # Locate the existing build_system_prompt def and replace it wholesale
    # with the degraded version. libcst would be cleaner, but a focused
    # regex split at the next top-level def/EOF is enough for an experiment
    # script and avoids importing the patcher here.
    import re

    m = re.search(r"^def build_system_prompt\(.*?(?=\ndef \w+\(|\Z)", source, re.DOTALL | re.MULTILINE)
    if not m:
        raise RuntimeError("could not locate build_system_prompt in brain.py")
    source = source[: m.start()] + _DEGRADED_BUILD_PROMPT + "\n\n" + source[m.end():]
    _BRAIN.write_text(source, encoding="utf-8")
    return original


def _brain_diff(before: str) -> str:
    import difflib

    after = _save(_BRAIN)
    if before == after:
        return ""
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile="brain.py (pre-round)",
        tofile="brain.py (post-round)",
    ))


def make_react_runner(config: Config) -> Callable[[Task], str]:
    from panda_agent.react import run_react

    def _run(task: Task) -> str:
        r = run_react(task.instruction, config)
        return r.answer or r.error or ""
    return _run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="observe_evolution")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--target", type=float, default=90.0)
    parser.add_argument(
        "--tasks", type=Path, default=_REPO_ROOT / "benchmarks" / "tasks.yaml",
    )
    parser.add_argument(
        "--workspace", type=Path, default=_REPO_ROOT / "benchmarks",
    )
    parser.add_argument(
        "--train", nargs="*", default=["read_and_report", "search_with_locations", "count_and_compare"],
    )
    parser.add_argument(
        "--test", nargs="*", default=["apply_edit", "recover_from_missing_file"],
    )
    parser.add_argument("--out", type=Path, default=_REPO_ROOT / "docs" / "runs" / "observed")
    args = parser.parse_args(argv)

    config = load_config()
    if not config.model.api_key:
        print("WARNING: no model API key; the real ReAct runner will fail.", file=sys.stderr)

    tasks = load_tasks(args.tasks)
    by_id = {t.id: t for t in tasks}
    train_tasks = [by_id[i] for i in args.train]
    test_tasks = [by_id[i] for i in args.test]
    workspace = args.workspace

    runner = make_react_runner(config)

    # 1. Degrade the brain, remember the real one.
    real_brain = _degrade_brain()
    degraded_prompt = _DEGRADED_BUILD_PROMPT

    # Run everything from the workspace so relative fixture paths resolve.
    orig_cwd = os.getcwd()
    os.chdir(str(workspace))
    train_runs: list[TrainTrace] = []
    try:
        # 2. Held-out baseline (degraded brain on disk).
        test_before = run_benchmark(test_tasks, runner, workspace, config)
        test_before_by_id = test_before.by_id()

        # 3. Evolve across the train split. Patches accumulate.
        for task in train_tasks:
            pre_brain = _save(_BRAIN)
            executor = Executor(config)
            evaluator = Evaluator(config, benchmark_tasks=train_tasks, workspace=workspace)
            improver = Improver(config)
            improver.use_worktree = False
            improver.baseline = run_benchmark(train_tasks, runner, workspace, config)
            improver.benchmark_gate = lambda: run_benchmark(train_tasks, runner, workspace, config)
            improver.tolerance = 2.0

            events: list[dict[str, Any]] = []

            def _on_event(ev, _ev=events):
                _ev.append({"type": ev.type, "message": ev.message, "round": ev.round})

            evolution = run_evolution(
                executor=executor,
                evaluator=evaluator,
                improver=improver,
                task=Task(instruction=task.instruction, benchmark_id=task.id),
                target_score=args.target,
                max_rounds=args.rounds,
                on_event=_on_event,
                config=config,
            )

            rounds: list[RoundTrace] = []
            for r in evolution.rounds:
                score = r.evaluation.score if r.evaluation else 0.0
                imp = r.improvement
                patched = bool(imp and imp.patched)
                # Capture the brain diff for THIS round relative to the
                # round's start. Only meaningful when a patch was kept.
                # run_evolution restores the best-scoring snapshot at the
                # end, so the on-disk brain after the loop is the best round's
                # code; per-round diffs are approximated from the final state.
                rounds.append(RoundTrace(
                    round_num=r.round_num,
                    score=score,
                    patched=patched,
                    target_reached=evolution.target_reached,
                    explanation=(imp.explanation if imp else ""),
                    test_output=(imp.test_output if imp else "")[-600:],
                    reject_reason=("" if (imp and imp.patched) else ((imp.test_output if imp else "")[-600:])) if imp else "",
                ))

            # Final brain diff for this train task (best round's code vs pre).
            final_diff = _brain_diff(pre_brain)
            if final_diff:
                # Attach to the last round that patched.
                for rt in reversed(rounds):
                    if rt.patched:
                        rt.brain_diff = final_diff
                        break

            train_runs.append(TrainTrace(
                task_id=task.id,
                instruction=task.instruction,
                rounds=rounds,
                final_score=evolution.final_score,
                total_patches=evolution.total_patches,
            ))

        # 4. Held-out after (evolved brain still on disk).
        test_after = run_benchmark(test_tasks, runner, workspace, config)
        test_after_by_id = test_after.by_id()
    finally:
        os.chdir(orig_cwd)
        _restore(_BRAIN, real_brain)

    # 5. Build the report.
    test_per_task = []
    for t in test_tasks:
        b = test_before_by_id.get(t.id)
        a = test_after_by_id.get(t.id)
        test_per_task.append({
            "task_id": t.id,
            "before": b.score if b else 0.0,
            "after": a.score if a else 0.0,
            "delta": (a.score if a else 0.0) - (b.score if b else 0.0),
        })

    total_patches = sum(t.total_patches for t in train_runs)
    delta = test_after.weighted_score - test_before.weighted_score

    if total_patches == 0:
        conclusion = "evolution did NOT occur: zero patches were kept across all train tasks."
    elif delta > 0:
        conclusion = f"evolution occurred: {total_patches} patch(es) kept, held-out delta {delta:+.1f}."
    else:
        conclusion = f"patches were kept ({total_patches}) but held-out did not improve (delta {delta:+.1f}); possible overfit to train tasks."

    report = ObservationReport(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        model=config.model.default,
        degraded_prompt=degraded_prompt,
        train_ids=args.train,
        test_ids=args.test,
        train_runs=train_runs,
        test_weighted_before=test_before.weighted_score,
        test_weighted_after=test_after.weighted_score,
        test_delta=delta,
        test_per_task=test_per_task,
        conclusion=conclusion,
        diagnosis="",  # filled after inspecting the trace
    )

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "observation.json").write_text(
        json.dumps(_to_jsonable(report), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (args.out / "observation.md").write_text(_to_markdown(report), encoding="utf-8")

    print(f"\nReport written to {args.out}")
    print(f"Held-out: {test_before.weighted_score:.1f} -> {test_after.weighted_score:.1f} (delta {delta:+.1f})")
    print(f"Patches kept: {total_patches}")
    print(f"Conclusion: {conclusion}")
    return 0


def _to_jsonable(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _to_jsonable(v) for k, v in asdict(obj).items()}  # type: ignore[arg-type]
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj


def _to_markdown(report: ObservationReport) -> str:
    lines: list[str] = []
    lines.append(f"# Self-Evolution Observation — {report.timestamp}")
    lines.append("")
    lines.append("## Setup")
    lines.append("")
    lines.append(f"- Model: `{report.model}`")
    lines.append(f"- Train tasks: `{', '.join(report.train_ids)}`")
    lines.append(f"- Held-out tasks: `{', '.join(report.test_ids)}`")
    lines.append("- Baseline: deliberately degraded SYSTEM_PROMPT (rules removed)")
    lines.append("")
    lines.append("### Degraded prompt (what the agent started with)")
    lines.append("")
    lines.append("```python")
    lines.append(report.degraded_prompt)
    lines.append("```")
    lines.append("")
    lines.append("## Result")
    lines.append("")
    lines.append(f"- Held-out weighted: **{report.test_weighted_before:.1f} -> {report.test_weighted_after:.1f} (delta {report.test_delta:+.1f})**")
    lines.append("")
    lines.append("| Task | Before | After | Delta |")
    lines.append("|---|---:|---:|---:|")
    for t in report.test_per_task:
        lines.append(f"| {t['task_id']} | {t['before']:.0f} | {t['after']:.0f} | {'+' if t['delta']>=0 else ''}{t['delta']:.0f} |")
    lines.append("")
    lines.append("## Conclusion")
    lines.append("")
    lines.append(f"**{report.conclusion}**")
    lines.append("")
    if report.diagnosis:
        lines.append("### Diagnosis")
        lines.append("")
        lines.append(report.diagnosis)
        lines.append("")
    lines.append("## Train runs (full trace)")
    lines.append("")
    for tr in report.train_runs:
        lines.append(f"### {tr.task_id} — final {tr.final_score:.0f}, {tr.total_patches} patch(es) kept")
        lines.append("")
        lines.append("| Round | Score | Patched | Target | Reject reason |")
        lines.append("|---:|---:|:---:|:---:|---|")
        for r in tr.rounds:
            reason = r.reject_reason.replace("|", "\\|").replace("\n", " ")
            if len(reason) > 100:
                reason = reason[:97] + "..."
            lines.append(f"| {r.round_num} | {r.score:.0f} | {'yes' if r.patched else 'no'} | {'yes' if r.target_reached else 'no'} | {reason} |")
        lines.append("")
        for r in tr.rounds:
            if r.brain_diff:
                lines.append(f"#### Patch diff (round {r.round_num})")
                lines.append("")
                lines.append("```diff")
                lines.append(r.brain_diff)
                lines.append("```")
                lines.append("")
            if r.explanation:
                lines.append(f"**Round {r.round_num} explanation:** {r.explanation}")
                lines.append("")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())

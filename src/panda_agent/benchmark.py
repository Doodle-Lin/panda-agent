"""Regression benchmark: the gate that makes evolution falsifiable.

The loop's original gate was "do the unit tests still pass?". That answers
whether the code is *broken*, not whether the agent got *better* -- a patch
can pass every test while degrading real task performance, and nothing
caught it.

This module runs a fixed task suite and produces a comparable score, so a
patch can be required to not regress. Two design constraints matter:

**Prefer deterministic scorers.** ``exact_match`` and ``file_state`` are
reproducible; ``llm_judge`` is not. LLM-judge noise propagates directly into
the accept/reject decision, so it is a last resort for genuinely open-ended
tasks.

**Tolerance is not optional.** The same code scores differently across runs
because the agent is stochastic. Rejecting any decrease would discard good
patches on noise alone; see :func:`estimate_noise`.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

import yaml

from .config import Config
from .llm import call_llm
from .parsing import parse_json_object
from .types import Task


ScorerName = Literal["exact_match", "file_state", "llm_judge"]


def _as_list(value: Any) -> list[str]:
    """Normalise a scalar-or-list expectation field into a list.

    YAML lets ``contains: port = 9000`` be written as a bare string, and
    iterating that yields single characters -- a check that passes almost
    vacuously. Normalising here keeps both spellings meaningful.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


# ---------------------------------------------------------------------------
# Task definitions
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkTask:
    id: str
    instruction: str
    scorer: ScorerName = "exact_match"
    expected: dict[str, Any] = field(default_factory=dict)
    rubric: str = ""
    weight: float = 1.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BenchmarkTask:
        missing = {"id", "instruction"} - set(d)
        if missing:
            raise ValueError(f"benchmark task missing required field(s): {sorted(missing)}")
        scorer = d.get("scorer", "exact_match")
        if scorer not in ("exact_match", "file_state", "llm_judge"):
            raise ValueError(f"task {d['id']}: unknown scorer {scorer!r}")
        if scorer == "llm_judge" and not d.get("rubric"):
            raise ValueError(f"task {d['id']}: llm_judge requires a 'rubric'")
        weight = float(d.get("weight", 1.0))
        if weight <= 0:
            raise ValueError(f"task {d['id']}: weight must be positive, got {weight}")
        return cls(
            id=str(d["id"]),
            instruction=str(d["instruction"]),
            scorer=scorer,
            expected=d.get("expected") or {},
            rubric=str(d.get("rubric") or ""),
            weight=weight,
        )


def load_tasks(path: Path) -> list[BenchmarkTask]:
    """Load and validate a benchmark suite from YAML."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a list of tasks, got {type(raw).__name__}")
    tasks = [BenchmarkTask.from_dict(d) for d in raw]
    ids = [t.id for t in tasks]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"{path}: duplicate task ids: {sorted(dupes)}")
    return tasks


# ---------------------------------------------------------------------------
# Scorers
# ---------------------------------------------------------------------------

def score_exact_match(task: BenchmarkTask, answer: str, workspace: Path) -> float:
    """Deterministic: does the answer contain / avoid the required strings?"""
    text = (answer or "").lower()
    required = [s.lower() for s in _as_list(task.expected.get("contains"))]
    forbidden = [s.lower() for s in _as_list(task.expected.get("not_contains"))]

    if any(f in text for f in forbidden):
        return 0.0
    if not required:
        return 100.0 if text.strip() else 0.0
    hits = sum(1 for r in required if r in text)
    return 100.0 * hits / len(required)


def score_file_state(task: BenchmarkTask, answer: str, workspace: Path) -> float:
    """Deterministic: is the filesystem in the expected state afterwards?

    Scores the *effect* of the task rather than what the agent claims, which
    is what you want for modification tasks -- an agent can report success
    without having changed anything.
    """
    rel = task.expected.get("file")
    if not rel:
        return 0.0
    target = (workspace / rel).resolve()
    if not target.is_relative_to(workspace.resolve()):
        raise ValueError(f"task {task.id}: expected.file escapes the workspace")
    if not target.exists():
        return 0.0

    content = target.read_text(encoding="utf-8", errors="replace")
    checks: list[bool] = []
    for s in _as_list(task.expected.get("contains")):
        checks.append(s in content)
    for s in _as_list(task.expected.get("not_contains")):
        checks.append(s not in content)
    if not checks:
        return 100.0
    return 100.0 * sum(checks) / len(checks)


_JUDGE_PROMPT = """\
Score how well this agent output satisfies the rubric. Be strict.

## Task
{instruction}

## Rubric
{rubric}

## Agent output
{answer}

Respond with ONLY a JSON object:
{{"score": <0-100>, "reason": "<one sentence>"}}
"""


def score_llm_judge(
    task: BenchmarkTask, answer: str, workspace: Path, config: Config | None = None
) -> float:
    """Non-deterministic fallback for open-ended tasks.

    Returns ``-1.0`` when judging fails, which the caller treats as an
    unusable measurement rather than a zero -- a judge outage is not evidence
    that the agent performed badly.
    """
    if config is None:
        return -1.0
    response = call_llm(
        [{
            "role": "user",
            "content": _JUDGE_PROMPT.format(
                instruction=task.instruction,
                rubric=task.rubric,
                answer=(answer or "")[:4000],
            ),
        }],
        config.model,
    )
    data, err = parse_json_object(response)
    if data is None or "score" not in data:
        return -1.0
    score = data["score"]
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return -1.0
    return float(max(0.0, min(100.0, score)))


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class TaskScore:
    task_id: str
    score: float
    weight: float
    usable: bool = True
    detail: str = ""


@dataclass
class BenchmarkResult:
    scores: list[TaskScore] = field(default_factory=list)

    @property
    def usable_scores(self) -> list[TaskScore]:
        return [s for s in self.scores if s.usable]

    @property
    def weighted_score(self) -> float:
        """Weighted mean over usable measurements only."""
        usable = self.usable_scores
        if not usable:
            return 0.0
        total_weight = sum(s.weight for s in usable)
        return sum(s.score * s.weight for s in usable) / total_weight

    @property
    def complete(self) -> bool:
        """False if any task failed to produce a usable measurement.

        An incomplete benchmark must not be compared against a complete one:
        the missing task may be exactly the one a patch broke.
        """
        return bool(self.scores) and all(s.usable for s in self.scores)

    def by_id(self) -> dict[str, TaskScore]:
        return {s.task_id: s for s in self.scores}

    def diff(self, baseline: BenchmarkResult) -> dict[str, float]:
        """Per-task score deltas against a baseline, worst first."""
        mine, theirs = self.by_id(), baseline.by_id()
        deltas = {
            tid: mine[tid].score - theirs[tid].score
            for tid in mine.keys() & theirs.keys()
            if mine[tid].usable and theirs[tid].usable
        }
        return dict(sorted(deltas.items(), key=lambda kv: kv[1]))

    def regressions(self, baseline: BenchmarkResult, threshold: float = 0.0) -> dict[str, float]:
        return {k: v for k, v in self.diff(baseline).items() if v < -threshold}

    def to_dict(self) -> dict[str, Any]:
        return {
            "weighted_score": round(self.weighted_score, 2),
            "complete": self.complete,
            "tasks": [
                {"id": s.task_id, "score": s.score, "weight": s.weight,
                 "usable": s.usable, "detail": s.detail}
                for s in self.scores
            ],
        }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_benchmark(
    tasks: list[BenchmarkTask],
    runner: Callable[[Task], str],
    workspace: Path,
    config: Config | None = None,
) -> BenchmarkResult:
    """Run every task through ``runner`` and score the outputs.

    ``runner`` maps a Task to the agent's final answer. Injecting it keeps
    this module independent of the ReAct loop, so the benchmark can be tested
    without an LLM.
    """
    result = BenchmarkResult()

    for task in tasks:
        try:
            answer = runner(Task(instruction=task.instruction))
        except Exception as e:
            result.scores.append(TaskScore(
                task.id, 0.0, task.weight, usable=True,
                detail=f"runner raised {type(e).__name__}: {e}",
            ))
            continue

        try:
            if task.scorer == "exact_match":
                score = score_exact_match(task, answer, workspace)
            elif task.scorer == "file_state":
                score = score_file_state(task, answer, workspace)
            else:
                score = score_llm_judge(task, answer, workspace, config)
        except Exception as e:
            result.scores.append(TaskScore(
                task.id, 0.0, task.weight, usable=False,
                detail=f"scorer raised {type(e).__name__}: {e}",
            ))
            continue

        if score < 0:
            result.scores.append(TaskScore(
                task.id, 0.0, task.weight, usable=False,
                detail="scorer could not produce a measurement",
            ))
        else:
            result.scores.append(TaskScore(task.id, score, task.weight, detail=""))

    return result


# ---------------------------------------------------------------------------
# Noise estimation
# ---------------------------------------------------------------------------

def estimate_noise(
    tasks: list[BenchmarkTask],
    runner: Callable[[Task], str],
    workspace: Path,
    config: Config | None = None,
    runs: int = 3,
) -> tuple[float, float]:
    """Measure run-to-run variance on unchanged code.

    Returns ``(mean, stdev)``. Use ``2 * stdev`` as the regression tolerance:
    tighter than that rejects good patches on sampling noise, looser lets
    real degradations through.
    """
    if runs < 2:
        raise ValueError("need at least 2 runs to estimate variance")
    totals = [
        run_benchmark(tasks, runner, workspace, config).weighted_score
        for _ in range(runs)
    ]
    return statistics.mean(totals), statistics.stdev(totals)


# ---------------------------------------------------------------------------
# Verification gate
# ---------------------------------------------------------------------------

@dataclass
class GateResult:
    """Outcome of the full patch-acceptance gate."""

    accepted: bool
    reason: str = ""
    score_before: float = 0.0
    score_after: float = 0.0
    per_task: dict[str, float] = field(default_factory=dict)

    @property
    def delta(self) -> float:
        return self.score_after - self.score_before


def check_no_regression(
    before: BenchmarkResult,
    after: BenchmarkResult,
    tolerance: float = 2.0,
) -> GateResult:
    """Accept a patch only if benchmark performance did not degrade.

    This is the gate that distinguishes "the code still compiles" from "the
    agent still works".
    """
    if not after.complete:
        unusable = [s.task_id for s in after.scores if not s.usable]
        return GateResult(
            accepted=False,
            reason=f"benchmark incomplete, cannot compare (unusable: {unusable})",
            score_before=before.weighted_score,
            score_after=after.weighted_score,
        )

    delta = after.weighted_score - before.weighted_score
    per_task = after.diff(before)

    if delta < -tolerance:
        worst = list(per_task.items())[:3]
        detail = ", ".join(f"{tid} {d:+.1f}" for tid, d in worst)
        return GateResult(
            accepted=False,
            reason=(
                f"regression: {before.weighted_score:.1f} -> "
                f"{after.weighted_score:.1f} ({delta:+.1f}, tolerance {tolerance}); "
                f"worst: {detail}"
            ),
            score_before=before.weighted_score,
            score_after=after.weighted_score,
            per_task=per_task,
        )

    return GateResult(
        accepted=True,
        reason=f"no regression ({delta:+.1f})",
        score_before=before.weighted_score,
        score_after=after.weighted_score,
        per_task=per_task,
    )

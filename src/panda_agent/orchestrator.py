"""Orchestrator ...drives the Executor →Evaluator →Improver loop.

This is the core of the PandaAgent framework. It runs rounds of
execution, evaluation, and improvement until the target score is
reached or max_rounds is exhausted.
"""

from __future__ import annotations

from typing import Callable

from .types import (
    Task,
    ExecutionResult,
    Evaluation,
    ImprovementResult,
    RoundResult,
    EvolutionResult,
    Event,
)
from .executor import Executor
from .evaluator import Evaluator
from .improver import Improver


def run_evolution(
    executor: Executor,
    evaluator: Evaluator,
    improver: Improver,
    task: Task,
    *,
    target_score: float = 95.0,
    max_rounds: int = 3,
    on_event: Callable[[Event], None] | None = None,
) -> EvolutionResult:
    """Run the self-evolution loop.

    Each round:
      1. Executor runs the task.
      2. Evaluator scores the result.
      3. If score >= target_score →stop, success.
      4. If not the last round, Improver patches tool code.
      5. Repeat with improved tools.

    Args:
        executor: Runs the task.
        evaluator: Evaluates the result.
        improver: Improves tool code.
        task: The task to work on.
        target_score: Stop when evaluation score reaches this (0-100).
        max_rounds: Maximum number of rounds.
        on_event: Optional callback for real-time events.

    Returns:
        EvolutionResult with per-round results and final score.
    """
    result = EvolutionResult()
    best_score = 0.0
    total_patches = 0

    def _emit(event_type: str, message: str, round_num: int, data: dict | None = None):
        if on_event:
            on_event(Event(
                type=event_type,
                message=message,
                round=round_num,
                data=data or {},
            ))

    for round_num in range(1, max_rounds + 1):
        round_result = RoundResult(round_num=round_num)

        # Step 1: Execute
        _emit("executor_start", "Executor: running task...", round_num)
        exec_result = executor.execute(task)
        round_result.execution = exec_result
        _emit(
            "executor_done",
            f"Executor finished. Output: {exec_result.output_path}",
            round_num,
        )

        # Step 2: Evaluate
        _emit("evaluator_start", "Evaluator: scoring result...", round_num)
        evaluation = evaluator.evaluate(task, exec_result)
        round_result.evaluation = evaluation
        _emit(
            "evaluator_done",
            f"Score: {evaluation.score:.0f}/100, issues: {evaluation.issues[:2]}",
            round_num,
        )

        # Track best score
        if evaluation.score > best_score:
            best_score = evaluation.score

        # Step 3: Check convergence
        if evaluation.score >= target_score:
            _emit(
                "target_reached",
                f"Target {target_score} reached with score {evaluation.score}",
                round_num,
            )
            result.target_reached = True
            result.rounds.append(round_result)
            break

        # Step 4: Improve (skip on last round ...no point)
        if round_num < max_rounds:
            _emit("improver_start", "Improver: generating code patch...", round_num)
            try:
                improvement = improver.improve(evaluation)
                round_result.improvement = improvement
                if improvement.patched:
                    total_patches += 1
                _emit(
                    "improver_done",
                    f"Patched: {improvement.patched}, Reverted: {improvement.reverted}, "
                    f"Tests: {improvement.tests_passed}, Attempts: {improvement.attempts}",
                    round_num,
                )
            except Exception as e:
                round_result.improvement = ImprovementResult(
                    patched=False,
                    reverted=False,
                    explanation=f"Improver error: {e}",
                )
                _emit("improver_error", f"Improver error: {e}", round_num)
        else:
            _emit("improver_skip", "Last round, skipping improvement", round_num)

        result.rounds.append(round_result)

    result.final_score = best_score
    result.total_patches = total_patches
    result.target_reached = result.target_reached or best_score >= target_score

    _emit(
        "complete",
        f"Done. Rounds: {len(result.rounds)}, Patches: {total_patches}, "
        f"Final score: {best_score:.0f}",
        max_rounds,
    )

    return result

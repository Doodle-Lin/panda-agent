"""Core data types for the EvoAgent framework.

All agents communicate through these structured types,
keeping the framework decoupled from any specific domain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Task — what the Executor works on
# ---------------------------------------------------------------------------

@dataclass
class Task:
    """A unit of work for the Executor.

    Attributes:
        input_path: Path to the input artifact (image, file, etc.).
        instruction: Natural-language instruction for the task.
        metadata: Extra domain-specific context (e.g. fg_regions for P图).
    """

    input_path: str
    instruction: str
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ExecutionResult — what the Executor produces
# ---------------------------------------------------------------------------

@dataclass
class ExecutionResult:
    """Output of an Executor run.

    Attributes:
        output_path: Path to the output artifact.
        tool_calls: List of tools called, with args, in order.
        success: Whether execution completed without errors.
        error: Error message if success is False.
    """

    output_path: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    success: bool = True
    error: str | None = None


# ---------------------------------------------------------------------------
# Evaluation — what the Evaluator produces
# ---------------------------------------------------------------------------

@dataclass
class Evaluation:
    """Structured evaluation of an ExecutionResult.

    Attributes:
        score: Overall quality score (0-100).
        issues: List of identified problems.
        root_cause: Analysis of why issues occur.
        suggested_changes: Concrete suggestions for the Improver.
        dimensions: Optional per-dimension scores (e.g. sharpness, blur_quality).
    """

    score: float
    issues: list[str] = field(default_factory=list)
    root_cause: str = ""
    suggested_changes: str = ""
    dimensions: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ImprovementResult — what the Improver produces
# ---------------------------------------------------------------------------

@dataclass
class ImprovementResult:
    """Result of an Improver attempt.

    Attributes:
        patched: Whether a code patch was applied.
        reverted: Whether the patch was reverted due to test failure.
        tests_passed: Whether tests passed after patching.
        diff: The applied diff (empty if no patch).
        explanation: Why the change was made.
        test_output: Last few lines of pytest output.
        attempts: Number of LLM calls made (including retries).
    """

    patched: bool = False
    reverted: bool = False
    tests_passed: bool = False
    diff: str = ""
    explanation: str = ""
    test_output: str = ""
    attempts: int = 0


# ---------------------------------------------------------------------------
# EvolutionResult — final output of the Orchestrator
# ---------------------------------------------------------------------------

@dataclass
class RoundResult:
    """One round of the evolution loop."""

    round_num: int
    execution: ExecutionResult | None = None
    evaluation: Evaluation | None = None
    improvement: ImprovementResult | None = None


@dataclass
class EvolutionResult:
    """Final result of run_evolution().

    Attributes:
        rounds: Per-round results.
        final_score: Best score achieved.
        total_patches: Number of successful code patches.
        target_reached: Whether target_score was met.
    """

    rounds: list[RoundResult] = field(default_factory=list)
    final_score: float = 0.0
    total_patches: int = 0
    target_reached: bool = False


# ---------------------------------------------------------------------------
# Event — for real-time monitoring
# ---------------------------------------------------------------------------

@dataclass
class Event:
    """An event emitted during the evolution loop.

    Attributes:
        type: Event type string (e.g. "executor_start", "qa_done").
        message: Human-readable message.
        round: Round number (1-based).
        data: Optional structured payload.
    """

    type: str
    message: str
    round: int = 0
    data: dict[str, Any] = field(default_factory=dict)

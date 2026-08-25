"""Core data types for PandaAgent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Task:
    input_path: str = ""
    instruction: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    output_path: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    success: bool = True
    error: str | None = None


@dataclass
class Evaluation:
    score: float = 0.0
    issues: list[str] = field(default_factory=list)
    root_cause: str = ""
    suggested_changes: str = ""
    dimensions: dict[str, float] = field(default_factory=dict)


@dataclass
class ImprovementResult:
    patched: bool = False
    reverted: bool = False
    tests_passed: bool = False
    diff: str = ""
    explanation: str = ""
    test_output: str = ""
    attempts: int = 0
    score_after: float = 0.0


@dataclass
class RoundResult:
    round_num: int
    execution: ExecutionResult | None = None
    evaluation: Evaluation | None = None
    improvement: ImprovementResult | None = None


@dataclass
class EvolutionResult:
    rounds: list[RoundResult] = field(default_factory=list)
    final_score: float = 0.0
    total_patches: int = 0
    target_reached: bool = False


@dataclass
class Event:
    type: str
    message: str
    round: int = 0
    data: dict[str, Any] = field(default_factory=dict)

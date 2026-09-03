"""Core data types for PandaAgent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Task:
    input_path: str = ""
    instruction: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    # Optional explicit binding to a BenchmarkTask.id. When set, the Evaluator
    # uses the named benchmark task's deterministic scorer directly instead of
    # substring-matching the instruction text -- so a real-world task phrased
    # differently from the benchmark still gets the objective scorer, and a
    # vaguely-worded task does not accidentally match the wrong benchmark.
    benchmark_id: str = ""


@dataclass
class ExecutionResult:
    output_path: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    success: bool = True
    error: str | None = None
    trace: "ExecutionTrace | None" = None


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


# ---------------------------------------------------------------------------
# Self-evolution types: ExecutionTrace, ErrorRecord, LearningResult
# ---------------------------------------------------------------------------

@dataclass
class TurnRecord:
    """One turn in the ReAct loop."""
    turn: int = 0
    reasoning: str = ""          # LLM thinking process
    action: str = ""            # TOOL_CALL / DONE / FAILED
    action_args: dict[str, Any] = field(default_factory=dict)
    tool_result: str = ""
    error: str = ""             # Error encountered this turn (if any)
    self_repaired: bool = False  # Did self-repair activate this turn?
    repair_strategy: str = ""    # What strategy was used to recover


@dataclass
class ExecutionTrace:
    """Full trace of a task execution — used by Learner to extract lessons.

    Captures every turn, every error, every self-repair, and every retry.
    This is the raw material for Level 2 (post-task learning).
    """
    turns: list[TurnRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # All errors encountered
    self_repairs: list[str] = field(default_factory=list)  # Repair strategies used
    retries: int = 0             # How many times the agent retried
    total_turns: int = 0
    final_success: bool = False
    task: str = ""

    def add_error(self, error: str):
        self.errors.append(error)

    def add_repair(self, strategy: str):
        self.self_repairs.append(strategy)


@dataclass
class ErrorRecord:
    """A persistent error record — stored in memory for cross-task learning.

    When the same error pattern appears ≥3 times across tasks, it triggers
    Level 3 structural evolution (source code patching).
    """
    error_pattern: str = ""     # Normalized error description (the pattern)
    task_context: str = ""     # What task triggered this error
    occurrences: int = 1        # How many times this pattern has been seen
    first_seen: str = ""       # ISO timestamp
    last_seen: str = ""        # ISO timestamp
    attempted_fixes: list[str] = field(default_factory=list)  # What was tried
    resolved: bool = False     # Was this resolved by a patch?


@dataclass
class LearningResult:
    """Result of Level 2 post-task learning."""
    lessons: list[str] = field(default_factory=list)  # Lessons extracted
    memory_written: bool = False  # Were lessons written to memory?
    error_patterns: list[str] = field(default_factory=list)  # Recurring patterns found
    trigger_evolution: bool = False  # Should Level 3 be triggered?
    trigger_reason: str = ""  # Why Level 3 should trigger


@dataclass
class RoundResult:
    round_num: int
    execution: ExecutionResult | None = None
    evaluation: Evaluation | None = None
    improvement: ImprovementResult | None = None
    learning: LearningResult | None = None


@dataclass
class EvolutionResult:
    rounds: list[RoundResult] = field(default_factory=list)
    final_score: float = 0.0
    total_patches: int = 0
    total_lessons: int = 0
    target_reached: bool = False
    # Set when the loop rewound the evolvable sources to an earlier, better
    # scoring state. None means the final on-disk code is the last round's.
    restored_from_round: int | None = None


@dataclass
class Event:
    type: str
    message: str
    round: int = 0
    data: dict[str, Any] = field(default_factory=dict)

"""Evaluator — inspects execution results and produces structured evaluation.

The base implementation is abstract; concrete plugins subclass this
to implement ``evaluate()``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .types import Task, ExecutionResult, Evaluation


class Evaluator(ABC):
    """Abstract Evaluator — scores the result and reports issues.

    Concrete implementations (e.g. VLMEvaluator for image editing) override
    ``evaluate()``.  The framework calls this once per round, after the
    Executor.
    """

    @abstractmethod
    def evaluate(self, task: Task, result: ExecutionResult) -> Evaluation:
        """Evaluate the execution result.

        Args:
            task: The original task.
            result: What the Executor produced.

        Returns:
            Evaluation with score (0-100), issues, root_cause, suggested_changes.
        """
        ...

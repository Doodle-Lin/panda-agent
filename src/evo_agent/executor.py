"""Executor — runs tools to accomplish a task.

The base implementation is abstract; concrete plugins subclass this
to implement ``execute()``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .types import Task, ExecutionResult


class Executor(ABC):
    """Abstract Executor — runs a task and produces a result.

    Concrete implementations (e.g. PhotoEditExecutor) override ``execute()``.
    The framework calls this once per round.
    """

    @abstractmethod
    def execute(self, task: Task) -> ExecutionResult:
        """Execute the task and return the result.

        Args:
            task: Contains input_path, instruction, metadata.

        Returns:
            ExecutionResult with output_path and tool_calls.
        """
        ...

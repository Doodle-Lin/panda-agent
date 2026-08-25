"""PandaAgent ...Generic 3-Agent Self-Evolution Framework.

Three agents form a closed loop:
  Executor   →runs tools to accomplish a task
  Evaluator  →scores the result and reports issues
  Improver   →patches tool code based on evaluation, tests, keeps/reverts

The Orchestrator drives the loop until target_score or max_rounds.
"""

from .types import (
    Task,
    ExecutionResult,
    Evaluation,
    ImprovementResult,
    EvolutionResult,
    Event,
)
from .executor import Executor
from .evaluator import Evaluator
from .improver import Improver
from .orchestrator import run_evolution

__version__ = "0.1.0"
__all__ = [
    "Task",
    "ExecutionResult",
    "Evaluation",
    "ImprovementResult",
    "EvolutionResult",
    "Event",
    "Executor",
    "Evaluator",
    "Improver",
    "run_evolution",
]

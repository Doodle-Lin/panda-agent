"""Photo-Edit plugin for EvoAgent — wraps the existing photo-edit-agent project.

This plugin demonstrates how to create concrete Executor/Evaluator/Improver
implementations for the EvoAgent framework.

It wraps the existing photo-edit-agent codebase:
  - Executor: calls the ReActAgent to edit images
  - Evaluator: uses VLM (Qwen3-VL-235B) to compare original vs edited
  - Improver: uses LLM (GLM52RJPT) to patch tools.py based on VLM evaluation
"""

from .executor import PhotoEditExecutor
from .evaluator import PhotoEditEvaluator
from .improver import PhotoEditImprover

__all__ = [
    "PhotoEditExecutor",
    "PhotoEditEvaluator",
    "PhotoEditImprover",
]

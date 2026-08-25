"""Photo-edit plugin — wraps the existing photo-edit-agent project.

Updated for PandaAgent v0.2 architecture.
"""

from .executor import PhotoEditExecutor
from .evaluator import PhotoEditEvaluator

__all__ = [
    "PhotoEditExecutor",
    "PhotoEditEvaluator",
]

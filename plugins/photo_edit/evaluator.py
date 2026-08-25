"""Photo-edit Evaluator — uses VLM to compare original vs edited image.

Wraps the existing qa_agent.py evaluate_edit() function.
"""

from __future__ import annotations

import sys
from pathlib import Path

from evo_agent.types import Task, ExecutionResult, Evaluation
from evo_agent.evaluator import Evaluator

_PHOTO_EDIT_ROOT = Path(r"E:\workspace\photo-edit-agent")


class PhotoEditEvaluator(Evaluator):
    """Evaluator that uses VLM (Qwen3-VL-235B) to evaluate image edits.

    Calls the existing qa_agent.evaluate_edit() which:
      1. Sends original + edited images to the VLM.
      2. Gets a structured evaluation (score, issues, root_cause, etc.).
    """

    def __init__(self):
        src = str(_PHOTO_EDIT_ROOT / "src")
        if src not in sys.path:
            sys.path.insert(0, src)

    def evaluate(self, task: Task, result: ExecutionResult) -> Evaluation:
        """Evaluate the edited image against the original."""
        from photo_edit_agent.qa_agent import evaluate_edit

        raw = evaluate_edit(task.input_path, result.output_path)

        return Evaluation(
            score=float(raw.get("overall_score", 0)),
            issues=raw.get("issues", []),
            root_cause=raw.get("root_cause", ""),
            suggested_changes=raw.get("suggested_changes", ""),
            dimensions={
                k: float(v)
                for k, v in raw.items()
                if isinstance(v, (int, float)) and k != "overall_score"
            },
        )

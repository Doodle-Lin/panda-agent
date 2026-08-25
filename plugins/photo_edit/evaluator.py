"""Photo-edit Evaluator — uses VLM to compare original vs edited image."""

from __future__ import annotations

import sys
from pathlib import Path

from panda_agent.types import Task, ExecutionResult, Evaluation
from panda_agent.orchestrator import Evaluator as BaseEvaluator

_PHOTO_EDIT_ROOT = Path(r"E:\workspace\photo-edit-agent")


class PhotoEditEvaluator(BaseEvaluator):
    """Evaluator that uses VLM to evaluate image edits."""

    def evaluate(self, task: Task, result: ExecutionResult) -> Evaluation:
        src = str(_PHOTO_EDIT_ROOT / "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from photo_edit_agent.qa_agent import evaluate_edit

        raw = evaluate_edit(task.input_path, result.output_path)
        return Evaluation(
            score=float(raw.get("overall_score", 0)),
            issues=raw.get("issues", []),
            root_cause=raw.get("root_cause", ""),
            suggested_changes=raw.get("suggested_changes", ""),
        )

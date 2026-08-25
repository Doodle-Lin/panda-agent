"""Photo-edit Executor — wraps the ReActAgent from photo-edit-agent."""

from __future__ import annotations

import sys
from pathlib import Path

from panda_agent.types import Task, ExecutionResult
from panda_agent.orchestrator import Executor as BaseExecutor

_PHOTO_EDIT_ROOT = Path(r"E:\workspace\photo-edit-agent")


class PhotoEditExecutor(BaseExecutor):
    """Executor that uses the photo-edit-agent's ReActAgent to edit images."""

    def execute(self, task: Task) -> ExecutionResult:
        src = str(_PHOTO_EDIT_ROOT / "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from photo_edit_agent.agent import run_edit

        result = run_edit(task.input_path, task.instruction)
        return ExecutionResult(
            output_path=result.get("output_path", ""),
            tool_calls=result.get("tool_calls", []),
            success=result.get("success", True),
            error=result.get("error"),
        )

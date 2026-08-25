"""Photo-edit Executor — wraps the ReActAgent from photo-edit-agent.

Loads the existing agent.py run_edit() function and calls it.
"""

from __future__ import annotations

import sys
from pathlib import Path

from evo_agent.types import Task, ExecutionResult
from evo_agent.executor import Executor


# Path to the existing photo-edit-agent project
_PHOTO_EDIT_ROOT = Path(r"E:\workspace\photo-edit-agent")


class PhotoEditExecutor(Executor):
    """Executor that uses the photo-edit-agent's ReActAgent to edit images.

    The photo-edit-agent project must be on sys.path (added automatically).
    """

    def __init__(self):
        # Add photo-edit-agent to sys.path so we can import its modules
        src = str(_PHOTO_EDIT_ROOT / "src")
        if src not in sys.path:
            sys.path.insert(0, src)

    def execute(self, task: Task) -> ExecutionResult:
        """Run the photo-edit agent on the input image.

        Args:
            task: input_path is the image, instruction is the edit request.
        """
        from photo_edit_agent.agent import run_edit

        result = run_edit(task.input_path, task.instruction)

        return ExecutionResult(
            output_path=result.get("output_path", ""),
            tool_calls=result.get("tool_calls", []),
            success=result.get("success", True),
            error=result.get("error"),
        )

"""Photo-edit Improver —uses LLM to patch tools.py based on VLM evaluation.

Subclasses the base Improver with photo-edit-specific configuration.
"""

from __future__ import annotations

from pathlib import Path

from panda_agent.improver import Improver
from panda_agent.llm import LLMConfig


_PHOTO_EDIT_ROOT = Path(r"E:\workspace\photo-edit-agent")


class PhotoEditImprover(Improver):
    """Improver that patches photo-edit-agent's tools.py.

    Uses GLM52RJPT (reasoning model) for code generation.
    keyword_map maps photo-edit issues to function names in tools.py.
    """

    def __init__(self, model: str = "GLM52RJPT"):
        self._model = model
        self._llm_config: LLMConfig | None = None

    def _load_config(self) -> LLMConfig:
        if self._llm_config is None:
            import os
            from dotenv import load_dotenv
            load_dotenv(_PHOTO_EDIT_ROOT / ".env")

            self._llm_config = LLMConfig(
                base_url=os.getenv("PHOTO_EDIT_LLM_BASE_URL", ""),
                api_key=os.getenv("PHOTO_EDIT_LLM_API_KEY", ""),
                model=self._model,
                max_tokens=int(os.getenv("PHOTO_EDIT_LLM_MAX_TOKENS", "8192")),
            )
        return self._llm_config

    @property
    def target_source_path(self) -> Path:
        return _PHOTO_EDIT_ROOT / "src" / "photo_edit_agent" / "tools.py"

    @property
    def test_path(self) -> Path:
        return _PHOTO_EDIT_ROOT / "tests" / "test_tools.py"

    @property
    def project_root(self) -> Path:
        return _PHOTO_EDIT_ROOT

    @property
    def llm_config(self) -> LLMConfig:
        return self._load_config()

    @property
    def keyword_map(self) -> dict[str, list[str]]:
        """Map photo-edit issues to function names in tools.py."""
        return {
            "halo": ["blur_background_precise"],
            "haloing": ["blur_background_precise"],
            "blur": ["blur_background_precise"],
            "edge": ["blur_background_precise"],
            "mask": ["blur_background_precise"],
            "background": ["blur_background_precise"],
            "sharpness": ["sharpen_region"],
            "noise": ["denoise_region"],
            "color": ["adjust_color"],
        }

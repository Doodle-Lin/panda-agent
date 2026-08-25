"""Tests for the photo-edit plugin.

Tests that the plugin classes can be instantiated and that their
properties resolve to the correct paths.
"""

import sys
from pathlib import Path

import pytest

# Add framework src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))


class TestPhotoEditPlugin:
    def test_executor_import(self):
        from plugins.photo_edit import PhotoEditExecutor
        executor = PhotoEditExecutor()
        assert isinstance(executor, object)

    def test_evaluator_import(self):
        from plugins.photo_edit import PhotoEditEvaluator
        evaluator = PhotoEditEvaluator()
        assert isinstance(evaluator, object)

    def test_improver_paths(self):
        from plugins.photo_edit import PhotoEditImprover
        improver = PhotoEditImprover()
        assert "tools.py" in str(improver.target_source_path)
        assert "test_tools.py" in str(improver.test_path)
        assert improver.project_root.exists()

    def test_keyword_map(self):
        from plugins.photo_edit import PhotoEditImprover
        improver = PhotoEditImprover()
        km = improver.keyword_map
        assert "halo" in km
        assert "blur_background_precise" in km["halo"]

    def test_improver_model_default(self):
        from plugins.photo_edit import PhotoEditImprover
        improver = PhotoEditImprover()
        assert improver._model == "GLM52RJPT"

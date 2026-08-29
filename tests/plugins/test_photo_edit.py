"""Tests for the photo-edit plugin."""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))


class TestPhotoEditPlugin:
    def test_executor_import(self):
        from plugins.photo_edit import PhotoEditExecutor
        assert PhotoEditExecutor is not None

    def test_evaluator_import(self):
        from plugins.photo_edit import PhotoEditEvaluator
        assert PhotoEditEvaluator is not None

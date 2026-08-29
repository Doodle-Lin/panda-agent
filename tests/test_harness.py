"""Tests for the local/remote collaboration harness."""

from __future__ import annotations

from scripts.harness import CONFLICT_MARKER, conventional_subject, protected_push_refs


def test_conventional_subject_accepts_scoped_commit() -> None:
    assert conventional_subject("feat(memory): embed persistent graph store")
    assert conventional_subject("fix!: reject unrelated upstream history")


def test_conventional_subject_rejects_ambiguous_commit() -> None:
    assert not conventional_subject("updates")
    assert not conventional_subject("Feat: mixed-case type")
    assert not conventional_subject("fix: ")


def test_conflict_marker_detection_is_line_anchored() -> None:
    assert CONFLICT_MARKER.search("<<<<<<< HEAD\nleft\n=======\nright\n>>>>>>> branch\n")
    assert not CONFLICT_MARKER.search("Documentation may mention <<<<<<< without a branch name.")


def test_pre_push_input_identifies_protected_destination() -> None:
    updates = (
        "refs/heads/topic abc refs/heads/topic 000\n"
        "refs/heads/topic abc refs/heads/master def\n"
    )

    assert protected_push_refs(updates) == ["refs/heads/master"]

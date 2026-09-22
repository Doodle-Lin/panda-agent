"""Audit Phase 6 react robustness tests.

Covers audit findings #8 (doom loop salvage fires on first detection), #8b
(repeated empty native FC responses burn turns), and #9 (benchmark
score_exact_match gives 100 on empty contains + non-empty text) from the
2026-09-14 audit.
"""
from __future__ import annotations

from panda_agent.benchmark import BenchmarkTask, score_exact_match


# ---------------------------------------------------------------------------
# #9: empty contains + non-empty text must NOT give full marks
# ---------------------------------------------------------------------------

class TestExactMatchEmptyContains:
    """An agent that emits 'ok' must not score 100 on a task with no
    required strings. The pre-fix returned 100.0 for any non-empty text
    when `contains` was empty — the gate was effectively disabled."""

    def test_empty_contains_non_empty_text_no_full_mark(self):
        task = BenchmarkTask(
            id="t1",
            instruction="do something",
            scorer="exact_match",
            expected={},
            weight=1.0,
        )
        score = score_exact_match(task, "ok", workspace=None)
        assert score == 0.0, (
            f"empty contains + non-empty text must not give full marks; "
            f"got {score}. The gate is vacuous if any answer passes."
        )

    def test_empty_contains_empty_text_zero(self):
        task = BenchmarkTask(
            id="t2",
            instruction="do something",
            scorer="exact_match",
            expected={},
            weight=1.0,
        )
        assert score_exact_match(task, "", workspace=None) == 0.0

    def test_contains_present_normal_scoring(self):
        task = BenchmarkTask(
            id="t3",
            instruction="list files",
            scorer="exact_match",
            expected={"contains": ["config.py"]},
            weight=1.0,
        )
        assert score_exact_match(task, "config.py", workspace=None) == 100.0
        assert score_exact_match(task, "other.py", workspace=None) == 0.0

    def test_not_contains_still_rejects(self):
        task = BenchmarkTask(
            id="t4",
            instruction="don't mention X",
            scorer="exact_match",
            expected={"not_contains": ["forbidden"]},
            weight=1.0,
        )
        assert score_exact_match(task, "this is forbidden", workspace=None) == 0.0


# ---------------------------------------------------------------------------
# #8: doom loop detection primitives
# ---------------------------------------------------------------------------

class TestDoomLoopDetection:
    """The pre-fix code triggered salvage on the *first* doom detection,
    because `_check_doom_loop(simple_calls[-3:])` returned True on the
    same data that triggered entry into the branch. The doom-loop warning
    was injected but never tested by the agent. The fix: track a doom
    strike counter; the warning fires on the first detection, salvage
    fires on the *second* consecutive detection with the same pattern.
    """

    def test_check_doom_loop_detects_three_repeats(self):
        from panda_agent.react import _check_doom_loop
        calls = [
            {"name": "read_file", "args": {"path": "x"}},
            {"name": "read_file", "args": {"path": "x"}},
            {"name": "read_file", "args": {"path": "x"}},
        ]
        assert _check_doom_loop(calls) is True

    def test_check_doom_loop_no_doom_on_two_repeats(self):
        from panda_agent.react import _check_doom_loop
        calls = [
            {"name": "read_file", "args": {"path": "x"}},
            {"name": "read_file", "args": {"path": "x"}},
        ]
        assert _check_doom_loop(calls) is False


# ---------------------------------------------------------------------------
# #8b: repeated empty native-FC responses must not loop forever
# ---------------------------------------------------------------------------

class TestEmptyNativeFCDoesNotLoop:
    """A persistent empty-content response from native function calling
    must break the loop after a small number of repeats, not increment
    turns until max_turns is hit."""

    def test_empty_content_breaks_after_two_repeats(self):
        from panda_agent.react import _EmptyContentTracker

        tracker = _EmptyContentTracker()
        # First empty response — note but continue.
        assert tracker.should_continue() is True
        tracker.record_empty()
        # Second empty response — still allow one more chance.
        assert tracker.should_continue() is True
        tracker.record_empty()
        # Third consecutive empty response — break the loop.
        assert tracker.should_continue() is False, (
            "after 2 consecutive empty native-FC responses, the loop must "
            "break rather than burn turns indefinitely."
        )

    def test_non_empty_resets_counter(self):
        from panda_agent.react import _EmptyContentTracker

        tracker = _EmptyContentTracker()
        tracker.record_empty()
        tracker.record_empty()
        tracker.record_non_empty()
        assert tracker.should_continue() is True
        tracker.record_empty()
        assert tracker.should_continue() is True

"""Tests for robust structured-output parsing.

These focus on the failure modes that silently corrupted the evolution
signal before: apostrophes inside JSON strings, prose around the object,
and parse failures masquerading as a mediocre score.
"""

from __future__ import annotations

import pytest

from panda_agent.parsing import (
    extract_json_blocks,
    parse_evaluation,
    parse_json_object,
    repair_json,
)


class TestExtractJsonBlocks:
    def test_extracts_single_object(self):
        assert extract_json_blocks('{"a": 1}') == ['{"a": 1}']

    def test_extracts_nested_object_as_one_block(self):
        text = '{"a": {"b": 2}}'
        assert extract_json_blocks(text) == [text]

    def test_extracts_multiple_separate_objects(self):
        blocks = extract_json_blocks('first {"a": 1} then {"b": 2}')
        assert blocks == ['{"a": 1}', '{"b": 2}']

    def test_braces_inside_strings_do_not_affect_nesting(self):
        text = '{"tpl": "use {placeholder} here"}'
        assert extract_json_blocks(text) == [text]

    def test_escaped_quote_inside_string(self):
        text = '{"msg": "he said \\"hi\\""}'
        assert extract_json_blocks(text) == [text]

    def test_no_object_returns_empty(self):
        assert extract_json_blocks("no json here") == []

    def test_unbalanced_braces_are_ignored(self):
        assert extract_json_blocks('{"a": 1') == []


class TestRepairJson:
    def test_removes_trailing_comma(self):
        assert parse_json_object('{"a": 1,}')[0] == {"a": 1}

    def test_converts_python_literals(self):
        data, _ = parse_json_object('{"ok": True, "bad": False, "nil": None}')
        assert data == {"ok": True, "bad": False, "nil": None}

    def test_single_quoted_keys_are_repaired(self):
        data, _ = parse_json_object("{'score': 80}")
        assert data == {"score": 80}

    def test_apostrophe_in_value_is_preserved(self):
        """The old ``.replace("'", '"')`` corrupted exactly this case."""
        raw = '{"root_cause": "the tool didn\'t return line numbers"}'
        data, err = parse_json_object(raw)
        assert err is None
        assert data["root_cause"] == "the tool didn't return line numbers"

    def test_repair_does_not_mangle_apostrophes(self):
        raw = '{"msg": "can\'t parse"}'
        assert "can't parse" in repair_json(raw)


class TestParseJsonObject:
    def test_ignores_prose_around_object(self):
        data, err = parse_json_object(
            'Let me evaluate.\n{"score": 72}\nHope that helps!'
        )
        assert err is None
        assert data["score"] == 72

    def test_handles_code_fence(self):
        data, err = parse_json_object('```json\n{"score": 88}\n```')
        assert err is None
        assert data["score"] == 88

    def test_empty_response_is_error(self):
        data, err = parse_json_object("   ")
        assert data is None
        assert "empty" in err

    def test_no_json_is_error(self):
        data, err = parse_json_object("I could not evaluate this.")
        assert data is None
        assert "no JSON object" in err

    def test_array_is_not_accepted_as_object(self):
        data, err = parse_json_object("[1, 2, 3]")
        assert data is None


class TestParseEvaluation:
    def test_parses_full_evaluation(self):
        r = parse_evaluation(
            '{"score": 85, "issues": ["a", "b"], '
            '"root_cause": "no line numbers", "suggested_changes": "add them"}'
        )
        assert r.ok
        assert r.evaluation.score == 85.0
        assert r.evaluation.issues == ["a", "b"]
        assert r.evaluation.root_cause == "no line numbers"

    def test_parse_failure_is_not_a_score(self):
        """Regression: a failure used to become ``score=50``.

        That fabricated signal made the Improver patch problems that did not
        exist. Failure must stay explicitly unusable.
        """
        r = parse_evaluation("The model refused to answer.")
        assert r.ok is False
        assert r.evaluation is None
        assert r.error

    def test_missing_score_key_is_failure(self):
        r = parse_evaluation('{"issues": ["something"]}')
        assert r.ok is False
        assert "score" in r.error

    def test_non_numeric_score_is_failure(self):
        r = parse_evaluation('{"score": "eighty"}')
        assert r.ok is False
        assert "not numeric" in r.error

    def test_boolean_score_is_failure(self):
        r = parse_evaluation('{"score": true}')
        assert r.ok is False

    @pytest.mark.parametrize("score", [-1, 101, 1000])
    def test_out_of_range_score_is_failure(self, score):
        r = parse_evaluation(f'{{"score": {score}}}')
        assert r.ok is False
        assert "out of range" in r.error

    @pytest.mark.parametrize("score", [0, 50, 100, 72.5])
    def test_boundary_scores_are_accepted(self, score):
        r = parse_evaluation(f'{{"score": {score}}}')
        assert r.ok
        assert r.evaluation.score == float(score)

    def test_string_issues_is_coerced_to_list(self):
        r = parse_evaluation('{"score": 60, "issues": "single issue"}')
        assert r.ok
        assert r.evaluation.issues == ["single issue"]

    def test_null_fields_become_empty(self):
        r = parse_evaluation('{"score": 60, "issues": null, "root_cause": null}')
        assert r.ok
        assert r.evaluation.issues == []
        assert r.evaluation.root_cause == ""

    def test_non_numeric_dimensions_are_dropped(self):
        r = parse_evaluation(
            '{"score": 70, "dimensions": {"speed": 8, "style": "good"}}'
        )
        assert r.ok
        assert r.evaluation.dimensions == {"speed": 8.0}

    def test_apostrophe_in_root_cause_survives(self):
        r = parse_evaluation(
            '{"score": 40, "root_cause": "search_files doesn\'t emit line numbers"}'
        )
        assert r.ok
        assert "doesn't" in r.evaluation.root_cause

"""Tests for AST-based patching.

Each of the first five tests corresponds to a case where the previous
regex implementation failed. They are written to fail loudly if anyone
reintroduces pattern-matching here.
"""

from __future__ import annotations

import ast

import pytest

from panda_agent.patching import describe_patch, replace_definition


def _valid(src: str) -> bool:
    try:
        ast.parse(src)
        return True
    except SyntaxError:
        return False


class TestRegressionsFromRegexVersion:
    """Cases the regex ``^def name\\(.*?(?=\\ndef ...)`` could not handle."""

    def test_top_level_function(self):
        src = "def foo(x):\n    return x\n\ndef bar():\n    pass\n"
        r = replace_definition(src, "def foo(x):\n    return x * 2\n")
        assert r.ok
        assert "return x * 2" in r.source
        assert "def bar" in r.source  # unrelated code preserved

    def test_decorated_function(self):
        """Regex silently no-opped: the decorator broke the ``^def`` anchor."""
        src = "@cache\ndef foo(x):\n    return x\n"
        r = replace_definition(src, "def foo(x):\n    return x * 2\n")
        assert r.ok
        assert "return x * 2" in r.source

    def test_async_function(self):
        """Regex matched only ``def``, never ``async def``."""
        src = "async def foo(x):\n    return x\n"
        r = replace_definition(src, "async def foo(x):\n    return x * 2\n")
        assert r.ok
        assert "return x * 2" in r.source

    def test_method_inside_class(self):
        """Regex required column 0, so indented methods never matched."""
        src = "class A:\n    def foo(self):\n        return 1\n"
        r = replace_definition(src, "def foo(self):\n    return 2\n")
        assert r.ok
        assert "return 2" in r.source
        assert _valid(r.source)
        assert "class A" in r.source

    def test_def_inside_string_literal_does_not_corrupt_file(self):
        """The dangerous one: regex truncated mid-literal and wrote invalid
        Python to disk, only caught later by pytest."""
        src = 'def foo():\n    s = """\ndef fake():\n    pass\n"""\n    return s\n\ndef bar():\n    pass\n'
        r = replace_definition(src, "def foo():\n    return 'new'\n")
        assert r.ok
        assert _valid(r.source), "patched source must always be valid Python"
        assert "def bar" in r.source


class TestDescribePatch:
    def test_identifies_function(self):
        assert describe_patch("def foo(): pass")[:2] == ("foo", "function")

    def test_identifies_async_function(self):
        assert describe_patch("async def foo(): pass")[:2] == ("foo", "function")

    def test_identifies_class(self):
        assert describe_patch("class Foo: pass")[:2] == ("Foo", "class")

    def test_identifies_assignment(self):
        assert describe_patch('PROMPT = "hi"')[:2] == ("PROMPT", "assignment")

    def test_syntax_error_is_reported(self):
        name, kind, err = describe_patch("def foo(:\n  pass")
        assert name is None
        assert "syntax error" in err

    def test_empty_patch_is_rejected(self):
        assert "empty" in describe_patch("")[2]

    def test_multiple_definitions_are_rejected(self):
        err = describe_patch("def a(): pass\ndef b(): pass")[2]
        assert "expected exactly 1" in err

    def test_bare_expression_is_rejected(self):
        assert describe_patch("x + 1")[2] is not None


class TestReplaceDefinition:
    def test_missing_target_is_reported_not_silent(self):
        """The regex version returned the source unchanged with no signal,
        making a dropped patch indistinguishable from a no-op patch."""
        r = replace_definition("def foo(): pass\n", "def nonexistent(): pass\n")
        assert r.ok is False
        assert "not found" in r.error
        assert r.target == "nonexistent"

    def test_invalid_patch_never_reaches_source(self):
        src = "def foo():\n    return 1\n"
        r = replace_definition(src, "def foo(:\n    broken")
        assert r.ok is False
        assert r.source == src

    def test_identical_patch_is_rejected(self):
        src = "def foo():\n    return 1\n"
        r = replace_definition(src, "def foo():\n    return 1\n")
        assert r.ok is False
        assert "identical" in r.error

    def test_unparseable_source_is_reported(self):
        r = replace_definition("def foo(:\n broken", "def foo(): pass")
        assert r.ok is False
        assert "does not parse" in r.error

    def test_replaces_module_level_constant(self):
        """brain.py's key evolvable surface is SYSTEM_PROMPT, a string."""
        src = 'SYSTEM_PROMPT = "old"\n\ndef helper():\n    pass\n'
        r = replace_definition(src, 'SYSTEM_PROMPT = "new and improved"')
        assert r.ok
        assert "new and improved" in r.source
        assert "def helper" in r.source

    def test_local_variable_with_same_name_is_not_touched(self):
        src = 'CONFIG = "module"\n\ndef f():\n    CONFIG = "local"\n    return CONFIG\n'
        r = replace_definition(src, 'CONFIG = "patched"')
        assert r.ok
        assert 'CONFIG = "patched"' in r.source
        assert 'CONFIG = "local"' in r.source, "local binding must survive"

    def test_replaces_class(self):
        src = "class A:\n    x = 1\n\nclass B:\n    pass\n"
        r = replace_definition(src, "class A:\n    x = 2\n")
        assert r.ok
        assert "x = 2" in r.source
        assert "class B" in r.source

    def test_preserves_surrounding_formatting(self):
        src = '"""Module doc."""\n\nimport os\n\n\ndef foo():\n    return 1\n'
        r = replace_definition(src, "def foo():\n    return 2\n")
        assert r.ok
        assert r.source.startswith('"""Module doc."""')
        assert "import os" in r.source

    @pytest.mark.parametrize(
        "patch",
        [
            "def foo(x, *args, **kwargs):\n    return x\n",
            "def foo(x: int = 3) -> int:\n    return x\n",
        ],
    )
    def test_signature_variants(self, patch):
        r = replace_definition("def foo():\n    return 0\n", patch)
        assert r.ok
        assert _valid(r.source)

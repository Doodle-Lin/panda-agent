"""AST-based source patching.

Replacing a function with a regex is unsound. The previous implementation
used ``^def name\\(.*?(?=\\ndef \\w+\\(|\\Z)`` which, verified empirically
against the real code path:

===========================  ======  =====================================
case                         works?  failure mode
===========================  ======  =====================================
top-level ``def``            yes     --
decorated function           no      silently no-ops, patch discarded
``async def``                no      silently no-ops, patch discarded
method inside a class        no      silently no-ops (``^def`` needs col 0)
body contains ``\\ndef`` in
a string literal             no      **truncates the file mid-literal,
                                     producing a SyntaxError on disk**
===========================  ======  =====================================

The last row is the dangerous one: a corrupt module reaches disk and is only
detected later by pytest. This module parses instead of pattern-matching, and
validates the result *before* the caller writes anything.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

import libcst as cst


@dataclass
class PatchResult:
    """Outcome of applying a patch to a source file."""

    ok: bool
    source: str
    error: str | None = None
    target: str | None = None
    kind: str | None = None  # "function" | "class" | "assignment"

    @property
    def changed(self) -> bool:
        return self.ok


# ---------------------------------------------------------------------------
# Patch inspection
# ---------------------------------------------------------------------------

def describe_patch(new_code: str) -> tuple[str | None, str | None, str | None]:
    """Identify what a patch defines.

    Returns ``(name, kind, error)``. A patch must define exactly one
    top-level function, class, or module-level assignment -- anything else is
    ambiguous about what it should replace.
    """
    try:
        tree = ast.parse(new_code)
    except SyntaxError as e:
        return None, None, f"patch has a syntax error: {e.msg} (line {e.lineno})"

    if not tree.body:
        return None, None, "patch is empty"

    targets: list[tuple[str, str]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            targets.append((node.name, "function"))
        elif isinstance(node, ast.ClassDef):
            targets.append((node.name, "class"))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    targets.append((t.id, "assignment"))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets.append((node.target.id, "assignment"))

    if not targets:
        return None, None, "patch defines no function, class, or assignment"
    if len(targets) > 1:
        names = ", ".join(n for n, _ in targets)
        return None, None, f"patch defines {len(targets)} things ({names}); expected exactly 1"

    name, kind = targets[0]
    return name, kind, None


# ---------------------------------------------------------------------------
# Transformers
# ---------------------------------------------------------------------------

class _FunctionReplacer(cst.CSTTransformer):
    """Replace a function definition, preserving surrounding formatting.

    Handles decorated, async, nested, and method definitions because libcst
    understands the grammar rather than matching line prefixes.
    """

    def __init__(self, name: str, new_node: cst.BaseStatement):
        self.name = name
        self.new_node = new_node
        self.count = 0

    def leave_FunctionDef(self, original: cst.FunctionDef, updated: cst.FunctionDef):
        if original.name.value == self.name:
            self.count += 1
            return self.new_node
        return updated


class _ClassReplacer(cst.CSTTransformer):
    def __init__(self, name: str, new_node: cst.BaseStatement):
        self.name = name
        self.new_node = new_node
        self.count = 0

    def leave_ClassDef(self, original: cst.ClassDef, updated: cst.ClassDef):
        if original.name.value == self.name:
            self.count += 1
            return self.new_node
        return updated


class _AssignReplacer(cst.CSTTransformer):
    """Replace a module-level assignment, e.g. ``SYSTEM_PROMPT = "..."``.

    Needed because brain.py's most important evolvable surface is a string
    constant, not a function.
    """

    def __init__(self, name: str, new_node: cst.BaseStatement):
        self.name = name
        self.new_node = new_node
        self.count = 0
        self._depth = 0

    def visit_FunctionDef(self, node) -> bool:
        self._depth += 1
        return True

    def leave_FunctionDef(self, original, updated):
        self._depth -= 1
        return updated

    def visit_ClassDef(self, node) -> bool:
        self._depth += 1
        return True

    def leave_ClassDef(self, original, updated):
        self._depth -= 1
        return updated

    def leave_SimpleStatementLine(self, original, updated):
        # Only rewrite at module level, never a same-named local.
        if self._depth != 0 or self.count:
            return updated
        for stmt in original.body:
            if isinstance(stmt, (cst.Assign, cst.AnnAssign)) and _assign_name(stmt) == self.name:
                self.count += 1
                return self.new_node
        return updated


def _assign_name(stmt: cst.Assign | cst.AnnAssign) -> str | None:
    if isinstance(stmt, cst.AnnAssign):
        return stmt.target.value if isinstance(stmt.target, cst.Name) else None
    for target in stmt.targets:
        if isinstance(target.target, cst.Name):
            return target.target.value
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def replace_definition(source: str, new_code: str) -> PatchResult:
    """Replace the definition in ``source`` that ``new_code`` redefines.

    The patched result is parsed before being returned, so a caller can never
    write a syntactically invalid module to disk.
    """
    name, kind, error = describe_patch(new_code)
    if error:
        return PatchResult(ok=False, source=source, error=error)

    try:
        module = cst.parse_module(source)
    except cst.ParserSyntaxError as e:
        return PatchResult(ok=False, source=source, error=f"source does not parse: {e}")

    try:
        new_node = cst.parse_statement(new_code.strip())
    except cst.ParserSyntaxError as e:
        return PatchResult(ok=False, source=source, error=f"patch does not parse as a statement: {e}")

    transformer: cst.CSTTransformer
    if kind == "function":
        transformer = _FunctionReplacer(name, new_node)
    elif kind == "class":
        transformer = _ClassReplacer(name, new_node)
    else:
        transformer = _AssignReplacer(name, new_node)

    result = module.visit(transformer)

    if transformer.count == 0:
        return PatchResult(
            ok=False,
            source=source,
            error=f"{kind} '{name}' not found in source",
            target=name,
            kind=kind,
        )

    patched = result.code

    # Validate before handing back. This is the guarantee the regex version
    # could not make: no corrupt module ever reaches the filesystem.
    try:
        ast.parse(patched)
    except SyntaxError as e:
        return PatchResult(
            ok=False,
            source=source,
            error=f"patched result is invalid Python: {e.msg} (line {e.lineno})",
            target=name,
            kind=kind,
        )

    if patched == source:
        return PatchResult(
            ok=False,
            source=source,
            error=f"{kind} '{name}' is already identical to the patch",
            target=name,
            kind=kind,
        )

    return PatchResult(ok=True, source=patched, target=name, kind=kind)

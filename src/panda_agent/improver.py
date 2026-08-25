"""Improver — reads evaluation, patches tool code, runs tests, keeps/reverts.

This is the core of the self-evolution loop.  It:
1. Reads the Evaluation (issues, root_cause, suggested_changes).
2. Extracts the relevant function(s) from the target source file.
3. Asks the LLM to generate a code patch.
4. Applies the patch, runs tests.
5. If tests fail, feeds the error back to the LLM and retries.
6. If all retries fail, reverts to the original code.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .llm import LLMConfig, call_llm
from .types import Evaluation, ImprovementResult


# ---------------------------------------------------------------------------
# Prompt template — generic, not domain-specific
# ---------------------------------------------------------------------------

_IMPROVER_PROMPT = """\
You are a code improvement agent. Your job is to fix bugs and improve \
the quality of a Python function based on an evaluation report.

## Evaluation Report
{evaluation_json}

## Current Source Code (relevant functions only)
```python
{source_code}
```

## Your Task
Based on the evaluation report, identify the specific function that \
needs modification and generate an improved version.

Output format (STRICTLY follow this):
```
PATCH_START
```python
def function_name(...):
    # improved code here
```
PATCH_END
EXPLANATION: Brief explanation of what you changed and why.
```

Rules:
1. Only output the COMPLETE function definition(s) that need changes.
2. Keep all other functions unchanged — do not include them.
3. The function signature must stay the same.
4. If you cannot determine a fix, output NO_CHANGE.
"""

_RETRY_PROMPT = """\
Your previous patch failed tests. Here is the error:

{test_error}

Please fix the issue and regenerate the patch. The error above tells \
you exactly what went wrong.

Output format:
```
PATCH_START
```python
def function_name(...):
    # fixed code here
```
PATCH_END
EXPLANATION: What was wrong and how you fixed it.
```
"""


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _extract_relevant_functions(
    source: str,
    evaluation: Evaluation,
    keyword_map: dict[str, list[str]] | None = None,
) -> str:
    """Extract function(s) from source relevant to the evaluation.

    Scans issues, root_cause, and suggested_changes for function-name
    keywords.  If keyword_map is provided, maps issue keywords to
    function names (e.g. {"halo": ["blur_background_precise"]}).

    Args:
        source: Full source code text.
        evaluation: The evaluation with issues and root_cause.
        keyword_map: Optional mapping from issue keywords to function names.

    Returns:
        Source code of the relevant function(s), joined by newlines.
    """
    # Build search text from evaluation
    search_text = " ".join(
        evaluation.issues
        + [evaluation.root_cause, evaluation.suggested_changes]
    ).lower()

    # Find all function definitions in source
    func_pattern = re.compile(r"^def (\w+)\(", re.MULTILINE)
    all_funcs = {}
    for m in func_pattern.finditer(source):
        name = m.group(1)
        all_funcs[name] = m.start()

    # Determine which functions are relevant
    relevant_names: set[str] = set()

    # 1. Direct name match
    for name in all_funcs:
        if name in search_text:
            relevant_names.add(name)

    # 2. Keyword map match
    if keyword_map:
        for keyword, func_names in keyword_map.items():
            if keyword.lower() in search_text:
                relevant_names.update(func_names)

    # 3. If no match, return all functions (let LLM decide)
    if not relevant_names:
        return source[:15000]

    # Extract each relevant function's full source
    results = []
    for name in relevant_names:
        if name not in all_funcs:
            continue
        start = all_funcs[name]
        # Find the next function definition or end of file
        remaining = source[start:]
        next_func = re.search(r"\ndef \w+\(", remaining[1:])
        if next_func:
            func_source = remaining[: next_func.start() + 1]
        else:
            func_source = remaining
        results.append(func_source.strip())

    return "\n\n".join(results) if results else source[:15000]


def _extract_patch(llm_response: str) -> str:
    """Extract code between PATCH_START and PATCH_END markers."""
    m = re.search(r"PATCH_START\s*```python\n(.*?)```", llm_response, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r"PATCH_START\n(.*?)PATCH_END", llm_response, re.DOTALL)
    if m:
        code = m.group(1).strip()
        if code.startswith("```python"):
            code = re.sub(r"^```python\n?", "", code)
        if code.endswith("```"):
            code = code[:-3].strip()
        return code
    return ""


def _extract_explanation(llm_response: str) -> str:
    """Extract the EXPLANATION line from the LLM response."""
    m = re.search(r"EXPLANATION:\s*(.+?)(?:\n```|\Z)", llm_response, re.DOTALL)
    return m.group(1).strip() if m else ""


def _replace_function(source: str, new_code: str) -> str:
    """Replace a function definition in source with new_code.

    Matches by function name.  Returns the patched source, or the
    original if no match is found.
    """
    # Extract function name from new_code
    m = re.match(r"def (\w+)\(", new_code)
    if not m:
        return source
    func_name = m.group(1)

    # Find the function in source
    pattern = re.compile(rf"^def {func_name}\(.*?(?=\ndef \w+\(|\Z)", re.DOTALL)
    if not pattern.search(source):
        return source

    return pattern.sub(new_code.rstrip() + "\n\n", source, count=1)


def _run_pytest(test_path: Path, project_root: Path, timeout: int = 300) -> tuple[bool, str]:
    """Run pytest and return (passed, output_tail)."""
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", str(test_path), "-x", "-q", "--tb=short"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout + result.stderr
        passed = result.returncode == 0 and "passed" in output
        return passed, output[-500:]
    except subprocess.TimeoutExpired:
        return False, f"pytest timed out after {timeout}s"
    except Exception as e:
        return False, f"pytest failed to run: {e}"


# ---------------------------------------------------------------------------
# Base Improver
# ---------------------------------------------------------------------------

class Improver(ABC):
    """Abstract Improver — patches tool code based on evaluation.

    Concrete implementations must provide:
      - ``target_source_path``: Path to the Python file being improved.
      - ``test_path``: Path to the pytest file for validation.
      - ``project_root``: Project root for running pytest.
      - ``llm_config``: LLM configuration for code generation.
      - ``keyword_map``: Optional mapping from issue keywords to function names.
      - ``max_retries``: Max LLM calls per improvement attempt (default 3).

    The base class implements ``improve()`` which handles the full
    retry loop.  Subclasses can override ``improve()`` for custom behavior.
    """

    @property
    @abstractmethod
    def target_source_path(self) -> Path:
        """Path to the Python file being improved (e.g. tools.py)."""
        ...

    @property
    @abstractmethod
    def test_path(self) -> Path:
        """Path to the pytest file for validation."""
        ...

    @property
    @abstractmethod
    def project_root(self) -> Path:
        """Project root directory for running pytest."""
        ...

    @property
    @abstractmethod
    def llm_config(self) -> LLMConfig:
        """LLM configuration for code generation."""
        ...

    @property
    def keyword_map(self) -> dict[str, list[str]] | None:
        """Optional mapping from issue keywords to function names."""
        return None

    @property
    def max_retries(self) -> int:
        """Max LLM calls per improvement attempt."""
        return 3

    @property
    def pytest_timeout(self) -> int:
        """Pytest timeout in seconds."""
        return 300

    def improve(self, evaluation: Evaluation) -> ImprovementResult:
        """Generate and apply a code patch based on the evaluation.

        If the first patch fails tests, the error message is fed back
        to the LLM for a retry, up to ``max_retries`` times.

        Args:
            evaluation: The evaluation with issues and suggestions.

        Returns:
            ImprovementResult with patched/reverted/diff/tests_passed.
        """
        import json

        source_path = self.target_source_path
        backup_path = source_path.with_suffix(".py.bak")

        # Backup original
        shutil.copy2(source_path, backup_path)

        # Read current source
        source = source_path.read_text(encoding="utf-8")

        # Extract relevant functions
        relevant = _extract_relevant_functions(source, evaluation, self.keyword_map)

        # Format evaluation as JSON
        eval_json = json.dumps(
            {
                "score": evaluation.score,
                "issues": evaluation.issues,
                "root_cause": evaluation.root_cause,
                "suggested_changes": evaluation.suggested_changes,
            },
            indent=2,
            ensure_ascii=False,
        )

        prompt = _IMPROVER_PROMPT.format(
            evaluation_json=eval_json,
            source_code=relevant,
        )

        attempts = 0
        for attempt in range(1, self.max_retries + 1):
            attempts = attempt

            # Call LLM
            if attempt == 1:
                response = call_llm(prompt, self.llm_config)
            else:
                # Feed test error back
                retry_prompt = _RETRY_PROMPT.format(test_error=last_test_output)
                response = call_llm(retry_prompt, self.llm_config)

            # Check for NO_CHANGE
            if response.strip().startswith("NO_CHANGE"):
                continue

            # Extract patch
            patch_code = _extract_patch(response)
            if not patch_code:
                continue

            # Apply patch
            patched_source = _replace_function(source, patch_code)
            if patched_source == source:
                continue

            source_path.write_text(patched_source, encoding="utf-8")

            # Run tests
            passed, test_output = _run_pytest(
                self.test_path, self.project_root, self.pytest_timeout
            )

            if passed:
                backup_path.unlink(missing_ok=True)
                return ImprovementResult(
                    patched=True,
                    reverted=False,
                    tests_passed=True,
                    diff=f"Replaced function in {source_path.name}",
                    explanation=_extract_explanation(response),
                    test_output=test_output,
                    attempts=attempts,
                )
            else:
                # Revert and retry
                shutil.copy2(backup_path, source_path)
                source = source_path.read_text(encoding="utf-8")
                last_test_output = test_output

        # All attempts failed — ensure original is restored
        if backup_path.exists():
            shutil.copy2(backup_path, source_path)
            backup_path.unlink(missing_ok=True)

        return ImprovementResult(
            patched=False,
            reverted=False,
            tests_passed=True,
            diff="",
            explanation="No change applied",
            test_output="",
            attempts=attempts,
        )

"""Self-evolution: Executor, Evaluator, Improver, Orchestrator.

The Improver can patch BOTH tools.py AND brain.py — evolving not just
the agent's "hands" but also its "mind".
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from .config import Config, load_config
from .llm import call_llm
from .react import run_react, ReActResult
from .tools import TOOLS, execute_tool, get_tool_descriptions
from .brain import build_system_prompt
from .memory import MemoryClient
from .types import Task, ExecutionResult, Evaluation, ImprovementResult, RoundResult, EvolutionResult, Event


# ---------------------------------------------------------------------------
# Executor — runs the task via ReAct loop
# ---------------------------------------------------------------------------

class Executor:
    """Executor: runs a task using the ReAct loop with current brain + tools."""

    def __init__(self, config: Config):
        self.config = config
        self.memory = MemoryClient(url=config.memory.graph_url) if config.memory.enabled else None

    def execute(self, task: Task) -> ExecutionResult:
        result = run_react(
            task.instruction,
            self.config,
            on_event=None,
            memory=self.memory,
        )
        return ExecutionResult(
            output_path="",
            tool_calls=result.tool_calls,
            success=result.success,
            error=result.error,
        )


# ---------------------------------------------------------------------------
# Evaluator — uses LLM to evaluate the execution result
# ---------------------------------------------------------------------------

_EVAL_PROMPT = """\
Evaluate the following task execution result. Score it 0-100.

Task: {task}

Execution result:
- Success: {success}
- Tool calls: {tool_calls}
- Answer/Error: {answer}

Respond in JSON:
{{"score": 85, "issues": ["issue1", "issue2"], "root_cause": "...", "suggested_changes": "..."}}
"""


class Evaluator:
    """Evaluator: uses LLM to score the execution result."""

    def __init__(self, config: Config):
        self.config = config

    def evaluate(self, task: Task, result: ExecutionResult) -> Evaluation:
        prompt = _EVAL_PROMPT.format(
            task=task.instruction,
            success=result.success,
            tool_calls=json.dumps(result.tool_calls, indent=2)[:2000],
            answer=result.error or "completed",
        )
        response = call_llm(
            [{"role": "user", "content": prompt}],
            self.config.model,
        )
        data = self._parse_eval_json(response)
        return Evaluation(
            score=float(data.get("score", 50)),
            issues=data.get("issues", []),
            root_cause=data.get("root_cause", ""),
            suggested_changes=data.get("suggested_changes", ""),
        )

    @staticmethod
    def _parse_eval_json(response: str) -> dict:
        """Extract evaluation JSON from LLM response, handling markdown blocks,
        think tags, and multi-segment responses."""
        if not response or response.startswith("ERROR:"):
            return {"score": 50, "issues": ["LLM call failed"]}

        # Strip markdown code fences if present
        cleaned = re.sub(r"```(?:json)?\s*", "", response)
        cleaned = re.sub(r"</?think>", "", cleaned).strip()

        # Try direct JSON parse first
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Try finding the last balanced JSON object (evaluation JSON is
        # typically the last thing in the response)
        # Find all potential JSON starts and try to parse from each
        for i in range(len(cleaned) - 1, -1, -1):
            if cleaned[i] == "}":
                # Walk backwards to find the matching opening brace
                depth = 0
                for j in range(i, -1, -1):
                    if cleaned[j] == "}":
                        depth += 1
                    elif cleaned[j] == "{":
                        depth -= 1
                        if depth == 0:
                            try:
                                return json.loads(cleaned[j:i + 1])
                            except json.JSONDecodeError:
                                break
                break

        return {"score": 50, "issues": ["Could not parse evaluation response"]}


# ---------------------------------------------------------------------------
# Improver — patches tools.py AND brain.py based on evaluation
# ---------------------------------------------------------------------------

_IMPROVE_PROMPT = """\
You are a code improvement agent. Fix bugs and improve quality based on \
an evaluation report.

## Evaluation
{evaluation_json}

## Current Source Code (relevant functions)
```python
{source_code}
```

## Target File: {target_file}

## Instructions
Output ONLY the function(s) you want to replace. Each function must be a \
complete, valid Python function starting with `def function_name(`.
Do NOT include module-level docstrings, imports, or constants unless \
you are also replacing them.

Output format:
```
PATCH_START
```python
def function_name(args):
    # your implementation
    ...
```
PATCH_END
EXPLANATION: what you changed and why
```

If no fix needed, output NO_CHANGE.
"""

_RETRY_PROMPT = """\
Previous patch failed tests:
{test_error}

Fix and regenerate. Same output format.
"""

# Source paths that can be evolved
_TOOLS_PATH = Path(__file__).parent / "tools.py"
_BRAIN_PATH = Path(__file__).parent / "brain.py"


def _extract_relevant(source: str, eval_data: Evaluation, keywords: list[str]) -> str:
    """Extract relevant functions from source based on evaluation issues.

    Matches function names and keywords found in the evaluation text.
    Returns only the matched function bodies, not the whole file.
    """
    search_text = " ".join(eval_data.issues + [eval_data.root_cause, eval_data.suggested_changes]).lower()
    # Find all function defs with their positions
    func_defs = list(re.finditer(r"^def (\w+)\(", source, re.MULTILINE))
    if not func_defs:
        return source[:5000]

    relevant = set()
    for m in func_defs:
        name = m.group(1)
        # Match if function name appears in evaluation text
        if name.lower() in search_text:
            relevant.add(name)

    # Also match by keyword: check if keyword appears in evaluation text
    # AND matches a function name or is part of a function's purpose
    for kw in keywords:
        if kw.lower() in search_text:
            for m in func_defs:
                name = m.group(1)
                if kw.lower() in name.lower():
                    relevant.add(name)

    if not relevant:
        # Fallback: return all function signatures + first 200 chars of each body
        lines = []
        for m in func_defs:
            name = m.group(1)
            end = m.end() + 200
            lines.append(source[m.start():end])
        return "\n...\n".join(lines) if lines else source[:3000]

    # Extract complete function bodies for relevant functions
    results = []
    for m in func_defs:
        name = m.group(1)
        if name not in relevant:
            continue
        start = m.start()
        # Find end of function (next def at same indent level or EOF)
        remaining = source[start:]
        next_def = re.search(r"\ndef \w+\(", remaining[1:])
        end = next_def.start() + 1 if next_def else len(remaining)
        results.append(remaining[:end].rstrip())

    return "\n\n".join(results) if results else source[:3000]


def _extract_patch(response: str) -> str:
    """Extract patched code from LLM response.

    Supports multiple formats:
    1. PATCH_START ```python ... ``` PATCH_END
    2. PATCH_START ... PATCH_END
    3. ```python ... ``` (without PATCH_START/END)
    4. ``` ... ``` (generic code fence)
    """
    # Format 1: PATCH_START with python code fence
    m = re.search(r"PATCH_START\s*```python\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Format 2: PATCH_START ... PATCH_END (may contain code fence)
    m = re.search(r"PATCH_START\n(.*?)PATCH_END", response, re.DOTALL)
    if m:
        code = m.group(1).strip()
        if code.startswith("```python"):
            code = re.sub(r"^```python\n?", "", code)
        elif code.startswith("```"):
            code = re.sub(r"^```\n?", "", code)
        if code.endswith("```"):
            code = code[:-3].strip()
        return code
    # Format 3: python code fence without PATCH_START/END
    m = re.search(r"```python\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Format 4: generic code fence
    m = re.search(r"```\n?(def \w+.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Format 5: raw function definition (no fences at all)
    m = re.search(r"(def \w+\([^)]*\).*?)(?=\n\n(?:EXPLANATION|```|\Z)|\Z)", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""


def _replace_function(source: str, new_code: str) -> str:
    """Replace function definition(s) in source.

    Handles three cases:
    1. Single function: `def foo(...): ...` → replace that function
    2. Multiple functions: `def foo(...): ...\n\ndef bar(...): ...` → replace each
    3. Full file (starts with docstring/imports): replace entire source
    """
    # Case 3: LLM returned a full file (starts with docstring or imports)
    if not new_code.lstrip().startswith("def "):
        # Check if it looks like a complete module
        if new_code.startswith('"""') or new_code.startswith("from ") or new_code.startswith("import "):
            return new_code
        # Try to extract the first def from the code
        m = re.search(r"^def (\w+)\(", new_code, re.MULTILINE)
        if not m:
            return source
        # Fall through to case 1 with the first function
    else:
        m = re.match(r"def (\w+)\(", new_code)

    # Case 1 & 2: replace each function found in new_code
    # Find all function definitions in new_code
    new_funcs = list(re.finditer(r"^def (\w+)\(", new_code, re.MULTILINE))
    if not new_funcs:
        return source

    result = source
    for i, fm in enumerate(new_funcs):
        name = fm.group(1)
        # Find the end of this function in new_code (next def or end)
        func_start = fm.start()
        remaining = new_code[func_start:]
        next_def = re.search(r"\ndef \w+\(", remaining[1:])
        if next_def:
            func_body = remaining[:next_def.start() + 1].rstrip()
        else:
            func_body = remaining.rstrip()

        # Replace in source — MULTILINE so ^ matches at line starts, not just file start
        pattern = re.compile(rf"^def {name}\(.*?(?=\ndef \w+\(|\Z)", re.DOTALL | re.MULTILINE)
        if pattern.search(result):
            result = pattern.sub(func_body + "\n\n", result, count=1)

    return result if result != source else source


def _run_pytest(test_path: Path, project_root: Path, timeout: int = 300) -> tuple[bool, str]:
    """Run pytest and return (passed, output_tail)."""
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", str(test_path), "-x", "-q", "--tb=short"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        output = (result.stdout or "") + (result.stderr or "")
        passed = result.returncode == 0 and "passed" in output
        return passed, output[-500:]
    except subprocess.TimeoutExpired:
        return False, f"pytest timed out after {timeout}s"
    except Exception as e:
        return False, f"pytest failed: {e}"


class Improver:
    """Improver: patches tools.py AND brain.py based on evaluation.

    Can evolve both the agent's "hands" (tools) and "mind" (brain).
    """

    def __init__(self, config: Config):
        self.config = config
        self.project_root = Path(__file__).parent.parent.parent
        self.test_path = self.project_root / "tests"

    def improve(self, evaluation: Evaluation) -> ImprovementResult:
        """Generate and apply patches based on evaluation."""
        results = []
        # Try improving tools.py first
        if self.config.evolution.improve_tools:
            r = self._improve_file(_TOOLS_PATH, evaluation, ["read", "write", "search", "patch", "run"])
            results.append(("tools", r))
        # Then try improving brain.py
        if self.config.evolution.improve_brain:
            r = self._improve_file(_BRAIN_PATH, evaluation, ["prompt", "strategy", "decision", "retry", "turn"])
            results.append(("brain", r))

        # Return the first successful patch
        for name, r in results:
            if r.patched:
                return r
        # Return the last result if none succeeded
        return results[-1][1] if results else ImprovementResult()

    def _improve_file(
        self, source_path: Path, evaluation: Evaluation, keywords: list[str]
    ) -> ImprovementResult:
        """Improve a single source file."""
        import sys as _sys
        backup_path = source_path.with_suffix(".py.bak")
        shutil.copy2(source_path, backup_path)
        source = source_path.read_text(encoding="utf-8")

        relevant = _extract_relevant(source, evaluation, keywords)
        eval_json = json.dumps({
            "score": evaluation.score,
            "issues": evaluation.issues,
            "root_cause": evaluation.root_cause,
            "suggested_changes": evaluation.suggested_changes,
        }, indent=2, ensure_ascii=False)

        prompt = _IMPROVE_PROMPT.format(
            evaluation_json=eval_json,
            source_code=relevant,
            target_file=source_path.name,
        )

        max_retries = self.config.agent.max_retries
        last_test_output = ""
        full_retry_prompt = prompt  # Include full context in retries

        for attempt in range(1, max_retries + 1):
            if attempt == 1:
                response = call_llm(
                    [{"role": "user", "content": prompt}],
                    self.config.model,
                    model=self.config.model.code_model or None,
                )
            else:
                retry_msg = _RETRY_PROMPT.format(test_error=last_test_output)
                # Re-send full context + retry info
                full_retry_prompt = prompt + "\n\n---\n" + retry_msg
                response = call_llm(
                    [{"role": "user", "content": full_retry_prompt}],
                    self.config.model,
                    model=self.config.model.code_model or None,
                )

            if response.strip().startswith("NO_CHANGE") or response.startswith("ERROR"):
                print(f"  [Improver:{source_path.name}] attempt {attempt}: NO_CHANGE/ERROR", file=_sys.stderr)
                continue

            patch_code = _extract_patch(response)
            if not patch_code:
                print(f"  [Improver:{source_path.name}] attempt {attempt}: _extract_patch failed, response[:200]={response[:200]!r}", file=_sys.stderr)
                continue

            patched = _replace_function(source, patch_code)
            if patched == source:
                print(f"  [Improver:{source_path.name}] attempt {attempt}: _replace_function no match, patch[:100]={patch_code[:100]!r}", file=_sys.stderr)
                continue

            source_path.write_text(patched, encoding="utf-8")

            # Run tests
            passed, test_output = _run_pytest(self.test_path, self.project_root)

            if passed:
                backup_path.unlink(missing_ok=True)
                return ImprovementResult(
                    patched=True,
                    tests_passed=True,
                    diff=f"Patched {source_path.name}",
                    explanation=_extract_explanation(response),
                    test_output=test_output,
                    attempts=attempt,
                )
            else:
                shutil.copy2(backup_path, source_path)
                source = source_path.read_text(encoding="utf-8")
                last_test_output = test_output

        # All failed — restore
        if backup_path.exists():
            shutil.copy2(backup_path, source_path)
            backup_path.unlink(missing_ok=True)

        return ImprovementResult(
            patched=False,
            tests_passed=True,
            explanation="No change applied",
            attempts=max_retries,
        )


def _extract_explanation(response: str) -> str:
    m = re.search(r"EXPLANATION:\s*(.+?)(?:\n```|\Z)", response, re.DOTALL)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# Orchestrator — drives the evolution loop
# ---------------------------------------------------------------------------

def run_evolution(
    executor: Executor | None,
    evaluator: Evaluator | None,
    improver: Improver | None,
    task: Task,
    *,
    target_score: float = 90.0,
    max_rounds: int = 3,
    on_event: Callable[[Event], None] | None = None,
    config: Config | None = None,
) -> EvolutionResult:
    """Run the self-evolution loop."""
    config = config or load_config()
    executor = executor or Executor(config)
    evaluator = evaluator or Evaluator(config)
    improver = improver or Improver(config)

    result = EvolutionResult()
    best_score = 0.0
    total_patches = 0

    def _emit(et, msg, rnd, data=None):
        if on_event:
            on_event(Event(type=et, message=msg, round=rnd, data=data or {}))

    for round_num in range(1, max_rounds + 1):
        round_result = RoundResult(round_num=round_num)

        # Execute
        _emit("executor_start", "Running task...", round_num)
        exec_result = executor.execute(task)
        round_result.execution = exec_result
        _emit("executor_done", f"Success: {exec_result.success}", round_num)

        # Evaluate
        _emit("evaluator_start", "Evaluating...", round_num)
        evaluation = evaluator.evaluate(task, exec_result)
        round_result.evaluation = evaluation
        _emit("evaluator_done", f"Score: {evaluation.score:.0f}/100", round_num)

        if evaluation.score > best_score:
            best_score = evaluation.score

        if evaluation.score >= target_score:
            _emit("target_reached", f"Target {target_score} reached", round_num)
            result.target_reached = True
            result.rounds.append(round_result)
            break

        # Improve
        if round_num < max_rounds:
            _emit("improver_start", "Generating patch...", round_num)
            try:
                improvement = improver.improve(evaluation)
                round_result.improvement = improvement
                if improvement.patched:
                    total_patches += 1
                _emit("improver_done", f"Patched: {improvement.patched}, Attempts: {improvement.attempts}", round_num)
            except Exception as e:
                round_result.improvement = ImprovementResult(explanation=f"Error: {e}")
                _emit("improver_error", str(e), round_num)
        else:
            _emit("improver_skip", "Last round", round_num)

        result.rounds.append(round_result)

    result.final_score = best_score
    result.total_patches = total_patches
    result.target_reached = result.target_reached or best_score >= target_score

    _emit("complete", f"Done. Score: {best_score:.0f}, Patches: {total_patches}", max_rounds)

    return result

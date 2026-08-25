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
        try:
            # Extract JSON from response
            m = re.search(r'\{.*\}', response, re.DOTALL)
            if m:
                data = json.loads(m.group(0).replace("'", '"'))
            else:
                data = {"score": 50, "issues": ["Could not parse evaluation"]}
        except json.JSONDecodeError:
            data = {"score": 50, "issues": ["Could not parse evaluation"]}

        return Evaluation(
            score=float(data.get("score", 50)),
            issues=data.get("issues", []),
            root_cause=data.get("root_cause", ""),
            suggested_changes=data.get("suggested_changes", ""),
        )


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

Output format:
```
PATCH_START
```python
{code_here}
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
    """Extract relevant functions from source based on keywords."""
    search_text = " ".join(eval_data.issues + [eval_data.root_cause, eval_data.suggested_changes]).lower()
    # Find all function defs
    funcs = {}
    for m in re.finditer(r"^def (\w+)\(", source, re.MULTILINE):
        funcs[m.group(1)] = m.start()
    # Match by keyword or function name
    relevant = set()
    for name in funcs:
        if name in search_text:
            relevant.add(name)
    for kw in keywords:
        if kw.lower() in search_text:
            relevant.add(kw)
    if not relevant:
        return source[:15000]
    # Extract function bodies
    results = []
    for name in relevant:
        if name not in funcs:
            continue
        start = funcs[name]
        remaining = source[start:]
        next_func = re.search(r"\ndef \w+\(", remaining[1:])
        end = next_func.start() + 1 if next_func else len(remaining)
        results.append(remaining[:end].strip())
    return "\n\n".join(results) if results else source[:15000]


def _extract_patch(response: str) -> str:
    """Extract code between PATCH_START and PATCH_END."""
    m = re.search(r"PATCH_START\s*```python\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r"PATCH_START\n(.*?)PATCH_END", response, re.DOTALL)
    if m:
        code = m.group(1).strip()
        if code.startswith("```python"):
            code = re.sub(r"^```python\n?", "", code)
        if code.endswith("```"):
            code = code[:-3].strip()
        return code
    return ""


def _replace_function(source: str, new_code: str) -> str:
    """Replace a function definition in source."""
    m = re.match(r"def (\w+)\(", new_code)
    if not m:
        return source
    name = m.group(1)
    pattern = re.compile(rf"^def {name}\(.*?(?=\ndef \w+\(|\Z)", re.DOTALL)
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

        for attempt in range(1, max_retries + 1):
            if attempt == 1:
                response = call_llm(
                    [{"role": "user", "content": prompt}],
                    self.config.model,
                    model=self.config.model.code_model or None,
                )
            else:
                retry_prompt = _RETRY_PROMPT.format(test_error=last_test_output)
                response = call_llm(
                    [{"role": "user", "content": retry_prompt}],
                    self.config.model,
                    model=self.config.model.code_model or None,
                )

            if response.strip().startswith("NO_CHANGE") or response.startswith("ERROR"):
                continue

            patch_code = _extract_patch(response)
            if not patch_code:
                continue

            patched = _replace_function(source, patch_code)
            if patched == source:
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

"""Self-evolution: Executor, Evaluator, Improver, Orchestrator.

The Improver can patch BOTH tools.py AND brain.py — evolving not just
the agent's "hands" but also its "mind".
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .config import Config, load_config
from .llm import call_llm
from .parsing import parse_evaluation
from .patching import replace_definition
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

_EVAL_RETRY_SUFFIX = """\

IMPORTANT: your previous response could not be parsed ({error}).
Respond with ONLY a single JSON object, no prose before or after it.
The "score" field must be a number between 0 and 100.
"""


class Evaluator:
    """Evaluator: uses LLM to score the execution result."""

    def __init__(self, config: Config):
        self.config = config

    def evaluate(self, task: Task, result: ExecutionResult) -> Evaluation | None:
        """Score an execution result.

        Returns ``None`` when the model's output could not be parsed after a
        retry. A caller that receives ``None`` has *no evaluation signal* for
        this round and must skip improving rather than acting on a guess --
        see :mod:`panda_agent.parsing` for why a default score is harmful.
        """
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
        parsed = parse_evaluation(response)
        if parsed.ok:
            return parsed.evaluation

        # One bounded retry that names the specific failure.
        retry_prompt = prompt + _EVAL_RETRY_SUFFIX.format(error=parsed.error)
        response = call_llm(
            [{"role": "user", "content": retry_prompt}],
            self.config.model,
        )
        parsed = parse_evaluation(response)
        if parsed.ok:
            return parsed.evaluation

        self.last_error = parsed.error
        return None


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
{{code_here}}
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
    """Replace a definition in source, returning source unchanged on failure.

    Backwards-compatible wrapper over :func:`panda_agent.patching.replace_definition`.
    Prefer that function directly: it reports *why* a patch did not apply,
    which this signature cannot express.
    """
    return replace_definition(source, new_code).source


def _run_pytest(test_path: Path, project_root: Path, timeout: int = 300) -> tuple[bool, str]:
    """Run pytest and return (passed, output_tail)."""
    try:
        result = subprocess.run(
            # sys.executable, not "python": a bare name resolves through PATH to
            # whatever interpreter happens to be first, which is routinely not the
            # one running this process. When it lands outside the active venv the
            # project's own dependencies are missing, pytest fails on import, and
            # the gate reads that as "this patch broke the tests" -- silently
            # reverting every patch, including the good ones.
            [sys.executable, "-m", "pytest", str(test_path), "-x", "-q", "--tb=short"],
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
        # Optional regression gate. When set, a patch must also leave benchmark
        # performance intact -- not merely keep the unit tests green.
        self.benchmark_gate: Callable[[], Any] | None = None
        self.baseline: Any | None = None
        self.tolerance: float = 2.0
        self.last_reject_reason: str | None = None

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

            patch_result = replace_definition(source, patch_code)
            if not patch_result.ok:
                # Feed the specific reason back so the next attempt is informed
                # rather than a blind retry. Nothing was written to disk.
                last_test_output = f"Patch could not be applied: {patch_result.error}"
                continue

            source_path.write_text(patch_result.source, encoding="utf-8")

            # Run tests
            passed, test_output = _run_pytest(self.test_path, self.project_root)

            if not passed:
                shutil.copy2(backup_path, source_path)
                source = source_path.read_text(encoding="utf-8")
                last_test_output = test_output
                continue

            # Second gate: unit tests passing only means the code is not
            # broken. Confirm the agent's measured behaviour did not degrade
            # before keeping the patch.
            gate_note = ""
            if self.benchmark_gate is not None and self.baseline is not None:
                from .benchmark import check_no_regression

                after = self.benchmark_gate()
                gate = check_no_regression(self.baseline, after, self.tolerance)
                if not gate.accepted:
                    self.last_reject_reason = gate.reason
                    shutil.copy2(backup_path, source_path)
                    source = source_path.read_text(encoding="utf-8")
                    last_test_output = (
                        f"Unit tests passed but benchmark regressed: {gate.reason}"
                    )
                    continue
                gate_note = f" | benchmark {gate.delta:+.1f}"

            backup_path.unlink(missing_ok=True)
            return ImprovementResult(
                patched=True,
                tests_passed=True,
                diff=f"Patched {source_path.name}{gate_note}",
                explanation=_extract_explanation(response),
                test_output=test_output,
                attempts=attempt,
            )

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
    best_round: int | None = None
    snapshots: dict[int, dict[Path, str]] = {}
    evolvable = [p for p in (_TOOLS_PATH, _BRAIN_PATH) if p.exists()]

    def _emit(et, msg, rnd, data=None):
        if on_event:
            on_event(Event(type=et, message=msg, round=rnd, data=data or {}))

    for round_num in range(1, max_rounds + 1):
        round_result = RoundResult(round_num=round_num)

        # Snapshot the evolvable sources *before* this round's patch, so the
        # best-performing code state can be restored at the end.
        snapshots[round_num] = {
            p: p.read_text(encoding="utf-8") for p in evolvable
        }

        # Execute
        _emit("executor_start", "Running task...", round_num)
        exec_result = executor.execute(task)
        round_result.execution = exec_result
        _emit("executor_done", f"Success: {exec_result.success}", round_num)

        # Evaluate
        _emit("evaluator_start", "Evaluating...", round_num)
        evaluation = evaluator.evaluate(task, exec_result)
        if evaluation is None:
            # No usable signal this round. Improving on a fabricated score
            # would optimise against noise, so skip straight to the next round.
            reason = getattr(evaluator, "last_error", "unparseable evaluation")
            _emit("evaluator_error", f"No evaluation signal: {reason}", round_num)
            result.rounds.append(round_result)
            continue

        round_result.evaluation = evaluation
        _emit("evaluator_done", f"Score: {evaluation.score:.0f}/100", round_num)

        if evaluation.score > best_score:
            best_score = evaluation.score
            best_round = round_num

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

    # The scored code state is the one snapshotted at the *start* of the best
    # round. Without this restore, run_evolution reports best_score while
    # leaving whatever the final patch produced on disk -- so the number it
    # returns describes code the caller never receives.
    if best_round is not None and snapshots.get(best_round):
        current = {p: p.read_text(encoding="utf-8") for p in evolvable}
        if current != snapshots[best_round]:
            for path, content in snapshots[best_round].items():
                path.write_text(content, encoding="utf-8")
            _emit(
                "restored_best",
                f"Restored code from round {best_round} (score {best_score:.0f})",
                max_rounds,
            )
            result.restored_from_round = best_round

    result.final_score = best_score
    result.total_patches = total_patches
    result.target_reached = result.target_reached or best_score >= target_score

    _emit("complete", f"Done. Score: {best_score:.0f}, Patches: {total_patches}", max_rounds)

    return result

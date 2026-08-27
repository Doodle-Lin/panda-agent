"""Self-evolution: Executor, Evaluator, Improver, Orchestrator.

The Improver can patch BOTH tools.py AND brain.py — evolving not just
the agent's "hands" but also its "mind".
"""

from __future__ import annotations

import json
import os
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
from .types import (
    Task, ExecutionResult, Evaluation, ImprovementResult,
    RoundResult, EvolutionResult, Event,
    ExecutionTrace, TurnRecord, ErrorRecord, LearningResult,
)


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
# Learner — Level 2: post-task learning from ExecutionTrace
# ---------------------------------------------------------------------------

_LEARN_PROMPT = """\
Analyze the following execution trace and extract lessons.

Task: {task}
Success: {success}

Execution trace:
- Total turns: {total_turns}
- Errors encountered: {errors}
- Self-repairs applied: {self_repairs}
- Tool calls made: {tool_calls}

Turns detail:
{turns_detail}

Respond in JSON:
{{
  "lessons": ["lesson1", "lesson2"],
  "recurring_errors": ["error_pattern_that_keeps_coming_back"],
  "is_structural": false,
  "structural_reason": ""
}}

Rules for lessons:
- Each lesson must be a concise, actionable knowledge point (not a narrative)
- Format: "When X, use Y" or "On Windows, Z is the correct approach" or "Tool A fails on B, use C instead"
- Include specific commands, paths, or parameters when relevant
- Example GOOD: "On Windows, use 'dir %USERPROFILE%\\Desktop' to list desktop files"
- Example BAD: "The agent should be more careful about operating system detection"
- Maximum 3 lessons, only the most valuable ones
- "is_structural" = true if the root cause is in the agent's prompt or tools (not the environment)
- "structural_reason" = if is_structural, explain which function/prompt needs fixing and why
"""


class Learner:
    """Learner: Level 2 post-task learning.

    Analyzes the ExecutionTrace after each task and:
    1. Extracts lessons -> writes to memory for future tasks
    2. Identifies recurring error patterns -> tracks occurrence count (persisted)
    3. If a pattern appears >=3 times AND is structural -> triggers Level 3
    """

    def __init__(self, config: Config):
        self.config = config
        self.memory = MemoryClient(url=config.memory.graph_url) if config.memory.enabled else None
        # Error pattern registry — persisted to $PANDA_HOME/error_counts.json
        self._error_counts: dict[str, int] = {}
        panda_home = os.environ.get("PANDA_HOME", os.path.expanduser("~/.panda"))
        self._counts_path = Path(panda_home) / "error_counts.json"
        self._load_error_counts()

    def _load_error_counts(self):
        """Load persisted error counts from disk."""
        try:
            if self._counts_path.exists():
                data = json.loads(self._counts_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._error_counts = {k: int(v) for k, v in data.items()}
        except Exception:
            pass

    def _save_error_counts(self):
        """Persist error counts to disk."""
        try:
            self._counts_path.parent.mkdir(parents=True, exist_ok=True)
            self._counts_path.write_text(
                json.dumps(self._error_counts, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def learn(self, task: Task, result: ExecutionResult, evaluation: Evaluation) -> LearningResult:
        """Analyze execution trace and extract lessons.

        Returns LearningResult with lessons, error patterns, and whether
        Level 3 should be triggered.
        """
        trace = result.trace
        if not trace:
            # No trace — create minimal from available data
            trace = ExecutionTrace(
                task=task.instruction,
                total_turns=len(result.tool_calls),
                final_success=result.success,
                errors=[result.error] if result.error else [],
            )

        # Format turns detail for LLM
        turns_detail = ""
        if trace.turns:
            for t in trace.turns[:10]:  # Limit to 10 turns for prompt size
                turns_detail += f"  Turn {t.turn}: action={t.action}"
                if t.error:
                    turns_detail += f" error={t.error[:100]}"
                if t.self_repaired:
                    turns_detail += f" self_repaired={t.repair_strategy}"
                turns_detail += "\n"

        prompt = _LEARN_PROMPT.format(
            task=task.instruction,
            success=trace.final_success,
            total_turns=trace.total_turns,
            errors=json.dumps(trace.errors[:5], ensure_ascii=False),
            self_repairs=json.dumps(trace.self_repairs[:5], ensure_ascii=False),
            tool_calls=json.dumps(
                [{"name": tc.get("name"), "args": tc.get("args")} for tc in result.tool_calls[:5]],
                ensure_ascii=False,
            ),
            turns_detail=turns_detail or "  (no turn data)",
        )

        response = call_llm(
            [{"role": "user", "content": prompt}],
            self.config.model,
        )
        data = self._parse_learn_json(response)

        # Write lessons to memory (embedded graph engine, no HTTP needed)
        memory_written = False
        if self.memory and data.get("lessons"):
            for lesson in data["lessons"][:5]:
                try:
                    # Write lesson as-is (no prefix) so retrieval can match
                    # by semantic similarity to the lesson content itself
                    self.memory.write(
                        lesson,
                        title=f"lesson:{task.instruction[:30]}",
                        node_type="reference",
                        source="panda_learner",
                    )
                    memory_written = True
                except Exception:
                    pass

        # Track recurring error patterns
        recurring = data.get("recurring_errors", [])
        for pattern in recurring:
            normalized = pattern.strip().lower()[:100]
            if normalized:
                self._error_counts[normalized] = self._error_counts.get(normalized, 0) + 1
        self._save_error_counts()

        # Check if Level 3 should trigger
        trigger = False
        trigger_reason = ""
        is_structural = data.get("is_structural", False)

        if is_structural and evaluation.score < 70:
            # Structural issue identified by LLM
            structural_reason = data.get("structural_reason", "")
            # Check if we've seen this kind of issue before
            pattern_key = structural_reason.strip().lower()[:100]
            self._error_counts[pattern_key] = self._error_counts.get(pattern_key, 0) + 1
            self._save_error_counts()

            if self._error_counts[pattern_key] >= 3:
                trigger = True
                trigger_reason = f"Structural issue seen {self._error_counts[pattern_key]} times: {structural_reason}"
            else:
                trigger_reason = f"Structural issue identified (occurrence {self._error_counts[pattern_key]}/3): {structural_reason}"

        return LearningResult(
            lessons=data.get("lessons", []),
            memory_written=memory_written,
            error_patterns=recurring,
            trigger_evolution=trigger,
            trigger_reason=trigger_reason,
        )

    @staticmethod
    def _parse_learn_json(response: str) -> dict:
        if not response or response.startswith("ERROR:"):
            return {"lessons": [], "recurring_errors": [], "is_structural": False}

        cleaned = re.sub(r"```(?:json)?\s*", "", response)
        cleaned = re.sub(r"</?think>", "", cleaned).strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Find last balanced JSON
        for i in range(len(cleaned) - 1, -1, -1):
            if cleaned[i] == "}":
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

        return {"lessons": [], "recurring_errors": [], "is_structural": False}


# ---------------------------------------------------------------------------
# Improver — Level 3: structural evolution (patch source code)

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
    """Extract patched code from LLM response. Supports multiple formats."""
    # Format 1: PATCH_START with python code fence
    m = re.search(r"PATCH_START\s*```python\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Format 2: PATCH_START ... PATCH_END
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
    # Format 3: python code fence without PATCH markers
    m = re.search(r"```python\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Format 4: generic code fence with def
    m = re.search(r"```\n?(def \w+.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Format 5: raw function definition
    m = re.search(r"(def \w+\([^)]*\).*?)(?=\n\n(?:EXPLANATION|```|\Z)|\Z)", response, re.DOTALL)
    if m:
        return m.group(1).strip()
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

    Can evolve both the agent's hands (tools) and mind (brain).
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
        self.memory = MemoryClient(url=config.memory.graph_url) if config.memory.enabled else None

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
        self, source_path: Path, evaluation: Evaluation,
        keywords: list[str], evidence: str = ""
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

        history_context = ""
        if self.memory and hasattr(self.memory, 'retrieve'):
            try:
                results = self.memory.retrieve(
                    f"{evaluation.root_cause} {' '.join(evaluation.issues)}",
                    top_k=3,
                )
                if results:
                    accepted = [r for r in results if 'accepted' in r.get('title', '').lower()]
                    rejected = [r for r in results if 'rejected' in r.get('title', '').lower()]
                    if accepted or rejected:
                        lines = ["## Past Patch History (from memory)"]
                        for r in accepted[:2]:
                            lines.append(f"  ACCEPTED: {r.get('content', '')[:200]}")
                        for r in rejected[:2]:
                            lines.append(f"  REJECTED: {r.get('content', '')[:200]}")
                        history_context = "\n".join(lines)
            except Exception:
                pass

        if history_context:
            prompt = prompt + "\n\n" + history_context

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

            if self.memory and hasattr(self.memory, 'write'):
                try:
                    score_delta = evaluation.score
                    explanation = _extract_explanation(response)
                    self.memory.write(
                        content=(
                            f"Patch accepted: {source_path.name}\n"
                            f"Root cause: {evaluation.root_cause}\n"
                            f"Score delta: +{score_delta}\n"
                            f"Explanation: {explanation}\n"
                        ),
                        title=f"patch_accepted: {source_path.name}",
                        node_type="reference",
                        source="panda_improver",
                    )
                except Exception:
                    pass

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

        if self.memory and hasattr(self.memory, 'write'):
            try:
                self.memory.write(
                    content=(
                        f"Patch rejected: {source_path.name}\n"
                        f"Root cause: {evaluation.root_cause}\n"
                        f"Reject reason: {last_test_output}\n"
                    ),
                    title=f"patch_rejected: {source_path.name}",
                    node_type="reference",
                    source="panda_improver",
                )
            except Exception:
                pass

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
    learner: "Learner | None" = None,
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

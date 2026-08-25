"""Self-evolution: Executor, Evaluator, Learner, Improver, Orchestrator.

Three-layer evolution:
  Level 1 (runtime):    Self-Repair in react.py — adapt tool calls on error
  Level 2 (post-task):  Learner — extract lessons from ExecutionTrace, write to memory
  Level 3 (structural): Improver — patch brain.py/tools.py, only when evidence ≥3
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
            trace=result.trace,
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
        if not response or response.startswith("ERROR:"):
            return {"score": 50, "issues": ["LLM call failed"]}

        cleaned = re.sub(r"```(?:json)?\s*", "", response)
        cleaned = re.sub(r"</?think>", "", cleaned).strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

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

        return {"score": 50, "issues": ["Could not parse evaluation response"]}


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

Rules:
- "lessons" = actionable advice for future similar tasks (e.g. "On Windows, use 'dir %USERPROFILE%\\Desktop' to list desktop files")
- "recurring_errors" = error patterns that appear more than once across tasks
- "is_structural" = true if the root cause is in the agent's prompt or tools (not the environment)
- "structural_reason" = if is_structural, explain which function/prompt needs fixing and why
"""


class Learner:
    """Learner: Level 2 post-task learning.

    Analyzes the ExecutionTrace after each task and:
    1. Extracts lessons → writes to memory for future tasks
    2. Identifies recurring error patterns → tracks occurrence count
    3. If a pattern appears ≥3 times AND is structural → triggers Level 3
    """

    def __init__(self, config: Config):
        self.config = config
        self.memory = MemoryClient(url=config.memory.graph_url) if config.memory.enabled else None
        # Error pattern registry (in-memory; persisted via memory service)
        self._error_counts: dict[str, int] = {}

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

        # Write lessons to memory
        memory_written = False
        if self.memory and data.get("lessons"):
            for lesson in data["lessons"][:5]:
                try:
                    self.memory.write(
                        f"Lesson for '{task.instruction[:50]}': {lesson}",
                        title=f"lesson:{task.instruction[:30]}",
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
# ---------------------------------------------------------------------------

_IMPROVE_PROMPT = """\
You are a code improvement agent. Fix bugs and improve quality based on \
an evaluation report and accumulated evidence.

## Evaluation
{evaluation_json}

## Evidence (from Learner)
{evidence}

## Current Source Code (relevant functions)
```python
{source_code}
```

## Target File: {target_file}

## CRITICAL CONSTRAINTS — read carefully
1. Do NOT change function signatures (parameter names, order, defaults). \
   Existing tests call these functions with exact argument names.
2. Do NOT change return types or formats. Tests assert specific return values.
3. Do NOT remove or rename any existing function.
4. Keep all existing behavior intact — only ADD or FIX, do not BREAK.
5. If you add a new function, also register it if needed (for tools.py).

## Instructions
Output ONLY the function(s) you want to replace. Each function must be a \
complete, valid Python function starting with `def function_name(`.
The function signature MUST match the original exactly.
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

_TOOLS_PATH = Path(__file__).parent / "tools.py"
_BRAIN_PATH = Path(__file__).parent / "brain.py"


def _extract_relevant(source: str, eval_data: Evaluation, keywords: list[str]) -> str:
    """Extract relevant functions from source based on evaluation issues."""
    search_text = " ".join(eval_data.issues + [eval_data.root_cause, eval_data.suggested_changes]).lower()
    func_defs = list(re.finditer(r"^def (\w+\()", source, re.MULTILINE))
    if not func_defs:
        return source[:5000]

    relevant = set()
    for m in func_defs:
        name = m.group(1)
        if name.lower() in search_text:
            relevant.add(name)

    for kw in keywords:
        if kw.lower() in search_text:
            for m in func_defs:
                name = m.group(1)
                if kw.lower() in name.lower():
                    relevant.add(name)

    if not relevant:
        lines = []
        for m in func_defs:
            name = m.group(1)
            end = m.end() + 200
            lines.append(source[m.start():end])
        return "\n...\n".join(lines) if lines else source[:3000]

    results = []
    for m in func_defs:
        name = m.group(1)
        if name not in relevant:
            continue
        start = m.start()
        remaining = source[start:]
        next_def = re.search(r"\ndef \w+\(", remaining[1:])
        end = next_def.start() + 1 if next_def else len(remaining)
        results.append(remaining[:end].rstrip())

    return "\n\n".join(results) if results else source[:3000]


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
    """Replace function definition(s) in source."""
    result = source

    # Extract function names from new_code
    new_defs = list(re.finditer(r"^def (\w+)\(", new_code, re.MULTILINE))
    if not new_defs:
        return source

    for m in new_defs:
        name = m.group(1)
        # Extract this function's full body from new_code
        start = m.start()
        next_in_new = re.search(r"\ndef \w+\(", new_code[start + 1:])
        if next_in_new:
            func_body = new_code[start:start + 1 + next_in_new.start()].rstrip()
        else:
            func_body = new_code[start:].rstrip()

        # Replace in source — MULTILINE so ^ matches at line starts
        pattern = re.compile(rf"^def {name}\(.*?(?=\ndef \w+\(|\Z)", re.DOTALL | re.MULTILINE)
        if pattern.search(result):
            result = pattern.sub(func_body + "\n\n", result, count=1)

    return result if result != source else source


def _try_fix_syntax(code: str, error: SyntaxError) -> str:
    """Try to auto-fix common LLM-generated syntax errors."""
    if not code or not error:
        return ""

    # Fix 1: Chinese quotes → ASCII
    fixed = code.replace("\u201c", '"').replace("\u201d", '"')
    fixed = fixed.replace("\u2018", "'").replace("\u2019", "'")
    fixed = fixed.replace("\u300c", '"').replace("\u300d", '"')
    if fixed != code:
        return fixed

    # Fix 2: Unterminated string literal
    err_str = str(error).lower()
    if "unterminated" in err_str:
        lines = code.splitlines()
        error_line = error.lineno or 0
        if error_line > 0 and error_line <= len(lines):
            line = lines[error_line - 1]
            for quote_char in ('"', "'"):
                count = line.count(quote_char) - line.count(f"\\{quote_char}")
                if count % 2 == 1:
                    lines[error_line - 1] = line + quote_char
                    return "\n".join(lines)

    # Fix 3: Unexpected EOF
    if "unexpected eof" in err_str or "unexpected end of file" in err_str:
        opens = code.count("(") - code.count(")")
        sq = code.count("[") - code.count("]")
        cu = code.count("{") - code.count("}")
        suffix = ""
        if opens > 0: suffix += ")" * opens
        if sq > 0: suffix += "]" * sq
        if cu > 0: suffix += "}" * cu
        if suffix:
            return code + "\n" + suffix

    return ""


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
        return passed, output[-1500:]
    except subprocess.TimeoutExpired:
        return False, f"pytest timed out after {timeout}s"
    except Exception as e:
        return False, f"pytest error: {e}"


def _extract_explanation(response: str) -> str:
    m = re.search(r"EXPLANATION:\s*(.+?)(?:\n```|\Z)", response, re.DOTALL)
    return m.group(1).strip() if m else ""


class Improver:
    """Improver: Level 3 structural evolution.

    Only triggers when Learner provides evidence (≥3 occurrences of same
    structural issue). Patches brain.py and/or tools.py with full validation:
    1. Syntax check (compile)
    2. Pytest must still pass
    3. Behavioral check (LLM responds correctly)
    4. Score must improve, else rollback
    """

    def __init__(self, config: Config):
        self.config = config
        project_root = Path(__file__).parent.parent.parent
        self.test_path = project_root / "tests"
        self.project_root = project_root

    def improve(
        self, evaluation: Evaluation, evidence: str = ""
    ) -> ImprovementResult:
        """Generate and apply patches based on evaluation and evidence."""
        targets = [
            (_TOOLS_PATH, ["list_files", "read_file", "write_file", "search_files", "run_command", "patch_file"]),
            (_BRAIN_PATH, ["build_system_prompt", "max_turns_for_task", "should_retry", "SYSTEM_PROMPT"]),
        ]

        for source_path, keywords in targets:
            result = self._improve_file(source_path, evaluation, keywords, evidence)
            if result.patched:
                return result

        return ImprovementResult(
            patched=False, tests_passed=True,
            explanation="No improvement applied", attempts=0,
        )

    def _improve_file(
        self, source_path: Path, evaluation: Evaluation,
        keywords: list[str], evidence: str = ""
    ) -> ImprovementResult:
        """Improve a single source file with guaranteed rollback."""
        import sys as _sys
        backup_path = source_path.with_suffix(".py.bak")
        shutil.copy2(source_path, backup_path)
        original_source = source_path.read_text(encoding="utf-8")
        source = original_source

        try:
            relevant = _extract_relevant(source, evaluation, keywords)
            eval_json = json.dumps({
                "score": evaluation.score,
                "issues": evaluation.issues,
                "root_cause": evaluation.root_cause,
                "suggested_changes": evaluation.suggested_changes,
            }, indent=2, ensure_ascii=False)

            prompt = _IMPROVE_PROMPT.format(
                evaluation_json=eval_json,
                evidence=evidence or "(no accumulated evidence yet)",
                source_code=relevant,
                target_file=source_path.name,
            )

            max_retries = self.config.agent.max_retries
            last_test_output = ""
            full_retry_prompt = prompt

            for attempt in range(1, max_retries + 1):
                if attempt == 1:
                    response = call_llm(
                        [{"role": "user", "content": prompt}],
                        self.config.model,
                        model=self.config.model.code_model or None,
                    )
                else:
                    retry_msg = _RETRY_PROMPT.format(test_error=last_test_output)
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

                # Syntax check before writing
                try:
                    compile(patched, source_path.name, "exec")
                except SyntaxError as se:
                    patched_fixed = _try_fix_syntax(patched, se)
                    if patched_fixed:
                        try:
                            compile(patched_fixed, source_path.name, "exec")
                            patched = patched_fixed
                            print(f"  [Improver:{source_path.name}] attempt {attempt}: auto-fixed syntax", file=_sys.stderr)
                        except SyntaxError as se2:
                            print(f"  [Improver:{source_path.name}] attempt {attempt}: SyntaxError (unfixable): {se2}", file=_sys.stderr)
                            last_test_output = f"SyntaxError: {se2}"
                            continue
                    else:
                        print(f"  [Improver:{source_path.name}] attempt {attempt}: SyntaxError: {se}", file=_sys.stderr)
                        last_test_output = f"SyntaxError: {se}"
                        continue

                source_path.write_text(patched, encoding="utf-8")

                # Gate 1: pytest
                passed, test_output = _run_pytest(self.test_path, self.project_root)
                if not passed:
                    print(f"  [Improver:{source_path.name}] attempt {attempt}: pytest failed, rolling back", file=_sys.stderr)
                    source_path.write_text(original_source, encoding="utf-8")
                    source = original_source
                    last_test_output = test_output
                    continue

                # Gate 2: behavioral check
                new_score = self._behavioral_check(evaluation)

                if new_score > evaluation.score:
                    print(f"  [Improver:{source_path.name}] attempt {attempt}: score {evaluation.score:.0f}→{new_score:.0f} ✓ kept", file=_sys.stderr)
                    backup_path.unlink(missing_ok=True)
                    return ImprovementResult(
                        patched=True, tests_passed=True,
                        diff=f"Patched {source_path.name}",
                        explanation=_extract_explanation(response),
                        test_output=test_output,
                        attempts=attempt,
                        score_after=new_score,
                    )
                else:
                    print(f"  [Improver:{source_path.name}] attempt {attempt}: score {evaluation.score:.0f}→{new_score:.0f} ✗ rolled back", file=_sys.stderr)
                    source_path.write_text(original_source, encoding="utf-8")
                    source = original_source
                    last_test_output = f"Behavioral check: score {evaluation.score:.0f}→{new_score:.0f}, no improvement"

            # All attempts failed
            source_path.write_text(original_source, encoding="utf-8")
            backup_path.unlink(missing_ok=True)
            return ImprovementResult(
                patched=False, tests_passed=True,
                explanation="No improvement applied", attempts=max_retries,
            )
        except Exception as e:
            print(f"  [Improver:{source_path.name}] exception: {e}", file=_sys.stderr)
            source_path.write_text(original_source, encoding="utf-8")
            backup_path.unlink(missing_ok=True)
            return ImprovementResult(
                patched=False, tests_passed=True,
                explanation=f"Error: {e}", attempts=0,
            )

    def _behavioral_check(self, evaluation: Evaluation) -> float:
        """Verify patched brain.py/tools.py still produces working LLM responses."""
        import sys
        from panda_agent.brain import build_system_prompt
        from panda_agent.tools import get_tool_descriptions

        try:
            test_prompt = "你好"
            system_prompt = build_system_prompt(get_tool_descriptions())
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": test_prompt},
            ]
            response = call_llm(messages, self.config.model, model=None)
            if not response or not response.strip():
                print(f"  [Improver] behavioral check: empty → brain broken", file=sys.stderr)
                return 10.0

            resp = response.strip()
            if len(resp) < 5:
                print(f"  [Improver] behavioral check: too short ({len(resp)} chars)", file=sys.stderr)
                return 30.0

            has_done = "DONE:" in resp or "DONE：" in resp
            has_tool = "TOOL_CALL:" in resp
            if has_done or has_tool:
                print(f"  [Improver] behavioral check: valid agent format ✓", file=sys.stderr)
                return 100.0

            print(f"  [Improver] behavioral check: LLM responds ({len(resp)} chars), no format markers", file=sys.stderr)
            return 80.0
        except Exception as e:
            print(f"  [Improver] behavioral check error: {e}", file=sys.stderr)
            return 0.0


# ---------------------------------------------------------------------------
# Orchestrator — drives the three-layer evolution loop
# ---------------------------------------------------------------------------

def run_evolution(
    executor: Executor | None,
    evaluator: Evaluator | None,
    learner: Learner | None,
    improver: Improver | None,
    task: Task,
    *,
    target_score: float = 90.0,
    max_rounds: int = 20,
    on_event: Callable[[Event], None] | None = None,
    config: Config | None = None,
) -> EvolutionResult:
    """Run the self-evolution loop with three-layer architecture.

    Level 1 (runtime):    Self-Repair in react.py — automatic, every run
    Level 2 (post-task):  Learner — extract lessons, write to memory, track patterns
    Level 3 (structural): Improver — patch source code, only when evidence ≥3

    Loop continues until:
    - Score reaches target_score (success)
    - max_rounds reached
    - 3 consecutive rounds with no score improvement
    """
    config = config or load_config()
    executor = executor or Executor(config)
    evaluator = evaluator or Evaluator(config)
    learner = learner or Learner(config)
    improver = improver or Improver(config)

    result = EvolutionResult()
    best_score = 0.0
    total_patches = 0
    total_lessons = 0
    stale_rounds = 0

    def _emit(et, msg, rnd, data=None):
        if on_event:
            on_event(Event(type=et, message=msg, round=rnd, data=data or {}))

    for round_num in range(1, max_rounds + 1):
        round_result = RoundResult(round_num=round_num)

        # === Execute (Level 1 self-repair happens inside) ===
        _emit("executor_start", f"Round {round_num}/{max_rounds}: executing task...", round_num)
        exec_result = executor.execute(task)
        round_result.execution = exec_result

        if exec_result.tool_calls:
            tools_summary = ", ".join(tc.get("name", "?") for tc in exec_result.tool_calls)
            _emit("executor_tools", f"Tools used: {tools_summary}", round_num)

        _emit("executor_done", f"Success: {exec_result.success}", round_num)

        # === Evaluate ===
        _emit("evaluator_start", f"Evaluating...", round_num)
        evaluation = evaluator.evaluate(task, exec_result)
        round_result.evaluation = evaluation

        _emit("evaluator_done", f"Score: {evaluation.score:.0f}/100", round_num,
              data={"score": evaluation.score, "issues": evaluation.issues})

        if best_score > 0:
            trend = "↑" if evaluation.score > best_score else ("↓" if evaluation.score < best_score else "→")
            _emit("score_trend", f"Trend: {best_score:.0f} {trend} {evaluation.score:.0f}", round_num)

        if evaluation.score > best_score:
            best_score = evaluation.score
            stale_rounds = 0
        else:
            stale_rounds += 1

        if evaluation.issues:
            for issue in evaluation.issues[:3]:
                _emit("eval_issue", f"⚠ {issue}", round_num)

        # === Level 2: Learn (always runs) ===
        _emit("learner_start", f"Learning from execution...", round_num)
        learning = learner.learn(task, exec_result, evaluation)
        round_result.learning = learning

        if learning.lessons:
            _emit("learner_done", f"Extracted {len(learning.lessons)} lesson(s)", round_num)
            for lesson in learning.lessons[:2]:
                _emit("learner_detail", f"  💡 {lesson[:120]}", round_num)
            total_lessons += len(learning.lessons)
        else:
            _emit("learner_done", f"No new lessons", round_num)

        # Check if Level 3 should trigger
        if learning.trigger_evolution:
            _emit("learner_trigger", f"⚠ Structural issue detected → triggering code evolution", round_num)
            _emit("learner_detail", f"  Evidence: {learning.trigger_reason[:150]}", round_num)

        # === Level 3: Improve (only when triggered or score is low) ===
        if evaluation.score >= target_score:
            _emit("target_reached", f"✓ Target {target_score:.0f} reached!", round_num)
            result.target_reached = True
            result.rounds.append(round_result)
            break

        should_improve = learning.trigger_evolution or evaluation.score < 70

        if should_improve:
            _emit("improver_start", f"Generating patch...", round_num)
            try:
                evidence_text = learning.trigger_reason or evaluation.root_cause
                improvement = improver.improve(evaluation, evidence=evidence_text)
                round_result.improvement = improvement
                if improvement.patched:
                    total_patches += 1
                    _emit("improver_done", f"✓ Patched (attempts: {improvement.attempts})", round_num,
                          data={"patched": True, "explanation": improvement.explanation})
                    if improvement.explanation:
                        _emit("improver_detail", f"  Change: {improvement.explanation[:200]}", round_num)
                else:
                    _emit("improver_done", f"✗ No patch applied (attempts: {improvement.attempts})", round_num,
                          data={"patched": False, "explanation": improvement.explanation})
                    if improvement.explanation:
                        _emit("improver_detail", f"  Reason: {improvement.explanation[:200]}", round_num)
            except Exception as e:
                round_result.improvement = ImprovementResult(explanation=f"Error: {e}")
                _emit("improver_error", f"Error: {e}", round_num)
        else:
            _emit("improver_start", f"Skipping code evolution (Level 2 learning is enough)", round_num)

        if stale_rounds >= 3:
            _emit("stale_stop", f"Stopping: no improvement for {stale_rounds} rounds", round_num)
            result.rounds.append(round_result)
            break

        result.rounds.append(round_result)
        _emit("round_end", f"{'─' * 50}", round_num)

    result.final_score = best_score
    result.total_patches = total_patches
    result.total_lessons = total_lessons
    result.target_reached = result.target_reached or best_score >= target_score

    _emit("complete", f"Done. Score: {best_score:.0f}/{target_score:.0f}, Patches: {total_patches}, Lessons: {total_lessons}, Rounds: {len(result.rounds)}", max_rounds)

    return result

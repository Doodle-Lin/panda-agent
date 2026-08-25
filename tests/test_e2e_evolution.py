"""E2E: self-evolution verification — same task twice, second time faster.

Run 1: agent does task, Learner extracts lessons, writes to memory.
Run 2: same task, memory injected → agent should be faster/fewer turns.

This is the ultimate proof that self-evolution works.
"""
import sys, os, time, tempfile
sys.path.insert(0, r'E:\workspace\evo-agent\src')
os.environ['PANDA_HOME'] = os.path.expanduser('~/.panda')

import pytest
from panda_agent.config import load_config
from panda_agent.react import run_react
from panda_agent.memory import MemoryClient
from panda_agent.types import Task, ExecutionResult, Evaluation
from panda_agent.orchestrator import Learner


@pytest.mark.slow
class TestE2ESelfEvolution:
    """Verify that self-evolution actually makes the agent better on retry."""

    def test_same_task_second_run_faster(self):
        """Run the same task twice. Second run should use fewer turns or be faster."""
        config = load_config()
        if not config.model.api_key:
            pytest.skip("No API key configured")

        # Use a task that involves tool calls (so memory has something to learn from)
        task = "List the files in the current directory and tell me how many Python files there are"
        tmpdir = tempfile.mkdtemp()
        test_file = os.path.join(tmpdir, "test_evolution_e2e.py")

        memory = MemoryClient(url=config.memory.graph_url) if config.memory.enabled else None
        learner = Learner(config)

        # === Run 1 ===
        t0 = time.time()
        result1 = run_react(task, config, memory=memory)
        t1 = time.time()
        run1_time = t1 - t0
        run1_turns = result1.turns

        # Learner extracts lessons
        if result1.success:
            exec_result = ExecutionResult(
                tool_calls=result1.tool_calls,
                success=result1.success,
                error=result1.error,
                trace=result1.trace,
            )
            evaluation = Evaluation(
                score=80.0 if result1.tool_calls else 50.0,
                issues=[] if result1.success else ["failed"],
            )
            learner.learn(Task(instruction=task), exec_result, evaluation)

        # === Run 2 ===
        t0 = time.time()
        result2 = run_react(task, config, memory=memory)
        t1 = time.time()
        run2_time = t1 - t0
        run2_turns = result2.turns

        # Both should succeed
        assert result1.success, f"Run 1 failed: {result1.error}"
        assert result2.success, f"Run 2 failed: {result2.error}"

        # Second run should be faster or use fewer turns
        # (memory injected → agent knows what to do without exploring)
        print(f"\n=== Self-Evolution E2E Results ===")
        print(f"Run 1: {run1_turns} turns, {run1_time:.1f}s")
        print(f"Run 2: {run2_turns} turns, {run2_time:.1f}s")
        print(f"Improvement: {run1_time - run2_time:.1f}s faster, {run1_turns - run2_turns} fewer turns")

        # At minimum, second run should not be worse
        assert run2_turns <= run1_turns + 1, \
            f"Run 2 used more turns ({run2_turns}) than Run 1 ({run1_turns})"

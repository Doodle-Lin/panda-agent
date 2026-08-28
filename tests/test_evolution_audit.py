"""Comprehensive self-evolution audit tests.

Tests each layer of the 3-layer self-evolution architecture:
  Level 1: Runtime self-repair (tool error → auto-fix)
  Level 2: Post-task learning (trace → lessons → memory → retrieval)
  Level 3: Structural improvement (persistent counts → Improver trigger → patch/rollback)

Each test verifies the COMPLETE closed loop, not just individual functions.
"""
import os
import json
import tempfile

from unittest.mock import patch, MagicMock
from panda_agent.react import run_react, _self_repair, _classify_error, _should_write_memory
from panda_agent.orchestrator import Learner, Improver
from panda_agent.config import Config, ModelConfig, AgentConfig, MemoryConfig, EvolutionConfig, DisplayConfig
from panda_agent.types import Task, ExecutionResult, Evaluation
from panda_agent.llm import LLMResponse


def make_config():
    return Config(
        model=ModelConfig(default="GLM52RJPT", api_key="k", base_url="u", max_tokens=8192),
        agent=AgentConfig(max_turns=5),
        memory=MemoryConfig(enabled=False),
        evolution=EvolutionConfig(),
        display=DisplayConfig(),
    )


# ===========================================================================
# Level 1: Runtime Self-Repair
# ===========================================================================

class TestLevel1SelfRepair:
    """Level 1: tool fails → _self_repair adapts the call → retry succeeds.

    Verifies:
    - Error classification covers all documented error types
    - Each error type has a repair strategy
    - Repair actually changes the tool call
    - run_react integrates self-repair and tracks it in trace
    """

    def test_classify_all_error_types(self):
        """All 6 documented error types must be classified correctly."""
        assert _classify_error("command timed out after 30s") == "timeout"
        assert _classify_error("Error: file not found: test.txt") == "not_found"
        assert _classify_error("Permission denied") == "permission"
        assert _classify_error("JSON parse error at line 5") == "parse_error"
        assert _classify_error("Unicode encoding error") == "encoding"
        assert _classify_error("Connection refused") == "network"
        assert _classify_error("something weird") == "unknown"

    def test_repair_not_found_expands_tilde(self):
        """not_found error with ~ path → expand to real path."""
        new_name, new_args, strategy = _self_repair(
            "Error: file not found: ~/Desktop/test.txt",
            "read_file", {"path": "~/Desktop/test.txt"}, make_config()
        )
        assert new_name == "read_file"
        assert "~" not in new_args["path"]
        assert "expanded path" in strategy

    def test_repair_not_found_switches_to_search(self):
        """not_found error without ~ → switch to search_files."""
        new_name, new_args, strategy = _self_repair(
            "Error: file not found: data.txt",
            "read_file", {"path": "data.txt"}, make_config()
        )
        assert new_name == "search_files"
        assert "switched" in strategy

    def test_repair_timeout_reduces_timeout(self):
        """timeout error → reduce timeout arg."""
        new_name, new_args, strategy = _self_repair(
            "Error: command timed out after 60s",
            "run_command", {"command": "ls", "timeout": 60}, make_config()
        )
        assert new_args["timeout"] == 30  # 60 // 2
        assert "reduced timeout" in strategy

    def test_repair_encoding_adds_chcp(self):
        """encoding error on run_command → add chcp 65001."""
        new_name, new_args, strategy = _self_repair(
            "Unicode encoding error",
            "run_command", {"command": "dir"}, make_config()
        )
        assert "chcp 65001" in new_args["command"]
        assert "chcp" in strategy

    def test_repair_permission_no_autofix(self):
        """permission error → no auto-fix available (safe choice)."""
        new_name, new_args, strategy = _self_repair(
            "Permission denied",
            "read_file", {"path": "/etc/shadow"}, make_config()
        )
        assert new_name == "read_file"
        assert new_args == {"path": "/etc/shadow"}
        assert "no auto-fix" in strategy

    def test_run_react_self_repair_in_trace(self):
        """run_react should record self-repair in ExecutionTrace."""
        with tempfile.TemporaryDirectory():
            config = make_config()
            config.agent.max_turns = 5

            # Turn 1: read_file fails (path not found with ~), self-repair expands
            # Turn 2: DONE
            resps = [
                LLMResponse(content="", reasoning="", tool_calls=[
                    {"id": "c1", "name": "read_file", "args": {"path": "~/nonexistent.txt"}}
                ]),
                LLMResponse(content="DONE: found it", reasoning="", tool_calls=[]),
            ]
            with patch("panda_agent.react.call_llm_detailed", side_effect=resps):
                with patch("panda_agent.react.execute_tool", side_effect=[
                    "Error: file not found: ~/nonexistent.txt",  # first call fails
                    "file content",  # self-repair retry succeeds
                ]):
                    result = run_react("read ~/nonexistent.txt", config)

            assert result.success
            assert result.trace is not None
            # Trace should have at least 1 repair
            assert len(result.trace.self_repairs) >= 1

    def test_self_repair_with_chinese_desktop(self):
        """not_found with '桌面' (Chinese for Desktop) → expand to Desktop."""
        new_name, new_args, strategy = _self_repair(
            "Error: not found: ~/桌面/file.txt",
            "read_file", {"path": "~/桌面/file.txt"}, make_config()
        )
        assert "Desktop" in new_args["path"]
        assert "桌面" not in new_args["path"]


# ===========================================================================
# Level 2: Post-Task Learning — complete closed loop
# ===========================================================================

class TestLevel2Learning:
    """Level 2: execution trace → Learner extracts lessons → writes to memory
    → memory is retrievable for future tasks.

    Verifies the COMPLETE closed loop, not just individual functions.
    """

    def test_lesson_written_to_memory_is_retrievable(self):
        """Lesson written by Learner should be retrievable by retrieve_context."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ['PANDA_HOME'] = tmpdir
            config = make_config()
            config.memory.enabled = True

            learner = Learner(config)

            # Mock LLM returns a structured lesson
            mock_response = json.dumps({
                "lessons": ["On Windows, use 'dir %USERPROFILE%\\Desktop' to list desktop files"],
                "recurring_errors": [],
                "is_structural": False,
                "structural_reason": "",
            })

            task = Task(instruction="list desktop files on windows")
            exec_result = ExecutionResult(
                tool_calls=[{"name": "run_command", "args": {"command": "ls ~/Desktop"}}],
                success=True,
            )
            evaluation = Evaluation(score=80, issues=[])

            with patch("panda_agent.orchestrator.call_llm", return_value=mock_response):
                learning = learner.learn(task, exec_result, evaluation)

            # Verify lesson was written
            assert learning.lessons
            assert learning.memory_written

            # Verify it's retrievable (same MemoryClient singleton)
            from panda_agent.memory import EmbeddedMemory
            mem = EmbeddedMemory.get()
            if mem:
                results = mem.retrieve("list desktop files on windows", top_k=5)
                # Should find the lesson
                found = any("dir" in r.get("content", "").lower() for r in results)
                assert found, f"Lesson should be retrievable. Results: {results[:2]}"

    def test_lesson_content_no_redundant_prefix(self):
        """Lesson written to memory should NOT have 'Lesson for ...' prefix.

        The prefix hurts semantic retrieval. We verify by intercepting
        the memory.write call rather than reading from graph memory
        (which has singleton state issues in tests).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ['PANDA_HOME'] = tmpdir
            config = make_config()
            config.memory.enabled = True

            # Reset singleton
            from panda_agent.memory import EmbeddedMemory
            EmbeddedMemory._instance = None

            learner = Learner(config)

            # Replace learner's memory with a mock to capture write calls
            mock_mem = MagicMock()
            mock_mem.is_available.return_value = True
            learner.memory = mock_mem

            lesson_text = "Use chcp 65001 for UTF-8 on Windows"
            mock_response = json.dumps({
                "lessons": [lesson_text],
                "recurring_errors": [],
                "is_structural": False,
                "structural_reason": "",
            })

            task = Task(instruction="fix encoding")
            exec_result = ExecutionResult(tool_calls=[], success=True)
            evaluation = Evaluation(score=80)

            with patch("panda_agent.orchestrator.call_llm", return_value=mock_response):
                learner.learn(task, exec_result, evaluation)

            # Verify memory.write was called with raw lesson, not prefixed
            assert mock_mem.write.called
            write_args = mock_mem.write.call_args_list[0]
            content_arg = write_args[0][0]  # first positional arg
            assert content_arg == lesson_text, \
                f"Should write raw lesson, got: '{content_arg}'"
            assert "Lesson for" not in content_arg, \
                "Lesson content should not have 'Lesson for' prefix"

    def test_should_write_memory_gates(self):
        """_should_write_memory should only write valuable experiences."""
        # 0 tool calls, no repair → no write
        assert _should_write_memory([], False) is False
        # 1 tool call, no repair → no write
        assert _should_write_memory([{"name": "list_files"}], False) is False
        # 2 tool calls, no repair → no write
        assert _should_write_memory([{"name": "a"}, {"name": "b"}], False) is False
        # 3+ tool calls → write
        assert _should_write_memory([{"name": "a"}, {"name": "b"}, {"name": "c"}], False) is True
        # Any tool call with repair → write
        assert _should_write_memory([{"name": "a"}], True) is True
        # 0 tool calls with repair → write
        assert _should_write_memory([], True) is True

    def test_error_counts_accumulate_across_restarts(self):
        """error_counts must persist across Learner instances (restarts)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ['PANDA_HOME'] = tmpdir
            config = make_config()

            # First instance: 2 occurrences
            mock_resp = json.dumps({
                "lessons": [], "recurring_errors": ["path error"],
                "is_structural": True, "structural_reason": "missing path rule",
            })
            learner1 = Learner(config)
            with patch("panda_agent.orchestrator.call_llm", return_value=mock_resp):
                for _ in range(2):
                    learner1.learn(
                        Task(instruction="test"),
                        ExecutionResult(success=False, error="path error"),
                        Evaluation(score=30),
                    )

            # Second instance (restart): should load counts
            learner2 = Learner(config)
            assert "missing path rule" in learner2._error_counts
            assert learner2._error_counts["missing path rule"] == 2

            # 3rd occurrence → trigger
            with patch("panda_agent.orchestrator.call_llm", return_value=mock_resp):
                learning = learner2.learn(
                    Task(instruction="test"),
                    ExecutionResult(success=False, error="path error"),
                    Evaluation(score=30),
                )
            assert learning.trigger_evolution is True


# ===========================================================================
# Level 3: Structural Improvement — trigger, patch, validate, rollback
# ===========================================================================

class TestLevel3Improvement:
    """Level 3: persistent error counts >= 3 → Improver triggers →
    patch source → pytest validates → behavior check → score must improve or rollback.

    Verifies trigger conditions and safety guarantees.
    """

    def test_trigger_requires_3_accumulated(self):
        """Level 3 should NOT trigger below 3, SHOULD trigger at 3."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ['PANDA_HOME'] = tmpdir
            config = make_config()
            mock_resp = json.dumps({
                "lessons": [], "recurring_errors": [],
                "is_structural": True, "structural_reason": "brain.py issue",
            })

            # 1st occurrence: no trigger
            learner = Learner(config)
            with patch("panda_agent.orchestrator.call_llm", return_value=mock_resp):
                r1 = learner.learn(Task("t"), ExecutionResult(success=False, error="err"), Evaluation(30))
            assert r1.trigger_evolution is False

            # 2nd: no trigger
            with patch("panda_agent.orchestrator.call_llm", return_value=mock_resp):
                r2 = learner.learn(Task("t"), ExecutionResult(success=False, error="err"), Evaluation(30))
            assert r2.trigger_evolution is False

            # 3rd: trigger!
            with patch("panda_agent.orchestrator.call_llm", return_value=mock_resp):
                r3 = learner.learn(Task("t"), ExecutionResult(success=False, error="err"), Evaluation(30))
            assert r3.trigger_evolution is True

    def test_trigger_requires_low_score(self):
        """Level 3 should only trigger when score < 70 AND is_structural."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ['PANDA_HOME'] = tmpdir
            config = make_config()
            mock_resp = json.dumps({
                "lessons": [], "recurring_errors": [],
                "is_structural": True, "structural_reason": "some issue",
            })

            learner = Learner(config)
            # Pre-load 2 occurrences
            with patch("panda_agent.orchestrator.call_llm", return_value=mock_resp):
                learner.learn(Task("t"), ExecutionResult(success=False, error="err"), Evaluation(30))
                learner.learn(Task("t"), ExecutionResult(success=False, error="err"), Evaluation(30))
                # 3rd with high score -> no trigger
                r = learner.learn(Task("t"), ExecutionResult(success=True), Evaluation(85))
            assert r.trigger_evolution is False, "Should not trigger when score >= 70"

    def test_improver_rollback_on_test_failure(self):
        """If Improver patch fails pytest, source must be restored."""
        config = make_config()
        improver = Improver(config)

        # Use a real temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write("def hello():\n    return 'world'\n")
            real_path = f.name

        try:
            from pathlib import Path
            source_path = Path(real_path)

            with patch("panda_agent.orchestrator.call_llm", return_value="```python\ndef hello():\n    return BROKEN\n```"):
                with patch("panda_agent.orchestrator._run_pytest", return_value=(False, "test failed")):
                    with patch("panda_agent.orchestrator._replace_function", return_value="def hello():\n    return BROKEN\n"):
                        with patch("panda_agent.orchestrator._extract_patch", return_value="def hello():\n    return BROKEN"):
                            result = improver._improve_file(
                                source_path, Evaluation(score=30, issues=["broken"]),
                                ["hello"], "evidence"
                            )

            assert result.patched is False
            # Original content should be restored
            restored = source_path.read_text(encoding="utf-8")
            assert "return 'world'" in restored, "Original source should be restored after failed patch"
        finally:
            os.unlink(real_path)


# ===========================================================================
# Cross-layer: Memory injection → agent behavior
# ===========================================================================

class TestMemoryInjectionClosedLoop:
    """Memory retrieval → system prompt injection → agent uses it.

    Verifies that retrieve_context output actually reaches the LLM
    via the system prompt, and is visible to the user.
    """

    def test_memory_injected_into_system_prompt(self):
        """retrieve_context output should be appended to system prompt."""
        config = make_config()
        config.memory.enabled = True  # MUST enable for memory injection
        mock_mem = MagicMock()
        mock_mem.is_available.return_value = True
        mock_mem.retrieve_context.return_value = "## Past Experience\n- [0.9] (reference) Use dir on Windows"

        captured_messages = []
        done_resp = LLMResponse(content="DONE: use dir", reasoning="", tool_calls=[])

        def capture_call(messages, config, **kw):
            captured_messages.append(list(messages))
            return done_resp

        with patch("panda_agent.react.call_llm_detailed", side_effect=capture_call):
            run_react("list files", config, memory=mock_mem)

        assert len(captured_messages) > 0
        system_msg = captured_messages[0][0]
        assert system_msg["role"] == "system"
        assert "Past Experience" in system_msg["content"]

    def test_memory_used_event_emitted(self):
        """When memory is injected, memory_used event should fire."""
        config = make_config()
        config.memory.enabled = True  # MUST enable
        mock_mem = MagicMock()
        mock_mem.is_available.return_value = True
        mock_mem.retrieve_context.return_value = "## Past Experience\n- [0.9] (reference) test lesson"

        events = []
        done_resp = LLMResponse(content="DONE: done", reasoning="", tool_calls=[])

        with patch("panda_agent.react.call_llm_detailed", return_value=done_resp):
            run_react("test", config, memory=mock_mem,
                      on_event=lambda et, msg: events.append((et, msg)))

        # Should have a memory_used event
        mem_events = [e for e in events if e[0] == "memory_used"]
        assert len(mem_events) >= 1, f"Should emit memory_used event. Events: {[e[0] for e in events]}"

    def test_no_memory_event_when_empty(self):
        """When memory returns nothing, no memory_used event should fire."""
        config = make_config()
        mock_mem = MagicMock()
        mock_mem.retrieve_context.return_value = ""

        events = []
        done_resp = LLMResponse(content="DONE: done", reasoning="", tool_calls=[])

        with patch("panda_agent.react.call_llm_detailed", return_value=done_resp):
            run_react("test", config, memory=mock_mem,
                      on_event=lambda et, msg: events.append((et, msg)))

        mem_events = [e for e in events if e[0] == "memory_used"]
        assert len(mem_events) == 0

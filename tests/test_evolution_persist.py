"""Test: error_counts persistence + self-evolution observability.

Level 2 Learner must persist error counts across restarts.
Level 3 Improver trigger must work with persisted counts.
"""
import os
import json
import tempfile

from panda_agent.orchestrator import Learner
from panda_agent.config import Config, ModelConfig, AgentConfig, MemoryConfig, EvolutionConfig, DisplayConfig
from panda_agent.types import Task, ExecutionResult, Evaluation
from unittest.mock import patch


def make_config():
    return Config(
        model=ModelConfig(default="GLM52RJPT", api_key="k", base_url="u", max_tokens=8192),
        agent=AgentConfig(max_turns=5),
        memory=MemoryConfig(enabled=False),
        evolution=EvolutionConfig(),
        display=DisplayConfig(),
    )


class TestErrorCountPersistence:
    """Learner must persist error counts to ~/.panda/error_counts.json."""

    def test_error_counts_saved_to_disk(self):
        """After learn() with a structural issue, error_counts.json should exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ['PANDA_HOME'] = tmpdir
            config = make_config()
            learner = Learner(config)

            # Mock LLM to return a structural issue
            mock_response = json.dumps({
                "lessons": ["On Windows, use dir not ls"],
                "recurring_errors": ["path not found error"],
                "is_structural": True,
                "structural_reason": "brain.py missing Windows path rule",
            })

            task = Task(instruction="list desktop files")
            exec_result = ExecutionResult(tool_calls=[], success=False, error="not found")
            evaluation = Evaluation(score=30, issues=["failed"])

            with patch("panda_agent.orchestrator.call_llm", return_value=mock_response):
                learner.learn(task, exec_result, evaluation)

            # error_counts.json should be created
            counts_path = os.path.join(tmpdir, "error_counts.json")
            assert os.path.exists(counts_path), "error_counts.json should be persisted"

            data = json.loads(open(counts_path, encoding='utf-8').read())
            assert "brain.py missing windows path rule" in data
            assert data["brain.py missing windows path rule"] == 1

    def test_error_counts_loaded_on_restart(self):
        """New Learner instance should load persisted error counts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ['PANDA_HOME'] = tmpdir
            # Write existing error counts
            counts_path = os.path.join(tmpdir, "error_counts.json")
            with open(counts_path, 'w', encoding='utf-8') as f:
                json.dump({"some structural issue": 2}, f)

            config = make_config()
            learner = Learner(config)

            # Should have loaded the existing counts
            assert "some structural issue" in learner._error_counts
            assert learner._error_counts["some structural issue"] == 2

    def test_trigger_fires_after_3_accumulated(self):
        """After 3 accumulated occurrences (across restarts), trigger should fire."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ['PANDA_HOME'] = tmpdir
            counts_path = os.path.join(tmpdir, "error_counts.json")
            with open(counts_path, 'w', encoding='utf-8') as f:
                json.dump({"missing windows path rule": 2}, f)

            config = make_config()
            learner = Learner(config)

            mock_response = json.dumps({
                "lessons": ["Use dir on Windows"],
                "recurring_errors": [],
                "is_structural": True,
                "structural_reason": "missing windows path rule",
            })

            task = Task(instruction="list files")
            exec_result = ExecutionResult(tool_calls=[], success=True)
            evaluation = Evaluation(score=30, issues=["low score"])

            with patch("panda_agent.orchestrator.call_llm", return_value=mock_response):
                learning = learner.learn(task, exec_result, evaluation)

            assert learning.trigger_evolution is True, "Should trigger after 3rd occurrence"
            assert "3 times" in learning.trigger_reason

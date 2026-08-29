"""Tests for PandaAgent framework — types, config, tools, react, orchestrator.

Uses mocks for LLM calls — no real API needed.
"""

from unittest.mock import MagicMock, patch

from panda_agent.types import (
    Task, ExecutionResult, Evaluation, EvolutionResult, Event,
)
from panda_agent.config import Config, ModelConfig, load_config, save_config
from panda_agent.llm import call_llm
from panda_agent.brain import build_system_prompt, SYSTEM_PROMPT, max_turns_for_task
from panda_agent.tools import TOOLS, execute_tool, get_tool_descriptions
from panda_agent.react import _parse_tool_call, _parse_done, _parse_failed
from panda_agent.memory import MemoryClient


# ---------------------------------------------------------------------------
# Type tests
# ---------------------------------------------------------------------------

class TestTypes:
    def test_task_defaults(self):
        t = Task()
        assert t.input_path == ""
        assert t.metadata == {}

    def test_execution_result_defaults(self):
        r = ExecutionResult()
        assert r.success is True
        assert r.error is None

    def test_evaluation_defaults(self):
        e = Evaluation()
        assert e.score == 0.0
        assert e.issues == []

    def test_event(self):
        ev = Event(type="test", message="hello", round=1)
        assert ev.type == "test"
        assert ev.data == {}


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

class TestConfig:
    def test_model_config_defaults(self):
        c = ModelConfig()
        assert c.default == "gpt-4o"
        assert c.max_tokens == 8192

    def test_config_defaults(self):
        c = Config()
        assert c.model.default == "gpt-4o"
        assert c.agent.max_turns == 10
        assert c.memory.enabled is True
        assert c.evolution.improve_brain is True

    def test_load_config_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PANDA_HOME", str(tmp_path))
        c = load_config()
        assert c.model.default == "gpt-4o"

    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PANDA_HOME", str(tmp_path))
        c = Config()
        c.model.default = "test-model"
        save_config(c)
        loaded = load_config()
        assert loaded.model.default == "test-model"


# ---------------------------------------------------------------------------
# Brain tests
# ---------------------------------------------------------------------------

class TestBrain:
    def test_system_prompt_has_tools_placeholder(self):
        assert "{tool_descriptions}" in SYSTEM_PROMPT

    def test_build_system_prompt(self):
        prompt = build_system_prompt("- read_file: read\n- write_file: write")
        assert "read_file" in prompt
        assert "write_file" in prompt

    def test_max_turns_simple(self):
        assert max_turns_for_task("just list files") == 8

    def test_max_turns_complex(self):
        assert max_turns_for_task("build a web server") == 30

    def test_max_turns_default(self):
        assert max_turns_for_task("check something") == 12


# ---------------------------------------------------------------------------
# Tools tests
# ---------------------------------------------------------------------------

class TestTools:
    def test_tools_registered(self):
        assert "read_file" in TOOLS
        assert "write_file" in TOOLS
        assert "search_files" in TOOLS
        assert "run_command" in TOOLS

    def test_get_tool_descriptions(self):
        desc = get_tool_descriptions()
        assert "read_file" in desc
        assert "write_file" in desc

    def test_execute_read_file(self, tmp_path, monkeypatch):
        # tmp_path lives outside the default workspace, so scope the boundary
        # to it rather than widening the boundary itself.
        monkeypatch.setenv("PANDA_WORKSPACE", str(tmp_path))
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = execute_tool("read_file", {"path": str(f)})
        assert "hello world" in result

    def test_execute_write_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PANDA_WORKSPACE", str(tmp_path))
        path = tmp_path / "out.txt"
        result = execute_tool("write_file", {"path": str(path), "content": "test"})
        assert "Wrote" in result
        assert path.read_text() == "test"

    def test_execute_unknown_tool(self):
        result = execute_tool("nonexistent", {})
        assert "unknown tool" in result

    def test_execute_list_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PANDA_WORKSPACE", str(tmp_path))
        (tmp_path / "a.txt").write_text("a")
        result = execute_tool("list_files", {"path": str(tmp_path)})
        assert "a.txt" in result

    def test_execute_patch_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PANDA_WORKSPACE", str(tmp_path))
        f = tmp_path / "test.txt"
        f.write_text("hello old world")
        result = execute_tool("patch_file", {"path": str(f), "old_string": "old", "new_string": "new"})
        assert "Patched" in result
        assert "hello new world" in f.read_text()


# ---------------------------------------------------------------------------
# React parsing tests
# ---------------------------------------------------------------------------

class TestReactParsing:
    def test_parse_tool_call(self):
        text = 'TOOL_CALL: {"name": "read_file", "args": {"path": "test.py"}}'
        result = _parse_tool_call(text)
        assert result["name"] == "read_file"
        assert result["args"]["path"] == "test.py"

    def test_parse_done(self):
        text = "DONE: task completed successfully"
        assert _parse_done(text) == "task completed successfully"

    def test_parse_done_multiline(self):
        """DONE: should capture multi-line answers, not just first line."""
        text = "DONE: 我是一个AI助手\n1. 文件操作\n2. 命令行\n3. 搜索文件"
        result = _parse_done(text)
        assert result is not None
        assert "文件操作" in result
        assert "命令行" in result
        assert "搜索文件" in result

    def test_parse_failed(self):
        text = "FAILED: could not find file"
        assert _parse_failed(text) == "could not find file"

    def test_parse_no_tool_call(self):
        assert _parse_tool_call("just thinking...") is None

    def test_parse_no_done(self):
        assert _parse_done("thinking...") is None


# ---------------------------------------------------------------------------
# LLM tests (mocked)
# ---------------------------------------------------------------------------

class TestLLM:
    @patch("panda_agent.llm.requests.post")
    def test_call_llm_content(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.iter_lines.return_value = [
            'data: {"choices":[{"delta":{"content":"hello"}}]}',
            'data: {"choices":[{"delta":{"content":" world"}}]}',
            "data: [DONE]",
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        config = ModelConfig(base_url="http://test/v1", api_key="k", default="m")
        result = call_llm([{"role": "user", "content": "hi"}], config)
        assert result == "hello world"

    @patch("panda_agent.llm.requests.post")
    def test_call_llm_reasoning_fallback(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.iter_lines.return_value = [
            'data: {"choices":[{"delta":{"content":"","reasoning_content":"thinking"}}]}',
            "data: [DONE]",
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        config = ModelConfig(base_url="http://test/v1", api_key="k", default="m")
        result = call_llm([{"role": "user", "content": "hi"}], config)
        assert result == "thinking"

    @patch("panda_agent.llm.requests.post")
    def test_call_llm_timeout(self, mock_post):
        import requests as req
        mock_post.side_effect = req.Timeout("timeout")
        config = ModelConfig(base_url="http://test/v1", api_key="k", default="m")
        result = call_llm([{"role": "user", "content": "hi"}], config)
        assert "ERROR" in result


# ---------------------------------------------------------------------------
# Memory tests (mocked)
# ---------------------------------------------------------------------------

class TestMemory:
    def test_retrieve_empty(self):
        """Embedded memory returns empty list when no data."""
        # MemoryClient uses EmbeddedMemory which may or may not be initialized
        # depending on whether sentence-transformers is available.
        # Just verify the interface works without crashing.
        client = MemoryClient()
        results = client.retrieve("nonexistent query that should return empty or small results")
        assert isinstance(results, list)

    def test_stats(self):
        """Stats returns a dict (may have error key if engine unavailable)."""
        client = MemoryClient()
        stats = client.stats()
        assert isinstance(stats, dict)

    def test_is_available(self):
        """is_available returns a bool."""
        client = MemoryClient()
        assert isinstance(client.is_available(), bool)


# ---------------------------------------------------------------------------
# Orchestrator tests
# ---------------------------------------------------------------------------

class TestOrchestrator:
    def test_evolution_result_defaults(self):
        r = EvolutionResult()
        assert r.rounds == []
        assert r.final_score == 0.0

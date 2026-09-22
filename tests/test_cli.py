"""CLI integration tests.

These tests cover the entry-point wiring that sits between user-invoked
commands (``panda chat``, ``panda evolve``) and the orchestrator. The CLI is
the user's primary entry into self-evolution; wiring bugs here silently skip
the safety gates the rest of the system enforces.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml


def _write_config(tmp_path: Path, evolution: dict) -> Path:
    cfg_path = tmp_path / "config.yaml"
    cfg = {
        "model": {"default": "test-model", "api_key": "k", "base_url": "u"},
        "agent": {"max_turns": 1, "max_retries": 1},
        "memory": {"enabled": False},
        "evolution": evolution,
        "display": {"tui": False},
    }
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return cfg_path


def test_cli_wires_benchmark_gate_when_suite_configured(tmp_path, monkeypatch):
    """US-A1: when ``evolution.benchmark_suite`` points at a real tasks.yaml,
    the CLI must construct an Improver whose ``benchmark_gate`` is callable
    and whose ``baseline`` is a populated BenchmarkResult.

    Without this wiring, the regression gate is dead code: a patch that keeps
    unit tests green but regresses measured behaviour silently lands. The
    gate exists exactly to catch the case the unit suite cannot see.
    """
    # 1. Create a tasks.yaml the CLI will pick up.
    suite_path = tmp_path / "tasks.yaml"
    suite_path.write_text(
        "- id: t1\n"
        "  instruction: 'list files'\n"
        "  scorer: exact_match\n"
        "  expected:\n"
        "    contains: ['config.py']\n"
        "  weight: 1.0\n",
        encoding="utf-8",
    )

    # 2. Config that references the suite and sets a tolerance.
    evolution_cfg = {
        "target_score": 90,
        "max_rounds": 1,
        "improve_brain": False,
        "improve_tools": False,
        "benchmark_suite": str(suite_path),
        "benchmark_tolerance": 5.0,
    }
    cfg_path = _write_config(tmp_path, evolution_cfg)
    monkeypatch.setenv("PANDA_HOME", str(tmp_path))

    # 3. Patch the heavy components so we can inspect the Improver the CLI
    #    actually built. We don't run the loop — we only care about the wiring.
    captured = {}

    class _FakeTUI:
        def __init__(self, *a, **kw):
            pass

        def banner(self):
            pass

        def event(self, *a, **kw):
            pass

        def reasoning(self, *a, **kw):
            pass

        def print(self, *a, **kw):
            pass

        def info(self, *a, **kw):
            pass

        def answer(self, *a, **kw):
            pass

        def error(self, *a, **kw):
            pass

        def user_input(self):
            raise EOFError

    class _FakeExecutor:
        def __init__(self, *a, **kw):
            pass

        execute = lambda self, task: None

    def fake_record(*a, **kw):
        return True

    # Patch Improver (the target of the wiring test) so we can capture it.
    import panda_agent.orchestrator as orch_mod
    import panda_agent.cli as cli_mod

    real_improver = orch_mod.Improver

    def capture_improver(config, *args, **kwargs):
        # The CLI calls Improver(config). Some call sites may also pass
        # gate=.../baseline=...; accept either form.
        imp = real_improver(config)
        # Store the call so the test can inspect it.
        captured["instance"] = imp
        captured["args"] = args
        captured["kwargs"] = kwargs
        return imp

    monkeypatch.setattr(orch_mod, "Improver", capture_improver)
    monkeypatch.setattr(cli_mod, "TUI", _FakeTUI)
    monkeypatch.setattr(orch_mod, "Executor", _FakeExecutor)

    # Patch record_evolution so no disk write happens.
    import panda_agent.evolution_history as hist_mod
    monkeypatch.setattr(hist_mod, "record_evolution", fake_record)

    # Patch run_react so the chat loop doesn't try to call an LLM. Use a
    # one-shot query to force the chat path that constructs the Improver
    # up front (matches the production wiring).
    def fake_run_react(*a, **kw):
        from panda_agent.react import ReActResult
        return ReActResult(success=True, answer="ok", turns=1)

    monkeypatch.setattr(cli_mod, "run_react", fake_run_react)

    args = SimpleNamespace(
        command="chat", query="hello", model=""
    )

    cli_mod.cmd_chat(args)

    # The Improver must exist and the gate must be wired.
    assert "instance" in captured, "Improver was never constructed by the CLI"
    imp = captured["instance"]
    assert imp.benchmark_gate is not None, (
        "Improver constructed by the CLI has no benchmark_gate even though "
        "evolution.benchmark_suite points at an existing tasks.yaml"
    )
    assert imp.baseline is not None, (
        "Improver constructed by the CLI has no baseline even though "
        "evolution.benchmark_suite points at an existing tasks.yaml"
    )
    # The baseline must be a populated BenchmarkResult.
    from panda_agent.benchmark import BenchmarkResult
    assert isinstance(imp.baseline, BenchmarkResult)
    assert imp.baseline.scores, "baseline BenchmarkResult has no scores"
    assert imp.tolerance == 5.0

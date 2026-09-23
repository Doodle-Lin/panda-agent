"""Audit Phase 8 — doom loop salvage waits one turn before firing.

Covers audit finding #8 (the unfinished part): the pre-fix code triggered
salvage on the *first* doom detection, because the salvage branch ran
when `len(simple_calls) >= 4 and _check_doom_loop(simple_calls[-3:])`
— which is exactly the condition that triggered entry. The injected
warning was never given a chance to be tested by the agent.

The fix tracks a `doom_strike` counter:
  - first detection (signature S): inject warning, continue. Strike = 1.
  - next turn, if the agent escapes (no doom detected, or doom with a
    different signature): reset strike to 0.
  - second detection with the SAME signature S: fire salvage.
"""
from __future__ import annotations

from panda_agent.llm import LLMResponse
from panda_agent.react import run_react


def _make_config():
    from panda_agent.config import (
        AgentConfig, Config, DisplayConfig, MemoryConfig, ModelConfig,
    )
    return Config(
        model=ModelConfig(default="m", api_key="k", base_url="u"),
        agent=AgentConfig(max_turns=5),
        memory=MemoryConfig(enabled=False),
        display=DisplayConfig(),
    )


def _tool_call_resp(name, args, tool_id="t0"):
    """Build an LLMResponse that looks like a parsed native FC tool call.

    The llm.py parser produces dicts with keys {"id", "name", "args"}, not
    the raw OpenAI {"id", "type", "function": {"name", "arguments"}} shape.
    """
    return LLMResponse(
        content="",
        reasoning="",
        tool_calls=[{"id": tool_id, "name": name, "args": args}],
    )


class TestDoomLoopSalvageWaitsOneTurn:
    """Salvage must fire on the SECOND consecutive doom detection with the
    same signature, not the first. The first detection only injects a
    warning so the agent has a turn to react."""

    def test_first_doom_injects_warning_does_not_salvage(self, monkeypatch):
        """On the first doom detection (3 identical calls in a row) the
        loop injects a warning and continues. Salvage is NOT attempted yet
        — the agent gets the next turn to try something different."""
        # We patch _build_salvage_prompt to track whether salvage was called.
        # On the first doom detection it must NOT be called.
        import panda_agent.react as react_mod

        salvage_called = {"count": 0}
        original_salvage = react_mod._build_salvage_prompt

        def tracking_salvage(calls):
            salvage_called["count"] += 1
            return original_salvage(calls)

        monkeypatch.setattr(react_mod, "_build_salvage_prompt", tracking_salvage)

        # Sequence: 3 identical read_file calls → doom detected on the 3rd.
        # We make the 4th LLM call return DONE so the loop exits cleanly
        # before salvage would fire on the second detection.
        # The agent should: detect doom → inject warning → next turn the
        # agent emits DONE → loop exits. Salvage never fires.
        responses = [
            _tool_call_resp("list_files", {"path": "."}),
            _tool_call_resp("list_files", {"path": "."}),
            _tool_call_resp("list_files", {"path": "."}),
            # After the doom warning, the agent emits DONE:
            LLMResponse(content="DONE: finished", reasoning="", tool_calls=[]),
        ]
        resp_iter = iter(responses)

        def fake_call(messages, model, **kw):
            return next(resp_iter)

        # Patch tool execution so list_files returns something benign.
        monkeypatch.setattr(
            react_mod, "execute_tool",
            lambda name, args: "ok",
        )
        monkeypatch.setattr(react_mod, "call_llm_detailed", fake_call)

        config = _make_config()
        run_react("test task", config)
        assert salvage_called["count"] == 0, (
            "Salvage fired on the first doom detection; the warning never "
            "got a turn to help the agent recover. Expected 0 salvage calls, "
            f"got {salvage_called['count']}."
        )

    def test_second_doom_fires_salvage(self, monkeypatch):
        """When the agent does NOT escape the doom loop after the warning
        (same signature repeats), salvage fires on the second detection."""
        import panda_agent.react as react_mod

        salvage_called = {"count": 0}
        original_salvage = react_mod._build_salvage_prompt

        def tracking_salvage(calls):
            salvage_called["count"] += 1
            return original_salvage(calls)

        monkeypatch.setattr(react_mod, "_build_salvage_prompt", tracking_salvage)

        # 6 identical calls: 3 triggers first doom (strike=1, warning only).
        # 4th-6th are still identical (same signature), so on the 6th call
        # the second doom is detected and salvage fires.
        responses = [
            _tool_call_resp("list_files", {"path": "."}),
            _tool_call_resp("list_files", {"path": "."}),
            _tool_call_resp("list_files", {"path": "."}),  # doom 1: warning
            _tool_call_resp("list_files", {"path": "."}),
            _tool_call_resp("list_files", {"path": "."}),
            _tool_call_resp("list_files", {"path": "."}),  # doom 2: salvage
            # Salvage call_llm_detailed returns DONE so the loop exits.
            LLMResponse(content="DONE: salvaged", reasoning="", tool_calls=[]),
        ]
        resp_iter = iter(responses)

        def fake_call(messages, model, **kw):
            return next(resp_iter)

        monkeypatch.setattr(
            react_mod, "execute_tool",
            lambda name, args: "ok",
        )
        monkeypatch.setattr(react_mod, "call_llm_detailed", fake_call)

        config = _make_config()
        # Give enough turns for both detections to occur.
        config.agent.max_turns = 10
        run_react("test task", config)
        assert salvage_called["count"] >= 1, (
            "Salvage never fired even after a second consecutive doom with "
            f"the same signature. salvage count = {salvage_called['count']}"
        )

    def test_agent_gets_one_llm_turn_between_warning_and_salvage(self, monkeypatch):
        """The real contract from audit #8: between the first doom warning
        and the salvage call, the agent must get at least one LLM turn
        where it can react to the warning. Salvage must NOT fire on the
        same LLM turn that the first warning is injected.

        We pin this by counting call_llm_detailed invocations: the salvage
        call must be at least the 3rd LLM invocation (turn 1, turn 2 with
        warning visible, turn 3 where salvage fires after re-detection).
        """
        import panda_agent.react as react_mod

        call_count = {"n": 0}
        salvage_fired_at = {"n": None}
        real_build_salvage = react_mod._build_salvage_prompt

        def fake_call(messages, model, **kw):
            call_count["n"] += 1
            return next(resp_iter)

        def tracking_salvage(calls):
            salvage_fired_at["n"] = call_count["n"]
            return real_build_salvage(calls)

        monkeypatch.setattr(react_mod, "_build_salvage_prompt", tracking_salvage)

        # Turn 1: first identical call. Turn 2: second identical (doom
        # detected at len=3 within this turn if 3 calls arrive, but we
        # send 1 call per turn). Turn 3: third identical → doom, warn,
        # break (len=3, no salvage). Turn 4: fourth identical → doom,
        # len=4, salvage.
        responses = [
            _tool_call_resp("list_files", {"path": "."}),  # turn 1
            _tool_call_resp("list_files", {"path": "."}),  # turn 2
            _tool_call_resp("list_files", {"path": "."}),  # turn 3: doom 1 (warn)
            _tool_call_resp("list_files", {"path": "."}),  # turn 4: doom 2 (salvage)
            LLMResponse(content="DONE: salvaged", reasoning="", tool_calls=[]),
        ]
        resp_iter = iter(responses)

        monkeypatch.setattr(
            react_mod, "execute_tool",
            lambda name, args: "ok",
        )
        monkeypatch.setattr(react_mod, "call_llm_detailed", fake_call)

        config = _make_config()
        config.agent.max_turns = 10
        run_react("test task", config)

        assert salvage_fired_at["n"] is not None, "salvage never fired"
        # Salvage must fire on LLM call >= 4: turns 1,2,3 (first doom+warn),
        # then at least one more LLM turn where the agent sees the warning,
        # then salvage. The minimum is 4 (turn 4 = the agent's chance after
        # the warning at turn 3).
        assert salvage_fired_at["n"] >= 4, (
            "Salvage fired on the same LLM turn as the first warning (call "
            f"{salvage_fired_at['n']}); the agent never got a turn to react "
            "to the warning. Expected salvage on call >= 4."
        )

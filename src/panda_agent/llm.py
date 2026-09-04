"""Streaming LLM caller — works with reasoning and non-reasoning models.

Reasoning models (e.g. GLM52RJPT) put output in reasoning_content
while content stays empty. This caller collects both and falls back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import requests

from .config import ModelConfig


@dataclass
class LLMResponse:
    """Detailed LLM response — reasoning, content, and tool calls separated."""
    content: str = ""        # The model's final answer / action
    reasoning: str = ""      # The model's thinking process (reasoning models only)
    error: str = ""           # Non-empty if the API call failed
    tool_calls: list = field(default_factory=list)  # Native function calling tool_calls

    @property
    def is_error(self) -> bool:
        return bool(self.error)

    @property
    def text(self) -> str:
        """Fallback: content if available, else reasoning."""
        return self.content if self.content.strip() else self.reasoning


def _call_llm_raw(
    messages: list[dict],
    config: ModelConfig,
    model_name: str,
    max_tokens: int | None,
    temperature: float,
    timeout: int,
    tools: list[dict] | None,
) -> LLMResponse:
    """Core LLM call logic — used by call_llm_detailed and fallback."""
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    reasoning_models = {"GLM52RJPT", "glm-4-reasoning", "o1", "o3", "deepseek-r1"}
    is_reasoning = model_name in reasoning_models or "reasoning" in model_name.lower()
    base_max = max_tokens or config.max_tokens
    effective_max = max(base_max, 16384) if is_reasoning else base_max
    payload = {
        "model": model_name,
        "messages": messages,
        "max_tokens": effective_max,
        "temperature": temperature,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools

    resp = requests.post(
        f"{config.base_url}/chat/completions",
        json=payload,
        headers=headers,
        timeout=(timeout, timeout),  # (connect, read) — read timeout per chunk
        stream=True,
    )
    resp.raise_for_status()
    resp.encoding = "utf-8"

    content = ""
    reasoning = ""
    tool_calls_acc: dict[int, dict] = {}

    # Use a deadline so a stalled stream (no data for a long time) is
    # treated as a timeout rather than hanging forever. iter_lines with a
    # read timeout catches the common case where the connection opens but
    # the server never sends the next chunk.
    import time as _time
    deadline = _time.monotonic() + timeout
    for line in resp.iter_lines(decode_unicode=True):
        if _time.monotonic() > deadline:
            raise requests.Timeout("stream stalled: no data within timeout window")
        # Reset deadline on each received line — the timeout is per-gap,
        # not per-total-stream.
        deadline = _time.monotonic() + timeout
        if not line or not line.startswith("data: "):
            continue
        data = line[6:]
        if data.strip() == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        delta = chunk.get("choices", [{}])[0].get("delta", {})
        content += delta.get("content") or ""
        reasoning += delta.get("reasoning_content") or ""

        tc_deltas = delta.get("tool_calls")
        if tc_deltas:
            for tc in tc_deltas:
                idx = tc.get("index", 0)
                if idx not in tool_calls_acc:
                    tool_calls_acc[idx] = {"id": tc.get("id", ""), "name": "", "arguments": ""}
                if tc.get("id"):
                    tool_calls_acc[idx]["id"] = tc["id"]
                fn = tc.get("function", {})
                if fn.get("name"):
                    tool_calls_acc[idx]["name"] = fn["name"]
                if fn.get("arguments"):
                    tool_calls_acc[idx]["arguments"] += fn["arguments"]

    import re as _re
    reasoning = _re.sub(r"</?think>", "", reasoning).strip()
    content = _re.sub(r"</?think>", "", content).strip()

    parsed_tool_calls = []
    for idx in sorted(tool_calls_acc.keys()):
        tc = tool_calls_acc[idx]
        args_str = tc["arguments"]
        try:
            args = json.loads(args_str) if args_str else {}
        except json.JSONDecodeError:
            args = {"_raw": args_str}
        parsed_tool_calls.append({"id": tc["id"], "name": tc["name"], "args": args})

    return LLMResponse(content=content, reasoning=reasoning, tool_calls=parsed_tool_calls)


def call_llm_detailed(
    messages: list[dict[str, str]],
    config: ModelConfig,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.2,
    timeout: int = 180,
    tools: list[dict] | None = None,
) -> LLMResponse:
    """Call LLM with native function calling support.

    Retries on transient failures (timeout, connection reset, 5xx) up to
    ``max_retries`` times with exponential backoff before falling back to
    ``config.fallback`` model. This is what makes the evolution loop
    resilient to flaky endpoints -- a single connection reset no longer
    scores the whole round 0.
    """
    import time

    effective_model = model or config.default
    max_retries = 3
    retry_delays = [2, 5, 10]  # seconds, exponential-ish

    for attempt in range(max_retries + 1):
        try:
            return _call_llm_raw(
                messages, config, effective_model,
                max_tokens, temperature, timeout, tools,
            )
        except (requests.Timeout, requests.ConnectionError) as e:
            if attempt < max_retries:
                delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                import sys
                print(
                    f"[llm] retry {attempt + 1}/{max_retries} after "
                    f"{type(e).__name__} ({delay}s delay)",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            # Exhausted retries; try fallback model if configured.
            fallback_model = getattr(config, "fallback", "") or ""
            if fallback_model and fallback_model != effective_model:
                try:
                    return _call_llm_raw(
                        messages, config, fallback_model,
                        max_tokens, temperature, timeout, tools,
                    )
                except Exception:
                    pass
            return LLMResponse(error=f"LLM call failed after {max_retries} retries: {e}")
        except requests.HTTPError as e:
            # 5xx is transient; 4xx is not (bad request, auth, etc.).
            status = getattr(e.response, "status_code", 0)
            if 500 <= status < 600 and attempt < max_retries:
                delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                import sys
                print(
                    f"[llm] retry {attempt + 1}/{max_retries} after HTTP {status} "
                    f"({delay}s delay)",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            return LLMResponse(error=f"LLM HTTP {status}: {e}")
        except Exception as e:
            return LLMResponse(error=f"unexpected: {e}")


def call_llm(
    messages: list[dict[str, str]],
    config: ModelConfig,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.2,
    timeout: int = 180,
) -> str:
    """Call LLM via streaming API and return full text.

    Backward-compatible wrapper around call_llm_detailed.
    Returns content if available, else reasoning, else error string.
    """
    resp = call_llm_detailed(
        messages, config,
        model=model, max_tokens=max_tokens,
        temperature=temperature, timeout=timeout,
    )
    if resp.is_error:
        return f"ERROR: {resp.error}"
    return resp.text


def call_llm_simple(
    prompt: str,
    config: ModelConfig,
    *,
    system: str | None = None,
    **kwargs: Any,
) -> str:
    """Convenience wrapper: single prompt → response."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return call_llm(messages, config, **kwargs)

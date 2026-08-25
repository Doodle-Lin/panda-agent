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
    """Call LLM and return detailed response with reasoning, content, and tool calls.

    For reasoning models (GLM52RJPT), reasoning_content contains the thinking
    process and content contains the final answer. For non-reasoning models,
    content contains the full response and reasoning is empty.

    If tools is provided, enables native function calling — the API returns
    tool_calls in the response with guaranteed-valid JSON arguments.
    """
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    effective_model = model or config.default
    reasoning_models = {"GLM52RJPT", "glm-4-reasoning", "o1", "o3", "deepseek-r1"}
    is_reasoning = effective_model in reasoning_models or "reasoning" in effective_model.lower()
    base_max = max_tokens or config.max_tokens
    effective_max = max(base_max, 16384) if is_reasoning else base_max
    payload = {
        "model": effective_model,
        "messages": messages,
        "max_tokens": effective_max,
        "temperature": temperature,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools

    try:
        resp = requests.post(
            f"{config.base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=timeout,
            stream=True,
        )
        resp.raise_for_status()
        resp.encoding = "utf-8"

        content = ""
        reasoning = ""
        # Accumulate tool_calls from streaming deltas
        # tool_calls format: [{"index": 0, "id": "call_xxx", "function": {"name": "...", "arguments": "..."}}]
        tool_calls_acc: dict[int, dict] = {}

        for line in resp.iter_lines(decode_unicode=True):
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

            # Parse tool_calls from streaming delta
            tc_deltas = delta.get("tool_calls")
            if tc_deltas:
                for tc in tc_deltas:
                    idx = tc.get("index", 0)
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": tc.get("id", ""),
                            "name": "",
                            "arguments": "",
                        }
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

        # Parse accumulated tool_calls into structured format
        parsed_tool_calls = []
        for idx in sorted(tool_calls_acc.keys()):
            tc = tool_calls_acc[idx]
            args_str = tc["arguments"]
            try:
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                args = {"_raw": args_str}
            parsed_tool_calls.append({
                "id": tc["id"],
                "name": tc["name"],
                "args": args,
            })

        return LLMResponse(content=content, reasoning=reasoning, tool_calls=parsed_tool_calls)
    except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
        return LLMResponse(error=f"LLM call failed: {e}")
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

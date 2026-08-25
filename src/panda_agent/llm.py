"""Streaming LLM caller — works with reasoning and non-reasoning models.

Reasoning models (e.g. GLM52RJPT) put output in reasoning_content
while content stays empty. This caller collects both and falls back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

from .config import ModelConfig


@dataclass
class LLMResponse:
    """Detailed LLM response — reasoning and content separated."""
    content: str = ""        # The model's final answer / action
    reasoning: str = ""      # The model's thinking process (reasoning models only)
    error: str = ""           # Non-empty if the API call failed

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
) -> LLMResponse:
    """Call LLM and return detailed response with reasoning and content separated.

    For reasoning models (GLM52RJPT), reasoning_content contains the thinking
    process and content contains the final answer. For non-reasoning models,
    content contains the full response and reasoning is empty.
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

        import re as _re
        reasoning = _re.sub(r"</?think>", "", reasoning).strip()
        content = _re.sub(r"</?think>", "", content).strip()

        return LLMResponse(content=content, reasoning=reasoning)
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

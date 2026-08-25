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

    Args:
        messages: OpenAI-format message list.
        config: Model configuration.
        model: Override model name.
        max_tokens: Override max_tokens (min 16384 for reasoning models).
        temperature: Sampling temperature.
        timeout: Request timeout in seconds.

    Returns:
        Full response text (content, or reasoning_content fallback).
    """
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    effective_model = model or config.default
    # Reasoning models (e.g. GLM52RJPT) need >=16384 max_tokens or they hang.
    # Non-reasoning models (e.g. GLM-5.2) work fine with config default and
    # forcing 16384 makes them generate very long responses, causing slowness.
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

        # Force UTF-8: streaming responses often lack charset in Content-Type,
        # and requests defaults to ISO-8859-1 per HTTP spec, garbling CJK text.
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

        return content if content.strip() else reasoning
    except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
        return f"ERROR: LLM call failed: {e}"
    except Exception as e:
        return f"ERROR: unexpected: {e}"


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

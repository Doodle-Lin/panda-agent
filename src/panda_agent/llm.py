"""Streaming LLM caller — works with both reasoning and non-reasoning models.

Reasoning models (e.g. GLM52RJPT) put their output in ``reasoning_content``
while ``content`` stays empty.  This caller collects both streams and
falls back to ``reasoning_content`` when ``content`` is empty.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class LLMConfig:
    """Configuration for an LLM API call."""

    base_url: str
    api_key: str
    model: str
    max_tokens: int = 8192
    temperature: float = 0.2


def load_llm_config(
    prefix: str = "",
    base_url_env: str = "",
    api_key_env: str = "",
    model_env: str = "",
    max_tokens_env: str = "",
) -> LLMConfig:
    """Load LLM config from environment variables.

    Args:
        prefix: If non-empty, looks for {prefix}_BASE_URL etc.
        base_url_env: Explicit env var name (overrides prefix).
        api_key_env: Explicit env var name.
        model_env: Explicit env var name.
        max_tokens_env: Explicit env var name.
    """
    if prefix:
        base_url_env = base_url_env or f"{prefix}_BASE_URL"
        api_key_env = api_key_env or f"{prefix}_API_KEY"
        model_env = model_env or f"{prefix}_MODEL"
        max_tokens_env = max_tokens_env or f"{prefix}_MAX_TOKENS"

    base_url = os.getenv(base_url_env, "http://localhost:8000/v1")
    api_key = os.getenv(api_key_env, "")
    model = os.getenv(model_env, "gpt-4o")
    max_tokens = int(os.getenv(max_tokens_env, "8192"))

    return LLMConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        max_tokens=max_tokens,
    )


def call_llm(
    prompt: str,
    config: LLMConfig,
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
    timeout: int = 180,
) -> str:
    """Call LLM via streaming API and return full text.

    For reasoning models where ``content`` is empty, falls back to
    ``reasoning_content``.

    Args:
        prompt: The user prompt.
        config: LLM configuration.
        max_tokens: Override max_tokens (e.g. 16384 for reasoning models).
        temperature: Override temperature.
        timeout: Request timeout in seconds.

    Returns:
        The full response text (content, or reasoning_content fallback).
    """
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    # Use larger max_tokens for reasoning models
    effective_max = max(max_tokens or config.max_tokens, 16384)
    payload = {
        "model": config.model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": effective_max,
        "temperature": temperature if temperature is not None else config.temperature,
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

        # Fall back to reasoning_content if content is empty
        return content if content.strip() else reasoning
    except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
        return f"NO_CHANGE\nError: LLM call failed: {e}"
    except Exception as e:
        return f"NO_CHANGE\nError: unexpected: {e}"

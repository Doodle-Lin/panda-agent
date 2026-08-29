"""Configuration loader — YAML config + env var substitution.

Config path: ~/.panda/config.yaml (or $PANDA_HOME/config.yaml)
Env vars: referenced as ${VAR_NAME} in YAML, resolved at load time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ModelConfig:
    default: str = "gpt-4o"
    code_model: str = ""
    vlm_model: str = ""
    base_url: str = "http://localhost:8000/v1"
    api_key: str = ""
    max_tokens: int = 8192
    fallback: str = ""  # fallback model name if primary fails


@dataclass
class AgentConfig:
    max_turns: int = 10
    max_retries: int = 3


@dataclass
class MemoryConfig:
    enabled: bool = True
    graph_url: str = "embedded://"
    storage_path: str = ""
    auto_write: bool = True


@dataclass
class EvolutionConfig:
    target_score: float = 90.0
    max_rounds: int = 3
    improve_brain: bool = True
    improve_tools: bool = True


@dataclass
class DisplayConfig:
    tui: bool = True
    show_reasoning: bool = False
    color: str = "auto"


@dataclass
class Config:
    model: ModelConfig = field(default_factory=ModelConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    evolution: EvolutionConfig = field(default_factory=EvolutionConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    _path: str = ""


def _expand_env(value: str) -> str:
    """Expand ${VAR_NAME} references in a string."""
    return os.path.expandvars(value) if "${" in value else value


def _expand_dict(d: dict) -> dict:
    """Recursively expand env vars in all string values."""
    result = {}
    for k, v in d.items():
        if isinstance(v, str):
            result[k] = _expand_env(v)
        elif isinstance(v, dict):
            result[k] = _expand_dict(v)
        else:
            result[k] = v
    return result


def config_path() -> Path:
    """Return the config file path."""
    home = os.getenv("PANDA_HOME", str(Path.home() / ".panda"))
    return Path(home) / "config.yaml"


def load_config() -> Config:
    """Load config from YAML file, with env var substitution.

    If no config file exists, returns defaults.
    """
    path = config_path()
    if not path.exists():
        return Config()

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    raw = _expand_dict(raw)

    cfg = Config(_path=str(path))
    if "model" in raw:
        cfg.model = ModelConfig(**raw["model"])
    if "agent" in raw:
        cfg.agent = AgentConfig(**raw["agent"])
    if "memory" in raw:
        cfg.memory = MemoryConfig(**raw["memory"])
    if "evolution" in raw:
        cfg.evolution = EvolutionConfig(**raw["evolution"])
    if "display" in raw:
        cfg.display = DisplayConfig(**raw["display"])

    return cfg


def save_config(cfg: Config) -> None:
    """Save config to YAML file."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "model": {
            "default": cfg.model.default,
            "code_model": cfg.model.code_model,
            "vlm_model": cfg.model.vlm_model,
            "base_url": cfg.model.base_url,
            "api_key": cfg.model.api_key,
            "max_tokens": cfg.model.max_tokens,
        },
        "agent": {
            "max_turns": cfg.agent.max_turns,
            "max_retries": cfg.agent.max_retries,
        },
        "memory": {
            "enabled": cfg.memory.enabled,
            "graph_url": cfg.memory.graph_url,
            "storage_path": cfg.memory.storage_path,
            "auto_write": cfg.memory.auto_write,
        },
        "evolution": {
            "target_score": cfg.evolution.target_score,
            "max_rounds": cfg.evolution.max_rounds,
            "improve_brain": cfg.evolution.improve_brain,
            "improve_tools": cfg.evolution.improve_tools,
        },
        "display": {
            "tui": cfg.display.tui,
            "show_reasoning": cfg.display.show_reasoning,
            "color": cfg.display.color,
        },
    }

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def default_config_yaml() -> str:
    """Return a default config YAML string for init."""
    return """\
model:
  default: gpt-4o
  code_model: ""
  vlm_model: ""
  base_url: http://localhost:8000/v1
  api_key: ${PANDA_API_KEY}
  max_tokens: 8192

agent:
  max_turns: 10
  max_retries: 3

memory:
  enabled: true
  graph_url: embedded://
  storage_path: ""  # empty = $PANDA_HOME/memory/memory.sqlite3
  auto_write: true

evolution:
  target_score: 90
  max_rounds: 3
  improve_brain: true
  improve_tools: true

display:
  tui: true
  show_reasoning: false
  color: auto
"""

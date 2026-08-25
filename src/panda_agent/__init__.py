"""PandaAgent — Self-Evolving Agent Framework.

A CLI agent that can execute tasks, remember across sessions, and
evolve its own brain through a 3-agent self-supervision loop.
"""

from .config import Config, load_config, save_config
from .llm import call_llm, call_llm_simple
from .brain import build_system_prompt, SYSTEM_PROMPT
from .tools import TOOLS, execute_tool, get_tool_descriptions
from .react import run_react, ReActResult
from .memory import MemoryClient
from .tui import TUI
from .types import Task, ExecutionResult, Evaluation, ImprovementResult, EvolutionResult

__version__ = "0.2.0"
__all__ = [
    "Config", "load_config", "save_config",
    "call_llm", "call_llm_simple",
    "build_system_prompt", "SYSTEM_PROMPT",
    "TOOLS", "execute_tool", "get_tool_descriptions",
    "run_react", "ReActResult",
    "MemoryClient",
    "TUI",
    "Task", "ExecutionResult", "Evaluation", "ImprovementResult", "EvolutionResult",
]

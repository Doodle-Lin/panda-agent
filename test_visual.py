"""Quick test: visualize evolve output with 3 rounds."""
import sys, os, time
sys.path.insert(0, r'E:\workspace\evo-agent\src')
os.environ['PANDA_HOME'] = os.path.expanduser('~/.panda')

from panda_agent.config import load_config
from panda_agent.orchestrator import run_evolution, Executor, Evaluator, Improver
from panda_agent.types import Task
from panda_agent.tui import TUI

config = load_config()
tui = TUI()
tui.banner()

task = Task(input_path='', instruction='帮我列出桌面文件')

t0 = time.time()
result = run_evolution(
    executor=Executor(config),
    evaluator=Evaluator(config),
    improver=Improver(config),
    task=task,
    target_score=85.0,
    max_rounds=3,
    on_event=lambda ev: tui.event(ev.type, ev.message),
    config=config,
)

tui.print(f"\nRounds: {len(result.rounds)}, Score: {result.final_score}, Patches: {result.total_patches}")

"""E2E: verify behavioral gate prevents score regression."""
import sys, os, time
sys.path.insert(0, r'E:\workspace\evo-agent\src')
os.environ['PANDA_HOME'] = os.path.expanduser('~/.panda')

from panda_agent.config import load_config
from panda_agent.orchestrator import run_evolution, Executor, Evaluator, Improver
from panda_agent.types import Task

config = load_config()
task = Task(input_path='', instruction='帮我列出桌面文件')

t0 = time.time()
result = run_evolution(
    executor=Executor(config),
    evaluator=Evaluator(config),
    improver=Improver(config),
    task=task,
    target_score=90.0,
    max_rounds=3,
    on_event=lambda ev: print(f'  [{time.time()-t0:.1f}s] {ev.type}: {ev.message}', flush=True),
    config=config,
)

print(f'\n=== Result ({time.time()-t0:.1f}s) ===')
print(f'Rounds: {len(result.rounds)}')
print(f'Final score: {result.final_score}')
print(f'Total patches: {result.total_patches}')
for r in result.rounds:
    print(f'  Round {r.round_num}:')
    if r.evaluation:
        print(f'    Score: {r.evaluation.score}')
    if r.improvement:
        print(f'    Patched: {r.improvement.patched}')
        print(f'    Score after: {r.improvement.score_after}')
        print(f'    Explanation: {r.improvement.explanation[:150]}')

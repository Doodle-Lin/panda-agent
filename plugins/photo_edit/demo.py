"""End-to-end demo: run the PandaAgent framework on the photo-edit task.

Usage:
    python plugins/photo_edit/demo.py

Requires the photo-edit-agent project at E:\\workspace\\photo-edit-agent
with a configured .env file.
"""

import sys
from pathlib import Path

# Add framework to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from panda_agent import run_evolution, Task
from plugins.photo_edit import (
    PhotoEditExecutor,
    PhotoEditEvaluator,
    PhotoEditImprover,
)


def main():
    image = r"C:\Users\<username>\AppData\Local\Hermes Agent CN Desktop\data\hermes-home\images\upload_20260824_143251_1.jpg"
    instruction = "blur the background to create depth of field, keep the dog and product box sharp"

    events = []
    def on_event(ev):
        line = f"[R{ev.round}] [{ev.type}] {ev.message[:200]}"
        print(line, flush=True)
        events.append(line)

    print("=" * 60, flush=True)
    print("PandaAgent —Photo Edit Self-Evolution Demo", flush=True)
    print("=" * 60, flush=True)

    result = run_evolution(
        executor=PhotoEditExecutor(),
        evaluator=PhotoEditEvaluator(),
        improver=PhotoEditImprover(),
        task=Task(input_path=image, instruction=instruction),
        target_score=95.0,
        max_rounds=3,
        on_event=on_event,
    )

    print("\n" + "=" * 60, flush=True)
    print("FINAL RESULT", flush=True)
    print("=" * 60, flush=True)
    print(f"Rounds: {len(result.rounds)}", flush=True)
    print(f"Final score: {result.final_score:.0f}/100", flush=True)
    print(f"Patches applied: {result.total_patches}", flush=True)
    print(f"Target reached: {result.target_reached}", flush=True)

    for r in result.rounds:
        ev = r.evaluation
        imp = r.improvement
        print(f"\n--- Round {r.round_num} ---", flush=True)
        if ev:
            print(f"  Score: {ev.score:.0f}/100", flush=True)
            print(f"  Issues: {ev.issues[:2]}", flush=True)
        if imp:
            print(f"  Patched: {imp.patched}, Tests: {imp.tests_passed}", flush=True)
            print(f"  Attempts: {imp.attempts}", flush=True)
            if imp.explanation:
                expl = imp.explanation[:200]
                print(f"  Why: {expl}", flush=True)


if __name__ == "__main__":
    main()

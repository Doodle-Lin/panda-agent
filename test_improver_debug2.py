"""Debug: trace Improver step by step (round 2)."""
import sys, os, time, json, re, shutil
from pathlib import Path

sys.path.insert(0, r'E:\workspace\evo-agent\src')
os.environ['PANDA_HOME'] = os.path.expanduser('~/.panda')

from panda_agent.config import load_config
from panda_agent.llm import call_llm
from panda_agent.orchestrator import (
    _IMPROVE_PROMPT, _extract_relevant, _extract_patch,
    _replace_function, _run_pytest, _TOOLS_PATH, _BRAIN_PATH
)
from panda_agent.types import Evaluation

config = load_config()

eval_data = Evaluation(
    score=60.0,
    issues=[
        "First tool call used '~' path with list_files which failed due to path resolution issue",
        "Final answer is just 'completed' without actually presenting the list of files to the user",
        "The agent retrieved the file list but did not synthesize or present the results in its final response"
    ],
    root_cause="list_files does not expand ~ to home directory; Executor does not pass answer to Evaluator",
    suggested_changes="Fix list_files to expand ~ paths; pass actual answer text to Evaluator"
)

source_path = _TOOLS_PATH
source = source_path.read_text(encoding="utf-8")
keywords = ["read", "write", "search", "patch", "run"]

relevant = _extract_relevant(source, eval_data, keywords)
print(f"=== Relevant source: {len(relevant)} chars ===")
print(relevant[:300])
print("...")
print()

eval_json = json.dumps({
    "score": eval_data.score,
    "issues": eval_data.issues,
    "root_cause": eval_data.root_cause,
    "suggested_changes": eval_data.suggested_changes,
}, indent=2, ensure_ascii=False)

prompt = _IMPROVE_PROMPT.format(
    evaluation_json=eval_json,
    source_code=relevant,
    target_file=source_path.name,
)

print(f"=== Prompt: {len(prompt)} chars ===")
print()

print("=== Calling LLM ===")
t0 = time.time()
response = call_llm(
    [{"role": "user", "content": prompt}],
    config.model,
    model=config.model.code_model or None,
)
print(f"Response: {len(response)} chars in {time.time()-t0:.1f}s")
print(f"Starts: {response[:150]!r}")
print()

if response.strip().startswith("NO_CHANGE"):
    print(">>> NO_CHANGE")
elif response.startswith("ERROR"):
    print(f">>> ERROR: {response[:200]}")
else:
    patch_code = _extract_patch(response)
    print(f"Patch: {len(patch_code)} chars")
    if patch_code:
        print(f"Patch starts: {patch_code[:200]!r}")
        print()
        patched = _replace_function(source, patch_code)
        if patched == source:
            print(">>> _replace_function FAILED")
        elif patched == patch_code:
            print(">>> Full file replacement")
        else:
            print(">>> Function replacement SUCCESS")
            print(f"Diff: {len(patched) - len(source)} chars change")
        # Show what changed
        if patched != source:
            # Find first difference
            for i, (a, b) in enumerate(zip(source, patched)):
                if a != b:
                    print(f"First diff at char {i}:")
                    print(f"  old: {source[i:i+100]!r}")
                    print(f"  new: {patched[i:i+100]!r}")
                    break
    else:
        print(">>> _extract_patch FAILED")
        print(f"Response: {response[:500]}")

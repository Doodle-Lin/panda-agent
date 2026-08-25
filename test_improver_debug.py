"""Debug: trace Improver step by step."""
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

# Use the actual evaluation from the last run
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

print(f"=== Prompt length: {len(prompt)} chars ===")
print(f"=== Relevant source length: {len(relevant)} chars ===")
print(f"=== Relevant source preview ===")
print(relevant[:500])
print()

print("=== Calling LLM (GLM52RJPT) ===")
t0 = time.time()
response = call_llm(
    [{"role": "user", "content": prompt}],
    config.model,
    model=config.model.code_model or None,
)
print(f"LLM response took {time.time()-t0:.1f}s")
print(f"Response length: {len(response)} chars")
print(f"Response starts with: {response[:200]!r}")
print()

# Check NO_CHANGE
if response.strip().startswith("NO_CHANGE"):
    print(">>> LLM said NO_CHANGE")
elif response.startswith("ERROR"):
    print(f">>> LLM error: {response[:200]}")
else:
    # Try to extract patch
    patch_code = _extract_patch(response)
    print(f"Extracted patch code: {len(patch_code) if patch_code else 0} chars")
    if patch_code:
        print(f"Patch preview: {patch_code[:300]}")
        print()
        # Try to apply
        patched = _replace_function(source, patch_code)
        if patched == source:
            print(">>> _replace_function FAILED — no match (patch didn't change source)")
            # Debug: what function name is in the patch?
            m = re.match(r"def (\w+)\(", patch_code)
            if m:
                fname = m.group(1)
                print(f"    Patch defines function: {fname}")
                # Check if function exists in source
                if f"def {fname}(" in source:
                    print(f"    Function {fname} exists in source — regex pattern didn't match")
                    # Show the pattern
                    pattern = re.compile(rf"^def {fname}\(.*?(?=\ndef \w+\(|\Z)", re.DOTALL)
                    m2 = pattern.search(source)
                    if m2:
                        print(f"    Pattern matched at pos {m2.start()}-{m2.end()}")
                    else:
                        print(f"    Pattern did NOT match — checking why...")
                        # Show first few lines of source
                        for i, line in enumerate(source.splitlines()[:5]):
                            print(f"      {i}: {line!r}")
                else:
                    print(f"    Function {fname} does NOT exist in source")
        else:
            print(">>> _replace_function SUCCESS — patch applied")
            # Write and test
            source_path.write_text(patched, encoding="utf-8")
            passed, test_output = _run_pytest(Path(r"E:\workspace\evo-agent\tests"), Path(r"E:\workspace\evo-agent"))
            print(f"    Tests passed: {passed}")
            if not passed:
                print(f"    Test output: {test_output[:300]}")
                # Restore
                backup = source_path.with_suffix(".py.bak")
                shutil.copy2(backup, source_path)
                backup.unlink()
    else:
        print(">>> _extract_patch FAILED — no patch found in response")
        print(f"    Response (first 500): {response[:500]}")

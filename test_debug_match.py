"""Debug: test _replace_function with max_turns_for_task patch."""
import re

source = open(r'E:\workspace\evo-agent\src\panda_agent\brain.py', encoding='utf-8').read()

# Simulate LLM patch
patch_code = '''def max_turns_for_task(task: str) -> int:
    """Determine max ReAct turns based on task complexity."""
    task_lower = task.lower()
    complex_keywords = ['build', 'create', 'write', 'fix', 'refactor', 'implement', 'deploy', 'optimize']
    for kw in complex_keywords:
        if kw in task_lower:
            return 15
    return 5'''

# Check _replace_function logic step by step
print(f"Source length: {len(source)}")
print(f"Patch code starts: {patch_code[:80]!r}")
print()

# Step 1: Check if new_code starts with "def "
print(f"new_code.lstrip().startswith('def '): {patch_code.lstrip().startswith('def ')}")

# Step 2: Find all function defs in patch_code
new_funcs = list(re.finditer(r"^def (\w+)\(", patch_code, re.MULTILINE))
print(f"Functions in patch: {[m.group(1) for m in new_funcs]}")

# Step 3: For each function, try to replace
for fm in new_funcs:
    name = fm.group(1)
    func_start = fm.start()
    remaining = patch_code[func_start:]
    next_def = re.search(r"\ndef \w+\(", remaining[1:])
    if next_def:
        func_body = remaining[:next_def.start() + 1].rstrip()
    else:
        func_body = remaining.rstrip()
    
    print(f"\nFunction: {name}")
    print(f"  func_body length: {len(func_body)}")
    print(f"  func_body[:100]: {func_body[:100]!r}")
    
    # Check pattern
    pattern = re.compile(rf"^def {name}\(.*?(?=\ndef \w+\(|\Z)", re.DOTALL | re.MULTILINE)
    m = pattern.search(source)
    if m:
        print(f"  Pattern matched at {m.start()}-{m.end()}")
        result = pattern.sub(func_body + "\n\n", source, count=1)
        print(f"  Replaced! new length: {len(result)} (was {len(source)})")
    else:
        print(f"  Pattern did NOT match")
        # Check if function exists in source
        if f"def {name}(" in source:
            print(f"  Function {name} EXISTS in source at pos {source.index(f'def {name}(')}")
            # Check the actual def line in source
            source_m = re.search(rf"^def {name}\(", source, re.MULTILINE)
            if source_m:
                print(f"  re.search with MULTILINE matched at {source_m.start()}")
            else:
                print(f"  re.search with MULTILINE did NOT match — checking line endings")
                # Show the actual line
                pos = source.index(f"def {name}(")
                line_start = source.rfind("\n", 0, pos) + 1
                line_end = source.find("\n", pos)
                actual_line = source[line_start:line_end]
                print(f"  Actual line: {actual_line!r}")
                print(f"  Has \\r: {'\\r' in actual_line}")
        else:
            print(f"  Function {name} does NOT exist in source")

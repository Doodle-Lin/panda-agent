# Agent Rules — panda-agent development

## Environment

- Python 3.12, Windows PowerShell
- Project root: E:\workspace\evo-agent
- Test command: `python -m pytest tests/ -q --tb=short`
- Lint: `python -m py_compile src/panda_agent/<file>` (no ruff configured)
- Install: `pip install -e . --no-deps`
- LLM model: GLM52RJPT (reasoning model, content empty → reasoning_content)

## Workflow (enforced — paste output or die)

1. **Read spec.md** → paste the acceptance criterion being worked on
2. **Write failing test** → run `pytest tests/<file>::<test> -v` → paste output (must see FAIL)
3. **Write minimal code** → run same test → paste output (must see PASS)
4. **Run full suite** → `pytest tests/ -q` → paste output (must see 116+ passed)
5. **git diff** → show user → commit only after confirmation

## Prohibitions

- Do NOT edit brain.py, tools.py, or react.py without a failing test first
- Do NOT change existing test assertions to make them pass — fix the code instead
- Do NOT add new dependencies
- Do NOT touch ~/.panda/config.yaml (user's config)
- Do NOT skip the RED phase — test must fail before implementation

## Failure Recovery

- 3 failed fix attempts → stop, invoke systematic debugging, question architecture
- write_file tool failing → debug the actual tool execution, not the prompt
- LLM not outputting TOOL_CALL/DONE → check if it's a prompt issue or model issue (reasoning model puts actions in content)

## Key Facts

- GLM52RJPT: reasoning model, content is empty, actions go in reasoning_content
- max_turns: max(task_estimate, config.agent.max_turns) — already fixed
- salvage: max_turns exceeded → one more LLM call with existing tool results
- graph memory: embedded GraphEngine, no HTTP server, persists to ~/.panda/memory/
- Tests: 116 passing, 34s, mock LLM for all non-e2e tests

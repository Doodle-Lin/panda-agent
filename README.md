# PandaAgent — Generic 3-Agent Self-Evolution Framework

A framework where three agents form a closed loop to iteratively improve
their own tool code through real-world execution, evaluation, and
LLM-driven code patching.

## Architecture

```
+----------+     +-----------+     +----------+
| Executor  |---->| Evaluator  |---->| Improver  |
| (execute) |     | (evaluate) |     | (improve) |
+----------+     +-----------+     +----------+
     ^                                 |
     +----------- improved tools -------+
```

- **Executor** runs tools to accomplish a task.
- **Evaluator** inspects the result, scores it (0-100), and reports issues.
- **Improver** reads the evaluation, generates a code patch, runs tests,
  and keeps the patch only if tests pass.
- The loop repeats until the target score is reached or max rounds are exhausted.

## Quick Start

```bash
pip install -e ".[test]"
pytest tests/
```

## Usage

```python
from panda_agent import run_evolution
from my_plugin import MyExecutor, MyEvaluator, MyImprover

result = run_evolution(
    executor=MyExecutor(),
    evaluator=MyEvaluator(),
    improver=MyImprover(),
    task=Task(input_path="input.jpg", instruction="blur the background"),
    target_score=95.0,
    max_rounds=3,
)
```

## Writing a Plugin

1. **Executor**: Subclass `Executor`, implement `execute(task) -> ExecutionResult`.
2. **Evaluator**: Subclass `Evaluator`, implement `evaluate(task, result) -> Evaluation`.
3. **Improver**: Subclass `Improver`, set `target_source_path`, `test_path`,
   `project_root`, `llm_config`. Optionally override `keyword_map`.

## Design Principles

- **Plugins, not hardcoding** — Executor/Evaluator/Improver are injectable.
- **Code is the evolution target** — Improver patches tool source files.
- **Error-feedback retry** — Failed patches feed errors back to the LLM.
- **Safety first** — Failed patches are always reverted.
- **Model-agnostic** — Any LLM works; reasoning models need `reasoning_content` fallback.

## License

MIT

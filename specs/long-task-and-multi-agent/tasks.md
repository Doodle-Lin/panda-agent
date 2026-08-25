# Task Traceability — spec → test mapping

| Spec criterion | Test file | Status |
|----------------|----------|--------|
| P1.1 write_file 后文件实际存在 | tests/test_write_file.py::test_write_file_creates_file | 🔴 |
| P1.2 写入失败返回明确错误 | tests/test_write_file.py::test_write_file_error_on_failure | 🔴 |
| P1.3 agent 识别写入失败并重试 | tests/test_react.py::test_write_file_retry_on_failure | 🔴 |
| P2.1 写小说 30 轮内完成 | tests/test_long_task.py::test_write_story_completes | 🔴 (slow) |
| P2.2 文件实际创建在桌面 | tests/test_long_task.py::test_story_file_exists | 🔴 (slow) |
| P2.3 文件内容 ≥ 1500 字 | tests/test_long_task.py::test_story_content_length | 🔴 (slow) |
| P2.4 故事内容完整 | tests/test_long_task.py::test_story_has_title_and_ending | 🔴 (slow) |
| P3.1 复杂任务自动分解 | tests/test_orchestrate.py::test_complex_task_decomposes | 🔴 |
| P3.2 子任务独立 ReAct 循环 | tests/test_orchestrate.py::test_subtask_isolated_context | 🔴 |
| P3.3 子任务结果传回主 agent | tests/test_orchestrate.py::test_subtask_result_aggregation | 🔴 |
| P3.4 简单任务不走分解 | tests/test_orchestrate.py::test_simple_task_no_decompose | 🔴 |
| P3.5 子任务数量 ≤ 5 | tests/test_orchestrate.py::test_max_subtasks | 🔴 |
| P4.1 子任务 messages 独立 | tests/test_orchestrate.py::test_context_isolation | 🔴 |
| P4.2 主 agent 只接收最终结果 | tests/test_orchestrate.py::test_main_agent_gets_answer_only | 🔴 |
| P4.3 子任务失败主 agent 重试 | tests/test_orchestrate.py::test_subtask_failure_retry | 🔴 |

## Phases

### Phase 1: Debug write_file (P1) — find root cause first
- [x] Run write_file directly, verify file exists on disk → **write_file itself works**
- [x] Write failing test for write_file → **RED: prompt has no rule requiring write_file for file creation**
- [x] Fix: add rule 7 to system prompt → **GREEN: 117/117 passing**
- [x] Checkpoint: write_file works, existing tests still pass

### Phase 2: Multi-agent orchestration (P3+P4)
- [ ] Write failing test for task decomposition (complex vs simple)
- [ ] Write failing test for context isolation
- [ ] Implement orchestrator: decompose → sub-React → aggregate
- [ ] Checkpoint: mock LLM tests pass, 116+ tests green

### Phase 3: E2E verification (P2) — real LLM
- [ ] Run panda "写小说" with real LLM, verify file on desktop
- [ ] Checkpoint: novel file exists, ≥1500 chars, has title and ending

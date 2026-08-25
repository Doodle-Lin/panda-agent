# Task Traceability — spec → test mapping (revised)

| Spec criterion | Test file | Status |
|----------------|----------|--------|
| P1.1 超阈值自动压缩 | tests/test_context_compress.py::test_compress_on_threshold | 🔴 |
| P1.2 保留 system + 最近 N 轮 | tests/test_context_compress.py::test_preserve_recent | 🔴 |
| P1.3 旧工具输出替换为摘要 | tests/test_context_compress.py::test_truncate_old_tool_results | 🔴 |
| P1.4 压缩后 token 减少 ≥50% | tests/test_context_compress.py::test_token_reduction | 🔴 |
| P1.5 压缩后 agent 继续正常执行 | tests/test_context_compress.py::test_compress_then_continue | 🔴 |
| P2.1 连续3次相同 tool call 检测 | tests/test_doom_loop.py::test_detect_doom_loop | 🔴 |
| P2.2 注入警告 prompt | tests/test_doom_loop.py::test_inject_warning | 🔴 |
| P2.3 仍重复 → FAILED | tests/test_doom_loop.py::test_fail_after_warning | 🔴 |
| P2.4 不同参数重试不算死循环 | tests/test_doom_loop.py::test_different_args_not_doom | 🔴 |
| P3.1 max_turns 到极限注入 MAX_STEPS_PROMPT | tests/test_soft_limit.py::test_inject_max_steps_prompt | 🔴 |
| P3.2 注入后 text-only 回复 | tests/test_soft_limit.py::test_text_only_after_prompt | 🔴 |
| P3.3 替代 salvage（salvage 保留为 fallback） | tests/test_soft_limit.py::test_salvage_still_works | 🔴 |

## Phases

### Phase 1: Debug write_file (P1) — ✅ COMPLETED
- [x] write_file itself works — root cause was prompt missing rule 7
- [x] Added rule 7: must use write_file to create files
- [x] 117/117 passing

### Phase 2: Doom Loop Detection (P2) — simplest, highest impact
- [ ] Write failing test: 3 identical tool calls → detected
- [ ] Implement doom loop detection in react.py
- [ ] Checkpoint: 117+ tests green

### Phase 3: Soft Limit (P3) — replace hard cutoff
- [ ] Write failing test: max_turns → inject MAX_STEPS_PROMPT
- [ ] Implement soft limit in react.py
- [ ] Checkpoint: 117+ tests green

### Phase 4: Context Compression (P1) — most complex
- [ ] Write failing test: threshold → compress → continue
- [ ] Implement context compression in react.py
- [ ] Checkpoint: 117+ tests green

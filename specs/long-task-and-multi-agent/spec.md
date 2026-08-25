# Long Task Support — Context Compression + Doom Loop Detection + Soft Limit

## Goal

让 panda 能完成长任务（如"写 2000 字小说到桌面"），不因轮数耗尽或上下文溢出而放弃。

## Problem Analysis（基于 opencode/Hermes 源码研究修正）

之前以为是"轮数不够"和"缺少多 agent"，但源码研究表明：

- **opencode 默认 max_steps = Infinity**，靠 compaction + doom loop 检测防无限循环
- **Hermes 默认 max_iterations = sys.maxsize**，靠三层上下文压缩 + 16个一次性恢复守护
- 两者都不靠任务分解处理长任务，靠的是**上下文管理 + 优雅降级**

panda 真正缺的三个机制：

1. **无上下文压缩** — 长任务消息越积越多，token 溢出后 LLM 响应质量下降
2. **无死循环检测** — 推理模型格式强制每轮浪费 2x 轮数，重复调用同一工具无人发现
3. **硬截断而非软限制** — max_turns 到了直接放弃（已加 salvage，但不够优雅）

## Architecture Decision

采用 opencode 的模式（比 Hermes 简单，适合 panda 的规模）：

1. **上下文压缩**：超过阈值时，摘要旧工具输出，保留近期消息
2. **死循环检测**：连续 3 次相同 tool call → 注入警告 prompt，不硬停
3. **软限制**：max_turns 到极限时注入 MAX_STEPS_PROMPT，让 LLM 优雅总结

不做多 agent 编排 — opencode 和 Hermes 的子 agent 是用于任务分解，不是长任务处理的核心。

## Acceptance Criteria

### P1: 上下文压缩
- [ ] 上下文超过阈值（configurable，默认 80% max_tokens）时自动压缩
- [ ] 压缩保留 system prompt + 最近 N 轮对话（默认 6）
- [ ] 旧工具输出替换为摘要（"<tool result from turn N: ...truncated...>"）
- [ ] 压缩后消息总 token 数显著减少（至少减少 50%）
- [ ] 压缩后 agent 能继续正常执行（不崩溃、不丢上下文关键信息）

### P2: 死循环检测
- [ ] 连续 3 次调用相同工具 + 相同参数 → 检测到死循环
- [ ] 死循环时注入警告 prompt："You are repeating the same tool call. Try a different approach."
- [ ] 注入警告后如果 LLM 仍重复 → 标记 FAILED
- [ ] 不影响正常的重试（如不同参数的重试不算死循环）

### P3: 软限制（替代硬截断）
- [ ] max_turns 到极限时注入 MAX_STEPS_PROMPT（类似 opencode）
- [ ] MAX_STEPS_PROMPT 内容：禁用工具 + 要求总结已完成工作 + 列出剩余任务
- [ ] 注入后给 LLM 最后一轮生成 text-only 回复
- [ ] 替代当前的 salvage 机制（salvage 保留作为 fallback）

## Constraints

- 不引入新依赖
- 不增加简单任务的开销（简单任务不触发压缩/检测）
- 保持现有 117 个测试全部通过
- 代码改动 ≤ 150 行新增
- GLM52RJPT 推理模型兼容
- opencode 的 compaction 模式作为参考，但简化到 panda 规模

## Reference

- opencode: `prompt.ts` runLoop, `compaction.ts`, `max-steps.ts`, `processor.ts` doom loop
- Hermes: `context_compressor.py`, `turn_retry_state.py`, `turn_finalizer.py`

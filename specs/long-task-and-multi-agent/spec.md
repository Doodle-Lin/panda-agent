# Long Task Support + Lightweight Multi-Agent Orchestration

## Goal

让 panda 能完成长任务（如"写 2000 字小说到桌面"），不因轮数耗尽而放弃。

## Problem Analysis

当前 panda 处理长任务时失败的原因链：

1. **write_file 工具静默失败** — agent 调了 write_file 但文件没创建（需 debug 根因）
2. **轮数浪费** — 推理模型每轮格式强制消耗 2x 轮数，10 轮实际只有 5 个有效动作
3. **上下文累积污染** — 所有轮次的工具输出在一个 messages 数组里，越积越脏
4. **无任务分解** — 一个 ReAct 循环跑到底，复杂任务没有拆分机制
5. **max_turns 超限直接放弃** — 已修复（salvage 机制），但根因还在

## Architecture Decision

采用**轻量多 agent 编排**——不需要进程管理、IPC、独立 LLM 实例：

- 主 agent（Orchestrator）分析任务复杂度
- 复杂任务分解成子任务，每个子任务用独立 ReAct 循环（新 messages，干净上下文）
- 子任务结果传回主 agent 聚合
- 简单任务仍走单 ReAct 循环（不增加开销）

## Acceptance Criteria

### P1: write_file 不再静默失败
- [ ] write_file 工具调用后，文件实际存在于磁盘上
- [ ] 写入失败时，工具返回明确错误信息（不是空输出）
- [ ] agent 能识别写入失败并重试或换策略

### P2: 长任务能完成
- [ ] "帮我在桌面写个两千字左右的科幻短篇小说" 能在 30 轮内完成
- [ ] 文件实际创建在桌面上
- [ ] 文件内容 ≥ 1500 字（允许 ±25% 浮动）
- [ ] 故事内容完整（有标题、有结尾，不是片段）

### P3: 轻量多 agent 编排
- [ ] 复杂任务（含 "write" / "create" / "build" 关键词）自动分解为子任务
- [ ] 每个子任务用独立 ReAct 循环执行（独立 messages 数组）
- [ ] 子任务结果传回主 agent，主 agent 聚合后输出最终答案
- [ ] 简单任务（"list files" / "你好"）不走分解，直接单 ReAct 循环
- [ ] 子任务数量 ≤ 5（防止无限分解）

### P4: 上下文隔离
- [ ] 子任务的 messages 数组不包含其他子任务的历史
- [ ] 主 agent 只接收子任务的最终结果（answer），不接收中间过程
- [ ] 子任务失败时，主 agent 收到错误信息并决定重试或换策略

## Constraints

- 不引入进程管理、IPC、独立 LLM 实例
- 不增加简单任务的开销（简单任务走原路径）
- 保持现有 116 个测试全部通过
- 代码改动 ≤ 200 行新增
- GLM52RJPT 推理模型兼容（content 空 → reasoning_content）

# PandaAgent 代码审查报告

> **历史快照，不是当前 issue 列表。** 本报告记录 2026-08-27 的审查结论；表中的
> “待修复”状态只反映当时的工作树，不能作为当前 `master` 的状态判断。当前基线由
> GitHub Actions `quality` 和 `python scripts/harness.py verify` 定义。
>
> 后续已完成的关键修复包括：可移植的内嵌 SQLite 图记忆（原 #6/#13/#37/#44）、
> 测试绝对路径清理（原 #23）、Ruff 基线与 PR CI（原 #45）。请通过 GitHub Issues
> 跟踪新的问题，不要把本报告的旧条目重新当作待办。

**审查日期**: 2026-08-27
**审查范围**: `src/panda_agent/` (18 文件) + `tests/` (28 文件, ~198 测试用例)
**审查人**: Hermes Agent (自动化)

---

## 严重性分级说明

| 级别 | 含义 |
|------|------|
| **P0** | 阻断性 — 代码无法运行、安全漏洞、数据丢失 |
| **P1** | 严重 — 逻辑错误、功能缺失、接口不匹配 |
| **P2** | 中等 — 代码质量、可维护性、边界条件 |
| **P3** | 轻微 — 风格、文档、优化建议 |

---

## 问题汇总表

| # | 级别 | 文件 | 行号 | 问题摘要 | 状态 |
|---|------|------|------|----------|------|
| 1 | **P0** | orchestrator.py | — | 双重定义 — 文件含两套完整实现，原始版本遮蔽重构版本 | ✅ 已修复 |
| 2 | **P0** | orchestrator.py | 128 | `_LEARN_PROMPT` 三引号字符串损坏 | ✅ 已修复 |
| 3 | **P0** | orchestrator.py | 654 | `ast.parse()` 失败 — 文件无法编译 | ✅ 已修复 |
| 4 | **P0** | tools.py | 111 | `_tool_run_command` 使用 `shell=True` — shell 注入漏洞 | ✅ 已修复(合并security) |
| 5 | **P0** | tools.py | 38-62 | 文件工具未调用 `security.safe_path` — 路径穿越漏洞 | ✅ 已修复(合并security) |
| 6 | **P0** | memory.py | 15 | 硬编码 `${PANDA_HOME}/graph_memory` — 不可移植 | 🔴 待修复(Phase 5) |
| 7 | **P0** | cli.py | 355 | `improver.improve(evaluation, evidence=...)` — 签名不匹配 | 🔴 待修复(Phase 2) |
| 8 | **P0** | test_evolution.py | 65-76 | 测试期望 `score=50` (伪造分数) | ✅ 已修复 |
| 9 | **P0** | test_evolution.py | 20 | 导入 `_try_fix_syntax` — 已删除的函数 | ✅ 已修复 |
| 10 | **P0** | test_regression.py | 244 | 调用 `_parse_eval_json` — 已删除的方法 | ✅ 已修复 |
| 11 | **P0** | test_orchestrator.py | — | import 崩溃 | ✅ 已修复(去重后可编译) |
| 12 | **P0** | test_security.py | 50-54 | 工具未接入安全边界 | ✅ 已修复(合并security) |
| 13 | **P0** | test_memory.py | 20 | 硬编码路径 | 🔴 待修复(Phase 5) |
| 14 | **P1** | orchestrator.py | 507 | 原始 `_run_pytest` 使用裸 `"python"` | ✅ 已修复(去重后保留正确版) |
| 15 | **P1** | orchestrator.py | 668 | Improver 缺少 `benchmark_gate` 等 | ✅ 已修复(去重后保留正确版) |
| 16 | **P1** | orchestrator.py | 803-909 | 缺少快照+恢复最佳代码逻辑 | ✅ 已修复(去重后保留正确版) |
| 17 | **P1** | orchestrator.py | 848 | 缺少 `evaluation is None` 处理 | ✅ 已修复(去重后保留正确版) |
| 18 | **P1** | react.py | 425-432 | 自修复后 `tool_calls` 重复追加 | 🔴 待修复 |
| 19 | **P1** | react.py | 269 | `not_found` 自修复用 `pattern="*"` (glob，非 regex) | 🔴 待修复 |
| 20 | **P1** | tools.py | 108-127 | `sanitized_env()` 未调用 | ✅ 已修复(合并security) |
| 21 | **P1** | cli.py | 391 | `_save_session` 引用未定义的 `result` | 🔴 待修复 |
| 22 | **P1** | llm.py | 64-70 | `stream=True` 无 response 清理 | 🔴 待修复 |
| 23 | **P1** | 多个测试文件 | 多处 | 硬编码 `sys.path.insert` 绝对路径 | 🔴 待修复 |
| 24 | **P1** | test_doom_loop.py | 21 | `_FakeLLM.__call__` 签名不匹配 | 🔴 待修复 |
| 25 | **P2** | react.py | 50-55 | token 估算低估 CJK | 🔴 待修复 |
| 26 | **P2** | react.py | 471 | doom loop 二次检查冗余 | 🔴 待修复 |
| 27 | **P2** | security.py | 159 | `_SENSITIVE_FRAGMENTS` 误匹配 KEYBOARD 等 | 🔴 待修复 |
| 28 | **P2** | tools.py | 74-87 | 搜索无文件大小限制 | 🔴 待修复 |
| 29 | **P2** | tools.py | 256 | `get_tool_schemas` required 参数启发式不精确 | 🔴 待修复 |
| 30 | **P2** | parsing.py | 88-90 | `repair_json` 损坏含 "True" 的字符串 | 🔴 待修复 |
| 31 | **P2** | llm.py | 50 | `reasoning_models` 硬编码集合 | 🔴 待修复 |
| 32 | **P2** | llm.py | 106 | `import re as _re` 在函数内 | 🔴 待修复 |
| 33 | **P2** | config.py | 106-114 | YAML 意外键会 TypeError | 🔴 待修复 |
| 34 | **P2** | config.py | 124-153 | `save_config` 未保存 `fallback`/`vlm_model` | 🔴 待修复 |
| 35 | **P2** | brain.py | 78-98 | `max_turns_for_task` 关键词匹配过于简单 | 🔴 待修复 |
| 36 | **P2** | cli.py | 360-362 | `_learn_after_task` 裸 `except: pass` | 🔴 待修复 |
| 37 | **P2** | memory.py | 36-40 | monkey-patch 非线程安全 | 🔴 待修复 |
| 38 | **P2** | 多个测试文件 | — | `_FakeLLM.__call__` 签名不一致 | 🔴 待修复 |
| 39 | **P3** | types.py | 20 | `ExecutionResult.success` 默认 `True` | 🔴 待修复 |
| 40 | **P3** | types.py | 21 | `ExecutionResult.error` 类型不一致 | 🔴 待修复 |
| 41 | **P3** | brain.py | 54-65 | `should_retry` 已定义但未调用 | 🔴 待修复 |
| 42 | **P3** | patching.py | — | 无修饰 async 函数替换的测试 | 🔴 待修复 |
| 43 | **P3** | config.py | 66 | `_expand_env` 平台相关 | 🔴 待修复 |
| 44 | **P3** | memory.py | 115 | `MemoryClient.__init__` 接受 `url` 但不使用 | 🔴 待修复 |
| 45 | **P3** | pyproject.toml | — | 无 ruff/mypy/CI 配置 | 🔴 待修复 |

---

## 历史快照状态 (2026-08-27 18:30)

### 测试基线
```
339 passed, 15 failed, 1 skipped, 6 deselected
```

### 15个失败分类

| 分类 | 数量 | 根因 | 修复方案 |
|------|------|------|----------|
| test_patch_fuzzy | 5 | origin tools.py 有 fuzzy matching, 我们的版本没有 exact match 后的 3 个 fuzzy 策略 | Phase 1: 恢复 origin 的 fuzzy matching 代码 |
| test_react_native | 3 | safe_path(workspace root) 阻止测试写入临时文件 | Phase 1: 测试设置 PANDA_WORKSPACE |
| test_extract_patch | 4 | Format 3/4/5 (python fence, generic fence, raw def) 在去重后被删除 | Phase 1: 恢复多格式支持 |
| test_regression | 1 | shell=True 被安全加固替换,中文命令测试失败 | Phase 1: 适配 safe run_command |
| test_memory | 1 | 硬编码路径导致 import 失败 | Phase 5 |
| test_evolution | 1 | run_evolution 签名变化(加了 learner 参数) | Phase 1: 测试传 learner=None |

### 已修复的关键 P0 问题

1. **orchestrator.py 双重定义** — 去重后保留 libcst/benchmark_gate/sys.executable 版本
2. **tools.py shell=True** — 合并 origin 的 security.py (parse_command + safe_path + sanitized_env)
3. **test imports** — 移除 `_try_fix_syntax` 和 `_parse_eval_json` 的引用,改用 `parse_evaluation`
4. **test_security Windows** — 修复 python3→python, 添加 symlink skip

### 待修复的关键问题 (按优先级)

**Phase 2 (核心循环可靠)**:
- #7: cli.py `improver.improve(evaluation, evidence=...)` 签名不匹配
- #21: cli.py `_save_session` 引用未定义变量
- #18: react.py 自修复后 tool_calls 重复追加

**Phase 3 (进化历史)**:
- #6: memory.py 硬编码路径
- #37: memory.py monkey-patch 非线程安全

**Phase 5 (可移植性)**:
- #6/#13: 硬编码 `${PANDA_HOME}/graph_memory` 路径
- #23: 测试文件硬编码 `sys.path.insert`

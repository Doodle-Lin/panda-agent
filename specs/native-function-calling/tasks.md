# Tasks: Native Function Calling

| Phase | Spec 验收标准 | 测试文件 | 状态 |
|-------|--------------|---------|------|
| 1 | tools schema 生成 | test_tools_schema.py | ✅ 5/5 |
| 2 | LLMResponse.tool_calls + call_llm_detailed 支持 tools | test_llm_function_calling.py | ✅ 4/4 |
| 3 | run_react 优先用 tool_calls, fallback 文本解析 | test_react_native.py | ✅ 4/4 |
| 4 | SYSTEM_PROMPT 保留 text fallback 指令 | — | ✅ 保留 |
| 5 | e2e 真实 LLM 验证 | test_e2e.py | ✅ 4/5 |


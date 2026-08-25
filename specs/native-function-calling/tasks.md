# Tasks: Native Function Calling

| Phase | Spec 验收标准 | 测试文件 | 状态 |
|-------|--------------|---------|------|
| 1 | tools schema 生成 | test_tools_schema.py | TODO |
| 2 | LLMResponse.tool_calls + call_llm_detailed 支持 tools | test_llm_function_calling.py | TODO |
| 3 | run_react 优先用 tool_calls, fallback 文本解析 | test_react_native.py | TODO |
| 4 | SYSTEM_PROMPT 去掉 TOOL_CALL 指令 | test_prompt_update.py | TODO |
| 5 | e2e 真实 LLM 长内容写入 | test_e2e.py (现有) | TODO |

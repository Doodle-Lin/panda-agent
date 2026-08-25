# Spec: Native Function Calling

## 目标
把 panda 的工具调用从文本协议（`TOOL_CALL: {json}` 正则解析）切换到 OpenAI 兼容的原生 function calling，彻底消除 JSON 解析脆弱性问题。

## 问题分析
当前方式：system prompt 教 LLM 输出 `TOOL_CALL: {json}` → 正则提取 → json.loads 解析
- 换行符未转义导致 json.loads 失败（已修但治标不治本）
- 贪婪正则匹配错误（`}"}` bug）
- 推理模型 content 为空时无法解析

原生 function calling：API 保证 arguments 是合法 JSON，所有特殊字符自动转义

## 验收标准
1. `call_llm_detailed` 支持 `tools` 参数，返回 `LLMResponse.tool_calls`
2. `run_react` 优先使用 `tool_calls`，文本解析作为 fallback
3. 5000 字中文内容写入不再出现 JSON 解析失败
4. 现有 147 个测试全通过
5. e2e 真实 LLM 测试通过（含长内容写入）
6. 推理模型（GLM52RJPT）和非推理模型都兼容

# Agent: Native Function Calling

## 开发环境
- E:\workspace\evo-agent
- Python 3.12, pytest
- 模型: GLM52RJPT (推理模型, content 空→reasoning_content)
- API: https://your-api-endpoint.com/v1 (OpenAI 兼容)

## 规则
1. TDD: 先写测试看 RED → 改代码看 GREEN
2. 不删文本解析 fallback——保留给不支持 function calling 的模型
3. 优先 tool_calls，fallback 到文本解析
4. LLMResponse 新增 tool_calls 字段，不破坏现有字段
5. 每步 commit

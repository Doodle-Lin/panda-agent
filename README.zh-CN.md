# 🐼 PandaAgent

**一个会改自己代码的 Agent 框架 —— 并且能证明这次改动真的让它变好了。**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-197%20passing-brightgreen.svg)](#测试)

[English](README.md) · **简体中文**

大多数「自我进化 Agent」项目让 LLM 改自己的源码，就管这叫进化。但难点从来不是**生成补丁** —— 现在的模型写个补丁毫无压力。难点是**判断这个补丁到底让系统变好了还是变坏了**。PandaAgent 就是围绕这个问题构建的。

```
                    ┌─────────────────────────────────────┐
                    │            进化循环                  │
                    └─────────────────────────────────────┘

   ┌──────────┐      ┌───────────┐      ┌──────────┐
   │ Executor │─────>│ Evaluator │─────>│ Improver │
   │  执行者   │      │  评估者    │      │  改进者   │
   │ ReAct+工具│      │ 打分/诊断  │      │ 补丁+验证 │
   └──────────┘      └───────────┘      └──────────┘
        ▲                                     │
        │                                     │ 打补丁
        │            ┌──────────────┐          │
        └────────────│ tools.py     │<─────────┘
          重跑验证    │ brain.py     │
                     └──────────────┘
                      「手」和「脑」
```

三个 Agent 构成闭环：

| 角色 | 职责 | 能碰什么 |
|---|---|---|
| **Executor** | 用 ReAct 循环执行任务 | 读 `brain.py`（提示词/策略）+ `tools.py`（能力） |
| **Evaluator** | 0–100 打分，诊断根因 | 只读 |
| **Improver** | 生成补丁、验证、保留或回滚 | 写 `tools.py` / `brain.py` |

和别家不同的地方：这个 Agent 同时进化自己的**「手」（工具）和「脑」（系统提示词 + 决策逻辑）**，而且每个补丁都必须通过验证门禁才会被保留。

---

## 项目状态

**Alpha —— 能跑的原型，不是生产就绪。** 先说清楚，因为「自我进化」这个词太容易让人期待过高。

| 组件 | 状态 | 说明 |
|---|---|---|
| ReAct 循环 + 工具执行 | ✅ 可用 | 6 个内置工具 |
| 三 Agent 进化循环 | ✅ 可用 | Executor → Evaluator → Improver |
| 补丁应用 | ✅ 可用 | libcst CST 重写，自动备份，失败回滚 |
| 脑进化 | ✅ 可用 | 改提示词 + 决策逻辑 |
| CLI + TUI | ✅ 可用 | `panda run`、`panda evolve` |
| **回归门禁** | ✅ 可用 | 补丁不得让实测任务表现下降 |
| **执行边界** | ✅ 可用 | 命令白名单 + 工作区隔离 |
| 图记忆 | 🟡 可选 | 需要外部服务；缺失时优雅降级 |
| 进化历史 → 记忆 | 🔴 未做 | Improver 不会从历史补丁的成败中学习 |
| 操作系统级沙箱 | 🟡 部分 | 有白名单和路径边界，但无内核隔离。见[安全](#安全) |

**测试：197 个用例**，覆盖解析、补丁、benchmark、orchestrator 和安全边界。

跑在你在意的东西上之前，请先读[已知限制](#已知限制)。

---

## 为什么做这个

自我进化系统的核心矛盾：**验证比生成难得多。**

给 LLM 一份评估报告让它改代码，它会给你一个看起来很合理的补丁。问题是——你怎么知道这个补丁不是让系统变差了？

「单元测试通过」回答的是**「代码有没有坏」**，不是**「Agent 有没有变好」**。一个补丁可以完美通过所有测试，同时让 Agent 的实际表现明显下降。这就是绝大多数自进化项目的真实状态：它们在盲目地改。

PandaAgent 的做法是给补丁加第二道门禁：**跑一组有明确预期结果的回归任务，分数不下降才接受。** 这让「进化」变成一个可以被证伪的说法。

---

## 快速开始

### 安装

```bash
git clone https://github.com/Doodle-Lin/panda-agent.git
cd panda-agent
pip install -e ".[test]"
pytest tests/          # 197 个用例，约 2 秒
```

### 配置

```bash
panda config init      # 生成 ~/.panda/config.yaml
```

```yaml
# ~/.panda/config.yaml
model:
  default: "your-model-name"
  base_url: "http://localhost:8000/v1"   # 任何 OpenAI 兼容端点
  api_key: ${PANDA_API_KEY}              # 支持环境变量展开
  max_tokens: 8192

agent:
  max_turns: 10
  max_retries: 3

memory:
  enabled: false                          # 图记忆默认关闭
  graph_url: "http://127.0.0.1:9121"

evolution:
  improve_tools: true
  improve_brain: true
```

```bash
export PANDA_API_KEY="sk-..."
panda config show      # 输出中 api_key 会被脱敏
```

支持任何 OpenAI 兼容端点：vLLM、SGLang、Ollama、LM Studio、OpenAI、DeepSeek、Qwen。推理模型通过 `reasoning_content` 回退处理。

### 跑一个任务（不进化）

```bash
panda run "列出 src/ 下所有 Python 文件，找出最大的那个"
```

### 跑进化循环

```bash
panda evolve "搜索代码库里的 TODO 注释并总结" \
    --target-score 90 \
    --max-rounds 3
```

每一轮发生什么：

```
第 1 轮  Executor  → 通过 ReAct 执行任务
         Evaluator → 打 65/100，根因：「search_files 不输出行号，
                     Agent 无法引用具体位置」
         Improver  → 修改 tools.py 里的 _tool_search_files
                     跑 pytest → 通过 → 跑 benchmark → 未退化 → 保留

第 2 轮  Executor  → 用修改后的工具重跑
         Evaluator → 打 92/100 → 达到目标，停止
```

### 让补丁必须通过实测

默认情况下 Improver 只检查单元测试还过不过。想同时要求 Agent **没有变差**，给它一组任务：

```yaml
# benchmarks/tasks.yaml
- id: search_with_locations
  instruction: 找出 src/ 下所有 TODO，列出文件名和行号
  scorer: exact_match
  expected:
    contains: ["config.py", "7"]
  weight: 1.5

- id: apply_edit
  instruction: 把 config.py 里的 DEFAULT_PORT 从 8080 改成 9090
  scorer: file_state          # 看文件实际状态，不看 Agent 自己怎么说
  expected:
    file: config.py
    contains: "DEFAULT_PORT = 9090"
    not_contains: "DEFAULT_PORT = 8080"
  weight: 2.0
```

```python
from pathlib import Path
from panda_agent.benchmark import load_tasks, run_benchmark, estimate_noise
from panda_agent.orchestrator import Improver

tasks = load_tasks(Path("benchmarks/tasks.yaml"))
workspace = Path("benchmarks")
runner = lambda task: my_executor.execute(task).output

# Agent 本身是随机的，容忍度应该用实测方差来定，不要靠猜。
# 2 * stdev 是个合理的起点。
mean, stdev = estimate_noise(tasks, runner, workspace, runs=3)

improver = Improver(config)
improver.baseline = run_benchmark(tasks, runner, workspace)
improver.benchmark_gate = lambda: run_benchmark(tasks, runner, workspace)
improver.tolerance = max(2.0, 2 * stdev)
```

这样一个「pytest 通过但 benchmark 分数掉了超过容忍度」的补丁会被回滚，而且拒绝原因会喂给下一次尝试的提示词。

**打分器的选择很关键。** `exact_match` 和 `file_state` 是确定性的、可复现的；`llm_judge` 不是，它的噪声会直接传导到「接受/拒绝」的决策里。只在任务真的开放到没法用前两种时才用它。

### Python API

```python
from panda_agent import run_evolution, Task

result = run_evolution(
    executor=None,      # None = 用内置默认实现
    evaluator=None,
    improver=None,
    task=Task(instruction="重构 config 加载器，支持环境变量"),
    target_score=90.0,
    max_rounds=3,
)

print(f"最终分数: {result.final_score}")
print(f"保留的补丁数: {result.total_patches}")
for r in result.rounds:
    print(f"  第 {r.round_num} 轮: {r.evaluation.score:.0f} — {r.evaluation.root_cause}")
```

三个 Agent 都可注入 —— 传你自己的 `Executor` / `Evaluator` / `Improver` 就能切换到别的领域（见[扩展](#扩展)）。

---

## 进化具体怎么跑的

每一轮：

**1. 执行** —— `Executor` 跑 ReAct 循环：LLM 看到 `brain.py` 里的系统提示词加 `tools.py` 里的工具描述，输出 `TOOL_CALL: {"name": ..., "args": {...}}`，结果追加回对话，重复直到 `DONE:` / `FAILED:` 或到达轮次上限。

**2. 评估** —— `Evaluator` 把任务和工具调用轨迹发给 LLM，要求返回 JSON：`{"score": 0-100, "issues": [...], "root_cause": "...", "suggested_changes": "..."}`。

**3. 改进** —— `Improver`：
- 把目标文件（`tools.py` 或 `brain.py`）备份成 `.py.bak`
- 只提取和评估相关的函数（关键词匹配），控制提示词长度
- 让代码模型按 `PATCH_START ... PATCH_END` 格式返回补丁
- 用 libcst 替换定义，**写盘前先验证结果能被解析**
- **门禁 1 —— `pytest tests/`**：回答「代码坏了吗」。失败则从备份恢复，把错误喂回去重试。
- **门禁 2 —— 回归 benchmark**：回答「Agent 变差了吗」。跑任务集，加权分数下降超过容忍度就拒绝，并把原因喂回去，让下一次尝试不再是盲试。没配任务集时跳过。

**4. 循环** —— 直到 `score >= target_score` 或轮次耗尽。

**门禁 2 才是让这个循环可被证伪的关键。** 一个补丁可以是完全合法的 Python、通过全部单元测试，同时让 Agent 实测变差 —— 我们用一个「只是不再输出行号」的 Agent 验证过：加权分 100 → 89.3，现在会被拒绝。

### 什么是可进化的

`tools.py` —— Agent 的**「手」**：

| 工具 | 用途 |
|---|---|
| `read_file` | 读文件 |
| `write_file` | 写/建文件 |
| `search_files` | 正则搜索 |
| `list_files` | 列目录 |
| `patch_file` | 文件内查找替换 |
| `run_command` | 执行白名单内命令，不走 shell —— 见[安全](#安全) |
| `memory_retrieve` | 查图记忆（如启用） |

`brain.py` —— Agent 的**「脑」**：

- `SYSTEM_PROMPT` —— 核心指令集
- `should_retry(tool, error, count, max)` —— 重试策略
- `max_turns_for_task(task)` —— 按复杂度分配轮次预算

函数签名保持稳定，这样 Improver 可以重写函数体而不破坏调用方。

---

## 图记忆

可选的联想记忆，后端是外部图服务。用嵌入相似度加 Personalized PageRank 扩散，所以检索出来的是**相关**知识，不只是字面相似的文本。

```yaml
memory:
  enabled: true
  graph_url: "http://127.0.0.1:9121"
  auto_write: true      # 自动持久化任务结果
```

```python
from panda_agent.memory import MemoryClient

mem = MemoryClient()
if mem.is_available():
    mem.write("老显卡上 vLLM 需要加 --enforce-eager", title="vllm-tip")
    ctx = mem.retrieve_context("为什么 vllm 启动很慢", top_k=3)
    # → 返回可直接注入 LLM 的 markdown 块
```

**设计说明：** 服务挂掉时每个方法都降级为空操作 —— `retrieve` 返回 `[]`，`write` 返回 `{"error": ...}`，不抛异常。记忆是增强项，永远不是硬依赖。这是刻意的：一个因为旁路服务连不上就崩掉的 Agent 框架没法用。

**当前限制：** 图服务是独立项目，没打包进来，所以这个特性不太好试。打包一个最小参考实现在[路线图](#路线图)里。

---

## 安全

威胁模型不是「操作者有恶意」。**Agent 的下一步动作是由一个上下文里含有文件内容和命令输出的模型决定的** —— 所以它读到的任何东西都能影响它接下来干什么。工具参数是不可信输入。

### 已经强制的

**命令走白名单，不经过 shell。** `run_command` 把字符串解析成 argv 列表，拒绝 shell 元字符，然后以 `shell=False` 执行。没有 shell，就没有注入的地方。

```bash
run_command "pytest tests/ -q"              # 允许
run_command "echo SAFE; echo INJECTED"      # 拒绝：元字符 ';'
run_command "curl http://example.com"       # 拒绝：'curl' 不在白名单
```

**文件访问限制在工作区内。** 路径先解析再做包含检查，所以 `..` 穿越和符号链接逃逸都会被拦。

```bash
read_file "src/panda_agent/tools.py"        # 允许
read_file "/etc/passwd"                     # 拒绝：越出工作区
read_file "a/../../../etc/passwd"           # 拒绝：越出工作区
```

**子进程拿到的是清理过的环境变量。** 名字长得像凭据的（`*KEY*`、`*TOKEN*`、`*SECRET*`、`*PASSWORD*` 等）会被移除，这样一个 dump 环境变量的命令不会变成凭据泄露。

| 变量 | 用途 |
|---|---|
| `PANDA_WORKSPACE` | 文件工具的边界目录（默认当前目录） |
| `PANDA_ALLOWED_COMMANDS` | 额外允许的命令，空格或逗号分隔 |
| `PANDA_UNSAFE=1` | 完全关闭强制 —— 仅用于隔离环境 |

### 还没有的

**没有操作系统级隔离。** 白名单管的是**哪些程序**能跑，不管程序跑起来之后能干什么。`python3` 必须放开（循环需要它），而 `python3 -c '...'` 能干任何 Python 能干的事。跑不可信任务请整体放进容器或虚拟机 —— **白名单是纵深防御，不是牢笼。**

**测试门禁原理上可绕过。** 补丁靠 `pytest tests/` 验证，但 Agent 手上有 `patch_file`，而测试和源码在同一个仓库里。没有任何结构性机制阻止它改测试而不是改代码。回归 benchmark 让这件事更难（削弱测试并不能提高 benchmark 分数），但正确的解法是在独立 checkout 里验证。见路线图 R2。

**没有提示词注入防御。** 文件内容和命令输出直接回灌进对话。一个含有对抗性指令的文件仍然可以引导循环；边界能限制损害范围，但不能识别攻击意图。

安全问题欢迎通过 GitHub issue 反馈。

---

## 已知限制

直说，因为这些决定了这个项目对你有没有用：

### 🔴 Improver 不从自己的历史里学习

每次改进都从零开始。循环知道第 3 轮打了 72 分，但不知道第 1 轮已经试过类似的补丁并且被拒了。每个补丁的结果 —— 接受、拒绝、以及为什么 —— 全部丢弃。

这是**剩下最大的缺口，也是最有意思的一个**：图记忆已经存在，但只服务 Executor。把补丁结果喂进去，Improver 就能积累关于「怎么改这个代码库」的元知识，而不只是「怎么做这个任务」。见路线图 R1。

### 🟡 benchmark 门禁需要有任务集才有意义

`check_no_regression` 的质量完全取决于你给它什么任务。`benchmarks/` 里自带的那套是起点，不是 benchmark —— 5 个任务、玩具级 fixture。真实部署需要能代表你实际工作负载的任务，并且跑 `estimate_noise` 用实测方差来定容忍度，而不是用默认的 2.0。

### 🟡 没有操作系统级沙箱

白名单和路径边界堵住了明显的洞，但 `python3` 必须放开，而它能干任何 Python 能干的事。见[安全](#安全)。

### 🟡 ReAct 终止判断可能误判

如果响应里没有 `TOOL_CALL`、没有 `DONE:`，长度超过 20 字符，且不以 "Continue" 开头，就被当作最终答案（[`react.py`](src/panda_agent/react.py)）。这个逻辑存在是因为有些模型不输出 `DONE:` 前缀，但一个正在「想出声」的模型会被误判为已完成。结构化输出能消除这种猜测。

### 🟡 图记忆需要外部服务

`memory.enabled: true` 期待一个旁路服务。没有它，框架优雅降级而不是崩溃 —— 但这个特性实际上是关闭的，直到你自己提供一个。见路线图 R4。

---

## 路线图

按「对项目真正有用」的程度排序。

### R1 —— 把进化历史喂进图记忆 🔴

**剩下最有意思的活。** 现在每个补丁的结果都被丢掉；存下来，循环就从「瞎试」变成「不试已经失败过的」：

- 每次尝试存成一个节点：目标函数、补丁 diff、分数变化、接受/拒绝、拒绝原因
- 生成补丁前先查：把同一个函数上过往尝试的结果给 Improver
- 把积累的知识显式呈现出来，例如：
  `✅ 已接受 (+6.2)：给 search_files 输出加了行号`
  `❌ 已拒绝 (-8.1)：改成 JSON 输出，LLM 反而更难解析`

**这才是「自我改进」而非「自我修改」的分界线**：它学的是怎么改自己，不只是怎么做任务。

### R2 —— 在独立 checkout 里验证补丁 🔴

现在 Agent 能碰到那些约束它的测试。把代码树复制到临时目录、在副本上应用补丁、对副本跑测试，能让「削弱门禁」从「不鼓励」变成「结构上不可能」。

### R3 —— 操作系统级隔离 🟡

白名单管得住哪些程序能跑，管不住 `python3` 跑起来之后干什么。给命令工具和测试执行加容器或 `seccomp`/`nsjail`。

### R4 —— 打包一个参考图记忆服务 🟡

内置一个最小的嵌入 + PageRank 实现，让 `memory.enabled: true` 开箱可用，而不是依赖一个没打包的外部服务。

### R5 —— 可观测性 🟢

- 持久化每一轮：任务、分数、补丁 diff、测试输出、benchmark 变化
- `panda history` 查看进化轨迹
- 导出 trace 供分析

### R6 —— 打包发布 🟢

- 发 PyPI
- CI：PR 上跑 pytest + ruff + mypy

---

## 近期改动

已完成的路线图项，附上当初促成它们的证据：

| 改动 | 为什么 |
|---|---|
| **回归门禁**（`benchmark.py`） | 旧门禁问的是「测试过吗」，不是「Agent 变好了吗」。实测验证：一个仅仅不再输出行号的 Agent，加权分 100 → 89.3，现在会被拒绝 —— 而它能通过全部单元测试。 |
| **libcst 补丁**（`patching.py`） | 正则替换在装饰器、`async`、嵌套函数、类内定义上会失败。最糟的情况：函数体里含有 `\ndef ` 这段文本时，文件被截断成语法错误状态，**而且发生在 pytest 能发现之前**。 |
| **严格评估解析**（`parsing.py`） | 解析失败会静默变成 `score = 50`，于是 Improver 在对着噪声做优化。现在失败会重试一次，然后明确报告「没有信号」。 |
| **执行边界**（`security.py`） | `shell=True` 经实测可利用：`echo SAFE; echo INJECTED` 两半都执行了。文件工具接受 `..` 穿越。 |
| **Improver 提示词修复** | `_IMPROVE_PROMPT` 里有个字面量 `{code_here}`，被 `str.format` 当成待填字段，所以**每一次调用都抛 `KeyError`** —— 这个自进化框架的核心机制从未运行过。是给它写第一个测试时发现的。 |
| **循环测试覆盖** | `orchestrator.py` 从 0 个测试到 19 个，上面那个提示词 bug 就是这么暴露的。 |

测试规模：19 → 197 个用例。

---

## 扩展

实现三个角色，就能把这个循环对准你自己的领域：

```python
from panda_agent.types import Task, ExecutionResult, Evaluation

class ImageExecutor:
    def execute(self, task: Task) -> ExecutionResult:
        # 跑你自己的流程
        return ExecutionResult(output_path="out.jpg", success=True, tool_calls=[])

class ImageEvaluator:
    def evaluate(self, task: Task, result: ExecutionResult) -> Evaluation:
        score = my_quality_metric(result.output_path)   # 比如用 VLM 打分
        return Evaluation(
            score=score,
            issues=["背景模糊不均匀"],
            root_cause="高斯核大小是硬编码的",
            suggested_changes="让核大小自适应图像分辨率",
        )

run_evolution(
    executor=ImageExecutor(),
    evaluator=ImageEvaluator(),
    improver=None,          # 内置 Improver 会改 tools.py
    task=Task(input_path="in.jpg", instruction="把背景模糊掉"),
)
```

完整示例见 `plugins/photo_edit/`。

**Evaluator 的设计是杠杆所在。** 含糊的评估产出含糊的 `root_cause`，进而产出没用的补丁。评估越具体、越有诊断性，进化效果越好。能用客观指标的地方就别用 LLM 的主观判断。

---

## 测试

```bash
pytest tests/ -q          # 197 个用例，约 2 秒
```

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| `test_framework.py` | 32 | types、config、brain、tools、ReAct 解析、LLM、memory |
| `test_security.py` | 32 | 命令注入、路径穿越、环境变量清理 |
| `test_benchmark.py` | 29 | 打分器、加权、门禁决策、噪声估计 |
| `test_parsing.py` | 28 | JSON 提取、撇号处理、解析失败语义 |
| `test_patching.py` | 22 | 装饰器、async、嵌套、类方法、常量替换 |
| `test_orchestrator.py` | 19 | 补丁保留/回滚/因退化被拒、Evaluator 重试、循环计分 |

有几个测试是专门用来**钉住曾经坏过的行为**的，最重要的是 `test_patch_that_passes_tests_but_degrades_is_rejected` —— 整个 benchmark 门禁存在的理由就是它。如果哪个测试挡住了你，那是信息，不是障碍。

---

## 贡献

欢迎贡献，特别是 R1 和 R2 —— 那是「demo」和「工具」的分界线。

```bash
git clone https://github.com/Doodle-Lin/panda-agent.git
cd panda-agent
pip install -e ".[test]"
pytest tests/
```

约定：

- **行为变更必须配测试。** 特别是碰到进化循环或补丁应用的部分。
- **不要为了让补丁通过而削弱验证门禁。** 如果门禁本身不对，那就明确地修门禁，并说明理由。
- **不要删掉回归测试来让改动通过。** 有些测试专门钉住曾经坏过的行为。
- **保持记忆可选。** 图服务缺失时优雅降级是硬要求。
- **文档跟代码同步。** 行为变了，同一个 PR 里更新 README（中英两份）。

适合入手的：R4（打包参考记忆服务）和 R5（可观测性）比较独立。R1 是这个项目真正差异化的部分，想做点有意思的可以从它开始。

---

## 设计原则

- **验证胜过生成。** 生成补丁很容易，证明它有用才是产品。
- **脑和手一起进化。** 提示词和决策逻辑跟工具一样可以被改。
- **失败即回滚。** 补丁失败一定回滚。旁路服务缺失只降级，绝不崩溃。
- **一切可注入。** Executor / Evaluator / Improver 都能替换成任何领域的实现。
- **模型无关。** 任何 OpenAI 兼容端点；推理模型通过 `reasoning_content` 回退支持。
- **对现状诚实。** 限制是写出来的，不是藏起来的。

---

## 许可证

MIT —— 见 [LICENSE](LICENSE)。

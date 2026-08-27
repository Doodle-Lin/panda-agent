# Feature Spec: PandaAgent — 高质量开源自进化Agent框架

> **Rule:** Write WHAT and WHY. Not HOW. No tech stack, no API design, no code structure.

## Goal

将 PandaAgent 从"有趣的原型"完善成一个高质量的开源项目:核心自进化循环可靠且可验证、图记忆成为真正的自我改进引擎、项目可被外部用户直接 clone 使用并贡献代码。

## User Stories

> Prioritize as P1 (MVP — delivers value alone), P2, P3. Each story must be independently testable and independently deliverable. P1 alone should be a viable MVP.

### User Story 1 — 测试全绿 + 代码质量基线 (Priority: P1)

作为一个开发者,我 clone PandaAgent 后能立刻 `pip install -e ".[test]"` 并跑通所有非 slow 测试,没有 import 错误、没有编码问题、没有接口不对齐的失败。

**Why this priority**: 如果连测试都跑不通,后面所有开发都没有安全网。这是所有后续工作的前提。

**Independent Test**: `python -m pytest tests/ -q -m "not slow"` → 0 failures, 0 errors。

**Acceptance Scenarios** (Given/When/Then):

1. **Given** a fresh clone, **When** user runs `pip install -e ".[test]" && pytest tests/ -q -m "not slow"`, **Then** all tests pass (0 failed, 0 errors)
2. **Given** test_security.py exists, **When** pytest collects it, **Then** all 60 tests pass — no import errors, no interface mismatch
3. **Given** tools.py has `get_tool_schemas`, **When** react.py imports it, **Then** no ImportError
4. **Given** pyproject.toml is UTF-8, **When** pytest reads it, **Then** no UnicodeDecodeError
5. **Given** ruff is installed, **When** user runs `ruff check src/ tests/`, **Then** 0 errors (warnings acceptable for now)

---

### User Story 2 — 核心进化循环可靠 (Priority: P1)

作为一个研究者,我运行 `panda evolve -t "task"` 后,循环的每个环节都产生可靠的信号:评估分数可信(解析失败不伪装成50分)、补丁门禁有回归基准、循环结束后磁盘上的代码是最高分那轮的。

**Why this priority**: 自进化的核心价值是"可验证的改进"。如果信号不可信、代码不回退到最优,整个循环就是随机漫步——这是 DESIGN.md 的核心洞察。

**Independent Test**: 运行 `panda evolve` 后检查 `result.final_score` 与磁盘上 tools.py/brain.py 的实际状态一致。

**Acceptance Scenarios** (Given/When/Then):

1. **Given** Evaluator receives an unparseable LLM response, **When** it tries twice, **Then** returns `None` (not `Evaluation(score=50)`) — no fabricated score
2. **Given** Improver applies a patch that passes pytest but degrades benchmark score by > tolerance, **When** the benchmark gate runs, **Then** the patch is reverted with a reason containing "regression"
3. **Given** Round 1 scores 85 (good patch) and Round 2 scores 60 (bad patch), **When** the loop finishes, **Then** disk has Round 1's code, not Round 2's — `restored_from_round == 1`
4. **Given** `result.final_score == 85`, **When** user reads tools.py on disk, **Then** it matches the snapshot taken at the start of Round 1
5. **Given** Improver has `benchmark_gate=None`, **When** it runs, **Then** the gate is skipped (not crashed) — optional feature degrades gracefully

---

### User Story 3 — 进化历史写入图记忆 (Priority: P2)

作为 PandaAgent 的开发者,我希望每次补丁的接受/拒绝结果都写入图记忆,这样 Improver 在生成下一个补丁前能检索到"以前遇到类似问题是怎么解的",避免重复被拒绝的方向。

**Why this priority**: 这是项目最核心的差异化——不只是改代码,而是从改代码的成败中积累关于"如何改代码"的元知识。DESIGN.md R1 称之为"最大的未做部分"。

**Independent Test**: 运行一轮进化后,`panda memory search "patch accepted"` 能检索到结构化的补丁记录。

**Acceptance Scenarios** (Given/When/Then):

1. **Given** a patch is accepted (score improved), **When** the loop writes to memory, **Then** a node with `node_type="patch_accepted"` exists, containing: target file, root_cause, score delta, explanation
2. **Given** a patch is rejected (regression or tests failed), **When** the loop writes to memory, **Then** a node with `node_type="patch_rejected"` exists, containing: target file, root_cause, rejection reason, score delta
3. **Given** past patch outcomes exist in memory, **When** Improver generates a new patch for a similar root_cause, **Then** the improve prompt includes a "历史经验" section with accepted/rejected examples
4. **Given** memory is unavailable (service down or not installed), **When** the loop runs, **Then** it degrades gracefully — no crash, no memory writes, evolution continues

---

### User Story 4 — 图记忆可移植 (Priority: P2)

作为一个外部用户,我 clone PandaAgent 后不需要安装任何外部服务就能使用图记忆功能——memory.py 不能硬编码 `${PANDA_HOME}/graph_memory` 路径。

**Why this priority**: 一个开源项目的核心特性不能依赖未公开的外部路径。这会直接杀死采用率(DESIGN.md 4.2)。

**Independent Test**: 删除 `${PANDA_HOME}/graph_memory` 目录后 `panda memory search "test"` 不崩溃。

**Acceptance Scenarios** (Given/When/Then):

1. **Given** memory.py is imported, **When** it tries to find graph_memory, **Then** it searches `PANDA_HOME/graph_memory/` or `sys.path`, not a hardcoded `E:\` path
2. **Given** graph_memory is not installed, **When** MemoryClient initializes, **Then** it returns `is_available() == False` without raising
3. **Given** graph_memory IS available (bundled or installed), **When** user runs `panda memory search "query"`, **Then** results are returned via embedding + PageRank

---

### User Story 5 — 独立检出验证 (Priority: P3)

作为一个安全研究者,我希望补丁验证在一个 Agent 碰不到的干净检出中运行,这样即使 Agent 试图删测试断言,门禁也无法被绕过。

**Why this priority**: 安全加固,在核心循环稳定后做。不是 MVP,但决定了项目在不可信输入上的可用性。

**Independent Test**: 模拟 Agent 修改 test 文件后,验证仍然使用原始测试跑门禁。

**Acceptance Scenarios** (Given/When/Then):

1. **Given** Agent has write access to tests/, **When** a patch is applied, **Then** verification runs in a git worktree at HEAD — Agent's changes to tests/ are invisible
2. **Given** Agent weakened a test assertion, **When** the worktree gate runs, **Then** the weakened test is not used — original test from HEAD runs

---

### User Story 6 — 可观测性 + 打包发布 (Priority: P3)

作为一个开源用户,我希望 `panda history` 能看到进化轨迹(分数、补丁diff、benchmark delta),项目能在 PyPI 安装,CI 在 PR 上自动跑测试和 lint。

**Why this priority**: 面向采用率,在核心功能完成后做。

**Independent Test**: `pip install panda-agent` 可用,`panda history` 输出格式化表格。

**Acceptance Scenarios** (Given/When/Then):

1. **Given** evolution has run 3 rounds, **When** user runs `panda history`, **Then** a table shows: round, score, benchmark delta, patched file, status, reason
2. **Given** a PR is opened, **When** CI runs, **Then** pytest + ruff both pass on the PR
3. **Given** user runs `pip install panda-agent`, **When** install completes, **Then** `panda --version` works

---

## Edge Cases

- What happens when all rounds produce unparseable evaluations? → Loop should report "no signal" and exit, not fabricate scores
- What happens when LLM returns empty content with tool_calls? → Native FC path handles this (tool_calls processed, content treated as optional)
- What happens when memory write fails? → Graceful degradation, evolution continues, error logged to stderr
- What happens when benchmark task suite is empty? → Gate 2 skipped (same as `benchmark_gate=None`)

## Acceptance Criteria (cross-cutting)

> Every line below must be translatable to a test case with a yes/no answer.

- [ ] `pytest tests/ -q -m "not slow"` → 0 failed, 0 errors (US1)
- [ ] `ruff check src/ tests/` → 0 errors (US1)
- [ ] Unparseable eval response → `evaluate()` returns `None`, not `Evaluation(score=50)` (US2)
- [ ] Patch passing pytest but degrading benchmark by > tolerance → reverted (US2)
- [ ] Loop finishes → disk code matches best-scoring round (US2)
- [ ] `restored_from_round` is set when code was rewound (US2)
- [ ] `benchmark_gate=None` → gate skipped, no crash (US2)
- [ ] Accepted patch → memory node with `node_type="patch_accepted"` and score delta (US3)
- [ ] Rejected patch → memory node with `node_type="patch_rejected"` and rejection reason (US3)
- [ ] Improver prompt includes history section when past outcomes exist (US3)
- [ ] Memory unavailable → no crash, evolution continues (US3, US4)
- [ ] memory.py has no hardcoded `E:\` path (US4)
- [ ] graph_memory not installed → `is_available() == False`, no exception (US4)
- [ ] Verification in git worktree at HEAD — Agent's test changes invisible (US5)
- [ ] `panda history` shows formatted evolution trail (US6)
- [ ] `pip install panda-agent` works (US6)
- [ ] CI runs pytest + ruff on PRs (US6)

## Non-Functional Requirements

- [ ] No new hard dependencies for core package (requests, rich, pyyaml, libcst only)
- [ ] Memory/graph features as optional extras: `pip install -e ".[memory]"`
- [ ] Python 3.12+ compatible (uses `str | None` syntax)
- [ ] Windows compatible (no Unix-only paths/commands in core)
- [ ] All source files UTF-8 (no BOM, no UTF-16)

## Out of Scope

- OS-level sandboxing (Docker/seccomp) — documented as known limitation
- Prompt injection defense — documented, mitigation suggested, not solved
- Multi-agent parallel evolution — single evolution loop only
- Web UI — CLI + TUI only
- Non-OpenAI-compatible LLM backends — only OpenAI-compatible API

## Open Questions

- [UNCLEAR: Should we bundle a minimal graph-memory server in the repo, or keep it as a separate package?] — DESIGN.md R4 suggests bundling; needs decision on packaging strategy
- [UNCLEAR: Should the regression benchmark run with real LLM (slow, ~30s per round) or mock LLM (fast, but tests nothing real)?] — DESIGN.md suggests real LLM for final validation, mock for CI

## Assumptions

- Users have an OpenAI-compatible LLM endpoint configured (vLLM, SGLang, Ollama, etc.)
- Users have Python 3.12+ installed
- Graph memory is optional — framework works without it, just no learning accumulation
- The existing `benchmarks/tasks.yaml` is a starting point, not a real benchmark — users need to add representative tasks for their domain

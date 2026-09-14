# Spec: PandaAgent Optimization Audit Fixes

> **Rule:** Write WHAT and WHY. Not HOW. No tech stack, no API design, no code structure.

## Goal

将 PandaAgent 从"Alpha 原型"提升到"可被外部用户与贡献者信任"的状态:堵住审计发现的 14 处缺口,使核心进化闭环的兜底真正可靠、安全边界无已知绕过、可观测性闭环、文档与代码一致。

## User Stories

> 优先级 P1=阻塞可信度,P2=阻塞可用性,P3=阻塞采用率。每个 story 独立可测。

### US-A1 — 进化门真正接上 (Priority: P1)

作为交互式用户,我运行 `panda evolve` 或 `panda chat` 触发进化时,补丁不仅要过 pytest,还要过回归 benchmark 门 —— 否则一个"测试全绿但 benchmark 退化"的补丁会静默落地,这正是项目 R1 想修的失败模式。

**Why**: CLI 是用户的主入口,这个入口跳过 benchmark 门,意味着核心卖点"可验证的改进"在用户最常用路径上不生效。

**Independent Test**: 配置 `evolution.benchmark_suite` 指向 tasks.yaml 后,跑 `panda evolve`,退化补丁被回退。

**Acceptance Scenarios**:

1. **Given** tasks.yaml 配置存在, **When** 用户运行 `panda evolve`, **Then** Improver 收到 `benchmark_gate` 与 `baseline`,Gate 2 实际执行
2. **Given** tasks.yaml 未配置, **When** 用户运行 `panda evolve`, **Then** Gate 2 优雅跳过(不崩溃)
3. **Given** 一个补丁过 pytest 但 benchmark 退化超 tolerance, **When** CLI 路径执行, **Then** 补丁被回退,理由含 "regression"

### US-A2 — 隔离验证失败时拒绝补丁 (Priority: P1)

作为安全使用者,当我显式启用 `use_worktree=True` 后,worktree 创建失败不应让补丁静默通过 —— 应失败关闭,把补丁回退。

**Why**: fail-open 让 R2 的"独立检出验证"在配置错或权限缺失时形同虚设;这是安全边界,不能"差不多就行"。

**Independent Test**: 模拟 worktree 创建失败,验证补丁被回退而非放行。

**Acceptance Scenarios**:

1. **Given** `use_worktree=True` 且 worktree 创建失败, **When** Improver 验证, **Then** 返回 reject,补丁回退
2. **Given** `use_worktree=False`(默认), **When** Improver 验证, **Then** 保持现状(显式 opt-out)

### US-A3 — 中断不残留补丁 (Priority: P1)

作为开发者,当 pytest 子进程崩溃或被 Ctrl-C 中断时,盘上的源文件必须回到 patch 前的状态,而不是带着未验证的补丁停留。

**Why**: 一次中断留下脏盘,后续所有测试都在污染状态上跑,进化信号全失效。

**Independent Test**: 模拟 pytest 中抛 KeyboardInterrupt,验证源文件被恢复到 backup。

**Acceptance Scenarios**:

1. **Given** patch 已写盘, **When** pytest 抛 KeyboardInterrupt, **Then** finally 块恢复 backup,源文件回到 patch 前
2. **Given** backup 文件不存在(上次中断遗留), **When** 恢复逻辑触发, **Then** 不抛 FileNotFoundError,记录 warning

### US-A4 — security.py 进化受配置控制 (Priority: P1)

作为配置者,我能在 config 里关掉 security.py 的自动进化 —— 安全面比工具面敏感,默认应关闭。

**Why**: 当前 security.py 每次 `improve()` 都被尝试 patch,即使 tools/brain 都关了;配置面失控。

**Independent Test**: `evolution.improve_security: false` 时,security.py 不被 patch。

**Acceptance Scenarios**:

1. **Given** `improve_security` 未设置, **When** 用户看 config, **Then** 默认值是 `false`
2. **Given** `improve_security: false`, **When** 一轮 improve 执行, **Then** security.py 不在 `results` 列表里
3. **Given** `improve_security: true`, **When** 一轮 improve 执行, **Then** security.py 被尝试 patch

### US-A5 — symlink 逃逸被拒 (Priority: P1)

作为安全使用者,workspace 内一个指向 workspace 外的 symlink 不应让 `read_file` 读出去。

**Why**: 当前 `resolve_path` follow symlink 后,containment 检查在目标路径上,逃逸成功。这是已知的边界绕过。

**Independent Test**: 在 workspace 内创建指向 `/etc/passwd`(或 Windows 等价)的 symlink,验证 `read_file` 拒绝。

**Acceptance Scenarios**:

1. **Given** workspace 内有 symlink 指向外部, **When** `read_file` 该 symlink, **Then** 拒绝,返回 escape error
2. **Given** workspace 内有 symlink 指向 workspace 内部, **When** `read_file` 该 symlink, **Then** 允许

### US-A6 — pip/uv install 默认拒绝 (Priority: P2)

作为安全使用者,`pip install <任意包>` 不应在默认配置下被允许 —— 等同任意代码执行。

**Why**: allowlist 含 pip/uv 是必要的(用于依赖管理),但 install 子命令是 RCE 入口,需独立门控。

**Independent Test**: 默认配置下 `pip install evil-pkg` 被拒;`PANDA_ALLOW_INSTALL=1` 时放行。

**Acceptance Scenarios**:

1. **Given** 默认配置, **When** agent 跑 `pip install evil`, **Then** 拒绝
2. **Given** `PANDA_ALLOW_INSTALL=1`, **When** agent 跑 `pip install evil`, **Then** 允许(用户显式承担风险)
3. **Given** 默认配置, **When** agent 跑 `pip list`(只读), **Then** 允许

### US-A7 — shell 元字符过滤不再误杀 (Priority: P2)

作为使用者,在 `shell=False` 下,`grep "TODO.*FIXME"` 这种含 `*` 的合法正则不应被拒。

**Why**: 当前 `_SHELL_METACHARACTERS` 含 `*`/`?`/`!`,但 shell=False 下这些不是 shell 元字符。误杀正常搜索。

**Independent Test**: `run_command 'grep "TODO.*FIXME" src/'` 在 shell=False 下被允许。

**Acceptance Scenarios**:

1. **Given** shell=False, **When** 参数含 `*` 或 `?`, **Then** 允许(不是 shell 元字符)
2. **Given** shell=False, **When** 参数含 `;` 或 `&` 或 `` ` ``, **Then** 拒绝(仍是注入风险,避免歧义)

### US-A8 — 图记忆 schema 可演进 (Priority: P2)

作为使用者,我用旧版 PandaAgent 创建的 memory.sqlite3 在升级后不应 INSERT 失败 —— schema 必须版本化迁移。

**Why**: 当前 `_create_schema` 只 migrate `source` 一列;后续加的列在旧库上不存在,INSERT 炸。

**Independent Test**: 创建 v1 schema DB,用新代码打开,写入新字段不报错。

**Acceptance Scenarios**:

1. **Given** 一个旧 schema 的 memory DB, **When** 新代码首次连接, **Then** 自动迁移到当前 schema,无数据丢失
2. **Given** 迁移过程, **When** 中途失败, **Then** 原 DB 不被破坏(原子迁移或回滚)

### US-A9 — 图记忆写入可扩展、可并发 (Priority: P2)

作为长期使用者,记忆节点数增长到数千后 `write_if_novel` 不应 O(N) 扫描,且并发写不应损坏 embeddings 文件。

**Why**: 当前无索引 + 无锁,长期使用必然退化或损坏。

**Independent Test**: 1000 次连续写入耗时与 100 次的比例 < 2x;10 线程并发写后 embeddings 文件可读。

**Acceptance Scenarios**:

1. **Given** 1000 节点, **When** 写第 1001 个, **Then** 耗时不爆炸(SQL 索引过滤)
2. **Given** 10 线程并发写, **When** 全部完成, **Then** embeddings 文件完整可读
3. **Given** 任一写中途中断, **When** 后续读, **Then** 不读到半写状态(原子 rename)

### US-A10 — Skill 匹配不再过松 (Priority: P2)

作为使用者,一句"帮我做个视频"不应匹配到 5 个无关 skill;短 trigger 不应靠双向子串匹配几乎所有长输入。

**Why**: 当前 `trigger in text or text in trigger` 让 3 字 trigger 配 200 字输入几乎总命中,污染 prompt。

**Independent Test**: 输入 200 字、3 字 trigger → 不匹配;4 字 trigger 是输入子串 → 匹配。

**Acceptance Scenarios**:

1. **Given** trigger="做视频" (3字), text=200字描述任务, **When** 匹配, **Then** 不命中
2. **Given** 多个 skill 命中, **When** 注入 prompt, **Then** 按分数排序,至多 3 个
3. **Given** 一个 skill 文件 YAML 解析失败, **When** 加载, **Then** 记录到 stderr,不静默 return None

### US-A11 — Skill 自动生成可验证 (Priority: P2)

作为使用者,任务跑完后我能知道"agent 是否真的生成了 skill 文件",而不是只靠 LLM 自觉。

**Why**: 当前 skill 自动生成只在 prompt 里"告诉 LLM",无后置检查,生成与否不可观测。

**Independent Test**: 跑一个 5+ tool call 的任务,检查结果对象含 `skill_auto_generated: bool`。

**Acceptance Scenarios**:

1. **Given** 任务 5+ tool call 完成, **When** 检查 `$PANDA_HOME/skills/`, **Then** 若有新 .md 文件,`skill_auto_generated=True`
2. **Given** 任务 <5 tool call, **When** 完成, **Then** `skill_auto_generated=False`(不强制)
3. **Given** 已有 skill 文件, **When** 任务完成但未生成新 skill, **Then** `skill_auto_generated=False`

### US-A12 — 进化历史从脚本路径也写入 (Priority: P2)

作为研究者,我跑 `scripts/run_experiment.py` 后 `panda history` 能看到这一轮的轨迹 —— 当前脚本路径完全跳过 `record_evolution`。

**Why**: R5"Persist every round"在脚本路径上未实现,实验结果无审计 trail。

**Independent Test**: 跑 `run_evolution(...)` 后 `panda history --limit 5` 含本轮记录。

**Acceptance Scenarios**:

1. **Given** `run_evolution()` 完成, **When** 检查 history jsonl, **Then** 每轮有记录
2. **Given** 写入失败(磁盘满), **When** `record_evolution` 返回, **Then** 返回 False 且 stderr 有日志,不静默吞
3. **Given** 历史已 1000 条, **When** `panda history --limit 10`, **Then** 流式读取,不全量加载

### US-A13 — React 不烧光 turn (Priority: P2)

作为使用者,模型陷入空响应或 doom loop 时,react 循环应主动跳出,而不是烧光所有 turn。

**Why**: 当前空内容 native FC 持续递增 turn;doom loop salvage 在首次检测即触发,不给警告机会。

**Independent Test**: 模拟 5 次连续空响应,验证循环在 2 次后 DONE 而非继续。

**Acceptance Scenarios**:

1. **Given** native FC 返回空 content 且有 prior tool_calls, **When** 连续 2 次空, **Then** DONE 退出,不递增 turn
2. **Given** doom loop 首次检测, **When** 注入警告, **Then** 给 agent 一轮机会自救
3. **Given** doom loop 二次检测(同 pattern), **When** 触发 salvage, **Then** 主动跳出

### US-A14 — Benchmark 评分不再空匹配 (Priority: P2)

作为使用者,一个 `contains: []` 的 benchmark 任务不应让 agent 答 "ok" 拿满分。

**Why**: 当前 `score_exact_match` 空 contains + 非空 text → 100.0,评分无效。

**Independent Test**: `expected.contains=[]`, agent 输出 "ok" → score 0,不是 100。

**Acceptance Scenarios**:

1. **Given** `expected.contains=[]` 且 `expected.text` 未设置, **When** agent 输出任意, **Then** score 0(无校验目标 = 无分)
2. **Given** `expected.contains=["config.py"]`, **When** agent 输出含 "config.py", **Then** score 100(正常路径)

### US-A15 — README 与代码状态一致 (Priority: P3)

作为外部用户,我读 README 时看到的 R4 状态、配置项、环境变量应与代码实际一致 —— 不应再说"lexical only"而代码已有 embedding。

**Why**: 文档落后于代码会让用户低估能力或误用。

**Independent Test**: README R4 段落提到 embedding-when-available;config 表含 `improve_security`、`benchmark_suite`、`benchmark_tolerance`。

**Acceptance Scenarios**:

1. **Given** memory.py 支持 embedding, **When** 读 README R4, **Then** 提到 sentence_transformers 可选
2. **Given** 新增 config 字段, **When** 读 README 配置表, **Then** 三项都在
3. **Given** 新增 env 变量, **When** 读 README env 表, **Then** `PANDA_ALLOW_INSTALL` 在

### US-A16 — 仓库无垃圾文件 (Priority: P3)

作为贡献者,我 clone 仓库后根目录不应有 `generate_gradient.py`、`_write_helper.py`、`test.txt`、`~/` 这种实验残留。

**Why**: 污染根目录、误导新贡献者、可能被 IDE 误索引。

**Independent Test**: `git ls-files | grep -E '^(_|generate_gradient|gradient|test.txt|~)'` 输出为空。

**Acceptance Scenarios**:

1. **Given** master 分支, **When** 列出根目录跟踪文件, **Then** 无 `generate_gradient.py`、`gradient.png`、`_*.py`、`test.txt`、`~/`
2. **Given** `.gitignore`, **When** 检查, **Then** 含 `_*.py`、`~/`、`gradient.png`、`test.txt` 模式

## Edge Cases

- 用户配置 `benchmark_suite` 指向不存在的文件 → CLI 启动时报错并退回 Gate 2 关闭,不崩溃
- `sentence_transformers` 装了但模型下载失败 → 回退到 lexical,记 stderr warning
- 旧 memory DB schema_version 字段不存在 → 视为 v0,运行完整迁移链
- symlink 检测在 Windows 上需处理 reparse point

## Acceptance Criteria (cross-cutting)

- [ ] `pytest tests/ -q -m "not slow"` 不低于 baseline(374 passed, 1 skipped)
- [ ] `ruff check src tests scripts` 不引入新错误
- [ ] CLI 路径在配置 suite 时实际执行 Gate 2 (US-A1)
- [ ] worktree 创建失败 → 补丁被回退 (US-A2)
- [ ] pytest 中断 → 源文件恢复到 backup (US-A3)
- [ ] `improve_security: false` → security.py 不被 patch (US-A4)
- [ ] workspace 内 symlink 指向外部 → read_file 拒绝 (US-A5)
- [ ] `pip install` 默认拒绝,`PANDA_ALLOW_INSTALL=1` 放行 (US-A6)
- [ ] shell=False 下 `*` `?` 允许,`;` `&` `` ` `` 拒绝 (US-A7)
- [ ] 旧 schema DB 自动迁移 (US-A8)
- [ ] 1000 写入 vs 100 写入耗时比 < 2x (US-A9)
- [ ] 10 线程并发写 embeddings 不损坏 (US-A9)
- [ ] 3 字 trigger 不匹配 200 字 text (US-A10)
- [ ] YAML 解析失败 → stderr 日志 (US-A10)
- [ ] `skill_auto_generated` 字段存在 (US-A11)
- [ ] `run_evolution` 写 history jsonl (US-A12)
- [ ] `record_evolution` 失败 → stderr (US-A12)
- [ ] `panda history --limit N` 流式 (US-A12)
- [ ] 连续空 native FC → 2 次后 DONE (US-A13)
- [ ] doom loop 二次检测才 salvage (US-A13)
- [ ] 空 contains 不给满分 (US-A14)
- [ ] README R4 + 配置表 + env 表更新 (US-A15)
- [ ] 根目录无垃圾文件 (US-A16)

## Non-Functional Requirements

- [ ] 无新硬依赖(仅用已有:libcst, pyyaml, requests, rich;新增 sentence_transformers 已可选)
- [ ] Python 3.12+ 兼容
- [ ] Windows 兼容(无 Unix-only 路径/命令)
- [ ] 所有源文件 UTF-8 无 BOM
- [ ] 所有 commit Conventional Commits

## Out of Scope

- OS 级沙箱(seccomp/nsjail/container) — 仍是 R3,文档化为已知限制
- Prompt injection 检测 — 文档化,不解决
- Web UI — 仅 CLI + TUI
- 非 OpenAI 兼容 LLM 后端

## Open Questions

- [UNCLEAR: symlink 检测在 Windows reparse point 上是否需要 _winapi 调用?] — 用 `os.path.islink` + `Path.resolve` 先做 Unix-style,Windows reparse point 单独测
- [UNCLEAR: pip install 阻断是否应支持 allowlist 模式而非单 env 开关?] — 先用单 env 开关,allowlist 留下一轮

## Assumptions

- 用户在 git 仓库内运行(否则 worktree 验证不可用,文档化)
- `benchmarks/tasks.yaml` 是可选的,默认不配置 → Gate 2 关闭
- 旧 memory DB 用户会自然升级到新代码,schema 迁移在首次连接时跑

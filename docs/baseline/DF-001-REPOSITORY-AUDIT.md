# DF-001：zentao-dev-workflow 现有仓库、发行版本与 DevFlow AI 基线盘点

- 报告版本：v1.0
- 盘点日期：2026-11-20
- 状态：已完成

---

## 一、盘点摘要

| 维度 | 结论 |
|------|------|
| 开发目录 | `D:\WorkSpace\task-workflow`，独立 Git 仓库，main 分支，无 remote |
| 发行目录 | `D:\WorkSpace\lianzhu-skills\zentao-dev-workflow`，位于 lianzhu-skills 父仓库内，master 分支，有阿里云 remote |
| 代码分叉 | **存在**。两个目录自 2026-11-19 之后已产生实质性分歧 |
| 运行版本 | 全局安装的 Skill SKILL.md 为**第三版本**（既非 dev 也非 dist） |
| 稳定基线 | 两个 HEAD 均不适合直接作为基线。核心 `agent-manager/` 代码相同，但脚本和策略存在冲突 |

---

## 二、开发目录（task-workflow）

### 2.1 Git 状态

| 项目 | 值 |
|------|-----|
| Git 根 | `D:\WorkSpace\task-workflow` |
| Remote | **无**（独立仓库，仅本地） |
| 当前分支 | `main` |
| HEAD commit | `e792ce3` — "chore: gitignore 未完成的 zentao 模块" |
| HEAD 日期 | 2026-11-20 02:19:44 +0800 |
| 工作区状态 | **干净** |
| Stash | 1 条（WIP: human-decision-policy + recovery-protocol 补充） |
| Tags | 无 |

### 2.2 最近 10 条提交

```
e792ce3 chore: gitignore 未完成的 zentao 模块
5b2aff5 chore: 补齐 notify_provider 运行时依赖 + 测试基础设施
09aef92 fix: 工作流可靠性修复——L3 强制路由 + WAITING_HUMAN + 进度回写 + 通知合并 + 终止门禁
7bd38f8 docs: 双目录版本盘点与开发发布机制设计报告
e434d29 fix: 用户身份解析不再依赖 OS 用户名，统一走 current_user_id() 解析链
9466584 fix: _config.py adapters 加载跳过 .example 文件
4c9c3ea chore: 清理测试代码与产物，补 .gitignore 本地敏感项
a7f63ee fix: Jenkins 触发支持无参数 Job（buildWithParameters 400 回退 /build）
9afe7ff feat: 发版流程编排 + 禅道任务回写（工时日志）
c415857 feat: dw-qa-engineer 升级为测试执行 Agent
```

### 2.3 核心模块结构

| 路径 | 功能 |
|------|------|
| `agent-manager/` | Agent 执行引擎（runner、state_manager、task_router、verifier、retry、jenkins_adapter、failure_detector、monitor、notifier、zentao_reporter、models、exceptions） |
| `agent-manager/human_decision/` | HITL 人工决策子系统（decision_manager、decision_store、decision_detector、reply_handler、reply_parser、notifier、notify_provider、hitl_config、recovery_handler、session_resumer、timeout_handler、mcp_server、daemon_supervisor、cli） |
| `scripts/` | 工作流脚本（_args、_config、_home、dispatch、audit、zt、project_register、project_resolver、repo_check、jenkins_run、release_run、migrate_runstate、install、notify、start-claude） |
| `config/` | 运行时配置（adapters/、projects/、users/、workflow.yaml） |
| `.claude/skills/zentao-dev-workflow/` | Claude Code Skill（SKILL.md + references/ + templates/） |
| `.claude/agents/` | 7 个 dw-* Agent 定义 |
| `zentao/_shared/client/` | 禅道 API 客户端（tasks.py） |
| `tests/` | 测试（5 测试文件，36 个收集到的测试） |
| `execution/` | 运行时执行记录 |
| `reports/` | 项目报告 |

### 2.4 工作区中仅存在于此目录的文件（dev-only）

**代码**：
- `zentao/_shared/client/tasks.py` — 禅道任务读取 API 封装
- `scripts/release_run.py` — 发版编排脚本
- `scripts/jenkins_run.py_a` — jenkins_run.py 的陈旧备份

**配置（运行时，含真实数据）**：
- `config/adapters/hitl.yaml`、`hitl.local.yaml`
- `config/adapters/jenkins.yaml`、`notification.yaml`、`zentao.yaml`
- `config/projects/ai-platfrom.yaml`、`demo-react.yaml`、`jeebootx.yaml`
- `config/users/xuhongzu.yaml`

**Skill**：
- `.claude/skills/zentao-dev-workflow/references/release-process.md` — 发版流程参考
- `.claude/skills/workflow-register-project/SKILL.md` — 手动注册 Skill

**运行时数据**：
- `execution/` — 完整执行记录（3413、3415、3424、3428、3430、3431、3434、ai-platfrom、jeebootx）
- `agent-manager/logs/` — agent_manager.db + 备份 + HITL daemon 日志
- `reports/` — 各类验收和审计报告
- `.zentao_token.json` — 禅道凭据

**其他**：
- `Jenkinsfile.ai-test` — Jenkins pipeline 定义
- `.mcp.json` — MCP 配置
- `CLAUDE.md`、`Readme.md`

---

## 三、发行目录（lianzhu-skills/zentao-dev-workflow）

### 3.1 Git 状态

| 项目 | 值 |
|------|-----|
| Git 根 | `D:\WorkSpace\lianzhu-skills`（父仓库） |
| Remote | `https://codeup.aliyun.com/63bf8b4646bd5686f049dd2f/ai/lianzhu-skills.git` |
| 当前分支 | `master` |
| HEAD commit | `30cb0c4` — "fix: 工作流可靠性修复——L3 强制路由 + WAITING_HUMAN + 进度回写 + 通知合并 + 终止门禁" |
| HEAD 日期 | 2026-11-20 02:05:03 +0800 |
| 工作区状态 | **干净** |
| Stash | 无 |
| Tags | 无 |

### 3.2 最近 10 条提交

```
30cb0c4 fix: 工作流可靠性修复——L3 强制路由 + WAITING_HUMAN + 进度回写 + 通知合并 + 终止门禁
c7536fd fix: SKILL.md --user 参数改为可选，禁止 $(whoami) 模式
48ee415 refactor: workhour 切换为 v2 REST 端点，清理 Web 表单登录逻辑
9f745b4 chore: 清理代码中的开发决策日期标注与冗余授权注释
4ac2a62 feat: Phase 0.5 前置缺陷修复与稳定性加固
b79b819 feat: 新增 /workflow-register-project 命令与任务驱动注册编排
735def4 refactor: P0 独立运行与安装安全性改造（install.sh SRC 自定位、去硬编码、防呆护栏）
d6c3144 feat: install-global.sh 支持 --only 单独安装 + 本地配置保护
a7b8b08 feat: 新增 zentao-dev-workflow 通用多 Agent 研发工作流 skill
06e8f0d chore: 添加 sync_skills.ps1 到 .gitignore
```

### 3.3 核心模块结构

发行目录结构为**安装就绪**格式：

| 路径 | 功能 |
|------|------|
| `agent-manager/` | 同开发目录（代码相同） |
| `agent-manager/human_decision/` | 同开发目录（代码相同） |
| `scripts/` | 工作流脚本（多了 `register_from_task.py`，少了 `release_run.py`、`jenkins_run.py_a`） |
| `config/` | **仅模板**（无运行时真实配置） |
| `skill/` | Claude Code Skill（对应 dev 的 `.claude/skills/zentao-dev-workflow/`） |
| `agents/` | 7 个 dw-* Agent 定义（与 dev 相同） |
| `tests/` | 测试（**15 测试文件，169 个收集到的测试**） |
| `docs/architecture/` | 架构设计文档（10 个文件） |

### 3.4 仅存在于此目录的文件（dist-only）

**代码**：
- `scripts/register_from_task.py` — 任务驱动项目注册桥接（dev 没有）

**架构文档（完整的 DevFlow AI 架构设计）**：
- `docs/architecture/ACCEPTANCE-CRITERIA.md`
- `docs/architecture/AGENT-ADAPTERS.md`
- `docs/architecture/CONFIGURATION-DESIGN.md`
- `docs/architecture/CURRENT-ARCHITECTURE.md`
- `docs/architecture/DATABASE-MIGRATION.md`
- `docs/architecture/MIGRATION-PLAN.md`
- `docs/architecture/MODULE-MAPPING.md`
- `docs/architecture/PROVIDER-INTERFACES.md`
- `docs/architecture/TARGET-ARCHITECTURE.md`

**测试**（9 个 dev 没有的测试文件）：
- `tests/test_config_root.py`
- `tests/test_dispatch_state.py`
- `tests/test_invocation.py`
- `tests/test_project_register.py`
- `tests/test_register_from_task.py`
- `tests/test_registry_integration.py`
- `tests/test_repo_check.py`
- `tests/test_workflow_home.py`
- `tests/test_zt_project.py`

**报告**：
- `reports/PHASE-0.5-IMPLEMENTATION-REPORT.md`

### 3.5 发行目录缺少的文件

| 缺失文件 | 类型 | 影响 |
|----------|------|------|
| `zentao/` 模块 | 代码 | 禅道 API 客户端未打包到发行版 |
| `scripts/release_run.py` | 代码 | 发版编排脚本未打包 |
| `skill/references/release-process.md` | Skill 参考 | 发版流程 Skill 指令未打包 |
| `.claude/skills/workflow-register-project/` | Skill | 手动注册 Skill 未打包 |
| `Jenkinsfile.ai-test` | CI | Jenkins pipeline 未打包 |
| `config/adapters/*.yaml`（运行时） | 配置 | 发行版仅有 .example 模板 |

---

## 四、两个目录差异分析

### 4.1 agent-manager 核心代码：完全相同

所有 `agent-manager/*.py` 和 `agent-manager/human_decision/*.py` 文件的 **内容完全相同**。仅 `hitl_config.py`、`reply_handler.py`、`migrate_runstate.py` 存在 CRLF/LF 行尾差异（dev 为 CRLF，dist 为 LF），内容字节一致。

### 4.2 Agent 定义：完全相同

7 个 `dw-*.md` Agent 文件在所有三个位置（dev、dist、全局安装）的 MD5 完全一致。

### 4.3 脚本差异（有实质内容分歧）

| 文件 | 差异类型 | 详情 |
|------|----------|------|
| `project_register.py` | **D（功能实现不同）** | dist 多了 `detect_git_info` 自动收集、baseline_branch 自动检测、repo spec 解析、repo 状态复核；dev 多了 `current_user_id` 自动用户解析 |
| `project_resolver.py` | **A（dev 有，dist 无的部分功能）** | dev 新增了 `_resolve_user_id` 返回 `(id, source)` 元组、`_user_id_source()` 和 `whoami` 子命令 |
| `repo_check.py` | **B（dist 有，dev 无）** | dist 新增 `detect_git_info()` 函数，用于只读识别 Git remote/branch |
| `jenkins_run.py` | **A（dev 有，dist 无）** | dev 新增 4 行：无参数 Jenkins Job 的 `/build` 回退逻辑 |
| `register_from_task.py` | **B（dist 有，dev 无）** | 任务驱动注册桥接脚本（265 行），dev 完全没有 |
| `release_run.py` | **A（dev 有，dist 无）** | 发版编排脚本（279 行），dist 没有 |
| `_args.py`、`_config.py`、`_home.py`、`audit.py`、`dispatch.py`、`zt.py` | **无差异** | MD5 完全一致 |

### 4.4 Skill SKILL.md：三方均有差异

| 位置 | 行数 | 关键差异 |
|------|------|----------|
| dev | 163 行 | 最短版本。未注册项目 → 提示 `/workflow-register-project`；`task_close` 始终禁止自动 |
| dist | 245 行 | 最长版本。含完整 `WAITING_PROJECT_REGISTRATION` 就地注册流程；`task_close` 条件满足后自动执行 |
| 全局安装 | 232 行 | 介于两者之间。含注册流程但内容与 dist 不完全一致。**来源不明** |

### 4.5 Skill 参考文件差异

| 参考文件 | dev vs dist | dev vs 全局 |
|-----------|-------------|-------------|
| `agent-dispatch.md` | 路径措辞不同（`.claude/agents/` vs `agents/`） | 不同 |
| `approval-policy.md` | `task_close` 策略分歧（禁止 vs 条件自动） | 不同 |
| `continuation-protocol.md` | **相同** | **相同** |
| `human-decision-policy.md` | **相同** | 不同 |
| `project-adaptation.md` | Agent 目录路径措辞不同 | 不同 |
| `recovery-protocol.md` | dist 多了"注册未完成裁决"步骤 | 不同 |
| `workflow-lifecycle.md` | `task_finish/task_close` 授权分歧 | 不同 |
| `release-process.md` | **仅 dev 有** | 全局无 |

### 4.6 差异分类汇总

| 类型 | 数量 | 说明 |
|------|------|------|
| A（dev 有、dist 无） | ~15 | zentao 模块、release_run.py、Jenkins 回退逻辑、whoami CLI、release-process.md、运行时配置/数据 |
| B（dist 有、dev 无） | ~14 | register_from_task.py、detect_git_info、架构文档、9 个额外测试 |
| C（内容不同） | ~8 | SKILL.md、project_register.py、project_resolver.py、多个 Skill references |
| D（功能实现不同） | 2 | project_register.py（用户解析 vs git 探测）、task_close 策略 |
| E（配置/数据差异） | 大量 | dev 有运行时真实配置和 execution 数据，dist 仅有模板 |
| F（CRLF/LF） | 3 | hitl_config.py、reply_handler.py、migrate_runstate.py — 仅行尾差异 |

### 4.7 Stash 中的未提交修改

dev 仓库有一个 stash（WIP on main: `9466584`），包含：
1. `human-decision-policy.md` — 新增"决策授权→宿主权限桥接"和"长会话 HITL 工具使用注意"两段
2. `recovery-protocol.md` — 新增"外部副作用防重复执行"段

这些是 WIP 修改，尚未提交到 dev 仓库，也未在 dist 中出现。

---

## 五、实际运行版本确认

### 5.1 工作流根解析链

```
WORKFLOW_HOME 环境变量 →（空）
用户级配置 ~/.claude/zentao-dev-workflow.json →（不存在）
安装记录 ~/.claude/zentao-dev-workflow.home → D:/WorkSpace/task-workflow ✅
```

当前运行环境使用的工作流根：**`D:\WorkSpace\task-workflow`**（开发目录）

### 5.2 全局 Skill 安装

全局 Skill 安装于 `~/.claude/skills/zentao-dev-workflow/`，安装日期 2026-11-19 17:43：

| 组件 | 匹配来源 |
|------|----------|
| SKILL.md | **独立版本**（既非 dev 也非 dist，MD5: 61d3ec0e） |
| Agent 文件 | 与 dev 和 dist 完全相同 |
| 参考文件 | **独立版本**（大部分与两者均不同） |
| 模板文件 | 与 dev 和 dist 完全相同 |

### 5.3 版本不一致证据

1. **SKILL.md 三方法不同**：全局安装的 SKILL.md（MD5: 61d3ec0e）与 dev（bad03a08）和 dist（cefbb5ac）均不同。
2. **参考文件不一致**：7 个参考文件中仅 `continuation-protocol.md` 与 dev 相同，其余 6 个全部不同。
3. **Agent 一致**：Agent 文件在三方完全相同。
4. **模板一致**：模板文件在三方完全相同。

### 5.4 结论

当前 `/zentao-dev-workflow` 实际加载的是 `~/.claude/skills/zentao-dev-workflow/SKILL.md`，其内容为**一个独立版本**：
- 具备 WAITING_PROJECT_REGISTRATION 注册流程（与 dist 一致）
- 但内容与 dist 的 SKILL.md 不完全相同
- 不包含 dev 独有的 release-process.md 参考文件
- 参考文件内容与 dev 和 dist 均有所不同

**推测**：全局 Skill 安装来自 dist 目录的某个中间版本（2026-11-19 17:43 安装），此后 dist 的 SKILL.md 和 references 又发生了变更（dist 最新 commit 为 2026-11-20 02:05）。

---

## 六、DevFlow AI 新仓库状态

| 项目 | 值 |
|------|-----|
| 路径 | `D:\WorkSpace\devflow-ai` |
| Git 初始化 | ✅ 已初始化 |
| 当前分支 | `master` |
| HEAD commit | `1ed1577` — "docs: 添加 DevFlow AI 架构文档与研发计划" |
| Remote | `https://github.com/xhz9838-droid/devflow-ai.git` ✅ |
| 工作区状态 | **干净** |
| 已有内容 | `../plan_v1.md`、`../架构_v1.md`、`docs/baseline/`（本次盘点） |
| README | ❌ 无 |
| LICENSE | ❌ 无 |
| .gitignore | ❌ 无 |

**状态评估**：仓库就绪，远程已配置，可以开始接收代码。

---

## 七、核心模块可复用性评估

### 7.1 通用研发自动化能力（可直接复用）

| 模块 | 位置 | 成熟度 | 依赖 | 测试覆盖 | 复用建议 |
|------|------|--------|------|----------|----------|
| **state_manager** | `agent-manager/state_manager.py` | 高 | SQLite（内置） | 间接覆盖 | **直接复用**。SQLite 状态持久化、Schema 迁移、CRUD |
| **models** | `agent-manager/models.py` | 高 | dataclasses | 间接覆盖 | **直接复用**。Agent、Task、Node 数据模型 |
| **task_router** | `agent-manager/task_router.py` | 高 | models, state_manager | 间接覆盖 | **直接复用**。任务路由与状态机 |
| **runner** | `agent-manager/runner.py` | 高 | models, state_manager | 间接覆盖 | **接口化后复用**。Agent 启动逻辑需抽象 Provider 接口 |
| **retry** | `agent-manager/retry.py` | 中 | models, state_manager | 间接覆盖 | **直接复用**。失败重试与指数回退 |
| **failure_detector** | `agent-manager/failure_detector.py` | 中 | models | 间接覆盖 | **直接复用**。Agent 失败检测 |
| **verifier** | `agent-manager/verifier.py` | 中 | models, state_manager | 间接覆盖 | **直接复用**。任务验收门禁 |
| **exceptions** | `agent-manager/exceptions.py` | 高 | 无 | 间接覆盖 | **直接复用** |

### 7.2 HITL 人工决策（可复用，需适配）

| 模块 | 位置 | 成熟度 | 依赖 | 复用建议 |
|------|------|--------|------|----------|
| **decision_manager** | `agent-manager/human_decision/decision_manager.py` | 高 | decision_store, models | **直接复用**。决策创建、查询、生命周期 |
| **decision_store** | `agent-manager/human_decision/decision_store.py` | 高 | SQLite | **直接复用**。决策持久化 |
| **decision_detector** | `agent-manager/human_decision/decision_detector.py` | 高 | 无 | **直接复用**。代码中 HITL 标记识别 |
| **reply_handler** | `agent-manager/human_decision/reply_handler.py` | 高 | models, hitl_config | **接口化后复用**。回复来源（hook/watch/cli）需抽象 |
| **reply_parser** | `agent-manager/human_decision/reply_parser.py` | 中 | 无 | **直接复用**。DEC-ID 解析 |
| **mcp_server** | `agent-manager/human_decision/mcp_server.py` | 中 | decision_manager | **接口化后复用**。MCP 协议绑定需抽象 |
| **daemon_supervisor** | `agent-manager/human_decision/daemon_supervisor.py` | 中 | hitl_config | **直接复用**。后台 daemon 管理 |
| **notify_provider** | `agent-manager/human_decision/notify_provider.py` | 中 | hitl_config | **接口化后复用**。钉钉通知 → 通用通知接口 |
| **notifier** | `agent-manager/human_decision/notifier.py` | 中 | notify_provider, hitl_config | **接口化后复用**。同上 |
| **session_resumer** | `agent-manager/human_decision/session_resumer.py` | 中 | decision_store | **直接复用**。决策回复后恢复执行 |
| **recovery_handler** | `agent-manager/human_decision/recovery_handler.py` | 中 | decision_store, state_manager | **直接复用**。检查点恢复 |
| **timeout_handler** | `agent-manager/human_decision/timeout_handler.py` | 中 | hitl_config | **直接复用**。决策超时处理 |
| **hitl_config** | `agent-manager/human_decision/hitl_config.py` | 高 | yaml, env | **重构后复用**。cc-connect 管理 token 读取为专有逻辑 |

### 7.3 禅道专属能力（需隔离）

| 模块 | 位置 | 复用建议 |
|------|------|----------|
| **zentao_reporter** | `agent-manager/zentao_reporter.py` | **接口化后复用**。定义 Reporter 接口，禅道为实现之一 |
| `zentao/_shared/client/tasks.py` | dev only | **接口化后复用**。提取 TaskProvider 接口 |
| `scripts/zt.py` | 脚本 | **保留为 Provider 实现** |
| `config/adapters/zentao.yaml` | 配置 | **保留为 Provider 配置** |

### 7.4 钉钉专属能力（需隔离）

| 模块 | 复用建议 |
|------|----------|
| `notify_provider.py` | **接口化后复用**。定义 Notifier 接口，钉钉为实现之一 |
| `config/adapters/notification.yaml` | **保留为 Provider 配置** |

### 7.5 Jenkins 专属能力（需隔离）

| 模块 | 复用建议 |
|------|----------|
| `jenkins_adapter.py` | **接口化后复用**。定义 CIAdapter 接口，Jenkins 为实现之一 |
| `scripts/jenkins_run.py` | **保留为 Provider 实现** |
| `config/adapters/jenkins.yaml` | **保留为 Provider 配置** |

### 7.6 Claude Code 专属能力（需抽象）

| 模块 | 复用建议 |
|------|----------|
| `.claude/skills/` | Skill 内容转为 Workflow Engine 配置，不再依赖 Claude Code Skill 机制 |
| `.claude/agents/` | Agent 定义转为 Agent Adapter 配置 |
| `scripts/start-claude.sh` | 抽象为 Agent Adapter 启动接口 |
| Claude Code MCP Server | 抽象为 Agent Adapter 协议 |

### 7.7 DAG Scheduler

当前**没有独立的 DAG Scheduler 模块**。DAG 调度逻辑分散在：
- `scripts/dispatch.py` — 派发入口
- `agent-manager/task_router.py` — 任务路由
- `skill/SKILL.md` — 大量调度规则仍以自然语言在 Skill 提示词中

**结论**：DAG Scheduler 是 DevFlow AI Phase 1 需要**新建**的核心模块。现有 logic 可作为参考。

### 7.8 复用优先级建议

| 优先级 | 模块 | 复用方式 |
|--------|------|----------|
| P0 | state_manager, models, exceptions | 直接复用（无外部依赖） |
| P0 | failure_detector, retry, verifier | 直接复用 |
| P0 | decision_manager, decision_store, decision_detector, reply_parser | 直接复用 |
| P1 | runner, task_router | 接口化后复用（Agent 启动需抽象） |
| P1 | notify_provider, notifier | 接口化后复用（通知渠道抽象） |
| P1 | zentao_reporter, zt.py, zentao client | 接口化后复用（Reporter 接口） |
| P1 | jenkins_adapter, jenkins_run.py | 接口化后复用（CIAdapter 接口） |
| P2 | mcp_server, reply_handler | 接口化后复用（协议层抽象） |
| P2 | hitl_config | 重构后复用（cc-connect 依赖解耦） |
| P2 | Skill SKILL.md, references | 提取为 Workflow Core 配置 |

---

## 八、稳定基线建议

### 8.1 候选 commit 分析

两个仓库的 HEAD 均不适合直接作为基线：

| 候选 | Commit | 问题 |
|------|--------|------|
| dev HEAD | `e792ce3` (main) | 缺少 register_from_task.py、detect_git_info、架构文档、大量测试 |
| dist HEAD | `30cb0c4` (master) | 缺少 zentao 模块、release_run.py、release-process.md、Jenkinsfile；位于父仓库内 |

### 8.2 阻塞原因

1. **两个目录已分叉**，任何单一 HEAD 都会丢失另一方的重要代码。
2. **全局安装的 Skill 版本与两个仓库均不一致**，无法确定哪个版本是"稳定运行"版本。
3. **Dev stash 中有未提交的 WIP**（human-decision-policy + recovery-protocol 补充），需确认是否应纳入基线。
4. **Dev 有运行时数据和凭据**，需要清理后才能作为基线。

### 8.3 建议基线方案

**合并基线**：以 dev 仓库 `e792ce3`（main）为基础，merge dist 的以下内容：

**从 dist 合并到 dev（必须）**：
1. `scripts/register_from_task.py` — 任务驱动注册
2. `scripts/repo_check.py` 的 `detect_git_info()` — Git 信息自动收集
3. `docs/architecture/` — 完整架构设计文档（10 个文件）
4. `tests/test_*.py`（9 个额外测试）— 测试覆盖补充
5. `reports/PHASE-0.5-IMPLEMENTATION-REPORT.md`

**需要人工决策的冲突**：
1. `project_register.py` — 双方有不同功能增强，需合并
2. `project_resolver.py` — dev 有 whoami，dist 没有；需决定保留
3. SKILL.md 和 references — 策略分歧（task_close 自动 vs 禁止）；需明确策略

**合并前需处理**：
1. 清理 dev 的运行时数据（execution/、logs/、reports/、.zentao_token.json）
2. 确认 stash 中的 WIP 是否应纳入
3. 统一 task_close 策略

### 8.4 建议基线 commit

**建议**：以 dev `e792ce3` 为基础，merge dist 关键代码后，作为 DevFlow AI 的初始基线 commit。

理由：
- dev 有完整的运行时上下文和 zentao 模块
- dist 的补充代码可以 cherry-pick
- dev 是实际运行环境使用的工作流根

---

## 九、主要风险

| 风险 | 严重程度 | 说明 |
|------|----------|------|
| **Skill 版本三方不一致** | HIGH | 全局安装的 SKILL.md 与 dev 和 dist 均不同，无法确定哪个是"权威版本" |
| **task_close 策略分歧** | HIGH | dev "禁止自动 close" vs dist "条件满足后自动 close" — 策略冲突影响工作流语义 |
| **Dev stash 未提交** | MEDIUM | WIP 修改（权限桥接、副作用防重复）未合并到任一分支 |
| **Dev 仓库无 remote** | MEDIUM | 开发目录未关联远程仓库，代码仅本地存在 |
| **发行目录无运行时数据** | LOW | 发行目录缺少 execution/ 历史，但不影响代码基线 |
| **发行目录缺少 zentao 模块** | MEDIUM | 禅道 API 客户端未打包到发行版 |
| **CRLF/LF 不一致** | LOW | 3 个文件行尾不同，不影响功能 |

---

## 十、后续实施建议

### 10.1 DF-001 完成后应立即处理

1. **统一 Skill 版本**：将 dist 的最新 SKILL.md 安装到全局，消除版本不一致。
2. **合并 dev stash**：确认 stash 中的 WIP 是否应提交到 main。
3. **决策 task_close 策略**：明确 DevFlow AI 采用"禁止自动"还是"条件自动"。
4. **为 dev 仓库添加 remote**：防止本地唯一副本丢失。

### 10.2 DF-002（Phase 1: Workflow Core）前置条件

- [x] 旧版本盘点完成（DF-001）
- [ ] 稳定基线 commit 确定
- [ ] task_close 策略明确
- [ ] 核心模块复用方案确认

### 10.3 DF-003（DevFlow AI 初始代码基线建立）前置条件

- [ ] 合并基线代码准备就绪
- [ ] 运行时数据已清理
- [ ] CRLF/LF 已统一
- [ ] Dev 仓库远程已配置

### 10.4 DF-004（Agent Adapter + Provider）前置条件

- [ ] DF-002 Workflow Core 完成
- [ ] Reporter/Notifier/CIAdapter 接口定义完成
- [ ] 禅道/钉钉/Jenkins Provider 实现方案确认

### 10.5 DF-006（DevFlow AI 平台化）前置条件

- [ ] DF-002、DF-003、DF-004 全部完成
- [ ] 至少接入一个非 Claude Code 的 Agent

---

## 十一、待确认事项

以下事项需要人工确认，未确认前不应进入下一步：

1. **全局安装的 SKILL.md 来源**：2026-11-19 17:43 安装时的 dist 版本是哪个 commit？为什么与当前 dev 和 dist 均不同？
2. **task_close 策略**：DevFlow AI 采用"禁止自动 close"还是"条件满足后自动 close"？
3. **Stash WIP**：dev stash 中的"决策授权→宿主权限桥接"和"副作用防重复"修改是否应纳入基线？
4. **Dev 仓库 remote**：是否需要为 task-workflow 添加远程仓库？
5. **禅道模块归属**：`zentao/_shared/client/tasks.py` 是否应归入 DevFlow AI 的 Provider 层？
6. **release_run.py 归属**：发版编排逻辑是否应纳入 DevFlow AI？

---

*报告生成于 2026-11-20。所有 Git 信息和文件哈希基于盘点时快照。*
# DevFlow AI 总体研发计划与禅道任务拆分

- 文档版本：v1.0
- 产品名称：DevFlow AI
- 研发方式：独立基线、独立仓库、独立发行版、独立运行环境
- 现有产品：zentao-dev-workflow
- 研发目标：构建面向多种 AI Coding Agent 的轻量级软件研发自动化引擎
- 计划原则：先核心闭环，再扩展 Agent 和 Provider，最后建设管理界面

# 一、项目目标与产品定位

## 1.1 产品定位

DevFlow AI 是一个面向 AI Coding Agent 的轻量级软件研发自动化引擎。

通过可插拔架构统一管理：

- 研发需求接入
- 需求分析和技术方案
- 任务拆解与 DAG 调度
- AI Agent 执行
- 多 Agent 协作
- 代码质量验证
- 人工决策（HITL）
- 任务状态回写
- 异常重试与恢复
- 持续集成和部署
- 执行过程记录与通知

DevFlow AI 不应该只是一个 Claude Code Skill，也不应该依赖某一种 AI Coding Agent。

它的核心是独立的 Workflow Engine。Claude Code、Codex、OpenCode 等属于可接入的 Agent 执行端。

## 1.2 产品目标

第一阶段先保证一个完整的软件研发自动化闭环：

需求接入 → 需求分析 → 技术方案 → 任务拆解 → Agent 执行 → 代码验证 → 人工决策 → 进度回写 → 完成验收 → 任务完成。

要求：

1. 任务可以持续执行，不因终端关闭或 Agent 意外退出而丢失状态。
2. 任务执行状态由持久化状态管理器维护。
3. Agent 执行失败可以识别、重试、记录或通知。
4. 遇到需要人工决策的事项，通过钉钉通知，用户决策后可以恢复原工作流。
5. 任务进度自动回写禅道。
6. 任务完成必须经过 Verifier 验收。
7. Jenkins 真实构建和部署在规划阶段确定是否需要，实际触发前遵循人工确认机制。
8. 核心引擎不依赖 Claude Code 专属 Skill 才能工作。
9. 支持逐步接入其他 AI Coding Agent。
10. 新版本研发不影响现有 zentao-dev-workflow。

## 1.3 非目标

第一阶段不做：

- 重量级 Web 管理平台。
- Kubernetes 微服务化部署。
- Kafka、RabbitMQ 等分布式消息基础设施。
- 一开始同时接入所有 Agent。
- 一开始实现复杂企业级权限系统。
- 为了架构统一而大规模重写现有代码。
- 过早实现多租户和复杂组织管理。
- 过早实现复杂的可视化流程设计器。

# 二、现有版本与新版本的关系

## 2.1 两个产品独立维护

现有产品：

zentao-dev-workflow

新产品：

DevFlow AI

两者在 DevFlow AI 正式替换旧版本之前，必须保持独立。

| 项目     | zentao-dev-workflow                             | DevFlow AI                             |
| -------- | ----------------------------------------------- | -------------------------------------- |
| 产品定位 | 现有禅道研发工作流                              | 通用 AI 研发自动化引擎                 |
| 开发目录 | D:\WorkSpace\task-workflow                      | D:\WorkSpace\devflow-ai                |
| 发行目录 | D:\WorkSpace\lianzhu-skills\zentao-dev-workflow | D:\WorkSpace\lianzhu-skills\devflow-ai |
| Git 仓库 | 独立维护                                        | 独立维护                               |
| 版本管理 | 独立版本号和 Tag                                | 独立版本号和 Tag                       |
| 安装目录 | 旧版命名空间                                    | 新版命名空间                           |
| 配置     | 独立                                            | 独立                                   |
| SQLite   | 独立                                            | 独立                                   |
| 执行记录 | 独立                                            | 独立                                   |
| 发布     | 继续维护                                        | 独立发布                               |

## 2.2 旧版本维护策略

旧版本不停止维护。

主要工作：

- 修复影响现有任务执行的 Bug。
- 维护当前 Claude Code Skill 工作流。
- 保证禅道、钉钉、HITL 和 Agent 执行链路可用。
- 保留现有用户配置和执行历史。
- 必要时发布旧版本补丁。

旧版本的功能开发以稳定性修复为主，不继续承担 DevFlow AI 的新架构试验。

## 2.3 新版本基线

DevFlow AI 从经过验证的 zentao-dev-workflow 稳定 Git commit 建立初始基线。

要求：

1. 先确认旧版本稳定 commit。
2. 保留原始 Git 历史，建立可追溯的独立仓库。
3. 建立 DevFlow AI 自己的版本号和发布标签。
4. 不与旧版本共用工作区。
5. 不自动双向同步代码。
6. 旧版本 Bug 修复经过判断后，可以 cherry-pick 到新版本。
7. 新版本架构修改不反向覆盖旧版本。

基线建立后，两个版本独立演进。

# 三、源码、开发包、发行版与运行目录

这是 DevFlow AI 必须先明确的工程管理规范。

## 3.1 源码开发目录

Windows 开发目录：

D:\WorkSpace\devflow-ai

职责：

- 唯一源码开发位置。
- Git 代码管理。
- 架构设计和功能开发。
- 单元测试和集成测试。
- 文档维护。
- 版本提交和 Tag。
- 构建发布包。

禁止在发行目录直接进行日常功能开发。

## 3.2 发行版目录

Windows 发行目录：

D:\WorkSpace\lianzhu-skills\devflow-ai

职责：

- 保存经过测试的正式发行版本。
- 保存发行包、版本说明和必要的安装文件。
- 对外提供安装和升级来源。
- 记录发行版本对应的源码 commit。

发行目录不作为第二个开发工作区。

发布必须从指定 Git commit 或 Tag 构建，禁止通过两个目录之间的随意文件复制来同步版本。

## 3.3 开发包与发行包

区分三种内容：

### 源码

完整 Git 仓库，包含源代码、测试、开发文档、构建脚本。

### 开发包

开发和测试使用的构建产物。

要求：

- 可以从源码重复构建。
- 包含运行所需代码和依赖。
- 不包含真实凭据。
- 不包含开发人员本地运行数据。
- 不包含其他用户的 SQLite 数据库。

### 发行包

正式交付给用户安装的版本。

要求：

- 有明确版本号。
- 有对应源码 commit。
- 有发行说明。
- 有安装和升级说明。
- 有必要的版本迁移脚本。
- 有安装前检查和失败回滚能力。
- 不携带开发机真实配置和执行数据。

第一阶段优先采用简单可维护的 Python CLI/包结构，不为打包形式引入不必要的复杂度。

## 3.4 本地运行目录

源码、发行包、安装目录和运行数据必须分离。

推荐 Windows 运行目录：

| 类型         | 推荐路径                       | 用途                          |
| ------------ | ------------------------------ | ----------------------------- |
| 用户配置     | %APPDATA%\DevFlowAI\config     | 用户级配置和连接配置          |
| SQLite 状态  | %LOCALAPPDATA%\DevFlowAI\data  | 工作流、Agent 执行和事件状态  |
| 日志         | %LOCALAPPDATA%\DevFlowAI\logs  | 执行日志和诊断日志            |
| 缓存         | %LOCALAPPDATA%\DevFlowAI\cache | 临时缓存                      |
| 运行临时文件 | %LOCALAPPDATA%\DevFlowAI\tmp   | 临时文件                      |
| 项目工作区   | 用户指定的项目目录             | 实际代码仓库和 Agent 工作目录 |
| 发行程序     | 独立安装目录                   | CLI 和运行程序                |

如果仍然提供 Claude Code Skill，安装路径使用独立命名空间：

%USERPROFILE%.claude\skills\devflow-ai

不得覆盖：

%USERPROFILE%.claude\skills\zentao-dev-workflow

实际路径需要通过安装脚本和运行环境统一管理，不能散落硬编码。

## 3.5 macOS 兼容

架构阶段就应考虑 Windows/macOS 跨平台。

Windows 使用：

- APPDATA
- LOCALAPPDATA
- pathlib
- subprocess

macOS 使用：

- ~/Library/Application Support/DevFlowAI/config
- ~/Library/Application Support/DevFlowAI/data
- ~/Library/Logs/DevFlowAI

具体路径通过统一的 PathProvider 管理。

第一阶段不要求完成 macOS 安装适配，但不能继续增加 Windows 专属路径和 Shell 依赖。

## 3.6 运行数据隔离

以下内容禁止进入源码发布包：

- 真实 Token、Secret、密码。
- 用户个人配置。
- SQLite 数据库。
- execution 执行记录。
- Agent 会话和恢复上下文。
- 运行日志。
- 临时文件。

需要提供示例配置和初始化工具。

# 四、版本管理与发布机制

## 4.1 Git 管理

DevFlow AI 使用独立 Git 仓库。

建议版本策略：

- 开发阶段：0.x.x。
- 首个可试点版本：0.5.0。
- 首个正式稳定版本：1.0.0。
- Bug 修复：Patch。
- 兼容功能增加：Minor。
- 不兼容架构或接口变化：Major。

示例：

devflow-ai-v0.1.0
devflow-ai-v0.2.0
devflow-ai-v0.5.0
devflow-ai-v1.0.0

## 4.2 分支策略

初期不需要复杂 Git Flow。

建议：

- main：稳定可发布代码。
- feature/*：短期功能开发分支。
- fix/*：Bug 修复分支。

通过 PR 或本地审查合并到 main。

## 4.3 发布流程

开发源码
↓
单元测试
↓
集成测试
↓
端到端测试
↓
Git Commit
↓
创建 Release Tag
↓
从指定 Tag 构建发行包
↓
生成版本清单和校验信息
↓
更新发行目录
↓
备份当前运行版本
↓
安装或升级
↓
健康检查
↓
升级失败则回滚

## 4.4 发布记录

每个版本必须记录：

- 版本号。
- Git commit。
- 构建时间。
- 测试结果。
- 数据库 Schema 版本。
- 安装包信息。
- 变更说明。
- 升级注意事项。
- 回滚方式。

## 4.5 升级和回滚

升级前：

1. 备份当前配置。
2. 备份 SQLite 数据库。
3. 记录当前程序版本。
4. 检查数据库迁移兼容性。
5. 检查安装目标。

升级后：

1. 检查 CLI 能否正常启动。
2. 检查数据库迁移结果。
3. 检查核心 Provider 初始化。
4. 执行健康检查。
5. 记录升级结果。

失败时：

- 恢复上一个程序版本。
- 恢复兼容的配置和数据库备份。
- 保留升级失败日志。
- 不删除原始执行记录。

# 五、产品架构

## 5.1 总体架构

DevFlow AI 采用四层结构：

1. Agent Interfaces
2. Workflow Core
3. Provider Layer
4. Control Interface

### Agent Interfaces

统一不同 AI Coding Agent 的执行入口。

初期：

- Claude Code Adapter

后续：

- Codex Adapter
- OpenCode Adapter

Agent 只负责代码和研发任务执行，不负责整个工作流的权威状态。

### Workflow Core

核心模块：

- Workflow Manager
- Stage Manager
- DAG Scheduler
- Execution Manager
- State Manager
- Retry Manager
- Verifier
- Recovery Manager
- Human Decision Manager
- Event Manager
- Reporter
- Notifier

### Provider Layer

外部系统接入：

| Provider          | 作用                       |
| ----------------- | -------------------------- |
| ZenTao Provider   | 需求、任务、进度、完成状态 |
| DingTalk Provider | 消息通知和人工决策         |
| Jenkins Provider  | 构建和部署                 |
| Git Provider      | 分支、提交、PR、代码状态   |

Provider 使用统一接口，具体厂商实现独立。

### Control Interface

初期：

- CLI。
- Skill/Agent 调用入口。
- HTTP API（根据实际需要逐步增加）。

后续才考虑轻量 Web 控制中心。

## 5.2 推荐目录结构

以下为目标结构，不要求第一阶段一次性创建全部目录。

devflow-ai/
├── core/
│   ├── workflow/
│   ├── execution/
│   ├── scheduler/
│   ├── state/
│   ├── verifier/
│   ├── retry/
│   ├── recovery/
│   ├── events/
│   └── lifecycle/
├── adapters/
│   ├── base/
│   ├── claude_code/
│   ├── codex/
│   └── opencode/
├── providers/
│   ├── project_management/
│   │   └── zentao/
│   ├── notification/
│   │   └── dingtalk/
│   ├── cicd/
│   │   └── jenkins/
│   └── git/
├── human_decision/
├── cli/
├── api/
├── config/
├── schemas/
├── tests/
├── scripts/
├── docs/
├── reports/
└── pyproject.toml

现有 agent-manager、HITL、SQLite、DAG 等实现应优先评估复用，不应为了新目录而机械搬迁或重新实现。

## 5.3 Agent Adapter 规范

每种 Agent 通过统一 Adapter 接口接入。

需要抽象：

- Agent 启动。
- 参数传递。
- 工作目录。
- 环境变量。
- 执行状态。
- 输出读取。
- 退出状态。
- 超时和取消。
- 会话恢复能力。
- 人工审批能力。
- 工具调用能力。

不同 Agent 的能力不完全一致。

Adapter 应声明 capabilities，Workflow Core 根据实际能力决定调度方式，不能假定所有 Agent 都支持相同的功能。

# 六、核心工作流设计

## 6.1 统一生命周期

Workflow Core 统一负责：

任务接入
→ 任务初始化
→ 需求分析
→ 技术方案
→ DAG 任务拆解
→ 节点调度
→ Agent 执行
→ 结果持久化
→ 验收
→ 进度回写
→ 通知
→ 下一节点
→ 最终完成

生命周期不能只依赖 Skill 文档中的步骤说明。

关键状态流转必须由代码保证。

## 6.2 状态管理

SQLite 作为唯一权威执行状态存储。

需要保存：

- workflow。
- workflow_stages。
- workflow_nodes。
- agent_runs。
- task_locks。
- execution_events。
- human_decisions。
- notifications。
- retry_records。
- pending_writes。

具体表结构以现有 SQLite 实现盘点为准，禁止重复创建语义相同的状态系统。

状态至少区分：

- READY
- RUNNING
- WAITING_HUMAN
- RETRYING
- VERIFYING
- COMPLETED
- FAILED
- CANCELLED

WAITING_HUMAN 优先作为节点级状态。

某个节点等待人工决策时，不应无条件阻塞 DAG 中其他独立节点。

## 6.3 长任务执行与恢复

必须实现：

- 工作流状态持久化。
- 节点执行状态持久化。
- Agent 执行凭证记录。
- 异常退出检测。
- 超时检测。
- 重试。
- 恢复。
- 幂等执行。
- 事件记录。
- 检查点和恢复上下文。

终端关闭、Agent 进程异常退出或主控进程重启后，可以从 SQLite 恢复工作流。

不能依赖单一内存变量或 run-state.json 作为第二套权威状态。

## 6.4 HITL 人工决策

人工决策闭环：

Agent 发现需要人工决策
→ 创建 Decision
→ 持久化 Decision
→ 钉钉通知
→ 用户回复
→ 身份和决策结果校验
→ 决策入库
→ 更新节点状态
→ 恢复调度
→ 继续原工作流

要求：

- 决策记录持久化。
- 决策与工作流、节点绑定。
- 回调幂等。
- 重复回调不能重复执行。
- 通知成功不代表决策完成。
- 通知失败不能自动放行。
- 决策未完成时不能执行依赖该决策的节点。
- 不阻塞无依赖关系的独立节点。
- 决策后可以恢复原任务上下文。

钉钉是主要通知和人工决策渠道。

通知应区分：

NOTIFY_PENDING
NOTIFY_SENT
NOTIFY_FAILED
DECIDED

不能把消息发送成功当成人工批准。

## 6.5 失败处理和自动重试

失败分为：

- Agent 执行失败。
- 工具调用失败。
- 超时。
- 结果格式错误。
- Verifier 验收失败。
- Provider 调用失败。
- 数据库错误。
- 通知失败。
- 人工决策等待。
- 外部系统回写失败。

每种失败要记录 error_code、错误信息、attempt、时间和上下文。

重试策略：

- 可恢复错误按策略自动重试。
- 不可恢复错误标记失败并通知。
- 达到重试上限后通知。
- 人工决策等待不等于失败。
- 外部通知失败不能伪装成功。
- 回写失败进入 pending_writes，后续可补偿。

## 6.6 Verifier 验收

不能使用 Agent 退出码代替任务验收。

验收内容根据任务类型确定：

- 编译。
- 单元测试。
- 集成测试。
- 静态检查。
- 接口测试。
- 功能验证。
- 交付物检查。
- Git 状态检查。
- 业务验收。

只有 Verifier 通过，才能进入任务完成阶段。

## 6.7 禅道任务回写

ZenTao Provider 负责：

- 获取需求和任务。
- 获取任务详情。
- 创建或更新任务。
- 更新进度。
- 写入执行结果。
- 回写验收结果。
- 自动 finish/done。
- 满足关闭条件后自动 close。

用户已授权 AI Agent 自动执行 finish/done 和符合条件的 close，不需要重复询问。

完成规则：

- Agent 退出码 0 不等于任务完成。
- Verifier 通过后自动 finish/done。
- close 必须满足状态、验收和交付记录等关闭条件。
- 不删除任务、不删除项目、不清除历史记录。
- 回写失败必须记录并支持补偿。

## 6.8 Jenkins

Jenkins Provider 负责：

- 构建任务查询。
- 构建参数。
- 构建触发。
- 构建状态轮询。
- 构建结果记录。
- 部署状态查询。

规划阶段确定是否需要 Jenkins 构建和部署。

真实构建、部署属于必要人工决策事项，触发前遵循确认机制。

不得把 Jenkins 的真实执行授权混入一般代码修改授权。

# 七、开发优先级与阶段路线

## P0：必须优先实现的核心能力

P0 目标是完整研发闭环和执行可靠性。

包括：

- 独立源码仓库和基线。
- Workflow Core。
- SQLite 状态管理。
- DAG Scheduler。
- Claude Code Adapter。
- Agent Execution。
- Verifier。
- Retry。
- Recovery。
- HITL。
- 钉钉通知。
- ZenTao Provider。
- 阶段进度回写。
- 自动 finish/done。
- 条件满足后自动 close。
- 运行日志和执行事件。
- CLI 入口。
- 安装、升级、回滚基础机制。

## P1：扩展与工程化

- Codex Adapter。
- OpenCode Adapter。
- Jenkins Provider。
- Git Provider。
- 插件注册机制。
- Provider 配置管理。
- Agent capabilities。
- 工作流模板。
- 跨平台安装和运行。
- 更完整的诊断工具。
- API 接口。

## P2：后续增强

- 身份认证。
- 来源验证。
- HTTP Hook 鉴权。
- 轻量 Web 控制中心。
- 可视化 DAG。
- 多项目统一管理。
- 多用户和组织管理。
- 更复杂的权限与审计。
- 高级统计分析。

内网环境优先完成 P0 功能，不允许 P2 身份验证、来源验证和 HTTP Hook 鉴权阻塞核心功能交付。

# 八、里程碑规划

## M0：版本隔离与开发基线

目标：

- 保护现有 zentao-dev-workflow。
- 建立 DevFlow AI 独立源码和 Git 基线。
- 明确源码、开发包、发行包和运行目录。
- 建立版本管理和发布规范。

交付：

- 基线报告。
- 独立仓库。
- 目录规范。
- 发布规范。
- 隔离验证报告。

## M1：Workflow Core 核心独立化

目标：

将现有工作流核心能力从 Claude Code Skill 主控中逐步独立出来。

包括：

- Workflow 生命周期。
- State Manager。
- DAG Scheduler。
- Execution Manager。
- Verifier。
- Retry。
- Recovery。
- 统一 CLI。

交付：

- 可独立启动的 Workflow Core。
- SQLite 状态管理。
- DAG 执行。
- 失败重试和恢复。
- 核心测试。

## M2：Claude Code 端到端 Adapter

目标：

让 Claude Code 作为第一个 Agent Adapter 接入 Workflow Core。

完成：

- Agent 启动。
- 执行结果采集。
- 超时和失败处理。
- 执行上下文管理。
- 验收。
- 恢复。

交付：

- Claude Code Adapter。
- 完整研发任务端到端测试。

## M3：HITL、Provider 与可靠性闭环

目标：

让工作流可以无人值守地持续运行，必要时通过钉钉进行人工决策。

完成：

- Human Decision。
- DingTalk Provider。
- ZenTao Provider。
- 状态回写。
- Retry 和 Recovery。
- 通知和补偿机制。
- 终止条件检查。

交付：

- 完整人工决策闭环。
- 禅道任务自动回写。
- 异常恢复报告。

## M4：Agent 扩展与插件体系

目标：

让 DevFlow AI 不依赖 Claude Code。

完成：

- Codex Adapter。
- OpenCode Adapter。
- Agent capabilities。
- Provider 插件注册。
- 插件配置管理。

交付：

- 至少两个 Agent Adapter。
- 多 Provider 可插拔验证。

## M5：试点与正式发行

目标：

让 DevFlow AI 可以独立承担真实研发任务。

完成：

- 独立安装。
- 配置初始化。
- 数据迁移工具（确有需要时）。
- 升级和回滚。
- 端到端测试。
- 并行试点。
- 发布文档。

交付：

- DevFlow AI v1.0.0。
- 正式发行包。
- 安装和升级手册。
- 旧版本兼容与回滚报告。

## M6：轻量控制中心

目标：

根据实际使用需求提供统一管理入口。

可能包括：

- 工作流列表。
- 任务执行状态。
- Agent 执行记录。
- DAG 进度。
- 人工决策列表。
- 失败和重试记录。
- 日志查看。
- Provider 配置。

该阶段不阻塞核心引擎发布。

# 九、禅道任务拆分

以下任务可以直接创建到禅道。

建议新建项目：DevFlow AI。

任务标题统一使用 `[M0]`、`[M1]` 等里程碑前缀。

每个任务独立交付，任务之间通过依赖关系控制执行顺序。

## M0：版本隔离与开发基线

### DF-001：盘点现有 zentao-dev-workflow 仓库与发行版本

优先级：P0

任务内容：

- 盘点现有开发目录和发行目录。
- 确认 Git 仓库、分支、commit。
- 检查未提交修改。
- 检查实际全局运行版本。
- 盘点 Skill、Agent、agent-manager、HITL、SQLite、DAG、Provider。
- 识别可复用模块和历史重复实现。

交付物：

- 仓库盘点报告。
- 稳定版本候选 commit。
- 可复用模块清单。
- 风险清单。

验收标准：

- 能明确确认旧版本实际运行来源。
- 能确定 DevFlow AI 基线来源。
- 不覆盖任何未提交修改。
- 不修改旧版本代码。

### DF-002：建立 zentao-dev-workflow 唯一开发源与发行流程

优先级：P0

依赖：DF-001

任务内容：

- 固定 task-workflow 为唯一开发源。
- 明确发行目录只接收发布产物。
- 设计单向发布脚本。
- 排除真实配置、数据库、执行记录和日志。
- 建立版本标签和发布记录。

交付物：

- 开发规范。
- 发布规范。
- 发布脚本。
- 版本记录模板。

验收标准：

- 开发源和发行目录职责明确。
- 能从指定 commit 生成发行版本。
- 不会覆盖个人运行数据。
- 发行版本可追溯到源码 commit。

### DF-003：建立 DevFlow AI 独立 Git 基线

优先级：P0

依赖：DF-001

任务内容：

- 从确定的稳定 commit 建立 DevFlow AI 独立仓库。
- 保留 Git 历史。
- 建立 main 和基础开发分支规范。
- 建立独立版本号。
- 创建初始基线 Tag。
- 增加基础 README 和开发说明。

交付物：

- D:\WorkSpace\devflow-ai。
- 独立 Git 仓库。
- 初始 Tag。
- 基线说明。

验收标准：

- 与旧版本不共享工作区。
- 两个仓库可以独立提交。
- 可以追溯初始基线 commit。
- 新仓库修改不会影响旧仓库。

### DF-004：设计 DevFlow AI 目录、安装和运行数据隔离规范

优先级：P0

依赖：DF-003

任务内容：

- 定义源码目录。
- 定义发行目录。
- 定义开发包和发行包。
- 定义 Windows 运行目录。
- 定义 macOS 路径规范。
- 定义配置、SQLite、日志、缓存、执行数据路径。
- 定义 Claude Code Skill 安装命名空间。
- 建立路径管理模块。

交付物：

- 目录结构规范。
- PathProvider 设计。
- 配置和运行数据隔离方案。

验收标准：

- 源码和运行数据分离。
- 新版本不覆盖旧版本配置。
- 新版本不覆盖旧版本数据库。
- 不依赖硬编码 Windows 路径。
- 有清晰的环境初始化方式。

### DF-005：建立 DevFlow AI 构建、发行和版本管理机制

优先级：P0

依赖：DF-003、DF-004

任务内容：

- 建立 Python 项目构建规范。
- 建立开发包构建流程。
- 建立发行包构建流程。
- 建立版本号和 Git Tag 规则。
- 生成版本清单。
- 记录构建来源 commit。
- 建立发布前检查。

交付物：

- 构建脚本。
- 发行脚本。
- 版本清单。
- 发布说明模板。

验收标准：

- 可以从指定 Tag 构建发行包。
- 发行包不包含真实凭据和运行数据。
- 发行包能够追溯源码。
- 构建过程可重复。

## M1：Workflow Core 核心独立化

### DF-006：盘点并确定现有核心模块复用方案

优先级：P0

依赖：DF-003

任务内容：

重点盘点：

- agent-manager。
- State Manager。
- DAG Scheduler。
- Execution Manager。
- Verifier。
- Retry。
- HITL。
- SQLite。
- ZenTao Reporter。
- DingTalk Notifier。

明确：

- 直接复用。
- 接口化后复用。
- 需要重构。
- 需要重新实现。

交付物：

- 模块复用评估报告。
- 目标架构映射。
- 最小改造清单。

验收标准：

- 不重复实现已有功能。
- 不进行机械目录搬迁。
- 明确每个核心模块的职责和依赖。

### DF-007：建立 Workflow Core 统一入口与生命周期管理

优先级：P0

依赖：DF-006

任务内容：

- 建立 Workflow Manager。
- 定义工作流生命周期。
- 定义阶段和节点生命周期。
- 建立统一执行入口。
- 建立状态转换校验。
- 定义终止条件。

交付物：

- Workflow Core。
- 生命周期模型。
- CLI 基础入口。

验收标准：

- 能独立创建、启动和查询工作流。
- 生命周期由代码维护。
- 非法状态转换被拒绝。
- 工作流状态可以持久化。

### DF-008：统一 SQLite 状态管理和 Schema 迁移

优先级：P0

依赖：DF-007

任务内容：

- 统一权威数据库。
- 梳理现有表结构。
- 管理 Schema 版本。
- 建立幂等迁移。
- 建立事务和备份机制。
- 定义状态和事件模型。

交付物：

- State Manager。
- 数据库 Schema。
- Migration。
- 数据模型文档。

验收标准：

- SQLite 是唯一权威状态源。
- 不存在两套并行权威状态。
- 重复迁移不会破坏数据。
- 迁移失败能够安全退出并保留诊断信息。

### DF-009：统一 DAG Scheduler 和节点依赖调度

优先级：P0

依赖：DF-007、DF-008

任务内容：

- 节点依赖管理。
- 可执行节点识别。
- 并行节点调度。
- 阻塞节点处理。
- WAITING_HUMAN 支持。
- 节点状态持久化。
- 调度恢复。

交付物：

- DAG Scheduler。
- 节点状态模型。
- 调度测试。

验收标准：

- 独立节点可以并行推进。
- 人工等待节点不阻塞无依赖节点。
- 依赖节点未完成时不提前执行。
- 调度状态重启后可以恢复。

### DF-010：统一 Agent Execution Manager

优先级：P0

依赖：DF-007、DF-008

任务内容：

- Agent 执行生命周期。
- Agent Run 持久化。
- 执行超时。
- 进程退出检测。
- 执行日志采集。
- 执行锁。
- 幂等控制。
- 异常退出恢复。

交付物：

- Execution Manager。
- Agent Run 数据模型。
- 执行日志和事件记录。

验收标准：

- 可以记录完整执行过程。
- 进程异常退出后可以识别。
- 不会重复执行已经成功完成的节点。
- 可以定位每次执行的输入、输出和错误。

### DF-011：建立 Verifier 验收引擎

优先级：P0

依赖：DF-007、DF-010

任务内容：

- 统一验收接口。
- 支持命令检查。
- 支持测试检查。
- 支持交付物检查。
- 支持验收结果持久化。
- 支持失败原因分类。

交付物：

- Verifier。
- VerifyReport。
- 验收规则配置。

验收标准：

- Agent 退出码不直接决定任务完成。
- 验收失败可以阻止后续完成状态。
- 验收结果可追溯。
- 验收规则可以扩展。

### DF-012：建立 Retry 和 Recovery 机制

优先级：P0

依赖：DF-008、DF-009、DF-010、DF-011

任务内容：

- 失败分类。
- 自动重试策略。
- 最大重试次数。
- 重试记录。
- Checkpoint。
- 工作流恢复。
- 节点恢复。
- 失败终止策略。

交付物：

- Retry Manager。
- Recovery Manager。
- 恢复测试。

验收标准：

- 可恢复错误可以自动重试。
- 达到重试上限可以通知。
- 重启后可以恢复未完成工作流。
- 不重复执行已完成节点。
- 人工等待状态不被误判为失败。

### DF-013：建立统一终止条件和工作流收敛机制

优先级：P0

依赖：DF-009、DF-011、DF-012

任务内容：

建立统一 can_terminate(workflow_id) 检查。

检查：

- 是否存在可执行节点。
- 是否存在运行中的 Agent。
- 是否存在待人工决策。
- 是否存在待回写事项。
- 是否存在待重试事项。
- 是否存在未完成验收。
- 是否存在未处理的失败节点。

交付物：

- TerminationChecker。
- TerminationReport。
- 终止条件测试。

验收标准：

- 未完成工作流不能错误终止。
- WAITING_HUMAN 不会被误判为 COMPLETED。
- 存在可执行独立节点时不能提前结束。
- 完成条件可以由代码统一判定。

## M2：Claude Code Adapter 和端到端执行

### DF-014：建立 Agent Adapter 统一接口

优先级：P0

依赖：DF-010

任务内容：

- Agent Adapter Protocol。
- Agent 启动和参数传递。
- 工作目录和环境变量。
- 执行状态读取。
- 输出和错误采集。
- 超时和取消。
- capabilities 声明。

交付物：

- Agent Adapter 基础接口。
- capabilities 模型。
- Adapter 测试规范。

验收标准：

- Workflow Core 不直接依赖具体 Agent 命令。
- 不同 Agent 可以独立实现 Adapter。
- Agent 能力差异可以声明和检查。

### DF-015：实现 Claude Code Adapter

优先级：P0

依赖：DF-014

任务内容：

- Claude Code 进程启动。
- Prompt 和任务上下文传递。
- 工作目录管理。
- 执行输出采集。
- 退出状态识别。
- 超时和异常处理。
- 会话恢复能力评估。
- 工具和权限审批边界处理。

交付物：

- Claude Code Adapter。
- 配置示例。
- 集成测试。

验收标准：

- Workflow Core 可以通过统一接口启动 Claude Code。
- 可以获得真实执行状态和输出。
- Agent 异常退出可以被识别。
- 不绕过 Claude Code 宿主权限审批。

### DF-016：实现完整研发任务端到端闭环

优先级：P0

依赖：DF-009、DF-011、DF-012、DF-013、DF-015

任务内容：

使用测试项目验证：

- 工作流初始化。
- 需求分析。
- 技术方案。
- DAG 拆解。
- Claude Code 执行。
- 代码修改。
- 编译和测试。
- Verifier 验收。
- 任务状态更新。
- 最终完成。

交付物：

- E2E 测试。
- 完整执行报告。
- 故障测试报告。

验收标准：

- 能够独立完成一个真实研发任务。
- 执行状态完整可追溯。
- 失败能够识别和恢复。
- 验收通过后才进入完成阶段。

## M3：HITL 与 Provider

### DF-017：统一 Human Decision 人工决策管理

优先级：P0

依赖：DF-008、DF-009

任务内容：

- Decision 生命周期。
- 决策持久化。
- 决策和节点绑定。
- 回调处理。
- 幂等。
- 决策恢复。
- 超时处理。
- 待决策查询。

交付物：

- Human Decision Manager。
- 数据模型。
- 决策状态机。
- 测试。

验收标准：

- 决策记录可以恢复。
- 重复回调不重复执行。
- 决策未通过不能执行依赖节点。
- 决策完成后可以恢复原工作流。

### DF-018：实现 DingTalk Notification Provider

优先级：P0

依赖：DF-017

任务内容：

- 统一通知接口。
- 钉钉发送。
- 消息模板。
- 发送状态记录。
- 失败重试。
- 消息送达验证。
- 通知事件管理。

交付物：

- DingTalk Provider。
- 通知模板。
- 通知测试。

验收标准：

- 真实消息能够送达。
- 发送失败不会虚报成功。
- 通知状态持久化。
- 通知成功不等于人工决策完成。

### DF-019：打通钉钉人工决策完整闭环

优先级：P0

依赖：DF-017、DF-018

任务内容：

- 发起人工决策。
- 钉钉通知。
- 用户回复。
- 回调校验。
- Decision 入库。
- 节点状态更新。
- DAG 恢复。
- 原工作流继续执行。

交付物：

- HITL E2E 测试。
- 完整决策闭环报告。

验收标准：

- 不需要用户回到终端手动重新启动原任务。
- 决策可以正确关联原工作流。
- 通知失败和决策失败能够区分。
- 决策后可以恢复原节点和后续依赖节点。
- 不能因人工等待而丢失任务上下文。

### DF-020：实现 ZenTao Project Management Provider

优先级：P0

依赖：DF-007、DF-008、DF-011

任务内容：

- 禅道认证和连接。
- 需求和任务查询。
- 任务创建。
- 任务进度更新。
- 执行结果回写。
- Verifier 结果回写。
- finish/done。
- 条件满足后的 close。
- pending_writes 补偿。

交付物：

- ZenTao Provider。
- 统一接口。
- 集成测试。

验收标准：

- 可以从禅道获取任务。
- 阶段进度自动回写。
- 验收通过后自动 finish/done。
- 满足关闭条件后自动 close。
- 回写失败不会丢失执行结果。
- 不删除任务和历史记录。

### DF-021：实现 Jenkins CI/CD Provider

优先级：P1

依赖：DF-014

任务内容：

- Jenkins 连接配置。
- Job 查询。
- 参数读取。
- 构建触发。
- 构建状态查询。
- 构建结果回写。
- 部署流程接入。
- 人工确认机制。

交付物：

- Jenkins Provider。
- 构建和部署测试。

验收标准：

- 能查询构建状态。
- 能记录构建结果。
- 未经必要人工确认不得触发真实构建或部署。
- 构建失败能够通知和记录。

### DF-022：实现 Git Provider

优先级：P1

依赖：DF-014

任务内容：

- Git 仓库状态。
- 分支管理。
- commit 信息。
- diff 检查。
- 变更文件检查。
- PR/MR 能力评估。
- 代码交付记录。

交付物：

- Git Provider。
- 集成测试。

验收标准：

- 可以读取仓库状态。
- 可以记录代码变更和 commit。
- 不擅自覆盖用户未提交修改。
- 不执行未经授权的破坏性 Git 操作。

## M4：多 Agent 和插件体系

### DF-023：实现 Codex Adapter

优先级：P1

依赖：DF-014、DF-016

任务内容：

- Codex 启动。
- 参数和上下文传递。
- 执行结果读取。
- 状态识别。
- 超时和异常处理。
- capabilities 适配。

验收标准：

- Codex 可以通过统一 Adapter 接口执行任务。
- 不修改 Workflow Core 的核心生命周期。
- 与 Claude Code Adapter 共用同一套验收和状态管理。

### DF-024：实现 OpenCode Adapter

优先级：P1

依赖：DF-014、DF-016

任务内容：

- OpenCode 启动。
- 执行状态读取。
- 输出采集。
- 工具能力适配。
- 超时和错误处理。

验收标准：

- OpenCode 可以通过统一 Adapter 接口接入。
- 不影响其他 Adapter。
- 不把 OpenCode 专属实现写入 Workflow Core。

### DF-025：建立 Provider 插件注册与配置机制

优先级：P1

依赖：DF-018、DF-020、DF-021、DF-022

任务内容：

- Provider 注册。
- Provider 生命周期。
- 配置加载。
- 启用和禁用。
- 能力声明。
- 连接状态检查。
- 示例插件。

验收标准：

- 新 Provider 不需要修改 Workflow Core 核心逻辑。
- Provider 可以独立启用和禁用。
- 缺少非必需 Provider 不阻塞核心引擎启动。

### DF-026：建立工作流模板与项目配置管理

优先级：P1

依赖：DF-007、DF-025

任务内容：

- 工作流模板。
- 项目配置。
- Agent 选择。
- Provider 绑定。
- 验收规则。
- 任务类型。
- 项目级运行参数。

验收标准：

- 不同项目可以使用不同工作流配置。
- 配置与源码分离。
- 配置版本可以追溯。
- 配置错误有明确提示。

## M5：发行、试点和正式升级

### DF-027：实现 DevFlow AI 独立安装和初始化

优先级：P0

依赖：DF-004、DF-005、DF-016

任务内容：

- CLI 安装。
- 配置初始化。
- SQLite 初始化。
- 日志目录初始化。
- Agent 环境检查。
- Provider 连接检查。
- 健康检查。
- 卸载边界。

验收标准：

- 可以在独立环境安装。
- 不修改旧版本目录。
- 不修改旧版本配置和数据库。
- 可以独立启动和执行测试工作流。

### DF-028：实现升级、数据库迁移和回滚

优先级：P0

依赖：DF-005、DF-008、DF-027

任务内容：

- 版本检查。
- 升级前备份。
- Schema 迁移。
- 安装包替换。
- 升级后健康检查。
- 失败回滚。
- 升级日志。

验收标准：

- 升级前可恢复旧程序版本。
- 数据迁移失败不会破坏原始数据库。
- 升级失败可以恢复。
- 不覆盖旧产品数据。

### DF-029：建立 DevFlow AI 端到端稳定性测试

优先级：P0

依赖：DF-016、DF-019、DF-020、DF-027

任务内容：

覆盖：

- 正常执行。
- Agent 失败。
- Agent 超时。
- 终端关闭。
- 主控重启。
- 自动重试。
- 人工决策。
- 钉钉通知失败。
- 禅道回写失败。
- SQLite 迁移。
- 重复回调。
- 重复执行。
- 工作流恢复。
- 非法终止。
- 任务完成和关闭。

验收标准：

- 核心场景全部通过。
- 没有状态丢失。
- 不会将未完成任务误标记为完成。
- 失败原因可追溯。
- 关键链路具备自动化测试。

### DF-030：DevFlow AI 并行试点与旧版本兼容验证

优先级：P0

依赖：DF-028、DF-029

任务内容：

- 选择试点项目。
- 使用 DevFlow AI 执行真实任务。
- 旧版继续承担正式任务。
- 对比执行结果和稳定性。
- 记录故障和人工干预次数。
- 验证旧版不受影响。

验收标准：

- 新旧版本可以并行运行。
- 配置、数据库、执行记录完全隔离。
- 新版本可以完成实际研发任务。
- 旧版本继续正常工作。
- 所有问题有修复或规避方案。

### DF-031：DevFlow AI v1.0.0 正式发行

优先级：P0

依赖：DF-030

任务内容：

- 冻结正式版本。
- 创建 Release Tag。
- 构建发行包。
- 生成 Release Notes。
- 编写安装和升级手册。
- 编写回滚手册。
- 发布版本。
- 评估旧版升级方案。

验收标准：

- 正式发行包可以独立安装。
- 安装、升级和回滚经过验证。
- 核心研发闭环稳定。
- 旧版本仍可保留和恢复。
- 有明确的版本来源和变更记录。

## M6：轻量控制中心

### DF-032：设计轻量级 DevFlow AI 管理界面

优先级：P2

依赖：DF-016

任务内容：

- 工作流列表。
- 工作流详情。
- 节点执行状态。
- Agent 执行记录。
- 人工决策列表。
- 失败和重试记录。
- 日志查看。
- Provider 配置。

验收标准：

- 界面只承担管理和监控职责。
- 核心业务逻辑仍由 Workflow Core 执行。
- 不重复实现调度和状态管理。

### DF-033：实现 DevFlow AI 管理 API 和 Web 控制中心

优先级：P2

依赖：DF-032

任务内容：

- API。
- 工作流查询。
- 执行记录查询。
- 决策查询。
- 状态监控。
- 基础管理界面。

验收标准：

- 可以查看工作流执行状态。
- 可以查看失败和恢复记录。
- 不影响 CLI 执行能力。

# 十、禅道任务执行顺序与依赖

建议按以下顺序创建和推进任务：

第一批：基线与工程管理

DF-001 → DF-002
DF-001 → DF-003 → DF-004 → DF-005

第二批：核心架构

DF-006 → DF-007 → DF-008
DF-007 → DF-009
DF-007、DF-008 → DF-010
DF-010 → DF-011
DF-008、DF-009、DF-010、DF-011 → DF-012
DF-009、DF-011、DF-012 → DF-013

第三批：Agent 执行

DF-010 → DF-014 → DF-015
DF-009、DF-011、DF-012、DF-013、DF-015 → DF-016

第四批：人工决策与 Provider

DF-008、DF-009 → DF-017 → DF-018 → DF-019
DF-007、DF-008、DF-011 → DF-020
DF-014 → DF-021、DF-022

第五批：扩展

DF-014、DF-016 → DF-023、DF-024
DF-018、DF-020、DF-021、DF-022 → DF-025
DF-007、DF-025 → DF-026

第六批：正式交付

DF-004、DF-005、DF-016 → DF-027
DF-005、DF-008、DF-027 → DF-028
DF-016、DF-019、DF-020、DF-027 → DF-029
DF-028、DF-029 → DF-030 → DF-031

最后：

DF-016 → DF-032 → DF-033

说明：

- 上述是逻辑依赖，不代表所有任务必须串行。
- 互不依赖的测试、文档和 Provider 工作可以并行。
- 核心接口未确定前，不要提前派发大量并行开发 Agent。
- 不要为了并行而制造文件冲突。
- 每个任务必须在前置条件满足后再执行。

# 十一、禅道任务创建规范

建议为每个任务设置以下字段：

| 字段     | 规则                          |
| -------- | ----------------------------- |
| 所属项目 | DevFlow AI                    |
| 任务名称 | 使用 DF 编号和明确功能名称    |
| 优先级   | P0 / P1 / P2                  |
| 任务类型 | 开发 / 测试 / 文档 / 工程建设 |
| 前置任务 | 按依赖关系设置                |
| 预计工时 | 盘点后估算，不预先虚构        |
| 指派人员 | 根据实际能力安排              |
| 任务描述 | 背景、目标、实现范围          |
| 交付物   | 代码、文档、测试报告等        |
| 验收标准 | 可验证的具体条件              |

## AI Agent 执行要求

1. 先读取禅道任务和项目上下文。
2. 先盘点现有代码，禁止未经分析直接重写。
3. 先输出实施方案和影响范围。
4. 规划阶段解决必要人工决策。
5. 执行启动后，按 DAG 持续推进。
6. 遇到人工决策，通过 HITL 和钉钉通知。
7. 非阻塞事项记录后继续。
8. 每个阶段持久化状态并回写禅道进度。
9. 执行失败按 Retry 策略处理。
10. 通过 Verifier 验收后自动 finish/done。
11. 满足关闭条件后自动 close。
12. Jenkins 真实构建和部署遵循人工确认机制。
13. 未完成的任务不能伪装成完成。
14. 不允许因终端会话结束而丢失工作流状态。

# 十二、项目验收标准

DevFlow AI 达到 v1.0.0 必须满足：

## 核心功能

- Workflow Core 独立运行。
- SQLite 统一管理工作流状态。
- DAG 调度正常。
- Claude Code Adapter 正常。
- Verifier 正常。
- Retry 正常。
- Recovery 正常。
- HITL 完整闭环。
- 钉钉真实通知。
- 禅道任务获取和回写。
- 自动 finish/done。
- 条件满足后自动 close。
- Jenkins 人工确认机制。

## 可靠性

- Agent 异常退出可检测。
- 主控重启可以恢复。
- 终端关闭不会导致状态丢失。
- 人工决策后可以恢复。
- 通知失败可追溯。
- 回写失败可补偿。
- 不重复执行已完成节点。
- 不错误终止未完成工作流。

## 工程管理

- 独立 Git 仓库。
- 独立版本管理。
- 可重复构建。
- 发行包不包含真实运行数据。
- 安装和升级可验证。
- 失败可以回滚。
- 旧版本不受影响。
- 有完整的开发和使用文档。

## 产品扩展

- Agent Adapter 可插拔。
- Provider 可插拔。
- capabilities 可声明。
- 核心引擎不依赖 Claude Code Skill。
- 后续可以接入 Codex、OpenCode。

# 十三、明确暂缓事项

以下工作不应提前进入 P0：

1. 身份认证体系。
2. 来源验证。
3. HTTP Hook 鉴权。
4. 重量级 Web 管理平台。
5. 可视化 DAG 编辑器。
6. Kubernetes 部署。
7. 分布式消息队列。
8. 复杂多租户。
9. 大规模微服务化。
10. 一开始支持所有 Agent。

优先保证核心引擎可以可靠地完成真实研发任务。

# 十四、总体实施原则

1. 旧版稳定维护，新版独立研发。
2. 从经过验证的稳定 commit 建立 DevFlow AI 基线。
3. 不在旧版源码仓库中直接进行新产品大规模重构。
4. 不在发行目录中日常开发。
5. 不让开发包携带真实运行数据。
6. SQLite 是唯一权威执行状态。
7. 核心生命周期由代码保证。
8. Skill 负责研发规范和任务引导，不承担全部工作流状态管理。
9. Workflow Core 负责调度、执行状态、恢复和验收。
10. Agent Adapter 负责连接不同 AI Coding Agent。
11. Provider 负责连接禅道、钉钉、Jenkins 和 Git。
12. 先 Claude Code 完整闭环，再扩展其他 Agent。
13. 先 CLI，再考虑 Web 管理中心。
14. 架构阶段考虑跨平台，后续完成 macOS 适配。
15. 新版本达到真实可用标准后，再考虑正式替换旧版本。

**最终目标：DevFlow AI 不只是一个自动调用 Claude Code 的脚本，而是一个能够独立管理研发工作流、调度 AI Agent、处理人工决策、完成质量验收、自动回写项目管理系统，并且能够在异常后恢复的通用研发自动化引擎。**
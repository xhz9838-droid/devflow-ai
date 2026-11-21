DevFlow AI
面向 AI Coding Agent 的可插拔研发工作流引擎与控制平台

一个独立运行的 AI 研发工作流引擎，通过插件、MCP、CLI 和 WebUI 接入不同 AI Coding 工具，统一提供工作流编排、Agent 调度、任务持续执行、中断恢复和人工决策能力。

## 当前状态

P0.5 已完成真实 Agent 可靠性验证；DF-018 已增加 Planning Layer 与 Plan / Execute 双阶段工作流：调研 → 决策策略 → 结构化 Plan → 不可变 Snapshot → 现有 Workflow Core 执行 → 受控 Plan Change。

- 架构：[docs/architecture/P0-ARCHITECTURE.md](docs/architecture/P0-ARCHITECTURE.md)
- Planning Layer：[docs/architecture/DF-018-PLANNING-ARCHITECTURE.md](docs/architecture/DF-018-PLANNING-ARCHITECTURE.md)
- 决策：[docs/decisions/ADR-001-P0-CORE.md](docs/decisions/ADR-001-P0-CORE.md)、[ADR-DF-018](docs/decisions/ADR-DF-018-PLAN-EXECUTE.md)、[Decision Policy](docs/decisions/ADR-DF-018-DECISION-POLICY.md)
- 实施报告：[docs/implementation/DF-018-IMPLEMENTATION-REPORT.md](docs/implementation/DF-018-IMPLEMENTATION-REPORT.md)
- P0.5 验收：[docs/implementation/P0.5-ACCEPTANCE-REPORT.md](docs/implementation/P0.5-ACCEPTANCE-REPORT.md)
- 分阶段报告：[docs/implementation/](docs/implementation/)
- 基线盘点：[docs/baseline/](docs/baseline/)

## 快速开始

要求 Python 3.11+，无第三方运行时依赖。

```bash
python -m devflow --db devflow.db init
python -m devflow --db devflow.db create --name demo --goal "示例" --nodes nodes.json
python -m devflow --db devflow.db start <workflow-id>
python -m devflow --db devflow.db status <workflow-id>
python -m devflow --db devflow.db inspect <workflow-id>
python -m devflow --db devflow.db recover
```

Planning Layer：

```bash
python -m devflow --workdir . plan create "给 calculator.py 增加 add/subtract 和 pytest 测试"
python -m devflow plan show <plan-id>
python -m devflow plan clarify <plan-id>
python -m devflow plan answer <plan-id> <clarification-id> "按推荐方案"
python -m devflow plan validate <plan-id>
python -m devflow plan confirm <plan-id>
python -m devflow workflow start --plan <plan-id>
```

`plan answer` 默认自动重规划；Plan 确认后 Snapshot 不可修改，执行期变化通过 Plan Change Request 处理。

`start` 通过 Claude Code Adapter 调用本机 `claude` CLI。需要在临时/受控目录中允许文件编辑时，可显式传入：

```bash
export DEVFLOW_CLAUDE_EXTRA_ARGS="--permission-mode acceptEdits"
```

人工决策：

```bash
python -m devflow --db devflow.db decisions <workflow-id>
python -m devflow --db devflow.db resolve <decision-id> --option approve
python -m devflow --db devflow.db resume <workflow-id>
```

## 测试

```bash
python -m pytest -q
```

真实 Claude 测试默认跳过，需要本机已登录 Claude CLI：

```bash
DEVFLOW_REAL_CLAUDE=1 python -m pytest -q -m real_agent
```

关键环境变量：

| 变量 | 默认 | 作用 |
|------|------|------|
| `DEVFLOW_DB` | `devflow.db` | SQLite 路径 |
| `DEVFLOW_MAX_PARALLEL` | `4` | 并行节点数 |
| `DEVFLOW_AGENT_TIMEOUT` | `1800` | Agent 默认超时（秒） |
| `DEVFLOW_RECOVERY_GRACE` | `30` | 心跳/无界 Run 的存活窗口（秒） |
| `DEVFLOW_ENGINE_LEASE_SECONDS` | `300` | 单 Engine 调度租约 TTL |
| `DEVFLOW_CLAUDE_EXTRA_ARGS` | 空 | 传给 Claude CLI 的额外参数 |

## 代码结构

```
devflow/
├── engine.py           编排
├── lifecycle.py        状态机
├── scheduler.py        DAG 调度
├── state.py            SQLite 权威状态
├── rows.py             SQLite 行映射
├── db.py               Schema 迁移
├── verifier.py         完成判定
├── continuation.py     WCP
├── recovery.py         中断恢复
├── hitl.py             人工决策
├── agent_protocol.py   运行中决策/计划变更请求协议
├── lease.py            单 Engine 租约
├── platform_proc.py    跨平台进程身份/终止
├── cli.py              控制入口
├── planning/           Planning Layer
│   ├── policy.py       L0-L3 Decision Policy
│   ├── validator.py    Plan Schema/DAG/验收
│   ├── store.py        Plan 持久化
│   ├── planner.py      Planner 接口 + ClaudeCodePlanner
│   ├── manager.py      Plan 编排
│   └── changes.py      Plan Change Request
└── agents/             Agent Adapter + Claude Code 实现
```

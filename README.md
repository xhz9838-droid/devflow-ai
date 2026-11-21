DevFlow AI
面向 AI Coding Agent 的可插拔研发工作流引擎与控制平台

一个独立运行的 AI 研发工作流引擎，通过插件、MCP、CLI 和 WebUI 接入不同 AI Coding 工具，统一提供工作流编排、Agent 调度、任务持续执行、中断恢复和人工决策能力。

## 当前状态

P0 核心引擎已完成：生命周期、SQLite 权威状态、DAG 调度、Agent 执行、Verifier、WCP、恢复、HITL 和端到端闭环。不依赖禅道、钉钉或 Jenkins 即可独立运行。

- 架构：[docs/architecture/P0-ARCHITECTURE.md](docs/architecture/P0-ARCHITECTURE.md)
- 决策：[docs/decisions/ADR-001-P0-CORE.md](docs/decisions/ADR-001-P0-CORE.md)
- 实施报告：[docs/implementation/P0-REPORT.md](docs/implementation/P0-REPORT.md)
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
├── cli.py              控制入口
└── agents/             Agent Adapter + Claude Code 实现
```

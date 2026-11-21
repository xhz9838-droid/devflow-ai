# P0 实施报告：Workflow Engine 核心

- 版本：0.1.0
- 日期：2026-11-21
- 结论：**P0 完成**。核心引擎可在无禅道、无钉钉、无 Jenkins 的条件下独立创建、执行、中断、恢复、人工决策并完成 Workflow。
- 测试：`86 passed`（`python -m pytest -q`，约 26 秒）

## 1. 交付对照

| 阶段 | 交付 | 代码 | 测试 |
|------|------|------|------|
| P0-1 骨架与架构 | 分层、配置、日志、异常、CLI 入口 | `devflow/engine.py`, `config.py`, `errors.py`, `cli.py`, `__main__.py` | `test_e2e.py` |
| P0-2 生命周期 | Workflow/Node 状态机、转换服务、事件记录 | `lifecycle.py`, `state.py`, `models.py` | `test_lifecycle.py`, `test_state.py` |
| P0-3 SQLite 状态 | 8 张业务表、迁移、Repository/StateStore | `db.py`, `state.py` | `test_state.py` |
| P0-4 DAG 调度 | DAG 校验、拓扑排序、READY/BLOCKED、并行、原子 claim | `scheduler.py` | `test_scheduler.py` |
| P0-5 Agent 执行 | Adapter 契约、Execution Manager、Claude Code Adapter | `agents/` | `test_agents.py` |
| P0-6 完成判定 | Verifier、VerifyReport、CompletionVerifier | `verifier.py` | `test_verifier.py` |
| P0-7 WCP | Checkpoint Manager、Continuation Context Builder | `continuation.py` | `test_continuation.py` |
| P0-8 恢复 | RecoveryManager、RetryPolicy、启动扫描命令 | `recovery.py` | `test_recovery.py` |
| P0-9 HITL | 决策状态机、存储、超时、恢复 | `hitl.py` | `test_hitl.py` |
| P0-10 端到端 | 创建→调度→执行→验收→中断→恢复→HITL→完成 | `engine.py` | `test_e2e.py` |

## 2. 代码结构

```
devflow/
├── __init__.py, __main__.py, cli.py     控制入口
├── config.py, errors.py                 配置、日志、异常
├── models.py                            领域模型与状态枚举
├── db.py                                SQLite 连接与迁移
├── state.py                             权威状态存储（唯一 SQL 层）
├── rows.py                              SQLite 行与领域模型映射
├── lifecycle.py                         四套状态机
├── scheduler.py                         DAG 校验与调度
├── verifier.py                          节点验收与完成判定
├── continuation.py                      WCP
├── recovery.py                          中断恢复与重试
├── hitl.py                              人工决策
├── engine.py                            编排
└── agents/
    ├── base.py                          AgentAdapter / AgentResult / Registry
    ├── manager.py                       Agent Run 生命周期
    └── claude_code.py                   Claude Code CLI Adapter
tests/                                   9 个测试模块，86 个用例
```

## 3. 关键接口

### 3.1 Engine

```python
WorkflowEngine(config, adapter, verifier=None)

create_workflow(name, goal, nodes, context=None) -> Workflow
start(workflow_id) -> Workflow
resume(workflow_id) -> Workflow
cancel(workflow_id, reason) -> Workflow
recover_all() -> list[RecoveryResult]
run(workflow_id, max_iterations=100) -> Workflow
status(workflow_id) -> dict
inspect(workflow_id, event_limit=100) -> dict
```

### 3.2 Agent Adapter

```python
class AgentAdapter:
    name: str
    capabilities: frozenset[Capability]
    def execute(self, request: AgentRequest) -> AgentResult: ...
```

异常约定：`AgentTimeout` → TIMEOUT，`AgentInterrupted` → INTERRUPTED，其他异常 → FAILED。

### 3.3 Node 定义（CLI JSON）

```json
{
  "id": "implement",
  "name": "实现需求",
  "dependencies": ["analyze"],
  "required": true,
  "idempotency_key": "implement-v1",
  "execution_config": {
    "prompt": "可选，自定义提示词",
    "timeout_seconds": 1800,
    "artifacts": ["dist/report.md"],
    "acceptance": [
      {"type": "exit_code", "equals": 0},
      {"type": "output_contains", "value": "done"},
      {"type": "artifact_exists", "path": "dist/report.md"}
    ],
    "human_decision": {
      "question": "是否批准进入开发？",
      "decision_type": "approval",
      "options": [{"id": "approve", "label": "批准"}, {"id": "reject", "label": "驳回"}],
      "timeout_seconds": 3600
    }
  },
  "retry_policy": {
    "max_retries": 2,
    "base_delay_seconds": 1,
    "multiplier": 2,
    "max_delay_seconds": 60
  }
}
```

## 4. 数据模型变化

新增 9 张表（含迁移表）：

- `schema_migrations`
- `workflows`
- `workflow_stages`
- `workflow_nodes`
- `agent_runs`
- `execution_events`
- `checkpoints`
- `human_decisions`
- `retry_records`

索引覆盖 Workflow 状态、节点状态、Agent Run 状态、事件历史；`human_decisions(node_id) WHERE status='PENDING'` 与 `retry_records(node_id, attempt)` 为唯一约束，防止重复决策和重复重试记录。

## 5. 端到端验收证据

`tests/test_e2e.py::test_p0_end_to_end_with_interruption_recovery_and_hitl` 覆盖 P0-10 全部 15 个步骤：

1. 创建 6 节点 Workflow。
2. `start` 完成初始化。
3. DAG 依赖：`n1→n2→n3`、`n4` 人工节点、`n5` 独立节点、`n6` 汇聚。
4. 首轮并行调度 `n1/n4/n5`。
5. 通过 Agent Adapter 执行。
6. 结果、退出码、输出写入 `workflow_nodes.output`。
7. Verifier 通过后才把节点置为 `SUCCEEDED`。
8. 每个批次后保存 Checkpoint。
9. `n2` 首次执行模拟 Agent 中断（`AgentInterrupted`）。
10. Recovery 将 `n2` 从 `RUNNING` 恢复到 `READY`，写入 `retry_records`。
11. `n4` 进入 `WAITING_HUMAN`，同时 `n1/n5` 已独立完成。
12. CLI 等价调用解析人工决策（approve）。
13. `resume` 从原 Workflow 继续，无需重新开始。
14. 所有节点验收通过。
15. Workflow 进入 `COMPLETED`。

额外断言：

- `n1` 只执行 1 次，`n2` 执行 2 次（中断一次、重试一次），无重复执行。
- Agent Run 历史同时包含 `INTERRUPTED` 与 `SUCCEEDED`。
- 事件流包含 `node.interrupted`、`node.retry_scheduled`、`human_decision.requested`、`human_decision.resolved`、`workflow.recovery_started`、`workflow.recovered`、`workflow.completed`。
- 最新 Checkpoint 的 `progress.completed == 6`，`side_effects_completed` 含幂等键。
- 包源码不包含 `zentao`、`dingtalk`、`jenkins` 字样，证明 Core 无外部系统依赖。

## 6. 测试结果

```
$ python -m pytest -q
86 passed in 26.46s
```

| 模块 | 覆盖内容 |
|------|---------|
| `test_lifecycle.py` | 合法/非法转换、终态、节点重试与人工等待 |
| `test_state.py` | 迁移幂等、事务、事件历史、原子 claim、唯一约束、FK |
| `test_scheduler.py` | 环检测、拓扑排序、阻塞传播、恢复解阻、并发 claim |
| `test_verifier.py` | 验收条件、证据、完成判定 |
| `test_continuation.py` | Checkpoint 一致性、序列、上下文、失败不吞 |
| `test_recovery.py` | 中断重试、重试耗尽、心跳超时、成功免重跑、决策对账、幂等 |
| `test_hitl.py` | 请求幂等、选项校验、恢复、超时、取消、审计 |
| `test_agents.py` | 运行分类、输出持久化、Claude 命令安全、注册表 |
| `test_e2e.py` | 完整闭环、失败收敛、取消、独立节点不阻塞 |

## 7. 已知问题与边界

| 问题 | 影响 | 处理 |
|------|------|------|
| 单主控模型 | 多个 Engine 进程同时操作同一 DB 没有 leader 选举 | P0 定位单机单进程；Recovery 对心跳有效的 Run 返回 `active`，不做抢占；控制命令的重复扫描通过同状态幂等写入容忍 |
| 取消不杀死子进程 | `cancel` 是状态级操作 | Adapter 超时是硬停止；P1 记录 pid 并终止外部进程 |
| 进程内心跳 | Agent 运行期间由后台线程刷新心跳，避免被其他 `recover` 误判 | 心跳间隔随 timeout 缩放；跨进程长任务仍需 P1 的 pid/进程监控 |
| 无界 Run | `timeout_seconds: 0` 表示无截止时间，以 grace 作为心跳存活窗口 | 心跳停止超过 grace 即按中断恢复；无心跳的无界 Run 依赖人工处理 |
| 输出整体入库 | 大输出会增大 SQLite | 当前保存摘要与输出；P1 可按需外置/截断 |
| Stage 仅分组 | 不自动推进阶段状态 | P1 如需阶段门禁再扩展 |
| 仅前置人工决策 | 不支持 Agent 运行中动态请求决策 | 当前 `human_decision` 在节点执行前门禁；P1 可增加运行中事件 |
| Windows `.cmd` 适配 | `claude` 若为 `.cmd`，`shell=False` 可能需要平台适配 | 通过 Agent Adapter 层处理，不进入 Core |
| `run()` 迭代上限 | 默认按 `max(100, 节点数*2+10)` 自动放大，极端非链式 DAG 仍可能触顶 | 触顶会写事件并抛错，不会静默停止；可显式传入 `max_iterations` |

## 8. P1 准备条件

- Agent Adapter：新增 Codex/OpenCode，仅需实现 `execute` 并声明 `capabilities`。
- Provider：独立包实现项目管理、通知、Git、CI；Core 只记录结果，不依赖其可用性。
- CLI：已具备 create/start/status/inspect/resume/cancel/decisions/events/recover/list；P1 可增加模板与批量操作。
- 通知：在事件流之上增加可靠投递，失败不得改写 Workflow 状态。
- 模板：以 Node 列表 + 策略 JSON 表达，不绑定 Agent/Provider。

## 9. 验证命令

```bash
python -m pytest -q
python -m devflow --db devflow.db init
python -m devflow --db devflow.db create --name demo --goal "演示" --nodes nodes.json
python -m devflow --db devflow.db start <workflow-id>
python -m devflow --db devflow.db status <workflow-id>
python -m devflow --db devflow.db decisions <workflow-id>
python -m devflow --db devflow.db resolve <decision-id> --option approve
python -m devflow --db devflow.db resume <workflow-id>
python -m devflow --db devflow.db recover
```

> `start` 会调用本机 `claude` CLI。端到端自动化验证使用测试内的 ScriptedAdapter，不依赖本机 Agent 安装。

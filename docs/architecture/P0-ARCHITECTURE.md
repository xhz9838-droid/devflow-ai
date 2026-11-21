# DevFlow AI P0 核心架构

- 版本：0.1.0（P0）
- 更新：2026-11-21
- 对应代码：`devflow/`
- 原则：SQLite 是唯一权威状态；无 Provider 也能独立完成 Workflow。

## 1. 分层与依赖方向

```
┌──────────────────────────────────────────────┐
│ Control Interface                            │
│ devflow/cli.py, devflow/__main__.py          │
└───────────────────┬──────────────────────────┘
                    │ 只调用 Engine 公共方法
┌───────────────────▼──────────────────────────┐
│ Workflow Engine (orchestration)              │
│ devflow/engine.py                            │
└──┬────────┬─────────┬─────────┬─────────┬────┘
   │        │         │         │         │
┌──▼──┐ ┌───▼────┐ ┌──▼────┐ ┌──▼────┐ ┌──▼──────┐
│Life │ │DAG     │ │Verify │ │WCP    │ │Recovery │
│cycle│ │Scheduler│ │       │ │       │ │+ HITL   │
└──┬──┘ └───┬────┘ └──┬────┘ └──┬────┘ └──┬──────┘
   └────────┴─────────┴─────────┴─────────┘
                    │
          ┌─────────▼──────────┐
          │ Persistence        │
          │ StateStore → SQLite│
          └─────────┬──────────┘
                    │ Agent Adapter 接口
          ┌─────────▼──────────┐
          │ AgentAdapter       │
          │ ClaudeCodeAdapter  │
          └────────────────────┘
```

依赖规则：

- `engine` 只依赖接口、模型和状态存储，不依赖任何外部系统。
- `agents/base.py` 定义 Adapter 契约；`claude_code.py` 是唯一具体实现。
- `providers/` 在 P0 不存在，Core 不引用禅道、钉钉、Jenkins。
- 状态读写集中在 `state.py`，其他模块不直接写 SQL。

## 2. 模块职责

| 模块 | 文件 | 职责 |
|------|------|------|
| 配置/日志/异常 | `config.py`, `errors.py` | 环境配置、统一日志、异常层次 |
| 模型 | `models.py` | Workflow / Node / AgentRun / Decision / Checkpoint / Event / VerifyReport |
| 迁移 | `db.py` | SQLite 连接、Schema 版本、DDL 迁移 |
| 状态存储 | `state.py` | 所有权威状态的读写、事件追加、事务 |
| 生命周期 | `lifecycle.py` | 四套状态机的合法转换表 |
| 调度 | `scheduler.py` | DAG 校验、环检测、READY/BLOCKED 重算、原子 claim |
| Agent 执行 | `agents/` | Adapter 契约、Claude Code 实现、Agent Run 管理 |
| 验收 | `verifier.py` | 节点验收条件、Workflow 完成判定 |
| WCP | `continuation.py` | Checkpoint、Continuation Context |
| 恢复 | `recovery.py` | 中断识别、重试/退避、状态对账 |
| HITL | `hitl.py` | 决策创建、校验、超时、恢复 |
| 编排 | `engine.py` | 组合上述模块，驱动 Workflow |
| 控制入口 | `cli.py` | 命令解析与输出，不含状态机/调度逻辑 |

## 3. 生命周期状态机

### 3.1 Workflow

```
CREATED → INITIALIZING → RUNNING → COMPLETED
                         │  ├──→ WAITING → RUNNING
                         │  ├──→ WAITING_HUMAN → RUNNING
                         │  ├──→ RECOVERING → RUNNING
                         │  └──→ FAILED / CANCELLED
```

- `COMPLETED` / `FAILED` / `CANCELLED` 为终态，没有出边。
- `WAITING_HUMAN` 是 Workflow 级提示：没有可执行的独立节点且仍有待决策。
- 任一非法转换抛 `InvalidTransition`，不修改数据库。

### 3.2 Node

```
PENDING → READY → RUNNING → SUCCEEDED
                   │  ├──→ WAITING_HUMAN → READY
                   │  ├──→ FAILED → READY（重试）
                   │  └──→ READY（恢复）
PENDING/BLOCKED → BLOCKED → READY（上游恢复后）
```

- `SUCCEEDED / FAILED / SKIPPED / CANCELLED` 为终态。
- Node 与 Workflow 状态分离：一个 Node 等待人工不阻塞无依赖节点。

### 3.3 Agent Run / Decision

```
AgentRun: RUNNING → SUCCEEDED | FAILED | TIMEOUT | INTERRUPTED | CANCELLED
Decision: PENDING → RESOLVED | CANCELLED | TIMEOUT
```

Agent 进程退出码只影响 Agent Run，不能直接决定 Node 或 Workflow 完成。

## 4. 持久化模型

| 表 | 作用 | 关键约束 |
|----|------|---------|
| `schema_migrations` | Schema 版本 | 主键 version |
| `workflows` | Workflow 权威状态 | status 索引 |
| `workflow_stages` | 阶段分组 | 外键 workflow |
| `workflow_nodes` | DAG 节点 | `(workflow_id,status)` 索引、FK |
| `agent_runs` | 每次 Agent 调用 | 记录 attempt/exit_code/output/心跳 |
| `execution_events` | 完整执行历史 | 追加写，不覆盖 |
| `checkpoints` | WCP 检查点 | `UNIQUE(workflow_id, sequence)` |
| `human_decisions` | 人工决策 | 每个节点最多一个 PENDING（部分唯一索引） |
| `retry_records` | 重试审计 | `UNIQUE(node_id, attempt)` |

一致性规则：

- 所有状态写入走 `StateStore` 的 `BEGIN IMMEDIATE` 事务。
- 状态变更与事件写入在同一事务内完成。
- Checkpoint 的快照与插入在同一事务内完成；失败抛 `CheckpointError`。
- 不维护第二份 JSON 权威状态。

## 5. DAG 调度

- 创建时执行 Kahn 拓扑排序：重复 ID、自依赖、未知依赖、环均拒绝。
- 每轮从数据库重新计算，不依赖内存状态：
  - 依赖全部 `SUCCEEDED/SKIPPED` → `READY`
  - 任一依赖失败/取消 → 传递性 `BLOCKED`
  - 上游重试成功后，`BLOCKED` 可回到 `READY`
- `claim_node` 使用 `UPDATE ... WHERE status='READY'` 原子抢占，避免重复调度。
- 可执行节点通过 `ThreadPoolExecutor` 并行执行。
- 退避中的 READY 节点按 `retry_records.next_retry_at` 等待，不会绕过退避。

## 6. Agent 执行层

- `AgentAdapter.execute(AgentRequest) -> AgentResult`。
- `AgentExecutionManager` 负责 Agent Run 生命周期与异常分类：
  - `AgentTimeout` → `TIMEOUT`
  - `AgentInterrupted` → `INTERRUPTED`
  - 其他异常 → `FAILED`
- Claude Code Adapter 使用 `subprocess.run(..., shell=False)`，超时可配置。
- Engine 不拼装 Claude Code 命令，只调用 Adapter。

## 7. Verifier

- 节点级：`execution_config.acceptance` 支持 `exit_code`、`output_contains`、`output_not_contains`、`artifact_exists`。
- 无验收条件时仍要求 Agent Run 成功。
- `VerifyReport` 持久化到 `workflow_nodes.output.verification`。
- Workflow 完成由 `CompletionVerifier` 判定：必需节点全部 `SUCCEEDED`、验收通过、无待决策、无运行中节点。
- 完成判定不读取任何第三方系统状态。

## 8. WCP（Workflow Continuation Protocol）

Checkpoint 内容：

- workflow / stage / node / decision / agent_run 快照
- 最近 50 条事件
- Continuation Context：目标、当前节点、进度、已完成/运行中/待执行/阻塞节点、待决策、交付物、已完成的副作用（幂等键）、未解决问题、下一步动作、最后事件

恢复规则：

- SQLite 始终是权威；Checkpoint 是交接产物。
- 节点不在 `SUCCEEDED` 前不会被视为完成。
- 带 `idempotency_key` 的已完成节点记录在 `side_effects_completed`，避免重复副作用。
- Agent 已完成但节点未推进时，恢复会先用 Verifier 复核，而不是盲目重跑。

## 9. Recovery 与 Retry

识别：

- Agent Run `INTERRUPTED`
- `RUNNING` 但心跳超时（`timeout + grace`）；运行期间由 AgentExecutionManager 的后台线程持续刷新心跳
- 无界 Run（`timeout_seconds <= 0`）使用 `grace` 作为心跳存活窗口，心跳停止即视为中断
- 节点 `RUNNING` 但没有 Agent Run
- Agent 成功但节点未推进
- `WAITING_HUMAN` 但决策已解决/超时（对账）

处理：

- 未超限：`RUNNING → READY`，写 `retry_records`，记录 `next_retry_at`。
- 超限：`FAILED`，由 Core 判定 Workflow `FAILED`。
- 有 `RUNNING` 且心跳有效的 Run：视为另一执行者仍在运行，返回 `active`，不做抢占。
- 恢复操作幂等：重复 scan 不重复写重试记录；同状态写入是无操作；并发 reconciliation 的竞争转换被忽略。
- Workflow 进入 `COMPLETED/FAILED` 前会取消未决决策与非终态节点，终态工作流不再可被外部决策改写。

退避：`delay = base_delay * multiplier^(n-1)`，上限 `max_delay`。

## 10. HITL

- 决策是 Core 原生数据，不依赖消息平台。
- 节点 `execution_config.human_decision` 触发决策，节点进入 `WAITING_HUMAN`。
- 决策选项在创建时规范化；解析时校验选项。
- `WAITING_HUMAN` 节点不阻塞无依赖节点。
- 决策入库即完成；通知成功不等于决策完成。
- 决策结果、解决人、时间、payload 全部持久化并写审计事件。
- 超时/取消会使节点失败，由 Workflow 统一收敛。

## 11. 跨平台

- 路径使用 `pathlib.Path`。
- Agent 进程通过 Adapter 调用，Core 不含 `cmd.exe`/PowerShell。
- 状态中不保存盘符相关假设；`workdir` 由配置传入。
- `subprocess.run` 使用参数列表和 `shell=False`。

## 12. P1 扩展点

- 多 Agent：注册更多 `AgentAdapter`，用 `capabilities` 声明能力。
- Provider：独立包实现 `ProjectManagement/Notification/Git/CI`，由 Engine 记录结果。
- CLI：现有控制入口已覆盖 create/start/status/inspect/resume/cancel/decisions/events。
- 模板：以 Node 列表 + 策略表达，不绑定 Agent 或 Provider。

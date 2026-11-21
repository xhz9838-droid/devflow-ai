# ADR-001：P0 核心基线决策

- 状态：已采纳
- 日期：2026-11-21
- 范围：P0（Workflow Engine 核心）

## 背景

DevFlow AI 需要从产品目标出发建立独立 Workflow Core，而不是迁移旧 Skill 的执行流程。P0 必须证明：不依赖禅道、钉钉、Jenkins，也能创建、执行、恢复、人工决策并完成 Workflow。

## 决策

### D1. Python 3.11+ 标准库 + SQLite，不引入运行时依赖

- 使用 `sqlite3`、`dataclasses`、`argparse`、`subprocess`、`concurrent.futures`。
- 不引入 ORM、Web 框架、消息中间件。
- 原因：P0 的约束是可靠性验证，不是平台化；减少依赖面便于单机部署和测试。
- 后果：复杂查询需手写 SQL；后续如需要可替换 Repository 实现而不改 Engine。

### D2. 单一 Python 包，按职责分文件

- `devflow/` 内部按 core 职责拆分，不建立空目录或未来占位。
- 不复制旧版 Skill 目录结构。
- 原因：避免为假设中的扩展付出结构成本。

### D3. SQLite 是唯一权威状态

- 所有状态、事件、决策、检查点都写 SQLite。
- 不维护 JSON 状态文件。
- 状态变更与事件写入同事务；Checkpoint 快照同事务。
- 原因：恢复能力取决于权威状态的一致性。

### D4. Workflow / Node / Agent Run 三层分离

- Workflow 由 Core 收敛；Node 由 Scheduler 推进；Agent Run 由 Execution Manager 记录。
- Agent 退出码只影响 Agent Run。
- Node 成功必须经过 Verifier；Workflow 完成必须经过 CompletionVerifier。
- 原因：避免把 Agent 行为误当任务完成。

### D5. 同步批次 + 线程池执行

- `run()` 每轮重算 READY，claim 后并行执行该批次，等待完成再进入下一轮。
- 不引入异步事件循环或消息队列。
- 原因：P0 单进程闭环足够；SQLite 事务和原子 claim 已能防止重复调度。
- 后果：Engine 进程必须存活；中断由 Recovery 在下一轮处理。

### D6. 中断不是失败

- `AgentInterrupted`、心跳超时、进程消失都先进入 Recovery。
- Recovery 决定重试或失败；不直接把 Workflow 判定为 FAILED。
- 原因：P0-8 的核心要求。

### D7. Checkpoint 是交接产物，不是权威

- Checkpoint 记录快照和 Continuation Context。
- 恢复时以数据库当前状态为准；Checkpoint 用于构建 Agent 交接上下文。
- 原因：避免两套状态冲突。

### D8. 人工决策是 Core 原生能力

- 决策表、状态机、超时、审计全部在 Core 内实现。
- 通知只是 P1 的可插拔 Provider，不能代表决策完成。
- 节点级 `WAITING_HUMAN`，不阻塞独立节点。
- 原因：HITL 是长任务可靠运行的必要组成。

### D9. 重试策略数据化

- 节点 `retry_policy` 支持 `max_retries`、`base_delay_seconds`、`multiplier`、`max_delay_seconds`。
- 退避时间持久化到 `retry_records.next_retry_at`，Engine 在调度前等待。
- 原因：恢复和重试必须可审计、可复现。

## 不采纳

- 微服务、Kubernetes、Kafka/RabbitMQ。
- 独立 Web 平台、数据库集群。
- 在 Core 内直接 import Claude Code 或任何 Provider SDK。
- 用 Agent 对话全文作为恢复依据。

## 后果与风险

- 单进程执行模型不提供多主控并发协调；SQLite 只保证写入串行化。
- 外部 Provider 缺失不影响 Core；接入后其失败也不能改写核心状态。
- 大输出可能增大 SQLite；P0 接受，后续可按需截断或外置。

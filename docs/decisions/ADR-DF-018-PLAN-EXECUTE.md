# ADR-DF-018：Plan / Execute 双阶段与 Snapshot 契约

- 状态：已采纳
- 日期：2026-11-21
- 范围：DF-018 Planning Layer

## 背景

DevFlow AI 已具备 P0/P0.5 Workflow Core，但缺少执行前的需求调研、关键决策筛选和结构化计划。需要在不重写 Core、不引入第二套状态管理的前提下，增加 Planning Layer。

## 决策

### D1. 一个 Core，Planning Layer 作为前置阶段

Planning Layer 生成 Execution Plan 和 Snapshot；Workflow Core 继续负责调度、Agent、WCP、Recovery、HITL、Verifier。不建立独立 Plan Engine、独立数据库或独立调度器。

### D2. Execution Plan 是结构化数据，不是 Markdown

Plan 包含 scope、decisions、nodes、依赖、验收条件、交付要求。只有通过 Schema/DAG/验收校验的 Plan 才能确认。

### D3. 确认生成不可变 Snapshot

- 确认在单个 SQLite 事务中写入 Snapshot、Version 和 Plan 状态。
- Snapshot 内容 Hash 稳定。
- SQLite Trigger 禁止修改/删除 Snapshot 和 Version。
- Workflow 创建时绑定 Snapshot ID 和版本，同一 Snapshot 只允许一个 Workflow。

### D4. Plan 变更必须走受控流程

执行期间不允许 Agent 直接修改 DAG。Agent 只能写 Plan Change Request：

- LOW：Core 记录并应用，生成新 Snapshot。
- MAJOR：进入 Workflow 级 HITL，人工批准后生成新 Snapshot。
- 已完成未受影响节点不重跑；受影响节点用替代节点重新执行。
- 旧 Snapshot、旧节点和变更历史全部保留。

### D5. Planner 与 Agent Adapter 解耦

`Planner` 接口接收目标、仓库上下文和已回答问题；`ClaudeCodePlanner` 复用现有 Agent Adapter。Planning Layer 不硬编码 Claude CLI 命令，不新建 Agent 进程管理。

### D6. 仓库调研使用只读权限

Planner 默认 `--permission-mode plan`，只读调研；执行 Agent 使用显式配置的权限。Plan 输出必须经过代码校验，模型不能直接写 Workflow 状态。

## 后果

- 规划与执行之间有可追溯、可恢复的 Snapshot 契约。
- 已确认计划不会被执行期 Agent 悄悄改写。
- 计划变更有明确风险分级、审批和新版本。
- 现有 P0/P0.5 状态机、SQLite 权威和测试保持兼容。
- 代价：Plan Change 的 DAG 变更逻辑仍是最小实现，不支持可视化编辑或复杂节点合并。

## 不采纳

- 独立 Plan Engine / 第二套数据库。
- 将 Plan 仅保存为 Markdown 或 Prompt。
- 允许 Agent 直接改 Workflow Node 表。
- 让模型自行决定 L3 风险而不经代码策略。
- 用通知成功代表人工决策完成。

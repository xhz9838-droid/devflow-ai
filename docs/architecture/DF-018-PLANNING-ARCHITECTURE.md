# DF-018 Planning Layer 架构

- 版本：1.0
- 日期：2026-11-21
- 代码：`devflow/planning/`
- 原则：Planning Layer 只生成和变更计划；Workflow Core 仍是唯一执行引擎与权威状态来源。

## 1. 总体结构

```
User objective
   │
   ▼
PlanManager ──► Planner (ClaudeCodePlanner / future Codex)
   │              │
   │              ├─ RepositoryContext（只读调研）
   │              ├─ Execution Plan JSON
   │              └─ clarification_candidates
   │
   ├─ DecisionPolicy（代码级 L0-L3）
   ├─ PlanValidator（Schema + DAG + 验收可验证）
   ├─ PlanStore（SQLite Repository）
   │
   ▼
Plan Ready → Confirm → immutable Snapshot
   │
   ▼
WorkflowEngine.create_workflow_from_plan
   │
   ├─ DAG Scheduler / Agent Execution / WCP / Recovery / HITL / Verifier
   └─ PlanChangeManager（执行期受控变更）
```

## 2. 模块职责

| 模块 | 文件 | 职责 |
|------|------|------|
| 模型 | `planning/models.py` | Plan 状态机、Clarification、Snapshot、Version、ChangeRequest |
| 决策策略 | `planning/policy.py` | L0-L3 分类、调研优先、批量限制、变更风险分类 |
| 校验 | `planning/validator.py` | Plan Schema、DAG、验收条件、内容 Hash、转换为 Workflow Node |
| 存储 | `planning/store.py` | plans/clarifications/versions/snapshots/change_requests 的 SQL Repository |
| Planner | `planning/planner.py` | Planner 接口、仓库上下文、Claude Code 规划实现、JSON 提取 |
| 编排 | `planning/manager.py` | 生成、澄清、回答、重规划、校验、确认、历史 |
| 变更 | `planning/changes.py` | Plan Change Request、低风险应用、重大变更 HITL、新 Snapshot |
| 执行集成 | `engine.py` | Snapshot → Workflow、Plan 绑定、运行中变更应用 |

Planning Layer 不直接操作 Workflow 状态；所有执行状态仍通过 `StateStore`。

## 3. Plan 生命周期

```
DRAFT
  → INVESTIGATING
  → ANALYZING
  → NEEDS_CLARIFICATION → ANALYZING
  → PLAN_READY
  → CONFIRMED
  → EXECUTION_READY
```

- `FAILED`：规划失败，可重新进入 `INVESTIGATING/ANALYZING`。
- `CANCELLED`：终态。
- `CONFIRMED` 后计划内容不可直接修改；变化必须生成新版本。
- `EXECUTION_READY` 表示已绑定 Workflow。

## 4. Execution Plan

```json
{
  "title": "...",
  "objective": "...",
  "scope": {"included": [], "excluded": []},
  "assumptions": [],
  "decisions": [{"level": "L1|L2", "subject": "...", "choice": "...", "reason": "..."}],
  "risks": [],
  "nodes": [
    {
      "id": "...", "name": "...", "description": "...", "type": "agent",
      "agent": {"adapter": "claude-code", "role": "..."},
      "dependencies": [],
      "inputs": {},
      "expected_outputs": [],
      "acceptance_criteria": [{"type": "artifact_exists", "path": "..."}],
      "retry_policy": {},
      "execution_config": {"prompt": "..."}
    }
  ],
  "acceptance_criteria": [],
  "delivery_requirements": [],
  "clarification": {"required": [], "resolved": []},
  "metadata": {}
}
```

校验规则：

- title/objective/scope/nodes 必需。
- DAG 无环、依赖存在、节点 id 唯一（复用 `DAGScheduler.validate`）。
- 每个 agent 节点必须有机器可验证的 `acceptance_criteria`。
- 支持条件：`exit_code`（Agent 退出码，或带 `command` 的命令退出码）、`output_contains`、`output_not_contains`、`artifact_exists`、`artifact_contains`。
- 顶层 `acceptance_criteria` 必须被某个节点的验收条件覆盖，保证最终交付可验证。
- 存在未解决的 L3 Clarification 时禁止确认。

## 5. Decision Policy

代码在 `planning/policy.py`，不是 Prompt 建议。

| 级别 | 行为 |
|------|------|
| L0 明确需求 | 不提问，不重复确认 |
| L1 普通技术实现 | 自动决策，记录 decisions |
| L2 低风险不确定 | 优先调研，采用推荐/现有规范，记录 decisions |
| L3 重大决策 | 必须有调研证据、影响说明、2-4 个选项和推荐项，才进入 Clarification |

额外规则：

- 没有 research 的 L3 不提问，记录 suppressed。
- 没有 impact 的 L3 不提问。
- 少于 2 个选项自动决策。
- 推荐项不在选项内则丢弃。
- 重复问题、已回答问题不再提问。
- 最多 5 个问题，超出进入 suppressed。
- 用户授权可降级“非风险型 L3”，但风险关键词（数据库、API、删除、权限、兼容、范围、返工等）仍为 L3。

## 6. Snapshot 与不可变契约

- 确认时在单个事务内插入 `plan_snapshots`、`plan_versions`，并更新 `plans` 状态/版本。
- `content_hash` 为规范化 JSON 的 SHA-256，排除易变 metadata 时间戳。
- SQLite Trigger 禁止 UPDATE/DELETE `plan_snapshots` 与 `plan_versions`。
- Workflow 通过 `plan_id / plan_snapshot_id / plan_version` 绑定 Snapshot。
- 同一 Snapshot 只能绑定一个 Workflow（唯一索引）。
- 执行中计划变化生成新版本 + 新 Snapshot，旧 Snapshot 保留。

## 7. Plan Change Request

Agent 在 `<workdir>/.devflow/plan_changes/<node_id>.json` 写入：

```json
{
  "title": "Add a migration node",
  "reason": "the existing schema lacks a column",
  "impact": "one extra step before tests",
  "risk_level": "LOW|MAJOR",
  "affected_nodes": ["implement"],
  "proposed_changes": {
    "add_nodes": [],
    "update_nodes": [],
    "remove_nodes": []
  }
}
```

处理：

- Core 重新分类风险（Agent 不能把结构性变更降级为 LOW）。
- LOW：记录并立即应用，生成新版本/Snapshot，继续执行。
- MAJOR：创建 Workflow 级 HITL Decision；当前执行批次继续，Workflow 在无其他可执行工作时进入 `WAITING_HUMAN`。
- 批准：应用变更、生成新 Snapshot、绑定 Workflow、仅执行新增/受影响节点。
- 拒绝：保持原计划，记录 REJECTED。
- 已完成且未受影响的节点不重跑；受影响已完成节点生成 `<id>-rev<version>` 替代节点，旧节点保留在 Workflow 历史中。
- 变更历史保存在 `plan_change_requests` 与 workflow 事件中。

## 8. 数据库

Migration v4：

| 表/列 | 作用 |
|-------|------|
| `plans` | Plan 主状态、当前版本、Workflow 绑定 |
| `plan_clarifications` | L3 问题、调研、选项、回答 |
| `plan_versions` | 历史版本（不可变） |
| `plan_snapshots` | 确认快照（不可变） |
| `plan_change_requests` | 执行期变更、风险、决策、新版本 |
| `workflows.plan_id/plan_snapshot_id/plan_version` | Snapshot 契约绑定 |

## 9. CLI

```bash
devflow plan create "<目标>" [--title ...] [--context JSON] [--authorization ...]
devflow plan show <plan_id>
devflow plan clarify <plan_id>
devflow plan answer <plan_id> <clarification_id> "<回答>"
devflow plan validate <plan_id>
devflow plan confirm <plan_id>
devflow plan snapshot <plan_id>
devflow plan history <plan_id>
devflow plan changes <plan_id>
devflow workflow start --plan <plan_id>
```

- Plan 阶段允许用户交互；Execute 阶段普通实现细节不再询问用户。
- 执行期重大问题通过 HITL Decision 或 Plan Change Request。
- `plan confirm` 需要 `PLAN_READY` 且无待回答问题。
- `workflow start --plan` 使用现有 Engine 租约、调度、WCP、Recovery、Verifier。

## 10. 信任边界

- Planner 以 `--permission-mode plan` 运行，只读调研；不修改仓库。
- Plan 输出必须通过 Schema/DAG/验收校验，模型不能直接写 Workflow 状态。
- 已确认 Snapshot 由数据库 Trigger 保护。
- Plan Change Request 文件大小/JSON/字段校验；风险由 Core 判定。
- `exit_code` 带 `command` 的验收条件会在 workdir 内以 `shell=False` 执行已确认计划中的命令；这是用户在确认 Plan 时授权的执行边界。

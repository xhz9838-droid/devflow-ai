# DF-018 实施报告：Planning Layer 与 Plan / Execute 双阶段

- 日期：2026-11-21
- 状态：**完成**
- 默认测试：`158 passed, 5 skipped in 50.88s`
- 真实 Claude 规划 + 执行：`1 passed in 144.12s`
- 证据：`docs/implementation/evidence/DF-018/`
- 建议提交：`feat: add planning layer and plan/execute workflow`

## 1. 实现能力

| 能力 | 实现 |
|------|------|
| 需求调研 | `RepositoryContext` 读取 Git/文件/README/pyproject；Planner 只读调研 |
| 结构化 Plan | `planning/validator.py` Schema + DAG + 验收校验 |
| Decision Policy | `planning/policy.py` 代码级 L0-L3、调研优先、批量限制、授权边界 |
| Clarification | 持久化问题/调研/选项/推荐/回答；回答后重规划 |
| Plan 版本 | `plans` + `plan_versions` |
| 不可变 Snapshot | `plan_snapshots` + 事务确认 + 内容 Hash + SQLite Trigger |
| Plan/Execute 绑定 | `workflows.plan_id/plan_snapshot_id/plan_version` + 唯一索引 |
| Workflow 启动 | `engine.start_workflow_from_plan` 复用现有 Core |
| Plan Change Request | 文件协议 + Core 风险分类 + 低风险应用 / 重大 HITL + 新 Snapshot |
| 节点重跑控制 | 未受影响已完成节点不重跑；受影响节点生成替代节点 |
| CLI | `plan create/show/clarify/answer/validate/confirm/snapshot/history/changes` + `workflow start --plan` |
| 命令验收 | `exit_code` 支持 `command`，Verifier 在 workdir 内执行并检查退出码 |

## 2. 模块边界

```
devflow/planning/
├── models.py      Plan 状态机与数据模型
├── policy.py      L0-L3 Decision Policy、变更风险分类
├── validator.py   Plan Schema/DAG/验收、Hash、Node 转换
├── store.py       plans/clarifications/versions/snapshots/changes 的 SQL Repository
├── planner.py     Planner 接口、仓库上下文、ClaudeCodePlanner
├── manager.py     生成/回答/重规划/校验/确认/历史
└── changes.py     Plan Change Request 执行期变更
```

- `engine.py` 只增加 Snapshot → Workflow 入口、运行中变更钩子和 Prompt 协议段。
- `state.py` 只增加 Workflow Plan 绑定列、节点定义更新和 Snapshot 重绑定。
- Planning Layer 不直接改 Workflow 状态；所有执行仍由 Core/SQLite 管理。

## 3. Plan / Execute 状态流转

```
DRAFT → INVESTIGATING → ANALYZING
      → NEEDS_CLARIFICATION → ANALYZING
      → PLAN_READY → CONFIRMED → EXECUTION_READY
```

- `FAILED` 可重新规划；`CANCELLED` 终态。
- `CONFIRMED` 后 Plan 内容不能直接修改，必须生成新版本/Snapshot。
- `EXECUTION_READY` 表示已绑定 Workflow。

## 4. Decision Policy 实际行为

- L0/L1/L2 不进入用户问题列表，记录到 `plan.decisions`。
- L3 必须有调研证据、影响说明、2-4 个选项、推荐项。
- 缺少证据/影响、推荐项非法、单选项、重复问题、已回答问题、超出批量上限 → 不提问；记录 suppressed 或自动决策。
- 默认最多 5 个问题。
- 用户授权只降级非风险型 L3；数据库/API/删除/迁移/权限/安全/兼容/范围/返工等风险词仍为 L3。
- 变更请求的风险由 Core 重新分类，Agent 声明 LOW 不能绕过结构风险。

## 5. Execution Plan 与校验

Plan 包含 scope、assumptions、decisions、risks、nodes、acceptance、delivery requirements。

校验：

- title/objective/scope/nodes 必需。
- DAG 无环、依赖存在、id 唯一。
- 每个 agent 节点必须有机器可验收条件。
- 支持 `exit_code`（Agent 或命令）、`output_contains`、`output_not_contains`、`artifact_exists`、`artifact_contains`。
- 顶层验收条件必须被某个节点覆盖，保证最终可验证。
- 未解决 L3 问题禁止确认。

## 6. Snapshot 持久化与不可变

- 确认在单个事务内写 Snapshot、Version，并更新 Plan 状态/版本。
- Hash 为规范化 JSON SHA-256，排除易变 metadata 时间戳。
- SQLite Trigger 禁止 UPDATE/DELETE Snapshot 与 Version。
- 同一 Snapshot 只允许绑定一个 Workflow（唯一索引）。
- 执行中计划变化生成新版本；旧 Snapshot 和历史保留。

## 7. Plan Change Request

Agent 通过 `<workdir>/.devflow/plan_changes/<node_id>.json` 提交：

```json
{
  "title": "...",
  "reason": "...",
  "impact": "...",
  "risk_level": "LOW|MAJOR",
  "affected_nodes": ["..."],
  "proposed_changes": {
    "add_nodes": [],
    "update_nodes": [],
    "remove_nodes": []
  }
}
```

- LOW：Core 记录并应用，生成新 Snapshot，继续执行。
- MAJOR：创建 Workflow 级 HITL Decision；批准后应用，拒绝保持原计划。
- 已完成且未受影响的节点不重跑。
- 受影响的已完成节点生成 `<id>-rev<version>` 替代节点，旧节点保留。
- 变更历史在 `plan_change_requests` 与 Workflow 事件中可追溯。
- 非法/失败变更不会伪造成功；记录 FAILED 原因。

## 8. CLI 示例

```bash
python -m devflow --workdir . plan create "Add calculator.py ..." \
  --authorization "可自主决定普通技术细节"

python -m devflow plan show <plan_id>
python -m devflow plan clarify <plan_id>
python -m devflow plan answer <plan_id> <clarification_id> "keep compatible"
python -m devflow plan validate <plan_id>
python -m devflow plan confirm <plan_id>
python -m devflow plan snapshot <plan_id>
python -m devflow plan history <plan_id>
python -m devflow plan changes <plan_id>

python -m devflow workflow start --plan <plan_id>
```

`plan answer` 默认自动重规划；`--no-refine` 可仅保存回答。

## 9. SQLite Migration

Migration v4：

- `plans`
- `plan_clarifications`
- `plan_versions`
- `plan_snapshots`
- `plan_change_requests`
- `workflows.plan_id / plan_snapshot_id / plan_version`
- `plan_snapshots`、`plan_versions` 不可变 Trigger
- Snapshot 绑定唯一索引

SQLite 仍是唯一权威状态；未引入第二套数据库或调度器。

## 10. 测试结果

默认完整套件：

```bash
python -m pytest -q
```

```
158 passed, 5 skipped in 50.88s
```

- 原有 P0 / P0.5 的 112 个用例全部保持通过。
- 新增 46 个 DF-018 单元/集成用例：Decision Policy、Plan 校验、PlanManager、Snapshot 不可变、Plan/Execute 集成、Plan Change、命令验收、并发变更、并发 Workflow 创建、终态节点重新加入。
- 5 个 skipped 为真实 Claude 测试（DF-012、WCP、动态 HITL、最终 E2E、DF-018 真实规划）；需 `DEVFLOW_REAL_CLAUDE=1`。

专项测试（开发过程中真实执行）：

```
新规划测试 45 passed in 10.45s
```

## 11. 真实 Claude Agent 验证

真实测试：`tests/integration/test_real_planning.py`，`1 passed in 144.12s`。

证据 `evidence/DF-018/`：

| 项目 | 实际值 |
|------|--------|
| Planner | 真实 Claude，只读调研 |
| Plan 节点 | `implement_calculator`、`add_pytest_tests`、`run_pytest` |
| 自动决策 | 4 条 L1/L2 decisions |
| Clarification | 0（目标明确 + 授权充分） |
| Snapshot | v1，稳定 Hash |
| 执行 Agent | 3 个真实 Agent Run，全部 SUCCEEDED |
| Verifier | `python -m pytest tests -q` → `2 passed` |
| 生成文件 | `calculator.py`、`tests/test_calculator.py` |
| Workflow | `COMPLETED`，绑定 `plan_id`/`snapshot_id`/`version` |
| Plan 状态 | `EXECUTION_READY` |

这证明用户目标 → 调研 → 决策策略 → 结构化 Plan → Snapshot → 多 Agent 执行 → Verifier → 完成 的真实链路成立。

## 12. P0 / P0.5 回归

- 全量测试包含原有生命周期、SQLite、DAG、WCP、Recovery、HITL、Verifier、Lease、进程生命周期、真实 E2E 相关用例。
- 默认套件通过；原有真实测试仍保留并通过显式开关运行。
- 未删除、跳过或弱化任何旧测试。

## 13. 独立审查修复

审查发现并已修复：

| 级别 | 问题 | 修复 |
|------|------|------|
| HIGH | 对已完成节点的 LOW 声明变更可绕过 MAJOR | 任何 update/add 命中已完成节点都判定 MAJOR；已完成节点变更强制生成替代节点 |
| HIGH | 并行低风险变更基于旧 Plan 版本，可能丢更新 | 变更应用改为单 SQLite 事务：事务内读取最新 Plan、分配版本、合并、写 Snapshot/节点/绑定 |
| HIGH | 非法 Planner 输出使 Plan 停留在 ANALYZING | `_normalize_plan` 类型保护；生成循环捕获所有异常，两次失败后置 FAILED |
| MEDIUM | 变更应用非原子，失败后版本前进 | Plan/节点/绑定/变更状态在同一事务提交；终态 Workflow 直接拒绝 |
| MEDIUM | 已取消问题无法再次提问 | 仅抑制 ANSWERED；CANCELLED 可重新生成 |
| MEDIUM | 回答可引用其他 Plan 的 Clarification | Repository 校验 clarification 归属 |
| LOW | 并发创建 Workflow 抛出原始 IntegrityError | 捕获后返回已绑定 Workflow；Workflow+Stage+Nodes 原子创建 |
| LOW | Confirm 使用事务外旧 JSON | Confirm 在事务内读取并重新校验当前 Plan JSON |
| LOW | `plan generate --refine` 未生效；问题去重忽略标点 | CLI 读取 flag；问题 key 统一去标点/大小写/空白 |
| 残余 | CANCELLED/SKIPPED/FAILED 节点被重新加入计划时不执行 | 终态节点视为结构性变更，生成 `-rev` 替代节点；旧节点 `required=0`，不会阻塞完成 |

## 14. 已知限制

| 限制 | 说明 |
|------|------|
| Plan Change 复杂度 | 支持 add/update/remove/affected 替代节点，不支持可视化编辑或复杂 DAG 合并 |
| Planner 运行记录 | 规划调用复用 Adapter/进程管理，但不写入 workflow `agent_runs`；Plan/Snapshot 持久化 |
| 命令验收 | `exit_code.command` 在确认计划后于 workdir 执行，是明确的信任边界 |
| 风险策略 | 关键词+影响启发式，需在真实项目中校准 |
| 问题数量 | 默认最多 5 个；更多问题会被 suppressed 并记录 |
| 部分失败变更 | 变更应用中途失败会标记 FAILED；已完成的节点更新可能已部分生效，需人工检查 |
| 真实测试 | 需要本机 Claude 认证/网络，默认跳过 |
| 多用户/Web | 不在 DF-018 范围 |

## 15. 后续建议

1. P1-1：接入 Codex Planner/Adapter，验证 capabilities 差异。
2. P1-2：Provider 体系（禅道/钉钉/Git/CI），Plan 来源与通知解耦。
3. 增强 Plan Change 的 DAG 合并与影响分析。
4. 将命令验收限制为白名单/沙箱，降低 Plan 确认后的执行面。
5. 将真实 Agent 测试接入受控 CI（显式开关 + 成本上限）。
6. 为 Planner 运行增加独立审计记录（不绑定 Workflow Node）。

## 16. Git

建议提交：

```
feat: add planning layer and plan/execute workflow
```

包含：Planning Layer、v4 Migration、Engine/CLI 集成、测试、DF-018 文档、真实证据。

未推送远端；`docs/chat_decision/` 用户记录保持原样。

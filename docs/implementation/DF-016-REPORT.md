# DF-016：运行中动态人工决策报告

- 日期：2026-11-21
- 结论：**通过**。真实 Claude Code 在 Agent Run 中主动写出决策请求，Core 持久化并暂停节点；CLI 决策后新的 Agent Run 读取 `approved_decision` 并完成原 Workflow。
- 测试：真实 CLI `1 passed in 44.57s`；默认套件 `108 passed, 2 skipped`
- 证据：`docs/implementation/evidence/DF-016/`

## 1. 受控请求协议

Agent 通过在工作目录写入 JSON 文件请求决策：

```
<workdir>/.devflow/decisions/<node_id>.json
```

字段：

```json
{
  "question": "Approve writing the result?",
  "decision_type": "approval",
  "reason": "Human approval is required before writing",
  "context": {"detail": "..."},
  "options": [{"id": "approve", "label": "Approve"}],
  "recommended": "approve",
  "risk": "low"
}
```

约束：

- 文件最大 64KB。
- `question`（或 `title`）必填。
- `recommended` 必须是 options 中的 id。
- 文件名对 node_id 做安全字符替换，防止路径穿越。
- 成功持久化后文件移动到 `.devflow/decisions/archive/`，避免重复处理。
- 非法 JSON / 非法字段会使节点失败，不会静默忽略。

## 2. Core 处理流程

```
Agent Run 结束
  → 读取决策请求文件
  → 校验并持久化 Decision（含 reason/recommended/risk/context）
  → 节点 RUNNING → WAITING_HUMAN
  → Workflow WAITING_HUMAN
  → 其他独立节点继续执行
  → CLI resolve
  → 节点 WAITING_HUMAN → READY
  → 新 Agent Run，Continuation Context 含 approved_decision
  → Verifier → Workflow COMPLETED
```

- 通知不参与决策状态：决策以 SQLite 记录为准。
- 依赖该节点的后续节点在等待期间为 `BLOCKED`；决策完成后重新计算 DAG。
- 无依赖的 READY 节点不受影响。
- 决策选项校验、超时、取消、终态清理沿用 DF-016 之前的 HITL 实现。

## 3. 真实执行证据

真实测试 `tests/integration/test_real_hitl.py`：

| 阶段 | 证据 |
|------|------|
| Phase 1 | Workflow `WAITING_HUMAN`，节点 `WAITING_HUMAN`，Decision `PENDING`，`result.txt` 不存在 |
| 决策 | CLI 独立进程 `resolve --option approve`，返回码 0 |
| Phase 2 | CLI 独立进程 `resume`，新的 Agent Run |
| 完成 | Workflow `COMPLETED`，节点 `SUCCEEDED`，`result.txt` == `APPROVED` |
| Agent Run | 2 个，均 `SUCCEEDED`，均有 pid/process_start_time |
| Context | 第二个 Run 的 Prompt 含 `"selected_option": "approve"`，第一个不含 |
| 事件 | `human_decision.requested → human_decision.agent_requested → human_decision.resolved → agent.started → workflow.completed` |

证据文件：`phase1-inspect.json`、`final-inspect.json`、`decision.json`、`runs.json`、`result.txt`、`resolve.txt`、`resume.txt`、`pytest.log`。

## 4. 测试覆盖

| 场景 | 测试 |
|------|------|
| Agent 动态请求决策 → 等待 → 决策 → 新 Run → 完成 | `test_agent_dynamic_decision_parks_node_and_resumes` |
| 等待节点不阻塞独立节点；依赖节点 BLOCKED | 同上 + `test_waiting_human_node_does_not_block_independent_node` |
| 非法 JSON | `test_invalid_decision_request_fails_the_node` |
| 缺少 question | `test_decision_request_without_question_fails_the_node` |
| 路径安全 | `test_decision_request_path_is_sanitized` |
| 真实 CLI 全流程 | `test_real_agent_requests_human_decision_and_resumes` |

## 5. 已知限制

- 协议基于工作目录文件，而非 MCP/通知系统；符合 P0.5“不依赖通知系统实现决策”的约束。
- Agent 必须遵循 Prompt 中的协议说明；若 Agent 忽略协议直接执行，Core 无法强制其暂停（但验收仍由 Verifier 决定）。
- 决策请求文件属于受控输入，已限制大小并校验 JSON/字段；不执行文件中任何代码。
- 同一节点在决策解决后再次写请求会创建新决策（新的业务请求），不是重复恢复。

## 6. 下一阶段

DF-017：综合可靠性测试（多进程、强制退出、孤儿进程、锁竞争、故障注入）与 P0.5 最终真实 E2E 验收。

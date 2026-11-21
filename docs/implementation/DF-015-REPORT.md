# DF-015：WCP 真实上下文续跑报告

- 日期：2026-11-21
- 结论：**通过**。真实 Claude Code 在两个独立 Agent Run 之间通过结构化 Continuation Context 完成交接；已完成节点未重跑，最终 Workflow `COMPLETED`。
- 测试：真实 CLI `1 passed in 28.50s`
- 证据：`docs/implementation/evidence/DF-015/`

## 1. 真实流程

```
engine.start
  → research (Agent Run 1, 真实 Claude)
  → handoff 节点等待人工决策 → Workflow WAITING_HUMAN
CLI resolve（独立进程）
CLI resume（独立进程，重新获取 Engine 租约）
  → Recovery 读取 SQLite
  → 构建 Continuation Context（含 research 输出）
  → handoff (Agent Run 2, 真实 Claude)
  → Verifier 通过 → Workflow COMPLETED
```

## 2. Continuation Context 内容

`devflow/continuation.py` 的 `completed_nodes` 现在包含：

- 节点 id / name
- 结构化输出中的 `result` 文本（Claude JSON 的 `result` 字段）
- `verification_passed`
- `artifacts`
- `idempotency_key`

同时保留目标、进度、当前节点、待决策、阻塞节点、副作用、未解决问题、下一步动作、最近事件。

真实证据中，`research` 的输出为 `WCP-1E9CD214E0`，`handoff` 的 Prompt 包含该 token，最终 `handoff.txt` 内容完全一致。

## 3. 真实结果

| 检查项 | 实际值 |
|--------|--------|
| Workflow | `COMPLETED` |
| Node | research `SUCCEEDED`，handoff `SUCCEEDED` |
| Agent Run 数 | 2（research 1 次，handoff 1 次） |
| 两个 Run 的进程身份 | pid 10832 / 17512，均有 process_start_time |
| research 重跑 | 0 次 |
| Checkpoint 数 | 7 |
| handoff token | 与 research 输出完全一致 |
| 决策 | `human_decision.resolved` 审计事件存在 |
| 真实验证 | `artifact_contains` 校验 handoff.txt 内容 |

## 4. 发现并修复的问题

1. **自定义 Prompt 丢失 Continuation Context**：原 `_build_prompt` 在存在 `execution_config.prompt` 时直接返回自定义文本，导致 Agent 看不到已完成节点。已改为自定义 Prompt 后始终追加结构化上下文。
2. **Windows 命令行长度限制**：32K 上下文作为命令行参数会接近/超过 Windows 限制。Adapter 改为通过 stdin 传递 Prompt（`claude -p --output-format json`），不再受命令行长度限制。
3. **CLI 输出编码**：Windows 控制台默认 GBK，Agent 输出含 Unicode 时 `resume` 打印崩溃。CLI 启动时将 stdout/stderr 重配置为 UTF-8。

## 5. 测试

- 真实测试：`tests/integration/test_real_wcp.py`（默认跳过，需 `DEVFLOW_REAL_CLAUDE=1`）
- 单元测试：自定义 Prompt 仍包含 Continuation Context；已完成节点输出提取为交接文本。

## 6. 已知限制

- Context 中单节点输出截断为 4000 字符，整体 Prompt 上下文上限 32000 字符；超长交付物需落盘并通过 artifacts 引用。
- 交接依赖 Claude 按 Prompt 使用 Continuation Context；未启用 Agent 会话级 `--resume`，而是每次创建新 Agent Run（符合 WCP 设计）。
- 对于无 `result` 字段的 Adapter 输出，退回原始 stdout 文本。

## 7. 下一阶段

DF-016：Agent 运行中主动请求人工决策，决策后恢复原 Workflow。

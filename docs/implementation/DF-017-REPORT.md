# DF-017：P0.5 可靠性专项测试报告

- 日期：2026-11-21
- 结论：**通过**。17 类关键可靠性场景均有自动化或真实 Agent 覆盖；默认套件 `112 passed, 4 skipped`。
- 真实最终验收：`1 passed in 70.70s`（`tests/integration/test_p05_final_e2e.py`）
- 证据：`docs/implementation/evidence/P0.5-FINAL/`

## 1. 场景覆盖矩阵

| # | 场景 | 测试 |
|---|------|------|
| 1 | Engine 正常执行 | 全部 E2E / 真实测试 |
| 2 | Engine 进程强制退出 | `test_engine_crash_then_restart_recovers`（子进程 kill） |
| 3 | Engine 重启后恢复 Workflow | 同上 + 最终真实 E2E |
| 4 | Agent 进程仍存活时恢复 | `test_live_agent_survives_engine_crash_and_recovery_waits` |
| 5 | Agent 已退出但 DB 状态未推进 | 同上 + `test_successful_agent_is_verified_instead_of_rerun` |
| 6 | Agent 超时 | `test_claude_adapter_persists_pid_and_kills_on_timeout` |
| 7 | 孤儿进程检测 | `test_live_agent...` + `find_orphans` |
| 8 | 同一 Workflow 重复执行恢复 | `test_engine_crash...` 重复 resume；E2E |
| 9 | 两个 Engine 并发启动 | `test_engine_start_is_locked_while_another_engine_runs`、`test_concurrent_acquire_only_one_wins`、多进程租约测试 |
| 10 | Checkpoint 写入失败 | `test_checkpoint_failure_is_not_swallowed` |
| 11 | 人工决策后 Engine 重启 | 真实 DF-016（CLI resolve + 独立进程 resume） |
| 12 | Agent 已完成但 Node 未推进 | `test_successful_agent_is_verified_instead_of_rerun` |
| 13 | 已完成节点不能无条件重跑 | 最终 E2E 调用次数；`test_external_workdir_change...` |
| 14 | 外部修改工作目录后恢复 | `test_external_workdir_change_does_not_rerun_completed_node` |
| 15 | Verifier 失败后的状态处理 | `test_verifier_failure_marks_node_failed` |
| 16 | Workflow 终态清理 | `test_failed_workflow_closes_pending_decisions_and_nodes` |
| 17 | 并行节点中一个等待人工决策 | `test_agent_dynamic_decision_parks_node_and_resumes`、`test_waiting_human_node...` |

## 2. 多进程与故障注入

| 测试 | 方式 | 验证 |
|------|------|------|
| `test_engine_crash_then_restart_recovers` | 独立子进程运行 Engine，父进程 kill | 租约过期接管、Recovery 标记 INTERRUPTED、重试、完成、重复 resume 不重跑 |
| `test_live_agent_survives_engine_crash_and_recovery_waits` | 子进程 Engine + 真实操作系统子进程 Agent，kill Engine | Agent 存活时 Recovery 返回 active，不重复启动；终止后重试完成 |
| `test_lease_recovers_after_holder_process_dies` | 独立子进程持有租约后被 kill | 到期后安全接管，无双主控 |
| 最终真实 E2E | CLI 子进程启动 Engine，强制 kill + 终止 Agent | 真实 Claude 任务恢复、DAG 续跑、动态决策、完成 |

## 3. 测试结果

默认套件：

```bash
python -m pytest -q
```

```
112 passed, 4 skipped in 42.20s
```

4 个 skipped 为真实 Claude 测试（需要 `DEVFLOW_REAL_CLAUDE=1`）：

| 测试 | 结果 |
|------|------|
| `test_real_claude_creates_and_verifies_an_artifact` | `1 passed in 14.52s` |
| `test_real_wcp_handoff_across_agent_runs` | `1 passed in 28.50s` |
| `test_real_agent_requests_human_decision_and_resumes` | `1 passed in 44.57s` |
| `test_p05_final_real_e2e` | `1 passed in 70.70s` |

多进程专项：

```
tests/test_reliability.py     4 passed in 6.55s
tests/test_lease.py           5 passed in 2.73s
```

## 4. 故障注入点

- Engine 进程 `kill`
- Agent 进程树终止
- Agent 存活/死亡/PID 复用
- 租约持有进程异常退出
- Checkpoint 写入异常（单元注入）
- 非法/缺失决策请求
- Verifier 失败
- 终态后重复 resume
- 工作目录外部修改

## 5. 已知限制

- 跨主机不适用：PID/创建时间身份和 SQLite 租约均为本机单库模型。
- Engine 死亡但 Agent 存活时没有独立监督进程；Recovery 会等待其退出，再重试节点（幂等键保护副作用）。
- 真实 Claude 测试依赖本机认证和网络，默认跳过，不进入普通 CI。
- Windows 优雅终止依赖 `taskkill`；失败时强制终止。
- 无分布式 Leader Election、无多主控集群。

## 6. 下一阶段

P0.5 已具备进入 P1 的条件；最终验收见 `docs/implementation/P0.5-ACCEPTANCE-REPORT.md`。

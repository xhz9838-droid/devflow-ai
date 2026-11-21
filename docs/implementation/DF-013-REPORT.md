# DF-013：Agent 进程生命周期管理报告

- 日期：2026-11-21
- 结论：**通过**。Agent 进程的 PID、创建时间身份、存活判断、超时终止、子树终止、取消终止与孤儿检测均已实现并通过真实/自动化测试。
- 测试：`97 passed, 1 skipped`（默认套件）；真实 Claude 测试 `1 passed in 18.02s`
- 证据：`docs/implementation/evidence/DF-013/`

## 1. 实现内容

### 1.1 进程身份持久化

Schema v2 为 `agent_runs` 增加 `process_start_time`。Adapter 启动子进程后立即回调 Execution Manager，写入：

- `pid`
- `process_start_time`（平台创建时间令牌）
- `agent.process_started` 审计事件

真实运行证据：`pid=33664`，`process_start_time=win:134397247746887993`。

### 1.2 进程身份与存活判断

新增 `devflow/platform_proc.py`：

| 平台 | 身份来源 | 存活判断 |
|------|---------|---------|
| Windows | `GetProcessTimes` 创建时间（FILETIME） | `GetExitCodeProcess == STILL_ACTIVE` 且创建时间匹配 |
| Linux | `/proc/<pid>/stat` 第 22 字段 starttime | 非 zombie 且 starttime 匹配 |
| macOS/其他 POSIX | `ps -o lstart=` | 输出存在且匹配 |

**PID 复用防护**：所有存活与终止判断都带 `expected_start_time`，身份不匹配即视为原进程已退出，绝不按 PID 盲目终止。

### 1.3 进程终止

- POSIX：子进程以 `start_new_session=True` 启动，按进程组 `SIGTERM`（优雅）/ `SIGKILL`（强制）终止；仅当 `pgid == pid` 时使用 `killpg`，避免误杀当前进程组。
- Windows：`taskkill /PID <pid> /T`（优雅）/ 加 `/F`（强制）终止进程树；失败时回退到 `TerminateProcess`。
- Adapter 超时处理：先优雅终止，等待最多 2 秒，再强制终止；最后读取剩余 stdout/stderr 并抛出 `AgentTimeout`。
- 重复终止：进程不存在或身份不匹配时返回 `False`，不产生状态异常。

### 1.4 取消与状态收敛

`WorkflowEngine.cancel` 先调用 `AgentExecutionManager.terminate` 终止活动进程，再关闭决策、取消节点、进入 `CANCELLED`。Agent Run 置为 `CANCELLED`；Adapter 返回后的迟到结果写入 `agent.late_result`，节点状态不再被覆盖（`node.result_ignored`）。

### 1.5 Recovery 与孤儿检测

- 有 PID + 身份的运行：进程存活 → 返回 `active`，不重试、不重复启动；进程不存在或 PID 复用 → `INTERRUPTED` → 按重试策略处理。
- 无 PID 的运行：回退到心跳/租约窗口判断。
- `RecoveryManager.find_orphans` 与 `workflow inspect` 的 `orphans` 字段可列出进程已消失但仍为 `RUNNING` 的 Agent Run。

## 2. 测试覆盖

| 场景 | 测试 |
|------|------|
| 正常执行/退出 | `test_real_claude`、`test_e2e` |
| 进程身份与 PID 复用 | `test_current_process_identity_and_reuse_detection` |
| 子进程树终止 | `test_terminate_process_tree_kills_children` |
| 重复终止 | `test_terminate_is_idempotent` |
| Adapter 超时终止 | `test_claude_adapter_persists_pid_and_kills_on_timeout` |
| 进程身份持久化 | `test_manager_persists_process_identity`、`test_agent_run_process_identity_is_persisted` |
| 取消活动 Agent | `test_engine_cancel_terminates_live_agent_process` |
| 存活/死亡进程恢复 | `test_recovery_distinguishes_live_and_dead_agent_process` |
| 孤儿检测 | 同上，`find_orphans` |
| 真实 CLI | `evidence/DF-013/`（pid + 创建时间已落库） |

## 3. 真实执行证据

```bash
DEVFLOW_REAL_CLAUDE=1 DEVFLOW_EVIDENCE_DIR=docs/implementation/evidence/DF-013 \
python -m pytest -q tests/integration/test_real_claude.py -s
```

```
1 passed in 18.02s
```

证据包含 `inspect.json`、`agent-run.json`、`artifact.txt`、`pytest.log`。

## 4. 已知限制

- 主控进程被强制杀死而 Agent 子进程仍存活时，没有独立监督进程在子进程退出后写回结果；重启后的 Recovery 会识别“进程已退出”并重试节点，配合 `idempotency_key` 避免重复副作用。
- Windows 下“优雅终止”依赖 `taskkill` 对控制台进程的行为；失败时回退强制终止。
- 跨主机进程不适用（PID/创建时间仅本机有效）；P0.5 定位单机。
- 进程身份读取失败（权限/平台不支持）时不做盲目终止，回退到心跳判断。

## 5. 下一阶段

DF-014：SQLite 级别的单 Engine 运行租约，防止同一数据库被两个调度主控同时占用。

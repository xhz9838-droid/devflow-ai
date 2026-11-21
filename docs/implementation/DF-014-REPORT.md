# DF-014：单 Engine 实例运行保护报告

- 日期：2026-11-21
- 结论：**通过**。同一 SQLite 数据库只允许一个活动 Engine 调度；并发、过期接管、正常释放和多进程异常退出均已验证。
- 测试：`5 passed in 2.73s`（租约专项，含多进程）
- 代码：`devflow/lease.py`、`devflow/db.py` v3 迁移、`devflow/engine.py`

## 1. 设计

新增单行表 `engine_locks`：

```sql
CREATE TABLE engine_locks (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  owner_id TEXT NOT NULL,
  hostname TEXT NOT NULL,
  pid INTEGER NOT NULL,
  acquired_at TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  released_at TEXT
)
```

`EngineLease`：

- `acquire()`：在 `BEGIN IMMEDIATE` 事务中读取当前租约；有效租约且 owner 不同 → `EngineLocked`；否则 CAS 更新/插入。
- 续租线程：每 `TTL/3` 更新 `heartbeat_at/expires_at`。
- 调度轮次：`WorkflowEngine._run` 每轮调用 `ensure_held()`，租约丢失立即中止。
- `release()`：正常退出写入 `released_at`。
- 过期接管：`expires_at <= now` 时其他 owner 可 CAS 获取；SQLite 写事务保证只有一个赢家。

## 2. 命令边界

| 命令 | 是否获取调度租约 |
|------|-----------------|
| `start` / `resume` / `recover` | 是 |
| `status` / `inspect` / `events` / `decisions` / `list` | 否（只读） |
| `resolve` / `cancel` | 否（控制操作；cancel 会终止 Agent 进程） |
| `create` | 否（不调度） |

第二个 Engine 尝试 `start`/`resume`/`recover` 会收到包含 owner/host/pid/expires 的 `EngineLocked`。

## 3. 测试

| 测试 | 验证 |
|------|------|
| `test_acquire_blocks_second_owner` | 第二个 owner 被拒绝；释放后可获取 |
| `test_expired_lease_can_be_taken_over` | 过期租约可安全接管 |
| `test_concurrent_acquire_only_one_wins` | 8 线程并发只有 1 个赢家 |
| `test_engine_start_is_locked_while_another_engine_runs` | 真实 Engine 运行期间第二个 Engine 被拒绝，结束后可启动 |
| `test_lease_recovers_after_holder_process_dies` | 独立进程持有租约被 kill，到期后父进程接管 |

多进程测试使用 `tests/helpers/lease_holder.py` 作为独立进程。

## 4. 已知限制

- 仅单机单数据库；不实现跨主机 Leader Election。
- 租约 TTL 默认 300 秒，可通过 `DEVFLOW_ENGINE_LEASE_SECONDS` 调整；长批次依赖续租线程与每轮 `ensure_held`。
- 租约丢失后 Engine 在下一调度轮次中止，节点可能停在 RUNNING，由后续 Recovery 处理。
- `resolve`/`cancel` 不获取租约是有意设计：允许在 Engine 活动期间进行控制操作；它们不参与调度。

## 5. 下一阶段

DF-015：真实 Agent Run 之间的 WCP 上下文续跑。

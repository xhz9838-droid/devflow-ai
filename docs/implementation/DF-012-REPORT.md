# DF-012：真实 Claude Code Adapter E2E 验证报告

- 日期：2026-11-21
- 结论：**通过**。真实 Claude CLI 经 `WorkflowEngine → DAGScheduler → AgentExecutionManager → ClaudeCodeAdapter → Claude CLI → Verifier` 完成了节点并让 Workflow 进入 `COMPLETED`。
- 测试：`1 passed in 14.52s`（真实 CLI）
- 证据：`docs/implementation/evidence/DF-012/`

## 1. 环境

| 项目 | 值 |
|------|-----|
| OS | Windows 11 Pro |
| Python | 3.13.1 |
| Claude CLI | 2.1.310（`C:\Users\Administrator\npm-global\claude.CMD`） |
| 调用方式 | Python `subprocess`，`shell=False` |
| 认证 | 使用本机现有登录状态，未修改认证配置 |

Windows 下 `shutil.which("claude")` 返回 `.CMD` 文件；Python 3.13 `subprocess.run([...], shell=False)` 可直接执行，无需 `cmd.exe /c`。

## 2. 测试任务

在独立临时目录与临时 Git 仓库中执行：

- 节点 `write-artifact`
- Prompt：使用 Write 工具创建 `artifact.txt`，内容必须精确为 `DF-012-OK`
- 验收条件：
  - `exit_code == 0`
  - `artifact.txt` 存在
  - `artifact.txt` 包含 `DF-012-OK`
- 超时：240 秒
- 权限：`--permission-mode acceptEdits`（仅本次测试的 Adapter 参数）

未修改 DevFlow AI 主仓库，未操作真实业务项目，未执行部署。

## 3. 真实执行命令

```bash
DEVFLOW_REAL_CLAUDE=1 DEVFLOW_EVIDENCE_DIR=docs/implementation/evidence/DF-012 \
python -m pytest -q tests/integration/test_real_claude.py -s
```

真实输出：

```
1 passed in 14.52s
```

## 4. 真实结果

| 检查项 | 实际值 |
|--------|--------|
| Workflow | `COMPLETED` |
| Node | `SUCCEEDED` |
| Verifier | `passed=True` |
| Agent Run | `SUCCEEDED`，`exit_code=0` |
| Adapter | `claude-code` |
| Claude session_id | `84171100-6610-44c6-a6be-4cecf8123553` |
| 生成文件 | `artifact.txt` == `DF-012-OK` |
| Agent Run 数 | 1（无重复执行） |
| 事件链 | `agent.started → agent.status_changed → node.succeeded → workflow.completed` |

证据文件：

- `evidence/DF-012/command.txt`
- `evidence/DF-012/pytest.log`
- `evidence/DF-012/artifact.txt`
- `evidence/DF-012/agent-run.json`
- `evidence/DF-012/inspect.json`
- `evidence/DF-012/workflow-status.txt`

`agent-run.json` 包含真实 stdout（JSON）、session_id、退出码与 metadata；`inspect.json` 包含节点、Agent Run、Checkpoint、事件历史与 Verifier 证据。

## 5. 代码变更

| 文件 | 变更 |
|------|------|
| `devflow/verifier.py` | 新增 `artifact_contains` 验收条件 |
| `devflow/cli.py` | 支持 `DEVFLOW_CLAUDE_EXTRA_ARGS` 传递 Adapter 参数 |
| `tests/integration/test_real_claude.py` | 真实 Agent 集成测试（默认跳过） |
| `tests/test_verifier.py` | `artifact_contains` 单元测试 |
| `pyproject.toml` | 注册 `real_agent` marker |

## 6. 限制与后续

- 当前 Adapter 使用 `subprocess.run`，无法在执行期间向上层暴露 PID；进程生命周期管理属于 DF-013。
- 默认 CLI 不自动接受编辑权限；真实写入场景需显式设置 `DEVFLOW_CLAUDE_EXTRA_ARGS="--permission-mode acceptEdits"`，或在 Adapter 构造时传入参数。
- 本测试只证明最小真实链路，不代表长任务中断/恢复可靠性；该部分由 DF-013 与 DF-015 验证。

"""Child engine used by crash/restart reliability tests.

Usage:
  python hanging_engine.py <db> <workdir> fake
  python hanging_engine.py <db> <workdir> agent <fake_executable> <pid_file>
"""

import sys
import time

from devflow.agents.base import AgentAdapter, AgentResult
from devflow.agents.claude_code import ClaudeCodeAdapter
from devflow.config import DevFlowConfig
from devflow.engine import WorkflowEngine


class HangingAdapter(AgentAdapter):
    name = "hanging"

    def execute(self, request):
        time.sleep(120)
        return AgentResult(success=True)


def main() -> None:
    db, workdir, mode = sys.argv[1], sys.argv[2], sys.argv[3]
    node = {"id": "slow", "execution_config": {"timeout_seconds": 0}}
    if mode == "agent":
        executable, pid_file = sys.argv[4], sys.argv[5]
        adapter = ClaudeCodeAdapter(executable=executable)
        node["execution_config"] = {
            "timeout_seconds": 120,
            "agent_metadata": {"env": {"FAKE_PID_FILE": pid_file}},
        }
        agent_timeout = 120
    else:
        adapter = HangingAdapter()
        agent_timeout = 0
    engine = WorkflowEngine(
        DevFlowConfig(
            db_path=db,
            workdir=workdir,
            max_parallel=1,
            agent_timeout=agent_timeout,
            recovery_grace=0,
            engine_lease_seconds=2,
        ),
        adapter,
    )
    workflow = engine.create_workflow("crash test", "recover after engine crash", [node])
    print(workflow.id, flush=True)
    engine.start(workflow.id)


if __name__ == "__main__":
    main()

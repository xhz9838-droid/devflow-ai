import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from devflow.agents.base import AgentAdapter, AgentResult
from devflow.config import DevFlowConfig
from devflow.db import Database
from devflow.engine import WorkflowEngine
from devflow.errors import EngineLocked
from devflow.lease import EngineLease
from devflow.state import StateStore

ROOT = Path(__file__).resolve().parents[1]


def make_store(tmp_path):
    db = Database(tmp_path / "lease.db")
    db.migrate()
    return StateStore(db)


def wait_until(predicate, timeout=10.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_acquire_blocks_second_owner(tmp_path):
    store = make_store(tmp_path)
    first = EngineLease(store, ttl_seconds=60, owner_id="one", renew_interval=60)
    second = EngineLease(store, ttl_seconds=60, owner_id="two", renew_interval=60)
    first.acquire()
    with pytest.raises(EngineLocked) as error:
        second.acquire()
    assert error.value.owner == "one"
    first.release()
    second.acquire()
    assert second.info().owner_id == "two"
    second.release()


def test_expired_lease_can_be_taken_over(tmp_path):
    store = make_store(tmp_path)
    first = EngineLease(store, ttl_seconds=60, owner_id="one", renew_interval=60)
    second = EngineLease(store, ttl_seconds=60, owner_id="two", renew_interval=60)
    first.acquire()
    with store.db.transaction() as conn:
        conn.execute(
            "UPDATE engine_locks SET expires_at = ? WHERE id = 1",
            ("2000-01-01T00:00:00+00:00",),
        )
    second.acquire()
    assert second.info().owner_id == "two"
    first.release()
    second.release()


def test_concurrent_acquire_only_one_wins(tmp_path):
    store = make_store(tmp_path)
    leases = [
        EngineLease(store, ttl_seconds=60, owner_id=f"owner-{index}", renew_interval=60)
        for index in range(8)
    ]

    def attempt(lease):
        try:
            lease.acquire()
            return lease
        except EngineLocked:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(attempt, leases))
    winners = [lease for lease in results if lease is not None]
    assert len(winners) == 1
    winners[0].release()


class BlockingAdapter(AgentAdapter):
    name = "blocking"

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def execute(self, request):
        self.started.set()
        self.release.wait(timeout=10)
        return AgentResult(success=True, output="done", exit_code=0)


def test_engine_start_is_locked_while_another_engine_runs(tmp_path):
    adapter = BlockingAdapter()
    config = DevFlowConfig(
        db_path=tmp_path / "engine.db",
        workdir=tmp_path,
        max_parallel=1,
        agent_timeout=30,
        recovery_grace=1,
        engine_lease_seconds=60,
    )
    first = WorkflowEngine(config, adapter)
    second = WorkflowEngine(config, adapter)
    workflow_one = first.create_workflow("one", "goal", [{"id": "a"}])
    workflow_two = first.create_workflow("two", "goal", [{"id": "b"}])

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(first.start, workflow_one.id)
        assert adapter.started.wait(timeout=10)
        assert first.lease.held
        with pytest.raises(EngineLocked):
            second.start(workflow_two.id)
        adapter.release.set()
        assert future.result(timeout=20).status.value == "COMPLETED"

    adapter.release.set()
    assert second.start(workflow_two.id).status.value == "COMPLETED"


def test_lease_recovers_after_holder_process_dies(tmp_path):
    db_path = tmp_path / "multiprocess.db"
    store = StateStore(Database(db_path))
    store.db.migrate()
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    child = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "tests" / "helpers" / "lease_holder.py"),
            str(db_path),
            "2",
            "child",
            "30",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert child.stdout.readline().strip() == "READY"
        parent = EngineLease(store, ttl_seconds=2, owner_id="parent", renew_interval=60)
        with pytest.raises(EngineLocked):
            parent.acquire()

        child.kill()
        child.wait(timeout=10)

        deadline = time.time() + 10
        while True:
            try:
                parent.acquire()
                break
            except EngineLocked:
                if time.time() > deadline:
                    raise
                time.sleep(0.2)
        assert parent.info().owner_id == "parent"
        parent.release()
    finally:
        if child.poll() is None:
            child.kill()

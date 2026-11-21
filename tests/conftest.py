from __future__ import annotations

import threading

import pytest

from devflow.agents.base import AgentAdapter, AgentResult
from devflow.config import DevFlowConfig
from devflow.db import Database
from devflow.engine import WorkflowEngine
from devflow.state import StateStore


class ScriptedAdapter(AgentAdapter):
    name = "scripted"

    def __init__(self, handlers=None) -> None:
        self.handlers = dict(handlers or {})
        self.calls = []
        self.counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def execute(self, request):
        with self._lock:
            self.calls.append(request)
            count = self.counts.get(request.node_id, 0) + 1
            self.counts[request.node_id] = count
        handler = self.handlers.get(request.node_id)
        if handler is None:
            return AgentResult(success=True, output=f"ok:{request.node_id}", exit_code=0)
        if isinstance(handler, BaseException):
            raise handler
        return handler(request, count)

    def call_count(self, node_id: str) -> int:
        return self.counts.get(node_id, 0)


@pytest.fixture
def adapter():
    return ScriptedAdapter()


@pytest.fixture
def make_engine(tmp_path):
    def _make(adapter=None, **overrides):
        options = dict(
            db_path=tmp_path / "devflow.db",
            workdir=tmp_path,
            max_parallel=4,
            agent_timeout=30.0,
            recovery_grace=0.0,
            log_level="WARNING",
        )
        options.update(overrides)
        return WorkflowEngine(DevFlowConfig(**options), adapter or ScriptedAdapter())

    return _make


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / "state.db")
    db.migrate()
    return StateStore(db)


def _create(store: StateStore, specs, name="test workflow", goal="test goal"):
    workflow = store.create_workflow(name, goal)
    stage = store.create_stage(workflow.id, "main")
    store.create_nodes(workflow.id, specs, stage_id=stage.id)
    return workflow


@pytest.fixture
def create_workflow(store):
    def _factory(specs, name="test workflow", goal="test goal"):
        return _create(store, specs, name, goal)

    return _factory

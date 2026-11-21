from __future__ import annotations

import copy

from devflow.db import Database
from devflow.planning.manager import PlanManager
from devflow.planning.planner import Planner, PlannerError, PlanningResult
from devflow.planning.store import PlanStore


def condition(**kwargs):
    return dict(kwargs)


def plan_node(node_id: str, dependencies=None, **overrides):
    node = {
        "id": node_id,
        "name": node_id,
        "description": f"do {node_id}",
        "type": "agent",
        "agent": {"adapter": "claude-code", "role": "implementation agent"},
        "dependencies": list(dependencies or []),
        "inputs": {},
        "expected_outputs": [],
        "acceptance_criteria": [{"type": "exit_code", "equals": 0}],
        "retry_policy": {},
        "execution_config": {"prompt": f"execute {node_id}"},
    }
    node.update(overrides)
    return node


def valid_plan(objective: str = "implement a feature", **overrides):
    plan = {
        "title": "Test plan",
        "objective": objective,
        "project_context": {},
        "scope": {"included": ["the requested feature"], "excluded": []},
        "assumptions": [],
        "decisions": [],
        "risks": [],
        "nodes": [
            plan_node("implement"),
            plan_node("verify", dependencies=["implement"]),
        ],
        "acceptance_criteria": [{"type": "exit_code", "equals": 0}],
        "delivery_requirements": [],
        "metadata": {"created_by": "test"},
    }
    plan.update(overrides)
    return plan


class FakePlanner(Planner):
    def __init__(self, plans):
        self.plans = list(plans)
        self.calls = []

    def plan(self, request):
        self.calls.append(request)
        if not self.plans:
            raise PlannerError("no fake plan left")
        item = self.plans.pop(0)
        if isinstance(item, Exception):
            raise item
        return PlanningResult(plan=copy.deepcopy(item), raw_output="{}")


def make_manager(tmp_path, plans, workdir=None):
    db = Database(tmp_path / "plan.db")
    db.migrate()
    store = PlanStore(db)
    planner = FakePlanner(plans)
    manager = PlanManager(
        store, planner, workdir=str(workdir or tmp_path), planner_timeout=30
    )
    return manager, store, planner

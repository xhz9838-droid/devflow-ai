"""Execution Plan schema validation, DAG checks and workflow conversion."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from ..errors import DAGValidationError, ValidationError
from ..scheduler import DAGScheduler

SUPPORTED_CONDITIONS = {
    "exit_code",
    "output_contains",
    "output_not_contains",
    "artifact_exists",
    "artifact_contains",
}


class PlanValidationError(ValidationError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _validate_condition(condition: Any, where: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(condition, dict):
        return [f"{where}: acceptance condition must be an object"]
    kind = condition.get("type")
    if kind not in SUPPORTED_CONDITIONS:
        return [f"{where}: unsupported acceptance type {kind!r}"]
    if kind == "exit_code":
        expected = condition.get("equals", condition.get("expected", 0))
        if not isinstance(expected, int) or isinstance(expected, bool):
            errors.append(f"{where}: exit_code.equals must be an integer")
        if condition.get("command") is not None and not str(condition.get("command")).strip():
            errors.append(f"{where}: exit_code.command must be a non-empty string")
    elif kind in ("output_contains", "output_not_contains"):
        if not str(condition.get("value") or ""):
            errors.append(f"{where}: {kind}.value is required")
    elif kind == "artifact_exists":
        if not str(condition.get("path") or ""):
            errors.append(f"{where}: artifact_exists.path is required")
    elif kind == "artifact_contains":
        if not str(condition.get("path") or ""):
            errors.append(f"{where}: artifact_contains.path is required")
        if not str(condition.get("value") or ""):
            errors.append(f"{where}: artifact_contains.value is required")
    return errors


def validate_plan(plan: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(plan, dict):
        return ["plan must be a JSON object"]
    for key in ("title", "objective"):
        if not isinstance(plan.get(key), str) or not plan[key].strip():
            errors.append(f"plan.{key} is required")
    scope = plan.get("scope")
    if not isinstance(scope, dict) or not isinstance(scope.get("included"), list) or not scope["included"]:
        errors.append("plan.scope.included must be a non-empty list")
    for key in ("assumptions", "decisions", "risks", "delivery_requirements"):
        if plan.get(key) is not None and not isinstance(plan[key], list):
            errors.append(f"plan.{key} must be a list")

    nodes = plan.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return errors + ["plan.nodes must be a non-empty list"]

    specs = []
    node_conditions: dict[str, list[dict[str, Any]]] = {}
    for index, node in enumerate(nodes):
        where = f"node[{index}]"
        if not isinstance(node, dict):
            errors.append(f"{where}: must be an object")
            continue
        node_id = node.get("id")
        where = f"node {node_id!r}"
        if not isinstance(node_id, str) or not node_id:
            errors.append(f"{where}: id must be a non-empty string")
        if not isinstance(node.get("name"), str) or not node["name"].strip():
            errors.append(f"{where}: name is required")
        if node.get("type", "agent") != "agent":
            errors.append(f"{where}: only type=agent is supported")
        agent = node.get("agent")
        if not isinstance(agent, dict) or not str(agent.get("adapter") or ""):
            errors.append(f"{where}: agent.adapter is required")
        if not isinstance(node.get("dependencies") or [], list):
            errors.append(f"{where}: dependencies must be a list")
        if node.get("inputs") is not None and not isinstance(node["inputs"], dict):
            errors.append(f"{where}: inputs must be an object")
        if node.get("expected_outputs") is not None and not isinstance(
            node["expected_outputs"], list
        ):
            errors.append(f"{where}: expected_outputs must be a list")
        if node.get("execution_config") is not None and not isinstance(
            node["execution_config"], dict
        ):
            errors.append(f"{where}: execution_config must be an object")
        if node.get("retry_policy") is not None and not isinstance(
            node["retry_policy"], dict
        ):
            errors.append(f"{where}: retry_policy must be an object")
        criteria = node.get("acceptance_criteria")
        if not isinstance(criteria, list) or not criteria:
            errors.append(f"{where}: acceptance_criteria must be a non-empty list")
        else:
            for condition in criteria:
                errors.extend(_validate_condition(condition, where))
            node_conditions[str(node_id)] = [
                condition for condition in criteria if isinstance(condition, dict)
            ]
        specs.append(
            {
                "id": node_id,
                "dependencies": node.get("dependencies") or [],
                "execution_config": node.get("execution_config") or {},
                "retry_policy": node.get("retry_policy") or {},
                "input": node.get("inputs") or {},
            }
        )

    if specs:
        try:
            DAGScheduler.validate(specs)
        except DAGValidationError as exc:
            errors.append(f"DAG invalid: {exc}")

    top = plan.get("acceptance_criteria")
    if not isinstance(top, list) or not top:
        errors.append("plan.acceptance_criteria must be a non-empty list")
    else:
        node_condition_set = {
            _canonical(condition)
            for conditions in node_conditions.values()
            for condition in conditions
        }
        for condition in top:
            errors.extend(_validate_condition(condition, "plan.acceptance_criteria"))
            if isinstance(condition, dict) and _canonical(condition) not in node_condition_set:
                errors.append(
                    "plan.acceptance_criteria item is not verifiable by any node: "
                    + _canonical(condition)
                )
    return errors


def assert_plan_valid(plan: dict[str, Any]) -> None:
    errors = validate_plan(plan)
    if errors:
        raise PlanValidationError(errors)


def plan_content_hash(plan: dict[str, Any]) -> str:
    content = copy.deepcopy(plan)
    metadata = content.get("metadata")
    if isinstance(metadata, dict):
        for key in ("created_at", "updated_at", "confirmed_at", "snapshot_id"):
            metadata.pop(key, None)
    return hashlib.sha256(_canonical(content).encode("utf-8")).hexdigest()


def plan_node_to_spec(
    plan: dict[str, Any], node: dict[str, Any], *, default_timeout: float = 1800.0
) -> dict[str, Any]:
    objective = plan.get("objective", "")
    agent = node.get("agent") or {}
    execution_config = dict(node.get("execution_config") or {})
    execution_config["acceptance"] = list(node.get("acceptance_criteria") or [])
    if node.get("expected_outputs"):
        execution_config.setdefault("artifacts", list(node["expected_outputs"]))
    execution_config.setdefault("timeout_seconds", default_timeout)
    execution_config["plan_node_id"] = node["id"]
    if agent.get("adapter"):
        execution_config["preferred_adapter"] = agent["adapter"]
    if not execution_config.get("prompt"):
        role = agent.get("role") or "implementation agent"
        execution_config["prompt"] = (
            f"Plan objective: {objective}\n"
            f"Node: {node['name']} ({node['id']})\n"
            f"Role: {role}\n"
            f"Description: {node.get('description') or node['name']}\n"
            f"Expected outputs: {node.get('expected_outputs') or []}"
        )
    return {
        "id": node["id"],
        "name": node["name"],
        "dependencies": list(node.get("dependencies") or []),
        "execution_config": execution_config,
        "retry_policy": dict(node.get("retry_policy") or {}),
        "input": dict(node.get("inputs") or {}),
        "required": bool(node.get("required", True)),
        "idempotency_key": node.get("idempotency_key") or f"plan-node:{node['id']}",
    }


def to_workflow_nodes(
    plan: dict[str, Any], *, default_timeout: float = 1800.0
) -> list[dict[str, Any]]:
    assert_plan_valid(plan)
    return [
        plan_node_to_spec(plan, node, default_timeout=default_timeout)
        for node in plan["nodes"]
    ]

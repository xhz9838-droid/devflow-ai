"""DAG validation and dependency-driven node scheduling."""

from __future__ import annotations

from collections import deque
from typing import Any, Iterable

from .errors import DAGValidationError
from .models import Node, NodeStatus
from .state import StateStore

_DONE = {NodeStatus.SUCCEEDED, NodeStatus.SKIPPED}


def _validate_retry_policy(node_id: str, policy: dict[str, Any]) -> None:
    for key, value in policy.items():
        if key == "max_retries":
            try:
                parsed = int(value)
            except (TypeError, ValueError) as exc:
                raise DAGValidationError(
                    f"node {node_id}: retry_policy.max_retries must be an integer"
                ) from exc
            if parsed < 0:
                raise DAGValidationError(f"node {node_id}: max_retries must be >= 0")
        elif key in ("base_delay_seconds", "base_delay", "multiplier", "max_delay_seconds", "max_delay"):
            try:
                parsed = float(value)
            except (TypeError, ValueError) as exc:
                raise DAGValidationError(
                    f"node {node_id}: retry_policy.{key} must be a number"
                ) from exc
            if parsed < 0:
                raise DAGValidationError(f"node {node_id}: retry_policy.{key} must be >= 0")


def _ids_and_deps(nodes: Iterable[Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for node in nodes:
        if isinstance(node, dict):
            node_id = node.get("id")
            if not isinstance(node_id, str) or not node_id:
                raise DAGValidationError("every node needs a non-empty string id")
            deps = node.get("dependencies") or []
            if not isinstance(deps, list) or any(not isinstance(d, str) or not d for d in deps):
                raise DAGValidationError(f"node {node_id}: dependencies must be a list of node ids")
            for key in ("execution_config", "retry_policy", "input"):
                value = node.get(key)
                if value is not None and not isinstance(value, dict):
                    raise DAGValidationError(f"node {node_id}: {key} must be an object")
            _validate_retry_policy(node_id, node.get("retry_policy") or {})
        else:
            node_id = node.id
            deps = list(node.dependencies)
        if node_id in result:
            raise DAGValidationError(f"duplicate node id: {node_id}")
        result[node_id] = deps
    return result


def topological_order(nodes: Iterable[Any]) -> list[str]:
    """Kahn's algorithm. Raises DAGValidationError on cycles or unknown deps."""
    graph = _ids_and_deps(nodes)
    for node_id, deps in graph.items():
        if node_id in deps:
            raise DAGValidationError(f"node depends on itself: {node_id}")
        for dep in deps:
            if dep not in graph:
                raise DAGValidationError(f"node {node_id} depends on unknown node {dep}")

    indegree = {node_id: len(deps) for node_id, deps in graph.items()}
    children: dict[str, list[str]] = {node_id: [] for node_id in graph}
    for node_id, deps in graph.items():
        for dep in deps:
            children[dep].append(node_id)

    queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    order: list[str] = []
    while queue:
        node_id = queue.popleft()
        order.append(node_id)
        for child in sorted(children[node_id]):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if len(order) != len(graph):
        cyclic = sorted(set(graph) - set(order))
        raise DAGValidationError(f"cycle detected among nodes: {', '.join(cyclic)}")
    return order


class DAGScheduler:
    def __init__(self, store: StateStore) -> None:
        self.store = store

    @staticmethod
    def validate(specs: list[dict[str, Any]]) -> list[str]:
        return topological_order(specs)

    def refresh(self, workflow_id: str) -> list[Node]:
        """Recompute READY/BLOCKED from authoritative node statuses.

        Blocking is transitive, so a failed node blocks its whole downstream
        closure in one pass. Returns nodes that are executable right now.
        """
        nodes = self.store.list_nodes(workflow_id)
        by_id = {node.id: node for node in nodes}

        # Propagate from actual failures, not from the BLOCKED status itself:
        # a blocked node must become READY again once its upstream is retried
        # successfully.
        blocked: set[str] = {
            n.id for n in nodes if n.status in (NodeStatus.FAILED, NodeStatus.CANCELLED)
        }
        held: set[str] = {
            n.id for n in nodes if n.status is NodeStatus.WAITING_HUMAN
        }
        changed = True
        while changed:
            changed = False
            for node in nodes:
                if node.id not in blocked and any(
                    dep in blocked for dep in node.dependencies
                ):
                    blocked.add(node.id)
                    changed = True
                if node.id not in held and any(
                    dep in held for dep in node.dependencies
                ):
                    held.add(node.id)
                    changed = True

        for node in nodes:
            target: NodeStatus | None = None
            if node.status in (NodeStatus.PENDING, NodeStatus.BLOCKED):
                if node.id in blocked or node.id in held:
                    target = NodeStatus.BLOCKED
                elif all(by_id[dep].status in _DONE for dep in node.dependencies):
                    target = NodeStatus.READY
                elif node.status is NodeStatus.BLOCKED:
                    target = NodeStatus.PENDING
            elif node.status is NodeStatus.READY and node.id in blocked:
                target = NodeStatus.BLOCKED
            if target and target is not node.status:
                self.store.update_node_status(node.id, target, "scheduler")

        return self.store.list_nodes(workflow_id, NodeStatus.READY)

    def counts(self, workflow_id: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for node in self.store.list_nodes(workflow_id):
            result[node.status.value] = result.get(node.status.value, 0) + 1
        return result

    def required_succeeded(self, workflow_id: str) -> bool:
        return all(
            node.status is NodeStatus.SUCCEEDED
            for node in self.store.list_nodes(workflow_id)
            if node.required
        )

    def has_failed_required(self, workflow_id: str) -> bool:
        return any(
            node.status in (NodeStatus.FAILED, NodeStatus.CANCELLED)
            for node in self.store.list_nodes(workflow_id)
            if node.required
        )

    def has_open_work(self, workflow_id: str) -> bool:
        return any(
            node.status
            in (
                NodeStatus.PENDING,
                NodeStatus.READY,
                NodeStatus.RUNNING,
                NodeStatus.WAITING_HUMAN,
                NodeStatus.BLOCKED,
            )
            for node in self.store.list_nodes(workflow_id)
        )

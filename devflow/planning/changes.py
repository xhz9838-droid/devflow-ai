"""Controlled Plan Change Requests: low-risk apply, major HITL, new snapshot."""

from __future__ import annotations

import copy
from typing import Any

from ..errors import ValidationError
from ..hitl import HumanDecisionManager
from ..lifecycle import is_workflow_terminal
from ..models import DecisionStatus, NodeStatus, WorkflowStatus
from ..rows import load as _load
from ..state import StateStore
from .models import ChangeRisk, ChangeStatus, PlanChangeRequest
from .policy import classify_change_risk
from .store import PlanStore
from .validator import (
    PlanValidationError,
    plan_content_hash,
    plan_node_to_spec,
    validate_plan,
)

_UPDATABLE_NODE_STATUSES = {
    NodeStatus.PENDING,
    NodeStatus.READY,
    NodeStatus.BLOCKED,
    NodeStatus.WAITING_HUMAN,
}

_NODE_FIELDS = (
    "name",
    "description",
    "type",
    "agent",
    "dependencies",
    "inputs",
    "expected_outputs",
    "acceptance_criteria",
    "retry_policy",
    "execution_config",
    "required",
    "idempotency_key",
)


def _merge_node(node: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(node)
    for key in _NODE_FIELDS:
        if key in update:
            merged[key] = copy.deepcopy(update[key])
    return merged


class PlanChangeManager:
    def __init__(
        self,
        plans: PlanStore,
        workflows: StateStore,
        decisions: HumanDecisionManager,
    ) -> None:
        self.plans = plans
        self.workflows = workflows
        self.decisions = decisions

    # --------------------------------------------------------------- intake
    def handle_request(
        self, workflow_id: str, node_id: str, data: dict[str, Any]
    ) -> PlanChangeRequest:
        workflow = self.workflows.get_workflow(workflow_id)
        if not workflow.plan_id:
            raise ValidationError("plan change request received by a workflow without a plan")
        plan = self.plans.get_plan(workflow.plan_id)
        if not plan.plan_json:
            raise ValidationError("plan has no generated content")
        completed = {
            node.id
            for node in self.workflows.list_nodes(workflow_id)
            if node.status is NodeStatus.SUCCEEDED
        }
        terminal = {
            node.id
            for node in self.workflows.list_nodes(workflow_id)
            if node.status
            in (
                NodeStatus.SUCCEEDED,
                NodeStatus.FAILED,
                NodeStatus.CANCELLED,
                NodeStatus.SKIPPED,
            )
        }
        proposed = dict(data.get("proposed_changes") or {})
        risk = classify_change_risk(
            declared=data.get("risk_level"),
            title=str(data.get("title") or ""),
            reason=str(data.get("reason") or ""),
            impact=str(data.get("impact") or ""),
            proposed_changes=proposed,
            completed_node_ids=completed,
            terminal_node_ids=terminal,
        )
        change = self.plans.create_change_request(
            plan_id=plan.id,
            workflow_id=workflow_id,
            node_id=node_id,
            risk_level=risk,
            title=str(data.get("title") or "Plan change"),
            reason=str(data.get("reason") or ""),
            impact=str(data.get("impact") or ""),
            proposed_changes=proposed,
        )
        self.workflows.append_event(
            workflow_id,
            "plan.change_requested",
            {
                "change_request_id": change.id,
                "risk_level": risk.value,
                "title": change.title,
            },
            node_id=node_id,
        )
        if risk is ChangeRisk.LOW:
            try:
                self._apply(change, resolved_by="auto")
            except Exception as exc:
                self.plans.resolve_change_request(
                    change.id,
                    ChangeStatus.FAILED,
                    resolved_by="auto",
                    note=f"{type(exc).__name__}: {exc}",
                )
                self.workflows.append_event(
                    workflow_id,
                    "plan.change_failed",
                    {"change_request_id": change.id, "error": str(exc)},
                    node_id=node_id,
                )
            return self.plans.get_change_request(change.id)

        try:
            decision = self.decisions.request(
                workflow_id,
                None,
                {
                    "question": f"Approve plan change: {change.title}?",
                    "decision_type": "plan_change",
                    "options": [
                        {"id": "approve", "label": "Approve"},
                        {"id": "reject", "label": "Reject"},
                    ],
                    "recommended": "approve",
                    "reason": change.reason,
                    "risk": change.impact,
                    "context": {
                        "change_request_id": change.id,
                        "risk_level": risk.value,
                        "impact": change.impact,
                        "proposed_changes": change.proposed_changes,
                    },
                    "timeout_seconds": data.get("timeout_seconds"),
                },
            )
        except Exception as exc:
            self.plans.resolve_change_request(
                change.id,
                ChangeStatus.FAILED,
                resolved_by="core",
                note=f"could not create approval decision: {exc}",
            )
            raise
        self.plans.set_change_decision(change.id, decision.id)
        self.workflows.append_event(
            workflow_id,
            "plan.change_waiting_human",
            {"change_request_id": change.id, "decision_id": decision.id},
            node_id=node_id,
        )
        return self.plans.get_change_request(change.id)

    # --------------------------------------------------------- apply approved
    def apply_approved(self, workflow_id: str) -> list[str]:
        workflow = self.workflows.get_workflow(workflow_id)
        if not workflow.plan_id:
            return []
        applied: list[str] = []
        for change in self.plans.list_change_requests(workflow.plan_id, ChangeStatus.PENDING):
            if not change.decision_id:
                continue
            decision = self.decisions.get(change.decision_id)
            if decision.status is DecisionStatus.PENDING:
                continue
            if (
                decision.status is DecisionStatus.RESOLVED
                and decision.selected_option == "approve"
            ):
                try:
                    self._apply(change, resolved_by=decision.resolved_by or "human")
                    applied.append(change.id)
                except Exception as exc:
                    self.plans.resolve_change_request(
                        change.id,
                        ChangeStatus.FAILED,
                        resolved_by=decision.resolved_by or "human",
                        note=f"{type(exc).__name__}: {exc}",
                    )
                    self.workflows.append_event(
                        workflow_id,
                        "plan.change_failed",
                        {"change_request_id": change.id, "error": str(exc)},
                    )
            elif decision.status is DecisionStatus.RESOLVED:
                self.plans.resolve_change_request(
                    change.id,
                    ChangeStatus.REJECTED,
                    resolved_by=decision.resolved_by or "human",
                    note="change rejected by human",
                )
            else:
                self.plans.resolve_change_request(
                    change.id,
                    ChangeStatus.CANCELLED,
                    resolved_by="core",
                    note=f"decision {decision.status.value.lower()}",
                )
        return applied

    # ---------------------------------------------------------------- apply
    def _apply(self, change: PlanChangeRequest, *, resolved_by: str) -> None:
        """Plan version, snapshot, workflow nodes and binding commit atomically.

        BEGIN IMMEDIATE serializes concurrent applies; the plan row is read
        inside the transaction, so two parallel requests cannot build a new
        version from a stale base or collide on replacement ids.
        """
        with self.plans.db.transaction() as conn:
            plan_row = self.plans._plan_row(conn, change.plan_id)
            wf_row = self.workflows._workflow_row(conn, change.workflow_id)
            if is_workflow_terminal(WorkflowStatus(wf_row["status"])):
                raise ValidationError("workflow is terminal; plan change cannot be applied")
            plan_json = _load(plan_row["plan_json"], None)
            if not isinstance(plan_json, dict):
                raise ValidationError("plan has no content to change")
            current = copy.deepcopy(plan_json)
            version = int(plan_row["version"]) + 1
            proposed = change.proposed_changes or {}
            nodes = {
                node["id"]: copy.deepcopy(node)
                for node in current.get("nodes", [])
                if isinstance(node, dict) and node.get("id")
            }
            workflow_nodes = {
                row["id"]: row
                for row in conn.execute(
                    "SELECT * FROM workflow_nodes WHERE workflow_id = ? ORDER BY created_at, id",
                    (change.workflow_id,),
                ).fetchall()
            }
            terminal = {
                node_id
                for node_id, row in workflow_nodes.items()
                if NodeStatus(row["status"])
                in (
                    NodeStatus.SUCCEEDED,
                    NodeStatus.FAILED,
                    NodeStatus.CANCELLED,
                    NodeStatus.SKIPPED,
                )
            }
            updates = {
                str(update.get("id")): update
                for update in proposed.get("update_nodes") or []
                if isinstance(update, dict) and update.get("id")
            }
            additions: list[dict[str, Any]] = []
            for added in proposed.get("add_nodes") or []:
                if not isinstance(added, dict) or not added.get("id"):
                    raise ValidationError("add_nodes entries require an id")
                if added["id"] in nodes:
                    # "Add" an existing id is an update, never a duplicate insert.
                    updates[str(added["id"])] = added
                elif added["id"] in workflow_nodes:
                    status = NodeStatus(workflow_nodes[added["id"]]["status"])
                    if status is NodeStatus.RUNNING:
                        raise ValidationError(
                            f"cannot re-add node {added['id']} while it is RUNNING"
                        )
                    if status in _UPDATABLE_NODE_STATUSES:
                        nodes[added["id"]] = copy.deepcopy(added)
                    else:
                        new_id = f"{added['id']}-rev{version}"
                        replacement = copy.deepcopy(added)
                        replacement["id"] = new_id
                        metadata = dict(replacement.get("metadata") or {})
                        metadata["replaces"] = added["id"]
                        replacement["metadata"] = metadata
                        nodes[new_id] = replacement
                        replacements[added["id"]] = new_id
                        if status is not NodeStatus.SUCCEEDED:
                            self.workflows.set_node_required_in_conn(
                                conn, added["id"], False
                            )
                else:
                    additions.append(added)
            affected = set(proposed.get("affected_nodes") or [])
            affected |= {
                node_id
                for node_id in updates
                if node_id in terminal or updates[node_id].get("force_rework")
            }
            for node_id in sorted(affected | set(updates)):
                existing = workflow_nodes.get(node_id)
                if existing is not None and NodeStatus(existing["status"]) is NodeStatus.RUNNING:
                    raise ValidationError(
                        f"cannot change node {node_id} while it is RUNNING"
                    )
            replacements: dict[str, str] = {}
            for node_id in sorted(affected):
                if node_id in nodes and node_id in terminal:
                    new_id = f"{node_id}-rev{version}"
                    replacement = _merge_node(nodes[node_id], updates.get(node_id, {}))
                    replacement["id"] = new_id
                    metadata = dict(replacement.get("metadata") or {})
                    metadata["replaces"] = node_id
                    replacement["metadata"] = metadata
                    nodes[new_id] = replacement
                    del nodes[node_id]
                    replacements[node_id] = new_id
                    old_status = NodeStatus(workflow_nodes[node_id]["status"])
                    if old_status is not NodeStatus.SUCCEEDED:
                        self.workflows.set_node_required_in_conn(conn, node_id, False)

            for node_id, update in updates.items():
                target = replacements.get(node_id, node_id)
                if target in nodes:
                    nodes[target] = _merge_node(nodes[target], update)
                elif isinstance(update.get("node"), dict) and node_id not in nodes:
                    added = copy.deepcopy(update["node"])
                    added.setdefault("id", node_id)
                    nodes[added["id"]] = added

            for added in additions:
                nodes[added["id"]] = copy.deepcopy(added)

            for removed in proposed.get("remove_nodes") or []:
                if removed not in nodes:
                    continue
                dependents = [
                    node_id
                    for node_id, node in nodes.items()
                    if removed in (node.get("dependencies") or [])
                ]
                if dependents:
                    raise ValidationError(
                        f"cannot remove node {removed}; dependents: {dependents}"
                    )
                del nodes[removed]

            for node in nodes.values():
                node["dependencies"] = [
                    replacements.get(dep, dep) for dep in (node.get("dependencies") or [])
                ]
            current["nodes"] = list(nodes.values())
            metadata = dict(current.get("metadata") or {})
            metadata["changes"] = list(metadata.get("changes") or []) + [
                {
                    "change_request_id": change.id,
                    "version": version,
                    "title": change.title,
                    "risk_level": change.risk_level.value,
                    "affected_nodes": sorted(affected),
                }
            ]
            current["metadata"] = metadata
            errors = validate_plan(current)
            if errors:
                raise PlanValidationError(errors)

            stage_row = conn.execute(
                "SELECT id FROM workflow_stages WHERE workflow_id = ?"
                " ORDER BY sequence, created_at LIMIT 1",
                (change.workflow_id,),
            ).fetchone()
            stage_id = stage_row["id"] if stage_row else None
            plan_nodes = {node["id"]: node for node in current["nodes"]}
            for node_id, node in plan_nodes.items():
                spec = plan_node_to_spec(current, node)
                existing = workflow_nodes.get(node_id)
                if existing is None:
                    self.workflows.create_nodes_in_conn(
                        conn, change.workflow_id, [spec], stage_id=stage_id
                    )
                elif NodeStatus(existing["status"]) in _UPDATABLE_NODE_STATUSES:
                    self.workflows.update_node_definition_in_conn(
                        conn,
                        node_id,
                        name=spec["name"],
                        dependencies=spec["dependencies"],
                        execution_config=spec["execution_config"],
                        retry_policy=spec["retry_policy"],
                        input=spec["input"],
                        required=spec["required"],
                    )
            for node_id, row in workflow_nodes.items():
                status = NodeStatus(row["status"])
                if node_id not in plan_nodes and status in _UPDATABLE_NODE_STATUSES:
                    self.workflows.update_node_status_in_conn(
                        conn,
                        node_id,
                        NodeStatus.CANCELLED,
                        "removed by plan change",
                        event_type="node.cancelled",
                    )
                    self.workflows.set_node_required_in_conn(conn, node_id, False)
                elif node_id not in plan_nodes and status in (
                    NodeStatus.FAILED,
                    NodeStatus.CANCELLED,
                    NodeStatus.SKIPPED,
                ):
                    self.workflows.set_node_required_in_conn(conn, node_id, False)

            snapshot = self.plans.insert_snapshot_version_in_conn(
                conn,
                change.plan_id,
                current,
                plan_content_hash(current),
                created_by=resolved_by,
            )
            self.workflows.update_workflow_plan_snapshot_in_conn(
                conn, change.workflow_id, snapshot.id, snapshot.version
            )
            self.plans.resolve_change_request_in_conn(
                conn,
                change.id,
                ChangeStatus.APPROVED,
                new_version=snapshot.version,
                resolved_by=resolved_by,
            )
            self.workflows._add_event(
                conn,
                change.workflow_id,
                None,
                "plan.change_applied",
                {
                    "change_request_id": change.id,
                    "new_version": snapshot.version,
                    "snapshot_id": snapshot.id,
                },
            )

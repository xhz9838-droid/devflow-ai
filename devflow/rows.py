"""SQLite row <-> domain model mapping. Kept out of StateStore's logic."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .models import (
    AgentRun,
    AgentRunStatus,
    Checkpoint,
    DecisionStatus,
    ExecutionEvent,
    HumanDecision,
    Node,
    NodeStatus,
    RetryRecord,
    Stage,
    Workflow,
    WorkflowStatus,
)


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def load(value: str | None, default: Any) -> Any:
    return json.loads(value) if value else default


def to_workflow(row: sqlite3.Row) -> Workflow:
    return Workflow(
        id=row["id"],
        name=row["name"],
        goal=row["goal"],
        status=WorkflowStatus(row["status"]),
        context=load(row["context"], {}),
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def to_stage(row: sqlite3.Row) -> Stage:
    return Stage(
        id=row["id"],
        workflow_id=row["workflow_id"],
        name=row["name"],
        sequence=row["sequence"],
        status=row["status"],
        created_at=row["created_at"],
    )


def to_node(row: sqlite3.Row) -> Node:
    return Node(
        id=row["id"],
        workflow_id=row["workflow_id"],
        stage_id=row["stage_id"],
        name=row["name"],
        status=NodeStatus(row["status"]),
        dependencies=load(row["dependencies"], []),
        execution_config=load(row["execution_config"], {}),
        retry_policy=load(row["retry_policy"], {}),
        input=load(row["input"], {}),
        output=load(row["output"], None),
        error=row["error"],
        attempts=row["attempts"],
        required=bool(row["required"]),
        idempotency_key=row["idempotency_key"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def to_agent_run(row: sqlite3.Row) -> AgentRun:
    return AgentRun(
        id=row["id"],
        workflow_id=row["workflow_id"],
        node_id=row["node_id"],
        adapter=row["adapter"],
        status=AgentRunStatus(row["status"]),
        attempt=row["attempt"],
        prompt=row["prompt"],
        workdir=row["workdir"],
        pid=row["pid"],
        process_start_time=row["process_start_time"],
        exit_code=row["exit_code"],
        output=row["output"],
        error=row["error"],
        timeout_seconds=row["timeout_seconds"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        heartbeat_at=row["heartbeat_at"],
        metadata=load(row["metadata"], {}),
    )


def to_decision(row: sqlite3.Row) -> HumanDecision:
    return HumanDecision(
        id=row["id"],
        workflow_id=row["workflow_id"],
        node_id=row["node_id"],
        decision_type=row["decision_type"],
        question=row["question"],
        status=DecisionStatus(row["status"]),
        context=load(row["context"], {}),
        options=load(row["options"], []),
        selected_option=row["selected_option"],
        decision_payload=load(row["decision_payload"], {}),
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
        resolved_by=row["resolved_by"],
        expires_at=row["expires_at"],
        note=row["note"],
    )


def to_checkpoint(row: sqlite3.Row) -> Checkpoint:
    return Checkpoint(
        id=row["id"],
        workflow_id=row["workflow_id"],
        node_id=row["node_id"],
        reason=row["reason"],
        sequence=row["sequence"],
        state_snapshot=load(row["state_snapshot"], {}),
        context=load(row["context"], {}),
        created_at=row["created_at"],
    )


def to_event(row: sqlite3.Row) -> ExecutionEvent:
    return ExecutionEvent(
        id=row["id"],
        workflow_id=row["workflow_id"],
        node_id=row["node_id"],
        event_type=row["event_type"],
        payload=load(row["payload"], {}),
        actor=row["actor"],
        created_at=row["created_at"],
    )


def to_retry(row: sqlite3.Row) -> RetryRecord:
    return RetryRecord(
        id=row["id"],
        workflow_id=row["workflow_id"],
        node_id=row["node_id"],
        agent_run_id=row["agent_run_id"],
        attempt=row["attempt"],
        reason=row["reason"],
        next_retry_at=row["next_retry_at"],
        created_at=row["created_at"],
    )

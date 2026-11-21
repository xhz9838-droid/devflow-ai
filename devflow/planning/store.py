"""Planning persistence repository. All plan SQL lives here."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..db import Database
from ..errors import NotFoundError, ValidationError
from ..models import new_id, utcnow
from ..rows import dump as _dump, load as _load
from .models import (
    ChangeRisk,
    ChangeStatus,
    ClarificationStatus,
    Plan,
    PlanChangeRequest,
    PlanClarification,
    PlanSnapshot,
    PlanStatus,
    PlanVersion,
    ensure_plan_transition,
)
from .validator import PlanValidationError, plan_content_hash, validate_plan


def _plan(row: sqlite3.Row) -> Plan:
    return Plan(
        id=row["id"],
        title=row["title"],
        objective=row["objective"],
        status=PlanStatus(row["status"]),
        project_context=_load(row["project_context"], {}),
        plan_json=_load(row["plan_json"], None),
        version=row["version"],
        workflow_id=row["workflow_id"],
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        confirmed_at=row["confirmed_at"],
        confirmed_by=row["confirmed_by"],
    )


def _clarification(row: sqlite3.Row) -> PlanClarification:
    return PlanClarification(
        id=row["id"],
        plan_id=row["plan_id"],
        question=row["question"],
        level=row["level"],
        reason=row["reason"],
        research=_load(row["research"], ""),
        impact=row["impact"],
        options=_load(row["options"], []),
        recommended=row["recommended"],
        status=ClarificationStatus(row["status"]),
        answer=row["answer"],
        answered_by=row["answered_by"],
        created_at=row["created_at"],
        answered_at=row["answered_at"],
    )


def _snapshot(row: sqlite3.Row) -> PlanSnapshot:
    return PlanSnapshot(
        id=row["id"],
        plan_id=row["plan_id"],
        version=row["version"],
        plan_json=_load(row["plan_json"], {}),
        content_hash=row["content_hash"],
        created_at=row["created_at"],
        confirmed_by=row["confirmed_by"],
    )


def _version(row: sqlite3.Row) -> PlanVersion:
    return PlanVersion(
        id=row["id"],
        plan_id=row["plan_id"],
        version=row["version"],
        plan_json=_load(row["plan_json"], {}),
        content_hash=row["content_hash"],
        created_at=row["created_at"],
        created_by=row["created_by"],
        snapshot_id=row["snapshot_id"],
    )


def _change(row: sqlite3.Row) -> PlanChangeRequest:
    return PlanChangeRequest(
        id=row["id"],
        plan_id=row["plan_id"],
        workflow_id=row["workflow_id"],
        node_id=row["node_id"],
        risk_level=ChangeRisk(row["risk_level"]),
        status=ChangeStatus(row["status"]),
        title=row["title"],
        reason=row["reason"],
        impact=row["impact"],
        proposed_changes=_load(row["proposed_changes"], {}),
        decision_id=row["decision_id"],
        new_version=row["new_version"],
        note=row["note"],
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
        resolved_by=row["resolved_by"],
    )


class PlanStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _plan_row(self, conn, plan_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"plan not found: {plan_id}")
        return row

    # ------------------------------------------------------------------ plans
    def create_plan(
        self,
        title: str,
        objective: str,
        project_context: dict[str, Any] | None = None,
    ) -> Plan:
        if not objective.strip():
            raise ValidationError("plan objective is required")
        now = utcnow()
        plan = Plan(
            id=new_id("plan"),
            title=title.strip() or objective.strip()[:80],
            objective=objective.strip(),
            status=PlanStatus.DRAFT,
            project_context=project_context or {},
            created_at=now,
            updated_at=now,
        )
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO plans(id, status, title, objective, project_context, version,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                (
                    plan.id,
                    plan.status.value,
                    plan.title,
                    plan.objective,
                    _dump(plan.project_context),
                    plan.created_at,
                    plan.updated_at,
                ),
            )
        return plan

    def get_plan(self, plan_id: str) -> Plan:
        with self.db.read() as conn:
            return _plan(self._plan_row(conn, plan_id))

    def list_plans(self, status: PlanStatus | None = None) -> list[Plan]:
        with self.db.read() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM plans WHERE status = ? ORDER BY created_at, id",
                    (status.value,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM plans ORDER BY created_at, id").fetchall()
        return [_plan(row) for row in rows]

    def set_plan_status(
        self,
        plan_id: str,
        target: PlanStatus | str,
        *,
        plan_json: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> Plan:
        target = PlanStatus(target)
        with self.db.transaction() as conn:
            row = self._plan_row(conn, plan_id)
            current = PlanStatus(row["status"])
            if current is target:
                return _plan(row)
            ensure_plan_transition(current, target)
            conn.execute(
                "UPDATE plans SET status = ?, plan_json = ?, error = ?, updated_at = ?"
                " WHERE id = ?",
                (
                    target.value,
                    _dump(plan_json) if plan_json is not None else row["plan_json"],
                    error if error is not None else (None if target is not PlanStatus.FAILED else row["error"]),
                    utcnow(),
                    plan_id,
                ),
            )
            return _plan(self._plan_row(conn, plan_id))

    def save_plan_json(
        self, plan_id: str, plan_json: dict[str, Any], *, project_context: dict[str, Any] | None = None
    ) -> Plan:
        with self.db.transaction() as conn:
            self._plan_row(conn, plan_id)
            if project_context is None:
                conn.execute(
                    "UPDATE plans SET plan_json = ?, updated_at = ? WHERE id = ?",
                    (_dump(plan_json), utcnow(), plan_id),
                )
            else:
                conn.execute(
                    "UPDATE plans SET plan_json = ?, project_context = ?, updated_at = ? WHERE id = ?",
                    (_dump(plan_json), _dump(project_context), utcnow(), plan_id),
                )
            return _plan(self._plan_row(conn, plan_id))

    def confirm(
        self,
        plan_id: str,
        *,
        confirmed_by: str = "cli",
    ) -> PlanSnapshot:
        now = utcnow()
        with self.db.transaction() as conn:
            row = self._plan_row(conn, plan_id)
            current = PlanStatus(row["status"])
            if current is not PlanStatus.PLAN_READY:
                raise ValidationError(f"plan {plan_id} is {current.value}, not PLAN_READY")
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM plan_clarifications WHERE plan_id = ? AND status = 'PENDING'",
                (plan_id,),
            ).fetchone()["n"]
            if pending:
                raise ValidationError(f"plan {plan_id} still has {pending} pending clarification(s)")
            plan_json = _load(row["plan_json"], None)
            if not isinstance(plan_json, dict):
                raise ValidationError("plan has no generated content")
            errors = validate_plan(plan_json)
            if errors:
                raise PlanValidationError(errors)
            content_hash = plan_content_hash(plan_json)
            version = int(row["version"]) + 1
            snapshot = PlanSnapshot(
                id=new_id("psnap"),
                plan_id=plan_id,
                version=version,
                plan_json=plan_json,
                content_hash=content_hash,
                created_at=now,
                confirmed_by=confirmed_by,
            )
            version_row = PlanVersion(
                id=new_id("pver"),
                plan_id=plan_id,
                version=version,
                plan_json=plan_json,
                content_hash=content_hash,
                created_at=now,
                created_by=confirmed_by,
                snapshot_id=snapshot.id,
            )
            conn.execute(
                "INSERT INTO plan_snapshots(id, plan_id, version, plan_json, content_hash,"
                " created_at, confirmed_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot.id,
                    plan_id,
                    version,
                    _dump(plan_json),
                    content_hash,
                    now,
                    confirmed_by,
                ),
            )
            conn.execute(
                "INSERT INTO plan_versions(id, plan_id, version, plan_json, content_hash,"
                " created_at, created_by, snapshot_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    version_row.id,
                    plan_id,
                    version,
                    _dump(plan_json),
                    content_hash,
                    now,
                    confirmed_by,
                    snapshot.id,
                ),
            )
            conn.execute(
                "UPDATE plans SET status = ?, plan_json = ?, version = ?, confirmed_at = ?,"
                " confirmed_by = ?, updated_at = ?, error = NULL WHERE id = ?",
                (
                    PlanStatus.CONFIRMED.value,
                    _dump(plan_json),
                    version,
                    now,
                    confirmed_by,
                    now,
                    plan_id,
                ),
            )
            return snapshot

    def insert_snapshot_version_in_conn(
        self,
        conn,
        plan_id: str,
        plan_json: dict[str, Any],
        content_hash: str,
        *,
        created_by: str = "core",
    ) -> PlanSnapshot:
        now = utcnow()
        row = self._plan_row(conn, plan_id)
        current = PlanStatus(row["status"])
        if current not in (PlanStatus.CONFIRMED, PlanStatus.EXECUTION_READY):
            raise ValidationError(f"plan {plan_id} is {current.value}; cannot add a version")
        version = int(row["version"]) + 1
        snapshot = PlanSnapshot(
            id=new_id("psnap"),
            plan_id=plan_id,
            version=version,
            plan_json=plan_json,
            content_hash=content_hash,
            created_at=now,
            confirmed_by=created_by,
        )
        conn.execute(
            "INSERT INTO plan_snapshots(id, plan_id, version, plan_json, content_hash,"
            " created_at, confirmed_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (snapshot.id, plan_id, version, _dump(plan_json), content_hash, now, created_by),
        )
        conn.execute(
            "INSERT INTO plan_versions(id, plan_id, version, plan_json, content_hash,"
            " created_at, created_by, snapshot_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id("pver"),
                plan_id,
                version,
                _dump(plan_json),
                content_hash,
                now,
                created_by,
                snapshot.id,
            ),
        )
        conn.execute(
            "UPDATE plans SET plan_json = ?, version = ?, updated_at = ? WHERE id = ?",
            (_dump(plan_json), version, now, plan_id),
        )
        return snapshot

    def insert_snapshot_version(
        self,
        plan_id: str,
        plan_json: dict[str, Any],
        content_hash: str,
        *,
        created_by: str = "core",
    ) -> PlanSnapshot:
        with self.db.transaction() as conn:
            return self.insert_snapshot_version_in_conn(
                conn, plan_id, plan_json, content_hash, created_by=created_by
            )

    def bind_workflow(self, plan_id: str, workflow_id: str) -> Plan:
        with self.db.transaction() as conn:
            row = self._plan_row(conn, plan_id)
            current = PlanStatus(row["status"])
            existing = row["workflow_id"]
            if existing and existing != workflow_id:
                raise ValidationError(
                    f"plan {plan_id} is already bound to workflow {existing}"
                )
            if existing == workflow_id and current is PlanStatus.EXECUTION_READY:
                return _plan(row)
            if current is PlanStatus.CONFIRMED:
                ensure_plan_transition(current, PlanStatus.EXECUTION_READY)
            elif current is not PlanStatus.EXECUTION_READY:
                raise ValidationError(
                    f"plan {plan_id} is {current.value}; confirm it before creating a workflow"
                )
            conn.execute(
                "UPDATE plans SET workflow_id = ?, status = ?, updated_at = ? WHERE id = ?",
                (workflow_id, PlanStatus.EXECUTION_READY.value, utcnow(), plan_id),
            )
            return _plan(self._plan_row(conn, plan_id))

    # ---------------------------------------------------------- clarifications
    def create_clarifications(
        self, plan_id: str, items: list[dict[str, Any]]
    ) -> list[PlanClarification]:
        now = utcnow()
        created: list[PlanClarification] = []
        with self.db.transaction() as conn:
            self._plan_row(conn, plan_id)
            for item in items:
                clarification = PlanClarification(
                    id=new_id("clar"),
                    plan_id=plan_id,
                    question=str(item["question"]),
                    level=str(item.get("level") or "L3"),
                    reason=str(item.get("reason") or ""),
                    research=item.get("research") or "",
                    impact=str(item.get("impact") or ""),
                    options=list(item.get("options") or []),
                    recommended=item.get("recommended"),
                    status=ClarificationStatus.PENDING,
                    created_at=now,
                )
                conn.execute(
                    "INSERT INTO plan_clarifications(id, plan_id, question, level, reason,"
                    " research, impact, options, recommended, status, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        clarification.id,
                        plan_id,
                        clarification.question,
                        clarification.level,
                        clarification.reason,
                        _dump(clarification.research),
                        clarification.impact,
                        _dump(clarification.options),
                        clarification.recommended,
                        clarification.status.value,
                        now,
                    ),
                )
                created.append(clarification)
        return created

    def get_clarification(self, clarification_id: str) -> PlanClarification:
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT * FROM plan_clarifications WHERE id = ?", (clarification_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"clarification not found: {clarification_id}")
        return _clarification(row)

    def list_clarifications(
        self, plan_id: str, status: ClarificationStatus | None = None
    ) -> list[PlanClarification]:
        with self.db.read() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM plan_clarifications WHERE plan_id = ? AND status = ?"
                    " ORDER BY created_at, id",
                    (plan_id, status.value),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM plan_clarifications WHERE plan_id = ? ORDER BY created_at, id",
                    (plan_id,),
                ).fetchall()
        return [_clarification(row) for row in rows]

    def answer_clarification(
        self, plan_id: str, clarification_id: str, answer: str, *, answered_by: str = "cli"
    ) -> PlanClarification:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM plan_clarifications WHERE id = ?", (clarification_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"clarification not found: {clarification_id}")
            if row["plan_id"] != plan_id:
                raise ValidationError(
                    f"clarification {clarification_id} does not belong to plan {plan_id}"
                )
            if ClarificationStatus(row["status"]) is not ClarificationStatus.PENDING:
                raise ValidationError(f"clarification {clarification_id} is already answered")
            now = utcnow()
            conn.execute(
                "UPDATE plan_clarifications SET status = ?, answer = ?, answered_by = ?,"
                " answered_at = ? WHERE id = ?",
                (
                    ClarificationStatus.ANSWERED.value,
                    answer,
                    answered_by,
                    now,
                    clarification_id,
                ),
            )
            return _clarification(
                conn.execute(
                    "SELECT * FROM plan_clarifications WHERE id = ?", (clarification_id,)
                ).fetchone()
            )

    def cancel_clarification(self, clarification_id: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE plan_clarifications SET status = ? WHERE id = ? AND status = ?",
                (
                    ClarificationStatus.CANCELLED.value,
                    clarification_id,
                    ClarificationStatus.PENDING.value,
                ),
            )

    # ------------------------------------------------------ snapshots/versions
    def get_snapshot(self, plan_id: str, version: int | None = None) -> PlanSnapshot | None:
        with self.db.read() as conn:
            if version is None:
                row = conn.execute(
                    "SELECT * FROM plan_snapshots WHERE plan_id = ? ORDER BY version DESC LIMIT 1",
                    (plan_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM plan_snapshots WHERE plan_id = ? AND version = ?",
                    (plan_id, version),
                ).fetchone()
        return _snapshot(row) if row else None

    def list_snapshots(self, plan_id: str) -> list[PlanSnapshot]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM plan_snapshots WHERE plan_id = ? ORDER BY version", (plan_id,)
            ).fetchall()
        return [_snapshot(row) for row in rows]

    def list_versions(self, plan_id: str) -> list[PlanVersion]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM plan_versions WHERE plan_id = ? ORDER BY version", (plan_id,)
            ).fetchall()
        return [_version(row) for row in rows]

    # ------------------------------------------------------- change requests
    def create_change_request(
        self,
        *,
        plan_id: str,
        workflow_id: str,
        node_id: str | None,
        risk_level: ChangeRisk | str,
        title: str,
        reason: str = "",
        impact: str = "",
        proposed_changes: dict[str, Any] | None = None,
        decision_id: str | None = None,
    ) -> PlanChangeRequest:
        change = PlanChangeRequest(
            id=new_id("pcr"),
            plan_id=plan_id,
            workflow_id=workflow_id,
            node_id=node_id,
            risk_level=ChangeRisk(risk_level),
            status=ChangeStatus.PENDING,
            title=title,
            reason=reason,
            impact=impact,
            proposed_changes=proposed_changes or {},
            decision_id=decision_id,
            created_at=utcnow(),
        )
        with self.db.transaction() as conn:
            self._plan_row(conn, plan_id)
            conn.execute(
                "INSERT INTO plan_change_requests(id, plan_id, workflow_id, node_id,"
                " risk_level, status, title, reason, impact, proposed_changes, decision_id,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    change.id,
                    plan_id,
                    workflow_id,
                    node_id,
                    change.risk_level.value,
                    change.status.value,
                    title,
                    reason,
                    impact,
                    _dump(change.proposed_changes),
                    decision_id,
                    change.created_at,
                ),
            )
        return change

    def get_change_request(self, change_id: str) -> PlanChangeRequest:
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT * FROM plan_change_requests WHERE id = ?", (change_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"plan change request not found: {change_id}")
        return _change(row)

    def list_change_requests(
        self, plan_id: str, status: ChangeStatus | None = None
    ) -> list[PlanChangeRequest]:
        with self.db.read() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM plan_change_requests WHERE plan_id = ? AND status = ?"
                    " ORDER BY created_at, id",
                    (plan_id, status.value),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM plan_change_requests WHERE plan_id = ? ORDER BY created_at, id",
                    (plan_id,),
                ).fetchall()
        return [_change(row) for row in rows]

    def set_change_decision(self, change_id: str, decision_id: str) -> PlanChangeRequest:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE plan_change_requests SET decision_id = ? WHERE id = ?",
                (decision_id, change_id),
            )
            row = conn.execute(
                "SELECT * FROM plan_change_requests WHERE id = ?", (change_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"plan change request not found: {change_id}")
        return _change(row)

    def resolve_change_request_in_conn(
        self,
        conn,
        change_id: str,
        status: ChangeStatus | str,
        *,
        new_version: int | None = None,
        resolved_by: str = "core",
        note: str | None = None,
    ) -> PlanChangeRequest:
        status = ChangeStatus(status)
        row = conn.execute(
            "SELECT * FROM plan_change_requests WHERE id = ?", (change_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"plan change request not found: {change_id}")
        current = ChangeStatus(row["status"])
        if current is not status:
            conn.execute(
                "UPDATE plan_change_requests SET status = ?, new_version = ?, note = ?,"
                " resolved_at = ?, resolved_by = ? WHERE id = ?",
                (
                    status.value,
                    new_version if new_version is not None else row["new_version"],
                    note,
                    utcnow(),
                    resolved_by,
                    change_id,
                ),
            )
        row = conn.execute(
            "SELECT * FROM plan_change_requests WHERE id = ?", (change_id,)
        ).fetchone()
        return _change(row)

    def resolve_change_request(
        self,
        change_id: str,
        status: ChangeStatus | str,
        *,
        new_version: int | None = None,
        resolved_by: str = "core",
        note: str | None = None,
    ) -> PlanChangeRequest:
        with self.db.transaction() as conn:
            return self.resolve_change_request_in_conn(
                conn,
                change_id,
                status,
                new_version=new_version,
                resolved_by=resolved_by,
                note=note,
            )

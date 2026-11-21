"""SQLite connection and schema migration management."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .models import utcnow

MIGRATIONS: list[tuple[int, list[str]]] = [
    (
        1,
        [
            """
            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                goal TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                context TEXT NOT NULL DEFAULT '{}',
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows(status)",
            """
            CREATE TABLE IF NOT EXISTS workflow_stages (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_stages_workflow ON workflow_stages(workflow_id)",
            """
            CREATE TABLE IF NOT EXISTS workflow_nodes (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
                stage_id TEXT REFERENCES workflow_stages(id) ON DELETE SET NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                dependencies TEXT NOT NULL DEFAULT '[]',
                execution_config TEXT NOT NULL DEFAULT '{}',
                retry_policy TEXT NOT NULL DEFAULT '{}',
                input TEXT NOT NULL DEFAULT '{}',
                output TEXT,
                error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                required INTEGER NOT NULL DEFAULT 1,
                idempotency_key TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_nodes_workflow_status ON workflow_nodes(workflow_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_nodes_stage ON workflow_nodes(stage_id)",
            """
            CREATE TABLE IF NOT EXISTS agent_runs (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
                node_id TEXT NOT NULL REFERENCES workflow_nodes(id) ON DELETE CASCADE,
                adapter TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 1,
                prompt TEXT NOT NULL DEFAULT '',
                workdir TEXT NOT NULL DEFAULT '',
                pid INTEGER,
                exit_code INTEGER,
                output TEXT NOT NULL DEFAULT '',
                error TEXT,
                timeout_seconds REAL NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                heartbeat_at TEXT,
                metadata TEXT NOT NULL DEFAULT '{}'
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_runs_workflow ON agent_runs(workflow_id)",
            "CREATE INDEX IF NOT EXISTS idx_runs_node ON agent_runs(node_id)",
            "CREATE INDEX IF NOT EXISTS idx_runs_status ON agent_runs(status)",
            """
            CREATE TABLE IF NOT EXISTS execution_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
                node_id TEXT,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                actor TEXT NOT NULL DEFAULT 'core',
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_events_workflow ON execution_events(workflow_id, id)",
            "CREATE INDEX IF NOT EXISTS idx_events_type ON execution_events(event_type)",
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
                node_id TEXT,
                reason TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                state_snapshot TEXT NOT NULL,
                context TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(workflow_id, sequence)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_checkpoints_workflow ON checkpoints(workflow_id, sequence)",
            """
            CREATE TABLE IF NOT EXISTS human_decisions (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
                node_id TEXT,
                decision_type TEXT NOT NULL,
                question TEXT NOT NULL,
                context TEXT NOT NULL DEFAULT '{}',
                options TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL,
                selected_option TEXT,
                decision_payload TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                resolved_by TEXT,
                expires_at TEXT,
                note TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_decisions_workflow ON human_decisions(workflow_id, status)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_decisions_one_pending_per_node ON human_decisions(node_id) WHERE status = 'PENDING'",
            """
            CREATE TABLE IF NOT EXISTS retry_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
                node_id TEXT NOT NULL REFERENCES workflow_nodes(id) ON DELETE CASCADE,
                agent_run_id TEXT,
                attempt INTEGER NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                next_retry_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(node_id, attempt)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_retries_workflow ON retry_records(workflow_id)",
        ],
    ),
    (
        2,
        [
            "ALTER TABLE agent_runs ADD COLUMN process_start_time TEXT",
        ],
    ),
    (
        3,
        [
            """
            CREATE TABLE IF NOT EXISTS engine_locks (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                owner_id TEXT NOT NULL,
                hostname TEXT NOT NULL,
                pid INTEGER NOT NULL,
                acquired_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                released_at TEXT
            )
            """,
        ],
    ),
    (
        4,
        [
            "ALTER TABLE workflows ADD COLUMN plan_id TEXT",
            "ALTER TABLE workflows ADD COLUMN plan_snapshot_id TEXT",
            "ALTER TABLE workflows ADD COLUMN plan_version INTEGER",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_workflows_plan_snapshot"
            " ON workflows(plan_snapshot_id) WHERE plan_snapshot_id IS NOT NULL",
            """
            CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                title TEXT NOT NULL,
                objective TEXT NOT NULL,
                project_context TEXT NOT NULL DEFAULT '{}',
                plan_json TEXT,
                version INTEGER NOT NULL DEFAULT 0,
                workflow_id TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                confirmed_at TEXT,
                confirmed_by TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(status)",
            """
            CREATE TABLE IF NOT EXISTS plan_clarifications (
                id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
                question TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'L3',
                reason TEXT NOT NULL DEFAULT '',
                research TEXT NOT NULL DEFAULT '',
                impact TEXT NOT NULL DEFAULT '',
                options TEXT NOT NULL DEFAULT '[]',
                recommended TEXT,
                status TEXT NOT NULL,
                answer TEXT,
                answered_by TEXT,
                created_at TEXT NOT NULL,
                answered_at TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_plan_clarifications"
            " ON plan_clarifications(plan_id, status)",
            """
            CREATE TABLE IF NOT EXISTS plan_versions (
                id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
                version INTEGER NOT NULL,
                plan_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_by TEXT,
                snapshot_id TEXT,
                UNIQUE(plan_id, version)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS plan_snapshots (
                id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
                version INTEGER NOT NULL,
                plan_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                confirmed_by TEXT,
                UNIQUE(plan_id, version)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS plan_change_requests (
                id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
                workflow_id TEXT NOT NULL,
                node_id TEXT,
                risk_level TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                impact TEXT NOT NULL DEFAULT '',
                proposed_changes TEXT NOT NULL DEFAULT '{}',
                decision_id TEXT,
                new_version INTEGER,
                note TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                resolved_by TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_plan_changes"
            " ON plan_change_requests(plan_id, status)",
            "CREATE TRIGGER IF NOT EXISTS plan_snapshots_no_update"
            " BEFORE UPDATE ON plan_snapshots BEGIN"
            " SELECT RAISE(ABORT, 'plan snapshots are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS plan_snapshots_no_delete"
            " BEFORE DELETE ON plan_snapshots BEGIN"
            " SELECT RAISE(ABORT, 'plan snapshots are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS plan_versions_no_update"
            " BEFORE UPDATE ON plan_versions BEGIN"
            " SELECT RAISE(ABORT, 'plan versions are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS plan_versions_no_delete"
            " BEFORE DELETE ON plan_versions BEGIN"
            " SELECT RAISE(ABORT, 'plan versions are immutable'); END",
        ],
    ),
]


class Database:
    """One file, one authority. Every operation opens its own connection so the
    engine can call it from executor threads without sharing a handle."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.parent and str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self) -> int:
        """Apply pending migrations and return the resulting schema version."""
        with self.transaction() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                row["version"]
                for row in conn.execute("SELECT version FROM schema_migrations")
            }
            for version, statements in MIGRATIONS:
                if version in applied:
                    continue
                for statement in statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, utcnow()),
                )
        with self.read() as conn:
            row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
            return int(row["v"] or 0)

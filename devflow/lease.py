"""Database-level scheduling lease: one active engine per SQLite database."""

from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .errors import EngineLocked
from .models import parse_time, utcnow
from .state import StateStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LeaseInfo:
    owner_id: str
    hostname: str
    pid: int
    acquired_at: str
    heartbeat_at: str
    expires_at: str
    released_at: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


class EngineLease:
    """SQLite CAS lease. BEGIN IMMEDIATE makes acquisition and takeover race-safe."""

    def __init__(
        self,
        store: StateStore,
        *,
        ttl_seconds: float = 300.0,
        owner_id: str | None = None,
        renew_interval: float | None = None,
    ) -> None:
        self.store = store
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.owner_id = owner_id or (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        )
        self.renew_interval = renew_interval or max(1.0, self.ttl_seconds / 3.0)
        self._held = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def held(self) -> bool:
        return self._held

    def _info(self, row) -> LeaseInfo:
        return LeaseInfo(
            owner_id=row["owner_id"],
            hostname=row["hostname"],
            pid=row["pid"],
            acquired_at=row["acquired_at"],
            heartbeat_at=row["heartbeat_at"],
            expires_at=row["expires_at"],
            released_at=row["released_at"],
        )

    def _locked(self, row) -> EngineLocked:
        return EngineLocked(
            "engine lease is held by another process",
            owner=row["owner_id"],
            hostname=row["hostname"],
            pid=row["pid"],
            expires_at=row["expires_at"],
        )

    def acquire(self) -> LeaseInfo:
        now = _now()
        now_iso = _iso(now)
        expires_iso = _iso(now + timedelta(seconds=self.ttl_seconds))
        with self.store.db.transaction() as conn:
            row = conn.execute("SELECT * FROM engine_locks WHERE id = 1").fetchone()
            if row is not None and row["released_at"] is None:
                expiry = parse_time(row["expires_at"])
                if (
                    row["owner_id"] != self.owner_id
                    and expiry is not None
                    and expiry > now
                ):
                    raise self._locked(row)
            if row is None:
                conn.execute(
                    "INSERT INTO engine_locks(id, owner_id, hostname, pid, acquired_at,"
                    " heartbeat_at, expires_at, released_at) VALUES (1, ?, ?, ?, ?, ?, ?, NULL)",
                    (
                        self.owner_id,
                        socket.gethostname(),
                        os.getpid(),
                        now_iso,
                        now_iso,
                        expires_iso,
                    ),
                )
            else:
                cursor = conn.execute(
                    "UPDATE engine_locks SET owner_id = ?, hostname = ?, pid = ?,"
                    " acquired_at = ?, heartbeat_at = ?, expires_at = ?, released_at = NULL"
                    " WHERE id = 1 AND (released_at IS NOT NULL OR expires_at <= ?"
                    " OR owner_id = ?)",
                    (
                        self.owner_id,
                        socket.gethostname(),
                        os.getpid(),
                        now_iso,
                        now_iso,
                        expires_iso,
                        now_iso,
                        self.owner_id,
                    ),
                )
                if cursor.rowcount != 1:
                    current = conn.execute(
                        "SELECT * FROM engine_locks WHERE id = 1"
                    ).fetchone()
                    raise self._locked(current)
        self._held = True
        self._start_renewal()
        return LeaseInfo(
            owner_id=self.owner_id,
            hostname=socket.gethostname(),
            pid=os.getpid(),
            acquired_at=now_iso,
            heartbeat_at=now_iso,
            expires_at=expires_iso,
        )

    def renew(self) -> LeaseInfo:
        now = _now()
        now_iso = _iso(now)
        expires_iso = _iso(now + timedelta(seconds=self.ttl_seconds))
        with self.store.db.transaction() as conn:
            row = conn.execute("SELECT * FROM engine_locks WHERE id = 1").fetchone()
            if (
                row is None
                or row["owner_id"] != self.owner_id
                or row["released_at"] is not None
            ):
                self._held = False
                raise self._locked(row) if row is not None else EngineLocked(
                    "engine lease is missing"
                )
            conn.execute(
                "UPDATE engine_locks SET heartbeat_at = ?, expires_at = ?"
                " WHERE id = 1 AND owner_id = ? AND released_at IS NULL",
                (now_iso, expires_iso, self.owner_id),
            )
        return self._info(
            {
                "owner_id": self.owner_id,
                "hostname": socket.gethostname(),
                "pid": os.getpid(),
                "acquired_at": row["acquired_at"],
                "heartbeat_at": now_iso,
                "expires_at": expires_iso,
                "released_at": None,
            }
        )

    def ensure_held(self) -> None:
        if not self._held:
            raise EngineLocked("engine lease is not held")
        self.renew()

    def release(self) -> None:
        self._stop_renewal()
        if not self._held:
            return
        with self.store.db.transaction() as conn:
            conn.execute(
                "UPDATE engine_locks SET released_at = ?"
                " WHERE id = 1 AND owner_id = ? AND released_at IS NULL",
                (utcnow(), self.owner_id),
            )
        self._held = False

    def info(self) -> LeaseInfo | None:
        with self.store.db.read() as conn:
            row = conn.execute("SELECT * FROM engine_locks WHERE id = 1").fetchone()
        return self._info(row) if row else None

    def _start_renewal(self) -> None:
        self._stop.clear()

        def loop() -> None:
            while not self._stop.wait(self.renew_interval):
                try:
                    self.renew()
                except EngineLocked:
                    logger.error("engine lease lost for %s", self.owner_id)
                    return
                except Exception:
                    logger.exception("lease renewal failed for %s", self.owner_id)

        self._thread = threading.Thread(
            target=loop, name=f"lease-{self.owner_id}", daemon=True
        )
        self._thread.start()

    def _stop_renewal(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def __enter__(self) -> "EngineLease":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

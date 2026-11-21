"""Runtime configuration. Environment variables only; no provider assumptions."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DevFlowConfig:
    db_path: Path
    workdir: Path
    max_parallel: int = 4
    agent_timeout: float = 1800.0
    recovery_grace: float = 30.0
    log_level: str = "INFO"


def load_config(db_path: str | os.PathLike[str] | None = None, *, workdir: str | os.PathLike[str] | None = None) -> DevFlowConfig:
    return DevFlowConfig(
        db_path=Path(db_path or os.environ.get("DEVFLOW_DB", "devflow.db")),
        workdir=Path(workdir or os.environ.get("DEVFLOW_WORKDIR", os.getcwd())),
        max_parallel=int(os.environ.get("DEVFLOW_MAX_PARALLEL", "4")),
        agent_timeout=float(os.environ.get("DEVFLOW_AGENT_TIMEOUT", "1800")),
        recovery_grace=float(os.environ.get("DEVFLOW_RECOVERY_GRACE", "30")),
        log_level=os.environ.get("DEVFLOW_LOG_LEVEL", "INFO"),
    )


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

"""Controlled agent -> core protocols expressed as files in the workdir.

The agent asks for a human decision by writing a JSON file. The engine reads,
validates and persists it, then parks the node. No notification dependency.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .errors import ValidationError
from .models import utcnow

MAX_DECISION_REQUEST_BYTES = 64 * 1024


def _safe_node_id(node_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", node_id)
    cleaned = cleaned.replace("..", "__").strip(".")
    return cleaned or "node"


def decision_request_path(workdir: str | Path, node_id: str) -> Path:
    return Path(workdir) / ".devflow" / "decisions" / f"{_safe_node_id(node_id)}.json"


def read_decision_request(workdir: str | Path, node_id: str) -> dict | None:
    path = decision_request_path(workdir, node_id)
    if not path.exists():
        return None
    if path.stat().st_size > MAX_DECISION_REQUEST_BYTES:
        raise ValidationError("decision request file is too large")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid decision request JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError("decision request must be a JSON object")
    return data


def archive_decision_request(workdir: str | Path, node_id: str) -> Path | None:
    path = decision_request_path(workdir, node_id)
    if not path.exists():
        return None
    archive = path.parent / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[^0-9A-Za-z]", "", utcnow())
    target = archive / f"{_safe_node_id(node_id)}.{stamp}.json"
    try:
        path.replace(target)
        return target
    except OSError:
        path.unlink(missing_ok=True)
        return None

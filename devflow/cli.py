"""Control interface. No lifecycle or scheduling logic lives here."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .agents.claude_code import ClaudeCodeAdapter
from .config import configure_logging, load_config
from .engine import WorkflowEngine
from .errors import DevFlowError, ValidationError
from .models import WorkflowStatus


def _load_nodes(path: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if isinstance(data, dict):
        nodes = data.get("nodes")
        context = data.get("context") or {}
        if not isinstance(nodes, list):
            raise ValidationError("nodes file must contain a 'nodes' list")
        return nodes, context
    if isinstance(data, list):
        return data, {}
    raise ValidationError("nodes file must be a list or an object with 'nodes'")


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devflow", description="DevFlow AI workflow engine")
    parser.add_argument("--db", default=None, help="SQLite path (default: $DEVFLOW_DB or devflow.db)")
    parser.add_argument("--workdir", default=None, help="agent working directory")
    parser.add_argument("--log-level", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="apply schema migrations")

    create = sub.add_parser("create", help="create a workflow from a JSON node list")
    create.add_argument("--name", required=True)
    create.add_argument("--goal", default="")
    create.add_argument("--nodes", required=True, help="JSON file path, or - for stdin")

    for name in ("start", "resume", "status", "inspect"):
        cmd = sub.add_parser(name)
        cmd.add_argument("workflow_id")

    inspect = sub.choices["inspect"]
    inspect.add_argument("--events", type=int, default=100)

    cancel = sub.add_parser("cancel")
    cancel.add_argument("workflow_id")
    cancel.add_argument("--reason", default="cancelled by user")

    events = sub.add_parser("events")
    events.add_argument("workflow_id")
    events.add_argument("--limit", type=int, default=100)

    decisions = sub.add_parser("decisions")
    decisions.add_argument("workflow_id")
    decisions.add_argument("--all", action="store_true", help="include resolved decisions")

    resolve = sub.add_parser("resolve", help="resolve a pending human decision")
    resolve.add_argument("decision_id")
    resolve.add_argument("--option", default=None)
    resolve.add_argument("--payload", default=None, help="JSON object")
    resolve.add_argument("--by", default="cli")
    resolve.add_argument("--note", default=None)

    sub.add_parser("recover", help="scan and recover interrupted workflows")

    listing = sub.add_parser("list", help="list workflows")
    listing.add_argument("--status", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to GBK; agent output is UTF-8 JSON.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = _parser().parse_args(argv)
    config = load_config(args.db, workdir=args.workdir)
    configure_logging(args.log_level or config.log_level)
    extra_args = shlex.split(os.environ.get("DEVFLOW_CLAUDE_EXTRA_ARGS", ""))
    engine = WorkflowEngine(config, ClaudeCodeAdapter(extra_args=extra_args))
    try:
        if args.command == "init":
            _print({"db": str(config.db_path), "schema_version": engine.db.migrate()})
        elif args.command == "create":
            nodes, context = _load_nodes(args.nodes)
            workflow = engine.create_workflow(args.name, args.goal, nodes, context=context)
            _print(asdict(workflow))
        elif args.command == "start":
            _print(engine.status(engine.start(args.workflow_id).id))
        elif args.command == "resume":
            _print(engine.status(engine.resume(args.workflow_id).id))
        elif args.command == "cancel":
            _print(asdict(engine.cancel(args.workflow_id, args.reason)))
        elif args.command == "status":
            _print(engine.status(args.workflow_id))
        elif args.command == "inspect":
            _print(engine.inspect(args.workflow_id, args.events))
        elif args.command == "events":
            _print([asdict(e) for e in engine.store.list_events(args.workflow_id, args.limit)])
        elif args.command == "decisions":
            decisions = (
                engine.decisions.list(args.workflow_id)
                if args.all
                else engine.decisions.pending(args.workflow_id)
            )
            _print([asdict(d) for d in decisions])
        elif args.command == "resolve":
            payload = json.loads(args.payload) if args.payload else {}
            decision = engine.decisions.resolve(
                args.decision_id,
                args.option,
                payload,
                resolved_by=args.by,
                note=args.note,
            )
            _print(asdict(decision))
        elif args.command == "recover":
            _print([asdict(r) for r in engine.recover_all()])
        elif args.command == "list":
            status = WorkflowStatus(args.status) if args.status else None
            _print([asdict(w) for w in engine.store.list_workflows(status)])
        else:  # pragma: no cover - argparse enforces choices
            raise ValidationError(f"unknown command: {args.command}")
        return 0
    except (DevFlowError, OSError, json.JSONDecodeError) as exc:
        print(f"devflow: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

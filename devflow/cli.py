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
from .planning.manager import PlanManager
from .planning.planner import ClaudeCodePlanner
from .planning.store import PlanStore


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

    plan = sub.add_parser("plan", help="planning layer commands")
    plan_sub = plan.add_subparsers(dest="plan_command", required=True)
    plan_create = plan_sub.add_parser("create", help="create and investigate a plan")
    plan_create.add_argument("objective")
    plan_create.add_argument("--title", default=None)
    plan_create.add_argument("--context", default=None, help="JSON object")
    plan_create.add_argument("--authorization", action="append", default=[])
    for name in ("show", "clarify", "validate", "confirm", "snapshot", "history", "changes", "generate", "refine"):
        cmd = plan_sub.add_parser(name)
        cmd.add_argument("plan_id")
    plan_sub.choices["confirm"].add_argument("--by", default="cli")
    plan_sub.choices["snapshot"].add_argument("--version", type=int, default=None)
    plan_sub.choices["generate"].add_argument("--refine", action="store_true")
    plan_answer = plan_sub.add_parser("answer")
    plan_answer.add_argument("plan_id")
    plan_answer.add_argument("clarification_id")
    plan_answer.add_argument("answer")
    plan_answer.add_argument("--by", default="cli")
    plan_answer.add_argument("--no-refine", action="store_true")

    workflow = sub.add_parser("workflow", help="workflow commands")
    workflow_sub = workflow.add_subparsers(dest="workflow_command", required=True)
    workflow_start = workflow_sub.add_parser("start", help="start a workflow")
    workflow_start.add_argument("--plan", required=True, dest="plan_id")
    return parser


def _plan_manager(engine: WorkflowEngine, config, extra_args: list[str]) -> PlanManager:
    planner_args: list[str] = []
    skip_next = False
    for arg in extra_args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--permission-mode":
            skip_next = True
            continue
        if arg.startswith("--permission-mode="):
            continue
        if arg in ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions"):
            continue
        planner_args.append(arg)
    # Planning is always read-only, regardless of execution permission args.
    planner_args.extend(["--permission-mode", "plan"])
    planner = ClaudeCodePlanner(
        ClaudeCodeAdapter(extra_args=planner_args),
        timeout_seconds=config.agent_timeout,
    )
    return PlanManager(
        PlanStore(engine.db),
        planner,
        workdir=str(config.workdir),
        planner_timeout=config.agent_timeout,
    )


def _handle_plan(manager: PlanManager, args: argparse.Namespace) -> None:
    command = args.plan_command
    if command == "create":
        context = json.loads(args.context) if args.context else {}
        plan = manager.create(
            args.objective,
            title=args.title,
            project_context=context,
            authorizations=args.authorization,
        )
        plan = manager.generate(plan.id)
        _print(_plan_show(manager, plan.id))
    elif command == "show":
        _print(_plan_show(manager, args.plan_id))
    elif command == "clarify":
        _print(
            [
                asdict(item)
                for item in manager.store.list_clarifications(args.plan_id)
                if item.status.value == "PENDING"
            ]
        )
    elif command == "answer":
        manager.answer(
            args.plan_id,
            args.clarification_id,
            args.answer,
            answered_by=args.by,
            refine=not args.no_refine,
        )
        _print(_plan_show(manager, args.plan_id))
    elif command == "generate":
        manager.generate(args.plan_id, refine=args.refine)
        _print(_plan_show(manager, args.plan_id))
    elif command == "refine":
        manager.generate(args.plan_id, refine=True)
        _print(_plan_show(manager, args.plan_id))
    elif command == "validate":
        _print(manager.validate(args.plan_id))
    elif command == "confirm":
        snapshot = manager.confirm(args.plan_id, confirmed_by=args.by)
        _print(asdict(snapshot))
    elif command == "snapshot":
        snapshot = manager.snapshot(args.plan_id, args.version)
        _print(asdict(snapshot) if snapshot else None)
    elif command == "history":
        history = manager.history(args.plan_id)
        _print(
            {
                key: [asdict(item) for item in values]
                for key, values in history.items()
            }
        )
    elif command == "changes":
        _print([asdict(item) for item in manager.changes(args.plan_id)])
    else:  # pragma: no cover
        raise ValidationError(f"unknown plan command: {command}")


def _plan_show(manager: PlanManager, plan_id: str) -> dict[str, Any]:
    data = manager.show(plan_id)
    return {
        "plan": asdict(data["plan"]),
        "clarifications": [asdict(item) for item in data["clarifications"]],
        "snapshot": asdict(data["snapshot"]) if data["snapshot"] else None,
    }


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
        elif args.command == "plan":
            manager = _plan_manager(engine, config, extra_args)
            _handle_plan(manager, args)
        elif args.command == "workflow":
            if args.workflow_command != "start":
                raise ValidationError(f"unknown workflow command: {args.workflow_command}")
            workflow = engine.start_workflow_from_plan(args.plan_id)
            _print(engine.status(workflow.id))
        else:  # pragma: no cover - argparse enforces choices
            raise ValidationError(f"unknown command: {args.command}")
        return 0
    except (DevFlowError, OSError, json.JSONDecodeError) as exc:
        print(f"devflow: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

"""Unified exception hierarchy for the workflow engine."""

from __future__ import annotations


class DevFlowError(Exception):
    """Base class for all engine errors."""


class ValidationError(DevFlowError):
    """Invalid input at a trust boundary."""


class NotFoundError(DevFlowError):
    """Requested entity does not exist."""


class InvalidTransition(DevFlowError):
    """A state change violates the lifecycle state machine."""

    def __init__(self, entity: str, current: str, target: str) -> None:
        self.entity = entity
        self.current = current
        self.target = target
        super().__init__(f"illegal {entity} transition: {current} -> {target}")


class DAGValidationError(ValidationError):
    """DAG contains a cycle, duplicate id, or unknown dependency."""


class AgentExecutionError(DevFlowError):
    """The adapter failed in a way the engine must classify."""


class AgentTimeout(AgentExecutionError):
    """Agent execution exceeded its timeout."""


class AgentInterrupted(AgentExecutionError):
    """Agent process disappeared or was interrupted mid-run."""


class DecisionError(DevFlowError):
    """Human decision request/resolution is invalid."""


class CheckpointError(DevFlowError):
    """Checkpoint could not be built or persisted."""


class EngineLocked(DevFlowError):
    """Another engine holds the scheduling lease for this database."""

    def __init__(
        self,
        message: str,
        *,
        owner: str | None = None,
        hostname: str | None = None,
        pid: int | None = None,
        expires_at: str | None = None,
    ) -> None:
        self.owner = owner
        self.hostname = hostname
        self.pid = pid
        self.expires_at = expires_at
        detail = f"{message} (owner={owner}, host={hostname}, pid={pid}, expires_at={expires_at})"
        super().__init__(detail)

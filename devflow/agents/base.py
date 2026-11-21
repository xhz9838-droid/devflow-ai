"""Agent Adapter contract. Core never imports a concrete agent."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from ..errors import NotFoundError

ProcessStarted = Callable[[int, str | None], None]


class Capability(str, Enum):
    START = "start"
    RESUME_SESSION = "resume_session"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_CALLS = "tool_calls"
    PROCESS_CONTROL = "process_control"


@dataclass(frozen=True)
class AgentRequest:
    workflow_id: str
    node_id: str
    agent_run_id: str
    prompt: str
    workdir: str
    timeout_seconds: float
    context: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    on_process_started: ProcessStarted | None = field(default=None, compare=False, repr=False)


@dataclass
class AgentResult:
    success: bool
    output: str = ""
    exit_code: int | None = None
    error: str | None = None
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentAdapter(ABC):
    name: str = "adapter"
    capabilities: frozenset[Capability] = frozenset()

    @abstractmethod
    def execute(self, request: AgentRequest) -> AgentResult:
        """Run one node. Must raise AgentTimeout/AgentInterrupted for those
        cases so the execution manager can classify the run correctly."""
        raise NotImplementedError

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, AgentAdapter] = {}

    def register(self, adapter: AgentAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> AgentAdapter:
        if name not in self._adapters:
            raise NotFoundError(f"agent adapter not registered: {name}")
        return self._adapters[name]

    def names(self) -> list[str]:
        return sorted(self._adapters)

"""Stable plugin contracts for unAI's replaceable intelligence subsystems.

A plugin is intentionally small and side-effect boundaries are explicit. Experimental
implementations can be loaded without coupling the orchestrator to a particular model
family (transformer, recurrent, state-space, symbolic, graph, retrieval, or hybrid).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class TaskContext:
    task_id: str
    objective: str
    constraints: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    workspace: str | None = None
    budget: dict[str, int | float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Proposal:
    kind: str
    payload: dict[str, Any]
    rationale: str
    assumptions: list[str] = field(default_factory=list)
    risk_score: float = 0.0


@dataclass(slots=True)
class Evidence:
    source: str
    claim: str
    passed: bool | None
    artifact_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class CognitionPlugin(Protocol):
    name: str
    async def propose(self, context: TaskContext) -> list[Proposal]: ...


@runtime_checkable
class MemoryPlugin(Protocol):
    name: str
    async def recall(self, context: TaskContext, limit: int = 8) -> list[dict[str, Any]]: ...
    async def remember(self, context: TaskContext, event: dict[str, Any]) -> None: ...


@runtime_checkable
class RepositoryIndexPlugin(Protocol):
    name: str
    async def index(self, root: str) -> dict[str, Any]: ...
    async def query(self, root: str, query: str, limit: int = 12) -> list[dict[str, Any]]: ...


@runtime_checkable
class VerificationPlugin(Protocol):
    name: str
    async def verify(self, context: TaskContext, proposal: Proposal) -> list[Evidence]: ...


@runtime_checkable
class SearchPlugin(Protocol):
    name: str
    async def explore(self, context: TaskContext, proposals: list[Proposal], width: int = 3) -> list[Proposal]: ...


@dataclass(slots=True)
class PluginRegistry:
    """Explicit registry. No implicit module imports or arbitrary code loading."""
    cognition: dict[str, CognitionPlugin] = field(default_factory=dict)
    memory: dict[str, MemoryPlugin] = field(default_factory=dict)
    indexing: dict[str, RepositoryIndexPlugin] = field(default_factory=dict)
    verification: dict[str, VerificationPlugin] = field(default_factory=dict)
    search: dict[str, SearchPlugin] = field(default_factory=dict)

    def register(self, category: str, plugin: Any) -> None:
        target = getattr(self, category, None)
        if not isinstance(target, dict):
            raise ValueError(f"Unknown plugin category: {category}")
        name = getattr(plugin, "name", None)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Plugins must expose a non-empty stable name")
        if name in target:
            raise ValueError(f"Plugin already registered: {category}/{name}")
        target[name] = plugin

    def manifest(self) -> dict[str, list[str]]:
        return {key: sorted(value) for key, value in {
            "cognition": self.cognition, "memory": self.memory,
            "indexing": self.indexing, "verification": self.verification,
            "search": self.search,
        }.items()}

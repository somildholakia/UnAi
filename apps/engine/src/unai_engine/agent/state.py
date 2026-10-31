from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from datetime import datetime, timezone


class Phase(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    EXPLORING = "exploring"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    BLOCKED = "blocked"


_ALLOWED: dict[Phase, set[Phase]] = {
    Phase.CREATED: {Phase.PLANNING, Phase.BLOCKED},
    Phase.PLANNING: {Phase.EXPLORING, Phase.BLOCKED},
    Phase.EXPLORING: {Phase.EXECUTING, Phase.BLOCKED},
    Phase.EXECUTING: {Phase.VERIFYING, Phase.BLOCKED},
    Phase.VERIFYING: {Phase.EXPLORING, Phase.COMPLETED, Phase.BLOCKED},
    Phase.COMPLETED: set(),
    Phase.BLOCKED: {Phase.PLANNING},
}


@dataclass(slots=True)
class StateEvent:
    phase: Phase
    note: str
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass(slots=True)
class TaskState:
    task_id: str
    objective: str
    phase: Phase = Phase.CREATED
    events: list[StateEvent] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)

    def transition(self, target: Phase, note: str) -> None:
        if target not in _ALLOWED[self.phase]:
            raise ValueError(f"Invalid task transition: {self.phase.value} -> {target.value}")
        self.phase = target
        self.events.append(StateEvent(phase=target, note=note))

    def add_evidence(self, evidence_id: str) -> None:
        if self.phase in {Phase.COMPLETED, Phase.BLOCKED}:
            raise ValueError("Cannot attach evidence to a terminal task state")
        if evidence_id not in self.evidence_ids:
            self.evidence_ids.append(evidence_id)

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


TaskKind = Literal["bugfix", "feature", "review", "research", "general"]
TaskPermission = Literal[
    "workspace_read",
    "workspace_write",
    "run_tests",
    "network_access",
    "install_dependencies",
    "destructive_changes",
]


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=100_000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=50_000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=40)


class ChatResponse(BaseModel):
    message: str
    model: str
    mode: Literal["local-model"] = "local-model"


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    provider_configured: bool
    model: str
    execution: Literal["docker-isolated-only"] = "docker-isolated-only"
    notes: int = 0
    inference: Literal[False] = False
    gpu_required: Literal[False] = False


class PlanStep(BaseModel):
    id: str
    action: Literal["inspect", "search", "edit", "test", "lint", "typecheck", "ask_user", "report"]
    objective: str
    expected_evidence: str


class TaskCompileRequest(BaseModel):
    request: str = Field(min_length=1, max_length=20_000)
    constraints: list[str] = Field(default_factory=list, max_length=40)
    # These are explicit approvals supplied by the caller. The compiler never grants
    # permissions merely because the task appears to need them.
    permissions: list[TaskPermission] = Field(default_factory=list, max_length=6)


class TaskContract(BaseModel):
    task_id: str
    objective: str
    task_type: TaskKind = "general"
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    acceptance_checks: list[str] = Field(default_factory=list)
    permissions: list[TaskPermission] = Field(default_factory=list)
    required_permissions: list[TaskPermission] = Field(default_factory=list)
    missing_permissions: list[TaskPermission] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)


class BrainNoteCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=900_000)
    tags: list[str] = Field(default_factory=list, max_length=40)
    source: str = Field(default="user", max_length=120)

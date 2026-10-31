from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


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


class PlanStep(BaseModel):
    id: str
    action: Literal["inspect", "search", "edit", "test", "lint", "typecheck", "ask_user"]
    objective: str
    expected_evidence: str


class TaskContract(BaseModel):
    objective: str
    constraints: list[str] = Field(default_factory=list)
    acceptance_checks: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)

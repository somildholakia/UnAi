from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, Field, StringConstraints


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
    request: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]
    constraints: list[Annotated[str, StringConstraints(min_length=1, max_length=2_000)]] = Field(
        default_factory=list, max_length=40
    )
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


class TaskInspectRequest(TaskCompileRequest):
    workspace: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)] = "."
    symbol_limit: int = Field(default=12, ge=1, le=100)


class SymbolReference(BaseModel):
    path: str
    name: str
    kind: Literal["function", "method", "class"]
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    score: int = Field(ge=1)
    matched_terms: list[str] = Field(default_factory=list)


class EvidenceRecord(BaseModel):
    evidence_id: str
    task_id: str
    kind: Literal["precondition", "repository", "quality", "discovery", "execution", "patch"]
    source: str
    claim: str
    passed: bool | None = None
    artifact_ref: str | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: str


class TaskInspectionResponse(BaseModel):
    task_id: str
    status: Literal["inspection_complete", "awaiting_workspace_read", "workspace_unavailable"]
    next_gate: Literal["approval_required", "execution_not_implemented", "workspace_access_required", "workspace_correction_required"]
    contract: TaskContract
    workspace: str
    graph_summary: dict = Field(default_factory=dict)
    symbol_matches: list[SymbolReference] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    execution_started: Literal[False] = False


class TaskEvidenceSnapshot(BaseModel):
    task_id: str
    status: str
    workspace: str = "."
    contract: TaskContract
    created_at: str
    evidence: list[EvidenceRecord] = Field(default_factory=list)


class WorkspaceFileSnapshot(BaseModel):
    task_id: str
    file_path: str
    sha256: str
    size_bytes: int = Field(ge=0)
    encoding: Literal["utf-8"] = "utf-8"
    content: str


class PatchProposalRequest(BaseModel):
    file_path: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
    expected_sha256: Annotated[str, StringConstraints(pattern=r"^[a-fA-F0-9]{64}$")]
    proposed_content: Annotated[str, StringConstraints(min_length=1, max_length=500_000)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000)]


class PatchProposalResponse(BaseModel):
    proposal_id: str
    task_id: str
    file_path: str
    expected_sha256: str
    proposed_sha256: str
    status: Literal["pending"] = "pending"
    diff_preview: str = ""
    created_at: str


class PatchApplyApprovalRequest(BaseModel):
    confirm: Literal["apply-patch"]


class PatchApplyResponse(BaseModel):
    proposal_id: str
    task_id: str
    file_path: str
    status: Literal["applied"] = "applied"
    old_sha256: str
    new_sha256: str
    applied_at: str
    evidence: EvidenceRecord


class TaskRunApprovalRequest(BaseModel):
    confirm: Literal["run-python-tests"]


class TaskTestRunResponse(BaseModel):
    task_id: str
    status: Literal["passed", "failed", "unavailable"]
    command: Literal["python -m pytest -q"] = "python -m pytest -q"
    exit_code: int | None = None
    timed_out: bool = False
    sandboxed: bool = False
    staged_files: int = 0
    staged_bytes: int = 0
    skipped_files: int = 0
    stdout_preview: str = ""
    stderr_preview: str = ""
    evidence: EvidenceRecord


class BrainNoteCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=900_000)
    tags: list[str] = Field(default_factory=list, max_length=40)
    source: str = Field(default="user", max_length=120)

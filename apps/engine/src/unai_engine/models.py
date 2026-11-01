from __future__ import annotations

import ipaddress
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, Field, StringConstraints, field_validator


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
    kind: Literal["precondition", "repository", "quality", "discovery", "execution", "patch", "probe", "oracle", "counterexample", "trace", "shrink"]
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


class TaskQualityCheckResponse(BaseModel):
    task_id: str
    workspace: str
    files_discovered: int = Field(ge=0)
    files_parsed: int = Field(ge=0)
    parse_errors: int = Field(ge=0)
    passed: bool
    evidence: EvidenceRecord


class ProbeRequest(BaseModel):
    file_path: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
    symbol_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
    limit: int = Field(default=24, ge=1, le=60)


class ProbeCandidate(BaseModel):
    candidate_id: str
    function_name: str
    source_line: int = Field(ge=1)
    condition: str
    parameter_values: dict
    rationale: str


class ProbeResponse(BaseModel):
    task_id: str
    file_path: str
    symbol_name: str
    source_sha256: str
    status: Literal["generated", "no_supported_conditions", "parse_error"]
    candidates: list[ProbeCandidate] = Field(default_factory=list)
    evidence: EvidenceRecord
    limitations: list[str] = Field(default_factory=list)


class TaskProofResponse(BaseModel):
    task_id: str
    status: Literal[
        "inspection_only",
        "patch_pending",
        "tests_run_no_patch",
        "awaiting_tests",
        "tests_failed",
        "tests_unavailable",
        "tests_passed_after_patch",
        "tests_passed_but_quality_check_failed",
        "tests_passed_but_quality_check_unknown",
    ]
    patch_count: int = Field(ge=0)
    pending_patch_count: int = Field(ge=0)
    latest_patch_sha256: str | None = None
    latest_patch_at: str | None = None
    latest_test_passed: bool | None = None
    latest_test_at: str | None = None
    tests_after_latest_patch: bool = False
    quality_check_passed: bool | None = None
    quality_check_fresh: bool = False
    acceptance_checks: list[str] = Field(default_factory=list)
    remaining_unknowns: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class GroundedPropertyOracle(BaseModel):
    """A fixed, non-executable predicate the observed return value must satisfy."""

    kind: Literal[
        "output_non_negative",
        "output_positive",
        "output_truthy",
        "output_falsy",
        "output_is_none",
        "output_not_none",
        "output_is_empty",
        "output_not_empty",
        "output_equals_argument",
        "raises_exception",
        "output_stable_across_replays",
        "workspace_filesystem_unchanged",
    ]
    replay_count: int = Field(default=1, ge=1, le=4)
    argument_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ] | None = None
    exception_name: Literal[
        "ValueError",
        "TypeError",
        "KeyError",
        "IndexError",
        "ZeroDivisionError",
        "OverflowError",
        "AssertionError",
        "RuntimeError",
        "LookupError",
        "FileNotFoundError",
        "PermissionError",
        "TimeoutError",
    ] | None = None


class GroundedOracleAttestation(BaseModel):
    """Ed25519 signature over one exact, source-bound specification assertion."""

    key_id: Annotated[
        str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    ]
    issued_at: datetime
    signature_b64: Annotated[
        str, StringConstraints(pattern=r"^[A-Za-z0-9+/]{86}==$")
    ]

    @field_validator("issued_at")
    @classmethod
    def require_timezone_aware_issued_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("issued_at must include an explicit timezone.")
        return value


class GroundedOraclePublisherAttestation(BaseModel):
    """External publisher signature over one immutable specification artifact revision."""

    key_id: Annotated[
        str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    ]
    issuer: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=2, max_length=128)
    ]
    artifact_uri: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=12, max_length=1024)
    ]
    artifact_revision: Annotated[
        str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$")
    ]
    issued_at: datetime
    signature_b64: Annotated[
        str, StringConstraints(pattern=r"^[A-Za-z0-9+/]{86}==$")
    ]

    @field_validator("issued_at")
    @classmethod
    def require_timezone_aware_issued_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("issued_at must include an explicit timezone.")
        return value

    @field_validator("artifact_uri")
    @classmethod
    def require_public_https_artifact_uri(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.port not in (None, 443)
        ):
            raise ValueError(
                "Publisher artifact_uri must be a public HTTPS URL without credentials, query, fragment or non-standard port."
            )
        host = parsed.hostname
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("Publisher artifact_uri must use a public DNS hostname, not an IP address.")
        if host.casefold() in {"localhost"} or host.endswith(".localhost") or "." not in host:
            raise ValueError("Publisher artifact_uri must use a public hostname.")
        return value


class GroundedOracleCase(BaseModel):
    case_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")]
    arguments: dict[str, Any]
    instance_arguments: dict[str, Any] | None = None
    expected_output: Any = None
    oracle_property: GroundedPropertyOracle | None = None
    oracle_kind: Literal["user", "specification"]
    oracle_reference: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)
    ]
    oracle_artifact_path: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)
    ] | None = None
    oracle_artifact_sha256: Annotated[
        str, StringConstraints(pattern=r"^[a-fA-F0-9]{64}$")
    ] | None = None
    oracle_attestation: GroundedOracleAttestation | None = None
    oracle_publisher_attestation: GroundedOraclePublisherAttestation | None = None
    probe_candidate_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ] | None = None


class GroundedTestApprovalRequest(BaseModel):
    confirm: Literal["run-grounded-oracle-tests"]
    file_path: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
    symbol_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    source_sha256: Annotated[str, StringConstraints(pattern=r"^[a-fA-F0-9]{64}$")]
    cases: list[GroundedOracleCase] = Field(min_length=1, max_length=20)


class GroundedCounterexample(BaseModel):
    case_id: str
    probe_candidate_id: str | None = None
    oracle_kind: Literal["user", "specification"]
    oracle_reference: str
    oracle_property: GroundedPropertyOracle | None = None
    actual_summary: str
    actual_sha256: Annotated[str, StringConstraints(pattern=r"^[a-fA-F0-9]{64}$")] | None = None
    exception_message_sha256: Annotated[str, StringConstraints(pattern=r"^[a-fA-F0-9]{64}$")] | None = None
    target_line: int | None = Field(default=None, ge=1)
    target_function: Annotated[str, StringConstraints(max_length=128)] | None = None
    replay_count: int | None = Field(default=None, ge=2, le=4)
    replay_trace_sha256: Annotated[str, StringConstraints(pattern=r"^[a-fA-F0-9]{64}$")] | None = None
    filesystem_before_sha256: Annotated[str, StringConstraints(pattern=r"^[a-fA-F0-9]{64}$")] | None = None
    filesystem_after_sha256: Annotated[str, StringConstraints(pattern=r"^[a-fA-F0-9]{64}$")] | None = None
    filesystem_changed_entry_count: int | None = Field(default=None, ge=0)
    filesystem_changed_paths_sha256: Annotated[str, StringConstraints(pattern=r"^[a-fA-F0-9]{64}$")] | None = None
    filesystem_entry_count: int | None = Field(default=None, ge=0)
    filesystem_bytes_hashed: int | None = Field(default=None, ge=0)


class GroundedShrinkResult(BaseModel):
    case_id: str
    status: Literal[
        "smaller_failure_observed",
        "no_smaller_failure_observed",
        "no_supported_integer_arguments",
        "no_supported_numeric_arguments",
        "no_supported_reducible_arguments",
        "budget_exhausted",
        "unavailable",
    ]
    attempts: int = Field(ge=0)
    original_arguments_sha256: str
    smallest_observed_arguments_sha256: str | None = None
    smallest_observed_arguments: dict[str, Any] | None = None
    note: str


class GroundedTestResponse(BaseModel):
    task_id: str
    file_path: str
    symbol_name: str
    source_sha256: str
    status: Literal["passed", "failed", "unavailable"]
    case_count: int = Field(ge=1)
    passed_cases: int | None = Field(default=None, ge=0)
    counterexamples: list[GroundedCounterexample] = Field(default_factory=list)
    shrinking_results: list[GroundedShrinkResult] = Field(default_factory=list)
    execution_evidence: EvidenceRecord
    trace_evidence: EvidenceRecord
    oracle_evidence: list[EvidenceRecord] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class TaskProofBundleResponse(BaseModel):
    task_id: str
    generated_at: str
    overall_status: Literal[
        "no_grounded_tests",
        "grounded_tests_passed",
        "grounded_tests_failed",
        "grounded_tests_unavailable",
        "counterexamples_found",
    ]
    task_proof: TaskProofResponse
    grounded_case_count: int = Field(ge=0)
    grounded_run_count: int = Field(ge=0)
    grounded_counterexample_count: int = Field(ge=0)
    shrink_attempt_count: int = Field(ge=0)
    shrunk_counterexample_count: int = Field(ge=0)
    unverified_probe_candidate_count: int = Field(ge=0)
    counterexamples: list[GroundedCounterexample] = Field(default_factory=list)
    oracle_evidence: list[EvidenceRecord] = Field(default_factory=list)
    shrink_evidence: list[EvidenceRecord] = Field(default_factory=list)
    execution_traces: list[EvidenceRecord] = Field(default_factory=list)
    unresolved_risks: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class BrainNoteCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=900_000)
    tags: list[str] = Field(default_factory=list, max_length=40)
    source: str = Field(default="user", max_length=120)

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from fastapi.responses import JSONResponse
from uuid import uuid4
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from unai_engine.agent.orchestrator import AgentOrchestrator, SpecialistUnavailable
from unai_engine.config import settings
from unai_engine.models import (
    ApiTokenCreateRequest, ApiTokenCreateResponse, ApiTokenRevokeResponse,
    BrainNoteCreate, ChatRequest, ChatResponse, HealthResponse,
    SpecialistChatRequest, SpecialistChatResponse,
    TaskCompileRequest, TaskContract, TaskInspectRequest,
    TaskInspectionResponse, TaskEvidenceSnapshot, TaskRunApprovalRequest, TaskTestRunResponse,
    PatchProposalRequest, PatchProposalResponse, PatchApplyApprovalRequest, PatchApplyResponse,
    WorkspaceFileSnapshot, TaskProofResponse, ProbeRequest, ProbeResponse,
    TaskQualityCheckResponse, TaskCodeContextResponse, CodeContextSlice,
    EvidenceRecord, GroundedTestApprovalRequest,
    GroundedTestResponse, TaskProofBundleResponse,
  )
from unai_engine.providers.openai_compat import OpenAICompatibleProvider, ProviderError
from unai_engine.plugins.builtin import build_registry
from unai_engine.brain.vault import BrainError, MarkdownVault
from unai_engine.cognition.task_compiler import compile_task
from unai_engine.agent.inspection import TaskInspector
from unai_engine.verification.ledger import EvidenceLedger
from unai_engine.runtime.sandbox import DockerSandbox
from unai_engine.runtime.test_runner import IsolatedPythonTestRunner, TaskRunRejected
from unai_engine.runtime.patches import PatchConflict, PatchManager, PatchRejected
from unai_engine.runtime.workspace import WorkspaceError, resolve_workspace_path
from unai_engine.verification.proof import summarize_proof
from unai_engine.verification.probes import BoundaryProbeGenerator, ProbeTargetError
from unai_engine.verification.grounded import (
    GroundedTestVerifier,
    GroundedVerificationError,
    build_proof_bundle,
)
from unai_engine.security import (
    ALL_SCOPES,
    ApiTokenStore,
    AuthContext,
    TokenStoreError,
    resolve_api_identity,
)
from unai_engine.tenancy import TenantServices, build_tenant_services

provider = OpenAICompatibleProvider(
    base_url=settings.model_base_url,
    model=settings.model_name,
    api_key=settings.model_api_key,
    timeout=settings.model_timeout_seconds,
)
plugins = build_registry(code_graph_db_path=settings.code_graph_db_path)
brain = MarkdownVault(settings.brain_vault_root, settings.brain_index_path)
agent = AgentOrchestrator(
    provider,
    context_search=brain.search,
    specialist_models={
        "next_action": settings.specialist_next_action_model,
        "planner": settings.specialist_planner_model,
        "verifier": settings.specialist_verifier_model,
    },
)
evidence_ledger = EvidenceLedger(settings.evidence_db_path)
task_inspector = TaskInspector(plugins.indexing["python-code-graph"], evidence_ledger)
task_test_runner = IsolatedPythonTestRunner(
    evidence_ledger,
    DockerSandbox(settings.docker_image, settings.test_timeout_seconds),
    max_files=settings.test_max_files,
    max_bytes=settings.test_max_bytes,
)
patch_manager = PatchManager(evidence_ledger)
probe_generator = BoundaryProbeGenerator(evidence_ledger)
grounded_test_verifier = GroundedTestVerifier(
    evidence_ledger,
    patch_manager,
    task_test_runner,
    trusted_oracle_public_keys=settings.oracle_trusted_public_keys,
    trusted_oracle_publisher_keys=settings.oracle_trusted_publisher_keys,
)

api_token_store = ApiTokenStore(settings.api_token_store_path)
_tenant_services_cache: dict[str, TenantServices] = {}
_tenant_services_lock = RLock()


def _request_identity(request: Request) -> AuthContext:
    identity = getattr(request.state, "identity", None)
    if not isinstance(identity, AuthContext):
        raise HTTPException(status_code=401, detail="Authenticated request identity is unavailable.")
    return identity


def _require_scopes(request: Request, *required: str) -> AuthContext:
    identity = _request_identity(request)
    missing = set(required) - identity.scopes
    if not identity.is_admin and missing:
        raise HTTPException(
            status_code=403,
            detail={"message": "The API token lacks required scopes.", "required_scopes": sorted(missing)},
        )
    return identity


def _require_admin(request: Request) -> AuthContext:
    identity = _request_identity(request)
    if not identity.is_admin:
        raise HTTPException(status_code=403, detail="Only an installation administrator can manage API tokens.")
    return identity


def _tenant_services(request: Request) -> TenantServices:
    identity = _request_identity(request)
    if identity.tenant_id == "default":
        return TenantServices(
            tenant_id="default", workspace_root=Path(settings.workspace_root),
            plugins=plugins, brain=brain, agent=agent, evidence_ledger=evidence_ledger,
            task_inspector=task_inspector, task_test_runner=task_test_runner,
            patch_manager=patch_manager, probe_generator=probe_generator,
            grounded_test_verifier=grounded_test_verifier,
        )
    with _tenant_services_lock:
        cached = _tenant_services_cache.get(identity.tenant_id)
        if cached is not None:
            return cached
        try:
            services = build_tenant_services(
                identity.tenant_id, Path(settings.tenant_workspace_root),
                Path(settings.tenant_data_root), provider,
                specialist_models={
                    "next_action": settings.specialist_next_action_model,
                    "planner": settings.specialist_planner_model,
                    "verifier": settings.specialist_verifier_model,
                },
                docker_image=settings.docker_image,
                sandbox_timeout_seconds=settings.sandbox_timeout_seconds,
                test_max_files=settings.test_max_files,
                test_max_bytes=settings.test_max_bytes,
                trusted_oracle_public_keys=settings.oracle_trusted_public_keys,
                trusted_oracle_publisher_keys=settings.oracle_trusted_publisher_keys,
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=503, detail="Tenant storage could not be initialized safely.") from exc
        _tenant_services_cache[identity.tenant_id] = services
        return services


app = FastAPI(
    title="unAI Engine",
    version="0.1.0",
    description="Local-first, evidence-driven AI engineering prototype.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.middleware("http")
async def enforce_api_access(request: Request, call_next):
    """Authenticate each request and apply limits before any tenant data access."""
    if request.method != "OPTIONS":
        client_host = request.client.host if request.client else None
        try:
            identity, denial = resolve_api_identity(
                client_host, request.headers.get("Authorization"),
                getattr(settings, "api_token", ""), api_token_store,
            )
            if denial is None and identity is not None:
                allowed = api_token_store.allow_request(
                    identity, client_host, getattr(settings, "api_rate_limit_per_minute", 600)
                )
                if not allowed:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "API request rate limit exceeded. Retry after the current minute."},
                        headers={"Retry-After": "60"},
                    )
        except (TokenStoreError, OSError):
            return JSONResponse(status_code=503, content={"detail": "API authentication storage is unavailable."})
        if denial is not None or identity is None:
            status_code, detail = denial or (401, "Authentication failed.")
            headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
            return JSONResponse(status_code=status_code, content={"detail": detail}, headers=headers)
        request.state.identity = identity
    return await call_next(request)


@app.post("/api/auth/tokens", response_model=ApiTokenCreateResponse)
def create_api_token(request: ApiTokenCreateRequest, http_request: Request) -> ApiTokenCreateResponse:
    """Issue a scoped token; the opaque secret is returned once, never stored in plaintext."""
    _require_admin(http_request)
    try:
        created = api_token_store.create_token(
            request.tenant_id, request.label, request.scopes, request.expires_in_days
        )
        return ApiTokenCreateResponse(**created)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/auth/tokens/{token_id}/revoke", response_model=ApiTokenRevokeResponse)
def revoke_api_token(token_id: str, http_request: Request) -> ApiTokenRevokeResponse:
    """Revoke a scoped token immediately; every request checks revocation state."""
    _require_admin(http_request)
    try:
        revoked = api_token_store.revoke(token_id)
    except (OSError, TokenStoreError) as exc:
        raise HTTPException(status_code=503, detail="API token storage is unavailable.") from exc
    if not revoked:
        raise HTTPException(status_code=404, detail="Active API token not found.")
    return ApiTokenRevokeResponse(token_id=token_id, revoked=True)


@app.get("/api/auth/me")
def get_api_identity(http_request: Request) -> dict:
    identity = _request_identity(http_request)
    return {"tenant_id": identity.tenant_id, "token_id": identity.token_id,
            "scopes": sorted(identity.scopes), "administrator": identity.is_admin}


@app.post("/api/tasks/compile", response_model=TaskContract)
def task_compile(request: TaskCompileRequest, http_request: Request) -> TaskContract:
    """Compile a request into a provisional contract without running tools."""
    _require_scopes(http_request, "tasks:write")
    return compile_task(request)


def _repository_workspace(workspace: str, workspace_root: Path):
    try:
        root = resolve_workspace_path(workspace_root, workspace)
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not root.exists():
        raise HTTPException(status_code=404, detail="Workspace not found.")
    if not root.is_dir():
        raise HTTPException(status_code=400, detail="Workspace path must be a directory.")
    return root


@app.get("/api/repository/graph")
async def repository_graph(http_request: Request, workspace: str = ".") -> dict:
    """Return a bounded Python symbol/import/test graph for the caller's tenant."""
    _require_scopes(http_request, "workspace:read")
    services = _tenant_services(http_request)
    root = _repository_workspace(workspace, services.workspace_root)
    graph = await services.plugins.indexing["python-code-graph"].index(str(root))
    graph["root"] = workspace
    return graph


@app.get("/api/repository/search")
async def repository_search(
    http_request: Request,
    q: str = Query(min_length=1, max_length=200),
    workspace: str = ".",
    limit: int = Query(default=12, ge=1, le=100),
) -> dict:
    """Search Python symbols and return source line ranges, not source bodies."""
    _require_scopes(http_request, "workspace:read")
    services = _tenant_services(http_request)
    root = _repository_workspace(workspace, services.workspace_root)
    results = await services.plugins.indexing["python-code-graph"].query(str(root), q, limit)
    return {"workspace": workspace, "query": q, "results": results}


@app.post("/api/tasks/inspect", response_model=TaskInspectionResponse)
async def inspect_task(request: TaskInspectRequest, http_request: Request) -> TaskInspectionResponse:
    """Compile, inspect a workspace read-only, and persist an evidence snapshot."""
    _require_scopes(http_request, "tasks:write", "workspace:read")
    services = _tenant_services(http_request)
    return await services.task_inspector.inspect(request, services.workspace_root)


@app.post("/api/tasks/{task_id}/run-tests", response_model=TaskTestRunResponse)
async def run_task_tests(task_id: str, request: TaskRunApprovalRequest, http_request: Request) -> TaskTestRunResponse:
    """Run only the fixed pytest command after a separate explicit confirmation."""
    _require_scopes(http_request, "tasks:write", "tasks:read", "workspace:execute")
    services = _tenant_services(http_request)
    if request.confirm != "run-python-tests":
        raise HTTPException(status_code=422, detail="Explicit Python test confirmation is required.")
    try:
        return await asyncio.to_thread(services.task_test_runner.run, task_id, services.workspace_root)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task evidence not found.") from exc
    except TaskRunRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get(
    "/api/tasks/{task_id}/files/{file_path:path}",
    response_model=WorkspaceFileSnapshot,
)
def read_task_file(task_id: str, file_path: str, http_request: Request) -> WorkspaceFileSnapshot:
    """Read one bounded UTF-8 workspace file after explicit workspace-read approval."""
    _require_scopes(http_request, "tasks:read", "workspace:read")
    services = _tenant_services(http_request)
    try:
        return services.patch_manager.read_file(task_id, file_path, services.workspace_root)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task evidence not found.") from exc
    except PatchRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PatchConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/tasks/{task_id}/patch-proposals", response_model=PatchProposalResponse)
def create_patch_proposal(task_id: str, request: PatchProposalRequest, http_request: Request) -> PatchProposalResponse:
    """Store a candidate one-file replacement; the workspace is not changed yet."""
    _require_scopes(http_request, "tasks:write", "workspace:read", "workspace:write")
    services = _tenant_services(http_request)
    try:
        return services.patch_manager.propose(task_id, request, services.workspace_root)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task evidence not found.") from exc
    except PatchRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PatchConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(
    "/api/tasks/{task_id}/patch-proposals/{proposal_id}/apply",
    response_model=PatchApplyResponse,
)
def apply_patch_proposal(
    task_id: str,
    proposal_id: str,
    request: PatchApplyApprovalRequest,
    http_request: Request,
) -> PatchApplyResponse:
    """Apply a pending patch only after the separate apply-patch confirmation."""
    _require_scopes(http_request, "tasks:write", "workspace:read", "workspace:write")
    services = _tenant_services(http_request)
    if request.confirm != "apply-patch":
        raise HTTPException(status_code=422, detail="Explicit patch-application confirmation is required.")
    try:
        return services.patch_manager.apply(task_id, proposal_id, services.workspace_root)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task or patch proposal not found.") from exc
    except PatchRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PatchConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/tasks/{task_id}/probes", response_model=ProbeResponse)
def generate_task_probes(task_id: str, request: ProbeRequest, http_request: Request) -> ProbeResponse:
    """Generate unverified AST boundary inputs for an approved Python workspace file."""
    _require_scopes(http_request, "tasks:write", "workspace:read")
    services = _tenant_services(http_request)
    if not request.file_path.casefold().endswith(".py"):
        raise HTTPException(status_code=415, detail="AST boundary probes currently support Python files only.")
    try:
        source = services.patch_manager.read_file(task_id, request.file_path, services.workspace_root)
        return services.probe_generator.generate(
            task_id,
            source.file_path,
            request.symbol_name,
            source.content,
            source.sha256,
            request.limit,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task evidence not found.") from exc
    except PatchRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PatchConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProbeTargetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/tasks/{task_id}/grounded-tests", response_model=GroundedTestResponse)
async def run_grounded_task_tests(
    task_id: str,
    request: GroundedTestApprovalRequest,
    http_request: Request,
) -> GroundedTestResponse:
    """Run explicitly grounded expected-output cases in the disposable Docker sandbox."""
    _require_scopes(http_request, "tasks:write", "workspace:read", "workspace:execute")
    services = _tenant_services(http_request)
    if request.confirm != "run-grounded-oracle-tests":
        raise HTTPException(status_code=422, detail="Explicit grounded-test confirmation is required.")
    try:
        return await asyncio.to_thread(
            services.grounded_test_verifier.run,
            task_id,
            request,
            services.workspace_root,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task evidence not found.") from exc
    except PatchRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PatchConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GroundedVerificationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/tasks/{task_id}/quality-check", response_model=TaskQualityCheckResponse)
async def refresh_task_quality(task_id: str, http_request: Request) -> TaskQualityCheckResponse:
    """Re-index the approved workspace after changes and append fresh parse evidence."""
    _require_scopes(http_request, "tasks:write", "workspace:read")
    services = _tenant_services(http_request)
    snapshot = services.evidence_ledger.get_snapshot(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task evidence not found.")
    if "workspace_read" not in snapshot.contract.permissions:
        raise HTTPException(status_code=403, detail="This task has not explicitly approved workspace_read.")
    if snapshot.status != "inspection_complete":
        raise HTTPException(status_code=409, detail="Task must complete inspection before quality checks.")
    try:
        root = resolve_workspace_path(services.workspace_root, snapshot.workspace)
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail="Workspace path is no longer valid.") from exc
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="Inspected workspace no longer exists.")

    graph = await services.plugins.indexing["python-code-graph"].index(str(root))
    summary = dict(graph.get("summary", {}))
    parse_errors = int(summary.get("parse_errors", 0))
    passed = parse_errors == 0
    evidence = EvidenceRecord(
        evidence_id=uuid4().hex,
        task_id=task_id,
        kind="quality",
        source="python-ast-parser",
        claim=f"Python source parse errors after refresh: {parse_errors}",
        passed=passed,
        artifact_ref=f"workspace:{snapshot.workspace}",
        metadata={
            **summary,
            "errors": graph.get("errors", [])[:10],
            "checked_after_inspection": True,
        },
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    services.evidence_ledger.append_evidence(evidence)
    return TaskQualityCheckResponse(
        task_id=task_id,
        workspace=snapshot.workspace,
        files_discovered=int(summary.get("files_discovered", 0)),
        files_parsed=int(summary.get("files_parsed", 0)),
        parse_errors=parse_errors,
        passed=passed,
        evidence=evidence,
    )


@app.get("/api/tasks/{task_id}/proof", response_model=TaskProofResponse)
def task_proof(task_id: str, http_request: Request) -> TaskProofResponse:
    """Summarize the latest patch/test sequence without claiming semantic correctness."""
    _require_scopes(http_request, "tasks:read")
    snapshot = _tenant_services(http_request).evidence_ledger.get_snapshot(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task evidence not found.")
    return summarize_proof(snapshot)


@app.get("/api/tasks/{task_id}/proof-bundle", response_model=TaskProofBundleResponse)
def task_proof_bundle(task_id: str, http_request: Request) -> TaskProofBundleResponse:
    """Return grounded oracle cases, counterexamples, execution traces and unresolved risks."""
    _require_scopes(http_request, "tasks:read")
    snapshot = _tenant_services(http_request).evidence_ledger.get_snapshot(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task evidence not found.")
    return build_proof_bundle(snapshot)


@app.get("/api/tasks/{task_id}/evidence", response_model=TaskEvidenceSnapshot)
def task_evidence(task_id: str, http_request: Request) -> TaskEvidenceSnapshot:
    _require_scopes(http_request, "tasks:read")
    snapshot = _tenant_services(http_request).evidence_ledger.get_snapshot(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task evidence not found.")
    return snapshot


@app.get("/api/tasks/{task_id}/context", response_model=TaskCodeContextResponse)
def task_code_context(
    http_request: Request,
    task_id: str,
    limit: int = Query(default=5, ge=1, le=10),
    context_lines: int = Query(default=4, ge=0, le=8),
) -> TaskCodeContextResponse:
    """Return bounded, hash-pinned excerpts for symbols found by this approved inspection."""
    _require_scopes(http_request, "tasks:read", "workspace:read")
    services = _tenant_services(http_request)
    snapshot = services.evidence_ledger.get_snapshot(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task evidence not found.")
    if snapshot.status != "inspection_complete":
        raise HTTPException(status_code=409, detail="Task must complete inspection before context retrieval.")
    if "workspace_read" not in snapshot.contract.permissions:
        raise HTTPException(status_code=403, detail="This task has not explicitly approved workspace_read.")

    discovery = next(
        (
            item for item in reversed(snapshot.evidence)
            if item.kind == "discovery" and item.source == "symbol-retrieval"
        ),
        None,
    )
    matches = discovery.metadata.get("matches", []) if discovery else []
    slices: list[CodeContextSlice] = []
    total_characters = 0
    seen: set[tuple[str, str, int, int]] = set()
    max_slice_lines = 120
    max_slice_characters = 6_000
    max_total_characters = 24_000

    for match in matches:
        if len(slices) >= limit or not isinstance(match, dict):
            break
        path = match.get("path")
        name = match.get("name")
        kind = match.get("kind")
        expected_sha = match.get("source_sha256")
        start_line = match.get("start_line")
        end_line = match.get("end_line")
        if (
            not isinstance(path, str)
            or not isinstance(name, str)
            or kind not in {"function", "method", "class"}
            or not isinstance(expected_sha, str)
            or len(expected_sha) != 64
            or any(char not in "0123456789abcdefABCDEF" for char in expected_sha)
            or not isinstance(start_line, int)
            or isinstance(start_line, bool)
            or not isinstance(end_line, int)
            or isinstance(end_line, bool)
            or start_line < 1
            or end_line < start_line
        ):
            raise HTTPException(
                status_code=409,
                detail="Inspection lacks valid source fingerprints; re-inspect the workspace.",
            )
        identity = (path, name, start_line, end_line)
        if identity in seen:
            continue
        seen.add(identity)

        try:
            source = services.patch_manager.read_file(task_id, path, services.workspace_root)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Task evidence not found.") from exc
        except PatchRejected as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except PatchConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        if source.sha256.casefold() != expected_sha.casefold():
            raise HTTPException(
                status_code=409,
                detail=f"Source changed since inspection ({path}); re-inspect before retrieving context.",
            )

        lines = source.content.splitlines()
        if end_line > len(lines):
            raise HTTPException(
                status_code=409,
                detail=f"Source line range changed since inspection ({path}); re-inspect before retrieving context.",
            )
        excerpt_start = max(1, start_line - context_lines)
        requested_end = min(len(lines), end_line + context_lines)
        line_end = min(requested_end, excerpt_start + max_slice_lines - 1)
        remaining_total = max_total_characters - total_characters
        character_budget = min(max_slice_characters, remaining_total)
        numbered: list[str] = []
        excerpt_end = excerpt_start - 1
        used_characters = 0
        truncated = line_end < requested_end
        for line_number in range(excerpt_start, line_end + 1):
            line = f"{line_number}: {lines[line_number - 1]}"
            separator = 1 if numbered else 0
            available = character_budget - used_characters - separator
            if available <= 0:
                truncated = True
                break
            if len(line) > available:
                if not numbered:
                    numbered.append(line[:available])
                    used_characters += available
                    excerpt_end = line_number
                truncated = True
                break
            numbered.append(line)
            used_characters += separator + len(line)
            excerpt_end = line_number
        content = "\n".join(numbered)
        if not content:
            break
        total_characters += len(content)
        slices.append(
            CodeContextSlice(
                path=path,
                name=name,
                kind=kind,
                source_start_line=start_line,
                source_end_line=end_line,
                excerpt_start_line=excerpt_start,
                excerpt_end_line=max(excerpt_start, excerpt_end),
                sha256=source.sha256,
                content=content,
                truncated=truncated,
            )
        )
        if total_characters >= max_total_characters:
            break

    return TaskCodeContextResponse(
        task_id=task_id,
        workspace=snapshot.workspace,
        slices=slices,
        total_characters=total_characters,
    )


@app.get("/api/plugins")
async def plugin_manifest() -> dict[str, list[str]]:
    return plugins.manifest()


@app.get("/api/health", response_model=HealthResponse)
async def health(http_request: Request) -> HealthResponse:
    services = _tenant_services(http_request)
    return HealthResponse(provider_configured=provider.configured, model=provider.model, notes=services.brain.status()["notes"])


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, http_request: Request) -> ChatResponse:
    _require_scopes(http_request, "chat:use")
    services = _tenant_services(http_request)
    history = [{"role": item.role, "content": item.content} for item in request.history]
    try:
        answer = await services.agent.chat(request.message, history)
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ChatResponse(message=answer, model=provider.model)


@app.post(
    "/api/specialists/{specialist}/chat",
    response_model=SpecialistChatResponse,
)
async def specialist_chat(
    specialist: Literal["next_action", "planner", "verifier"],
    request: SpecialistChatRequest,
    http_request: Request,
) -> SpecialistChatResponse:
    """Call only the configured specialist model; missing adapters never fall back."""
    _require_scopes(http_request, "chat:use")
    services = _tenant_services(http_request)
    history = [{"role": item.role, "content": item.content} for item in request.history]
    try:
        answer, selected_model = await services.agent.specialist_chat(
            specialist, request.message, history
        )
    except SpecialistUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return SpecialistChatResponse(
        specialist=specialist,
        message=answer,
        model=selected_model,
    )


@app.get("/api/brain/status")
def brain_status(http_request: Request) -> dict:
    _require_scopes(http_request, "brain:read")
    return _tenant_services(http_request).brain.status()


@app.post("/api/brain/index")
def brain_index(http_request: Request) -> dict:
    _require_scopes(http_request, "brain:write")
    return _tenant_services(http_request).brain.index()


@app.get("/api/brain/search")
def brain_search(http_request: Request, q: str, limit: int = 10) -> dict:
    _require_scopes(http_request, "brain:read")
    return {"results": _tenant_services(http_request).brain.search(q, limit)}


@app.get("/api/brain/graph")
def brain_graph(http_request: Request, limit: int = 500) -> dict:
    _require_scopes(http_request, "brain:read")
    return _tenant_services(http_request).brain.graph(limit)


@app.get("/api/brain/notes/{note_path:path}")
def brain_read(note_path: str, http_request: Request) -> dict:
    _require_scopes(http_request, "brain:read")
    try:
        return _tenant_services(http_request).brain.read(note_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Brain note not found. Index the vault first.") from exc
    except BrainError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/brain/notes")
def brain_create(
    note: BrainNoteCreate,
    request: Request,
    brain_write_token: str | None = Header(default=None, alias="X-Brain-Write-Token"),
) -> dict:
    identity = _require_scopes(request, "brain:write")
    if identity.is_admin:
        # Preserve the legacy second-secret gate for installation-wide remote writes.
        client_host = request.client.host if request.client else ""
        is_loopback = client_host in {"127.0.0.1", "::1", "testclient"}
        authorized = (brain_write_token == settings.brain_write_token) if settings.brain_write_token else is_loopback
        if not authorized:
            raise HTTPException(status_code=401, detail="A valid brain write token is required for remote writes.")
    try:
        return _tenant_services(request).brain.create(note.title, note.body, note.tags, note.source)
    except BrainError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# API aliases matching the hosted Worker contract, so the same static brain UI
# works against either the local engine or the Cloudflare-hosted API.
@app.get("/api/search")
def brain_search_alias(http_request: Request, q: str, limit: int = 10) -> dict:
    _require_scopes(http_request, "brain:read")
    return {"results": _tenant_services(http_request).brain.search(q, limit)}


@app.get("/api/graph")
def brain_graph_alias(http_request: Request, limit: int = 500) -> dict:
    _require_scopes(http_request, "brain:read")
    return _tenant_services(http_request).brain.graph(limit)


@app.get("/api/notes/{note_path:path}")
def brain_read_alias(note_path: str, http_request: Request) -> dict:
    _require_scopes(http_request, "brain:read")
    try:
        return _tenant_services(http_request).brain.read(note_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Brain note not found. Index the vault first.") from exc
    except BrainError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/notes")
def brain_create_alias(
    note: BrainNoteCreate,
    request: Request,
    brain_write_token: str | None = Header(default=None, alias="X-Brain-Write-Token"),
) -> dict:
    return brain_create(note, request, brain_write_token)

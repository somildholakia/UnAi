from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi.responses import JSONResponse
from uuid import uuid4
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from unai_engine.agent.orchestrator import AgentOrchestrator, SpecialistUnavailable
from unai_engine.config import settings
from unai_engine.models import (
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
from unai_engine.security import api_access_error

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
    """Require an installation bearer token or restrict tokenless access to loopback."""
    if request.method != "OPTIONS":
        client_host = request.client.host if request.client else None
        denial = api_access_error(
            client_host,
            request.headers.get("Authorization"),
            getattr(settings, "api_token", ""),
        )
        if denial is not None:
            status_code, detail = denial
            headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
            return JSONResponse(
                status_code=status_code,
                content={"detail": detail},
                headers=headers,
            )
    return await call_next(request)


@app.post("/api/tasks/compile", response_model=TaskContract)
def task_compile(request: TaskCompileRequest) -> TaskContract:
    """Compile a request into a provisional contract without running tools."""
    return compile_task(request)


def _repository_workspace(workspace: str):
    try:
        root = resolve_workspace_path(settings.workspace_root, workspace)
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not root.exists():
        raise HTTPException(status_code=404, detail="Workspace not found.")
    if not root.is_dir():
        raise HTTPException(status_code=400, detail="Workspace path must be a directory.")
    return root


@app.get("/api/repository/graph")
async def repository_graph(workspace: str = ".") -> dict:
    """Return a bounded Python symbol/import/test graph for a workspace subdirectory."""
    root = _repository_workspace(workspace)
    graph = await plugins.indexing["python-code-graph"].index(str(root))
    graph["root"] = workspace
    return graph


@app.get("/api/repository/search")
async def repository_search(
    q: str = Query(min_length=1, max_length=200),
    workspace: str = ".",
    limit: int = Query(default=12, ge=1, le=100),
) -> dict:
    """Search Python symbols and return source line ranges, not source bodies."""
    root = _repository_workspace(workspace)
    results = await plugins.indexing["python-code-graph"].query(str(root), q, limit)
    return {"workspace": workspace, "query": q, "results": results}


@app.post("/api/tasks/inspect", response_model=TaskInspectionResponse)
async def inspect_task(request: TaskInspectRequest) -> TaskInspectionResponse:
    """Compile, inspect a workspace read-only, and persist an evidence snapshot."""
    return await task_inspector.inspect(request, settings.workspace_root)


@app.post("/api/tasks/{task_id}/run-tests", response_model=TaskTestRunResponse)
async def run_task_tests(task_id: str, request: TaskRunApprovalRequest) -> TaskTestRunResponse:
    """Run only the fixed pytest command after a separate explicit confirmation."""
    if request.confirm != "run-python-tests":
        raise HTTPException(status_code=422, detail="Explicit Python test confirmation is required.")
    try:
        return await asyncio.to_thread(task_test_runner.run, task_id, settings.workspace_root)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task evidence not found.") from exc
    except TaskRunRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get(
    "/api/tasks/{task_id}/files/{file_path:path}",
    response_model=WorkspaceFileSnapshot,
)
def read_task_file(task_id: str, file_path: str) -> WorkspaceFileSnapshot:
    """Read one bounded UTF-8 workspace file after explicit workspace-read approval."""
    try:
        return patch_manager.read_file(task_id, file_path, settings.workspace_root)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task evidence not found.") from exc
    except PatchRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PatchConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/tasks/{task_id}/patch-proposals", response_model=PatchProposalResponse)
def create_patch_proposal(task_id: str, request: PatchProposalRequest) -> PatchProposalResponse:
    """Store a candidate one-file replacement; the workspace is not changed yet."""
    try:
        return patch_manager.propose(task_id, request, settings.workspace_root)
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
) -> PatchApplyResponse:
    """Apply a pending patch only after the separate apply-patch confirmation."""
    if request.confirm != "apply-patch":
        raise HTTPException(status_code=422, detail="Explicit patch-application confirmation is required.")
    try:
        return patch_manager.apply(task_id, proposal_id, settings.workspace_root)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task or patch proposal not found.") from exc
    except PatchRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PatchConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/tasks/{task_id}/probes", response_model=ProbeResponse)
def generate_task_probes(task_id: str, request: ProbeRequest) -> ProbeResponse:
    """Generate unverified AST boundary inputs for an approved Python workspace file."""
    if not request.file_path.casefold().endswith(".py"):
        raise HTTPException(status_code=415, detail="AST boundary probes currently support Python files only.")
    try:
        source = patch_manager.read_file(task_id, request.file_path, settings.workspace_root)
        return probe_generator.generate(
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
) -> GroundedTestResponse:
    """Run explicitly grounded expected-output cases in the disposable Docker sandbox."""
    if request.confirm != "run-grounded-oracle-tests":
        raise HTTPException(status_code=422, detail="Explicit grounded-test confirmation is required.")
    try:
        return await asyncio.to_thread(
            grounded_test_verifier.run,
            task_id,
            request,
            settings.workspace_root,
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
async def refresh_task_quality(task_id: str) -> TaskQualityCheckResponse:
    """Re-index the approved workspace after changes and append fresh parse evidence."""
    snapshot = evidence_ledger.get_snapshot(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task evidence not found.")
    if "workspace_read" not in snapshot.contract.permissions:
        raise HTTPException(status_code=403, detail="This task has not explicitly approved workspace_read.")
    if snapshot.status != "inspection_complete":
        raise HTTPException(status_code=409, detail="Task must complete inspection before quality checks.")
    try:
        root = resolve_workspace_path(settings.workspace_root, snapshot.workspace)
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail="Workspace path is no longer valid.") from exc
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="Inspected workspace no longer exists.")

    graph = await plugins.indexing["python-code-graph"].index(str(root))
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
    evidence_ledger.append_evidence(evidence)
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
def task_proof(task_id: str) -> TaskProofResponse:
    """Summarize the latest patch/test sequence without claiming semantic correctness."""
    snapshot = evidence_ledger.get_snapshot(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task evidence not found.")
    return summarize_proof(snapshot)


@app.get("/api/tasks/{task_id}/proof-bundle", response_model=TaskProofBundleResponse)
def task_proof_bundle(task_id: str) -> TaskProofBundleResponse:
    """Return grounded oracle cases, counterexamples, execution traces and unresolved risks."""
    snapshot = evidence_ledger.get_snapshot(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task evidence not found.")
    return build_proof_bundle(snapshot)


@app.get("/api/tasks/{task_id}/evidence", response_model=TaskEvidenceSnapshot)
def task_evidence(task_id: str) -> TaskEvidenceSnapshot:
    snapshot = evidence_ledger.get_snapshot(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task evidence not found.")
    return snapshot


@app.get("/api/tasks/{task_id}/context", response_model=TaskCodeContextResponse)
def task_code_context(
    task_id: str,
    limit: int = Query(default=5, ge=1, le=10),
    context_lines: int = Query(default=4, ge=0, le=8),
) -> TaskCodeContextResponse:
    """Return bounded, hash-pinned excerpts for symbols found by this approved inspection."""
    snapshot = evidence_ledger.get_snapshot(task_id)
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
            source = patch_manager.read_file(task_id, path, settings.workspace_root)
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
async def health() -> HealthResponse:
    return HealthResponse(provider_configured=provider.configured, model=provider.model, notes=brain.status()["notes"])


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    history = [{"role": item.role, "content": item.content} for item in request.history]
    try:
        answer = await agent.chat(request.message, history)
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
) -> SpecialistChatResponse:
    """Call only the configured specialist model; missing adapters never fall back."""
    history = [{"role": item.role, "content": item.content} for item in request.history]
    try:
        answer, selected_model = await agent.specialist_chat(
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
def brain_status() -> dict:
    return brain.status()


@app.post("/api/brain/index")
def brain_index() -> dict:
    return brain.index()


@app.get("/api/brain/search")
def brain_search(q: str, limit: int = 10) -> dict:
    return {"results": brain.search(q, limit)}


@app.get("/api/brain/graph")
def brain_graph(limit: int = 500) -> dict:
    return brain.graph(limit)


@app.get("/api/brain/notes/{note_path:path}")
def brain_read(note_path: str) -> dict:
    try:
        return brain.read(note_path)
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
    # The Brain write secret is separate from the Engine Authorization bearer token.
    # Tokenless writes remain restricted to loopback/test clients.
    client_host = request.client.host if request.client else ""
    is_loopback = client_host in {"127.0.0.1", "::1", "testclient"}
    if settings.brain_write_token:
        authorized = brain_write_token == settings.brain_write_token
    else:
        authorized = is_loopback
    if not authorized:
        raise HTTPException(status_code=401, detail="A valid brain write token is required for remote writes.")
    try:
        return brain.create(note.title, note.body, note.tags, note.source)
    except BrainError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# API aliases matching the hosted Worker contract, so the same static brain UI
# works against either the local engine or the Cloudflare-hosted API.
@app.get("/api/search")
def brain_search_alias(q: str, limit: int = 10) -> dict:
    return {"results": brain.search(q, limit)}


@app.get("/api/graph")
def brain_graph_alias(limit: int = 500) -> dict:
    return brain.graph(limit)


@app.get("/api/notes/{note_path:path}")
def brain_read_alias(note_path: str) -> dict:
    try:
        return brain.read(note_path)
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

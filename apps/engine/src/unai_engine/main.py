from __future__ import annotations

import asyncio

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from unai_engine.agent.orchestrator import AgentOrchestrator
from unai_engine.config import settings
from unai_engine.models import (
    BrainNoteCreate, ChatRequest, ChatResponse, HealthResponse,
    TaskCompileRequest, TaskContract, TaskInspectRequest,
    TaskInspectionResponse, TaskEvidenceSnapshot, TaskRunApprovalRequest, TaskTestRunResponse,
  )
from unai_engine.providers.openai_compat import OpenAICompatibleProvider, ProviderError
from unai_engine.plugins.builtin import build_registry
from unai_engine.brain.vault import BrainError, MarkdownVault
from unai_engine.cognition.task_compiler import compile_task
from unai_engine.agent.inspection import TaskInspector
from unai_engine.verification.ledger import EvidenceLedger
from unai_engine.runtime.sandbox import DockerSandbox
from unai_engine.runtime.test_runner import IsolatedPythonTestRunner, TaskRunRejected
from unai_engine.runtime.workspace import WorkspaceError, resolve_workspace_path

provider = OpenAICompatibleProvider(
    base_url=settings.model_base_url,
    model=settings.model_name,
    api_key=settings.model_api_key,
    timeout=settings.model_timeout_seconds,
)
plugins = build_registry()
brain = MarkdownVault(settings.brain_vault_root, settings.brain_index_path)
agent = AgentOrchestrator(provider, context_search=brain.search)
evidence_ledger = EvidenceLedger(settings.evidence_db_path)
task_inspector = TaskInspector(plugins.indexing["python-code-graph"], evidence_ledger)
task_test_runner = IsolatedPythonTestRunner(
    evidence_ledger,
    DockerSandbox(settings.docker_image, settings.test_timeout_seconds),
    max_files=settings.test_max_files,
    max_bytes=settings.test_max_bytes,
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
    allow_headers=["Content-Type"],
)


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


@app.get("/api/tasks/{task_id}/evidence", response_model=TaskEvidenceSnapshot)
def task_evidence(task_id: str) -> TaskEvidenceSnapshot:
    snapshot = evidence_ledger.get_snapshot(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task evidence not found.")
    return snapshot


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
    authorization: str | None = Header(default=None),
) -> dict:
    # Tokenless writes are restricted to loopback/test clients. Remote deployments
    # must configure a server-side token; never expose it in the browser bundle.
    client_host = request.client.host if request.client else ""
    is_loopback = client_host in {"127.0.0.1", "::1", "testclient"}
    if settings.brain_write_token:
        authorized = authorization == f"Bearer {settings.brain_write_token}"
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
    authorization: str | None = Header(default=None),
) -> dict:
    return brain_create(note, request, authorization)

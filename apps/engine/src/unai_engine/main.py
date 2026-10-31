from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from unai_engine.agent.orchestrator import AgentOrchestrator
from unai_engine.config import settings
from unai_engine.models import BrainNoteCreate, ChatRequest, ChatResponse, HealthResponse, TaskCompileRequest, TaskContract
from unai_engine.providers.openai_compat import OpenAICompatibleProvider, ProviderError
from unai_engine.plugins.builtin import build_registry
from unai_engine.brain.vault import BrainError, MarkdownVault
from unai_engine.cognition.task_compiler import compile_task

provider = OpenAICompatibleProvider(
    base_url=settings.model_base_url,
    model=settings.model_name,
    api_key=settings.model_api_key,
    timeout=settings.model_timeout_seconds,
)
plugins = build_registry()
brain = MarkdownVault(settings.brain_vault_root, settings.brain_index_path)
agent = AgentOrchestrator(provider, context_search=brain.search)

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

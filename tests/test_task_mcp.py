from __future__ import annotations

from collections.abc import Callable

import pytest

from unai_engine.models import TaskInspectRequest
from unai_engine.task_mcp_server import TaskMCPBridge


class FakeResult:
    def model_dump(self, mode: str = "json") -> dict:
        return {"ok": True, "mode": mode}


class FakeClient:
    def __init__(self, calls: dict) -> None:
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def compile_task(self, request):
        self.calls["compile"] = request
        return FakeResult()

    async def inspect_task(self, request):
        self.calls["inspect"] = request
        return FakeResult()

    async def read_file(self, task_id, file_path):
        self.calls["read_file"] = (task_id, file_path)
        return FakeResult()

    async def generate_probes(self, task_id, request):
        self.calls["probes"] = (task_id, request)
        return FakeResult()

    async def get_evidence(self, task_id):
        self.calls["evidence"] = task_id
        return FakeResult()

    async def get_proof(self, task_id):
        self.calls["proof"] = task_id
        return FakeResult()


def make_factory(calls: dict, constructor_args: list) -> Callable[..., FakeClient]:
    def factory(base_url: str, api_token: str = "") -> FakeClient:
        constructor_args.append((base_url, api_token))
        return FakeClient(calls)

    return factory


@pytest.mark.asyncio
async def test_compile_never_grants_workspace_permissions() -> None:
    calls: dict = {}
    constructor_args: list = []
    bridge = TaskMCPBridge(
        "http://127.0.0.1:8000",
        client_factory=make_factory(calls, constructor_args),
    )

    result = await bridge.compile_task("Review the authentication flow", ["No edits"])

    assert result["ok"] is True
    assert calls["compile"].permissions == []
    assert calls["compile"].constraints == ["No edits"]
    assert constructor_args == [("http://127.0.0.1:8000", "")]


@pytest.mark.asyncio
async def test_inspection_is_disabled_by_default_and_does_not_create_client() -> None:
    calls: dict = {}
    constructor_args: list = []
    bridge = TaskMCPBridge(
        "http://127.0.0.1:8000",
        workspace="demo",
        client_factory=make_factory(calls, constructor_args),
    )

    with pytest.raises(PermissionError, match="UNAI_MCP_ALLOW_WORKSPACE_READ"):
        await bridge.inspect_workspace("Review the source")

    assert constructor_args == []
    assert "inspect" not in calls


@pytest.mark.asyncio
async def test_inspection_uses_only_process_configured_workspace_and_permission() -> None:
    calls: dict = {}
    constructor_args: list = []
    bridge = TaskMCPBridge(
        "http://127.0.0.1:8000",
        engine_api_token="x" * 40,
        workspace="approved-project",
        allow_workspace_read=True,
        client_factory=make_factory(calls, constructor_args),
    )

    result = await bridge.inspect_workspace(
        "Review the source",
        constraints=["Do not modify files"],
        symbol_limit=9,
    )

    request = calls["inspect"]
    assert result["ok"] is True
    assert isinstance(request, TaskInspectRequest)
    assert request.workspace == "approved-project"
    assert request.permissions == ["workspace_read"]
    assert request.constraints == ["Do not modify files"]
    assert request.symbol_limit == 9
    assert constructor_args == [("http://127.0.0.1:8000", "x" * 40)]


@pytest.mark.asyncio
async def test_inspection_requires_a_workspace_even_when_read_is_enabled() -> None:
    bridge = TaskMCPBridge(
        "http://127.0.0.1:8000",
        allow_workspace_read=True,
        client_factory=lambda *_args, **_kwargs: pytest.fail("client must not be created"),
    )

    with pytest.raises(RuntimeError, match="UNAI_MCP_WORKSPACE"):
        await bridge.inspect_workspace("Review the source")


@pytest.mark.asyncio
async def test_read_and_evidence_tools_delegate_to_typed_client() -> None:
    calls: dict = {}
    bridge = TaskMCPBridge(
        "http://127.0.0.1:8000",
        workspace="demo",
        allow_workspace_read=True,
        client_factory=make_factory(calls, []),
    )

    assert (await bridge.read_workspace_file("task-1", "src/app.py"))["ok"] is True
    assert (await bridge.get_task_evidence("task-1"))["ok"] is True
    assert (await bridge.get_task_proof("task-1"))["ok"] is True
    assert calls["read_file"] == ("task-1", "src/app.py")
    assert calls["evidence"] == "task-1"
    assert calls["proof"] == "task-1"


@pytest.mark.asyncio
async def test_boundary_probe_tool_is_explicitly_a_generation_call() -> None:
    calls: dict = {}
    bridge = TaskMCPBridge(
        "http://127.0.0.1:8000",
        workspace="demo",
        allow_workspace_read=True,
        client_factory=make_factory(calls, []),
    )

    result = await bridge.generate_boundary_probes(
        "task-1", "src/math.py", "clamp", limit=7
    )

    task_id, request = calls["probes"]
    assert result["ok"] is True
    assert task_id == "task-1"
    assert request.file_path == "src/math.py"
    assert request.symbol_name == "clamp"
    assert request.limit == 7


def test_mcp_bridge_has_no_patch_or_test_execution_methods() -> None:
    bridge = TaskMCPBridge("http://127.0.0.1:8000")
    assert not hasattr(bridge, "propose_patch")
    assert not hasattr(bridge, "apply_patch")
    assert not hasattr(bridge, "run_tests")

from dataclasses import replace

import httpx
import pytest

from unai_engine.agent.inspection import TaskInspector
from unai_engine.client import EngineClient, EngineClientError
from unai_engine.models import (
    PatchProposalRequest,
    ProbeRequest,
    TaskCompileRequest,
    TaskInspectRequest,
    TaskContract,
)
from unai_engine.runtime.patches import PatchManager
from unai_engine.verification.ledger import EvidenceLedger
from unai_engine.verification.probes import BoundaryProbeGenerator


def test_client_rejects_remote_url_without_token() -> None:
    with pytest.raises(ValueError, match="api_token is required"):
        EngineClient("https://engine.example")


def test_client_rejects_short_api_token() -> None:
    with pytest.raises(ValueError, match="at least 32"):
        EngineClient("https://engine.example", api_token="too-short")


@pytest.mark.asyncio
async def test_client_sends_bearer_token_and_returns_typed_contract(monkeypatch) -> None:
    import unai_engine.main as engine

    token = "client-test-" + ("k" * 48)
    monkeypatch.setattr(
        engine,
        "settings",
        replace(engine.settings, api_token=token),
    )
    transport = httpx.ASGITransport(app=engine.app, client=("203.0.113.20", 5432))
    async with EngineClient(
        "https://engine.example",
        api_token=token,
        transport=transport,
    ) as client:
        contract = await client.compile_task(
            TaskCompileRequest(request="Review the login flow")
        )
    assert isinstance(contract, TaskContract)
    assert contract.objective
    assert contract.task_type in {"bugfix", "feature", "review", "research", "general"}


@pytest.mark.asyncio
async def test_client_surfaces_authentication_failure_without_echoing_token(monkeypatch) -> None:
    import unai_engine.main as engine

    token = "client-test-" + ("m" * 48)
    monkeypatch.setattr(
        engine,
        "settings",
        replace(engine.settings, api_token="server-token-" + ("s" * 48)),
    )
    transport = httpx.ASGITransport(app=engine.app, client=("203.0.113.20", 5432))
    async with EngineClient(
        "https://engine.example",
        api_token=token,
        transport=transport,
    ) as client:
        with pytest.raises(EngineClientError) as raised:
            await client.compile_task(TaskCompileRequest(request="Review the login flow"))
    assert raised.value.status_code == 401
    assert token not in str(raised.value)


@pytest.mark.asyncio
async def test_client_runs_approval_gated_inspect_patch_and_proof_workflow(
    tmp_path,
    monkeypatch,
) -> None:
    import unai_engine.main as engine

    workspace_root = tmp_path / "workspaces"
    project = workspace_root / "demo"
    project.mkdir(parents=True)
    source_file = project / "source file.py"
    original = (
        "def calculate(value: int):\n"
        "    if value < 5:\n"
        "        return 0\n"
        "    return value\n"
    )
    source_file.write_text(original, encoding="utf-8")

    api_token = "client-integration-" + ("z" * 48)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    monkeypatch.setattr(
        engine,
        "settings",
        replace(
            engine.settings,
            workspace_root=workspace_root,
            evidence_db_path=tmp_path / "evidence.sqlite3",
            api_token=api_token,
        ),
    )
    monkeypatch.setattr(engine, "evidence_ledger", ledger)
    monkeypatch.setattr(
        engine,
        "task_inspector",
        TaskInspector(engine.plugins.indexing["python-code-graph"], ledger),
    )
    monkeypatch.setattr(engine, "patch_manager", PatchManager(ledger))
    monkeypatch.setattr(engine, "probe_generator", BoundaryProbeGenerator(ledger))

    transport = httpx.ASGITransport(
        app=engine.app,
        client=("203.0.113.25", 4567),
    )
    async with EngineClient(
        "https://engine.example",
        api_token=api_token,
        transport=transport,
    ) as client:
        inspected = await client.inspect_task(
            TaskInspectRequest(
                request="Review calculate boundary behavior and make a safe correction",
                workspace="demo",
                permissions=["workspace_read", "workspace_write"],
            )
        )
        assert inspected.status == "inspection_complete"

        snapshot = await client.read_file(inspected.task_id, "source file.py")
        assert snapshot.content == original
        assert len(snapshot.sha256) == 64

        probes = await client.generate_probes(
            inspected.task_id,
            ProbeRequest(file_path="source file.py", symbol_name="calculate", limit=6),
        )
        assert probes.status == "generated"
        assert probes.evidence.passed is None

        proposed = (
            "def calculate(value: int):\n"
            "    if value < 5:\n"
            "        return 5\n"
            "    return value\n"
        )
        proposal = await client.propose_patch(
            inspected.task_id,
            PatchProposalRequest(
                file_path="source file.py",
                expected_sha256=snapshot.sha256,
                proposed_content=proposed,
                description="Correct the lower boundary result",
            ),
        )
        assert proposal.status == "pending"
        assert source_file.read_text(encoding="utf-8") == original

        applied = await client.apply_patch(
            inspected.task_id,
            proposal.proposal_id,
            user_confirmed=True,
        )
        assert applied.status == "applied"
        assert source_file.read_text(encoding="utf-8") == proposed

        quality = await client.refresh_quality(inspected.task_id)
        assert quality.passed is True
        proof = await client.get_proof(inspected.task_id)
        evidence = await client.get_evidence(inspected.task_id)

    assert proof.status == "awaiting_tests"
    assert proof.quality_check_fresh is True
    assert evidence.task_id == inspected.task_id
    assert any(item.kind == "patch" for item in evidence.evidence)
    assert any(item.kind == "probe" for item in evidence.evidence)


@pytest.mark.asyncio
async def test_client_refuses_mutation_without_user_confirmation() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    async with EngineClient(
        "http://127.0.0.1:8000",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ValueError, match="user confirmation"):
            await client.apply_patch("task", "proposal", user_confirmed=False)
        with pytest.raises(ValueError, match="user confirmation"):
            await client.run_tests("task", user_confirmed=False)

    assert requests == []

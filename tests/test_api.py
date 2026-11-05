from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
import hashlib

from fastapi.testclient import TestClient

import unai_engine.main as main_module
from unai_engine.agent.inspection import TaskInspector
from unai_engine.agent.orchestrator import AgentOrchestrator
from unai_engine.main import app
from unai_engine.runtime.sandbox import DockerSandbox
from unai_engine.runtime.test_runner import IsolatedPythonTestRunner
from unai_engine.models import EvidenceRecord
from unai_engine.runtime.patches import PatchManager
from unai_engine.verification.probes import BoundaryProbeGenerator
from unai_engine.verification.ledger import EvidenceLedger


def _configure_inspection(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspaces"
    project = workspace_root / "demo"
    project.mkdir(parents=True)
    (project / "service.py").write_text(
        "def calculate_total(items):\n    return sum(items)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(workspace_root=workspace_root),
    )
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    monkeypatch.setattr(main_module, "evidence_ledger", ledger)
    monkeypatch.setattr(
        main_module,
        "task_inspector",
        TaskInspector(main_module.plugins.indexing["python-code-graph"], ledger),
    )
    monkeypatch.setattr(
        main_module,
        "task_test_runner",
        IsolatedPythonTestRunner(ledger, DockerSandbox()),
    )
    monkeypatch.setattr(main_module, "patch_manager", PatchManager(ledger))
    monkeypatch.setattr(main_module, "probe_generator", BoundaryProbeGenerator(ledger))
    return workspace_root


def test_health_endpoint_does_not_expose_provider_secret() -> None:
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "api_key" not in body
    assert "Authorization" not in body


def test_plugin_manifest_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/api/plugins")
    assert response.status_code == 200
    manifest = response.json()
    assert manifest["cognition"] == ["rule-microcore"]
    assert manifest["indexing"] == ["filesystem-index", "python-code-graph"]
    assert manifest["verification"] == ["static-evidence"]


def test_task_compile_api_returns_provisional_contract() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/tasks/compile",
        json={
            "request": "Fix the login crash and add a regression test",
            "constraints": ["Keep the existing API response shape"],
        },
    )
    assert response.status_code == 200
    contract = response.json()
    assert contract["task_type"] == "bugfix"
    assert contract["constraints"] == ["Keep the existing API response shape"]
    assert "workspace_write" in contract["missing_permissions"]
    assert "run_tests" in contract["missing_permissions"]
    assert len(contract["steps"]) >= 5


def test_task_compile_api_rejects_empty_or_whitespace_request() -> None:
    client = TestClient(app)
    assert client.post("/api/tasks/compile", json={"request": ""}).status_code == 422
    assert client.post("/api/tasks/compile", json={"request": "   "}).status_code == 422


def test_repository_graph_api_indexes_only_selected_workspace(tmp_path, monkeypatch) -> None:
    workspace_root = tmp_path / "workspaces"
    project = workspace_root / "demo"
    project.mkdir(parents=True)
    (project / "main.py").write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
    monkeypatch.setattr(main_module, "settings", SimpleNamespace(workspace_root=workspace_root))

    response = TestClient(app).get("/api/repository/graph?workspace=demo")
    assert response.status_code == 200
    body = response.json()
    assert body["root"] == "demo"
    assert any(node.get("qualified_name") == "greet" for node in body["nodes"])


def test_repository_search_returns_source_ranges(tmp_path, monkeypatch) -> None:
    workspace_root = tmp_path / "workspaces"
    project = workspace_root / "demo"
    project.mkdir(parents=True)
    (project / "service.py").write_text("def calculate_total(items):\n    return sum(items)\n", encoding="utf-8")
    monkeypatch.setattr(main_module, "settings", SimpleNamespace(workspace_root=workspace_root))

    response = TestClient(app).get("/api/repository/search?workspace=demo&q=calculate%20total")
    assert response.status_code == 200
    assert response.json()["results"][0]["start_line"] == 1


def test_repository_graph_rejects_workspace_escape(tmp_path, monkeypatch) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    monkeypatch.setattr(main_module, "settings", SimpleNamespace(workspace_root=workspace_root))

    response = TestClient(app).get("/api/repository/graph?workspace=../outside")
    assert response.status_code == 400


def test_repository_graph_returns_404_for_missing_workspace(tmp_path, monkeypatch) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    monkeypatch.setattr(main_module, "settings", SimpleNamespace(workspace_root=workspace_root))

    response = TestClient(app).get("/api/repository/graph?workspace=missing")
    assert response.status_code == 404


def test_task_inspection_requires_explicit_workspace_read_and_persists_blocker(tmp_path, monkeypatch) -> None:
    _configure_inspection(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.post(
        "/api/tasks/inspect",
        json={"request": "Fix calculate_total bug", "workspace": "demo"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "awaiting_workspace_read"
    assert body["next_gate"] == "workspace_access_required"
    assert body["execution_started"] is False
    assert body["graph_summary"] == {}
    assert body["evidence"][0]["passed"] is False

    snapshot = client.get(f"/api/tasks/{body['task_id']}/evidence")
    assert snapshot.status_code == 200
    assert snapshot.json()["status"] == "awaiting_workspace_read"
    assert len(snapshot.json()["evidence"]) == 1


def test_task_inspection_builds_read_only_graph_and_records_evidence(tmp_path, monkeypatch) -> None:
    _configure_inspection(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.post(
        "/api/tasks/inspect",
        json={
            "request": "Fix calculate_total bug and add a regression test",
            "workspace": "demo",
            "permissions": ["workspace_read"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "inspection_complete"
    assert body["next_gate"] == "approval_required"
    assert body["execution_started"] is False
    assert body["graph_summary"]["files_parsed"] == 1
    assert body["symbol_matches"][0]["name"] == "calculate_total"
    assert any(item["kind"] == "repository" for item in body["evidence"])
    assert any(item["kind"] == "quality" and item["passed"] is True for item in body["evidence"])
    assert "workspace_write" in body["contract"]["missing_permissions"]

    snapshot = client.get(f"/api/tasks/{body['task_id']}/evidence")
    assert snapshot.status_code == 200
    assert snapshot.json()["contract"]["task_id"] == body["task_id"]
    assert len(snapshot.json()["evidence"]) == len(body["evidence"])


def test_task_inspection_never_reads_outside_workspace(tmp_path, monkeypatch) -> None:
    _configure_inspection(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.post(
        "/api/tasks/inspect",
        json={
            "request": "Review repository",
            "workspace": "../outside",
            "permissions": ["workspace_read"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "workspace_unavailable"
    assert body["graph_summary"] == {}
    assert body["evidence"][0]["passed"] is False


def test_task_evidence_endpoint_returns_404_for_unknown_task() -> None:
    response = TestClient(app).get("/api/tasks/unknown-task/evidence")
    assert response.status_code == 404


def test_test_run_endpoint_requires_explicit_run_tests_permission(tmp_path, monkeypatch) -> None:
    _configure_inspection(tmp_path, monkeypatch)
    client = TestClient(app)
    inspected = client.post(
        "/api/tasks/inspect",
        json={
            "request": "Fix calculate_total and run regression tests",
            "workspace": "demo",
            "permissions": ["workspace_read"],
        },
    )
    assert inspected.status_code == 200
    task_id = inspected.json()["task_id"]

    response = client.post(
        f"/api/tasks/{task_id}/run-tests",
        json={"confirm": "run-python-tests"},
    )
    assert response.status_code == 403
    assert "run_tests" in response.json()["detail"]


def test_test_run_endpoint_requires_separate_confirmation() -> None:
    response = TestClient(app).post(
        "/api/tasks/nonexistent/run-tests",
        json={"confirm": "yes"},
    )
    assert response.status_code == 422


def test_patch_proposal_requires_second_confirmation_and_records_evidence(tmp_path, monkeypatch) -> None:
    workspace_root = _configure_inspection(tmp_path, monkeypatch)
    target = workspace_root / "demo" / "service.py"
    original = target.read_text(encoding="utf-8")
    client = TestClient(app)

    inspected = client.post(
        "/api/tasks/inspect",
        json={
            "request": "Implement a profile display feature",
            "workspace": "demo",
            "permissions": ["workspace_read", "workspace_write"],
        },
    )
    assert inspected.status_code == 200
    task_id = inspected.json()["task_id"]
    file_snapshot = client.get(f"/api/tasks/{task_id}/files/service.py")
    assert file_snapshot.status_code == 200
    assert file_snapshot.json()["content"] == original
    digest = file_snapshot.json()["sha256"]

    proposed = "def calculate_total(items):\n    return sum(items) + 1\n"
    proposal_response = client.post(
        f"/api/tasks/{task_id}/patch-proposals",
        json={
            "file_path": "service.py",
            "expected_sha256": digest,
            "proposed_content": proposed,
            "description": "Adjust the total calculation",
        },
    )
    assert proposal_response.status_code == 200
    proposal = proposal_response.json()
    assert proposal["status"] == "pending"
    assert (workspace_root / "demo" / "service.py").read_text(encoding="utf-8") == original

    applied = client.post(
        f"/api/tasks/{task_id}/patch-proposals/{proposal['proposal_id']}/apply",
        json={"confirm": "apply-patch"},
    )
    assert applied.status_code == 200
    assert applied.json()["status"] == "applied"
    assert target.read_text(encoding="utf-8") == proposed

    snapshot = client.get(f"/api/tasks/{task_id}/evidence")
    assert snapshot.status_code == 200
    assert [item["source"] for item in snapshot.json()["evidence"]][-2:] == [
        "patch-proposal", "patch-applier"
    ]


def test_patch_api_rejects_missing_write_approval(tmp_path, monkeypatch) -> None:
    workspace_root = _configure_inspection(tmp_path, monkeypatch)
    digest = hashlib.sha256((workspace_root / "demo" / "service.py").read_bytes()).hexdigest()
    client = TestClient(app)

    inspected = client.post(
        "/api/tasks/inspect",
        json={
            "request": "Implement a profile display feature",
            "workspace": "demo",
            "permissions": ["workspace_read"],
        },
    )
    task_id = inspected.json()["task_id"]
    response = client.post(
        f"/api/tasks/{task_id}/patch-proposals",
        json={
            "file_path": "service.py",
            "expected_sha256": digest,
            "proposed_content": "def changed():\n    return True\n",
            "description": "Test missing approval",
        },
    )
    assert response.status_code == 403


def test_patch_api_rejects_stale_file_hash(tmp_path, monkeypatch) -> None:
    workspace_root = _configure_inspection(tmp_path, monkeypatch)
    target = workspace_root / "demo" / "service.py"
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    client = TestClient(app)

    inspected = client.post(
        "/api/tasks/inspect",
        json={
            "request": "Implement a profile display feature",
            "workspace": "demo",
            "permissions": ["workspace_read", "workspace_write"],
        },
    )
    task_id = inspected.json()["task_id"]
    target.write_text("def concurrent_change():\n    return 'external'\n", encoding="utf-8")

    response = client.post(
        f"/api/tasks/{task_id}/patch-proposals",
        json={
            "file_path": "service.py",
            "expected_sha256": digest,
            "proposed_content": "def unsafe_overwrite():\n    return False\n",
            "description": "Should conflict",
        },
    )
    assert response.status_code == 409
    assert "concurrent_change" in target.read_text(encoding="utf-8")


def test_patch_apply_requires_exact_confirmation(tmp_path, monkeypatch) -> None:
    _configure_inspection(tmp_path, monkeypatch)
    response = TestClient(app).post(
        "/api/tasks/task-id/patch-proposals/proposal-id/apply",
        json={"confirm": "yes"},
    )
    assert response.status_code == 422


def test_task_file_snapshot_requires_workspace_read_permission(tmp_path, monkeypatch) -> None:
    workspace_root = _configure_inspection(tmp_path, monkeypatch)
    client = TestClient(app)
    inspected = client.post(
        "/api/tasks/inspect",
        json={
            "request": "Implement a profile display feature",
            "workspace": "demo",
            "permissions": [],
        },
    )
    assert inspected.status_code == 200
    task_id = inspected.json()["task_id"]

    response = client.get(f"/api/tasks/{task_id}/files/service.py")
    assert response.status_code == 403


def test_task_file_snapshot_rejects_sensitive_and_traversal_paths(tmp_path, monkeypatch) -> None:
    _configure_inspection(tmp_path, monkeypatch)
    client = TestClient(app)
    inspected = client.post(
        "/api/tasks/inspect",
        json={
            "request": "Implement a profile display feature",
            "workspace": "demo",
            "permissions": ["workspace_read"],
        },
    )
    task_id = inspected.json()["task_id"]

    assert client.get(f"/api/tasks/{task_id}/files/../outside.py").status_code in {400, 404, 409}
    assert client.get(f"/api/tasks/{task_id}/files/.env").status_code == 409


def test_task_proof_endpoint_summarizes_inspection_and_missing_task(tmp_path, monkeypatch) -> None:
    _configure_inspection(tmp_path, monkeypatch)
    client = TestClient(app)
    assert client.get("/api/tasks/not-found/proof").status_code == 404

    inspected = client.post(
        "/api/tasks/inspect",
        json={
            "request": "Review the service implementation",
            "workspace": "demo",
            "permissions": ["workspace_read"],
        },
    )
    assert inspected.status_code == 200
    proof = client.get(f"/api/tasks/{inspected.json()['task_id']}/proof")
    assert proof.status_code == 200
    assert proof.json()["status"] == "inspection_only"


def test_probe_api_requires_workspace_read_and_records_unverified_candidates(tmp_path, monkeypatch) -> None:
    workspace_root = _configure_inspection(tmp_path, monkeypatch)
    (workspace_root / "demo" / "branching.py").write_text(
        "def clamp(value: int):\n"
        "    if value < 10:\n"
        "        return 0\n"
        "    return value\n",
        encoding="utf-8",
    )
    client = TestClient(app)
    inspected = client.post(
        "/api/tasks/inspect",
        json={
            "request": "Review the service and test edge cases",
            "workspace": "demo",
            "permissions": ["workspace_read"],
        },
    )
    assert inspected.status_code == 200
    task_id = inspected.json()["task_id"]

    response = client.post(
        f"/api/tasks/{task_id}/probes",
        json={"file_path": "branching.py", "symbol_name": "clamp", "limit": 10},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "generated"
    assert [item["parameter_values"]["value"] for item in body["candidates"]] == [9, 10, 11]
    assert body["evidence"]["passed"] is None
    assert body["evidence"]["metadata"]["executed"] is False

    snapshot = client.get(f"/api/tasks/{task_id}/evidence")
    assert snapshot.status_code == 200
    assert snapshot.json()["evidence"][-1]["kind"] == "probe"


def test_probe_api_rejects_unknown_function(tmp_path, monkeypatch) -> None:
    _configure_inspection(tmp_path, monkeypatch)
    client = TestClient(app)
    inspected = client.post(
        "/api/tasks/inspect",
        json={
            "request": "Review the service",
            "workspace": "demo",
            "permissions": ["workspace_read"],
        },
    )
    task_id = inspected.json()["task_id"]
    response = client.post(
        f"/api/tasks/{task_id}/probes",
        json={"file_path": "service.py", "symbol_name": "missing_function"},
    )
    assert response.status_code == 404


def test_post_patch_quality_refresh_requires_read_permission_and_records_evidence(tmp_path, monkeypatch) -> None:
    _configure_inspection(tmp_path, monkeypatch)
    client = TestClient(app)
    inspected = client.post(
        "/api/tasks/inspect",
        json={
            "request": "Review the service implementation",
            "workspace": "demo",
            "permissions": ["workspace_read"],
        },
    )
    task_id = inspected.json()["task_id"]

    refreshed = client.post(f"/api/tasks/{task_id}/quality-check")
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body["passed"] is True
    assert body["files_parsed"] >= 1
    assert body["evidence"]["source"] == "python-ast-parser"

    proof = client.get(f"/api/tasks/{task_id}/proof")
    assert proof.status_code == 200
    assert proof.json()["quality_check_fresh"] is True
    assert proof.json()["quality_check_passed"] is True


def test_quality_refresh_rejects_task_without_read_permission(tmp_path, monkeypatch) -> None:
    _configure_inspection(tmp_path, monkeypatch)
    client = TestClient(app)
    inspected = client.post(
        "/api/tasks/inspect",
        json={
            "request": "Review the service implementation",
            "workspace": "demo",
            "permissions": [],
        },
    )
    task_id = inspected.json()["task_id"]
    refreshed = client.post(f"/api/tasks/{task_id}/quality-check")
    assert refreshed.status_code == 403



def test_engine_api_is_loopback_only_without_an_api_token(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "settings",
        replace(main_module.settings, api_token=""),
    )
    remote = TestClient(app, client=("203.0.113.9", 4567))
    loopback = TestClient(app, client=("127.0.0.1", 4567))
    assert remote.get("/api/plugins").status_code == 403
    assert loopback.get("/api/plugins").status_code == 200


def test_engine_api_requires_valid_bearer_token_for_remote_clients(monkeypatch) -> None:
    token = "unai-test-" + ("x" * 48)
    monkeypatch.setattr(
        main_module,
        "settings",
        replace(main_module.settings, api_token=token),
    )
    remote = TestClient(app, client=("203.0.113.9", 4567))
    assert remote.get("/api/plugins").status_code == 401
    assert remote.get(
        "/api/plugins",
        headers={"Authorization": "Bearer wrong-token"},
    ).status_code == 401
    authorized = remote.get(
        "/api/plugins",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert authorized.status_code == 200
    assert authorized.json()["verification"] == ["static-evidence"]


def test_engine_api_preflight_allows_authorization_header(monkeypatch) -> None:
    token = "unai-test-" + ("y" * 48)
    monkeypatch.setattr(
        main_module,
        "settings",
        replace(main_module.settings, api_token=token),
    )
    remote = TestClient(app, client=("203.0.113.9", 4567))
    response = remote.options(
        "/api/plugins",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert "authorization" in response.headers.get("access-control-allow-headers", "").lower()

def test_specialist_chat_endpoint_uses_configured_model_and_returns_role(monkeypatch) -> None:
    class FakeProvider:
        async def complete(self, messages, *, model_override=None):
            assert model_override == "local-next-action-lora"
            assert "next-action specialist" in messages[0]["content"]
            return "Inspect the source file first."

    monkeypatch.setattr(
        main_module,
        "agent",
        AgentOrchestrator(FakeProvider(), specialist_models={"next_action": "local-next-action-lora"}),
    )
    response = TestClient(app).post(
        "/api/specialists/next_action/chat",
        json={"message": "What should happen next?"},
    )

    assert response.status_code == 200
    assert response.json()["specialist"] == "next_action"
    assert response.json()["model"] == "local-next-action-lora"
    assert response.json()["message"] == "Inspect the source file first."


def test_specialist_chat_endpoint_returns_unavailable_without_base_model_fallback(monkeypatch) -> None:
    class FakeProvider:
        async def complete(self, messages, *, model_override=None):
            raise AssertionError("Unconfigured specialists must not invoke the provider.")

    monkeypatch.setattr(main_module, "agent", AgentOrchestrator(FakeProvider()))
    response = TestClient(app).post(
        "/api/specialists/verifier/chat",
        json={"message": "Verify this patch."},
    )

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_specialist_chat_endpoint_rejects_unknown_role() -> None:
    response = TestClient(app).post(
        "/api/specialists/general/chat",
        json={"message": "Do work."},
    )
    assert response.status_code == 422



def test_task_context_returns_hash_pinned_bounded_symbol_slices(tmp_path, monkeypatch) -> None:
    _configure_inspection(tmp_path, monkeypatch)
    client = TestClient(app)
    inspected = client.post(
        "/api/tasks/inspect",
        json={
            "request": "Fix calculate_total bug and add a regression test",
            "workspace": "demo",
            "permissions": ["workspace_read"],
        },
    )
    assert inspected.status_code == 200
    task_id = inspected.json()["task_id"]

    response = client.get(f"/api/tasks/{task_id}/context?limit=3&context_lines=1")
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == task_id
    assert body["slices"]
    first = body["slices"][0]
    assert first["path"] == "service.py"
    assert first["name"] == "calculate_total"
    assert "def calculate_total(items):" in first["content"]
    assert "return sum(items)" in first["content"]
    assert len(first["sha256"]) == 64
    assert body["total_characters"] <= 24_000


def test_task_context_requires_approved_workspace_read(tmp_path, monkeypatch) -> None:
    _configure_inspection(tmp_path, monkeypatch)
    client = TestClient(app)
    inspected = client.post(
        "/api/tasks/inspect",
        json={
            "request": "Fix calculate_total bug",
            "workspace": "demo",
            "permissions": [],
        },
    )
    task_id = inspected.json()["task_id"]
    assert client.get(f"/api/tasks/{task_id}/context").status_code == 409


def test_task_context_rejects_source_changed_after_inspection(tmp_path, monkeypatch) -> None:
    workspace_root = _configure_inspection(tmp_path, monkeypatch)
    client = TestClient(app)
    inspected = client.post(
        "/api/tasks/inspect",
        json={
            "request": "Fix calculate_total bug",
            "workspace": "demo",
            "permissions": ["workspace_read"],
        },
    )
    task_id = inspected.json()["task_id"]
    (workspace_root / "demo" / "service.py").write_text(
        "def calculate_total(items):\n    return len(items)\n",
        encoding="utf-8",
    )

    response = client.get(f"/api/tasks/{task_id}/context")
    assert response.status_code == 409
    assert "re-inspect" in response.json()["detail"].lower()


def test_coverage_guided_probe_api_uses_existing_hash_bound_evidence_without_execution(
    tmp_path, monkeypatch
) -> None:
    workspace_root = _configure_inspection(tmp_path, monkeypatch)
    target = workspace_root / "demo" / "branching.py"
    target.write_text(
        "def classify(value):\\n"
        "    if value < 0:\\n"
        "        return \\"negative\\"\\n"
        "    else:\\n"
        "        return \\"non-negative\\"\\n",
        encoding="utf-8",
    )
    client = TestClient(app)
    inspected = client.post(
        "/api/tasks/inspect",
        json={
            "request": "Review classify branch coverage",
            "workspace": "demo",
            "permissions": ["workspace_read"],
        },
    )
    assert inspected.status_code == 200
    task_id = inspected.json()["task_id"]

    generated = client.post(
        f"/api/tasks/{task_id}/probes",
        json={"file_path": "branching.py", "symbol_name": "classify", "limit": 10},
    )
    assert generated.status_code == 200
    probe = generated.json()
    source_sha256 = probe["source_sha256"]

    execution = EvidenceRecord(
        evidence_id="coverage-guided-execution",
        task_id=task_id,
        kind="execution",
        source="grounded-oracle-sandbox",
        claim="Supplied grounded cases were executed in Docker.",
        passed=True,
        metadata={"target_file": "branching.py", "target_function": "classify"},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    coverage = EvidenceRecord(
        evidence_id="coverage-guided-coverage",
        task_id=task_id,
        kind="coverage",
        source="grounded-coverage",
        claim="Coverage measured for supplied cases.",
        passed=None,
        metadata={
            "schema": "unai.coverage.v1",
            "execution_evidence_id": execution.evidence_id,
            "target_file": "branching.py",
            "symbol_name": "classify",
            "source_sha256": source_sha256,
            "trace_sha256": "a" * 64,
            "status": "complete",
            "branches": [{
                "branch_id": "branch-001",
                "kind": "if",
                "line": 2,
                "condition": "value < 0",
                "then_observed": True,
                "else_observed": False,
            }],
        },
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    main_module.evidence_ledger.append_evidence(execution)
    main_module.evidence_ledger.append_evidence(coverage)

    before = main_module.evidence_ledger.get_snapshot(task_id)
    before_execution_count = sum(item.kind == "execution" for item in before.evidence)
    response = client.post(
        f"/api/tasks/{task_id}/coverage-guided-probes",
        json={
            "file_path": "branching.py",
            "symbol_name": "classify",
            "source_sha256": source_sha256,
            "probe_evidence_id": probe["evidence"]["evidence_id"],
            "coverage_evidence_id": coverage.evidence_id,
            "limit": 10,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ranked"
    assert [item["candidate_id"] for item in body["candidates"]] == ["probe-2", "probe-3"]
    assert all(item["predicted_arm"] == "else" for item in body["candidates"])
    assert body["evidence"]["passed"] is None
    assert body["evidence"]["metadata"]["executed"] is False
    assert body["evidence"]["metadata"]["expected_outputs_inferred"] is False
    after = main_module.evidence_ledger.get_snapshot(task_id)
    assert sum(item.kind == "execution" for item in after.evidence) == before_execution_count


def test_coverage_guided_probe_api_rejects_stale_or_foreign_evidence(
    tmp_path, monkeypatch
) -> None:
    workspace_root = _configure_inspection(tmp_path, monkeypatch)
    target = workspace_root / "demo" / "branching.py"
    target.write_text(
        "def classify(value):\\n"
        "    if value < 0:\\n"
        "        return \\"negative\\"\\n"
        "    else:\\n"
        "        return \\"non-negative\\"\\n",
        encoding="utf-8",
    )
    client = TestClient(app)
    inspected = client.post(
        "/api/tasks/inspect",
        json={
            "request": "Review classify branch coverage",
            "workspace": "demo",
            "permissions": ["workspace_read"],
        },
    )
    task_id = inspected.json()["task_id"]
    generated = client.post(
        f"/api/tasks/{task_id}/probes",
        json={"file_path": "branching.py", "symbol_name": "classify"},
    )
    probe = generated.json()
    response = client.post(
        f"/api/tasks/{task_id}/coverage-guided-probes",
        json={
            "file_path": "branching.py",
            "symbol_name": "classify",
            "source_sha256": "b" * 64,
            "probe_evidence_id": probe["evidence"]["evidence_id"],
            "coverage_evidence_id": "missing-coverage",
        },
    )
    assert response.status_code == 409

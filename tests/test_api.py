from types import SimpleNamespace
import hashlib

from fastapi.testclient import TestClient

import unai_engine.main as main_module
from unai_engine.agent.inspection import TaskInspector
from unai_engine.main import app
from unai_engine.runtime.sandbox import DockerSandbox
from unai_engine.runtime.test_runner import IsolatedPythonTestRunner
from unai_engine.runtime.patches import PatchManager
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
    assert inspected.status_code == 200
    task_id = inspected.json()["task_id"]

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

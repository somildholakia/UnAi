from types import SimpleNamespace

from fastapi.testclient import TestClient

import unai_engine.main as main_module
from unai_engine.main import app


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

from fastapi.testclient import TestClient

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


def test_task_compile_api_rejects_empty_request() -> None:
    client = TestClient(app)
    response = client.post("/api/tasks/compile", json={"request": ""})
    assert response.status_code == 422

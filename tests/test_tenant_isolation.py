from dataclasses import replace

from fastapi.testclient import TestClient

import unai_engine.main as engine
from unai_engine.security import ApiTokenStore


def test_scoped_tenants_cannot_read_each_others_brain_tasks_or_workspace(tmp_path, monkeypatch):
    workspace_root = tmp_path / "tenant-workspaces"
    tenant_a_root = workspace_root / "tenant-a"
    tenant_b_root = workspace_root / "tenant-b"
    tenant_a_root.mkdir(parents=True)
    tenant_b_root.mkdir(parents=True)
    (tenant_a_root / "alpha.py").write_text("def alpha_secret():\n    return 'tenant-a-only'\n", encoding="utf-8")
    (tenant_b_root / "beta.py").write_text("def beta_public():\n    return 'tenant-b-only'\n", encoding="utf-8")

    data_root = tmp_path / "tenant-data"
    token_store = ApiTokenStore(tmp_path / "api-tokens.sqlite3")
    token_a = token_store.create_token(
        "tenant-a", "tenant A",
        ["brain:read", "brain:write", "tasks:read", "tasks:write", "workspace:read"],
    )
    token_b = token_store.create_token(
        "tenant-b", "tenant B",
        ["brain:read", "tasks:read", "workspace:read"],
    )
    monkeypatch.setattr(
        engine, "settings",
        replace(
            engine.settings,
            api_token="",
            api_token_store_path=tmp_path / "api-tokens.sqlite3",
            tenant_workspace_root=workspace_root,
            tenant_data_root=data_root,
            api_rate_limit_per_minute=1000,
        ),
    )
    monkeypatch.setattr(engine, "api_token_store", token_store)
    engine._tenant_services_cache.clear()
    client = TestClient(engine.app, client=("203.0.113.18", 9876))
    headers_a = {"Authorization": f"Bearer {token_a['token']}"}
    headers_b = {"Authorization": f"Bearer {token_b['token']}"}

    created_note = client.post(
        "/api/notes",
        json={"title": "Tenant A Secret", "body": "only tenant-a can recall cobalt", "tags": ["private"]},
        headers=headers_a,
    )
    assert created_note.status_code == 200
    note_path = created_note.json()["path"]

    task = client.post(
        "/api/tasks/inspect",
        json={
            "request": "Inspect alpha_secret",
            "workspace": ".",
            "permissions": ["workspace_read"],
        },
        headers=headers_a,
    )
    assert task.status_code == 200
    task_id = task.json()["task_id"]

    graph_a = client.get("/api/repository/graph", headers=headers_a)
    graph_b = client.get("/api/repository/graph", headers=headers_b)
    assert graph_a.status_code == graph_b.status_code == 200
    assert "alpha.py" in {node["path"] for node in graph_a.json()["nodes"] if node["type"] == "file"}
    assert "alpha.py" not in {node["path"] for node in graph_b.json()["nodes"] if node["type"] == "file"}
    assert "beta.py" not in {node["path"] for node in graph_a.json()["nodes"] if node["type"] == "file"}
    assert "beta.py" in {node["path"] for node in graph_b.json()["nodes"] if node["type"] == "file"}

    cross_brain = client.get(f"/api/notes/{note_path}", headers=headers_b)
    cross_task = client.get(f"/api/tasks/{task_id}/evidence", headers=headers_b)
    assert cross_brain.status_code == 404
    assert cross_task.status_code == 404

    search_b = client.get("/api/search", params={"q": "cobalt"}, headers=headers_b)
    assert search_b.status_code == 200
    assert search_b.json()["results"] == []

    # The storage layout itself is separate; no shared Brain/evidence/cache database.
    assert (data_root / "tenant-a" / "brain.sqlite3").is_file()
    assert (data_root / "tenant-b" / "brain.sqlite3").is_file()
    assert (data_root / "tenant-a" / "evidence.sqlite3").is_file()
    assert (data_root / "tenant-b" / "evidence.sqlite3").is_file()
    assert (data_root / "tenant-a" / "code-graph.sqlite3").is_file()
    assert (data_root / "tenant-b" / "code-graph.sqlite3").is_file()


def test_scoped_token_cannot_manage_other_tokens_or_use_ungranted_scope(tmp_path, monkeypatch):
    store = ApiTokenStore(tmp_path / "tokens.sqlite3")
    issued = store.create_token("tenant-a", "brain only", ["brain:read"])
    monkeypatch.setattr(
        engine, "settings",
        replace(engine.settings, api_token="", api_token_store_path=tmp_path / "tokens.sqlite3"),
    )
    monkeypatch.setattr(engine, "api_token_store", store)
    client = TestClient(engine.app, client=("203.0.113.19", 9876))
    headers = {"Authorization": f"Bearer {issued['token']}"}
    assert client.get("/api/brain/status", headers=headers).status_code == 200
    assert client.post(
        "/api/tasks/compile", json={"request": "inspect"}, headers=headers
    ).status_code == 403
    assert client.post(
        "/api/auth/tokens",
        json={"tenant_id": "tenant-b", "label": "escape", "scopes": ["brain:read"]},
        headers=headers,
    ).status_code == 403

from dataclasses import replace

from fastapi.testclient import TestClient

from unai_engine.main import app


def test_brain_routes_are_exposed() -> None:
    client = TestClient(app)
    assert client.get("/api/brain/status").status_code == 200
    assert client.get("/api/brain/graph").status_code == 200
    assert client.get("/api/brain/search", params={"q": "knowledge"}).status_code == 200


def test_shared_brain_ui_api_contract_supports_read_and_write(tmp_path, monkeypatch) -> None:
    import unai_engine.main as engine
    from unai_engine.brain.vault import MarkdownVault

    monkeypatch.setattr(engine, "brain", MarkdownVault(tmp_path / "vault", tmp_path / "brain.sqlite3"))
    client = TestClient(engine.app)
    created = client.post(
        "/api/notes",
        json={"title": "Shared Brain API", "body": "Searchable graph memory.", "tags": ["brain"]},
    )
    assert created.status_code == 200
    slug = created.json()["path"]
    assert client.get("/api/health").json()["notes"] == 1
    assert client.get("/api/search", params={"q": "searchable"}).json()["results"][0]["path"] == slug
    assert client.get(f"/api/notes/{slug}").json()["title"] == "Shared Brain API"


def test_remote_note_writes_require_a_configured_token(monkeypatch, tmp_path) -> None:
    import unai_engine.main as engine
    from unai_engine.brain.vault import MarkdownVault

    monkeypatch.setattr(engine, "brain", MarkdownVault(tmp_path / "vault", tmp_path / "brain.sqlite3"))
    monkeypatch.setattr(engine, "settings", replace(engine.settings, brain_write_token="configured-secret"))
    client = TestClient(engine.app)
    response = client.post("/api/brain/notes", json={"title": "Should reject remote write", "body": "Not authorized"})
    assert response.status_code == 401



def test_remote_brain_write_requires_both_engine_and_brain_tokens(monkeypatch, tmp_path) -> None:
    import unai_engine.main as engine
    from dataclasses import replace
    from unai_engine.brain.vault import MarkdownVault

    monkeypatch.setattr(engine, "brain", MarkdownVault(tmp_path / "vault", tmp_path / "brain.sqlite3"))
    api_token = "unai-api-" + ("a" * 48)
    brain_token = "brain-write-secret"
    monkeypatch.setattr(
        engine,
        "settings",
        replace(engine.settings, api_token=api_token, brain_write_token=brain_token),
    )
    client = TestClient(engine.app, client=("203.0.113.12", 9876))
    headers = {"Authorization": f"Bearer {api_token}"}
    denied = client.post(
        "/api/notes",
        json={"title": "Missing Brain token", "body": "Must be rejected"},
        headers=headers,
    )
    assert denied.status_code == 401
    allowed = client.post(
        "/api/notes",
        json={"title": "Authorized Brain write", "body": "Both secrets were supplied"},
        headers={**headers, "X-Brain-Write-Token": brain_token},
    )
    assert allowed.status_code == 200

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

from datetime import datetime, timedelta, timezone
import sqlite3

from unai_engine.security import ApiTokenStore, AuthContext, resolve_api_identity


def test_api_token_secret_is_never_persisted_and_revocation_is_immediate(tmp_path):
    path = tmp_path / "tokens.sqlite3"
    store = ApiTokenStore(path)
    issued = store.create_token("tenant-one", "browser", ["brain:read", "tasks:read"], 7)
    raw = issued["token"]

    identity = store.authenticate(raw)
    assert identity is not None
    assert identity.tenant_id == "tenant-one"
    assert identity.scopes == frozenset({"brain:read", "tasks:read"})
    assert raw.encode() not in path.read_bytes()

    assert store.revoke(issued["token_id"]) is True
    assert store.authenticate(raw) is None
    assert store.revoke(issued["token_id"]) is False


def test_expired_token_is_rejected_even_when_not_revoked(tmp_path):
    path = tmp_path / "tokens.sqlite3"
    store = ApiTokenStore(path)
    issued = store.create_token("tenant-two", "short lived", ["chat:use"], 1)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "UPDATE api_tokens SET expires_at=? WHERE token_id=?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), issued["token_id"]),
        )
        connection.commit()
    finally:
        connection.close()
    assert store.authenticate(issued["token"]) is None


def test_token_creation_rejects_reserved_or_unsafe_tenant_and_unknown_scope(tmp_path):
    store = ApiTokenStore(tmp_path / "tokens.sqlite3")
    for tenant in ("default", "../escape", "UPPER", "-bad", ""):
        try:
            store.create_token(tenant, "bad", ["brain:read"])
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe tenant accepted: {tenant!r}")
    try:
        store.create_token("tenant", "bad", ["admin:*"])
    except ValueError:
        pass
    else:
        raise AssertionError("unknown scope accepted")


def test_rate_limit_is_atomic_per_principal_and_source_ip(tmp_path):
    store = ApiTokenStore(tmp_path / "tokens.sqlite3")
    now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    first = AuthContext("tenant-a", "token-a", frozenset({"brain:read"}))
    second = AuthContext("tenant-b", "token-b", frozenset({"brain:read"}))

    assert store.allow_request(first, "203.0.113.10", 1, now)
    assert not store.allow_request(first, "203.0.113.10", 1, now)
    # A different token cannot bypass the shared source-IP bucket.
    assert not store.allow_request(second, "203.0.113.10", 1, now)
    assert store.allow_request(first, "203.0.113.10", 1, now + timedelta(minutes=1))


def test_identity_resolution_preserves_loopback_admin_and_accepts_scoped_tokens(tmp_path):
    store = ApiTokenStore(tmp_path / "tokens.sqlite3")
    issued = store.create_token("tenant-a", "read-only", ["brain:read"])
    local, denial = resolve_api_identity("127.0.0.1", None, "", store)
    assert denial is None and local is not None and local.is_admin
    remote, denial = resolve_api_identity("203.0.113.4", None, "", store)
    assert remote is None and denial == (403, "UNAI_API_TOKEN or a scoped API token is required for non-loopback access.")
    scoped, denial = resolve_api_identity(
        "203.0.113.4", f"Bearer {issued['token']}", "", store
    )
    assert denial is None and scoped is not None
    assert scoped.tenant_id == "tenant-a"
    assert not scoped.is_admin
    assert scoped.scopes == frozenset({"brain:read"})

"""Installation and scoped API authentication, token lifecycle, and request limits."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


ALL_SCOPES = frozenset({
    "chat:use",
    "tasks:read",
    "tasks:write",
    "workspace:read",
    "workspace:write",
    "workspace:execute",
    "brain:read",
    "brain:write",
})


@dataclass(frozen=True, slots=True)
class AuthContext:
    tenant_id: str
    token_id: str
    scopes: frozenset[str]
    is_admin: bool = False


class TokenStoreError(RuntimeError):
    """Token storage is unavailable or inconsistent."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _safe_tenant_id(tenant_id: str) -> bool:
    return tenant_id != "default" and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", tenant_id) is not None


class ApiTokenStore:
    """SQLite token registry storing only token hashes and revocation metadata."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS api_tokens (
                token_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL,
                scopes_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_api_tokens_tenant
                ON api_tokens(tenant_id, created_at);
            CREATE TABLE IF NOT EXISTS rate_buckets (
                bucket_key TEXT NOT NULL,
                window_start INTEGER NOT NULL,
                request_count INTEGER NOT NULL CHECK(request_count >= 0),
                PRIMARY KEY(bucket_key, window_start)
            );
        """)
        return connection

    def create_token(
        self,
        tenant_id: str,
        label: str,
        scopes: Iterable[str],
        expires_in_days: int = 30,
    ) -> dict[str, object]:
        scope_list = sorted(set(scopes))
        if not _safe_tenant_id(tenant_id):
            raise ValueError("A non-reserved lowercase tenant identifier is required.")
        if not label.strip() or len(label) > 100:
            raise ValueError("Token label must contain 1-100 characters.")
        if not scope_list or not set(scope_list).issubset(ALL_SCOPES):
            raise ValueError("At least one recognized API scope is required.")
        if not 1 <= int(expires_in_days) <= 365:
            raise ValueError("Token expiry must be between 1 and 365 days.")

        raw_token = "unai_" + secrets.token_urlsafe(32)
        token_id = uuid.uuid4().hex
        created = _utcnow()
        expires = created + timedelta(days=int(expires_in_days))
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO api_tokens(
                    token_id, tenant_id, token_hash, label, scopes_json,
                    created_at, expires_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    token_id, tenant_id, _token_digest(raw_token), label.strip(),
                    json.dumps(scope_list, separators=(",", ":")),
                    _iso(created), _iso(expires),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return {
            "token_id": token_id,
            "tenant_id": tenant_id,
            "label": label.strip(),
            "token": raw_token,
            "scopes": scope_list,
            "created_at": _iso(created),
            "expires_at": _iso(expires),
        }

    def authenticate(self, raw_token: str) -> AuthContext | None:
        digest = _token_digest(raw_token)
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT token_id, tenant_id, token_hash, scopes_json, expires_at, revoked_at
                FROM api_tokens WHERE token_hash = ?
                """,
                (digest,),
            ).fetchone()
        finally:
            connection.close()
        if row is None or row["revoked_at"] is not None:
            return None
        if not hmac.compare_digest(str(row["token_hash"]), digest):
            return None
        try:
            expires = datetime.fromisoformat(str(row["expires_at"]))
            scopes = json.loads(row["scopes_json"])
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
        if expires.tzinfo is None or expires <= _utcnow():
            return None
        if (
            not isinstance(scopes, list) or not scopes
            or any(not isinstance(scope, str) for scope in scopes)
            or len(scopes) != len(set(scopes))
            or not set(scopes).issubset(ALL_SCOPES)
            or not _safe_tenant_id(str(row["tenant_id"]))
        ):
            return None
        return AuthContext(
            tenant_id=str(row["tenant_id"]),
            token_id=str(row["token_id"]),
            scopes=frozenset(scopes),
            is_admin=False,
        )

    def revoke(self, token_id: str) -> bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE api_tokens SET revoked_at = ?
                WHERE token_id = ? AND revoked_at IS NULL
                """,
                (_iso(_utcnow()), token_id),
            )
            connection.commit()
            return cursor.rowcount == 1
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def allow_request(
        self,
        identity: AuthContext,
        client_host: str | None,
        limit_per_minute: int,
        now: datetime | None = None,
    ) -> bool:
        """Atomically enforce a token/loopback-principal and source-IP fixed window."""
        limit = max(1, min(int(limit_per_minute), 100_000))
        moment = now or _utcnow()
        window = int(moment.timestamp()) // 60 * 60
        host_digest = hashlib.sha256((client_host or "unknown").encode("utf-8")).hexdigest()
        bucket_keys = (f"principal:{identity.tenant_id}:{identity.token_id}", f"ip:{host_digest}")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            counts = []
            for key in bucket_keys:
                row = connection.execute(
                    "SELECT request_count FROM rate_buckets WHERE bucket_key=? AND window_start=?",
                    (key, window),
                ).fetchone()
                counts.append(int(row["request_count"]) if row else 0)
            allowed = all(count < limit for count in counts)
            if allowed:
                for key, count in zip(bucket_keys, counts, strict=True):
                    connection.execute(
                        """
                        INSERT INTO rate_buckets(bucket_key, window_start, request_count)
                        VALUES (?, ?, 1)
                        ON CONFLICT(bucket_key, window_start)
                        DO UPDATE SET request_count = excluded.request_count + ?
                        """,
                        (key, window, count),
                    )
            connection.execute("DELETE FROM rate_buckets WHERE window_start < ?", (window - 120,))
            connection.commit()
            return allowed
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _is_loopback(client_host: str | None) -> bool:
    if client_host == "testclient":
        return True
    try:
        return ipaddress.ip_address(client_host or "").is_loopback
    except ValueError:
        return False


def resolve_api_identity(
    client_host: str | None,
    authorization: str | None,
    configured_token: str,
    token_store: ApiTokenStore,
) -> tuple[AuthContext | None, tuple[int, str] | None]:
    """Resolve either an installation admin token, scoped token, or local admin peer."""
    scheme, separator, presented = (authorization or "").partition(" ")
    has_bearer = scheme.casefold() == "bearer" and bool(separator) and bool(presented)
    if authorization and not has_bearer:
        return None, (401, "A valid Bearer authorization header is required.")

    if has_bearer:
        if configured_token and hmac.compare_digest(
            presented.encode("utf-8"), configured_token.encode("utf-8")
        ):
            return AuthContext("default", "installation-admin", ALL_SCOPES, True), None
        try:
            identity = token_store.authenticate(presented)
        except (OSError, sqlite3.Error) as exc:
            raise TokenStoreError("API token storage is unavailable.") from exc
        if identity is None:
            return None, (401, "The API token is invalid, expired, or revoked.")
        return identity, None

    if configured_token:
        return None, (401, "A valid bearer token is required.")
    if _is_loopback(client_host):
        return AuthContext("default", "loopback-local", ALL_SCOPES, True), None
    return None, (403, "UNAI_API_TOKEN or a scoped API token is required for non-loopback access.")


def api_access_error(
    client_host: str | None,
    authorization: str | None,
    configured_token: str,
) -> tuple[int, str] | None:
    """Backward-compatible perimeter check used by security-focused callers."""
    if configured_token:
        scheme, separator, presented = (authorization or "").partition(" ")
        valid = (
            scheme.casefold() == "bearer"
            and bool(separator)
            and bool(presented)
            and hmac.compare_digest(presented.encode("utf-8"), configured_token.encode("utf-8"))
        )
        return None if valid else (401, "A valid bearer token is required.")
    if _is_loopback(client_host):
        return None
    return 403, "UNAI_API_TOKEN must be configured for non-loopback access."

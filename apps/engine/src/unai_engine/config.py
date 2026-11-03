from __future__ import annotations

import base64
import binascii
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from unai_engine.verification.publisher_trust_bundle import (
    PublisherTrustBundleError,
    load_signed_publisher_trust_bundle,
    parse_publisher_trust_roots,
)

load_dotenv()
REPO_ROOT = Path(__file__).resolve().parents[4]


def _path_setting(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default)).expanduser()
    return (value if value.is_absolute() else REPO_ROOT / value).resolve()


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _specialist_model_from_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}", value) is None:
        raise ValueError(f"{name} must be a safe model identifier with no spaces or control characters.")
    return value


def _oracle_public_keys_from_env() -> dict[str, str]:
    """Load reviewer key IDs mapped to base64 raw Ed25519 public keys."""
    raw = os.getenv("UNAI_ORACLE_TRUSTED_PUBLIC_KEYS", "{}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("UNAI_ORACLE_TRUSTED_PUBLIC_KEYS must be a JSON object.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("UNAI_ORACLE_TRUSTED_PUBLIC_KEYS must be a JSON object.")

    validated: dict[str, str] = {}
    for key_id, public_key_b64 in parsed.items():
        if (
            not isinstance(key_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", key_id) is None
            or not isinstance(public_key_b64, str)
        ):
            raise ValueError("Trusted oracle key entries require safe string IDs and base64 public keys.")
        try:
            public_key = base64.b64decode(public_key_b64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError(f"Trusted oracle public key {key_id!r} is not valid base64.") from exc
        if len(public_key) != 32:
            raise ValueError(f"Trusted oracle public key {key_id!r} must decode to 32 bytes.")
        validated[key_id] = public_key_b64
    return validated


def _oracle_publisher_keys_from_env() -> dict[str, dict[str, Any]]:
    """Load direct publisher keys or a signed, operator-rooted trust snapshot."""
    bundle_raw = os.getenv("UNAI_ORACLE_PUBLISHER_TRUST_BUNDLE", "").strip()
    if bundle_raw:
        direct_raw = os.getenv("UNAI_ORACLE_TRUSTED_PUBLISHER_KEYS", "{}").strip() or "{}"
        try:
            direct_entries = json.loads(direct_raw)
        except json.JSONDecodeError as exc:
            raise ValueError("UNAI_ORACLE_TRUSTED_PUBLISHER_KEYS must be valid JSON when a trust bundle is configured.") from exc
        if direct_entries != {}:
            raise ValueError(
                "Configure either UNAI_ORACLE_PUBLISHER_TRUST_BUNDLE or direct UNAI_ORACLE_TRUSTED_PUBLISHER_KEYS, not both."
            )
        try:
            roots = parse_publisher_trust_roots(
                os.getenv("UNAI_ORACLE_PUBLISHER_TRUST_ROOTS", "{}")
            )
            minimum_raw = os.getenv("UNAI_ORACLE_PUBLISHER_TRUST_MIN_SEQUENCE", "0")
            if not minimum_raw.isdecimal():
                raise PublisherTrustBundleError(
                    "UNAI_ORACLE_PUBLISHER_TRUST_MIN_SEQUENCE must be a non-negative integer."
                )
            publishers, _provenance = load_signed_publisher_trust_bundle(
                bundle_raw,
                roots,
                minimum_sequence=int(minimum_raw),
            )
            return publishers
        except PublisherTrustBundleError as exc:
            raise ValueError(str(exc)) from exc

    raw = os.getenv("UNAI_ORACLE_TRUSTED_PUBLISHER_KEYS", "{}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("UNAI_ORACLE_TRUSTED_PUBLISHER_KEYS must be a JSON object.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("UNAI_ORACLE_TRUSTED_PUBLISHER_KEYS must be a JSON object.")

    validated: dict[str, dict[str, Any]] = {}
    for key_id, entry in parsed.items():
        if (
            not isinstance(key_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", key_id) is None
            or not isinstance(entry, dict)
        ):
            raise ValueError("Publisher key entries require safe string IDs and object values.")
        allowed_fields = {
            "public_key_b64", "issuer", "allowed_domains",
            "not_before", "not_after", "revoked",
        }
        public_key_b64 = entry.get("public_key_b64")
        issuer = entry.get("issuer")
        domains = entry.get("allowed_domains")
        revoked = entry.get("revoked", False)
        if (
            set(entry) - allowed_fields
            or not isinstance(public_key_b64, str)
            or not isinstance(issuer, str)
            or not 2 <= len(issuer.strip()) <= 128
            or not isinstance(domains, list)
            or not 1 <= len(domains) <= 20
            or not all(isinstance(domain, str) for domain in domains)
            or not isinstance(revoked, bool)
        ):
            raise ValueError(
                f"Publisher key {key_id!r} requires public_key_b64, issuer, 1-20 allowed_domains and a boolean revoked flag."
            )

        def parse_window_bound(value: object, field_name: str) -> datetime | None:
            if value is None:
                return None
            if not isinstance(value, str):
                raise ValueError(
                    f"Publisher key {key_id!r} {field_name} must be a timezone-aware ISO-8601 timestamp."
                )
            try:
                parsed_time = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(
                    f"Publisher key {key_id!r} {field_name} is not a valid ISO-8601 timestamp."
                ) from exc
            if parsed_time.tzinfo is None or parsed_time.utcoffset() is None:
                raise ValueError(
                    f"Publisher key {key_id!r} {field_name} must include a timezone."
                )
            return parsed_time

        not_before = parse_window_bound(entry.get("not_before"), "not_before")
        not_after = parse_window_bound(entry.get("not_after"), "not_after")
        if not_before is not None and not_after is not None and not_before >= not_after:
            raise ValueError(f"Publisher key {key_id!r} has an invalid issuance window.")
        try:
            public_key = base64.b64decode(public_key_b64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError(f"Publisher public key {key_id!r} is not valid base64.") from exc
        if len(public_key) != 32:
            raise ValueError(f"Publisher public key {key_id!r} must decode to 32 bytes.")

        normalized_domains: list[str] = []
        for domain in domains:
            normalized = domain.casefold().rstrip(".")
            if (
                not normalized
                or len(normalized) > 253
                or normalized.startswith(".")
                or ".." in normalized
                or re.fullmatch(r"[a-z0-9.-]+", normalized) is None
                or any(
                    not label
                    or len(label) > 63
                    or label.startswith("-")
                    or label.endswith("-")
                    for label in normalized.split(".")
                )
            ):
                raise ValueError(
                    f"Publisher key {key_id!r} contains an invalid exact hostname allowlist entry."
                )
            if normalized not in normalized_domains:
                normalized_domains.append(normalized)
        validated[key_id] = {
            "public_key_b64": public_key_b64,
            "issuer": issuer.strip(),
            "allowed_domains": normalized_domains,
            "not_before": not_before,
            "not_after": not_after,
            "revoked": revoked,
        }
    return validated


@dataclass(frozen=True, slots=True)
class Settings:
    model_base_url: str = os.getenv("UNAI_MODEL_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/")
    model_name: str = os.getenv("UNAI_MODEL_NAME", "unai-local-coder")
    model_api_key: str = os.getenv("UNAI_MODEL_API_KEY", "")
    model_timeout_seconds: float = float(os.getenv("UNAI_MODEL_TIMEOUT_SECONDS", "180"))
    specialist_next_action_model: str = field(default_factory=lambda: _specialist_model_from_env("UNAI_SPECIALIST_NEXT_ACTION_MODEL"))
    specialist_planner_model: str = field(default_factory=lambda: _specialist_model_from_env("UNAI_SPECIALIST_PLANNER_MODEL"))
    specialist_verifier_model: str = field(default_factory=lambda: _specialist_model_from_env("UNAI_SPECIALIST_VERIFIER_MODEL"))
    cors_origins: tuple[str, ...] = _csv(os.getenv("UNAI_CORS_ORIGINS", "http://localhost:5173"))
    api_token: str = os.getenv("UNAI_API_TOKEN", "")
    workspace_root: Path = _path_setting("UNAI_WORKSPACE_ROOT", ".workspaces")
    docker_image: str = os.getenv("UNAI_DOCKER_IMAGE", "unai-python-test:latest")
    sandbox_timeout_seconds: int = int(os.getenv("UNAI_SANDBOX_TIMEOUT_SECONDS", "120"))
    brain_vault_root: Path = _path_setting("UNAI_BRAIN_VAULT_ROOT", ".brain-vault")
    brain_index_path: Path = _path_setting("UNAI_BRAIN_INDEX_PATH", ".data/brain.sqlite3")
    evidence_db_path: Path = _path_setting("UNAI_EVIDENCE_DB_PATH", ".data/evidence.sqlite3")
    code_graph_db_path: Path = _path_setting("UNAI_CODE_GRAPH_DB_PATH", ".data/code-graph.sqlite3")
    test_timeout_seconds: int = int(os.getenv("UNAI_TEST_TIMEOUT_SECONDS", "120"))
    test_max_files: int = int(os.getenv("UNAI_TEST_MAX_FILES", "5000"))
    test_max_bytes: int = int(os.getenv("UNAI_TEST_MAX_BYTES", str(50 * 1024 * 1024)))
    brain_write_token: str = os.getenv("UNAI_BRAIN_WRITE_TOKEN", "")
    oracle_trusted_public_keys: dict[str, str] = field(
        default_factory=_oracle_public_keys_from_env
    )
    oracle_trusted_publisher_keys: dict[str, dict[str, Any]] = field(
        default_factory=_oracle_publisher_keys_from_env
    )

    def __post_init__(self) -> None:
        if self.api_token and len(self.api_token) < 32:
            raise ValueError("UNAI_API_TOKEN must contain at least 32 characters.")


settings = Settings()

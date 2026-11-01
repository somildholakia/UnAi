from __future__ import annotations

import base64
import binascii
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()
REPO_ROOT = Path(__file__).resolve().parents[4]


def _path_setting(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default)).expanduser()
    return (value if value.is_absolute() else REPO_ROOT / value).resolve()


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


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
    """Load publisher keys, issuer identities and exact HTTPS hostname allowlists."""
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
        public_key_b64 = entry.get("public_key_b64")
        issuer = entry.get("issuer")
        domains = entry.get("allowed_domains")
        if (
            not isinstance(public_key_b64, str)
            or not isinstance(issuer, str)
            or not 2 <= len(issuer.strip()) <= 128
            or not isinstance(domains, list)
            or not 1 <= len(domains) <= 20
            or not all(isinstance(domain, str) for domain in domains)
        ):
            raise ValueError(
                f"Publisher key {key_id!r} requires public_key_b64, issuer and 1-20 allowed_domains."
            )
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
        }
    return validated


@dataclass(frozen=True, slots=True)
class Settings:
    model_base_url: str = os.getenv("UNAI_MODEL_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
    model_name: str = os.getenv("UNAI_MODEL_NAME", "qwen3-coder:latest")
    model_api_key: str = os.getenv("UNAI_MODEL_API_KEY", "")
    model_timeout_seconds: float = float(os.getenv("UNAI_MODEL_TIMEOUT_SECONDS", "180"))
    cors_origins: tuple[str, ...] = _csv(os.getenv("UNAI_CORS_ORIGINS", "http://localhost:5173"))
    api_token: str = os.getenv("UNAI_API_TOKEN", "")
    workspace_root: Path = _path_setting("UNAI_WORKSPACE_ROOT", ".workspaces")
    docker_image: str = os.getenv("UNAI_DOCKER_IMAGE", "unai-python-test:latest")
    sandbox_timeout_seconds: int = int(os.getenv("UNAI_SANDBOX_TIMEOUT_SECONDS", "120"))
    brain_vault_root: Path = _path_setting("UNAI_BRAIN_VAULT_ROOT", ".brain-vault")
    brain_index_path: Path = _path_setting("UNAI_BRAIN_INDEX_PATH", ".data/brain.sqlite3")
    evidence_db_path: Path = _path_setting("UNAI_EVIDENCE_DB_PATH", ".data/evidence.sqlite3")
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

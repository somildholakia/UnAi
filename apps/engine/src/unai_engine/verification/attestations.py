"""Trusted reviewer attestations for specification-grounded oracle cases.

A valid signature authenticates that a configured reviewer key signed the exact
test assertion, including the source, artifact, input, expected result/property and
task/workspace context. It does not prove the assertion is logically correct.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


ATTESTATION_SCHEMA = "unai.oracle-attestation.v1"


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Attestation issued_at must be timezone-aware.")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def build_oracle_attestation_statement(
    *,
    key_id: str,
    issued_at: datetime,
    task_id: str,
    workspace: str,
    file_path: str,
    symbol_name: str,
    source_sha256: str,
    case_id: str,
    oracle_kind: str,
    oracle_reference: str,
    oracle_artifact_path: str,
    oracle_artifact_sha256: str,
    arguments_sha256: str,
    expected_output_sha256: str | None,
    oracle_property_sha256: str | None,
) -> dict[str, Any]:
    """Build the exact assertion envelope that a trusted reviewer must sign."""
    return {
        "schema": ATTESTATION_SCHEMA,
        "key_id": key_id,
        "issued_at": _utc_timestamp(issued_at),
        "task_id": task_id,
        "workspace": workspace,
        "file_path": file_path,
        "symbol_name": symbol_name,
        "source_sha256": source_sha256.lower(),
        "case_id": case_id,
        "oracle_kind": oracle_kind,
        "oracle_reference": oracle_reference,
        "oracle_artifact_path": oracle_artifact_path,
        "oracle_artifact_sha256": oracle_artifact_sha256.lower(),
        "arguments_sha256": arguments_sha256.lower(),
        "expected_output_sha256": expected_output_sha256.lower() if expected_output_sha256 else None,
        "oracle_property_sha256": oracle_property_sha256.lower() if oracle_property_sha256 else None,
    }


def build_publisher_artifact_statement(
    *,
    key_id: str,
    issuer: str,
    issued_at: datetime,
    artifact_uri: str,
    artifact_revision: str,
    artifact_sha256: str,
) -> dict[str, Any]:
    """Build the signed manifest that binds a publisher identity to exact artifact bytes."""
    return {
        "schema": "unai.spec-publisher-manifest.v1",
        "key_id": key_id,
        "issuer": issuer,
        "issued_at": _utc_timestamp(issued_at),
        "artifact_uri": artifact_uri,
        "artifact_revision": artifact_revision,
        "artifact_sha256": artifact_sha256.lower(),
    }


def canonical_attestation_message(statement: dict[str, Any]) -> bytes:
    """Return UTF-8 canonical JSON bytes; no caller code is evaluated."""
    return json.dumps(
        statement,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def attestation_statement_sha256(statement: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_attestation_message(statement)).hexdigest()


def verify_ed25519_attestation(
    *,
    public_key_b64: str,
    signature_b64: str,
    message: bytes,
) -> bool:
    """Verify strict base64 Ed25519 material against an operator-configured key."""
    try:
        public_key_bytes = base64.b64decode(public_key_b64, validate=True)
        signature = base64.b64decode(signature_b64, validate=True)
        if len(public_key_bytes) != 32 or len(signature) != 64:
            return False
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(signature, message)
        return True
    except (InvalidSignature, ValueError, binascii.Error):
        return False

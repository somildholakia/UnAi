"""Offline verification for signed publisher trust-bundle snapshots.

The bundle signer is an operator-pinned trust root. This module deliberately does
not perform network discovery, follow redirects, or infer legal identity.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .attestations import canonical_attestation_message, verify_ed25519_attestation

TRUST_BUNDLE_SCHEMA = "unai.publisher-trust-bundle.v1"
_MAX_BUNDLE_BYTES = 256 * 1024
_MAX_PUBLISHERS = 256
_MAX_BUNDLE_LIFETIME = timedelta(days=30)
_MAX_CLOCK_SKEW = timedelta(minutes=5)
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DOMAIN_RE = re.compile(r"^[a-z0-9.-]+$")


class PublisherTrustBundleError(ValueError):
    """A signed publisher trust bundle is malformed, untrusted, stale, or downgraded."""


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PublisherTrustBundleError(f"Duplicate JSON member {key!r} is not allowed.")
        result[key] = value
    return result


def _parse_json_object(raw: str, *, description: str, max_bytes: int | None = None) -> dict[str, Any]:
    if not isinstance(raw, str):
        raise PublisherTrustBundleError(f"{description} must be JSON text.")
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PublisherTrustBundleError(f"{description} must be valid UTF-8.") from exc
    if max_bytes is not None and len(encoded) > max_bytes:
        raise PublisherTrustBundleError(f"{description} exceeds the {max_bytes}-byte limit.")
    try:
        parsed = json.loads(raw, object_pairs_hook=_strict_json_object)
    except (json.JSONDecodeError, PublisherTrustBundleError) as exc:
        if isinstance(exc, PublisherTrustBundleError):
            raise
        raise PublisherTrustBundleError(f"{description} must be valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise PublisherTrustBundleError(f"{description} must be a JSON object.")
    return parsed


def parse_publisher_trust_roots(raw: str) -> dict[str, str]:
    """Parse the operator-pinned transparency-root keyring."""
    roots = _parse_json_object(raw, description="UNAI_ORACLE_PUBLISHER_TRUST_ROOTS", max_bytes=32 * 1024)
    validated: dict[str, str] = {}
    for key_id, public_key_b64 in roots.items():
        if not isinstance(key_id, str) or not _KEY_ID_RE.fullmatch(key_id):
            raise PublisherTrustBundleError("Transparency-root entries require safe key IDs.")
        if not isinstance(public_key_b64, str):
            raise PublisherTrustBundleError(f"Transparency-root key {key_id!r} must be base64 text.")
        try:
            public_key = base64.b64decode(public_key_b64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise PublisherTrustBundleError(f"Transparency-root key {key_id!r} is not valid base64.") from exc
        if len(public_key) != 32:
            raise PublisherTrustBundleError(f"Transparency-root key {key_id!r} must decode to 32 bytes.")
        validated[key_id] = public_key_b64
    if not validated:
        raise PublisherTrustBundleError("At least one operator-pinned transparency root is required.")
    return validated


def _timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise PublisherTrustBundleError(f"Trust bundle {field_name} must be a timezone-aware ISO-8601 timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PublisherTrustBundleError(f"Trust bundle {field_name} is not a valid ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PublisherTrustBundleError(f"Trust bundle {field_name} must include an explicit timezone.")
    return parsed.astimezone(timezone.utc)


def _validate_publisher_entries(entries: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(entries, dict) or len(entries) > _MAX_PUBLISHERS:
        raise PublisherTrustBundleError(f"Trust bundle publishers must be an object with at most {_MAX_PUBLISHERS} entries.")

    validated: dict[str, dict[str, Any]] = {}
    allowed_fields = {"public_key_b64", "issuer", "allowed_domains", "not_before", "not_after", "revoked"}
    for key_id, entry in entries.items():
        if not isinstance(key_id, str) or not _KEY_ID_RE.fullmatch(key_id) or not isinstance(entry, dict):
            raise PublisherTrustBundleError("Publisher entries require safe key IDs and object values.")
        if set(entry) - allowed_fields:
            raise PublisherTrustBundleError(f"Publisher key {key_id!r} contains unsupported fields.")

        public_key_b64 = entry.get("public_key_b64")
        issuer = entry.get("issuer")
        domains = entry.get("allowed_domains")
        revoked = entry.get("revoked", False)
        if (
            not isinstance(public_key_b64, str)
            or not isinstance(issuer, str)
            or not 2 <= len(issuer.strip()) <= 128
            or not isinstance(domains, list)
            or not 1 <= len(domains) <= 20
            or not all(isinstance(domain, str) for domain in domains)
            or not isinstance(revoked, bool)
        ):
            raise PublisherTrustBundleError(
                f"Publisher key {key_id!r} requires a 32-byte public key, issuer, 1-20 domains and boolean revoked flag."
            )

        try:
            public_key = base64.b64decode(public_key_b64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise PublisherTrustBundleError(f"Publisher public key {key_id!r} is not valid base64.") from exc
        if len(public_key) != 32:
            raise PublisherTrustBundleError(f"Publisher public key {key_id!r} must decode to 32 bytes.")

        normalized_domains: list[str] = []
        for domain in domains:
            normalized = domain.casefold().rstrip(".")
            if (
                not normalized
                or len(normalized) > 253
                or normalized.startswith(".")
                or ".." in normalized
                or "*" in normalized
                or _DOMAIN_RE.fullmatch(normalized) is None
                or any(
                    not label
                    or len(label) > 63
                    or label.startswith("-")
                    or label.endswith("-")
                    for label in normalized.split(".")
                )
            ):
                raise PublisherTrustBundleError(f"Publisher key {key_id!r} contains an invalid exact hostname.")
            if normalized in {"localhost"} or normalized.endswith(".localhost") or "." not in normalized:
                raise PublisherTrustBundleError(f"Publisher key {key_id!r} contains a non-public hostname.")
            if normalized not in normalized_domains:
                normalized_domains.append(normalized)

        not_before = _timestamp(entry["not_before"], f"{key_id}.not_before") if entry.get("not_before") is not None else None
        not_after = _timestamp(entry["not_after"], f"{key_id}.not_after") if entry.get("not_after") is not None else None
        if not_before is not None and not_after is not None and not_before >= not_after:
            raise PublisherTrustBundleError(f"Publisher key {key_id!r} has an invalid issuance window.")

        validated[key_id] = {
            "public_key_b64": public_key_b64,
            "issuer": issuer.strip(),
            "allowed_domains": normalized_domains,
            "not_before": not_before,
            "not_after": not_after,
            "revoked": revoked,
        }
    return validated


def load_signed_publisher_trust_bundle(
    raw_bundle: str,
    trusted_roots: dict[str, str],
    *,
    minimum_sequence: int = 0,
    now: datetime | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Verify a bounded signed snapshot and return publisher keys plus provenance.

    Rollback protection is explicit: operators must raise minimum_sequence to
    their last accepted sequence when advancing the trust bundle.
    """
    if not isinstance(minimum_sequence, int) or isinstance(minimum_sequence, bool) or minimum_sequence < 0:
        raise PublisherTrustBundleError("Publisher trust minimum sequence must be a non-negative integer.")
    if not isinstance(trusted_roots, dict) or not trusted_roots:
        raise PublisherTrustBundleError("No operator-pinned transparency roots are configured.")

    envelope = _parse_json_object(raw_bundle, description="UNAI_ORACLE_PUBLISHER_TRUST_BUNDLE", max_bytes=_MAX_BUNDLE_BYTES)
    if set(envelope) != {"payload", "signature_b64"}:
        raise PublisherTrustBundleError("Trust bundle envelope must contain exactly payload and signature_b64.")
    payload = envelope["payload"]
    if not isinstance(payload, dict):
        raise PublisherTrustBundleError("Trust bundle payload must be a JSON object.")
    required_fields = {"schema", "root_key_id", "sequence", "issued_at", "expires_at", "publishers"}
    if set(payload) != required_fields:
        raise PublisherTrustBundleError("Trust bundle payload has missing or unsupported fields.")
    if payload.get("schema") != TRUST_BUNDLE_SCHEMA:
        raise PublisherTrustBundleError("Unsupported publisher trust bundle schema.")

    root_key_id = payload.get("root_key_id")
    if not isinstance(root_key_id, str) or root_key_id not in trusted_roots:
        raise PublisherTrustBundleError("Trust bundle root_key_id is not operator-pinned.")
    sequence = payload.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or not 1 <= sequence <= 2**63 - 1:
        raise PublisherTrustBundleError("Trust bundle sequence must be a positive 64-bit integer.")
    if sequence < minimum_sequence:
        raise PublisherTrustBundleError(
            f"Trust bundle sequence {sequence} is below the configured minimum {minimum_sequence}."
        )

    issued_at = _timestamp(payload.get("issued_at"), "issued_at")
    expires_at = _timestamp(payload.get("expires_at"), "expires_at")
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise PublisherTrustBundleError("Verification clock must be timezone-aware.")
    clock = clock.astimezone(timezone.utc)
    if issued_at > clock + _MAX_CLOCK_SKEW:
        raise PublisherTrustBundleError("Trust bundle issued_at is too far in the future.")
    if expires_at <= clock:
        raise PublisherTrustBundleError("Trust bundle has expired.")
    if expires_at <= issued_at or expires_at - issued_at > _MAX_BUNDLE_LIFETIME:
        raise PublisherTrustBundleError("Trust bundle validity must be positive and no longer than 30 days.")

    signature_b64 = envelope.get("signature_b64")
    if not isinstance(signature_b64, str):
        raise PublisherTrustBundleError("Trust bundle signature_b64 must be base64 text.")
    message = canonical_attestation_message(payload)
    if not verify_ed25519_attestation(
        public_key_b64=trusted_roots[root_key_id],
        signature_b64=signature_b64,
        message=message,
    ):
        raise PublisherTrustBundleError("Trust bundle signature is invalid.")

    publishers = _validate_publisher_entries(payload["publishers"])
    payload_sha256 = hashlib.sha256(message).hexdigest()
    provenance = {
        "schema": TRUST_BUNDLE_SCHEMA,
        "sequence": sequence,
        "root_key_id": root_key_id,
        "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "payload_sha256": payload_sha256,
        "publisher_count": len(publishers),
        "minimum_sequence": minimum_sequence,
        "rollback_floor_enforced": True,
    }
    for entry in publishers.values():
        entry["_trust_bundle"] = provenance.copy()
    return publishers, provenance

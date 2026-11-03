from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from unai_engine.verification.attestations import canonical_attestation_message
from unai_engine.verification.publisher_trust_bundle import (
    PublisherTrustBundleError,
    TRUST_BUNDLE_SCHEMA,
    load_signed_publisher_trust_bundle,
    parse_publisher_trust_roots,
)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _public_key_b64(private_key: Ed25519PrivateKey) -> str:
    return _b64(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )


def _make_bundle(
    *,
    now: datetime | None = None,
    sequence: int = 7,
    expires_delta: timedelta = timedelta(days=2),
    issued_delta: timedelta = timedelta(minutes=-1),
    publishers: dict | None = None,
) -> tuple[str, dict[str, str], dict]:
    clock = now or datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    root = Ed25519PrivateKey.generate()
    publisher = Ed25519PrivateKey.generate()
    payload = {
        "schema": TRUST_BUNDLE_SCHEMA,
        "root_key_id": "offline-root-2026",
        "sequence": sequence,
        "issued_at": (clock + issued_delta).isoformat().replace("+00:00", "Z"),
        "expires_at": (clock + expires_delta).isoformat().replace("+00:00", "Z"),
        "publishers": publishers or {
            "acme-specs-2026": {
                "public_key_b64": _public_key_b64(publisher),
                "issuer": "Acme Specifications",
                "allowed_domains": ["specs.acme.example"],
                "not_before": "2026-01-01T00:00:00Z",
                "revoked": False,
            }
        },
    }
    signature = _b64(root.sign(canonical_attestation_message(payload)))
    envelope = json.dumps(
        {"payload": payload, "signature_b64": signature},
        sort_keys=True,
        separators=(",", ":"),
    )
    roots = {"offline-root-2026": _public_key_b64(root)}
    return envelope, roots, payload


def test_signed_publisher_trust_bundle_verifies_and_returns_provenance() -> None:
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    bundle, roots, _payload = _make_bundle(now=now)

    publishers, provenance = load_signed_publisher_trust_bundle(
        bundle, roots, minimum_sequence=7, now=now
    )

    assert publishers["acme-specs-2026"]["issuer"] == "Acme Specifications"
    assert publishers["acme-specs-2026"]["allowed_domains"] == ["specs.acme.example"]
    assert publishers["acme-specs-2026"]["revoked"] is False
    assert publishers["acme-specs-2026"]["_trust_bundle"] == provenance
    assert provenance["sequence"] == 7
    assert provenance["root_key_id"] == "offline-root-2026"
    assert provenance["rollback_floor_enforced"] is True
    assert len(provenance["payload_sha256"]) == 64


def test_signed_publisher_trust_bundle_rejects_unpinned_root() -> None:
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    bundle, _roots, _payload = _make_bundle(now=now)
    other_root = Ed25519PrivateKey.generate()
    roots = {"different-root": _public_key_b64(other_root)}

    with pytest.raises(PublisherTrustBundleError, match="not operator-pinned"):
        load_signed_publisher_trust_bundle(bundle, roots, now=now)


def test_signed_publisher_trust_bundle_rejects_bad_signature_and_payload_tampering() -> None:
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    bundle, roots, payload = _make_bundle(now=now)
    payload["sequence"] = 8
    tampered = json.loads(bundle)
    tampered["payload"] = payload

    with pytest.raises(PublisherTrustBundleError, match="signature is invalid"):
        load_signed_publisher_trust_bundle(
            json.dumps(tampered), roots, minimum_sequence=7, now=now
        )


def test_signed_publisher_trust_bundle_enforces_expiry_and_clock_skew() -> None:
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    expired, roots, _ = _make_bundle(
        now=now,
        issued_delta=timedelta(days=-2),
        expires_delta=timedelta(days=-1),
    )
    with pytest.raises(PublisherTrustBundleError, match="has expired"):
        load_signed_publisher_trust_bundle(expired, roots, now=now)

    future, roots, _ = _make_bundle(
        now=now,
        issued_delta=timedelta(minutes=6),
        expires_delta=timedelta(days=1),
    )
    with pytest.raises(PublisherTrustBundleError, match="too far in the future"):
        load_signed_publisher_trust_bundle(future, roots, now=now)


def test_signed_publisher_trust_bundle_caps_validity_to_thirty_days() -> None:
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    bundle, roots, _ = _make_bundle(
        now=now,
        issued_delta=timedelta(days=-1),
        expires_delta=timedelta(days=31),
    )
    with pytest.raises(PublisherTrustBundleError, match="no longer than 30 days"):
        load_signed_publisher_trust_bundle(bundle, roots, now=now)


def test_signed_publisher_trust_bundle_enforces_operator_rollback_floor() -> None:
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    bundle, roots, _ = _make_bundle(now=now, sequence=6)

    with pytest.raises(PublisherTrustBundleError, match="below the configured minimum 7"):
        load_signed_publisher_trust_bundle(
            bundle, roots, minimum_sequence=7, now=now
        )


@pytest.mark.parametrize("sequence", [0, -1, True, 1.5, 2**63])
def test_signed_publisher_trust_bundle_requires_positive_64_bit_sequence(sequence) -> None:
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    bundle, roots, payload = _make_bundle(now=now)
    payload["sequence"] = sequence
    root = Ed25519PrivateKey.generate()
    roots = {"offline-root-2026": _public_key_b64(root)}
    signature = _b64(root.sign(canonical_attestation_message(payload)))
    envelope = json.dumps({"payload": payload, "signature_b64": signature})

    with pytest.raises(PublisherTrustBundleError, match="positive 64-bit integer"):
        load_signed_publisher_trust_bundle(envelope, roots, now=now)


def test_signed_publisher_trust_bundle_rejects_ip_literal_domains() -> None:
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    bundle, roots, _ = _make_bundle(
        now=now,
        publishers={
            "acme-specs-2026": {
                "public_key_b64": _b64(b"x" * 32),
                "issuer": "Acme Specifications",
                "allowed_domains": ["192.0.2.10"],
            }
        },
    )
    with pytest.raises(PublisherTrustBundleError, match="not an IP literal"):
        load_signed_publisher_trust_bundle(bundle, roots, now=now)


def test_signed_publisher_trust_bundle_rejects_wildcards_and_invalid_keys() -> None:
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    wildcard_bundle, roots, _ = _make_bundle(
        now=now,
        publishers={
            "acme-specs-2026": {
                "public_key_b64": _b64(b"x" * 32),
                "issuer": "Acme Specifications",
                "allowed_domains": ["*.acme.example"],
            }
        },
    )
    with pytest.raises(PublisherTrustBundleError, match="invalid exact hostname"):
        load_signed_publisher_trust_bundle(wildcard_bundle, roots, now=now)

    bad_key_bundle, roots, _ = _make_bundle(
        now=now,
        publishers={
            "acme-specs-2026": {
                "public_key_b64": _b64(b"x" * 31),
                "issuer": "Acme Specifications",
                "allowed_domains": ["specs.acme.example"],
            }
        },
    )
    with pytest.raises(PublisherTrustBundleError, match="must decode to 32 bytes"):
        load_signed_publisher_trust_bundle(bad_key_bundle, roots, now=now)


def test_signed_publisher_trust_bundle_rejects_duplicate_json_members_and_oversize() -> None:
    roots = {"root-2026": _b64(b"x" * 32)}
    duplicate = '{"payload": {}, "payload": {}, "signature_b64": "x"}'
    with pytest.raises(PublisherTrustBundleError, match="Duplicate JSON member"):
        load_signed_publisher_trust_bundle(duplicate, roots, now=datetime.now(timezone.utc))

    with pytest.raises(PublisherTrustBundleError, match="exceeds the 262144-byte limit"):
        load_signed_publisher_trust_bundle(" " * (256 * 1024 + 1), roots, now=datetime.now(timezone.utc))


def test_trust_bundle_signing_root_keyring_requires_valid_raw_ed25519_keys() -> None:
    valid = {"root-2026": _b64(b"x" * 32)}
    assert parse_publisher_trust_roots(json.dumps(valid)) == valid

    with pytest.raises(PublisherTrustBundleError, match="must decode to 32 bytes"):
        parse_publisher_trust_roots(json.dumps({"root-2026": _b64(b"x" * 31)}))

    with pytest.raises(PublisherTrustBundleError, match="At least one"):
        parse_publisher_trust_roots("{}")


def test_config_loads_signed_bundle_and_rejects_mixed_keyring(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    bundle, roots, _ = _make_bundle(
        now=now,
        issued_delta=timedelta(minutes=-1),
        expires_delta=timedelta(days=2),
    )
    monkeypatch.setenv("UNAI_ORACLE_TRUSTED_PUBLISHER_KEYS", "{}")
    monkeypatch.setenv("UNAI_ORACLE_PUBLISHER_TRUST_ROOTS", json.dumps(roots))
    monkeypatch.setenv("UNAI_ORACLE_PUBLISHER_TRUST_BUNDLE", bundle)
    monkeypatch.setenv("UNAI_ORACLE_PUBLISHER_TRUST_MIN_SEQUENCE", "7")

    from unai_engine.config import _oracle_publisher_keys_from_env

    keys = _oracle_publisher_keys_from_env()
    assert keys["acme-specs-2026"]["_trust_bundle"]["sequence"] == 7

    monkeypatch.setenv(
        "UNAI_ORACLE_TRUSTED_PUBLISHER_KEYS",
        json.dumps({
            "another-key": {
                "public_key_b64": _b64(b"x" * 32),
                "issuer": "Other Publisher",
                "allowed_domains": ["other.example"],
            }
        }),
    )
    with pytest.raises(ValueError, match="not both"):
        _oracle_publisher_keys_from_env()


def test_config_rejects_invalid_minimum_sequence(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    bundle, roots, _ = _make_bundle(now=now)
    monkeypatch.setenv("UNAI_ORACLE_TRUSTED_PUBLISHER_KEYS", "{}")
    monkeypatch.setenv("UNAI_ORACLE_PUBLISHER_TRUST_ROOTS", json.dumps(roots))
    monkeypatch.setenv("UNAI_ORACLE_PUBLISHER_TRUST_BUNDLE", bundle)
    monkeypatch.setenv("UNAI_ORACLE_PUBLISHER_TRUST_MIN_SEQUENCE", "-1")

    from unai_engine.config import _oracle_publisher_keys_from_env

    with pytest.raises(ValueError, match="non-negative integer"):
        _oracle_publisher_keys_from_env()

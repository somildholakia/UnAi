from __future__ import annotations

import base64
import hashlib
import sqlite3

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from unai_engine.verification.transparency import (
    SignedTreeHead,
    TransparencyCheckpointStore,
    leaf_hash,
    node_hash,
    sign_tree_head_message,
    verify_consistency_proof,
    verify_inclusion_proof,
    verify_publisher_manifest_inclusion,
    verify_signed_tree_head,
)


def _root(leaves: list[bytes]) -> bytes:
    if len(leaves) == 1:
        return leaf_hash(leaves[0])
    split = 1 << ((len(leaves) - 1).bit_length() - 1)
    return node_hash(_root(leaves[:split]), _root(leaves[split:]))


def _inclusion_path(leaves: list[bytes], index: int) -> list[str]:
    if len(leaves) == 1:
        return []
    split = 1 << ((len(leaves) - 1).bit_length() - 1)
    if index < split:
        return _inclusion_path(leaves[:split], index) + [_root(leaves[split:]).hex()]
    return _inclusion_path(leaves[split:], index - split) + [_root(leaves[:split]).hex()]


def _keypair() -> tuple[Ed25519PrivateKey, dict[str, str]]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private, {"log-key-1": base64.b64encode(public).decode("ascii")}


def _head(private: Ed25519PrivateKey, *, size: int, root: bytes, timestamp: int) -> SignedTreeHead:
    message = sign_tree_head_message(
        log_id="test-log", key_id="log-key-1", timestamp=timestamp,
        tree_size=size, root_hash=root.hex(),
    )
    return SignedTreeHead(
        log_id="test-log", key_id="log-key-1", timestamp=timestamp,
        tree_size=size, root_hash=root.hex(),
        signature_b64=base64.b64encode(private.sign(message)).decode("ascii"),
    )


def test_inclusion_proofs_cover_left_and_right_subtrees() -> None:
    leaves = [b"one", b"two", b"three", b"four", b"five"]
    root = _root(leaves).hex()
    for index, leaf in enumerate(leaves):
        assert verify_inclusion_proof(
            leaf=leaf, leaf_index=index, tree_size=len(leaves),
            proof=_inclusion_path(leaves, index), root_hash=root,
        )


def test_inclusion_proof_rejects_wrong_leaf_index_and_tampering() -> None:
    leaves = [b"alpha", b"beta", b"gamma"]
    proof = _inclusion_path(leaves, 1)
    assert not verify_inclusion_proof(
        leaf=b"not-beta", leaf_index=1, tree_size=3, proof=proof,
        root_hash=_root(leaves).hex(),
    )
    assert not verify_inclusion_proof(
        leaf=leaves[1], leaf_index=3, tree_size=3, proof=proof,
        root_hash=_root(leaves).hex(),
    )


def test_consistency_proofs_verify_growth_and_reject_tampering() -> None:
    first = [b"one"]
    second = [b"one", b"two"]
    third = [b"one", b"two", b"three"]
    assert verify_consistency_proof(
        old_size=1, new_size=2, old_root_hash=_root(first).hex(),
        new_root_hash=_root(second).hex(), proof=[leaf_hash(b"two").hex()],
    )
    assert verify_consistency_proof(
        old_size=2, new_size=3, old_root_hash=_root(second).hex(),
        new_root_hash=_root(third).hex(), proof=[leaf_hash(b"three").hex()],
    )
    assert not verify_consistency_proof(
        old_size=2, new_size=3, old_root_hash=_root(second).hex(),
        new_root_hash=_root(third).hex(), proof=[leaf_hash(b"tampered").hex()],
    )
    assert verify_consistency_proof(
        old_size=3, new_size=3, old_root_hash=_root(third).hex(),
        new_root_hash=_root(third).hex(), proof=[],
    )


def test_publisher_manifest_is_bound_to_signed_log_inclusion() -> None:
    private, trusted = _keypair()
    manifest = {
        "schema": "unai.spec-publisher-manifest.v1",
        "issuer": "example-specs",
        "artifact_uri": "https://specs.example.test/api.md",
        "artifact_revision": "rev-7",
        "artifact_sha256": hashlib.sha256(b"spec bytes").hexdigest(),
    }
    leaf = __import__(
        "unai_engine.verification.transparency", fromlist=["publisher_manifest_leaf"]
    ).publisher_manifest_leaf(manifest)
    leaves = [b"prior-entry", leaf, b"later-entry"]
    head = _head(private, size=3, root=_root(leaves), timestamp=101)
    assert verify_publisher_manifest_inclusion(
        statement=manifest, head=head, leaf_index=1,
        inclusion_proof=_inclusion_path(leaves, 1), trusted_log_keys=trusted,
    )
    changed = {**manifest, "artifact_sha256": "00" * 32}
    assert not verify_publisher_manifest_inclusion(
        statement=changed, head=head, leaf_index=1,
        inclusion_proof=_inclusion_path(leaves, 1), trusted_log_keys=trusted,
    )


def test_signed_tree_head_requires_pinned_ed25519_key() -> None:
    private, trusted = _keypair()
    head = _head(private, size=1, root=leaf_hash(b"entry"), timestamp=100)
    assert verify_signed_tree_head(head, trusted)
    assert not verify_signed_tree_head(head, {})
    forged = SignedTreeHead(**{**head.__dict__, "root_hash": "00" * 32})
    assert not verify_signed_tree_head(forged, trusted)


def test_checkpoint_store_enforces_consistency_and_monotonicity(tmp_path) -> None:
    private, trusted = _keypair()
    store = TransparencyCheckpointStore(tmp_path / "transparency.sqlite3")
    leaves = [b"one", b"two", b"three"]
    first = _head(private, size=2, root=_root(leaves[:2]), timestamp=100)
    assert store.accept(first, trusted)
    assert store.latest("test-log")["tree_size"] == 2

    second = _head(private, size=3, root=_root(leaves), timestamp=110)
    assert store.accept(second, trusted, [leaf_hash(leaves[2]).hex()])
    assert store.latest("test-log")["root_hash"] == _root(leaves).hex()
    assert not store.accept(second, trusted, [])
    with pytest.raises(ValueError, match="rollback"):
        store.accept(first, trusted)

    with pytest.raises(ValueError, match="consistency"):
        store.accept(
            _head(private, size=4, root=hashlib.sha256(b"wrong").digest(), timestamp=120),
            trusted, [],
        )


def test_checkpoint_observations_are_append_only(tmp_path) -> None:
    private, trusted = _keypair()
    path = tmp_path / "append-only.sqlite3"
    store = TransparencyCheckpointStore(path)
    head = _head(private, size=1, root=leaf_hash(b"one"), timestamp=100)
    store.accept(head, trusted)
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE transparency_observations SET timestamp = 1 WHERE log_id = ?",
                ("test-log",),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "DELETE FROM transparency_observations WHERE log_id = ?",
                ("test-log",),
            )

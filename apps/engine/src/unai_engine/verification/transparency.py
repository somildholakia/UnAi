"""Offline verification primitives for append-only publisher transparency logs.

The module verifies signed tree heads and RFC 6962-style Merkle proofs. It does
not discover logs, fetch proofs, establish publisher identity, or contact a
transparency service; log keys must be pinned by the operator.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

TREE_HEAD_SCHEMA = "unai.transparency-tree-head.v1"


def _canonical_json(value: dict[str, object]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _decode_hash(value: str) -> bytes:
    try:
        raw = bytes.fromhex(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Merkle hashes must be 64-character hexadecimal SHA-256 values.") from exc
    if len(raw) != 32:
        raise ValueError("Merkle hashes must be 32 bytes.")
    return raw


def _decode_b64(value: str, expected_length: int) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except (TypeError, binascii.Error) as exc:
        raise ValueError("Invalid base64 transparency material.") from exc
    if len(raw) != expected_length:
        raise ValueError("Transparency material has an invalid length.")
    return raw


def leaf_hash(leaf: bytes) -> bytes:
    """RFC 6962 leaf hash: SHA256(0x00 || leaf)."""
    return hashlib.sha256(b"\\x00" + leaf).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    """RFC 6962 internal-node hash: SHA256(0x01 || left || right)."""
    if len(left) != 32 or len(right) != 32:
        raise ValueError("Merkle child hashes must be 32 bytes.")
    return hashlib.sha256(b"\\x01" + left + right).digest()


def publisher_manifest_leaf(statement: dict[str, object]) -> bytes:
    """Return the exact canonical publisher-manifest bytes committed to a log."""
    return _canonical_json(statement)


def verify_publisher_manifest_inclusion(
    *, statement: dict[str, object], head: "SignedTreeHead", leaf_index: int,
    inclusion_proof: Sequence[str], trusted_log_keys: Mapping[str, str],
) -> bool:
    """Verify that a canonical publisher manifest is committed by a trusted tree head."""
    return verify_signed_tree_head(head, trusted_log_keys) and verify_inclusion_proof(
        leaf=publisher_manifest_leaf(statement),
        leaf_index=leaf_index,
        tree_size=head.tree_size,
        proof=inclusion_proof,
        root_hash=head.root_hash,
    )

def build_transparency_witness_statement(head: "SignedTreeHead") -> dict[str, object]:
    """Canonical statement independent witnesses sign for one exact tree head."""
    return {
        "schema": "unai.transparency-witness.v1",
        "log_id": head.log_id,
        "tree_head_sha256": hashlib.sha256(head.signed_message()).hexdigest(),
        "tree_size": head.tree_size,
        "timestamp": head.timestamp,
        "root_hash": head.root_hash.lower(),
    }


@dataclass(frozen=True)
class SignedTreeHead:
    log_id: str
    key_id: str
    timestamp: int
    tree_size: int
    root_hash: str
    signature_b64: str

    def signed_message(self) -> bytes:
        if not self.log_id or not self.key_id or len(self.log_id) > 256 or len(self.key_id) > 128:
            raise ValueError("Transparency log and key identifiers must be non-empty and bounded.")
        if not isinstance(self.timestamp, int) or isinstance(self.timestamp, bool) or self.timestamp < 0:
            raise ValueError("Tree-head timestamp must be a non-negative integer.")
        if not isinstance(self.tree_size, int) or isinstance(self.tree_size, bool) or self.tree_size < 1:
            raise ValueError("Tree size must be a positive integer.")
        _decode_hash(self.root_hash)
        return _canonical_json({
            "schema": TREE_HEAD_SCHEMA,
            "log_id": self.log_id,
            "key_id": self.key_id,
            "timestamp": self.timestamp,
            "tree_size": self.tree_size,
            "root_hash": self.root_hash.lower(),
        })


def sign_tree_head_message(
    *, log_id: str, key_id: str, timestamp: int, tree_size: int, root_hash: str
) -> bytes:
    """Build the canonical bytes a log operator signs with its Ed25519 private key."""
    return SignedTreeHead(
        log_id=log_id,
        key_id=key_id,
        timestamp=timestamp,
        tree_size=tree_size,
        root_hash=root_hash,
        signature_b64="",
    ).signed_message()


def verify_signed_tree_head(
    head: SignedTreeHead, trusted_log_keys: Mapping[str, str]
) -> bool:
    """Verify a tree head against an operator-pinned key ID -> base64 public-key map."""
    try:
        public_key_b64 = trusted_log_keys.get(head.key_id)
        if public_key_b64 is None:
            return False
        public_key = _decode_b64(public_key_b64, 32)
        signature = _decode_b64(head.signature_b64, 64)
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, head.signed_message())
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def verify_inclusion_proof(
    *, leaf: bytes, leaf_index: int, tree_size: int, proof: Sequence[str], root_hash: str
) -> bool:
    """Verify a leaf inclusion path using the RFC 6962 Merkle tree algorithm."""
    if isinstance(leaf_index, bool) or isinstance(tree_size, bool):
        return False
    if tree_size < 1 or leaf_index < 0 or leaf_index >= tree_size:
        return False
    try:
        root = _decode_hash(root_hash)
        path = [_decode_hash(item) for item in proof]
        fn = leaf_index
        sn = tree_size - 1
        value = leaf_hash(leaf)
        for sibling in path:
            if sn == 0:
                return False
            if (fn & 1) == 1 or fn == sn:
                value = node_hash(sibling, value)
                while (fn & 1) == 0 and fn != 0:
                    fn >>= 1
                    sn >>= 1
            else:
                value = node_hash(value, sibling)
            fn >>= 1
            sn >>= 1
        return sn == 0 and value == root
    except (ValueError, TypeError):
        return False


def verify_consistency_proof(
    *, old_size: int, new_size: int, old_root_hash: str, new_root_hash: str,
    proof: Sequence[str],
) -> bool:
    """Verify that a larger tree extends a previously accepted Merkle tree."""
    if any(isinstance(value, bool) for value in (old_size, new_size)):
        return False
    if old_size < 0 or new_size < old_size or new_size < 1:
        return False
    try:
        old_root = _decode_hash(old_root_hash)
        new_root = _decode_hash(new_root_hash)
        path = [_decode_hash(item) for item in proof]
    except (ValueError, TypeError):
        return False
    if old_size == 0:
        return not path
    if old_size == new_size:
        return not path and old_root == new_root

    fn = old_size - 1
    sn = new_size - 1
    while fn & 1:
        fn >>= 1
        sn >>= 1

    if fn == 0:
        first = old_root
        second = old_root
        proof_offset = 0
    else:
        if not path:
            return False
        first = path[0]
        second = path[0]
        proof_offset = 1

    for sibling in path[proof_offset:]:
        if sn == 0:
            return False
        if (fn & 1) == 1 or fn == sn:
            first = node_hash(sibling, first)
            second = node_hash(sibling, second)
            while (fn & 1) == 0 and fn != 0:
                fn >>= 1
                sn >>= 1
        else:
            second = node_hash(second, sibling)
        fn >>= 1
        sn >>= 1
    return first == old_root and second == new_root and sn == 0


class TransparencyCheckpointStore:
    """SQLite append-only observations with monotonic per-log checkpoint checks."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS transparency_observations (
                    log_id TEXT NOT NULL,
                    tree_size INTEGER NOT NULL,
                    timestamp INTEGER NOT NULL,
                    root_hash TEXT NOT NULL,
                    key_id TEXT NOT NULL,
                    signature_b64 TEXT NOT NULL,
                    PRIMARY KEY (log_id, tree_size)
                )"""
            )
            connection.execute(
                """CREATE TRIGGER IF NOT EXISTS transparency_observations_no_update
                BEFORE UPDATE ON transparency_observations
                BEGIN SELECT RAISE(ABORT, 'transparency observations are append-only'); END"""
            )
            connection.execute(
                """CREATE TRIGGER IF NOT EXISTS transparency_observations_no_delete
                BEFORE DELETE ON transparency_observations
                BEGIN SELECT RAISE(ABORT, 'transparency observations are append-only'); END"""
            )

    def latest(self, log_id: str) -> dict[str, object] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT log_id, tree_size, timestamp, root_hash, key_id
                FROM transparency_observations WHERE log_id = ?
                ORDER BY tree_size DESC LIMIT 1""",
                (log_id,),
            ).fetchone()
        return dict(row) if row else None

    def accept(
        self, head: SignedTreeHead, trusted_log_keys: Mapping[str, str],
        consistency_proof: Sequence[str] = (),
    ) -> bool:
        """Verify and append a checkpoint; reject rollback, equivocation and broken growth."""
        if not verify_signed_tree_head(head, trusted_log_keys):
            raise ValueError("Tree-head signature is invalid or its key is not trusted.")
        root = _decode_hash(head.root_hash).hex()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                previous = connection.execute(
                    """SELECT tree_size, timestamp, root_hash FROM transparency_observations
                    WHERE log_id = ? ORDER BY tree_size DESC LIMIT 1""",
                    (head.log_id,),
                ).fetchone()
                if previous:
                    old_size = int(previous["tree_size"])
                    old_timestamp = int(previous["timestamp"])
                    old_root = str(previous["root_hash"])
                    if head.tree_size < old_size:
                        raise ValueError("Tree-head rollback detected.")
                    if head.timestamp < old_timestamp:
                        raise ValueError("Tree-head timestamp rollback detected.")
                    if head.tree_size == old_size:
                        if root != old_root or consistency_proof:
                            raise ValueError("Tree-head equivocation or unexpected proof detected.")
                        connection.execute("COMMIT")
                        return False
                    if not verify_consistency_proof(
                        old_size=old_size,
                        new_size=head.tree_size,
                        old_root_hash=old_root,
                        new_root_hash=root,
                        proof=consistency_proof,
                    ):
                        raise ValueError("Tree-head consistency proof is invalid or missing.")
                elif consistency_proof:
                    raise ValueError("Initial tree-head checkpoint must not include a consistency proof.")

                connection.execute(
                    """INSERT INTO transparency_observations
                    (log_id, tree_size, timestamp, root_hash, key_id, signature_b64)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (head.log_id, head.tree_size, head.timestamp, root, head.key_id, head.signature_b64),
                )
                connection.execute("COMMIT")
                return True
            except Exception:
                connection.execute("ROLLBACK")
                raise

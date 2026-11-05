from __future__ import annotations

import ast
import base64
import hashlib
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from unai_engine.cognition.task_compiler import compile_task
from unai_engine.models import (
    EvidenceRecord,
    GroundedOracleAttestation,
    GroundedOraclePublisherAttestation,
    GroundedOracleTransparencyProof,
    GroundedOracleTransparencyTreeHead,
    GroundedOracleTransparencyWitness,
    GroundedOracleCase,
    GroundedPropertyOracle,
    GroundedTestApprovalRequest,
    TaskCompileRequest,
    TaskTestRunResponse,
)
from unai_engine.runtime.patches import PatchManager
from unai_engine.runtime.sandbox import ExecutionResult
from unai_engine.runtime.test_runner import IsolatedPythonTestRunner, WorkspaceStagingError
from unai_engine.verification.attestations import (
    build_oracle_attestation_statement,
    build_publisher_artifact_statement,
    canonical_attestation_message,
)
from unai_engine.verification.grounded import (
    GroundedTestVerifier,
    GroundedVerificationError,
    build_proof_bundle,
)
from unai_engine.verification.ledger import EvidenceLedger
from unai_engine.verification.probes import BoundaryProbeGenerator
from unai_engine.verification.transparency import (
    SignedTreeHead,
    TransparencyCheckpointStore,
    build_transparency_witness_statement,
    leaf_hash,
    node_hash,
    publisher_manifest_leaf,
    sign_tree_head_message,
)


class FakeGroundedRunner:
    def __init__(self, ledger: EvidenceLedger, status="passed", stdout="", stderr="") -> None:
        self.ledger = ledger
        self.status = status
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[dict] = []
        self.response_queue: list[tuple[str, str, str]] = []
        self.generated_responder = None

    def run_generated_test(
        self,
        task_id: str,
        workspace_root: Path,
        generated_test_path: str,
        generated_test_content: str,
        evidence_metadata: dict,
        *,
        evidence_source: str = "grounded-oracle-sandbox",
    ) -> TaskTestRunResponse:
        if self.response_queue:
            status, stdout, stderr = self.response_queue.pop(0)
        elif self.generated_responder is not None:
            status, stdout, stderr = self.generated_responder(
                generated_test_content, evidence_source, evidence_metadata
            )
        else:
            status, stdout, stderr = self.status, self.stdout, self.stderr
        self.calls.append(
            {
                "task_id": task_id,
                "path": generated_test_path,
                "content": generated_test_content,
                "metadata": evidence_metadata,
            }
        )
        now = datetime.now(timezone.utc).isoformat()
        event = EvidenceRecord(
            evidence_id=f"run-{len(self.calls)}",
            task_id=task_id,
            kind="execution",
            source=evidence_source,
            claim="Grounded pytest run completed.",
            passed=True if status == "passed" else False if status == "failed" else None,
            artifact_ref=f"task:{task_id}/grounded-oracle-tests",
            metadata={
                "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
                "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
                "exit_code": 0 if status == "passed" else 1 if status == "failed" else None,
                "sandboxed": status != "unavailable",
                **evidence_metadata,
            },
            created_at=now,
        )
        self.ledger.append_evidence(event)
        return TaskTestRunResponse(
            task_id=task_id,
            status=status,
            exit_code=event.metadata["exit_code"],
            sandboxed=status != "unavailable",
            stdout_preview=stdout,
            stderr_preview=stderr,
            evidence=event,
        )


def _setup(tmp_path: Path, permissions=None):
    workspace_root = tmp_path / "workspaces"
    project = workspace_root / "demo"
    project.mkdir(parents=True)
    source = (
        "def clamp(value: int):\n"
        "    if value < 10:\n"
        "        return 0\n"
        "    return value\n"
    )
    target = project / "service.py"
    target.write_text(source, encoding="utf-8")

    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    contract = compile_task(
        TaskCompileRequest(
            request="Verify the clamp lower boundary against the contract",
            permissions=permissions or ["workspace_read", "run_tests"],
        )
    )
    precondition = EvidenceRecord(
        evidence_id="inspection",
        task_id=contract.task_id,
        kind="precondition",
        source="test",
        claim="Workspace was inspected with the caller's approvals.",
        passed=True,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    ledger.record_inspection(contract, "inspection_complete", [precondition], workspace="demo")
    patch_manager = PatchManager(ledger)
    snapshot = patch_manager.read_file(contract.task_id, "service.py", workspace_root)
    generator = BoundaryProbeGenerator(ledger)
    probes = generator.generate(
        contract.task_id, "service.py", "clamp", snapshot.content, snapshot.sha256, limit=8
    )
    runner = FakeGroundedRunner(ledger)
    verifier = GroundedTestVerifier(ledger, patch_manager, runner)
    return contract.task_id, workspace_root, target, source, ledger, snapshot, probes, runner, verifier


def _request(
    snapshot,
    *,
    candidate_id="probe-1",
    expected=0,
    source_hash=None,
    oracle_artifact_path=None,
    oracle_artifact_sha256=None,
):
    return GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="clamp",
        source_sha256=source_hash or snapshot.sha256,
        cases=[
            GroundedOracleCase(
                case_id="lower-edge",
                arguments={"value": 9},
                expected_output=expected,
                oracle_kind="specification",
                oracle_reference="product-spec.md#clamp-lower-bound",
                oracle_artifact_path=oracle_artifact_path,
                oracle_artifact_sha256=oracle_artifact_sha256,
                probe_candidate_id=candidate_id,
            )
        ],
    )




def _signed_specification_request(
    task_id: str,
    snapshot,
    *,
    spec_content: str,
    expected_output=0,
    private_key=None,
):
    private_key = private_key or Ed25519PrivateKey.generate()
    issued_at = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    arguments = {"value": 9}
    artifact_path = "product-spec.md"
    artifact_sha256 = hashlib.sha256(spec_content.encode("utf-8")).hexdigest()
    canonical_json = lambda value: json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    statement = build_oracle_attestation_statement(
        key_id="reviewer-release",
        issued_at=issued_at,
        task_id=task_id,
        workspace="demo",
        file_path="service.py",
        symbol_name="clamp",
        source_sha256=snapshot.sha256,
        case_id="signed-lower-edge",
        oracle_kind="specification",
        oracle_reference="product-spec.md#clamp-lower-bound",
        oracle_artifact_path=artifact_path,
        oracle_artifact_sha256=artifact_sha256,
        arguments_sha256=hashlib.sha256(canonical_json(arguments)).hexdigest(),
        expected_output_sha256=hashlib.sha256(canonical_json(expected_output)).hexdigest(),
        oracle_property_sha256=None,
    )
    signature_b64 = base64.b64encode(
        private_key.sign(canonical_attestation_message(statement))
    ).decode("ascii")
    public_key_b64 = base64.b64encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="clamp",
        source_sha256=snapshot.sha256,
        cases=[
            GroundedOracleCase(
                case_id="signed-lower-edge",
                arguments=arguments,
                expected_output=expected_output,
                oracle_kind="specification",
                oracle_reference="product-spec.md#clamp-lower-bound",
                oracle_artifact_path=artifact_path,
                oracle_artifact_sha256=artifact_sha256,
                oracle_attestation=GroundedOracleAttestation(
                    key_id="reviewer-release",
                    issued_at=issued_at,
                    signature_b64=signature_b64,
                ),
            )
        ],
    )
    return request, public_key_b64, signature_b64


def _publisher_signed_request(task_id: str, snapshot, *, spec_content: str):
    request, _reviewer_public, _reviewer_signature = _signed_specification_request(
        task_id, snapshot, spec_content=spec_content
    )
    request.cases[0].oracle_attestation = None
    private_key = Ed25519PrivateKey.generate()
    issued_at = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    artifact_uri = "https://specs.acme.example/releases/2026-11/product-spec.md"
    artifact_revision = "release-2026.11"
    artifact_sha256 = hashlib.sha256(spec_content.encode("utf-8")).hexdigest()
    statement = build_publisher_artifact_statement(
        key_id="acme-specs-2026",
        issuer="Acme Specifications",
        issued_at=issued_at,
        artifact_uri=artifact_uri,
        artifact_revision=artifact_revision,
        artifact_sha256=artifact_sha256,
    )
    signature_b64 = base64.b64encode(
        private_key.sign(canonical_attestation_message(statement))
    ).decode("ascii")
    public_key_b64 = base64.b64encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    request.cases[0].oracle_publisher_attestation = GroundedOraclePublisherAttestation(
        key_id="acme-specs-2026",
        issuer="Acme Specifications",
        artifact_uri=artifact_uri,
        artifact_revision=artifact_revision,
        issued_at=issued_at,
        signature_b64=signature_b64,
    )
    trust_entry = {
        "public_key_b64": public_key_b64,
        "issuer": "Acme Specifications",
        "allowed_domains": ["specs.acme.example"],
    }
    return request, trust_entry, signature_b64


def test_trusted_publisher_signature_authenticates_exact_specification_artifact(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    spec_content = "# Clamp requirement\\nValues below 10 return zero.\\n"
    (workspace_root / "demo" / "product-spec.md").write_text(spec_content, encoding="utf-8")
    request, trust_entry, signature_b64 = _publisher_signed_request(
        task_id, snapshot, spec_content=spec_content
    )
    verifier.trusted_oracle_publisher_keys = {"acme-specs-2026": trust_entry}

    result = verifier.run(task_id, request, workspace_root)

    assert result.status == "passed"
    oracle_metadata = result.oracle_evidence[0].metadata
    assert oracle_metadata["oracle_artifact_integrity"] == "trusted-ed25519-publisher-signature"
    publisher = oracle_metadata["oracle_publisher_attestation"]
    assert publisher["verified"] is True
    assert publisher["algorithm"] == "Ed25519"
    assert publisher["key_id"] == "acme-specs-2026"
    assert publisher["issuer"] == "Acme Specifications"
    assert publisher["artifact_revision"] == "release-2026.11"
    assert publisher["artifact_sha256"] == hashlib.sha256(spec_content.encode("utf-8")).hexdigest()
    assert oracle_metadata["oracle_publisher_attestation_signature_b64"] == signature_b64
    assert signature_b64 not in runner.calls[0]["content"]
    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    bundle = build_proof_bundle(saved)
    assert not any("lack a valid signature from an operator-configured trusted publisher key" in risk for risk in bundle.unresolved_risks)
    assert any("lack a valid signature from an operator-configured trusted reviewer key" in risk for risk in bundle.unresolved_risks)



def test_signed_trust_bundle_provenance_is_preserved_in_grounded_evidence(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    spec_content = "# Clamp requirement\\nValues below 10 return zero.\\n"
    (workspace_root / "demo" / "product-spec.md").write_text(spec_content, encoding="utf-8")
    request, trust_entry, _signature = _publisher_signed_request(
        task_id, snapshot, spec_content=spec_content
    )
    trust_entry["_trust_bundle"] = {
        "schema": "unai.publisher-trust-bundle.v1",
        "sequence": 42,
        "root_key_id": "offline-root-2026",
        "payload_sha256": "a" * 64,
        "publisher_count": 1,
        "minimum_sequence": 42,
        "rollback_floor_enforced": True,
    }
    verifier.trusted_oracle_publisher_keys = {"acme-specs-2026": trust_entry}

    result = verifier.run(task_id, request, workspace_root)

    assert result.status == "passed"
    provenance = result.oracle_evidence[0].metadata["oracle_publisher_attestation"]["trust_bundle"]
    assert provenance["schema"] == "unai.publisher-trust-bundle.v1"
    assert provenance["sequence"] == 42
    assert provenance["root_key_id"] == "offline-root-2026"
    assert provenance["rollback_floor_enforced"] is True


def test_publisher_attestation_rejects_untrusted_issuer_or_domain_before_execution(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    spec_content = "# Clamp requirement\\nValues below 10 return zero.\\n"
    (workspace_root / "demo" / "product-spec.md").write_text(spec_content, encoding="utf-8")
    request, trust_entry, _signature = _publisher_signed_request(
        task_id, snapshot, spec_content=spec_content
    )
    trust_entry["issuer"] = "Different Publisher"
    verifier.trusted_oracle_publisher_keys = {"acme-specs-2026": trust_entry}

    with pytest.raises(GroundedVerificationError, match="issuer does not match") as raised:
        verifier.run(task_id, request, workspace_root)

    assert raised.value.status_code == 403
    assert runner.calls == []
    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    assert not any(item.kind == "oracle" for item in saved.evidence)


def test_publisher_attestation_rejects_artifact_uri_outside_domain_allowlist(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    spec_content = "# Clamp requirement\\nValues below 10 return zero.\\n"
    (workspace_root / "demo" / "product-spec.md").write_text(spec_content, encoding="utf-8")
    request, trust_entry, _signature = _publisher_signed_request(
        task_id, snapshot, spec_content=spec_content
    )
    trust_entry["allowed_domains"] = ["other.example"]
    verifier.trusted_oracle_publisher_keys = {"acme-specs-2026": trust_entry}

    with pytest.raises(GroundedVerificationError, match="not allowed") as raised:
        verifier.run(task_id, request, workspace_root)

    assert raised.value.status_code == 403
    assert runner.calls == []


def test_publisher_signature_is_bound_to_revision_and_artifact_bytes(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    spec_content = "# Clamp requirement\\nValues below 10 return zero.\\n"
    (workspace_root / "demo" / "product-spec.md").write_text(spec_content, encoding="utf-8")
    request, trust_entry, _signature = _publisher_signed_request(
        task_id, snapshot, spec_content=spec_content
    )
    verifier.trusted_oracle_publisher_keys = {"acme-specs-2026": trust_entry}
    request.cases[0].oracle_publisher_attestation.artifact_revision = "release-2026.12"

    with pytest.raises(GroundedVerificationError, match="Publisher signature .* is invalid") as raised:
        verifier.run(task_id, request, workspace_root)

    assert raised.value.status_code == 403
    assert runner.calls == []
    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    assert not any(item.kind == "oracle" for item in saved.evidence)


@pytest.mark.parametrize(
    "artifact_uri,error_match",
    [
        ("http://specs.acme.example/product-spec.md", "public HTTPS URL"),
        ("https://127.0.0.1/product-spec.md", "public DNS hostname"),
        ("https://user:pass@specs.acme.example/product-spec.md", "public HTTPS URL"),
    ],
)
def test_publisher_attestation_rejects_non_public_or_unsafe_urls(artifact_uri, error_match) -> None:
    signature_b64 = base64.b64encode(b"x" * 64).decode("ascii")
    with pytest.raises(ValidationError, match=error_match):
        GroundedOraclePublisherAttestation(
            key_id="acme-specs-2026",
            issuer="Acme Specifications",
            artifact_uri=artifact_uri,
            artifact_revision="release-2026.11",
            issued_at=datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc),
            signature_b64=signature_b64,
        )


def test_removed_publisher_key_id_is_revoked_before_execution(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    spec_content = "# Clamp requirement\\nValues below 10 return zero.\\n"
    (workspace_root / "demo" / "product-spec.md").write_text(spec_content, encoding="utf-8")
    request, _trust_entry, _signature = _publisher_signed_request(
        task_id, snapshot, spec_content=spec_content
    )
    verifier.trusted_oracle_publisher_keys = {}

    with pytest.raises(GroundedVerificationError, match="not in the configured trusted publisher keyring"):
        verifier.run(task_id, request, workspace_root)

    assert runner.calls == []
    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    assert not any(item.kind == "oracle" for item in saved.evidence)


@pytest.mark.parametrize(
    "lifecycle,error_match",
    [
        ({"revoked": True}, "has been revoked"),
        ({"not_before": datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)}, "not yet valid"),
        ({"not_after": datetime(2026, 11, 1, 8, 59, tzinfo=timezone.utc)}, "outside its validity window"),
    ],
)
def test_publisher_key_lifecycle_enforces_revocation_and_issuance_windows(
    tmp_path: Path, lifecycle: dict, error_match: str
) -> None:
    task_id, workspace_root, _target, _source, ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    spec_content = "# Clamp requirement\\nValues below 10 return zero.\\n"
    (workspace_root / "demo" / "product-spec.md").write_text(spec_content, encoding="utf-8")
    request, trust_entry, _signature = _publisher_signed_request(
        task_id, snapshot, spec_content=spec_content
    )
    trust_entry.update(lifecycle)
    verifier.trusted_oracle_publisher_keys = {"acme-specs-2026": trust_entry}

    with pytest.raises(GroundedVerificationError, match=error_match) as raised:
        verifier.run(task_id, request, workspace_root)

    assert raised.value.status_code == 403
    assert runner.calls == []
    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    assert not any(item.kind == "oracle" for item in saved.evidence)


def test_publisher_keyring_parses_rotation_windows_and_rejects_invalid_windows(monkeypatch) -> None:
    from unai_engine.config import _oracle_publisher_keys_from_env

    public_key_b64 = base64.b64encode(b"x" * 32).decode("ascii")
    monkeypatch.setenv(
        "UNAI_ORACLE_TRUSTED_PUBLISHER_KEYS",
        json.dumps({
            "acme-specs-2025": {
                "public_key_b64": public_key_b64,
                "issuer": "Acme Specifications",
                "allowed_domains": ["specs.acme.example"],
                "not_before": "2025-01-01T00:00:00Z",
                "not_after": "2025-12-31T23:59:59Z",
            },
            "acme-specs-2026": {
                "public_key_b64": public_key_b64,
                "issuer": "Acme Specifications",
                "allowed_domains": ["specs.acme.example"],
                "not_before": "2026-01-01T00:00:00+00:00",
                "revoked": False,
            },
        }),
    )

    keys = _oracle_publisher_keys_from_env()

    assert keys["acme-specs-2025"]["not_before"] == datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert keys["acme-specs-2025"]["not_after"] == datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    assert keys["acme-specs-2026"]["not_after"] is None
    assert keys["acme-specs-2026"]["revoked"] is False
    assert keys["acme-specs-2025"]["issuer"] == keys["acme-specs-2026"]["issuer"]

    monkeypatch.setenv(
        "UNAI_ORACLE_TRUSTED_PUBLISHER_KEYS",
        json.dumps({
            "acme-specs-2026": {
                "public_key_b64": public_key_b64,
                "issuer": "Acme Specifications",
                "allowed_domains": ["specs.acme.example"],
                "not_before": "2026-12-31T00:00:00Z",
                "not_after": "2026-01-01T00:00:00Z",
            }
        }),
    )
    with pytest.raises(ValueError, match="invalid issuance window"):
        _oracle_publisher_keys_from_env()


def test_publisher_trust_config_requires_valid_keys_and_exact_domains(monkeypatch) -> None:
    from unai_engine.config import _oracle_publisher_keys_from_env

    public_key_b64 = base64.b64encode(b"x" * 32).decode("ascii")
    monkeypatch.setenv(
        "UNAI_ORACLE_TRUSTED_PUBLISHER_KEYS",
        json.dumps({
            "acme-specs-2026": {
                "public_key_b64": public_key_b64,
                "issuer": "Acme Specifications",
                "allowed_domains": ["specs.acme.example"],
            }
        }),
    )
    trust = _oracle_publisher_keys_from_env()
    assert trust["acme-specs-2026"]["issuer"] == "Acme Specifications"
    assert trust["acme-specs-2026"]["allowed_domains"] == ["specs.acme.example"]

    monkeypatch.setenv(
        "UNAI_ORACLE_TRUSTED_PUBLISHER_KEYS",
        json.dumps({
            "acme-specs-2026": {
                "public_key_b64": public_key_b64,
                "issuer": "Acme Specifications",
                "allowed_domains": ["https://specs.acme.example"],
            }
        }),
    )
    with pytest.raises(ValueError, match="invalid exact hostname"):
        _oracle_publisher_keys_from_env()


def test_trusted_ed25519_attestation_authenticates_exact_specification_case(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    spec_content = "# Clamp requirement\\nValues below 10 return zero.\\n"
    (workspace_root / "demo" / "product-spec.md").write_text(spec_content, encoding="utf-8")
    request, public_key_b64, signature_b64 = _signed_specification_request(
        task_id, snapshot, spec_content=spec_content
    )
    verifier.trusted_oracle_public_keys = {"reviewer-release": public_key_b64}

    result = verifier.run(task_id, request, workspace_root)

    assert result.status == "passed"
    oracle_metadata = result.oracle_evidence[0].metadata
    assert oracle_metadata["oracle_artifact_integrity"] == "trusted-ed25519-review-attestation"
    attestation = oracle_metadata["oracle_attestation"]
    assert attestation["verified"] is True
    assert attestation["algorithm"] == "Ed25519"
    assert attestation["key_id"] == "reviewer-release"
    assert oracle_metadata["oracle_attestation_signature_b64"] == signature_b64
    assert signature_b64 not in runner.calls[0]["content"]
    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    bundle = build_proof_bundle(saved)
    assert not any(
        "lack a valid signature from an operator-configured trusted reviewer key" in risk
        for risk in bundle.unresolved_risks
    )
    assert any(
        "lack a valid signature from an operator-configured trusted publisher key" in risk
        for risk in bundle.unresolved_risks
    )
    assert any("do not prove the underlying specification" in risk for risk in bundle.unresolved_risks)


def test_tampered_expected_output_invalidates_reviewer_signature_before_execution(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    spec_content = "# Clamp requirement\\nValues below 10 return zero.\\n"
    (workspace_root / "demo" / "product-spec.md").write_text(spec_content, encoding="utf-8")
    request, public_key_b64, _signature_b64 = _signed_specification_request(
        task_id, snapshot, spec_content=spec_content
    )
    verifier.trusted_oracle_public_keys = {"reviewer-release": public_key_b64}
    request.cases[0].expected_output = 1

    with pytest.raises(GroundedVerificationError, match="signature .* is invalid") as raised:
        verifier.run(task_id, request, workspace_root)

    assert raised.value.status_code == 403
    assert runner.calls == []
    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    assert not any(item.kind == "oracle" for item in saved.evidence)


def test_unconfigured_reviewer_key_is_rejected_before_execution(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    spec_content = "# Clamp requirement\\nValues below 10 return zero.\\n"
    (workspace_root / "demo" / "product-spec.md").write_text(spec_content, encoding="utf-8")
    request, _public_key_b64, _signature_b64 = _signed_specification_request(
        task_id, snapshot, spec_content=spec_content
    )
    verifier.trusted_oracle_public_keys = {}

    with pytest.raises(GroundedVerificationError, match="not in the configured trusted keyring") as raised:
        verifier.run(task_id, request, workspace_root)

    assert raised.value.status_code == 403
    assert runner.calls == []
    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    assert not any(item.kind == "oracle" for item in saved.evidence)


def test_attestation_requires_a_pinned_specification_artifact(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    private_key = Ed25519PrivateKey.generate()
    signature_b64 = base64.b64encode(private_key.sign(b"irrelevant")).decode("ascii")
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="clamp",
        source_sha256=snapshot.sha256,
        cases=[
            GroundedOracleCase(
                case_id="missing-artifact",
                arguments={"value": 9},
                expected_output=0,
                oracle_kind="specification",
                oracle_reference="product specification requirement",
                oracle_attestation=GroundedOracleAttestation(
                    key_id="reviewer-release",
                    issued_at=datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc),
                    signature_b64=signature_b64,
                ),
            )
        ],
    )

    with pytest.raises(GroundedVerificationError, match="requires a pinned specification artifact"):
        verifier.run(task_id, request, workspace_root)

    assert runner.calls == []


def test_oracle_attestation_rejects_timezone_naive_issuance_time() -> None:
    signature_b64 = base64.b64encode(b"x" * 64).decode("ascii")
    with pytest.raises(ValidationError, match="explicit timezone"):
        GroundedOracleAttestation(
            key_id="reviewer-release",
            issued_at=datetime(2026, 11, 1, 9, 0),
            signature_b64=signature_b64,
        )

def test_grounded_case_turns_exact_probe_into_specification_backed_test(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, ledger, snapshot, probes, runner, verifier = _setup(tmp_path)
    assert probes.candidates[0].candidate_id == "probe-1"

    result = verifier.run(task_id, _request(snapshot), workspace_root)

    assert result.status == "passed"
    assert result.case_count == 1
    assert result.passed_cases == 1
    assert result.counterexamples == []
    assert result.oracle_evidence[0].passed is None
    assert result.oracle_evidence[0].metadata["oracle_reference"] == "product-spec.md#clamp-lower-bound"
    call = runner.calls[0]
    assert call["path"].startswith("unai_generated_tests/")
    assert call["metadata"]["target_sha256"] == snapshot.sha256
    assert "UNAI_COUNTEREXAMPLE:" in call["content"]
    ast.parse(call["content"])

    stored = ledger.get_snapshot(task_id)
    assert stored is not None
    assert any(item.kind == "oracle" for item in stored.evidence)
    assert any(item.kind == "trace" for item in stored.evidence)


def test_failed_grounded_case_records_counterexample_and_proof_bundle(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, ledger, snapshot, probes, runner, verifier = _setup(tmp_path)
    runner.status = "failed"
    runner.stdout = (
        "FAILED unai_generated_tests/test_oracle.py::test_grounded_oracle_case[lower-edge]\n"
        'UNAI_COUNTEREXAMPLE:{"case_id":"lower-edge","actual_type":"int","actual_summary":"1"}\n'
    )

    result = verifier.run(task_id, _request(snapshot, expected=0), workspace_root)

    assert result.status == "failed"
    assert result.passed_cases == 0
    assert len(result.counterexamples) == 1
    assert result.counterexamples[0].case_id == "lower-edge"
    assert result.counterexamples[0].actual_summary == "int: 1"

    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    bundle = build_proof_bundle(saved)
    assert bundle.overall_status == "counterexamples_found"
    assert bundle.grounded_case_count == 1
    assert bundle.grounded_run_count == 1
    assert bundle.grounded_counterexample_count == 1
    assert bundle.unverified_probe_candidate_count == len(probes.candidates) - 1
    assert len(bundle.execution_traces) == 1
    assert any("lack a valid signature from an operator-configured trusted reviewer key" in risk for risk in bundle.unresolved_risks)


def test_counterexample_trace_records_actual_result_hash_and_proof_metadata(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    actual_sha256 = hashlib.sha256(b"1").hexdigest()
    runner.status = "failed"
    runner.stdout = (
        'UNAI_COUNTEREXAMPLE:{"case_id":"lower-edge","actual_type":"int",'
        '"actual_summary":"1","actual_sha256":"' + actual_sha256 + '"}' + chr(10)
    )

    result = verifier.run(task_id, _request(snapshot, expected=0), workspace_root)

    assert result.status == "failed"
    counterexample = result.counterexamples[0]
    assert counterexample.actual_sha256 == actual_sha256
    assert counterexample.exception_message_sha256 is None
    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    event = next(item for item in saved.evidence if item.kind == "counterexample")
    assert event.metadata["actual_sha256"] == actual_sha256
    assert event.metadata["target_line"] is None


def test_exception_counterexample_trace_records_target_frame_and_message_hash(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    message_sha256 = hashlib.sha256(b"bad value").hexdigest()
    runner.status = "failed"
    runner.stdout = (
        'UNAI_COUNTEREXAMPLE:{"case_id":"lower-edge","actual_type":"ValueError",'
        '"actual_summary":"ValueError: bad value","exception_message_sha256":"'
        + message_sha256
        + '","target_frame":{"line":7,"function":"clamp"}}' + chr(10)
    )

    result = verifier.run(task_id, _request(snapshot, expected=0), workspace_root)

    assert result.status == "failed"
    counterexample = result.counterexamples[0]
    assert counterexample.exception_message_sha256 == message_sha256
    assert counterexample.target_line == 7
    assert counterexample.target_function == "clamp"
    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    event = next(item for item in saved.evidence if item.kind == "counterexample")
    assert event.metadata["exception_message_sha256"] == message_sha256
    assert event.metadata["target_line"] == 7
    assert event.metadata["target_function"] == "clamp"


def test_generated_oracle_harness_emits_structured_trace_fields(tmp_path: Path) -> None:
    _task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)

    verifier.run(snapshot.task_id, _request(snapshot), workspace_root)

    generated = runner.calls[0]["content"]
    ast.parse(generated)
    assert "def _json_sha256(value):" in generated
    assert "def _target_exception_frame(exc):" in generated
    assert "exception_message_sha256" in generated
    assert "actual_sha256" in generated
    assert "target_frame" in generated
    assert "traceback.extract_tb(exc.__traceback__)" in generated


def test_grounded_request_rejects_stale_source_fingerprint(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    request = _request(snapshot, source_hash="0" * 64)

    with pytest.raises(GroundedVerificationError, match="source changed") as raised:
        verifier.run(task_id, request, workspace_root)

    assert raised.value.status_code == 409
    assert runner.calls == []


def test_grounded_request_requires_explicit_read_and_test_approvals(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(
        tmp_path, permissions=["workspace_read"]
    )

    with pytest.raises(GroundedVerificationError, match="run_tests") as raised:
        verifier.run(task_id, _request(snapshot), workspace_root)

    assert raised.value.status_code == 403
    assert runner.calls == []


def test_candidate_must_belong_to_exact_source_and_function(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    request = _request(snapshot, candidate_id="probe-does-not-exist")

    with pytest.raises(GroundedVerificationError, match="was not found"):
        verifier.run(task_id, request, workspace_root)

    assert runner.calls == []


def test_oracle_case_must_include_all_required_function_parameters(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="clamp",
        source_sha256=snapshot.sha256,
        cases=[
            GroundedOracleCase(
                case_id="missing-input",
                arguments={},
                expected_output=0,
                oracle_kind="user",
                oracle_reference="User-supplied expected result",
            )
        ],
    )

    with pytest.raises(GroundedVerificationError, match="missing required parameters"):
        verifier.run(task_id, request, workspace_root)

    assert runner.calls == []


def test_generated_oracle_code_serializes_untrusted_text_in_json_string(tmp_path: Path) -> None:
    _task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="clamp",
        source_sha256=snapshot.sha256,
        cases=[
            GroundedOracleCase(
                case_id="safe-id",
                arguments={"value": 10},
                expected_output="'); import os; os.remove('/workspace/data') #",
                oracle_kind="user",
                oracle_reference="User supplied an adversarial-looking expected string",
            )
        ],
    )

    verifier.run(snapshot.task_id, request, workspace_root)
    generated = runner.calls[0]["content"]
    ast.parse(generated)
    assert "json.loads(" in generated
    imported = [
        alias.name
        for node in ast.walk(ast.parse(generated))
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    # The generated harness imports os for its fixed, no-follow replay trace writer.
    # Caller text is serialized as JSON and must never become an os.remove call.
    assert "os" in imported
    generated_tree = ast.parse(generated)
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
        and node.func.attr == "remove"
        for node in ast.walk(generated_tree)
    )


class FakeSandbox:
    def __init__(self, exit_code=0, stdout="", stderr="") -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.seen_path: Path | None = None
        self.seen_command: str | None = None
        self.seen_test = ""

    def run(self, workspace: Path, command: str):
        self.seen_path = workspace
        self.seen_command = command
        generated = list((workspace / "unai_generated_tests").glob("*.py"))
        assert len(generated) == 1
        self.seen_test = generated[0].read_text(encoding="utf-8")
        return ExecutionResult(command, self.exit_code, self.stdout, self.stderr)


def test_runner_stages_generated_test_only_in_disposable_copy(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, ledger, _snapshot, _probes, _fake, _verifier = _setup(tmp_path)
    sandbox = FakeSandbox()
    runner = IsolatedPythonTestRunner(ledger, sandbox)  # type: ignore[arg-type]

    result = runner.run_generated_test(
        task_id,
        workspace_root,
        "unai_generated_tests/test_oracle_example.py",
        "def test_example():\n    assert 1 == 1\n",
        {"target_sha256": "a" * 64, "oracle_case_ids": ["case-1"]},
    )

    assert result.status == "passed"
    assert result.evidence.source == "grounded-oracle-sandbox"
    assert sandbox.seen_command == "python -m pytest -q"
    assert sandbox.seen_test.startswith("def test_example")
    assert not (target.parent / "unai_generated_tests").exists()
    assert sandbox.seen_path is not None
    assert not sandbox.seen_path.exists()  # the TemporaryDirectory is removed after execution


def test_runner_refuses_generated_test_outside_reserved_staging_folder(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, ledger, _snapshot, _probes, _fake, _verifier = _setup(tmp_path)
    sandbox = FakeSandbox()
    runner = IsolatedPythonTestRunner(ledger, sandbox)  # type: ignore[arg-type]

    result = runner.run_generated_test(
        task_id,
        workspace_root,
        "../test.py",
        "def test_bad():\n    assert True\n",
        {},
    )

    assert result.status == "unavailable"
    assert sandbox.seen_command is None


def test_test_runner_extracts_counterexample_markers_before_preview_truncation(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, ledger, _snapshot, _probes, _fake, _verifier = _setup(tmp_path)
    first = 'UNAI_COUNTEREXAMPLE:{"case_id":"one","actual_type":"int","actual_summary":"1"}'
    second = 'UNAI_COUNTEREXAMPLE:{"case_id":"two","actual_type":"str","actual_summary":"secret=abc123"}'
    sandbox = FakeSandbox(exit_code=1, stdout=(first + "\n") + ("trace " * 2000) + ("\n" + second))
    runner = IsolatedPythonTestRunner(ledger, sandbox)  # type: ignore[arg-type]

    result = runner.run_generated_test(
        task_id,
        workspace_root,
        "unai_generated_tests/test_oracle.py",
        "def test_placeholder():\n    assert False\n",
        {},
        evidence_source="grounded-oracle-shrink-sandbox",
    )

    assert result.evidence.source == "grounded-oracle-shrink-sandbox"
    markers = result.evidence.metadata["counterexample_markers"]
    assert [item["case_id"] for item in markers] == ["one", "two"]
    assert markers[1]["actual_summary"] == "secret=[REDACTED]"


def test_unavailable_oracle_run_does_not_mark_probe_as_verified(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, ledger, snapshot, probes, runner, verifier = _setup(tmp_path)
    runner.status = "unavailable"

    result = verifier.run(task_id, _request(snapshot), workspace_root)

    assert result.status == "unavailable"
    stored = ledger.get_snapshot(task_id)
    assert stored is not None
    bundle = build_proof_bundle(stored)
    assert bundle.overall_status == "grounded_tests_unavailable"
    assert bundle.unverified_probe_candidate_count == len(probes.candidates)


def test_old_counterexample_remains_historical_but_latest_pass_controls_status(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    runner.status = "failed"
    runner.stdout = (
        'UNAI_COUNTEREXAMPLE:{"case_id":"lower-edge","actual_type":"int","actual_summary":"1"}\n'
    )
    failed = verifier.run(task_id, _request(snapshot, expected=0), workspace_root)
    assert len(failed.counterexamples) == 1

    runner.status = "passed"
    runner.stdout = ""
    passed = verifier.run(task_id, _request(snapshot, expected=1), workspace_root)
    assert passed.status == "passed"

    stored = ledger.get_snapshot(task_id)
    assert stored is not None
    bundle = build_proof_bundle(stored)
    assert bundle.overall_status == "grounded_tests_passed"
    assert bundle.grounded_counterexample_count == 1
    assert bundle.counterexamples[0].case_id == "lower-edge"


def test_grounded_oracle_payload_has_per_value_size_limit(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="clamp",
        source_sha256=snapshot.sha256,
        cases=[
            GroundedOracleCase(
                case_id="oversized-output",
                arguments={"value": 9},
                expected_output="x" * 10_001,
                oracle_kind="user",
                oracle_reference="Oversized user-supplied expected value",
                probe_candidate_id="probe-1",
            )
        ],
    )

    with pytest.raises(GroundedVerificationError, match="10 KB per-value limit"):
        verifier.run(task_id, request, workspace_root)

    assert runner.calls == []



def test_async_grounded_function_uses_asyncio_run_in_generated_harness(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "async def add_one(value: int):\n"
        "    return value + 1\n",
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="add_one",
        source_sha256=fresh.sha256,
        cases=[
            GroundedOracleCase(
                case_id="async-add-one",
                arguments={"value": 9},
                expected_output=10,
                oracle_kind="user",
                oracle_reference="User supplied async addition result",
            )
        ],
    )

    result = verifier.run(task_id, request, workspace_root)

    assert result.status == "passed"
    generated = runner.calls[0]["content"]
    ast.parse(generated)
    assert "import asyncio\nimport atexit\nimport copy\nimport hashlib\nimport importlib.util" in generated
    assert "asyncio.run(_FUNCTION(**case['arguments']))" in generated
    assert runner.calls[0]["metadata"]["target_is_async"] is True


def test_sync_grounded_harness_records_target_kind(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)

    verifier.run(task_id, _request(snapshot), workspace_root)

    assert runner.calls[0]["metadata"]["target_is_async"] is False


def test_staticmethod_grounded_target_uses_explicit_class_binding(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "class Calculator:" + chr(10)
        + "    @staticmethod" + chr(10)
        + "    def add(value, offset=1):" + chr(10)
        + "        return value + offset" + chr(10),
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="Calculator.add",
        source_sha256=fresh.sha256,
        cases=[
            GroundedOracleCase(
                case_id="static-add",
                arguments={"value": 9},
                expected_output=10,
                oracle_kind="user",
                oracle_reference="User supplied static method result",
            )
        ],
    )

    result = verifier.run(task_id, request, workspace_root)

    assert result.status == "passed"
    generated = runner.calls[0]["content"]
    ast.parse(generated)
    assert "_OWNER = 'Calculator'" in generated
    assert "_METHOD = 'add'" in generated
    assert "_CLASS = getattr(_MODULE, _OWNER, None)" in generated
    assert "_FUNCTION = getattr(_CLASS, _METHOD, None)" in generated
    assert "_FUNCTION(**case['arguments'])" in generated
    assert runner.calls[0]["metadata"]["target_is_async"] is False


def test_nested_instance_method_runs_with_explicit_fixture_in_generated_harness(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    source = (
        "class Namespace:\n"
        "    class Counter:\n"
        "        def __init__(self, start):\n"
        "            self.value = start\n"
        "        def add(self, amount):\n"
        "            return self.value + amount\n"
    )
    target.write_text(source, encoding="utf-8")
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="Namespace.Counter.add",
        source_sha256=fresh.sha256,
        cases=[
            GroundedOracleCase(
                case_id="nested-counter-add",
                arguments={"amount": 5},
                instance_arguments={"start": 7},
                expected_output=12,
                oracle_kind="user",
                oracle_reference="User supplied nested Counter.add result",
            )
        ],
    )

    result = verifier.run(task_id, request, workspace_root)

    assert result.status == "passed"
    generated_call = runner.calls[0]
    assert "_OWNER_PATH = ('Namespace', 'Counter')" in generated_call["content"]
    staged_root = tmp_path / "nested-instance-harness"
    staged_root.mkdir()
    (staged_root / "service.py").write_text(source, encoding="utf-8")
    generated_test = staged_root / generated_call["path"]
    generated_test.parent.mkdir(parents=True, exist_ok=True)
    generated_test.write_text(generated_call["content"], encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(generated_test)],
        cwd=staged_root,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert result.oracle_evidence[0].metadata["instance_arguments_sha256"]


def test_nested_staticmethod_runs_through_explicit_namespace_path(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    source = (
        "class Namespace:\n"
        "    class Math:\n"
        "        @staticmethod\n"
        "        def add(value, offset=2):\n"
        "            return value + offset\n"
    )
    target.write_text(source, encoding="utf-8")
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="Namespace.Math.add",
        source_sha256=fresh.sha256,
        cases=[
            GroundedOracleCase(
                case_id="nested-static-add",
                arguments={"value": 8},
                expected_output=10,
                oracle_kind="user",
                oracle_reference="User supplied nested static method result",
            )
        ],
    )

    result = verifier.run(task_id, request, workspace_root)

    assert result.status == "passed"
    generated_call = runner.calls[0]
    staged_root = tmp_path / "nested-static-harness"
    staged_root.mkdir()
    (staged_root / "service.py").write_text(source, encoding="utf-8")
    generated_test = staged_root / generated_call["path"]
    generated_test.parent.mkdir(parents=True, exist_ok=True)
    generated_test.write_text(generated_call["content"], encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(generated_test)],
        cwd=staged_root,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_nested_class_inheritance_is_rejected_before_execution(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    source = (
        "class Namespace:\n"
        "    class Base:\n"
        "        pass\n"
        "    class Counter(Base):\n"
        "        def read(self, value):\n"
        "            return value\n"
    )
    target.write_text(source, encoding="utf-8")
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="Namespace.Counter.read",
        source_sha256=fresh.sha256,
        cases=[
            GroundedOracleCase(
                case_id="nested-inheritance-rejected",
                arguments={"value": 1},
                instance_arguments={},
                expected_output=1,
                oracle_kind="user",
                oracle_reference="User supplied nested class fixture check",
            )
        ],
    )

    with pytest.raises(
        GroundedVerificationError,
        match="Nested-class targets require undecorated namespace classes without bases or metaclass keywords",
    ):
        verifier.run(task_id, request, workspace_root)
    assert runner.calls == []


def test_async_classmethod_uses_bound_cls_and_asyncio_run(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "class AsyncCalculator:" + chr(10)
        + "    @classmethod" + chr(10)
        + "    async def add(cls, value):" + chr(10)
        + "        return value + 1" + chr(10),
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="AsyncCalculator.add",
        source_sha256=fresh.sha256,
        cases=[
            GroundedOracleCase(
                case_id="async-class-add",
                arguments={"value": 9},
                expected_output=10,
                oracle_kind="user",
                oracle_reference="User supplied async classmethod result",
            )
        ],
    )

    result = verifier.run(task_id, request, workspace_root)

    assert result.status == "passed"
    generated = runner.calls[0]["content"]
    ast.parse(generated)
    assert "asyncio.run(_FUNCTION(**case['arguments']))" in generated
    assert "_OWNER = 'AsyncCalculator'" in generated
    assert runner.calls[0]["metadata"]["target_is_async"] is True


@pytest.mark.parametrize(
    "source,symbol_name,error",
    [
        (
            "class Calculator:" + chr(10) + "    @property" + chr(10) + "    def add(self):" + chr(10) + "        return 1" + chr(10),
            "Calculator.add",
            "Only explicitly decorated @staticmethod/@classmethod",
        ),
        (
            "def marker(fn):" + chr(10) + "    return fn" + chr(10) + "@marker" + chr(10) + "def add(value):" + chr(10) + "    return value" + chr(10),
            "add",
            "Decorated functions are not supported at module level",
        ),
    ],
)
def test_unsupported_method_and_decorator_invocation_forms_are_rejected(
    tmp_path: Path, source: str, symbol_name: str, error: str
) -> None:
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(source, encoding="utf-8")
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name=symbol_name,
        source_sha256=fresh.sha256,
        cases=[
            GroundedOracleCase(
                case_id="unsupported-invocation",
                arguments={"value": 1},
                expected_output=1,
                oracle_kind="user",
                oracle_reference="User supplied invocation result",
            )
        ],
    )

    with pytest.raises(GroundedVerificationError, match=error):
        verifier.run(task_id, request, workspace_root)

    assert runner.calls == []


def test_classmethod_bound_parameter_is_not_exposed_as_oracle_argument(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "class Calculator:" + chr(10)
        + "    @classmethod" + chr(10)
        + "    def add(cls, value):" + chr(10)
        + "        return value" + chr(10),
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="Calculator.add",
        source_sha256=fresh.sha256,
        cases=[
            GroundedOracleCase(
                case_id="class-parameter-rejected",
                arguments={"cls": "caller-controlled", "value": 1},
                expected_output=1,
                oracle_kind="user",
                oracle_reference="User supplied classmethod result",
            )
        ],
    )

    with pytest.raises(GroundedVerificationError, match="unknown parameters: cls"):
        verifier.run(task_id, request, workspace_root)

    assert runner.calls == []



def test_instance_method_uses_explicit_json_constructor_fixture(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "class Counter:\n"
        "    def __init__(self, start, step=1):\n"
        "        self.value = start\n"
        "        self.step = step\n"
        "    def advance(self, amount=1):\n"
        "        self.value += amount * self.step\n"
        "        return self.value\n",
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="Counter.advance",
        source_sha256=fresh.sha256,
        cases=[
            GroundedOracleCase(
                case_id="counter-advance",
                arguments={"amount": 3},
                instance_arguments={"start": 10, "step": 2},
                expected_output=16,
                oracle_kind="user",
                oracle_reference="User supplied Counter.advance result",
            )
        ],
    )

    result = verifier.run(task_id, request, workspace_root)

    assert result.status == "passed"
    generated = runner.calls[0]["content"]
    ast.parse(generated)
    assert "_FUNCTION = _CLASS.__dict__.get(_METHOD) if _CLASS is not None else None" in generated
    assert "_FUNCTION(_CLASS(**case['instance_arguments']), **case['arguments'])" in generated
    assert runner.calls[0]["metadata"]["target_invocation_kind"] == "instance_method"
    fixture_hash = result.oracle_evidence[0].metadata["instance_arguments_sha256"]
    assert fixture_hash == hashlib.sha256(
        json.dumps(
            {"start": 10, "step": 2},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    assert any(item.kind == "oracle" for item in saved.evidence)


def test_async_instance_method_uses_fresh_constructor_fixture(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "class AsyncCounter:\n"
        "    def __init__(self, start):\n"
        "        self.value = start\n"
        "    async def advance(self, amount):\n"
        "        return self.value + amount\n",
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="AsyncCounter.advance",
        source_sha256=fresh.sha256,
        cases=[
            GroundedOracleCase(
                case_id="async-instance",
                arguments={"amount": 5},
                instance_arguments={"start": 7},
                expected_output=12,
                oracle_kind="user",
                oracle_reference="User supplied async instance-method result",
            )
        ],
    )

    result = verifier.run(task_id, request, workspace_root)

    assert result.status == "passed"
    generated = runner.calls[0]["content"]
    ast.parse(generated)
    assert "asyncio.run(_FUNCTION(_CLASS(**case['instance_arguments']), **case['arguments']))" in generated
    assert runner.calls[0]["metadata"]["target_is_async"] is True
    assert runner.calls[0]["metadata"]["target_invocation_kind"] == "instance_method"


@pytest.mark.parametrize(
    "source,instance_arguments,error",
    [
        (
            "class Counter:\n    def __init__(self, start):\n        self.start = start\n"
            "    def read(self, amount):\n        return self.start + amount\n",
            {},
            "missing required constructor parameters: start",
        ),
        (
            "class Counter:\n    def __init__(self):\n        pass\n"
            "    def read(self, amount):\n        return amount\n",
            {"unexpected": 1},
            "unknown constructor parameters: unexpected",
        ),
        (
            "class Base:\n    pass\nclass Counter(Base):\n"
            "    def read(self, amount):\n        return amount\n",
            {},
            "without bases or metaclass keywords",
        ),
        (
            "class Counter:\n    def __new__(cls):\n        return object.__new__(cls)\n"
            "    def read(self, amount):\n        return amount\n",
            {},
            "custom __new__",
        ),
        (
            "class Counter:\n    async def __init__(self):\n        pass\n"
            "    def read(self, amount):\n        return amount\n",
            {},
            "synchronous, undecorated method",
        ),
    ],
)
def test_instance_method_rejects_unsafe_or_invalid_fixture_construction(
    tmp_path: Path, source: str, instance_arguments: dict, error: str
) -> None:
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(source, encoding="utf-8")
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="Counter.read",
        source_sha256=fresh.sha256,
        cases=[
            GroundedOracleCase(
                case_id="bad-instance-fixture",
                arguments={"amount": 1},
                instance_arguments=instance_arguments,
                expected_output=1,
                oracle_kind="user",
                oracle_reference="User supplied instance fixture check",
            )
        ],
    )

    with pytest.raises(GroundedVerificationError, match=error):
        verifier.run(task_id, request, workspace_root)

    assert runner.calls == []


def test_instance_fixture_is_rejected_for_static_method_targets(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "class Calculator:\n"
        "    @staticmethod\n"
        "    def add(value):\n"
        "        return value + 1\n",
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="Calculator.add",
        source_sha256=fresh.sha256,
        cases=[
            GroundedOracleCase(
                case_id="static-fixture-rejected",
                arguments={"value": 1},
                instance_arguments={},
                expected_output=2,
                oracle_kind="user",
                oracle_reference="User supplied static method fixture check",
            )
        ],
    )

    with pytest.raises(GroundedVerificationError, match="only for an ordinary instance-method"):
        verifier.run(task_id, request, workspace_root)

    assert runner.calls == []


def test_instance_fixture_hash_is_covered_by_reviewer_attestation_statement() -> None:
    statement = build_oracle_attestation_statement(
        key_id="reviewer-release",
        issued_at=datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc),
        task_id="task-1",
        workspace="demo",
        file_path="service.py",
        symbol_name="Counter.advance",
        source_sha256="a" * 64,
        case_id="counter-case",
        oracle_kind="specification",
        oracle_reference="product-spec.md#counter",
        oracle_artifact_path="product-spec.md",
        oracle_artifact_sha256="b" * 64,
        arguments_sha256="c" * 64,
        expected_output_sha256="d" * 64,
        oracle_property_sha256=None,
        instance_arguments_sha256="e" * 64,
    )
    assert statement["instance_arguments_sha256"] == "e" * 64

    legacy_statement = build_oracle_attestation_statement(
        key_id="reviewer-release",
        issued_at=datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc),
        task_id="task-1",
        workspace="demo",
        file_path="service.py",
        symbol_name="clamp",
        source_sha256="a" * 64,
        case_id="clamp-case",
        oracle_kind="specification",
        oracle_reference="product-spec.md#clamp",
        oracle_artifact_path="product-spec.md",
        oracle_artifact_sha256="b" * 64,
        arguments_sha256="c" * 64,
        expected_output_sha256="d" * 64,
        oracle_property_sha256=None,
    )
    assert "instance_arguments_sha256" not in legacy_statement


def test_decorated_async_function_remains_rejected(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "def marker(fn):\n"
        "    return fn\n\n"
        "@marker\n"
        "async def add_one(value):\n"
        "    return value + 1\n",
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="add_one",
        source_sha256=fresh.sha256,
        cases=[
            GroundedOracleCase(
                case_id="decorated-async",
                arguments={"value": 9},
                expected_output=10,
                oracle_kind="user",
                oracle_reference="User supplied decorated async result",
            )
        ],
    )

    with pytest.raises(GroundedVerificationError, match="Decorated functions"):
        verifier.run(task_id, request, workspace_root)

    assert runner.calls == []



def test_specification_artifact_hash_is_recorded_and_verified(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    spec = workspace_root / "demo" / "product-spec.md"
    spec_content = "# Clamp requirement\nValues below 10 return zero.\n"
    spec.write_text(spec_content, encoding="utf-8")
    expected_hash = hashlib.sha256(spec_content.encode("utf-8")).hexdigest()

    result = verifier.run(
        task_id,
        _request(
            snapshot,
            oracle_artifact_path="product-spec.md",
            oracle_artifact_sha256=expected_hash,
        ),
        workspace_root,
    )

    metadata = result.oracle_evidence[0].metadata
    assert metadata["oracle_artifact_path"] == "product-spec.md"
    assert metadata["oracle_artifact_sha256"] == expected_hash
    assert metadata["oracle_artifact_integrity"] == "workspace-file-sha256-verified"
    assert metadata["oracle_artifact_size_bytes"] == len(spec_content.encode("utf-8"))
    assert runner.calls[0]["metadata"]["oracle_references"][0]["oracle_artifact_sha256"] == expected_hash
    assert runner.calls[0]["metadata"]["staged_fingerprint_assertions_embedded"] is True
    generated = runner.calls[0]["content"]
    staged_root = tmp_path / "staged"
    staged_root.mkdir()
    (staged_root / "service.py").write_text(
        (workspace_root / "demo" / "service.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (staged_root / "product-spec.md").write_text(spec_content, encoding="utf-8")
    generated_path = staged_root / "unai_generated_tests" / "test_oracle.py"
    generated_path.parent.mkdir()
    generated_path.write_text(generated, encoding="utf-8")
    namespace = {"__file__": str(generated_path)}
    exec(compile(generated, str(generated_path), "exec"), namespace)
    namespace["test_grounded_oracle_case"](namespace["_CASES"][0])

    # The same generated harness rejects a document whose bytes changed after
    # the engine's final pre-staging read.
    (staged_root / "product-spec.md").write_text("# Modified spec\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Pinned specification artifact changed"):
        exec(compile(generated, str(generated_path), "exec"), {"__file__": str(generated_path)})


def test_specification_artifact_hash_mismatch_rejects_before_execution(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    spec = workspace_root / "demo" / "product-spec.md"
    spec.write_text("# Actual specification\\n", encoding="utf-8")

    with pytest.raises(GroundedVerificationError, match="does not match its supplied SHA-256") as raised:
        verifier.run(
            task_id,
            _request(
                snapshot,
                oracle_artifact_path="product-spec.md",
                oracle_artifact_sha256="0" * 64,
            ),
            workspace_root,
        )

    assert raised.value.status_code == 409
    assert runner.calls == []
    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    assert not any(item.kind == "oracle" for item in saved.evidence)


def test_specification_artifact_requires_a_path_hash_pair(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)

    with pytest.raises(GroundedVerificationError, match="must supply both"):
        verifier.run(
            task_id,
            _request(snapshot, oracle_artifact_path="product-spec.md"),
            workspace_root,
        )

    assert runner.calls == []


def test_specification_artifact_path_rejects_traversal_and_non_text_files(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)

    with pytest.raises(GroundedVerificationError, match="workspace-relative"):
        verifier.run(
            task_id,
            _request(
                snapshot,
                oracle_artifact_path="../outside.md",
                oracle_artifact_sha256="0" * 64,
            ),
            workspace_root,
        )

    assert runner.calls == []



def test_specification_artifact_is_rechecked_before_sandbox_staging(tmp_path: Path, monkeypatch) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    spec = workspace_root / "demo" / "product-spec.md"
    initial_content = "# Original specification\n"
    spec.write_text(initial_content, encoding="utf-8")
    expected_hash = hashlib.sha256(initial_content.encode("utf-8")).hexdigest()
    original_read = verifier.patch_manager.read_file
    artifact_reads = 0

    def change_spec_before_second_read(read_task_id, file_path, read_workspace_root):
        nonlocal artifact_reads
        if file_path == "product-spec.md":
            artifact_reads += 1
            if artifact_reads == 2:
                spec.write_text("# Changed after verification\\n", encoding="utf-8")
        return original_read(read_task_id, file_path, read_workspace_root)

    monkeypatch.setattr(verifier.patch_manager, "read_file", change_spec_before_second_read)
    with pytest.raises(
        GroundedVerificationError,
        match="changed after test generation and before sandbox staging",
    ) as raised:
        verifier.run(
            task_id,
            _request(
                snapshot,
                oracle_artifact_path="product-spec.md",
                oracle_artifact_sha256=expected_hash,
            ),
            workspace_root,
        )

    assert raised.value.status_code == 409
    assert artifact_reads == 2
    assert runner.calls == []



def test_user_oracle_does_not_create_missing_specification_pin_risk(tmp_path: Path) -> None:
    task_id, _workspace_root, _target, _source, ledger, _snapshot, _probes, _runner, _verifier = _setup(tmp_path)
    ledger.append_evidence(
        EvidenceRecord(
            evidence_id="user-oracle-only",
            task_id=task_id,
            kind="oracle",
            source="grounded-oracle",
            claim="Expected output supplied directly by the user.",
            metadata={"case_id": "user-case", "oracle_kind": "user"},
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    )

    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    bundle = build_proof_bundle(saved)

    assert not any(
        "specification references are not bound" in risk
        for risk in bundle.unresolved_risks
    )



def _property_request(
    snapshot,
    *,
    value=9,
    property_kind="output_non_negative",
    argument_name=None,
    exception_name=None,
    replay_count=1,
):
    return GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="clamp",
        source_sha256=snapshot.sha256,
        cases=[
            GroundedOracleCase(
                case_id="property-negative",
                arguments={"value": value},
                oracle_property=GroundedPropertyOracle(
                    kind=property_kind,
                    argument_name=argument_name,
                    exception_name=exception_name,
                    replay_count=replay_count,
                ),
                oracle_kind="user",
                oracle_reference="User supplied non-negative return-value property",
            )
        ],
    )


def _generated_cases(generated_source: str) -> list[dict]:
    tree = ast.parse(generated_source)
    assignment = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_CASES" for target in node.targets)
    )
    payload = ast.literal_eval(assignment.value.args[0])
    return json.loads(payload)


def test_replay_count_model_rejects_values_above_per_case_limit() -> None:
    with pytest.raises(ValidationError):
        GroundedPropertyOracle(
            kind="output_stable_across_replays",
            replay_count=5,
        )


def test_replay_stability_contract_requires_explicit_bounded_invocations(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)

    request = _property_request(
        snapshot,
        property_kind="output_stable_across_replays",
        replay_count=1,
    )
    with pytest.raises(GroundedVerificationError, match="at least 2 invocations"):
        verifier.run(task_id, request, workspace_root)
    assert runner.calls == []

    request = _property_request(
        snapshot,
        property_kind="output_non_negative",
        replay_count=2,
    )
    with pytest.raises(GroundedVerificationError, match="can set replay_count only"):
        verifier.run(task_id, request, workspace_root)
    assert runner.calls == []


def test_replay_stability_contract_enforces_request_wide_invocation_budget(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    cases = [
        GroundedOracleCase(
            case_id=f"replay-case-{index}",
            arguments={"value": index},
            oracle_property=GroundedPropertyOracle(
                kind="output_stable_across_replays",
                replay_count=4,
            ),
            oracle_kind="user",
            oracle_reference="User supplied repeatability invariant",
        )
        for index in range(7)
    ]
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="clamp",
        source_sha256=snapshot.sha256,
        cases=cases,
    )

    with pytest.raises(GroundedVerificationError, match="24 function invocations per request"):
        verifier.run(task_id, request, workspace_root)
    assert runner.calls == []


def _execute_replay_harness(
    tmp_path: Path,
    *,
    source: str,
    value,
    expected_stable: bool,
):
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(source, encoding="utf-8")
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = _property_request(
        fresh,
        value=value,
        property_kind="output_stable_across_replays",
        replay_count=3,
    )
    result = verifier.run(task_id, request, workspace_root)
    assert result.status == "passed"

    generated_call = runner.calls[0]
    staged_root = tmp_path / "replay-harness-workspace"
    staged_root.mkdir()
    (staged_root / "service.py").write_text(source, encoding="utf-8")
    generated_test = staged_root / generated_call["path"]
    generated_test.parent.mkdir(parents=True, exist_ok=True)
    generated_test.write_text(generated_call["content"], encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(generated_test)],
        cwd=staged_root,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    trace_relative = generated_call["metadata"]["replay_trace_relative_path"]
    trace_path = staged_root / trace_relative
    assert trace_path.is_file()
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    record = trace["records"][0]
    assert record["kind"] == "output_stable_across_replays"
    assert record["replay_count"] == 3
    assert record["stable"] is expected_stable
    assert len(record["results"]) == 3
    assert "actual_summary" not in json.dumps(record)
    if expected_stable:
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert len({item["actual_sha256"] for item in record["results"]}) == 1
    else:
        assert completed.returncode != 0
        assert "UNAI_COUNTEREXAMPLE:" in completed.stdout + completed.stderr
        assert len({item["actual_sha256"] for item in record["results"]}) == 3
    return record


def _execute_filesystem_audit_harness(
    tmp_path: Path,
    *,
    source: str,
    value=1,
):
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(source, encoding="utf-8")
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = _property_request(
        fresh,
        value=value,
        property_kind="workspace_filesystem_unchanged",
    )
    result = verifier.run(task_id, request, workspace_root)
    assert result.status == "passed"

    generated_call = runner.calls[0]
    staged_root = tmp_path / "filesystem-audit-workspace"
    staged_root.mkdir()
    (staged_root / "service.py").write_text(source, encoding="utf-8")
    generated_test = staged_root / generated_call["path"]
    generated_test.parent.mkdir(parents=True, exist_ok=True)
    generated_test.write_text(generated_call["content"], encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(generated_test)],
        cwd=staged_root,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    trace_path = staged_root / generated_call["metadata"]["replay_trace_relative_path"]
    assert trace_path.is_file()
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    record = trace["records"][0]
    assert record["kind"] == "workspace_filesystem_unchanged"
    assert record["status"] == "complete"
    assert "side-effect.txt" not in json.dumps(record)
    return completed, record, staged_root


def _execute_external_fixture_harness(
    tmp_path: Path,
    *,
    source: str,
    fixture_mounted: bool = True,
):
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(source, encoding="utf-8")
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = _property_request(
        fresh,
        property_kind="external_fixture_filesystem_unchanged",
    )
    result = verifier.run(task_id, request, workspace_root)
    assert result.status == "passed"

    generated_call = runner.calls[0]
    staged_root = tmp_path / "external-fixture-audit-workspace"
    staged_root.mkdir()
    (staged_root / "service.py").write_text(source, encoding="utf-8")
    generated_test = staged_root / generated_call["path"]
    generated_test.parent.mkdir(parents=True, exist_ok=True)
    generated_test.write_text(generated_call["content"], encoding="utf-8")
    fixture_root = tmp_path / "managed-external-side-effects"
    fixture_root.mkdir()
    environment = dict(os.environ)
    if fixture_mounted:
        environment["UNAI_SIDE_EFFECTS_ROOT"] = str(fixture_root)
    else:
        environment.pop("UNAI_SIDE_EFFECTS_ROOT", None)

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(generated_test)],
        cwd=staged_root,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        env=environment,
    )
    trace_path = staged_root / generated_call["metadata"]["replay_trace_relative_path"]
    assert trace_path.is_file()
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    record = trace["records"][0]
    return completed, record, fixture_root


def test_external_side_effect_fixture_audit_passes_without_mutation(tmp_path: Path) -> None:
    completed, record, fixture_root = _execute_external_fixture_harness(
        tmp_path,
        source=(
            "def clamp(value):\n"
            "    return value\n"
        ),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert record["kind"] == "external_fixture_filesystem_unchanged"
    assert record["status"] == "complete"
    assert record["unchanged"] is True
    assert record["changed_entry_count"] == 0
    assert record["before_sha256"] == record["after_sha256"]
    assert list(fixture_root.iterdir()) == []


def test_external_side_effect_fixture_audit_detects_managed_mount_writes(tmp_path: Path) -> None:
    completed, record, fixture_root = _execute_external_fixture_harness(
        tmp_path,
        source=(
            "import os\n"
            "from pathlib import Path\n"
            "def clamp(value):\n"
            "    root = Path(os.environ['UNAI_SIDE_EFFECTS_ROOT'])\n"
            "    (root / 'outbox.json').write_text(str(value), encoding='utf-8')\n"
            "    return value\n"
        ),
    )

    assert completed.returncode != 0
    assert "UNAI_COUNTEREXAMPLE:" in completed.stdout + completed.stderr
    assert record["kind"] == "external_fixture_filesystem_unchanged"
    assert record["unchanged"] is False
    assert record["changed_entry_count"] >= 1
    assert record["before_sha256"] != record["after_sha256"]
    assert (fixture_root / "outbox.json").is_file()
    assert "outbox.json" not in json.dumps(record)


def test_external_fixture_audit_does_not_ignore_python_cache_named_directories(tmp_path: Path) -> None:
    completed, record, fixture_root = _execute_external_fixture_harness(
        tmp_path,
        source=(
            "import os\n"
            "from pathlib import Path\n"
            "def clamp(value):\n"
            "    cache = Path(os.environ['UNAI_SIDE_EFFECTS_ROOT']) / '__pycache__'\n"
            "    cache.mkdir()\n"
            "    (cache / 'side-effect.pyc').write_bytes(b'payload')\n"
            "    return value\n"
        ),
    )

    assert completed.returncode != 0
    assert record["kind"] == "external_fixture_filesystem_unchanged"
    assert record["unchanged"] is False
    assert record["changed_entry_count"] >= 2
    assert (fixture_root / "__pycache__" / "side-effect.pyc").is_file()


def test_external_side_effect_fixture_is_unavailable_without_explicit_mount(tmp_path: Path) -> None:
    completed, record, _fixture_root = _execute_external_fixture_harness(
        tmp_path,
        source="def clamp(value):\n    return value\n",
        fixture_mounted=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert record["kind"] == "external_fixture_filesystem_unchanged"
    assert record["status"] == "unavailable"
    assert record["reason_code"] == "fixture_unavailable"


def test_workspace_filesystem_unchanged_passes_without_mutation(tmp_path: Path) -> None:
    completed, record, _staged_root = _execute_filesystem_audit_harness(
        tmp_path,
        source=(
            "def clamp(value):\n"
            "    return value + 1\n"
        ),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert record["unchanged"] is True
    assert record["changed_entry_count"] == 0
    assert record["before_sha256"] == record["after_sha256"]
    assert record["entry_count"] > 0
    assert record["bytes_hashed"] >= 0


def test_workspace_filesystem_unchanged_detects_created_files_without_recording_paths(tmp_path: Path) -> None:
    completed, record, staged_root = _execute_filesystem_audit_harness(
        tmp_path,
        source=(
            "from pathlib import Path\n"
            "def clamp(value):\n"
            "    Path('side-effect.txt').write_text(str(value), encoding='utf-8')\n"
            "    return value\n"
        ),
    )

    assert completed.returncode != 0
    assert "UNAI_COUNTEREXAMPLE:" in completed.stdout + completed.stderr
    assert record["unchanged"] is False
    assert record["changed_entry_count"] >= 1
    assert record["before_sha256"] != record["after_sha256"]
    assert record["changed_paths_sha256"]
    assert (staged_root / "side-effect.txt").is_file()
    assert "side-effect.txt" not in json.dumps(record)


def test_workspace_mutation_is_structured_and_is_not_automatically_shrunk(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "def clamp(value):" + chr(10) + "    return value" + chr(10),
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    runner.status = "failed"
    runner.stdout = (
        "FAILED unai_generated_tests/test_oracle.py::test_grounded_oracle_case[property-negative]\n"
        + "UNAI_COUNTEREXAMPLE:"
        + json.dumps(
            {
                "case_id": "property-negative",
                "actual_type": "WorkspaceMutation",
                "actual_summary": "The target changed the disposable workspace filesystem.",
                "actual_sha256": "a" * 64,
                "filesystem_before_sha256": "b" * 64,
                "filesystem_after_sha256": "c" * 64,
                "filesystem_changed_entry_count": 1,
                "filesystem_changed_paths_sha256": "d" * 64,
                "filesystem_entry_count": 5,
                "filesystem_bytes_hashed": 1024,
            },
            separators=(",", ":"),
        )
    )

    result = verifier.run(
        task_id,
        _property_request(
            fresh,
            property_kind="workspace_filesystem_unchanged",
        ),
        workspace_root,
    )

    assert result.status == "failed"
    assert len(result.counterexamples) == 1
    counterexample = result.counterexamples[0]
    assert counterexample.actual_summary.startswith("WorkspaceMutation:")
    assert counterexample.filesystem_before_sha256 == "b" * 64
    assert counterexample.filesystem_after_sha256 == "c" * 64
    assert counterexample.filesystem_changed_entry_count == 1
    assert counterexample.filesystem_changed_paths_sha256 == "d" * 64
    assert result.shrinking_results == []
    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    evidence = next(item for item in saved.evidence if item.kind == "counterexample")
    assert evidence.metadata["filesystem_changed_entry_count"] == 1
    assert evidence.metadata["filesystem_bytes_hashed"] == 1024


def test_replay_divergence_is_a_structured_counterexample_without_input_shrinking(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "def clamp(value):" + chr(10) + "    return value" + chr(10),
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    runner.status = "failed"
    runner.stdout = (
        "FAILED unai_generated_tests/test_oracle.py::test_grounded_oracle_case[property-negative]\\n"
        + "UNAI_COUNTEREXAMPLE:"
        + json.dumps(
            {
                "case_id": "property-negative",
                "actual_type": "ReplayDivergence",
                "actual_summary": "Repeated identical inputs returned different results.",
                "actual_sha256": "a" * 64,
                "replay_count": 3,
                "replay_trace_sha256": "b" * 64,
            },
            separators=(",", ":"),
        )
    )

    result = verifier.run(
        task_id,
        _property_request(
            fresh,
            property_kind="output_stable_across_replays",
            replay_count=3,
        ),
        workspace_root,
    )

    assert result.status == "failed"
    assert len(result.counterexamples) == 1
    counterexample = result.counterexamples[0]
    assert counterexample.actual_summary.startswith("ReplayDivergence:")
    assert counterexample.replay_count == 3
    assert counterexample.replay_trace_sha256 == "b" * 64
    assert result.shrinking_results == []
    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    evidence = next(item for item in saved.evidence if item.kind == "counterexample")
    assert evidence.metadata["replay_count"] == 3
    assert evidence.metadata["replay_trace_sha256"] == "b" * 64


def test_replay_stability_detects_same_process_global_state_changes(tmp_path: Path) -> None:
    record = _execute_replay_harness(
        tmp_path,
        source=(
            "counter = 0\n"
            "def clamp(value):\n"
            "    global counter\n"
            "    counter += 1\n"
            "    return value + counter\n"
        ),
        value=10,
        expected_stable=False,
    )
    assert [item["replay_index"] for item in record["results"]] == [1, 2, 3]
    assert all(item["status"] == "returned" for item in record["results"])


def test_replay_stability_resets_mutable_json_inputs_but_preserves_module_state(tmp_path: Path) -> None:
    record = _execute_replay_harness(
        tmp_path,
        source=(
            "def clamp(value):\n"
            "    value.append('new')\n"
            "    return len(value)\n"
        ),
        value=["start"],
        expected_stable=True,
    )
    assert len({item["actual_sha256"] for item in record["results"]}) == 1


def test_async_replay_stability_detects_module_state_changes(tmp_path: Path) -> None:
    record = _execute_replay_harness(
        tmp_path,
        source=(
            "counter = 0\n"
            "async def clamp(value):\n"
            "    global counter\n"
            "    counter += 1\n"
            "    return value + counter\n"
        ),
        value=5,
        expected_stable=False,
    )
    assert len({item["actual_sha256"] for item in record["results"]}) == 3


def test_property_oracle_harness_uses_fixed_predicates_without_expected_value(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "def clamp(value):" + chr(10) + "    return -abs(value)" + chr(10),
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    result = verifier.run(task_id, _property_request(fresh), workspace_root)

    assert result.status == "passed"
    generated = runner.calls[0]["content"]
    ast.parse(generated)
    assert "def _property_holds(actual, arguments, property_spec):" in generated
    assert '"output_non_negative"' in generated
    assert "expected_output" not in _generated_cases(generated)[0]
    assert runner.calls[0]["metadata"]["oracle_case_ids"] == ["property-negative"]

    staged_root = tmp_path / "property-staged"
    staged_root.mkdir()
    (staged_root / "service.py").write_text(
        target.read_text(encoding="utf-8"), encoding="utf-8"
    )
    generated_path = staged_root / "unai_generated_tests" / "test_oracle.py"
    generated_path.parent.mkdir()
    generated_path.write_text(generated, encoding="utf-8")
    namespace = {"__file__": str(generated_path)}
    exec(compile(generated, str(generated_path), "exec"), namespace)
    with pytest.raises(AssertionError, match="UNAI_COUNTEREXAMPLE"):
        namespace["test_grounded_oracle_case"](namespace["_CASES"][0])


def test_property_counterexample_is_shrunk_only_when_same_property_still_fails(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "def clamp(value):" + chr(10) + "    return -abs(value)" + chr(10),
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)

    def evaluate_generated_cases(generated_source, evidence_source, evidence_metadata):
        markers = []
        for case in _generated_cases(generated_source):
            value = case["arguments"]["value"]
            actual = -abs(value)
            if actual < 0:
                markers.append(
                    "UNAI_COUNTEREXAMPLE:" + json.dumps(
                        {
                            "case_id": case["case_id"],
                            "actual_type": "int",
                            "actual_summary": str(actual),
                        },
                        separators=(",", ":"),
                    )
                )
        if markers:
            return "failed", chr(10).join(markers), ""
        return "passed", "", ""

    runner.generated_responder = evaluate_generated_cases
    result = verifier.run(task_id, _property_request(fresh), workspace_root)

    assert result.status == "failed"
    assert len(result.counterexamples) == 1
    assert len(runner.calls) == 3
    shrink_cases = _generated_cases(runner.calls[1]["content"])
    assert len(shrink_cases) == 5
    assert {item["arguments"]["value"] for item in shrink_cases} == {0, 1, 2, 4, 8}
    assert all(item["oracle_property"]["kind"] == "output_non_negative" for item in shrink_cases)
    assert all("expected_output" not in item for item in shrink_cases)
    assert runner.calls[1]["metadata"]["parent_execution_evidence_id"] == result.execution_evidence.evidence_id
    next_round = _generated_cases(runner.calls[2]["content"])
    assert [item["arguments"]["value"] for item in next_round] == [0]
    assert runner.calls[2]["metadata"]["previous_shrink_execution_evidence_id"] == "run-2"
    assert runner.calls[2]["metadata"]["shrink_round"] == 2
    shrink = result.shrinking_results[0]
    assert shrink.status == "smaller_failure_observed"
    assert shrink.attempts == 6
    assert shrink.smallest_observed_arguments == {"value": 1}
    assert shrink.smallest_observed_arguments_sha256 == hashlib.sha256(
        b'{"value":1}'
    ).hexdigest()

    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    bundle = build_proof_bundle(saved)
    assert bundle.overall_status == "counterexamples_found"
    assert bundle.shrink_attempt_count == 6
    assert bundle.shrunk_counterexample_count == 1
    assert any(item.source == "grounded-oracle-shrink-sandbox" for item in saved.evidence)
    assert any(item.source == "grounded-shrink-trace" for item in saved.evidence)


def test_cumulative_shrinking_reduces_two_integer_arguments_across_rounds(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "def combine(left, right):" + chr(10)
        + "    return -(abs(left) + abs(right))" + chr(10),
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="combine",
        source_sha256=fresh.sha256,
        cases=[
            GroundedOracleCase(
                case_id="two-argument-property",
                arguments={"left": 5, "right": 4},
                oracle_property=GroundedPropertyOracle(kind="output_non_negative"),
                oracle_kind="user",
                oracle_reference="Combined result must be non-negative",
            )
        ],
    )

    def evaluate_generated_cases(generated_source, evidence_source, evidence_metadata):
        markers = []
        for case in _generated_cases(generated_source):
            args = case["arguments"]
            actual = -(abs(args["left"]) + abs(args["right"]))
            if actual < 0:
                markers.append(
                    "UNAI_COUNTEREXAMPLE:" + json.dumps(
                        {
                            "case_id": case["case_id"],
                            "actual_type": "int",
                            "actual_summary": str(actual),
                        },
                        separators=(",", ":"),
                    )
                )
        if markers:
            return "failed", chr(10).join(markers), ""
        return "passed", "", ""

    runner.generated_responder = evaluate_generated_cases
    result = verifier.run(task_id, request, workspace_root)

    assert result.status == "failed"
    assert len(result.counterexamples) == 1
    assert len(runner.calls) == 4
    rounds = [
        _generated_cases(call["content"])
        for call in runner.calls[1:]
    ]
    assert [len(items) for items in rounds] == [8, 4, 1]
    assert all(item["oracle_property"]["kind"] == "output_non_negative" for items in rounds for item in items)
    shrink = result.shrinking_results[0]
    assert shrink.status == "smaller_failure_observed"
    assert shrink.attempts == 13
    assert shrink.smallest_observed_arguments == {"left": 0, "right": 1}

    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    evidence = next(
        item for item in saved.evidence
        if item.kind == "shrink" and item.metadata.get("case_id") == "two-argument-property"
    )
    assert evidence.metadata["iteration_count"] == 3
    assert len(evidence.metadata["iteration_history"]) == 3
    bundle = build_proof_bundle(saved)
    assert bundle.shrink_attempt_count == 13
    assert bundle.shrunk_counterexample_count == 1


def test_property_contract_rejects_expected_output_and_requires_argument_reference(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    with pytest.raises(GroundedVerificationError, match="must omit expected_output"):
        request = _property_request(snapshot)
        request.cases[0].expected_output = 0
        verifier.run(task_id, request, workspace_root)

    request = _property_request(snapshot, property_kind="output_equals_argument")
    with pytest.raises(GroundedVerificationError, match="must name an argument"):
        verifier.run(task_id, request, workspace_root)
    assert runner.calls == []


def test_exact_oracle_counterexample_does_not_trigger_input_shrinking(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    runner.status = "failed"
    runner.stdout = (
        'UNAI_COUNTEREXAMPLE:{"case_id":"lower-edge","actual_type":"int","actual_summary":"1"}'
    )

    result = verifier.run(task_id, _request(snapshot, expected=0), workspace_root)

    assert result.counterexamples
    assert result.shrinking_results == []
    assert len(runner.calls) == 1


def test_integer_shrink_candidates_are_deterministic_and_never_increase_magnitude() -> None:
    from unai_engine.verification.grounded import _integer_shrink_candidates

    assert _integer_shrink_candidates(9) == [0, 1, 2, 4, 8]
    assert _integer_shrink_candidates(-9) == [0, -1, -2, -4, -8]
    assert _integer_shrink_candidates(0) == []
    assert _integer_shrink_candidates(True) == []



def test_exact_oracle_requires_explicit_expected_output_even_when_null_is_default(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    request = GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="clamp",
        source_sha256=snapshot.sha256,
        cases=[
            GroundedOracleCase(
                case_id="missing-expected",
                arguments={"value": 9},
                oracle_kind="user",
                oracle_reference="Expected output was accidentally omitted",
            )
        ],
    )

    with pytest.raises(GroundedVerificationError, match="must explicitly supply expected_output"):
        verifier.run(task_id, request, workspace_root)

    assert runner.calls == []


def test_property_shrinking_rejects_non_finite_float_arguments(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)

    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(GroundedVerificationError, match="JSON-compatible"):
            verifier.run(task_id, _property_request(snapshot, value=value), workspace_root)

    assert runner.calls == []


def test_expected_exception_property_accepts_only_the_selected_allowlisted_exception(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "def clamp(value):" + chr(10) + "    return int(value)" + chr(10),
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = _property_request(
        fresh,
        value="not-an-integer",
        property_kind="raises_exception",
        exception_name="ValueError",
    )

    result = verifier.run(task_id, request, workspace_root)

    assert result.status == "passed"
    generated = runner.calls[0]["content"]
    assert '"raises_exception"' in generated
    assert '"ValueError"' in generated
    staged_root = tmp_path / "exception-staged"
    staged_root.mkdir()
    (staged_root / "service.py").write_text(
        target.read_text(encoding="utf-8"), encoding="utf-8"
    )
    generated_path = staged_root / "unai_generated_tests" / "test_oracle.py"
    generated_path.parent.mkdir()
    generated_path.write_text(generated, encoding="utf-8")
    namespace = {"__file__": str(generated_path)}
    exec(compile(generated, str(generated_path), "exec"), namespace)
    namespace["test_grounded_oracle_case"](namespace["_CASES"][0])


def test_expected_exception_property_rejects_custom_class_with_allowlisted_name(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "class ValueError(Exception):" + chr(10)
        + "    pass" + chr(10) + chr(10)
        + "def clamp(value):" + chr(10)
        + "    raise ValueError('custom exception')" + chr(10),
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = _property_request(
        fresh,
        property_kind="raises_exception",
        exception_name="ValueError",
    )

    verifier.run(task_id, request, workspace_root)
    generated = runner.calls[0]["content"]
    staged_root = tmp_path / "custom-exception-staged"
    staged_root.mkdir()
    (staged_root / "service.py").write_text(
        target.read_text(encoding="utf-8"), encoding="utf-8"
    )
    generated_path = staged_root / "unai_generated_tests" / "test_oracle.py"
    generated_path.parent.mkdir()
    generated_path.write_text(generated, encoding="utf-8")
    namespace = {"__file__": str(generated_path)}
    exec(compile(generated, str(generated_path), "exec"), namespace)

    with pytest.raises(AssertionError, match="UNAI_COUNTEREXAMPLE"):
        namespace["test_grounded_oracle_case"](namespace["_CASES"][0])


def test_expected_exception_property_rejects_normal_return(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "def clamp(value):" + chr(10) + "    return value" + chr(10),
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = _property_request(
        fresh,
        property_kind="raises_exception",
        exception_name="ValueError",
    )

    verifier.run(task_id, request, workspace_root)
    generated = runner.calls[0]["content"]
    staged_root = tmp_path / "normal-return-staged"
    staged_root.mkdir()
    (staged_root / "service.py").write_text(
        target.read_text(encoding="utf-8"), encoding="utf-8"
    )
    generated_path = staged_root / "unai_generated_tests" / "test_oracle.py"
    generated_path.parent.mkdir()
    generated_path.write_text(generated, encoding="utf-8")
    namespace = {"__file__": str(generated_path)}
    exec(compile(generated, str(generated_path), "exec"), namespace)

    with pytest.raises(AssertionError, match="UNAI_COUNTEREXAMPLE"):
        namespace["test_grounded_oracle_case"](namespace["_CASES"][0])


def test_expected_exception_property_reports_wrong_or_missing_exception(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, _ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "def clamp(value):" + chr(10) + "    return int(value)" + chr(10),
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)
    request = _property_request(
        fresh,
        value="not-an-integer",
        property_kind="raises_exception",
        exception_name="TypeError",
    )

    verifier.run(task_id, request, workspace_root)
    generated = runner.calls[0]["content"]
    staged_root = tmp_path / "wrong-exception-staged"
    staged_root.mkdir()
    (staged_root / "service.py").write_text(
        target.read_text(encoding="utf-8"), encoding="utf-8"
    )
    generated_path = staged_root / "unai_generated_tests" / "test_oracle.py"
    generated_path.parent.mkdir()
    generated_path.write_text(generated, encoding="utf-8")
    namespace = {"__file__": str(generated_path)}
    exec(compile(generated, str(generated_path), "exec"), namespace)

    with pytest.raises(AssertionError, match="UNAI_COUNTEREXAMPLE"):
        namespace["test_grounded_oracle_case"](namespace["_CASES"][0])


def test_expected_exception_property_requires_allowlisted_exception_name(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)

    with pytest.raises(GroundedVerificationError, match="must select an allowlisted exception_name"):
        verifier.run(
            task_id,
            _property_request(snapshot, property_kind="raises_exception"),
            workspace_root,
        )

    with pytest.raises(GroundedVerificationError, match="cannot set exception_name"):
        verifier.run(
            task_id,
            _property_request(snapshot, exception_name="ValueError"),
            workspace_root,
        )

    assert runner.calls == []


def test_expected_exception_property_rejects_unlisted_exception_names(tmp_path: Path) -> None:
    _task_id, _workspace_root, _target, _source, _ledger, snapshot, _probes, runner, _verifier = _setup(tmp_path)

    with pytest.raises(ValidationError):
        _property_request(
            snapshot,
            property_kind="raises_exception",
            exception_name="ImportError",
        )

    assert runner.calls == []



def test_float_shrink_candidates_are_finite_and_strictly_smaller() -> None:
    from unai_engine.verification.grounded import _float_shrink_candidates

    positive = _float_shrink_candidates(9.5)
    negative = _float_shrink_candidates(-9.5)

    assert positive == sorted(set(positive), key=lambda item: (abs(item), item))
    assert negative == sorted(set(negative), key=lambda item: (abs(item), item))
    assert 0.0 in positive and 0.0 in negative
    assert all(math.isfinite(item) and abs(item) < 9.5 for item in positive)
    assert all(math.isfinite(item) and abs(item) < 9.5 for item in negative)
    assert _float_shrink_candidates(0.0) == []
    assert _float_shrink_candidates(-0.0) == []
    assert _float_shrink_candidates(float("nan")) == []
    assert _float_shrink_candidates(float("inf")) == []
    assert _float_shrink_candidates(float("-inf")) == []
    assert _float_shrink_candidates(True) == []


def test_property_counterexample_shrinks_finite_float_arguments_in_sandbox_rounds(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "def clamp(value):" + chr(10) + "    return -abs(value)" + chr(10),
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)

    def evaluate_generated_cases(generated_source, evidence_source, evidence_metadata):
        markers = []
        for case in _generated_cases(generated_source):
            value = case["arguments"]["value"]
            actual = -abs(value)
            if actual < 0:
                markers.append(
                    "UNAI_COUNTEREXAMPLE:" + json.dumps(
                        {
                            "case_id": case["case_id"],
                            "actual_type": "float" if isinstance(actual, float) else "int",
                            "actual_summary": str(actual),
                        },
                        separators=(",", ":"),
                    )
                )
        if markers:
            return "failed", chr(10).join(markers), ""
        return "passed", "", ""

    runner.generated_responder = evaluate_generated_cases
    result = verifier.run(task_id, _property_request(fresh, value=9.5), workspace_root)

    assert result.status == "failed"
    assert len(runner.calls) >= 2
    assert len(runner.calls) <= 1 + 6
    shrink_rounds = [
        _generated_cases(call["content"])
        for call in runner.calls[1:]
    ]
    all_candidates = [case for round_cases in shrink_rounds for case in round_cases]
    assert all_candidates
    assert len(all_candidates) <= 24
    assert all(
        isinstance(case["arguments"]["value"], float)
        and math.isfinite(case["arguments"]["value"])
        and abs(case["arguments"]["value"]) < 9.5
        for case in all_candidates
    )
    shrink = result.shrinking_results[0]
    assert shrink.status == "smaller_failure_observed"
    assert isinstance(shrink.smallest_observed_arguments["value"], float)
    assert 0.0 < abs(shrink.smallest_observed_arguments["value"]) < 9.5
    assert shrink.attempts == len(all_candidates)

    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    evidence = next(
        item for item in saved.evidence
        if item.kind == "shrink" and item.metadata.get("case_id") == "property-negative"
    )
    assert evidence.metadata["status"] == "smaller_failure_observed"
    assert evidence.metadata["attempts"] <= 24



def test_string_shrink_candidates_are_bounded_and_strictly_shorter() -> None:
    from unai_engine.verification.grounded import _string_shrink_candidates

    candidates = _string_shrink_candidates("abcdefgh")
    assert candidates == sorted(set(candidates), key=lambda item: (len(item), item))
    assert "" in candidates
    assert 0 < len(candidates) <= 12
    assert all(isinstance(item, str) and len(item) < len("abcdefgh") for item in candidates)
    assert _string_shrink_candidates("") == []
    assert _string_shrink_candidates("x") == [""]


def test_property_counterexample_shrinks_strings_in_bounded_sandbox_rounds(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "def clamp(value):" + chr(10) + "    return -len(value)" + chr(10),
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)

    def evaluate_generated_cases(generated_source, evidence_source, evidence_metadata):
        markers = []
        for case in _generated_cases(generated_source):
            value = case["arguments"]["value"]
            actual = -len(value)
            if actual < 0:
                markers.append(
                    "UNAI_COUNTEREXAMPLE:" + json.dumps(
                        {
                            "case_id": case["case_id"],
                            "actual_type": "int",
                            "actual_summary": str(actual),
                        },
                        separators=(",", ":"),
                    )
                )
        if markers:
            return "failed", chr(10).join(markers), ""
        return "passed", "", ""

    runner.generated_responder = evaluate_generated_cases
    result = verifier.run(task_id, _property_request(fresh, value="abcdefgh"), workspace_root)

    assert result.status == "failed"
    assert len(runner.calls) >= 2
    assert len(runner.calls) <= 1 + 6
    rounds = [_generated_cases(call["content"]) for call in runner.calls[1:]]
    candidates = [case for round_cases in rounds for case in round_cases]
    assert candidates
    assert len(candidates) <= 24
    assert all(
        isinstance(case["arguments"]["value"], str)
        and len(case["arguments"]["value"]) < len("abcdefgh")
        for case in candidates
    )
    shrink = result.shrinking_results[0]
    assert shrink.status == "smaller_failure_observed"
    assert isinstance(shrink.smallest_observed_arguments["value"], str)
    assert 0 < len(shrink.smallest_observed_arguments["value"]) < len("abcdefgh")
    assert shrink.attempts == len(candidates)

    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    evidence = next(
        item for item in saved.evidence
        if item.kind == "shrink" and item.metadata.get("case_id") == "property-negative"
    )
    assert evidence.metadata["status"] == "smaller_failure_observed"
    assert evidence.metadata["attempts"] <= 24





def test_nested_shrink_candidates_reduce_values_inside_json_containers() -> None:
    from unai_engine.verification.grounded import (
        _argument_measure,
        _nested_argument_shrink_candidates,
    )

    original = {
        "payload": {
            "items": [1, 2, 3],
            "caption": "abcdef",
        }
    }
    candidates = _nested_argument_shrink_candidates(original)

    assert 0 < len(candidates) <= 12
    assert all(_argument_measure(item) < _argument_measure(original) for item in candidates)
    assert any(
        item.get("payload", {}).get("items") != original["payload"]["items"]
        for item in candidates
    )
    assert any(
        item.get("payload", {}).get("caption") != original["payload"]["caption"]
        for item in candidates
    )
    assert all(isinstance(item, dict) for item in candidates)


def test_nested_collection_counterexample_is_shrunk_inside_disposable_sandbox(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "def clamp(value):" + chr(10)
        + "    items = value.get('payload', {}).get('items', [])" + chr(10)
        + "    return -len(items)" + chr(10),
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)

    def evaluate_generated_cases(generated_source, evidence_source, evidence_metadata):
        markers = []
        for case in _generated_cases(generated_source):
            items = case["arguments"]["value"].get("payload", {}).get("items", [])
            actual = -len(items)
            if actual < 0:
                markers.append(
                    "UNAI_COUNTEREXAMPLE:" + json.dumps(
                        {
                            "case_id": case["case_id"],
                            "actual_type": "int",
                            "actual_summary": str(actual),
                        },
                        separators=(",", ":"),
                    )
                )
        if markers:
            return "failed", chr(10).join(markers), ""
        return "passed", "", ""

    runner.generated_responder = evaluate_generated_cases
    original_value = {"payload": {"items": ["a", "b", "c"]}}
    result = verifier.run(
        task_id,
        _property_request(fresh, value=original_value),
        workspace_root,
    )

    assert result.status == "failed"
    shrink = result.shrinking_results[0]
    assert shrink.status == "smaller_failure_observed"
    assert shrink.smallest_observed_arguments is not None
    smallest_value = shrink.smallest_observed_arguments["value"]
    assert set(smallest_value) <= {"payload"}
    assert len(smallest_value.get("payload", {}).get("items", [])) == 1
    assert shrink.attempts <= 24
    assert 1 <= len(runner.calls) - 1 <= 6
    assert all(call["metadata"]["sandboxed"] if "sandboxed" in call["metadata"] else True for call in runner.calls[1:])

    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    shrink_events = [
        item for item in saved.evidence
        if item.kind == "shrink" and item.source == "grounded-property-shrinker"
    ]
    assert len(shrink_events) == 1
    assert shrink_events[0].metadata["attempts"] == shrink.attempts
    assert shrink_events[0].metadata["iteration_count"] <= 6
    assert build_proof_bundle(saved).shrink_attempt_count == shrink.attempts


def test_collection_shrink_candidates_only_remove_top_level_items() -> None:
    from unai_engine.verification.grounded import _collection_shrink_candidates

    original_list = [{"nested": [1, 2]}, {"nested": [3, 4]}, {"nested": [5, 6]}, {"nested": [7, 8]}]
    list_candidates = _collection_shrink_candidates(original_list)
    assert [] in list_candidates
    assert 0 < len(list_candidates) <= 12
    assert all(
        isinstance(candidate, list)
        and len(candidate) < len(original_list)
        and all(item in original_list for item in candidate)
        for candidate in list_candidates
    )

    original_dict = {"a": [1, 2], "b": [3, 4], "c": [5, 6], "d": [7, 8]}
    dict_candidates = _collection_shrink_candidates(original_dict)
    assert {} in dict_candidates
    assert 0 < len(dict_candidates) <= 12
    assert all(
        isinstance(candidate, dict)
        and len(candidate) < len(original_dict)
        and all(original_dict[key] == value for key, value in candidate.items())
        for candidate in dict_candidates
    )
    assert _collection_shrink_candidates({1: "not-json-object-key"}) == []
    assert _collection_shrink_candidates([]) == []
    assert _collection_shrink_candidates({}) == []


def test_property_counterexample_shrinks_list_arguments_in_bounded_sandbox_rounds(tmp_path: Path) -> None:
    task_id, workspace_root, target, _source, ledger, _snapshot, _probes, runner, verifier = _setup(tmp_path)
    target.write_text(
        "def clamp(value):" + chr(10) + "    return -len(value)" + chr(10),
        encoding="utf-8",
    )
    fresh = verifier.patch_manager.read_file(task_id, "service.py", workspace_root)

    def evaluate_generated_cases(generated_source, evidence_source, evidence_metadata):
        markers = []
        for case in _generated_cases(generated_source):
            actual = -len(case["arguments"]["value"])
            if actual < 0:
                markers.append(
                    "UNAI_COUNTEREXAMPLE:" + json.dumps(
                        {
                            "case_id": case["case_id"],
                            "actual_type": "int",
                            "actual_summary": str(actual),
                        },
                        separators=(",", ":"),
                    )
                )
        if markers:
            return "failed", chr(10).join(markers), ""
        return "passed", "", ""

    runner.generated_responder = evaluate_generated_cases
    result = verifier.run(
        task_id,
        _property_request(fresh, value=[1, 2, 3, 4, 5, 6, 7, 8]),
        workspace_root,
    )

    assert result.status == "failed"
    assert len(runner.calls) >= 2
    assert len(runner.calls) <= 1 + 6
    rounds = [_generated_cases(call["content"]) for call in runner.calls[1:]]
    candidates = [case for round_cases in rounds for case in round_cases]
    assert candidates
    assert len(candidates) <= 24
    from unai_engine.verification.grounded import _argument_measure

    original_value = [1, 2, 3, 4, 5, 6, 7, 8]
    assert all(
        isinstance(case["arguments"]["value"], list)
        and len(case["arguments"]["value"]) <= len(original_value)
        and _argument_measure(case["arguments"]["value"]) < _argument_measure(original_value)
        for case in candidates
    )
    shrink = result.shrinking_results[0]
    assert shrink.status == "smaller_failure_observed"
    assert isinstance(shrink.smallest_observed_arguments["value"], list)
    assert 0 < len(shrink.smallest_observed_arguments["value"]) <= 8
    assert _argument_measure(shrink.smallest_observed_arguments["value"]) < _argument_measure(original_value)
    assert shrink.attempts == len(candidates)

    saved = ledger.get_snapshot(task_id)
    assert saved is not None
    evidence = next(
        item for item in saved.evidence
        if item.kind == "shrink" and item.metadata.get("case_id") == "property-negative"
    )
    assert evidence.metadata["status"] == "smaller_failure_observed"
    assert evidence.metadata["attempts"] <= 24



def _transparency_root(leaves: list[bytes]) -> bytes:
    if len(leaves) == 1:
        return leaf_hash(leaves[0])
    split = 1 << ((len(leaves) - 1).bit_length() - 1)
    return node_hash(_transparency_root(leaves[:split]), _transparency_root(leaves[split:]))


def _publisher_transparency_request(task_id: str, snapshot, spec_content: str):
    request, publisher_trust, _signature = _publisher_signed_request(
        task_id, snapshot, spec_content=spec_content
    )
    publisher = request.cases[0].oracle_publisher_attestation
    statement = build_publisher_artifact_statement(
        key_id=publisher.key_id,
        issuer=publisher.issuer,
        issued_at=publisher.issued_at,
        artifact_uri=publisher.artifact_uri,
        artifact_revision=publisher.artifact_revision,
        artifact_sha256=hashlib.sha256(spec_content.encode("utf-8")).hexdigest(),
    )
    manifest = publisher_manifest_leaf(statement)
    leaves = [b"prior-log-entry", manifest]
    root = _transparency_root(leaves)
    log_private = Ed25519PrivateKey.generate()
    log_public = base64.b64encode(
        log_private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    timestamp = int(datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc).timestamp())
    message = sign_tree_head_message(
        log_id="acme-transparency",
        key_id="acme-log-key-2026",
        timestamp=timestamp,
        tree_size=2,
        root_hash=root.hex(),
    )
    head = SignedTreeHead(
        log_id="acme-transparency",
        key_id="acme-log-key-2026",
        timestamp=timestamp,
        tree_size=2,
        root_hash=root.hex(),
        signature_b64=base64.b64encode(log_private.sign(message)).decode("ascii"),
    )
    witness_private = Ed25519PrivateKey.generate()
    witness_public = base64.b64encode(
        witness_private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    witness_signature = base64.b64encode(
        witness_private.sign(canonical_attestation_message(build_transparency_witness_statement(head)))
    ).decode("ascii")
    publisher.transparency = GroundedOracleTransparencyProof(
        tree_head=GroundedOracleTransparencyTreeHead(
            log_id=head.log_id,
            key_id=head.key_id,
            timestamp=head.timestamp,
            tree_size=head.tree_size,
            root_hash=head.root_hash,
            signature_b64=head.signature_b64,
        ),
        leaf_index=1,
        inclusion_proof=[leaf_hash(leaves[0]).hex()],
        witnesses=[
            GroundedOracleTransparencyWitness(
                key_id="acme-witness-key-2026",
                signature_b64=witness_signature,
            )
        ],
    )
    return request, publisher_trust, {
        "acme-log-key-2026": {
            "public_key_b64": log_public,
            "log_id": "acme-transparency",
        }
    }, {
        "acme-witness-key-2026": {
            "public_key_b64": witness_public,
            "witness_id": "independent-witness-a",
        }
    }, head, log_private, witness_private


def _configure_transparency_verifier(verifier, publisher_trust, log_keys, witness_keys, tmp_path):
    verifier.trusted_oracle_publisher_keys = {"acme-specs-2026": publisher_trust}
    verifier.trusted_oracle_transparency_log_keys = log_keys
    verifier.trusted_oracle_transparency_witness_keys = witness_keys
    verifier.oracle_transparency_required = True
    verifier.oracle_transparency_min_witnesses = 1
    verifier.transparency_checkpoint_store = TransparencyCheckpointStore(
        tmp_path / "publisher-transparency.sqlite3"
    )


def test_publisher_oracle_requires_and_records_verified_transparency(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    spec_content = "# Trusted release\\nThe lower clamp boundary is zero.\\n"
    (workspace_root / "demo" / "product-spec.md").write_text(spec_content, encoding="utf-8")
    request, publisher_trust, log_keys, witness_keys, head, _log_private, _witness_private = _publisher_transparency_request(
        task_id, snapshot, spec_content
    )
    _configure_transparency_verifier(verifier, publisher_trust, log_keys, witness_keys, tmp_path)

    result = verifier.run(task_id, request, workspace_root)

    assert result.status == "passed"
    metadata = result.oracle_evidence[0].metadata
    assert metadata["oracle_artifact_integrity"] == "trusted-ed25519-publisher-transparency"
    provenance = metadata["oracle_publisher_attestation"]["transparency"]
    assert provenance["verified"] is True
    assert provenance["log_id"] == head.log_id
    assert provenance["tree_size"] == head.tree_size
    assert provenance["distinct_witness_count"] == 1
    assert provenance["checkpoint_newly_accepted"] is True
    assert verifier.transparency_checkpoint_store.latest(head.log_id)["root_hash"] == head.root_hash
    assert head.signature_b64 not in runner.calls[0]["content"]


def test_publisher_transparency_rejects_tampered_inclusion_before_execution(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    spec_content = "# Trusted release\\nThe lower clamp boundary is zero.\\n"
    (workspace_root / "demo" / "product-spec.md").write_text(spec_content, encoding="utf-8")
    request, publisher_trust, log_keys, witness_keys, _head, _log_private, _witness_private = _publisher_transparency_request(
        task_id, snapshot, spec_content
    )
    _configure_transparency_verifier(verifier, publisher_trust, log_keys, witness_keys, tmp_path)
    request.cases[0].oracle_publisher_attestation.transparency.inclusion_proof[0] = "00" * 32

    with pytest.raises(GroundedVerificationError, match="inclusion proof or signed tree head is invalid"):
        verifier.run(task_id, request, workspace_root)

    assert runner.calls == []


def test_publisher_transparency_rejects_cross_log_key_substitution(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    spec_content = "# Trusted release\\nThe lower clamp boundary is zero.\\n"
    (workspace_root / "demo" / "product-spec.md").write_text(spec_content, encoding="utf-8")
    request, publisher_trust, log_keys, witness_keys, _head, _log_private, _witness_private = _publisher_transparency_request(
        task_id, snapshot, spec_content
    )
    log_keys["acme-log-key-2026"]["log_id"] = "attacker-log"
    _configure_transparency_verifier(verifier, publisher_trust, log_keys, witness_keys, tmp_path)

    with pytest.raises(GroundedVerificationError, match="identity does not match"):
        verifier.run(task_id, request, workspace_root)

    assert runner.calls == []


def test_publisher_transparency_checkpoint_rejects_rollback(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)
    spec_content = "# Trusted release\\nThe lower clamp boundary is zero.\\n"
    (workspace_root / "demo" / "product-spec.md").write_text(spec_content, encoding="utf-8")
    request, publisher_trust, log_keys, witness_keys, head, log_private, witness_private = _publisher_transparency_request(
        task_id, snapshot, spec_content
    )
    _configure_transparency_verifier(verifier, publisher_trust, log_keys, witness_keys, tmp_path)
    verifier.run(task_id, request, workspace_root)
    assert verifier.transparency_checkpoint_store.latest(head.log_id)["tree_size"] == 2
    calls_before_rollback = len(runner.calls)

    publisher = request.cases[0].oracle_publisher_attestation
    statement = build_publisher_artifact_statement(
        key_id=publisher.key_id,
        issuer=publisher.issuer,
        issued_at=publisher.issued_at,
        artifact_uri=publisher.artifact_uri,
        artifact_revision=publisher.artifact_revision,
        artifact_sha256=hashlib.sha256(spec_content.encode("utf-8")).hexdigest(),
    )
    older_root = leaf_hash(publisher_manifest_leaf(statement))
    older_timestamp = head.timestamp - 10
    older_message = sign_tree_head_message(
        log_id=head.log_id,
        key_id=head.key_id,
        timestamp=older_timestamp,
        tree_size=1,
        root_hash=older_root.hex(),
    )
    older_head = SignedTreeHead(
        log_id=head.log_id,
        key_id=head.key_id,
        timestamp=older_timestamp,
        tree_size=1,
        root_hash=older_root.hex(),
        signature_b64=base64.b64encode(log_private.sign(older_message)).decode("ascii"),
    )
    older_witness_signature = base64.b64encode(
        witness_private.sign(canonical_attestation_message(build_transparency_witness_statement(older_head)))
    ).decode("ascii")
    publisher.transparency = GroundedOracleTransparencyProof(
        tree_head=GroundedOracleTransparencyTreeHead(
            log_id=older_head.log_id,
            key_id=older_head.key_id,
            timestamp=older_head.timestamp,
            tree_size=older_head.tree_size,
            root_hash=older_head.root_hash,
            signature_b64=older_head.signature_b64,
        ),
        leaf_index=0,
        inclusion_proof=[],
        witnesses=[GroundedOracleTransparencyWitness(
            key_id="acme-witness-key-2026",
            signature_b64=older_witness_signature,
        )],
    )

    with pytest.raises(GroundedVerificationError, match="checkpoint rejected: Tree-head rollback detected"):
        verifier.run(task_id, request, workspace_root)

    assert len(runner.calls) == calls_before_rollback
    assert verifier.transparency_checkpoint_store.latest(head.log_id)["tree_size"] == 2


def test_recursive_shrinker_supports_eight_nested_containers_but_not_nine() -> None:
    from unai_engine.verification.grounded import (
        _argument_measure,
        _nested_argument_shrink_candidates,
    )

    def nested(levels: int) -> dict | int:
        value: dict | int = 11
        for _ in range(levels):
            value = {"child": value}
        return value

    def leaf_if_present(value: dict | int, levels: int) -> int | None:
        current = value
        for _ in range(levels):
            if not isinstance(current, dict) or "child" not in current:
                return None
            current = current["child"]
        return current if type(current) is int else None

    depth_eight = nested(8)
    eight_candidates = _nested_argument_shrink_candidates(depth_eight)
    assert eight_candidates
    assert any(
        (reduced := leaf_if_present(candidate, 8)) is not None and reduced != 11
        for candidate in eight_candidates
    )
    assert all(
        _argument_measure(candidate) < _argument_measure(depth_eight)
        for candidate in eight_candidates
    )

    depth_nine = nested(9)
    nine_candidates = _nested_argument_shrink_candidates(depth_nine)
    # A candidate may remove a containing key, but the value beyond the depth
    # boundary must never be changed while its full path remains present.
    for candidate in nine_candidates:
        candidate_leaf = leaf_if_present(candidate, 9)
        if candidate_leaf is not None:
            assert candidate_leaf == 11


def test_recursive_shrinker_enforces_hard_visited_node_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    import unai_engine.verification.grounded as grounded

    monkeypatch.setattr(grounded, "_MAX_NESTED_SHRINK_NODES", 9)
    monkeypatch.setattr(grounded, "_MAX_NESTED_CANDIDATES_PER_ARGUMENT", 100)
    original = grounded._direct_value_shrink_candidates
    visited = 0

    def tracked(value: object) -> list[object]:
        nonlocal visited
        visited += 1
        return original(value)

    monkeypatch.setattr(grounded, "_direct_value_shrink_candidates", tracked)
    value = {f"item-{index:03d}": index + 10 for index in range(80)}
    candidates = grounded._nested_argument_shrink_candidates(value)

    assert visited == 9
    assert len(candidates) <= 100
    assert all(
        grounded._argument_measure(candidate) < grounded._argument_measure(value)
        for candidate in candidates
    )


def test_recursive_shrinker_is_deterministic_non_mutating_and_type_safe() -> None:
    from unai_engine.verification.grounded import (
        _argument_measure,
        _direct_value_shrink_candidates,
        _nested_argument_shrink_candidates,
    )

    original = {
        "payload": {
            "values": [-9, 2.5, True, None],
            "label": "unicode-λ-🙂-value",
        }
    }
    before = json.loads(json.dumps(original, ensure_ascii=False))

    first = _nested_argument_shrink_candidates(original)
    second = _nested_argument_shrink_candidates(original)

    assert first == second
    assert original == before
    assert 0 < len(first) <= 12
    assert all(
        _argument_measure(candidate) < _argument_measure(original)
        for candidate in first
    )
    assert _direct_value_shrink_candidates(True) == []
    assert _direct_value_shrink_candidates(None) == []
    assert all(type(item) is int for item in _direct_value_shrink_candidates(-9))
    assert all(type(item) is float for item in _direct_value_shrink_candidates(2.5))
    assert all(type(item) is str for item in _direct_value_shrink_candidates("abc"))


from __future__ import annotations

import ast
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from unai_engine.cognition.task_compiler import compile_task
from unai_engine.models import (
    EvidenceRecord,
    GroundedOracleCase,
    GroundedPropertyOracle,
    GroundedTestApprovalRequest,
    TaskCompileRequest,
    TaskTestRunResponse,
)
from unai_engine.runtime.patches import PatchManager
from unai_engine.runtime.sandbox import ExecutionResult
from unai_engine.runtime.test_runner import IsolatedPythonTestRunner, WorkspaceStagingError
from unai_engine.verification.grounded import (
    GroundedTestVerifier,
    GroundedVerificationError,
    build_proof_bundle,
)
from unai_engine.verification.ledger import EvidenceLedger
from unai_engine.verification.probes import BoundaryProbeGenerator


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
    assert any("Oracle provenance" in risk for risk in bundle.unresolved_risks)


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
    assert "os" not in imported


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
    assert "import asyncio\nimport hashlib\nimport importlib.util" in generated
    assert "asyncio.run(_FUNCTION(**case['arguments']))" in generated
    assert runner.calls[0]["metadata"]["target_is_async"] is True


def test_sync_grounded_harness_records_target_kind(tmp_path: Path) -> None:
    task_id, workspace_root, _target, _source, _ledger, snapshot, _probes, runner, verifier = _setup(tmp_path)

    verifier.run(task_id, _request(snapshot), workspace_root)

    assert runner.calls[0]["metadata"]["target_is_async"] is False


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

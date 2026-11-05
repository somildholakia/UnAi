from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from unai_engine.cognition.task_compiler import compile_task
from unai_engine.models import (
    EvidenceRecord,
    GroundedOracleCase,
    GroundedTestApprovalRequest,
    TaskCompileRequest,
    TaskTestRunResponse,
)
from unai_engine.runtime.patches import PatchManager
from unai_engine.runtime.sandbox import DockerSandbox
from unai_engine.runtime.test_runner import (
    IsolatedPythonTestRunner,
    _validated_coverage_trace,
)
from unai_engine.verification.coverage import (
    CoveragePlanningError,
    analyze_coverage,
    build_coverage_plan,
)
from unai_engine.verification.grounded import (
    GroundedTestVerifier,
    _generated_pytest,
    _signature_plan,
    _target_descriptor,
    build_proof_bundle,
)
from unai_engine.verification.ledger import EvidenceLedger


SOURCE = (
    "def classify(value):\n"
    "    if value < 0:\n"
    "        return \"negative\"\n"
    "    else:\n"
    "        return \"non-negative\"\n"
)


def _function(source: str = SOURCE, name: str = "classify"):
    tree = ast.parse(source)
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_plan_reports_statement_lines_and_supported_arms() -> None:
    plan = build_coverage_plan(SOURCE, _function())

    assert plan.coverable_lines == (2, 3, 5)
    assert plan.truncated is False
    assert len(plan.branches) == 1
    branch = plan.branches[0]
    assert branch.branch_id == "branch-001"
    assert branch.kind == "if"
    assert branch.line == 2
    assert branch.condition == "value < 0"
    assert branch.then_lines == frozenset({3})
    assert branch.else_lines == frozenset({5})


def test_plan_excludes_nested_scope_bodies_and_docstrings() -> None:
    source = (
        "def target(value):\n"
        "    \"\"\"Docstring with if and while words.\"\"\"\n"
        "    def inner(number):\n"
        "        if number:\n"
        "            return 1\n"
        "        return 2\n"
        "    result = 0\n"
        "    if value:\n"
        "        result = inner(value)\n"
        "    return result\n"
    )

    plan = build_coverage_plan(source, _function(source, "target"))

    assert 2 not in plan.coverable_lines
    assert 3 in plan.coverable_lines
    assert 4 not in plan.coverable_lines
    assert 5 not in plan.coverable_lines
    assert 6 not in plan.coverable_lines
    assert plan.branches and len(plan.branches) == 1
    assert plan.branches[0].line == 8


def test_plan_honors_supported_branch_bounds() -> None:
    source = "def target(value):\n" + "".join(
        f"    if value == {index}:\n        return {index}\n"
        for index in range(205)
    )
    plan = build_coverage_plan(source, _function(source, "target"))

    assert len(plan.branches) == 200
    assert plan.truncated is True


def test_analysis_scores_only_observable_lines_and_arms() -> None:
    plan = build_coverage_plan(SOURCE, _function())
    result = analyze_coverage(
        plan,
        {2, 3},
        {"negative": [2, 3]},
        target_file="service.py",
        symbol_name="classify",
        source_sha256=_digest(SOURCE),
        trace_sha256="a" * 64,
    )

    assert result.status == "complete"
    assert result.coverable_line_count == 3
    assert result.executed_line_count == 2
    assert result.missing_lines == [5]
    assert result.missing_line_count == 1
    assert result.line_coverage == pytest.approx(2 / 3)
    assert result.supported_branch_arms == 2
    assert result.observed_branch_arms == 1
    assert result.branch_coverage == pytest.approx(0.5)
    assert result.branches[0].then_observed is True
    assert result.branches[0].else_observed is False
    assert result.cases_measured == 1


def test_if_without_else_does_not_claim_a_false_arm() -> None:
    source = (
        "def target(value):\n"
        "    if value < 0:\n"
        "        return -1\n"
        "    return value\n"
    )
    plan = build_coverage_plan(source, _function(source, "target"))
    result = analyze_coverage(
        plan,
        {2, 3},
        {"negative": [2, 3]},
        target_file="service.py",
        symbol_name="target",
        source_sha256=_digest(source),
        trace_sha256="b" * 64,
    )

    assert result.branches[0].else_observed is None
    assert result.supported_branch_arms == 1
    assert result.observed_branch_arms == 1
    assert result.branch_coverage == 1.0


def test_plan_rejects_invalid_targets() -> None:
    with pytest.raises(CoveragePlanningError):
        build_coverage_plan("", _function())
    with pytest.raises(CoveragePlanningError):
        build_coverage_plan("x = 1\n", ast.parse("x = 1\n").body[0])  # type: ignore[arg-type]


def test_generated_harness_writes_a_bounded_line_trace(tmp_path: Path) -> None:
    descriptor = _target_descriptor(SOURCE, "classify")
    plan = _signature_plan(descriptor["function"])
    cases = [
        {
            "case_id": "negative",
            "arguments": {"value": -1},
            "expected_output": "negative",
            "oracle_property": None,
            "oracle_artifact_path": None,
            "oracle_artifact_sha256": None,
            "_unai_signature": plan,
            "_unai_constructor_signature": None,
        },
        {
            "case_id": "non-negative",
            "arguments": {"value": 4},
            "expected_output": "non-negative",
            "oracle_property": None,
            "oracle_artifact_path": None,
            "oracle_artifact_sha256": None,
            "_unai_signature": plan,
            "_unai_constructor_signature": None,
        },
    ]
    generated = _generated_pytest(
        "service.py",
        "classify",
        cases,
        _digest(SOURCE),
        coverage_trace_path=(
            "unai_generated_tests/test_oracle_coverage.coverage.json"
        ),
    )

    assert "sys.settrace" in generated
    assert ".coverage.json" in generated

    workspace = tmp_path / "harness-workspace"
    workspace.mkdir()
    (workspace / "service.py").write_text(SOURCE, encoding="utf-8")
    generated_test = workspace / "unai_generated_tests" / "test_oracle_coverage.py"
    generated_test.parent.mkdir(parents=True)
    generated_test.write_text(generated, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(generated_test)],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    payload = json.loads(
        (
            workspace
            / "unai_generated_tests"
            / "test_oracle_coverage.coverage.json"
        ).read_text(encoding="utf-8")
    )
    assert payload["schema"] == "unai.coverage-trace.v1"
    assert payload["target_file"] == "service.py"
    assert payload["target_sha256"] == _digest(SOURCE)
    assert payload["executed_lines"] == [2, 3, 5]
    assert payload["case_lines"] == {
        "negative": [2, 3],
        "non-negative": [2, 5],
    }


def _write_coverage_trace(
    workspace: Path,
    payload: dict,
    name: str = "test_oracle.coverage.json",
) -> str:
    directory = workspace / "unai_generated_tests"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(
        json.dumps(payload, sort_keys=True), encoding="utf-8"
    )
    return f"unai_generated_tests/{name}"


def test_runner_coverage_trace_validation_normalizes_and_rejects_unsafe_input(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source_sha256 = _digest(SOURCE)
    valid_payload = {
        "schema": "unai.coverage-trace.v1",
        "target_file": "service.py",
        "target_sha256": source_sha256,
        "executed_lines": [5, 2, 5],
        "case_lines": {"negative": [3, 2], "non-negative": [5, 5]},
    }
    relative = _write_coverage_trace(workspace, valid_payload)
    record, status = _validated_coverage_trace(
        workspace,
        relative,
        ["negative", "non-negative"],
        "service.py",
        source_sha256,
    )
    assert status == "complete"
    assert record is not None
    assert record["executed_lines"] == [2, 5]
    assert record["case_lines"] == {
        "negative": [2, 3],
        "non-negative": [5],
    }
    assert len(record["trace_sha256"]) == 64

    for relative_path, case_ids, target_sha256 in (
        ("../test_oracle.coverage.json", ["negative"], source_sha256),
        ("unai_generated_tests/other.json", ["negative"], source_sha256),
    ):
        invalid, invalid_status = _validated_coverage_trace(
            workspace,
            relative_path,
            case_ids,
            "service.py",
            target_sha256,
        )
        assert invalid is None
        assert invalid_status in {"invalid_path", "missing"}

    bad_case_payload = {**valid_payload, "case_lines": {"unknown": [2]}}
    bad_case_relative = _write_coverage_trace(
        workspace, bad_case_payload, "bad_case.coverage.json"
    )
    _, bad_case_status = _validated_coverage_trace(
        workspace,
        bad_case_relative,
        ["negative", "non-negative"],
        "service.py",
        source_sha256,
    )
    assert bad_case_status == "invalid"

    bad_line_payload = {**valid_payload, "executed_lines": [0]}
    bad_line_relative = _write_coverage_trace(
        workspace, bad_line_payload, "bad_line.coverage.json"
    )
    _, bad_line_status = _validated_coverage_trace(
        workspace,
        bad_line_relative,
        ["negative", "non-negative"],
        "service.py",
        source_sha256,
    )
    assert bad_line_status == "invalid"

    wrong_hash_payload = {**valid_payload, "target_sha256": "c" * 64}
    wrong_hash_relative = _write_coverage_trace(
        workspace, wrong_hash_payload, "wrong_hash.coverage.json"
    )
    _, wrong_hash_status = _validated_coverage_trace(
        workspace,
        wrong_hash_relative,
        ["negative", "non-negative"],
        "service.py",
        source_sha256,
    )
    assert wrong_hash_status == "invalid"

    oversized = workspace / "unai_generated_tests" / "oversized.coverage.json"
    oversized.write_text(" " * 262_145, encoding="utf-8")
    _, oversized_status = _validated_coverage_trace(
        workspace,
        "unai_generated_tests/oversized.coverage.json",
        ["negative"],
        "service.py",
        source_sha256,
    )
    assert oversized_status == "oversized"


class FakeCoverageRunner:
    def __init__(
        self,
        ledger: EvidenceLedger,
        *,
        trace_status: str = "complete",
    ) -> None:
        self.ledger = ledger
        self.trace_status = trace_status
        self.calls: list[dict] = []

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
        self.calls.append(
            {
                "path": generated_test_path,
                "content": generated_test_content,
                "metadata": evidence_metadata,
            }
        )
        metadata = {
            **evidence_metadata,
            "sandboxed": True,
            "exit_code": 0,
            "timed_out": False,
            "coverage_trace_status": self.trace_status,
        }
        if self.trace_status == "complete":
            metadata["coverage_trace"] = {
                "schema": "unai.coverage-trace.v1",
                "target_file": "service.py",
                "target_sha256": evidence_metadata["target_sha256"],
                "executed_lines": [2, 3, 5],
                "case_lines": {
                    "negative": [2, 3],
                    "non-negative": [2, 5],
                },
                "trace_sha256": "d" * 64,
            }
        event = EvidenceRecord(
            evidence_id=f"coverage-run-{len(self.calls)}",
            task_id=task_id,
            kind="execution",
            source=evidence_source,
            claim="Grounded pytest run completed.",
            passed=True,
            artifact_ref=f"task:{task_id}/grounded-oracle-tests",
            metadata=metadata,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.ledger.append_evidence(event)
        return TaskTestRunResponse(
            task_id=task_id,
            status="passed",
            exit_code=0,
            sandboxed=True,
            stdout_preview="2 passed\n",
            stderr_preview="",
            evidence=event,
        )


def _setup_verifier(
    tmp_path: Path,
    *,
    trace_status: str = "complete",
):
    workspace_root = tmp_path / "workspaces"
    project = workspace_root / "demo"
    project.mkdir(parents=True)
    (project / "service.py").write_text(SOURCE, encoding="utf-8")
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    contract = compile_task(
        TaskCompileRequest(
            request="Verify classify against explicit oracle outputs",
            permissions=["workspace_read", "run_tests"],
        )
    )
    ledger.record_inspection(
        contract,
        "inspection_complete",
        [
            EvidenceRecord(
                evidence_id="inspection",
                task_id=contract.task_id,
                kind="precondition",
                source="test",
                claim="Workspace inspected.",
                passed=True,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        ],
        workspace="demo",
    )
    patch_manager = PatchManager(ledger)
    snapshot = patch_manager.read_file(contract.task_id, "service.py", workspace_root)
    runner = FakeCoverageRunner(ledger, trace_status=trace_status)
    verifier = GroundedTestVerifier(ledger, patch_manager, runner)
    return contract.task_id, workspace_root, ledger, snapshot, runner, verifier


def _coverage_request(snapshot) -> GroundedTestApprovalRequest:
    return GroundedTestApprovalRequest(
        confirm="run-grounded-oracle-tests",
        file_path="service.py",
        symbol_name="classify",
        source_sha256=snapshot.sha256,
        measure_coverage=True,
        cases=[
            GroundedOracleCase(
                case_id="negative",
                arguments={"value": -1},
                expected_output="negative",
                oracle_kind="user",
                oracle_reference="Requester supplied negative oracle output",
            ),
            GroundedOracleCase(
                case_id="non-negative",
                arguments={"value": 4},
                expected_output="non-negative",
                oracle_kind="user",
                oracle_reference="Requester supplied non-negative oracle output",
            ),
        ],
    )


def test_verifier_records_complete_coverage_evidence_and_proof_summary(
    tmp_path: Path,
) -> None:
    task_id, workspace_root, ledger, snapshot, runner, verifier = _setup_verifier(
        tmp_path
    )

    result = verifier.run(task_id, _coverage_request(snapshot), workspace_root)

    assert result.status == "passed"
    assert result.coverage is not None
    assert result.coverage.status == "complete"
    assert result.coverage.line_coverage == 1.0
    assert result.coverage.branch_coverage == 1.0
    assert result.coverage.cases_measured == 2
    assert result.coverage_evidence is not None
    assert result.coverage_evidence.kind == "coverage"
    assert result.coverage_evidence.source == "grounded-coverage"
    assert runner.calls[0]["metadata"]["coverage_requested"] is True
    assert runner.calls[0]["metadata"]["coverage_trace_relative_path"].endswith(
        ".coverage.json"
    )
    assert "sys.settrace" in runner.calls[0]["content"]

    after = ledger.get_snapshot(task_id)
    assert after is not None
    bundle = build_proof_bundle(after)
    assert bundle.coverage_run_count == 1
    assert bundle.latest_coverage_summary is not None
    assert bundle.coverage_evidence == [result.coverage_evidence]
    assert not any("unobserved by the supplied oracle cases" in risk for risk in bundle.unresolved_risks)


def test_unavailable_coverage_does_not_change_oracle_verdict(tmp_path: Path) -> None:
    task_id, workspace_root, ledger, snapshot, _runner, verifier = _setup_verifier(
        tmp_path, trace_status="missing"
    )

    result = verifier.run(task_id, _coverage_request(snapshot), workspace_root)

    assert result.status == "passed"
    assert result.coverage is not None
    assert result.coverage.status == "unavailable"
    assert "missing" in (result.coverage.reason or "")
    assert result.coverage_evidence is not None
    assert result.coverage_evidence.passed is None

    after = ledger.get_snapshot(task_id)
    assert after is not None
    bundle = build_proof_bundle(after)
    assert bundle.coverage_run_count == 1
    assert any("coverage measurement was unavailable" in risk for risk in bundle.unresolved_risks)


def test_coverage_is_opt_in_by_default(tmp_path: Path) -> None:
    task_id, workspace_root, _ledger, snapshot, runner, verifier = _setup_verifier(
        tmp_path
    )
    request = _coverage_request(snapshot)
    request.measure_coverage = False

    result = verifier.run(task_id, request, workspace_root)

    assert result.status == "passed"
    assert result.coverage is None
    assert result.coverage_evidence is None
    assert runner.calls[0]["metadata"]["coverage_requested"] is False
    assert runner.calls[0]["metadata"]["coverage_trace_relative_path"] is None


def test_coverage_runs_in_disposable_docker_sandbox(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker is unavailable; the CI engine job builds the sandbox image.")
    image_check = subprocess.run(
        ["docker", "image", "inspect", "unai-python-test:latest"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if image_check.returncode != 0:
        pytest.skip("unai-python-test:latest is not built; the CI engine job builds it.")

    task_id, workspace_root, ledger, snapshot, _runner, _verifier = _setup_verifier(
        tmp_path
    )
    runner = IsolatedPythonTestRunner(ledger, DockerSandbox(timeout_seconds=45))
    verifier = GroundedTestVerifier(
        ledger, PatchManager(ledger), runner
    )

    result = verifier.run(task_id, _coverage_request(snapshot), workspace_root)

    assert result.status == "passed"
    assert result.coverage is not None
    assert result.coverage.status == "complete", (
        f"coverage={result.coverage.model_dump(mode='json')}; "
        f"trace_status={result.execution_evidence.metadata.get('coverage_trace_status')}; "
        f"trace_path={result.execution_evidence.metadata.get('coverage_trace_relative_path')}"
    )
    assert result.coverage.branch_coverage == 1.0
    assert result.execution_evidence.metadata["coverage_trace_status"] == "complete"
    assert result.execution_evidence.metadata["coverage_trace"]["executed_lines"] == [2, 3, 5]

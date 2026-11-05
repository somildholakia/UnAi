from __future__ import annotations

import ast
import hashlib
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import unai_engine.main as main_module
from unai_engine.cognition.task_compiler import compile_task
from unai_engine.models import (
    EvidenceRecord,
    GroundedMutationApprovalRequest,
    GroundedMutationResponse,
    GroundedOracleCase,
    GroundedPropertyOracle,
    GroundedTestResponse,
    TaskCompileRequest,
    TaskTestRunResponse,
)
from unai_engine.runtime.patches import PatchManager
from unai_engine.runtime.sandbox import DockerSandbox, ExecutionResult
from unai_engine.runtime.test_runner import (
    IsolatedPythonTestRunner,
    WorkspaceStagingError,
    _validate_source_overrides,
)
from unai_engine.verification.grounded import (
    GroundedTestVerifier,
    GroundedVerificationError,
    build_proof_bundle,
)
from unai_engine.verification.ledger import EvidenceLedger
from unai_engine.verification.mutation import (
    MUTATION_EVIDENCE_SOURCE,
    MUTATION_SUMMARY_SOURCE,
    MutationPlanningError,
    MutationTestVerifier,
    plan_mutations,
)

SOURCE = """def target(value, flag):
    if value < 3 and flag:
        return 0
    return value + 2
"""


def _function(source: str, name: str = "target") -> ast.FunctionDef:
    tree = ast.parse(source)
    matches = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- planning


def test_plan_mutations_is_deterministic_and_allowlisted() -> None:
    first = plan_mutations(SOURCE, _function(SOURCE), 8)
    second = plan_mutations(SOURCE, _function(SOURCE), 8)

    assert first == second
    assert [item.mutant_id for item in first] == [
        f"mut-{index:03d}" for index in range(1, len(first) + 1)
    ]
    assert len(first) >= 5
    operators = {item.operator for item in first}
    assert "compare:Lt->LtE" in operators
    assert "boolean:And->Or" in operators
    assert "arithmetic:Add->Sub" in operators
    assert any(operator.startswith("literal:int") for operator in operators)
    for item in first:
        assert item.mutant_sha256 == _digest(item.mutant_source)
        assert item.mutant_source != SOURCE
        compile(item.mutant_source, "<mutant>", "exec")
        assert 1 <= item.line <= len(SOURCE.splitlines())


def test_plan_mutations_skips_nested_scopes_docstrings_and_chained_comparisons() -> None:
    source = '''def target(value):
    """Docstring with value < 3 and 7."""
    def inner(number):
        return number + 1
    callback = lambda item: item < 1
    return value if 10 < value < 20 else value * 2
'''
    mutants = plan_mutations(source, _function(source), 8)

    assert mutants
    assert all(item.line not in {2, 3, 5} for item in mutants)
    assert not any(item.operator.startswith("compare:") for item in mutants)


def test_plan_mutations_caps_budget_and_rejects_invalid_limits() -> None:
    capped = plan_mutations(SOURCE, _function(SOURCE), 2)
    assert len(capped) == 2
    assert [item.mutant_id for item in capped] == ["mut-001", "mut-002"]

    for invalid in (0, -1, 9, True):
        with pytest.raises(MutationPlanningError):
            plan_mutations(SOURCE, _function(SOURCE), invalid)  # type: ignore[arg-type]


def test_plan_mutations_handles_non_ascii_byte_offsets() -> None:
    source = 'def target(value):\n    note = "café ☕"\n    return value < 3\n'
    mutants = plan_mutations(source, _function(source), 8)

    compare = next(item for item in mutants if item.operator.startswith("compare:"))
    assert "café ☕" in compare.mutant_source
    assert "<=" in compare.mutant_source
    compile(compare.mutant_source, "<mutant>", "exec")


def test_plan_mutations_returns_nothing_for_mutation_free_body() -> None:
    source = "def target(value):\n    return value\n"
    assert plan_mutations(source, _function(source), 4) == []


# ---------------------------------------------------------------- runner


def _make_task(ledger: EvidenceLedger, workspace: str = "demo") -> str:
    contract = compile_task(
        TaskCompileRequest(
            request="Fix the calculation and run the tests",
            permissions=["workspace_read", "run_tests"],
        )
    )
    ledger.record_inspection(
        contract,
        "inspection_complete",
        [
            EvidenceRecord(
                evidence_id="inspection-" + contract.task_id,
                task_id=contract.task_id,
                kind="precondition",
                source="test",
                claim="Inspection completed",
                passed=True,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        ],
        workspace=workspace,
    )
    return contract.task_id


def _workspace(tmp_path: Path, source: str = SOURCE) -> tuple[Path, Path]:
    root = tmp_path / "workspaces"
    workspace = root / "demo"
    workspace.mkdir(parents=True)
    (workspace / "service.py").write_text(source, encoding="utf-8")
    return root, workspace


class RecordingSandbox:
    def __init__(self, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self.observed: str | None = None
        self.observed_test: str | None = None

    def run(self, workspace: Path, command: str) -> ExecutionResult:
        self.observed = (workspace / "service.py").read_text(encoding="utf-8")
        self.observed_test = (
            workspace / "unai_generated_tests" / "test_mutation_case.py"
        ).read_text(encoding="utf-8")
        return ExecutionResult(
            command=command,
            exit_code=self.exit_code,
            stdout="1 passed\n" if self.exit_code == 0 else "1 failed\n",
            stderr="",
        )


def test_source_override_validation_enforces_boundary() -> None:
    assert _validate_source_overrides(None) == {}
    assert _validate_source_overrides({"src/module.py": "x = 1\n"}) == {
        "src/module.py": "x = 1\n"
    }
    for invalid in (
        {"/etc/passwd.py": "x = 1\n"},
        {"../module.py": "x = 1\n"},
        {"src/../module.py": "x = 1\n"},
        {"src/module.txt": "x = 1\n"},
        {"unai_generated_tests/module.py": "x = 1\n"},
        {"src/module.py": ""},
        {"src/module.py": "x" * 300_001},
        {f"src/module{index}.py": "x = 1\n" for index in range(3)},
    ):
        with pytest.raises(WorkspaceStagingError):
            _validate_source_overrides(invalid)


def test_runner_applies_mutant_override_and_reports_kill_semantics(tmp_path: Path) -> None:
    root, _ = _workspace(tmp_path)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _make_task(ledger)
    sandbox = RecordingSandbox()
    runner = IsolatedPythonTestRunner(ledger, sandbox)
    mutant = "def target(value, flag):\n    return value - 2\n"

    execution = runner.run_generated_test(
        task_id,
        root,
        "unai_generated_tests/test_mutation_case.py",
        "def test_placeholder():\n    assert True\n",
        {"mutant_id": "mut-001"},
        evidence_source=MUTATION_EVIDENCE_SOURCE,
        source_overrides={"service.py": mutant},
    )

    assert sandbox.observed == mutant
    assert sandbox.observed_test == "def test_placeholder():\n    assert True\n"
    assert execution.status == "passed"
    assert execution.evidence.passed is False
    assert execution.evidence.metadata["mutation_killed"] is False
    assert execution.evidence.metadata["source_override_sha256"] == {
        "service.py": _digest(mutant)
    }
    assert execution.evidence.source == MUTATION_EVIDENCE_SOURCE


def test_runner_rejects_overrides_outside_mutation_runs(tmp_path: Path) -> None:
    root, _ = _workspace(tmp_path)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _make_task(ledger)
    runner = IsolatedPythonTestRunner(ledger, RecordingSandbox())

    with pytest.raises(ValueError):
        runner.run(
            task_id,
            root,
            evidence_source="docker-sandbox",
            source_overrides={"service.py": "x = 1\n"},
        )


# ---------------------------------------------------------------- verifier


class FakeGroundedVerifier:
    def __init__(self, response: GroundedTestResponse) -> None:
        self.response = response
        self.calls: list[tuple] = []

    def run(self, task_id, request, workspace_root):
        self.calls.append((task_id, request, workspace_root))
        return self.response


class FakeMutationRunner:
    def __init__(self, ledger: EvidenceLedger, statuses: list[str]) -> None:
        self.ledger = ledger
        self.statuses = statuses
        self.calls: list[tuple] = []

    def run_generated_test(
        self,
        task_id,
        workspace_root,
        generated_test_path,
        generated_test_content,
        evidence_metadata,
        *,
        evidence_source,
        source_overrides,
    ):
        index = len(self.calls)
        status = self.statuses[index] if index < len(self.statuses) else "passed"
        self.calls.append(
            (
                generated_test_path,
                generated_test_content,
                evidence_metadata,
                evidence_source,
                source_overrides,
            )
        )
        passed = None if status == "unavailable" else status == "failed"
        evidence = EvidenceRecord(
            evidence_id=f"mutation-exec-{index}",
            task_id=task_id,
            kind="execution",
            source=evidence_source,
            claim=f"Mutant run {status}",
            passed=passed,
            metadata={
                **evidence_metadata,
                "mutation_killed": status == "failed",
                "sandboxed": status != "unavailable",
                "timed_out": False,
                "exit_code": 0 if status == "passed" else 1,
            },
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.ledger.append_evidence(evidence)
        return TaskTestRunResponse(
            task_id=task_id,
            status=status,
            exit_code=0 if status == "passed" else 1,
            sandboxed=status != "unavailable",
            evidence=evidence,
        )


def _grounded_response(
    task_id: str, source_sha256: str, status: str = "passed"
) -> GroundedTestResponse:
    execution = EvidenceRecord(
        evidence_id=f"baseline-{status}",
        task_id=task_id,
        kind="execution",
        source="grounded-oracle-sandbox",
        claim="Grounded baseline",
        passed=True if status == "passed" else False if status == "failed" else None,
        metadata={"sandboxed": status != "unavailable", "target_sha256": source_sha256},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    trace = EvidenceRecord(
        evidence_id=f"baseline-trace-{status}",
        task_id=task_id,
        kind="trace",
        source="grounded-execution-trace",
        claim="Trace",
        passed=None,
        metadata={},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return GroundedTestResponse(
        task_id=task_id,
        file_path="service.py",
        symbol_name="target",
        source_sha256=source_sha256,
        status=status,
        case_count=1,
        passed_cases=1 if status == "passed" else None,
        execution_evidence=execution,
        trace_evidence=trace,
    )


def _request(source: str = SOURCE, **overrides) -> GroundedMutationApprovalRequest:
    payload = {
        "confirm": "run-grounded-mutation-tests",
        "file_path": "service.py",
        "symbol_name": "target",
        "source_sha256": _digest(source),
        "cases": [
            GroundedOracleCase(
                case_id="case-1",
                arguments={"value": 5, "flag": True},
                expected_output=7,
                oracle_kind="user",
                oracle_reference="user acceptance case #1",
            )
        ],
        "max_mutants": 3,
    }
    payload.update(overrides)
    return GroundedMutationApprovalRequest(**payload)


def _verifier(tmp_path: Path, statuses: list[str], baseline_status: str = "passed", source: str = SOURCE):
    root, _ = _workspace(tmp_path, source)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _make_task(ledger)
    grounded = FakeGroundedVerifier(_grounded_response(task_id, _digest(source), baseline_status))
    runner = FakeMutationRunner(ledger, statuses)
    verifier = MutationTestVerifier(ledger, PatchManager(ledger), runner, grounded)
    return verifier, grounded, runner, ledger, task_id, root


def test_mutation_verifier_reports_survivors_and_records_evidence(tmp_path: Path) -> None:
    verifier, grounded, runner, ledger, task_id, root = _verifier(
        tmp_path, ["failed", "passed", "failed"]
    )

    result = verifier.run(task_id, _request(), root)

    assert result.status == "failed"
    assert result.baseline_status == "passed"
    assert result.mutants_total == 3
    assert result.mutants_killed == 2
    assert result.mutants_survived == 1
    assert result.mutants_unavailable == 0
    assert result.mutation_score == pytest.approx(2 / 3)
    assert [item.status for item in result.mutants] == ["killed", "survived", "killed"]
    assert grounded.calls[0][1].confirm == "run-grounded-oracle-tests"
    assert len(runner.calls) == 3
    for _, generated_content, metadata, evidence_source, overrides in runner.calls:
        assert evidence_source == MUTATION_EVIDENCE_SOURCE
        assert metadata["mutant_id"].startswith("mut-")
        assert overrides["service.py"] != SOURCE
        assert metadata["generated_test_sha256"] == _digest(generated_content)
    assert result.summary_evidence.kind == "mutation"
    assert result.summary_evidence.source == MUTATION_SUMMARY_SOURCE
    assert result.summary_evidence.passed is False
    assert result.summary_evidence.metadata["mutants_survived"] == 1
    snapshot = ledger.get_snapshot(task_id)
    assert snapshot is not None
    assert snapshot.evidence[-1].evidence_id == result.summary_evidence.evidence_id


def test_mutation_verifier_passes_only_when_every_mutant_is_killed(tmp_path: Path) -> None:
    verifier, _, _, _, task_id, root = _verifier(tmp_path, ["failed", "failed"])

    result = verifier.run(task_id, _request(max_mutants=2), root)

    assert result.status == "passed"
    assert result.mutants_killed == 2
    assert result.mutants_survived == 0
    assert result.mutation_score == 1.0
    assert result.summary_evidence.passed is True


@pytest.mark.parametrize("baseline_status", ["failed", "unavailable"])
def test_mutation_verifier_stops_when_baseline_does_not_pass(
    tmp_path: Path, baseline_status: str
) -> None:
    verifier, grounded, runner, _, task_id, root = _verifier(
        tmp_path, ["failed"], baseline_status=baseline_status
    )

    result = verifier.run(task_id, _request(), root)

    assert result.status == baseline_status
    assert result.baseline_status == baseline_status
    assert result.mutants_total == 0
    assert result.mutants == []
    assert result.mutation_score is None
    assert result.summary_evidence.passed is (
        False if baseline_status == "failed" else None
    )
    assert result.summary_evidence.metadata["reason"] == "baseline_grounded_suite_not_passed"
    assert len(grounded.calls) == 1
    assert runner.calls == []


def test_mutation_verifier_marks_no_applicable_mutations_unavailable(tmp_path: Path) -> None:
    source = "def target(value, flag):\n    return value\n"
    verifier, _, runner, _, task_id, root = _verifier(tmp_path, ["failed"], source=source)

    result = verifier.run(task_id, _request(source=source), root)

    assert result.status == "unavailable"
    assert result.mutants_total == 0
    assert result.summary_evidence.passed is None
    assert result.summary_evidence.metadata["reason"] == "no_applicable_mutations"
    assert runner.calls == []


def test_mutation_verifier_marks_partial_availability_unavailable(tmp_path: Path) -> None:
    verifier, _, _, _, task_id, root = _verifier(
        tmp_path, ["failed", "unavailable", "failed"]
    )

    result = verifier.run(task_id, _request(), root)

    assert result.status == "unavailable"
    assert result.mutants_unavailable == 1
    assert result.mutation_score == 1.0
    assert result.summary_evidence.passed is None


def test_mutation_verifier_rejects_nondeterministic_properties_before_baseline(
    tmp_path: Path,
) -> None:
    request = _request()
    request.cases[0] = GroundedOracleCase(
        case_id="case-1",
        arguments={"value": 5, "flag": True},
        oracle_property=GroundedPropertyOracle(
            kind="output_stable_across_replays", replay_count=2
        ),
        oracle_kind="specification",
        oracle_reference="specification stability case",
    )
    verifier, grounded, runner, _, task_id, root = _verifier(tmp_path, ["failed"])

    with pytest.raises(GroundedVerificationError) as excinfo:
        verifier.run(task_id, request, root)

    assert excinfo.value.status_code == 422
    assert grounded.calls == []
    assert runner.calls == []


def test_mutation_verifier_rejects_stale_source_after_baseline(tmp_path: Path) -> None:
    verifier, _, runner, _, task_id, root = _verifier(tmp_path, ["failed"])
    request = _request()
    request.source_sha256 = "a" * 64

    with pytest.raises(GroundedVerificationError) as excinfo:
        verifier.run(task_id, request, root)

    assert excinfo.value.status_code == 409
    assert runner.calls == []


def test_mutation_verifier_executes_through_disposable_docker_sandbox(
    tmp_path: Path,
) -> None:
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

    source = (
        "def classify(value):\n"
        "    if value < 0:\n"
        "        return 'negative'\n"
        "    return 'non-negative'\n"
    )
    root, _ = _workspace(tmp_path, source)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _make_task(ledger)
    patch_manager = PatchManager(ledger)
    runner = IsolatedPythonTestRunner(ledger, DockerSandbox(timeout_seconds=45))
    grounded = GroundedTestVerifier(ledger, patch_manager, runner)
    verifier = MutationTestVerifier(ledger, patch_manager, runner, grounded)
    request = GroundedMutationApprovalRequest(
        confirm="run-grounded-mutation-tests",
        file_path="service.py",
        symbol_name="classify",
        source_sha256=_digest(source),
        cases=[
            GroundedOracleCase(
                case_id="negative",
                arguments={"value": -1},
                expected_output="negative",
                oracle_kind="user",
                oracle_reference="user acceptance case negative",
            ),
            GroundedOracleCase(
                case_id="positive",
                arguments={"value": 1},
                expected_output="non-negative",
                oracle_kind="user",
                oracle_reference="user acceptance case positive",
            ),
        ],
        max_mutants=2,
    )

    result = verifier.run(task_id, request, root)

    assert result.baseline_status == "passed"
    assert result.mutants_total == 2
    assert result.mutants_killed >= 1
    assert result.mutants_killed + result.mutants_survived == 2
    assert all(item.sandboxed for item in result.mutants)
    assert result.summary_evidence.metadata["schema"] == "unai.mutation-run.v1"


def test_mutation_verifier_rejects_changed_specification_artifact(tmp_path: Path) -> None:
    verifier, _, runner, _, task_id, root = _verifier(tmp_path, ["failed"])
    (root / "demo" / "spec.md").write_text("expected behavior", encoding="utf-8")
    request = _request()
    request.cases[0] = GroundedOracleCase(
        case_id="case-1",
        arguments={"value": 5, "flag": True},
        expected_output=7,
        oracle_kind="specification",
        oracle_reference="specification acceptance case",
        oracle_artifact_path="spec.md",
        oracle_artifact_sha256="f" * 64,
    )

    with pytest.raises(GroundedVerificationError) as excinfo:
        verifier.run(task_id, request, root)

    assert excinfo.value.status_code == 409
    assert runner.calls == []


def test_proof_bundle_exposes_latest_mutation_summary(tmp_path: Path) -> None:
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    root, _ = _workspace(tmp_path)
    task_id = _make_task(ledger)
    summary = EvidenceRecord(
        evidence_id="mutation-summary",
        task_id=task_id,
        kind="mutation",
        source=MUTATION_SUMMARY_SOURCE,
        claim="Two mutants survived",
        passed=False,
        metadata={
            "status": "failed",
            "mutants_total": 4,
            "mutants_killed": 2,
            "mutants_survived": 2,
            "mutants_unavailable": 0,
            "baseline_status": "passed",
        },
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    ledger.append_evidence(summary)
    snapshot = ledger.get_snapshot(task_id)
    assert snapshot is not None

    bundle = build_proof_bundle(snapshot)

    assert bundle.mutation_run_count == 1
    assert bundle.latest_mutation_summary is not None
    assert bundle.latest_mutation_summary.evidence_id == "mutation-summary"
    assert any("2 mutant(s) alive" in risk for risk in bundle.unresolved_risks)
    assert root.is_dir()


# ---------------------------------------------------------------- API


class FakeMutationService:
    def __init__(self, response: GroundedMutationResponse) -> None:
        self.response = response
        self.calls: list[tuple] = []

    def run(self, task_id, request, workspace_root):
        self.calls.append((task_id, request, workspace_root))
        return self.response


def _api_response(task_id: str) -> GroundedMutationResponse:
    baseline = _grounded_response(task_id, "b" * 64)
    summary = EvidenceRecord(
        evidence_id="summary",
        task_id=task_id,
        kind="mutation",
        source=MUTATION_SUMMARY_SOURCE,
        claim="Mutation summary",
        passed=True,
        metadata={"mutants_total": 1, "mutants_killed": 1, "mutants_survived": 0},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return GroundedMutationResponse(
        task_id=task_id,
        file_path="service.py",
        symbol_name="target",
        source_sha256="b" * 64,
        status="passed",
        baseline_status="passed",
        baseline_evidence=baseline.execution_evidence,
        mutants_total=1,
        mutants_killed=1,
        mutants_survived=0,
        mutants_unavailable=0,
        mutation_score=1.0,
        mutants=[],
        summary_evidence=summary,
    )


def _api_payload(confirm: str) -> dict:
    return {
        "confirm": confirm,
        "file_path": "service.py",
        "symbol_name": "target",
        "source_sha256": "b" * 64,
        "cases": [
            {
                "case_id": "case-1",
                "arguments": {"value": 5, "flag": True},
                "expected_output": 7,
                "oracle_kind": "user",
                "oracle_reference": "user acceptance case #1",
            }
        ],
        "max_mutants": 2,
    }


def test_mutation_endpoint_requires_exact_confirmation(monkeypatch) -> None:
    fake = FakeMutationService(_api_response("task-1"))
    monkeypatch.setattr(main_module, "mutation_test_verifier", fake)

    response = TestClient(main_module.app).post(
        "/api/tasks/task-1/mutation-tests",
        json=_api_payload("yes"),
    )

    assert response.status_code == 422
    assert fake.calls == []


def test_mutation_endpoint_delegates_after_confirmation(monkeypatch, tmp_path) -> None:
    fake = FakeMutationService(_api_response("task-1"))
    monkeypatch.setattr(main_module, "mutation_test_verifier", fake)
    monkeypatch.setattr(
        main_module,
        "settings",
        main_module.settings.__class__(
            **{
                **{
                    field: getattr(main_module.settings, field)
                    for field in main_module.settings.__dataclass_fields__
                },
                "workspace_root": tmp_path,
            }
        ),
    )

    response = TestClient(main_module.app).post(
        "/api/tasks/task-1/mutation-tests",
        json=_api_payload("run-grounded-mutation-tests"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "passed"
    assert response.json()["summary_evidence"]["source"] == MUTATION_SUMMARY_SOURCE
    assert len(fake.calls) == 1
    task_id, request, workspace_root = fake.calls[0]
    assert task_id == "task-1"
    assert request.confirm == "run-grounded-mutation-tests"
    assert workspace_root == tmp_path

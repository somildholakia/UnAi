import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from unai_engine.cognition.task_compiler import compile_task
from unai_engine.models import EvidenceRecord, TaskCompileRequest
from unai_engine.runtime.sandbox import ExecutionResult, DockerSandbox
from unai_engine.runtime.test_runner import (
    IsolatedPythonTestRunner,
    TaskRunRejected,
    WorkspaceStagingError,
    stage_python_workspace,
)
from unai_engine.verification.ledger import EvidenceLedger


def _make_task(ledger: EvidenceLedger, permissions=None, workspace="demo") -> str:
    approved = permissions if permissions is not None else ["workspace_read", "run_tests"]
    contract = compile_task(
        TaskCompileRequest(
            request="Fix the calculation bug and run the tests",
            permissions=approved,
        )
    )
    evidence = EvidenceRecord(
        evidence_id="inspection-" + contract.task_id,
        task_id=contract.task_id,
        kind="precondition",
        source="test",
        claim="Inspection was completed",
        passed=True,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    ledger.record_inspection(
        contract,
        "inspection_complete",
        [evidence],
        workspace=workspace,
    )
    return contract.task_id


class FakeSandbox:
    def __init__(self, exit_code=0, stdout="", stderr="", timed_out=False):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.called_with = None

    def run(self, workspace: Path, command: str) -> ExecutionResult:
        self.called_with = (workspace, command)
        assert command == "python -m pytest -q"
        assert (workspace / "src" / "module.py").is_file()
        assert (workspace / "tests" / "test_module.py").is_file()
        assert not (workspace / ".env").exists()
        assert not (workspace / ".git").exists()
        assert not (workspace / "id_rsa").exists()
        (workspace / "created-by-test.txt").write_text("temporary", encoding="utf-8")
        return ExecutionResult(
            command=command,
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            timed_out=self.timed_out,
        )


def _make_workspace(root: Path) -> Path:
    workspace = root / "demo"
    (workspace / "src").mkdir(parents=True)
    (workspace / "tests").mkdir()
    (workspace / ".git").mkdir()
    (workspace / "src" / "module.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    (workspace / "tests" / "test_module.py").write_text("def test_add(): assert 1 + 1 == 2\n", encoding="utf-8")
    (workspace / ".env").write_text("API_KEY=private-value\n", encoding="utf-8")
    (workspace / ".git" / "config").write_text("private git metadata", encoding="utf-8")
    (workspace / "id_rsa").write_text("private key", encoding="utf-8")
    (workspace / "image.png").write_bytes(b"\x89PNG")
    return workspace


def test_runner_uses_filtered_temporary_copy_and_appends_execution_evidence(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    workspace = _make_workspace(root)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _make_task(ledger)
    sandbox = FakeSandbox(stdout="2 passed\napi_key=supersecret\nBearer abc.def.ghi")
    runner = IsolatedPythonTestRunner(ledger, sandbox)

    result = runner.run(task_id, root)

    assert result.status == "passed"
    assert result.sandboxed is True
    assert result.exit_code == 0
    assert result.staged_files == 2
    assert "supersecret" not in result.stdout_preview
    assert "abc.def.ghi" not in result.stdout_preview
    assert not (workspace / "created-by-test.txt").exists()
    assert sandbox.called_with is not None
    assert sandbox.called_with[0] != workspace

    snapshot = ledger.get_snapshot(task_id)
    assert snapshot is not None
    assert snapshot.workspace == "demo"
    assert snapshot.evidence[-1].kind == "execution"
    assert snapshot.evidence[-1].passed is True


def test_runner_loads_replay_and_filesystem_audit_trace_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    _make_workspace(root)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _make_task(ledger)

    class ReplayTraceSandbox(FakeSandbox):
        def run(self, workspace: Path, command: str) -> ExecutionResult:
            result = super().run(workspace, command)
            trace_path = workspace / "unai_generated_tests" / "test_oracle.replay.json"
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            trace_path.write_text(
                json.dumps({
                    "schema": "unai.replay-trace.v1",
                    "records": [
                        {
                            "case_id": "repeatable-case",
                            "kind": "output_stable_across_replays",
                            "replay_count": 2,
                            "stable": False,
                            "results": [
                                {
                                    "replay_index": 1,
                                    "status": "returned",
                                    "actual_type": "int",
                                    "actual_sha256": "1" * 64,
                                },
                                {
                                    "replay_index": 2,
                                    "status": "returned",
                                    "actual_type": "int",
                                    "actual_sha256": "2" * 64,
                                },
                            ],
                        },
                        {
                            "case_id": "filesystem-case",
                            "kind": "workspace_filesystem_unchanged",
                            "status": "complete",
                            "unchanged": False,
                            "before_sha256": "3" * 64,
                            "after_sha256": "4" * 64,
                            "changed_entry_count": 1,
                            "changed_paths_sha256": "5" * 64,
                            "entry_count": 7,
                            "bytes_hashed": 11,
                        },
                    ],
                }),
                encoding="utf-8",
            )
            return result

    runner = IsolatedPythonTestRunner(ledger, ReplayTraceSandbox())
    result = runner.run_generated_test(
        task_id,
        root,
        "unai_generated_tests/test_oracle.py",
        "def test_placeholder():\n    assert True\n",
        {
            "trace_case_ids": ["repeatable-case", "filesystem-case"],
            "filesystem_case_ids": ["filesystem-case"],
            "replay_trace_relative_path": "unai_generated_tests/test_oracle.replay.json",
        },
    )

    assert result.status == "passed"
    records = result.evidence.metadata["replay_trace_records"]
    assert result.evidence.metadata["replay_trace_status"] == "complete"
    assert result.evidence.metadata["filesystem_audit_status"] == "complete"
    assert records[0]["case_id"] == "repeatable-case"
    assert records[0]["stable"] is False
    assert [item["actual_sha256"] for item in records[0]["results"]] == [
        "1" * 64,
        "2" * 64,
    ]
    assert records[1]["kind"] == "workspace_filesystem_unchanged"
    assert records[1]["changed_entry_count"] == 1
    assert records[1]["unchanged"] is False


def test_runner_marks_filesystem_audit_unavailable_instead_of_passing(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    _make_workspace(root)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _make_task(ledger)

    class UnavailableFilesystemAuditSandbox(FakeSandbox):
        def run(self, workspace: Path, command: str) -> ExecutionResult:
            result = super().run(workspace, command)
            trace_path = workspace / "unai_generated_tests" / "test_oracle.replay.json"
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            trace_path.write_text(
                json.dumps({
                    "schema": "unai.replay-trace.v1",
                    "records": [{
                        "case_id": "filesystem-case",
                        "kind": "workspace_filesystem_unchanged",
                        "status": "unavailable",
                        "reason_code": "request_byte_budget_exceeded",
                    }],
                }),
                encoding="utf-8",
            )
            return result

    runner = IsolatedPythonTestRunner(ledger, UnavailableFilesystemAuditSandbox())
    result = runner.run_generated_test(
        task_id,
        root,
        "unai_generated_tests/test_oracle.py",
        "def test_placeholder():\n    assert True\n",
        {
            "trace_case_ids": ["filesystem-case"],
            "filesystem_case_ids": ["filesystem-case"],
            "replay_trace_relative_path": "unai_generated_tests/test_oracle.replay.json",
        },
    )

    assert result.status == "unavailable"
    assert result.evidence.passed is None
    assert result.evidence.metadata["filesystem_audit_status"] == "unavailable"
    assert result.evidence.metadata["filesystem_audit_unavailable_case_ids"] == ["filesystem-case"]


def test_runner_rejects_symlinked_replay_trace_directory(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    _make_workspace(root)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _make_task(ledger)

    class SymlinkedReplayTraceSandbox(FakeSandbox):
        def run(self, workspace: Path, command: str) -> ExecutionResult:
            result = super().run(workspace, command)
            generated_dir = workspace / "unai_generated_tests"
            for item in generated_dir.iterdir():
                item.unlink()
            generated_dir.rmdir()
            generated_dir.symlink_to(workspace / "src", target_is_directory=True)
            return result

    runner = IsolatedPythonTestRunner(ledger, SymlinkedReplayTraceSandbox())
    result = runner.run_generated_test(
        task_id,
        root,
        "unai_generated_tests/test_oracle.py",
        "def test_placeholder():\n    assert True\n",
        {
            "trace_case_ids": ["repeatable-case"],
            "replay_trace_relative_path": "unai_generated_tests/test_oracle.replay.json",
        },
    )

    assert result.status == "passed"
    assert result.evidence.metadata["replay_trace_status"] == "unsafe_directory"
    assert result.evidence.metadata["replay_trace_records"] == []


def test_runner_rejects_execution_without_explicit_run_tests_permission(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    _make_workspace(root)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _make_task(ledger, permissions=["workspace_read"])
    sandbox = FakeSandbox()
    runner = IsolatedPythonTestRunner(ledger, sandbox)

    with pytest.raises(TaskRunRejected, match="run_tests"):
        runner.run(task_id, root)

    assert sandbox.called_with is None


def test_runner_never_falls_back_to_host_when_docker_is_missing(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "workspaces"
    _make_workspace(root)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _make_task(ledger)
    monkeypatch.setattr("unai_engine.runtime.sandbox.shutil.which", lambda _: None)
    runner = IsolatedPythonTestRunner(ledger, DockerSandbox())

    result = runner.run(task_id, root)

    assert result.status == "unavailable"
    assert result.sandboxed is False
    assert result.evidence.passed is None
    assert result.evidence.metadata["error"] == "SandboxUnavailable"


def test_missing_pytest_image_does_not_become_a_false_test_failure(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    _make_workspace(root)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _make_task(ledger)
    sandbox = FakeSandbox(exit_code=1, stderr="No module named pytest")
    runner = IsolatedPythonTestRunner(ledger, sandbox)

    result = runner.run(task_id, root)

    assert result.status == "unavailable"
    assert result.sandboxed is True
    assert result.evidence.passed is None
    assert result.evidence.metadata["error"] == "pytest_missing_from_sandbox_image"


def test_staging_budget_failure_stops_before_sandbox_run(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    workspace = _make_workspace(root)
    destination = tmp_path / "stage"

    with pytest.raises(WorkspaceStagingError, match="budget exceeded"):
        stage_python_workspace(workspace, destination, max_files=1)

    assert destination.exists()
    assert len(list(destination.rglob("*.py"))) <= 1

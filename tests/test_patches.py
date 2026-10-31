from datetime import datetime, timezone
import hashlib
from pathlib import Path

import pytest

from unai_engine.cognition.task_compiler import compile_task
from unai_engine.models import EvidenceRecord, PatchProposalRequest, TaskCompileRequest
from unai_engine.runtime.patches import PatchConflict, PatchManager, PatchRejected
from unai_engine.verification.ledger import EvidenceLedger


def _task(ledger: EvidenceLedger, workspace: str = "demo", permissions=None, request_text=None) -> str:
    if permissions is None:
        permissions = ["workspace_read", "workspace_write"]
    contract = compile_task(
        TaskCompileRequest(
            request=request_text or "Implement the profile display feature",
            permissions=permissions,
        )
    )
    evidence = EvidenceRecord(
        evidence_id="inspection-" + contract.task_id,
        task_id=contract.task_id,
        kind="precondition",
        source="test",
        claim="Inspection completed",
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


def _workspace(root: Path) -> tuple[Path, str, str]:
    project = root / "demo"
    project.mkdir(parents=True)
    content = "def greeting(name):\n    return f'Hello {name}'\n"
    target = project / "service.py"
    target.write_text(content, encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return project, content, digest


def _proposal(file_path: str, expected_sha256: str, proposed: str = "def greeting(name):\n    return f'Welcome {name}'\n'):
    return PatchProposalRequest(
        file_path=file_path,
        expected_sha256=expected_sha256,
        proposed_content=proposed,
        description="Improve greeting copy",
    )


def test_patch_is_stored_as_pending_without_changing_workspace(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    workspace, original, digest = _workspace(root)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _task(ledger)
    manager = PatchManager(ledger)

    proposal = manager.propose(task_id, _proposal("service.py", digest), root)

    assert proposal.status == "pending"
    assert proposal.expected_sha256 == digest
    assert proposal.proposed_sha256 != digest
    assert "--- a/service.py" in proposal.diff_preview
    assert (workspace / "service.py").read_text(encoding="utf-8") == original
    persisted = ledger.get_patch_proposal(task_id, proposal.proposal_id)
    assert persisted is not None
    assert persisted["status"] == "pending"
    snapshot = ledger.get_snapshot(task_id)
    assert snapshot is not None
    assert snapshot.evidence[-1].kind == "patch"


def test_approved_patch_apply_changes_only_target_and_appends_evidence(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    workspace, original, digest = _workspace(root)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _task(ledger)
    manager = PatchManager(ledger)
    proposal = manager.propose(task_id, _proposal("service.py", digest), root)

    applied = manager.apply(task_id, proposal.proposal_id, root)

    changed = (workspace / "service.py").read_text(encoding="utf-8")
    assert applied.status == "applied"
    assert changed != original
    assert "Welcome" in changed
    assert hashlib.sha256((workspace / "service.py").read_bytes()).hexdigest() == applied.new_sha256
    persisted = ledger.get_patch_proposal(task_id, proposal.proposal_id)
    assert persisted is not None
    assert persisted["status"] == "applied"
    snapshot = ledger.get_snapshot(task_id)
    assert snapshot is not None
    assert snapshot.evidence[-1].source == "patch-applier"
    assert snapshot.evidence[-1].passed is True


def test_applied_patch_cannot_be_applied_again(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    workspace, _, digest = _workspace(root)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _task(ledger)
    manager = PatchManager(ledger)
    proposal = manager.propose(task_id, _proposal("service.py", digest), root)
    manager.apply(task_id, proposal.proposal_id, root)

    with pytest.raises(PatchConflict, match="not pending"):
        manager.apply(task_id, proposal.proposal_id, root)


def test_stale_workspace_change_is_never_overwritten(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    workspace, _, digest = _workspace(root)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _task(ledger)
    manager = PatchManager(ledger)
    proposal = manager.propose(task_id, _proposal("service.py", digest), root)
    external_change = "def external_edit():\n    return 'keep this'\n"
    (workspace / "service.py").write_text(external_change, encoding="utf-8")

    with pytest.raises(PatchConflict, match="changed after proposal"):
        manager.apply(task_id, proposal.proposal_id, root)

    assert (workspace / "service.py").read_text(encoding="utf-8") == external_change
    persisted = ledger.get_patch_proposal(task_id, proposal.proposal_id)
    assert persisted is not None
    assert persisted["status"] == "pending"


def test_missing_workspace_write_approval_rejects_proposal(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    _, _, digest = _workspace(root)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _task(ledger, permissions=["workspace_read"])
    manager = PatchManager(ledger)

    with pytest.raises(PatchRejected, match="workspace_read and workspace_write"):
        manager.propose(task_id, _proposal("service.py", digest), root)


def test_destructive_contract_requires_separate_destructive_approval(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    _, _, digest = _workspace(root)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _task(
        ledger,
        permissions=["workspace_read", "workspace_write"],
        request_text="Remove obsolete output from the profile module",
    )
    manager = PatchManager(ledger)

    with pytest.raises(PatchRejected, match="destructive_changes"):
        manager.propose(task_id, _proposal("service.py", digest), root)


@pytest.mark.parametrize(
    "path",
    [
        "../outside.py",
        "/etc/passwd.py",
        "service.py/../../outside.py",
        ".env",
        ".env.local.py",
        "secrets/config.py",
        "credentials.json",
        "id_rsa",
        "unsupported.bin",
    ],
)
def test_patch_path_validation_rejects_escape_and_sensitive_targets(tmp_path: Path, path: str) -> None:
    root = tmp_path / "workspaces"
    _workspace(root)
    with pytest.raises(PatchConflict):
        PatchManager._target(root, "demo", path)


def test_symlink_patch_target_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    workspace, _, _ = _workspace(root)
    outside = tmp_path / "outside.py"
    outside.write_text("private = True\n", encoding="utf-8")
    link = workspace / "linked.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is not supported")
    with pytest.raises(PatchConflict, match="Symlinked"):
        PatchManager._target(root, "demo", "linked.py")


def test_wrong_base_hash_is_rejected_before_storing_proposal(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    _workspace(root)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _task(ledger)
    manager = PatchManager(ledger)

    with pytest.raises(PatchConflict, match="changed since"):
        manager.propose(task_id, _proposal("service.py", "0" * 64), root)


def test_read_file_returns_content_hash_and_appends_discovery_evidence(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    workspace, original, digest = _workspace(root)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _task(ledger, permissions=["workspace_read"])
    manager = PatchManager(ledger)

    snapshot = manager.read_file(task_id, "service.py", root)

    assert snapshot.content == original
    assert snapshot.sha256 == digest
    assert snapshot.size_bytes == len(original.encode("utf-8"))
    evidence = ledger.get_snapshot(task_id)
    assert evidence is not None
    assert evidence.evidence[-1].source == "workspace-file-snapshot"
    assert evidence.evidence[-1].metadata["sha256"] == digest


def test_read_file_rejects_task_without_workspace_read(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    _workspace(root)
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    task_id = _task(ledger, permissions=["workspace_write"])
    manager = PatchManager(ledger)

    with pytest.raises(PatchRejected, match="workspace_read"):
        manager.read_file(task_id, "service.py", root)

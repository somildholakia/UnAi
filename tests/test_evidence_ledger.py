from datetime import datetime, timezone
import sqlite3

import pytest

from unai_engine.cognition.task_compiler import compile_task
from unai_engine.models import EvidenceRecord, TaskCompileRequest
from unai_engine.verification.ledger import EvidenceLedger, EvidenceLedgerError


def _task_and_evidence():
    contract = compile_task(TaskCompileRequest(request="Review the API implementation"))
    evidence = EvidenceRecord(
        evidence_id="evidence-1",
        task_id=contract.task_id,
        kind="repository",
        source="test",
        claim="The test repository was inspected",
        passed=True,
        artifact_ref="workspace:sample",
        metadata={"files": 3},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return contract, evidence


def test_ledger_persists_task_contract_and_evidence(tmp_path) -> None:
    path = tmp_path / "evidence.sqlite3"
    ledger = EvidenceLedger(path)
    contract, evidence = _task_and_evidence()

    ledger.record_inspection(contract, "inspection_complete", [evidence])
    snapshot = EvidenceLedger(path).get_snapshot(contract.task_id)

    assert snapshot is not None
    assert snapshot.status == "inspection_complete"
    assert snapshot.contract.objective == contract.objective
    assert len(snapshot.evidence) == 1
    assert snapshot.evidence[0].evidence_id == "evidence-1"
    assert snapshot.evidence[0].metadata == {"files": 3}


def test_evidence_rows_are_immutable(tmp_path) -> None:
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    contract, evidence = _task_and_evidence()
    ledger.record_inspection(contract, "inspection_complete", [evidence])

    connection = sqlite3.connect(ledger.path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE task_evidence SET claim = 'rewritten' WHERE evidence_id = ?",
                (evidence.evidence_id,),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "DELETE FROM task_evidence WHERE evidence_id = ?",
                (evidence.evidence_id,),
            )
        connection.rollback()
    finally:
        connection.close()


def test_mismatched_evidence_rolls_back_task_insert(tmp_path) -> None:
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    contract, evidence = _task_and_evidence()
    wrong = evidence.model_copy(update={"task_id": "another-task"})

    with pytest.raises(EvidenceLedgerError, match="belong"):
        ledger.record_inspection(contract, "inspection_complete", [wrong])

    assert ledger.get_snapshot(contract.task_id) is None

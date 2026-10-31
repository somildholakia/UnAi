"""SQLite-backed append-only task and evidence ledger."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from unai_engine.models import EvidenceRecord, TaskContract, TaskEvidenceSnapshot


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    objective TEXT NOT NULL,
    contract_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_evidence (
    evidence_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    claim TEXT NOT NULL,
    passed INTEGER NULL,
    artifact_ref TEXT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_evidence_task_time
    ON task_evidence(task_id, created_at, evidence_id);
CREATE TRIGGER IF NOT EXISTS task_evidence_no_update
BEFORE UPDATE ON task_evidence
BEGIN
    SELECT RAISE(ABORT, 'task evidence is append-only');
END;
CREATE TRIGGER IF NOT EXISTS task_evidence_no_delete
BEFORE DELETE ON task_evidence
BEGIN
    SELECT RAISE(ABORT, 'task evidence is append-only');
END;
"""


class EvidenceLedgerError(RuntimeError):
    pass


class EvidenceLedger:
    """Minimal durable ledger; evidence rows cannot be updated or deleted."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.executescript(_SCHEMA)
        return connection

    def record_inspection(
        self,
        contract: TaskContract,
        status: str,
        evidence: list[EvidenceRecord],
    ) -> None:
        if any(item.task_id != contract.task_id for item in evidence):
            raise EvidenceLedgerError("Every evidence item must belong to the inspected task.")

        created_at = evidence[0].created_at if evidence else ""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO tasks(task_id, status, objective, contract_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    contract.task_id,
                    status,
                    contract.objective,
                    json.dumps(contract.model_dump(mode="json"), ensure_ascii=False),
                    created_at,
                ),
            )
            connection.executemany(
                """
                INSERT INTO task_evidence(
                    evidence_id, task_id, kind, source, claim, passed,
                    artifact_ref, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.evidence_id,
                        item.task_id,
                        item.kind,
                        item.source,
                        item.claim,
                        None if item.passed is None else int(item.passed),
                        item.artifact_ref,
                        json.dumps(item.metadata, ensure_ascii=False),
                        item.created_at,
                    )
                    for item in evidence
                ],
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_snapshot(self, task_id: str) -> TaskEvidenceSnapshot | None:
        connection = self._connect()
        try:
            task = connection.execute(
                "SELECT task_id, status, contract_json, created_at FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if task is None:
                return None

            rows = connection.execute(
                """
                SELECT evidence_id, task_id, kind, source, claim, passed,
                       artifact_ref, metadata_json, created_at
                FROM task_evidence
                WHERE task_id = ?
                ORDER BY created_at, evidence_id
                """,
                (task_id,),
            ).fetchall()
            evidence = [
                EvidenceRecord(
                    evidence_id=row["evidence_id"],
                    task_id=row["task_id"],
                    kind=row["kind"],
                    source=row["source"],
                    claim=row["claim"],
                    passed=None if row["passed"] is None else bool(row["passed"]),
                    artifact_ref=row["artifact_ref"],
                    metadata=json.loads(row["metadata_json"]),
                    created_at=row["created_at"],
                )
                for row in rows
            ]
            return TaskEvidenceSnapshot(
                task_id=task["task_id"],
                status=task["status"],
                contract=TaskContract.model_validate(json.loads(task["contract_json"])),
                created_at=task["created_at"],
                evidence=evidence,
            )
        finally:
            connection.close()

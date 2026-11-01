from __future__ import annotations

from fastapi.testclient import TestClient

import unai_engine.main as main_module
from unai_engine.models import EvidenceRecord, GroundedTestResponse


def _response(task_id: str) -> GroundedTestResponse:
    event = EvidenceRecord(
        evidence_id="exec",
        task_id=task_id,
        kind="execution",
        source="grounded-oracle-sandbox",
        claim="Grounded test completed.",
        passed=True,
        metadata={"stdout_sha256": "a" * 64, "stderr_sha256": "b" * 64},
        created_at="2026-11-01T00:00:00+00:00",
    )
    trace = EvidenceRecord(
        evidence_id="trace",
        task_id=task_id,
        kind="trace",
        source="grounded-execution-trace",
        claim="Execution trace captured.",
        passed=None,
        metadata={"trace_sha256": "c" * 64},
        created_at="2026-11-01T00:00:01+00:00",
    )
    oracle = EvidenceRecord(
        evidence_id="oracle",
        task_id=task_id,
        kind="oracle",
        source="grounded-oracle",
        claim="Expected output supplied.",
        passed=None,
        metadata={"oracle_kind": "user", "oracle_reference": "user acceptance case"},
        created_at="2026-11-01T00:00:00+00:00",
    )
    return GroundedTestResponse(
        task_id=task_id,
        file_path="service.py",
        symbol_name="calculate",
        source_sha256="d" * 64,
        status="passed",
        case_count=1,
        passed_cases=1,
        execution_evidence=event,
        trace_evidence=trace,
        oracle_evidence=[oracle],
    )


class FakeVerifier:
    def __init__(self) -> None:
        self.calls = []

    def run(self, task_id, request, workspace_root):
        self.calls.append((task_id, request, workspace_root))
        return _response(task_id)


def _request(confirm: str = "run-grounded-oracle-tests") -> dict:
    return {
        "confirm": confirm,
        "file_path": "service.py",
        "symbol_name": "calculate",
        "source_sha256": "d" * 64,
        "cases": [
            {
                "case_id": "case-1",
                "arguments": {"value": 1},
                "expected_output": 2,
                "oracle_kind": "user",
                "oracle_reference": "user acceptance case #1",
            }
        ],
    }


def test_grounded_test_endpoint_requires_exact_confirmation(monkeypatch) -> None:
    fake = FakeVerifier()
    monkeypatch.setattr(main_module, "grounded_test_verifier", fake)

    response = TestClient(main_module.app).post(
        "/api/tasks/task-1/grounded-tests",
        json=_request(confirm="yes"),
    )

    assert response.status_code == 422
    assert fake.calls == []


def test_grounded_test_endpoint_delegates_after_confirmation(monkeypatch, tmp_path) -> None:
    fake = FakeVerifier()
    monkeypatch.setattr(main_module, "grounded_test_verifier", fake)
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
        "/api/tasks/task-1/grounded-tests",
        json=_request(),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "passed"
    assert response.json()["execution_evidence"]["source"] == "grounded-oracle-sandbox"
    assert len(fake.calls) == 1
    task_id, request, workspace_root = fake.calls[0]
    assert task_id == "task-1"
    assert request.confirm == "run-grounded-oracle-tests"
    assert workspace_root == tmp_path

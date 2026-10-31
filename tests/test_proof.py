from datetime import datetime, timezone, timedelta

from unai_engine.cognition.task_compiler import compile_task
from unai_engine.models import EvidenceRecord, TaskCompileRequest, TaskEvidenceSnapshot
from unai_engine.verification.proof import summarize_proof


BASE = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


def _event(task_id, suffix, source, kind, passed, metadata=None, minutes=0, artifact_ref=None):
    return EvidenceRecord(
        evidence_id=f"{task_id}-{suffix}",
        task_id=task_id,
        kind=kind,
        source=source,
        claim=f"Evidence {suffix}",
        passed=passed,
        artifact_ref=artifact_ref,
        metadata=metadata or {},
        created_at=(BASE + timedelta(minutes=minutes)).isoformat(),
    )


def _snapshot(events):
    contract = compile_task(TaskCompileRequest(request="Implement a profile feature"))
    fixed = [
        item.model_copy(update={"task_id": contract.task_id}) for item in events
    ]
    return TaskEvidenceSnapshot(
        task_id=contract.task_id,
        status="inspection_complete",
        workspace="demo",
        contract=contract,
        created_at=BASE.isoformat(),
        evidence=fixed,
    )


def _patch_events(passed=True, test_minutes=3, quality_passed=True):
    proposal = _event(
        "placeholder", "proposal", "patch-proposal", "patch", True,
        artifact_ref="proposal:p1", minutes=1,
    )
    apply = _event(
        "placeholder", "apply", "patch-applier", "patch", True,
        metadata={"new_sha256": "a" * 64},
        artifact_ref="proposal:p1",
        minutes=2,
    )
    test = _event(
        "placeholder", "test", "docker-sandbox", "execution", passed,
        metadata={"exit_code": 0 if passed else 1},
        minutes=test_minutes,
    )
    quality = _event(
        "placeholder", "quality", "python-ast-parser", "quality", quality_passed,
        minutes=0,
    )
    return [quality, proposal, apply, test]


def test_proof_is_inspection_only_without_patch_or_test_events() -> None:
    result = summarize_proof(_snapshot([]))
    assert result.status == "inspection_only"
    assert result.patch_count == 0
    assert result.tests_after_latest_patch is False


def test_pending_patch_is_not_reported_as_applied() -> None:
    pending = _event(
        "placeholder", "proposal", "patch-proposal", "patch", True,
        artifact_ref="proposal:p1",
    )
    result = summarize_proof(_snapshot([pending]))
    assert result.status == "patch_pending"
    assert result.patch_count == 0
    assert result.pending_patch_count == 1


def test_applied_patch_requires_a_later_test_run() -> None:
    result = summarize_proof(_snapshot(_patch_events(test_minutes=1)))
    assert result.status == "awaiting_tests"
    assert result.patch_count == 1
    assert result.latest_patch_sha256 == "a" * 64
    assert result.tests_after_latest_patch is False


def test_successful_tests_after_patch_are_reported_with_limits() -> None:
    result = summarize_proof(_snapshot(_patch_events(passed=True, test_minutes=3)))
    assert result.status == "tests_passed_after_patch"
    assert result.latest_test_passed is True
    assert result.tests_after_latest_patch is True
    assert result.quality_check_passed is True
    assert any("not a correctness" in item.lower() or "acceptance criteria" in item.lower()
               for item in result.limitations)


def test_failed_and_unavailable_test_results_are_not_collapsed() -> None:
    failed = summarize_proof(_snapshot(_patch_events(passed=False, test_minutes=3)))
    unavailable = summarize_proof(
        _snapshot(_patch_events(passed=None, test_minutes=3))
    )
    assert failed.status == "tests_failed"
    assert unavailable.status == "tests_unavailable"


def test_failed_quality_check_blocks_clean_test_summary() -> None:
    result = summarize_proof(
        _snapshot(_patch_events(passed=True, test_minutes=3, quality_passed=False))
    )
    assert result.status == "tests_passed_but_quality_check_failed"

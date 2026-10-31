"""Evidence summary for patch/test ordering; deliberately not a correctness oracle."""
from __future__ import annotations

from unai_engine.models import TaskEvidenceSnapshot, TaskProofResponse


_LIMITATIONS = [
    "A passing pytest run is evidence only for the tests that were included and executed.",
    "The deterministic task compiler does not prove that all acceptance criteria were understood.",
    "No adversarial counterexample search or independent semantic review is performed here.",
    "Repository parse cleanliness is reported separately and is not inferred from test success.",
]


def summarize_proof(snapshot: TaskEvidenceSnapshot) -> TaskProofResponse:
    events = sorted(snapshot.evidence, key=lambda item: (item.created_at, item.evidence_id))

    proposal_ids = {
        item.artifact_ref.removeprefix("proposal:")
        for item in events
        if item.source == "patch-proposal" and item.artifact_ref
    }
    applied_events = [
        item for item in events
        if item.source == "patch-applier" and item.kind == "patch"
    ]
    applied_ids = {
        item.artifact_ref.removeprefix("proposal:")
        for item in applied_events
        if item.artifact_ref
    }
    latest_patch = applied_events[-1] if applied_events else None
    pending_count = len(proposal_ids - applied_ids)

    test_events = [
        item for item in events
        if item.source == "docker-sandbox" and item.kind == "execution"
    ]
    latest_test = test_events[-1] if test_events else None
    quality_events = [
        item for item in events
        if item.source == "python-ast-parser" and item.kind == "quality"
    ]
    latest_quality = quality_events[-1] if quality_events else None
    tests_after_patch = bool(
        latest_patch is not None
        and latest_test is not None
        and latest_test.created_at > latest_patch.created_at
    )

    if latest_patch is None:
        if pending_count:
            status = "patch_pending"
        elif latest_test is not None and latest_test.passed is False:
            status = "tests_failed"
        elif latest_test is not None and latest_test.passed is None:
            status = "tests_unavailable"
        elif latest_test is not None:
            status = "tests_run_no_patch"
        else:
            status = "inspection_only"
    elif not tests_after_patch:
        status = "awaiting_tests"
    elif latest_test is not None and latest_test.passed is False:
        status = "tests_failed"
    elif latest_test is not None and latest_test.passed is None:
        status = "tests_unavailable"
    elif latest_quality is not None and latest_quality.passed is False:
        status = "tests_passed_but_quality_check_failed"
    else:
        status = "tests_passed_after_patch"

    latest_patch_hash = None
    if latest_patch is not None:
        latest_patch_hash = str(latest_patch.metadata.get("new_sha256") or "") or None

    return TaskProofResponse(
        task_id=snapshot.task_id,
        status=status,
        patch_count=len(applied_events),
        pending_patch_count=pending_count,
        latest_patch_sha256=latest_patch_hash,
        latest_patch_at=latest_patch.created_at if latest_patch else None,
        latest_test_passed=latest_test.passed if latest_test else None,
        latest_test_at=latest_test.created_at if latest_test else None,
        tests_after_latest_patch=tests_after_patch,
        quality_check_passed=latest_quality.passed if latest_quality else None,
        acceptance_checks=list(snapshot.contract.acceptance_checks),
        remaining_unknowns=list(snapshot.contract.unknowns),
        evidence_ids=[item.evidence_id for item in events],
        limitations=list(_LIMITATIONS),
    )

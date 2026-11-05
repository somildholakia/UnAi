from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from unai_engine.cognition.task_compiler import compile_task
from unai_engine.models import EvidenceRecord, TaskCompileRequest, CoverageGuidedProbeRequest
from unai_engine.runtime.patches import PatchManager
from unai_engine.verification.coverage_guided import (
    CoverageGuidanceError,
    CoverageGuidedProbePlanner,
    rank_coverage_guided_probes,
)
from unai_engine.verification.ledger import EvidenceLedger
from unai_engine.verification.probes import BoundaryProbeGenerator


SOURCE = (
    "def classify(value):\n"
    "    if value < 0:\n"
    "        return \"negative\"\n"
    "    else:\n"
    "        return \"non-negative\"\n"
)


def _digest(source: str = SOURCE) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _probe_candidates(source: str = SOURCE) -> list[dict]:
    # These are the exact deterministic candidate records produced by the AST generator.
    return [
        {
            "candidate_id": "probe-1",
            "function_name": "classify",
            "source_line": 2,
            "condition": "value < 0",
            "parameter_values": {"value": -1},
            "rationale": "below the threshold",
        },
        {
            "candidate_id": "probe-2",
            "function_name": "classify",
            "source_line": 2,
            "condition": "value < 0",
            "parameter_values": {"value": 0},
            "rationale": "at the threshold",
        },
        {
            "candidate_id": "probe-3",
            "function_name": "classify",
            "source_line": 2,
            "condition": "value < 0",
            "parameter_values": {"value": 1},
            "rationale": "above the threshold",
        },
    ]


def _coverage_branches(*, then_observed: bool, else_observed: bool) -> list[dict]:
    return [
        {
            "branch_id": "branch-001",
            "kind": "if",
            "line": 2,
            "condition": "value < 0",
            "then_observed": then_observed,
            "else_observed": else_observed,
        }
    ]


def test_ranking_prioritizes_only_the_unobserved_if_arm() -> None:
    ranked, status = rank_coverage_guided_probes(
        SOURCE,
        "classify",
        _probe_candidates(),
        _coverage_branches(then_observed=True, else_observed=False),
        limit=10,
    )

    assert status == "ranked"
    assert [item.candidate_id for item in ranked] == ["probe-2", "probe-3"]
    assert all(item.predicted_arm == "else" for item in ranked)
    assert all(item.unobserved_arms == ["else"] for item in ranked)
    assert all(item.priority == 100 for item in ranked)


def test_ranking_does_not_repeat_already_observed_arms() -> None:
    ranked, status = rank_coverage_guided_probes(
        SOURCE,
        "classify",
        _probe_candidates(),
        _coverage_branches(then_observed=True, else_observed=True),
    )

    assert status == "no_uncovered_candidates"
    assert ranked == []


def test_unsupported_calls_are_not_executed_for_arm_prediction() -> None:
    source = (
        "def classify(value):\n"
        "    if external_check(value):\n"
        "        return 1\n"
        "    else:\n"
        "        return 0\n"
    )
    candidates = [
        {
            "candidate_id": "probe-1",
            "function_name": "classify",
            "source_line": 2,
            "condition": "external_check(value)",
            "parameter_values": {"value": 1},
            "rationale": "truthiness probe",
        }
    ]
    ranked, status = rank_coverage_guided_probes(
        source,
        "classify",
        candidates,
        [
            {
                "branch_id": "branch-001",
                "kind": "if",
                "line": 2,
                "condition": "external_check(value)",
                "then_observed": True,
                "else_observed": False,
            }
        ],
    )

    assert status == "ranked"
    assert ranked[0].predicted_arm == "unknown"
    assert ranked[0].priority == 40


def test_ranking_rejects_ambiguous_symbol_and_bounded_limit() -> None:
    ambiguous = (
        "def classify(value):\n    if value: return 1\n"
        "class Other:\n    def classify(self, value): return value\n"
    )
    with pytest.raises(CoverageGuidanceError):
        rank_coverage_guided_probes(ambiguous, "missing", [], [])
    with pytest.raises(CoverageGuidanceError):
        rank_coverage_guided_probes(SOURCE, "classify", [], [], limit=61)


def _setup_planner(tmp_path: Path):
    workspace_root = tmp_path / "workspaces"
    project = workspace_root / "demo"
    project.mkdir(parents=True)
    (project / "service.py").write_text(SOURCE, encoding="utf-8")
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    contract = compile_task(
        TaskCompileRequest(
            request="Review classify boundary behavior",
            permissions=["workspace_read"],
        )
    )
    inspected = EvidenceRecord(
        evidence_id="inspection",
        task_id=contract.task_id,
        kind="precondition",
        source="test",
        claim="Workspace inspected.",
        passed=True,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    ledger.record_inspection(
        contract,
        "inspection_complete",
        [inspected],
        workspace="demo",
    )
    patch_manager = PatchManager(ledger)
    source = patch_manager.read_file(contract.task_id, "service.py", workspace_root)
    probes = BoundaryProbeGenerator(ledger).generate(
        contract.task_id,
        "service.py",
        "classify",
        source.content,
        source.sha256,
        12,
    )
    execution = EvidenceRecord(
        evidence_id="execution-1",
        task_id=contract.task_id,
        kind="execution",
        source="grounded-oracle-sandbox",
        claim="Grounded oracle cases were executed in the disposable Docker workspace.",
        passed=True,
        metadata={
            "target_file": "service.py",
            "target_function": "classify",
            "target_sha256": source.sha256,
        },
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    coverage = EvidenceRecord(
        evidence_id="coverage-1",
        task_id=contract.task_id,
        kind="coverage",
        source="grounded-coverage",
        claim="Coverage measured for supplied oracle cases.",
        passed=None,
        metadata={
            "schema": "unai.coverage.v1",
            "execution_evidence_id": execution.evidence_id,
            "target_file": "service.py",
            "symbol_name": "classify",
            "source_sha256": source.sha256,
            "trace_sha256": "a" * 64,
            "status": "complete",
            "branches": _coverage_branches(then_observed=True, else_observed=False),
        },
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    ledger.append_evidence(execution)
    ledger.append_evidence(coverage)
    planner = CoverageGuidedProbePlanner(ledger, patch_manager)
    request = CoverageGuidedProbeRequest(
        file_path="service.py",
        symbol_name="classify",
        source_sha256=source.sha256,
        probe_evidence_id=probes.evidence.evidence_id,
        coverage_evidence_id=coverage.evidence_id,
    )
    return planner, contract.task_id, workspace_root, request, ledger


def test_planner_binds_inputs_and_appends_non_verdict_evidence(tmp_path: Path) -> None:
    planner, task_id, workspace_root, request, ledger = _setup_planner(tmp_path)

    result = planner.generate(task_id, request, workspace_root)

    assert result.status == "ranked"
    assert [item.candidate_id for item in result.candidates] == ["probe-2", "probe-3"]
    assert result.evidence.kind == "probe"
    assert result.evidence.source == "coverage-guided-probes"
    assert result.evidence.passed is None
    assert result.evidence.metadata["executed"] is False
    assert result.evidence.metadata["expected_outputs_inferred"] is False
    snapshot = ledger.get_snapshot(task_id)
    assert snapshot is not None
    assert snapshot.evidence[-1].evidence_id == result.evidence.evidence_id


def test_planner_rejects_stale_source_hash(tmp_path: Path) -> None:
    planner, task_id, workspace_root, request, _ledger = _setup_planner(tmp_path)
    stale_request = request.model_copy(update={"source_sha256": "b" * 64})

    with pytest.raises(CoverageGuidanceError, match="stale"):
        planner.generate(task_id, stale_request, workspace_root)


def test_planner_rejects_coverage_from_different_target(tmp_path: Path) -> None:
    planner, task_id, workspace_root, request, ledger = _setup_planner(tmp_path)
    snapshot = ledger.get_snapshot(task_id)
    coverage = next(item for item in snapshot.evidence if item.evidence_id == request.coverage_evidence_id)
    wrong = coverage.model_copy(
        update={"metadata": {**coverage.metadata, "target_file": "other.py"}}
    )
    # Evidence is append-only; append a separate invalid record and point the
    # request at it rather than attempting to alter the original coverage event.
    wrong = wrong.model_copy(update={"evidence_id": "coverage-wrong"})
    ledger.append_evidence(wrong)
    invalid_request = request.model_copy(
        update={"coverage_evidence_id": "coverage-wrong"}
    )

    with pytest.raises(CoverageGuidanceError, match="different symbol"):
        planner.generate(task_id, invalid_request, workspace_root)

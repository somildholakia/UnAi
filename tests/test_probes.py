from datetime import datetime, timezone
from pathlib import Path

import pytest

from unai_engine.models import TaskCompileRequest
from unai_engine.cognition.task_compiler import compile_task
from unai_engine.verification.ledger import EvidenceLedger
from unai_engine.verification.probes import BoundaryProbeGenerator, ProbeTargetError


def _generator(tmp_path: Path):
    ledger = EvidenceLedger(tmp_path / "evidence.sqlite3")
    contract = compile_task(
        TaskCompileRequest(
            request="Review a profile service",
            permissions=["workspace_read"],
        )
    )
    # The generator only appends evidence to an existing inspected task.
    from unai_engine.models import EvidenceRecord

    ledger.record_inspection(
        contract,
        "inspection_complete",
        [
            EvidenceRecord(
                evidence_id="inspection",
                task_id=contract.task_id,
                kind="precondition",
                source="test",
                claim="Read access was approved",
                passed=True,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        ],
        workspace="demo",
    )
    return contract.task_id, ledger, BoundaryProbeGenerator(ledger)


def test_numeric_branch_generates_below_equal_and_above_probes(tmp_path: Path) -> None:
    task_id, ledger, generator = _generator(tmp_path)
    source = "def clamp(value: int):\n    if value < 10:\n        return 0\n    return value\n"

    result = generator.generate(
        task_id, "service.py", "clamp", source, "a" * 64, limit=20
    )

    values = [
        item.parameter_values["value"]
        for item in result.candidates
        if item.source_line == 2
    ]
    assert values == [9, 10, 11]
    assert result.status == "generated"
    assert result.evidence.passed is None
    assert result.evidence.metadata["executed"] is False
    snapshot = ledger.get_snapshot(task_id)
    assert snapshot is not None
    assert snapshot.evidence[-1].kind == "probe"


def test_len_boundary_respects_string_annotation(tmp_path: Path) -> None:
    task_id, _, generator = _generator(tmp_path)
    source = (
        "def shorten(value: str):\n"
        "    if len(value) > 4:\n"
        "        return value[:4]\n"
        "    return value\n"
    )

    result = generator.generate(
        task_id, "service.py", "shorten", source, "b" * 64, limit=10
    )
    values = [item.parameter_values["value"] for item in result.candidates]
    assert values == ["xxx", "xxxx", "xxxxx"]


def test_membership_branch_generates_member_and_non_member(tmp_path: Path) -> None:
    task_id, _, generator = _generator(tmp_path)
    source = (
        "def route(name: str):\n"
        "    if name in ('home', 'settings'):\n"
        "        return True\n"
        "    return False\n"
    )

    result = generator.generate(
        task_id, "routes.py", "route", source, "c" * 64, limit=10
    )
    values = [item.parameter_values["name"] for item in result.candidates]
    assert values[:2] == ["home", "settings"]
    assert "__not_member__" in values


def test_truthiness_branch_generates_typed_representatives(tmp_path: Path) -> None:
    task_id, _, generator = _generator(tmp_path)
    source = (
        "def has_name(name: str):\n"
        "    if not name:\n"
        "        return False\n"
        "    return True\n"
    )

    result = generator.generate(
        task_id, "users.py", "has_name", source, "d" * 64, limit=10
    )
    values = [item.parameter_values["name"] for item in result.candidates]
    assert values == ["", "probe-value"]


def test_probe_generation_caps_output_and_marks_it_unverified(tmp_path: Path) -> None:
    task_id, _, generator = _generator(tmp_path)
    source = (
        "def classify(value: int):\n"
        "    if value < 2:\n"
        "        return 0\n"
        "    if value < 5:\n"
        "        return 1\n"
        "    return 2\n"
    )

    result = generator.generate(
        task_id, "classify.py", "classify", source, "e" * 64, limit=2
    )
    assert len(result.candidates) == 2
    assert result.evidence.passed is None


def test_parse_error_is_recorded_as_failed_probe_generation(tmp_path: Path) -> None:
    task_id, _, generator = _generator(tmp_path)
    result = generator.generate(
        task_id, "broken.py", "anything", "def broken(:\n", "f" * 64
    )
    assert result.status == "parse_error"
    assert result.evidence.passed is False
    assert result.evidence.metadata["line"] == 1


def test_ambiguous_function_name_requires_qualified_name(tmp_path: Path) -> None:
    task_id, _, generator = _generator(tmp_path)
    source = (
        "def send(value: int):\n"
        "    if value < 2:\n"
        "        return False\n"
        "    return True\n"
        "class Client:\n"
        "    def send(self, value: int):\n"
        "        if value < 3:\n"
        "            return False\n"
        "        return True\n"
    )

    with pytest.raises(ProbeTargetError, match="ambiguous"):
        generator.generate(task_id, "client.py", "send", source, "1" * 64)


def test_non_python_source_is_rejected_as_unsupported(tmp_path: Path) -> None:
    task_id, _, generator = _generator(tmp_path)
    with pytest.raises(ProbeTargetError, match="Python files only"):
        generator.generate(task_id, "service.js", "handler", "function handler() {}", "a" * 64)

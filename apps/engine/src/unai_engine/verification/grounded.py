"""Specification/user-oracle-backed tests for selected unAI boundary probes.

This module never invents expected outputs. Callers must supply explicit expected
JSON values and a human-readable oracle reference. Generated pytest code is written
only to a disposable staging copy and executed through the existing Docker sandbox.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from unai_engine.models import (
    EvidenceRecord,
    GroundedCounterexample,
    GroundedTestApprovalRequest,
    GroundedTestResponse,
    TaskEvidenceSnapshot,
    TaskProofBundleResponse,
)
from unai_engine.runtime.patches import PatchConflict, PatchManager, PatchRejected
from unai_engine.runtime.test_runner import IsolatedPythonTestRunner
from unai_engine.verification.ledger import EvidenceLedger
from unai_engine.verification.proof import summarize_proof


_LIMITATIONS = [
    "Expected outputs are supplied by the requester; oracle references are recorded but not automatically authenticated against an external specification.",
    "A passing set of supplied cases does not prove general correctness or completeness of the task acceptance criteria.",
    "Undecorated, module-level synchronous and async Python functions with keyword-addressable parameters are supported; methods and decorated functions remain unsupported.",
    "A workspace specification artifact can be SHA-256-pinned, but that proves content integrity only, not publisher identity, authorship, authority or correctness.",
    "The selected function must return JSON-compatible values; exceptions and import/collection failures are reported as test failures, not silently converted into expected results.",
    "The sandbox stages a filtered copy. Missing project dependencies, omitted non-Python assets, or environment differences can make results unavailable or inconclusive.",
    "Execution traces retain hashes and redacted bounded previews, not a complete deterministic replay of process state.",
]


class GroundedVerificationError(RuntimeError):
    """A rejected grounded-verification request with an HTTP-compatible status."""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


def _json_text(value: Any, label: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise GroundedVerificationError(f"{label} must contain JSON-compatible values.") from exc


def _digest(value: Any, label: str) -> str:
    return hashlib.sha256(_json_text(value, label).encode("utf-8")).hexdigest()


def _target_function(source: str, symbol_name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    if not symbol_name.isidentifier():
        raise GroundedVerificationError("Only a simple module-level function name is supported.")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise GroundedVerificationError(
            f"Target Python source does not parse at line {exc.lineno}.", 409
        ) from exc

    matches = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol_name
    ]
    if len(matches) != 1:
        if not matches:
            raise GroundedVerificationError(
                f"Top-level Python function not found: {symbol_name}.", 404
            )
        raise GroundedVerificationError(
            f"Function name is ambiguous: {symbol_name}.", 409
        )

    function = matches[0]
    if function.decorator_list:
        raise GroundedVerificationError("Decorated functions are not yet supported for grounded tests.")
    if function.args.posonlyargs or function.args.vararg or function.args.kwarg:
        raise GroundedVerificationError(
            "Positional-only, *args and **kwargs signatures are not supported."
        )
    return function


def _signature(function: ast.FunctionDef) -> tuple[set[str], set[str]]:
    positional = list(function.args.args)
    defaults_start = len(positional) - len(function.args.defaults)
    required = {
        arg.arg for index, arg in enumerate(positional)
        if index < defaults_start
    }
    allowed = {arg.arg for arg in positional}
    for arg, default in zip(function.args.kwonlyargs, function.args.kw_defaults):
        allowed.add(arg.arg)
        if default is None:
            required.add(arg.arg)
    return allowed, required


def _candidate_index(
    snapshot: TaskEvidenceSnapshot,
    file_path: str,
    symbol_name: str,
    source_sha256: str,
) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    expected_ref = f"workspace-file:{file_path}#{symbol_name}"
    for event in snapshot.evidence:
        if (
            event.kind != "probe"
            or event.source != "ast-boundary-probes"
            or event.artifact_ref != expected_ref
            or event.metadata.get("source_sha256") != source_sha256
        ):
            continue
        for candidate in event.metadata.get("candidates", []):
            candidate_id = candidate.get("candidate_id")
            if candidate.get("function_name") == symbol_name and isinstance(candidate_id, str):
                candidates[candidate_id] = candidate
    return candidates


def _validate_cases(
    snapshot: TaskEvidenceSnapshot,
    request: GroundedTestApprovalRequest,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[dict[str, Any]]:
    allowed, required = _signature(function)
    candidates = _candidate_index(
        snapshot, request.file_path, request.symbol_name, request.source_sha256
    )
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    total_payload_bytes = 0

    for case in request.cases:
        artifact_path = case.oracle_artifact_path
        artifact_sha256 = case.oracle_artifact_sha256
        if (artifact_path is None) != (artifact_sha256 is None):
            raise GroundedVerificationError(
                f"Oracle case {case.case_id} must supply both oracle_artifact_path and oracle_artifact_sha256."
            )
        if artifact_path is not None:
            artifact_relative = PurePosixPath(artifact_path)
            if (
                case.oracle_kind != "specification"
                or artifact_relative.is_absolute()
                or artifact_relative.as_posix() != artifact_path
                or "\\" in artifact_path
                or any(part in {"", ".", ".."} for part in artifact_relative.parts)
                or artifact_relative.suffix.casefold() not in {".md", ".txt", ".rst", ".adoc"}
                or artifact_path == request.file_path
            ):
                raise GroundedVerificationError(
                    f"Oracle case {case.case_id} must reference a separate workspace-relative Markdown/text specification file."
                )
        if case.case_id in seen:
            raise GroundedVerificationError("Oracle case IDs must be unique within a run.")
        seen.add(case.case_id)
        arguments = dict(case.arguments)
        unknown = set(arguments) - allowed
        missing = required - set(arguments)
        if unknown:
            raise GroundedVerificationError(
                f"Oracle case {case.case_id} contains unknown parameters: {', '.join(sorted(unknown))}."
            )
        if missing:
            raise GroundedVerificationError(
                f"Oracle case {case.case_id} is missing required parameters: {', '.join(sorted(missing))}."
            )

        if case.probe_candidate_id:
            candidate = candidates.get(case.probe_candidate_id)
            if candidate is None:
                raise GroundedVerificationError(
                    f"Probe candidate {case.probe_candidate_id} was not found for this exact file, function and source hash.",
                    409,
                )
            candidate_arguments = candidate.get("parameter_values")
            if not isinstance(candidate_arguments, dict) or any(
                arguments.get(key) != value or type(arguments.get(key)) is not type(value)
                for key, value in candidate_arguments.items()
            ):
                raise GroundedVerificationError(
                    f"Oracle case {case.case_id} must preserve all inputs from probe {case.probe_candidate_id}."
                )

        arguments_json = _json_text(arguments, f"Arguments for case {case.case_id}")
        expected_json = _json_text(case.expected_output, f"Expected output for case {case.case_id}")
        arguments_bytes = len(arguments_json.encode("utf-8"))
        expected_bytes = len(expected_json.encode("utf-8"))
        if arguments_bytes > 10_000 or expected_bytes > 10_000:
            raise GroundedVerificationError(
                f"Oracle case {case.case_id} exceeds the 10 KB per-value limit."
            )
        total_payload_bytes += arguments_bytes + expected_bytes
        if total_payload_bytes > 200_000:
            raise GroundedVerificationError(
                "Grounded oracle payload exceeds the 200 KB aggregate limit."
            )
        normalized.append(
            {
                "case_id": case.case_id,
                "arguments": arguments,
                "expected_output": case.expected_output,
                "oracle_kind": case.oracle_kind,
                "oracle_reference": case.oracle_reference,
                "oracle_artifact_path": artifact_path,
                "oracle_artifact_sha256": artifact_sha256.lower() if artifact_sha256 else None,
                "probe_candidate_id": case.probe_candidate_id,
                "arguments_sha256": _digest(arguments, "Arguments"),
                "expected_sha256": _digest(case.expected_output, "Expected output"),
            }
        )
    return normalized


def _generated_pytest(
    file_path: str,
    symbol_name: str,
    cases: list[dict[str, Any]],
    target_sha256: str,
    *,
    is_async: bool = False,
) -> str:
    payload = _json_text(cases, "Oracle cases")
    target_hash_literal = repr(target_sha256)
    asyncio_import = "import asyncio\n" if is_async else ""
    invocation = (
        "asyncio.run(_FUNCTION(**case['arguments']))"
        if is_async
        else "_FUNCTION(**case['arguments'])"
    )
    # Embed a JSON string as a Python string literal; never interpolate caller text
    # as executable Python source.
    payload_literal = repr(payload)
    file_literal = repr(file_path)
    symbol_literal = repr(symbol_name)
    return f'''"""Generated by unAI from explicit, supplied oracle cases. Do not edit."""
from __future__ import annotations

{asyncio_import}import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_FILE_PATH = {file_literal}
_SYMBOL = {symbol_literal}
_CASES = json.loads({payload_literal})
_EXPECTED_TARGET_SHA256 = {target_hash_literal}
_TARGET = (_ROOT / _FILE_PATH).resolve()
if not _TARGET.is_relative_to(_ROOT) or not _TARGET.is_file():
    raise RuntimeError("Grounded test target is missing or outside the staged workspace.")
if hashlib.sha256(_TARGET.read_bytes()).hexdigest() != _EXPECTED_TARGET_SHA256:
    raise RuntimeError("Grounded test target changed after its verified source snapshot.")
for _case in _CASES:
    _oracle_path = _case.get("oracle_artifact_path")
    if not _oracle_path:
        continue
    _oracle_target = (_ROOT / _oracle_path).resolve()
    if not _oracle_target.is_relative_to(_ROOT) or not _oracle_target.is_file():
        raise RuntimeError("Pinned specification artifact is missing or outside the staged workspace.")
    _actual_oracle_sha256 = hashlib.sha256(_oracle_target.read_bytes()).hexdigest()
    if _actual_oracle_sha256 != _case.get("oracle_artifact_sha256"):
        raise RuntimeError("Pinned specification artifact changed before grounded test execution.")
sys.path.insert(0, str(_ROOT))
_SPEC = importlib.util.spec_from_file_location("unai_grounded_target", _TARGET)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("Could not load the grounded test target.")
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)
_FUNCTION = getattr(_MODULE, _SYMBOL, None)
if not callable(_FUNCTION):
    raise RuntimeError("Grounded test target is not callable.")


def _strict_equal(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _strict_equal(actual[key], expected[key]) for key in expected
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _strict_equal(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case["case_id"])
def test_grounded_oracle_case(case):
    try:
        actual = {invocation}
    except Exception as exc:
        details = {{
            "case_id": case["case_id"],
            "actual_type": type(exc).__name__,
            "actual_summary": repr(exc)[:300],
        }}
        raise AssertionError(
            "UNAI_COUNTEREXAMPLE:" + json.dumps(details, sort_keys=True, separators=(",", ":"))
        ) from exc
    if not _strict_equal(actual, case["expected_output"]):
        details = {{
            "case_id": case["case_id"],
            "actual_type": type(actual).__name__,
            "actual_summary": repr(actual)[:300],
        }}
        raise AssertionError(
            "UNAI_COUNTEREXAMPLE:" + json.dumps(details, sort_keys=True, separators=(",", ":"))
        )
'''


class GroundedTestVerifier:
    """Converts caller-grounded cases into disposable pytest tests and trace evidence."""

    def __init__(
        self,
        ledger: EvidenceLedger,
        patch_manager: PatchManager,
        test_runner: IsolatedPythonTestRunner,
    ) -> None:
        self.ledger = ledger
        self.patch_manager = patch_manager
        self.test_runner = test_runner

    def run(
        self,
        task_id: str,
        request: GroundedTestApprovalRequest,
        workspace_root,
    ) -> GroundedTestResponse:
        snapshot = self.ledger.get_snapshot(task_id)
        if snapshot is None:
            raise KeyError(task_id)
        if snapshot.status != "inspection_complete":
            raise GroundedVerificationError(
                "Task must complete read-only inspection before grounded tests.", 409
            )
        permissions = set(snapshot.contract.permissions)
        if not {"workspace_read", "run_tests"}.issubset(permissions):
            raise GroundedVerificationError(
                "This task must explicitly approve workspace_read and run_tests.", 403
            )
        relative = PurePosixPath(request.file_path)
        if (
            relative.is_absolute()
            or "\\" in request.file_path
            or any(part in {"", ".", ".."} for part in relative.parts)
            or not request.file_path.casefold().endswith(".py")
        ):
            raise GroundedVerificationError(
                "Grounded tests require a clean, workspace-relative Python file path."
            )

        try:
            source_snapshot = self.patch_manager.read_file(
                task_id, request.file_path, workspace_root
            )
        except (PatchRejected, PatchConflict):
            raise
        if source_snapshot.sha256.lower() != request.source_sha256.lower():
            raise GroundedVerificationError(
                "The source changed since the oracle request was prepared. Read the file and probes again.",
                409,
            )
        function = _target_function(source_snapshot.content, request.symbol_name)
        normalized = _validate_cases(snapshot, request, function)

        # A supplied workspace document hash pins the exact bytes used as the
        # specification reference. It does not authenticate the document author.
        artifact_fingerprints: dict[str, dict[str, Any]] = {}
        for case in normalized:
            artifact_path = case["oracle_artifact_path"]
            if not artifact_path:
                continue
            fingerprint = artifact_fingerprints.get(artifact_path)
            if fingerprint is None:
                artifact_snapshot = self.patch_manager.read_file(
                    task_id, artifact_path, workspace_root
                )
                fingerprint = {
                    "sha256": artifact_snapshot.sha256,
                    "size_bytes": artifact_snapshot.size_bytes,
                }
                artifact_fingerprints[artifact_path] = fingerprint
            if fingerprint["sha256"].lower() != case["oracle_artifact_sha256"]:
                raise GroundedVerificationError(
                    f"Specification artifact changed or does not match its supplied SHA-256: {artifact_path}.",
                    409,
                )
            case["oracle_artifact_size_bytes"] = fingerprint["size_bytes"]

        # Record every supplied oracle before executing it. A source/reference is
        # provenance, not an independently authenticated oracle.
        oracle_events: list[EvidenceRecord] = []
        now = datetime.now(timezone.utc).isoformat()
        for case in normalized:
            event = EvidenceRecord(
                evidence_id=uuid4().hex,
                task_id=task_id,
                kind="oracle",
                source="grounded-oracle",
                claim=f"Expected output supplied for grounded case {case['case_id']}.",
                passed=None,
                artifact_ref=f"oracle-case:{task_id}:{case['case_id']}",
                metadata={
                    "case_id": case["case_id"],
                    "file_path": request.file_path,
                    "symbol_name": request.symbol_name,
                    "source_sha256": source_snapshot.sha256,
                    "arguments_sha256": case["arguments_sha256"],
                    "expected_sha256": case["expected_sha256"],
                    "oracle_kind": case["oracle_kind"],
                    "oracle_reference": case["oracle_reference"],
                    "oracle_artifact_path": case["oracle_artifact_path"],
                    "oracle_artifact_sha256": case["oracle_artifact_sha256"],
                    "oracle_artifact_size_bytes": case.get("oracle_artifact_size_bytes"),
                    "oracle_artifact_integrity": (
                        "workspace-file-sha256-verified"
                        if case["oracle_artifact_path"]
                        else "requester-reference-only"
                    ),
                    "probe_candidate_id": case["probe_candidate_id"],
                },
                created_at=now,
            )
            self.ledger.append_evidence(event)
            oracle_events.append(event)

        is_async = isinstance(function, ast.AsyncFunctionDef)
        generated_content = _generated_pytest(
            request.file_path,
            request.symbol_name,
            normalized,
            source_snapshot.sha256,
            is_async=is_async,
        )
        generated_path = f"unai_generated_tests/test_oracle_{uuid4().hex}.py"
        run_metadata = {
            "target_file": request.file_path,
            "target_function": request.symbol_name,
            "target_is_async": is_async,
            "target_sha256": source_snapshot.sha256,
            "generated_test_sha256": hashlib.sha256(
                generated_content.encode("utf-8")
            ).hexdigest(),
            "oracle_case_ids": [case["case_id"] for case in normalized],
            "oracle_references": [
                {
                    "case_id": case["case_id"],
                    "kind": case["oracle_kind"],
                    "reference": case["oracle_reference"],
                    "oracle_artifact_path": case["oracle_artifact_path"],
                    "oracle_artifact_sha256": case["oracle_artifact_sha256"],
                    "probe_candidate_id": case["probe_candidate_id"],
                }
                for case in normalized
            ],
            "generated_test_path": generated_path,
        }

        # Re-check both the implementation and any pinned specification files
        # immediately before the staging runner copies the workspace.
        latest_source = self.patch_manager.read_file(
            task_id, request.file_path, workspace_root
        )
        if latest_source.sha256.lower() != source_snapshot.sha256.lower():
            raise GroundedVerificationError(
                "The target source changed after test generation and before sandbox staging.",
                409,
            )
        for artifact_path, fingerprint in artifact_fingerprints.items():
            latest_artifact = self.patch_manager.read_file(
                task_id, artifact_path, workspace_root
            )
            if latest_artifact.sha256.lower() != fingerprint["sha256"].lower():
                raise GroundedVerificationError(
                    f"Specification artifact changed after test generation and before sandbox staging: {artifact_path}.",
                    409,
                )
        run_metadata["source_fingerprint_rechecked_before_staging"] = True
        run_metadata["source_fingerprint_checked_in_staged_workspace"] = True
        run_metadata["oracle_artifact_fingerprints_rechecked_before_staging"] = sorted(
            artifact_fingerprints
        )

        execution = self.test_runner.run_generated_test(
            task_id,
            workspace_root,
            generated_path,
            generated_content,
            run_metadata,
        )

        output = "\n".join(
            [execution.stdout_preview or "", execution.stderr_preview or ""]
        )
        marker_values: dict[str, dict[str, str]] = {}
        recorded_markers = execution.evidence.metadata.get("counterexample_markers", [])
        if isinstance(recorded_markers, list):
            for marker in recorded_markers:
                if not isinstance(marker, dict):
                    continue
                case_id = marker.get("case_id")
                if isinstance(case_id, str) and case_id not in marker_values:
                    marker_values[case_id] = {
                        "actual_type": str(marker.get("actual_type", "unknown"))[:120],
                        "actual_summary": str(marker.get("actual_summary", ""))[:500],
                    }
        # Fallback for test doubles and legacy evidence without structured marker metadata.
        for match in re.findall(r"UNAI_COUNTEREXAMPLE:(\{[^\r\n]*\})", output):
            try:
                marker = json.loads(match)
            except json.JSONDecodeError:
                continue
            case_id = marker.get("case_id")
            if isinstance(case_id, str) and case_id not in marker_values:
                marker_values[case_id] = {
                    "actual_type": str(marker.get("actual_type", "unknown"))[:120],
                    "actual_summary": str(marker.get("actual_summary", ""))[:500],
                }

        case_by_id = {case["case_id"]: case for case in normalized}
        counterexamples: list[GroundedCounterexample] = []
        for case_id, marker in marker_values.items():
            case = case_by_id.get(case_id)
            if case is None:
                continue
            actual_summary = f"{marker['actual_type']}: {marker['actual_summary']}"
            counterexample = GroundedCounterexample(
                case_id=case_id,
                probe_candidate_id=case["probe_candidate_id"],
                oracle_kind=case["oracle_kind"],
                oracle_reference=case["oracle_reference"],
                actual_summary=actual_summary[:600],
            )
            counterexamples.append(counterexample)
            self.ledger.append_evidence(
                EvidenceRecord(
                    evidence_id=uuid4().hex,
                    task_id=task_id,
                    kind="counterexample",
                    source="grounded-oracle-comparator",
                    claim=f"Observed output did not match the supplied oracle for case {case_id}.",
                    passed=False,
                    artifact_ref=f"oracle-case:{task_id}:{case_id}",
                    metadata={
                        "case_id": case_id,
                        "probe_candidate_id": case["probe_candidate_id"],
                        "execution_evidence_id": execution.evidence.evidence_id,
                        "source_sha256": source_snapshot.sha256,
                        "arguments_sha256": case["arguments_sha256"],
                        "expected_sha256": case["expected_sha256"],
                        "oracle_kind": case["oracle_kind"],
                        "oracle_reference": case["oracle_reference"],
                        "actual_type": marker["actual_type"],
                        "actual_summary": marker["actual_summary"],
                    },
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            )

        stdout_bytes = (execution.stdout_preview or "").encode("utf-8")
        stderr_bytes = (execution.stderr_preview or "").encode("utf-8")
        trace_metadata = {
            "command": execution.command,
            "exit_code": execution.exit_code,
            "timed_out": execution.timed_out,
            "sandboxed": execution.sandboxed,
            "stdout_sha256": execution.evidence.metadata.get("stdout_sha256"),
            "stderr_sha256": execution.evidence.metadata.get("stderr_sha256"),
            "stdout_preview": execution.stdout_preview,
            "stderr_preview": execution.stderr_preview,
            "target_sha256": source_snapshot.sha256,
            "generated_test_sha256": run_metadata["generated_test_sha256"],
            "case_ids": [case["case_id"] for case in normalized],
            "output_preview_sha256": hashlib.sha256(stdout_bytes + b"\x00" + stderr_bytes).hexdigest(),
        }
        trace_metadata["trace_sha256"] = hashlib.sha256(
            _json_text(trace_metadata, "Execution trace").encode("utf-8")
        ).hexdigest()
        trace_evidence = EvidenceRecord(
            evidence_id=uuid4().hex,
            task_id=task_id,
            kind="trace",
            source="grounded-execution-trace",
            claim="Captured the bounded execution status, source fingerprints and redacted test-output previews.",
            passed=None,
            artifact_ref=f"trace:{task_id}:{execution.evidence.evidence_id}",
            metadata=trace_metadata,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.ledger.append_evidence(trace_evidence)

        passed_cases: int | None = None
        if execution.status == "passed":
            passed_cases = len(normalized)
        elif counterexamples:
            passed_cases = max(0, len(normalized) - len(counterexamples))

        return GroundedTestResponse(
            task_id=task_id,
            file_path=request.file_path,
            symbol_name=request.symbol_name,
            source_sha256=source_snapshot.sha256,
            status=execution.status,
            case_count=len(normalized),
            passed_cases=passed_cases,
            counterexamples=counterexamples,
            execution_evidence=execution.evidence,
            trace_evidence=trace_evidence,
            oracle_evidence=oracle_events,
            limitations=list(_LIMITATIONS),
        )


def build_proof_bundle(snapshot: TaskEvidenceSnapshot) -> TaskProofBundleResponse:
    """Build an evidence-indexed report without turning missing evidence into success."""
    events = sorted(snapshot.evidence, key=lambda item: (item.created_at, item.evidence_id))
    oracle_events = [
        item for item in events
        if item.kind == "oracle" and item.source == "grounded-oracle"
    ]
    grounded_runs = [
        item for item in events
        if item.kind == "execution" and item.source == "grounded-oracle-sandbox"
    ]
    counterexample_events = [
        item for item in events
        if item.kind == "counterexample" and item.source == "grounded-oracle-comparator"
    ]
    trace_events = [
        item for item in events
        if item.kind == "trace" and item.source == "grounded-execution-trace"
    ]
    probes = [
        item for item in events
        if item.kind == "probe" and item.source == "ast-boundary-probes"
    ]
    candidate_keys: set[tuple[str, str]] = set()
    verified_candidates: set[tuple[str, str]] = set()
    for event in probes:
        source_hash = str(event.metadata.get("source_sha256", ""))
        for candidate in event.metadata.get("candidates", []):
            candidate_id = candidate.get("candidate_id")
            if isinstance(candidate_id, str):
                candidate_keys.add((source_hash, candidate_id))

    # A supplied oracle is not enough: only a completed sandbox run establishes
    # that the linked probe case was actually exercised.
    for run in grounded_runs:
        if (
            run.passed is None
            or run.metadata.get("sandboxed") is not True
            or run.metadata.get("timed_out") is True
            or not isinstance(run.metadata.get("exit_code"), int)
        ):
            continue
        source_hash = str(run.metadata.get("target_sha256", ""))
        executed_case_ids = set(run.metadata.get("oracle_case_ids", []))
        for oracle in oracle_events:
            case_id = str(oracle.metadata.get("case_id", ""))
            candidate_id = oracle.metadata.get("probe_candidate_id")
            if (
                case_id in executed_case_ids
                and oracle.metadata.get("source_sha256") == source_hash
                and isinstance(candidate_id, str)
            ):
                verified_candidates.add((source_hash, candidate_id))
    unverified_count = len(candidate_keys - verified_candidates)

    counterexamples = [
        GroundedCounterexample(
            case_id=str(item.metadata.get("case_id", "unknown")),
            probe_candidate_id=item.metadata.get("probe_candidate_id"),
            oracle_kind=item.metadata.get("oracle_kind", "user"),
            oracle_reference=str(item.metadata.get("oracle_reference", "Unspecified oracle reference")),
            actual_summary=(
                f"{item.metadata.get('actual_type', 'unknown')}: "
                f"{item.metadata.get('actual_summary', '')}"
            )[:600],
        )
        for item in counterexample_events
    ]

    latest_run = grounded_runs[-1] if grounded_runs else None
    latest_run_counterexamples = [
        item for item in counterexample_events
        if latest_run is not None
        and item.metadata.get("execution_evidence_id") == latest_run.evidence_id
    ]
    if not grounded_runs:
        overall_status = "no_grounded_tests"
    elif latest_run is not None and latest_run.passed is None:
        overall_status = "grounded_tests_unavailable"
    elif latest_run is not None and latest_run.passed is False and latest_run_counterexamples:
        overall_status = "counterexamples_found"
    elif latest_run is not None and latest_run.passed is False:
        overall_status = "grounded_tests_failed"
    else:
        overall_status = "grounded_tests_passed"

    risks = list(snapshot.contract.unknowns)
    if not oracle_events:
        risks.append("No user- or specification-referenced expected outputs have been executed.")
    else:
        risks.append(
            "Oracle provenance is requester-supplied; a workspace artifact hash proves content integrity only, not publisher identity, authorship, authority or correctness."
        )
        if any(not item.metadata.get("oracle_artifact_sha256") for item in oracle_events):
            risks.append("One or more specification references are not bound to a SHA-256-pinned workspace document.")
    if unverified_count:
        risks.append(
            f"{unverified_count} AST-generated probe candidate(s) have not been tied to a grounded oracle case."
        )
    if counterexamples:
        risks.append(
            "Counterexamples are tied to the source hash recorded at execution time; later edits require fresh reruns."
        )
    if not any(item.passed is True for item in grounded_runs):
        risks.append("No passing grounded-oracle execution is currently recorded.")
    risks.extend(
        [
            "Existing passing tests and a clean Python parse are evidence, not proof of complete semantic correctness.",
            "The verifier currently does not perform automatic shrinking, symbolic execution, or an independent model review.",
        ]
    )

    return TaskProofBundleResponse(
        task_id=snapshot.task_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        overall_status=overall_status,
        task_proof=summarize_proof(snapshot),
        grounded_case_count=len(oracle_events),
        grounded_run_count=len(grounded_runs),
        grounded_counterexample_count=len(counterexample_events),
        unverified_probe_candidate_count=unverified_count,
        counterexamples=counterexamples,
        oracle_evidence=oracle_events[-100:],
        execution_traces=trace_events[-30:],
        unresolved_risks=risks,
        limitations=list(_LIMITATIONS),
    )

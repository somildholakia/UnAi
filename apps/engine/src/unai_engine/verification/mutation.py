"""Bounded mutation-based test-strength verification for grounded oracle suites.

A mutant is a deterministic, single-token change inside the selected Python
function or method body. Mutants are executed with the same generated pytest
harness and the same disposable Docker staging copy as the grounded oracle run.
The baseline (unmutated) suite must pass first; a mutant is "killed" only when
that suite fails against the mutated source.

This module does not infer expected outputs, does not mutate arbitrary files and
does not claim that a high mutation score proves correctness.
"""
from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from unai_engine.models import (
    EvidenceRecord,
    GroundedMutantResult,
    GroundedMutationApprovalRequest,
    GroundedMutationResponse,
    GroundedTestResponse,
    TaskEvidenceSnapshot,
)
from unai_engine.runtime.patches import PatchManager
from unai_engine.runtime.test_runner import IsolatedPythonTestRunner
from unai_engine.verification.grounded import (
    GroundedTestApprovalRequest,
    GroundedTestVerifier,
    GroundedVerificationError,
    _generated_pytest,
    _target_descriptor,
    _validate_cases,
)
from unai_engine.verification.ledger import EvidenceLedger


MAX_MUTANTS_PER_REQUEST = 8
MAX_MUTATED_SOURCE_BYTES = 300_000
MUTATION_EVIDENCE_SOURCE = "grounded-mutation-sandbox"
MUTATION_SUMMARY_SOURCE = "grounded-mutation-summary"

_NON_DETERMINISTIC_PROPERTIES = {
    "output_stable_across_replays",
    "workspace_filesystem_unchanged",
    "external_fixture_filesystem_unchanged",
}

_LIMITATIONS = [
    "Mutation results measure whether the supplied oracle cases distinguish specific single-token source changes; they do not prove the oracle cases are correct or complete.",
    "Only one deterministic mutation per mutant is generated inside the selected target body, using a fixed allowlist of comparison, boolean, arithmetic and literal changes.",
    "At most 8 mutants and 9 sandbox rounds (1 baseline + 8 mutants) run per request; each round uses the existing disposable Docker staging copy.",
    "Nested functions, lambdas, classes, chained comparisons, annotation expressions and docstrings are not mutated. Equivalent mutants can survive and are reported as survivors.",
    "Non-deterministic replay/filesystem property cases are rejected because their failure would not identify a killed mutant.",
    "The target source and every mutant harness are re-read or regenerated under the exact source/specification fingerprints verified by the baseline grounded run.",
]


class MutationPlanningError(ValueError):
    """The requested source cannot be mutated within the bounded policy."""


@dataclass(frozen=True, slots=True)
class MutationCandidate:
    mutant_id: str
    operator: str
    line: int
    column: int
    original: str
    replacement: str
    description: str
    mutant_source: str
    mutant_sha256: str


@dataclass(frozen=True, slots=True)
class _DraftMutation:
    start: int
    end: int
    operator: str
    line: int
    column: int
    original: str
    replacement: str


_COMPARISON_SWAPS: dict[str, tuple[str, str, str]] = {
    "Lt": ("<", "LtE", r"(?<![<>=!])<(?![<=])"),
    "LtE": ("<=", "Lt", r"<="),
    "Gt": (">", "GtE", r"(?<![<>=])>(?![>=])"),
    "GtE": (">=", "Gt", r">="),
    "Eq": ("==", "NotEq", r"=="),
    "NotEq": ("!=", "Eq", r"!="),
    "In": ("in", "NotIn", r"\bin\b"),
    "NotIn": ("not in", "In", r"\bnot\s+in\b"),
    "Is": ("is", "IsNot", r"\bis\b"),
    "IsNot": ("is not", "Is", r"\bis\s+not\b"),
}
_BOOLEAN_SWAPS: dict[str, tuple[str, str, str]] = {
    "And": ("and", "Or", r"\band\b"),
    "Or": ("or", "And", r"\bor\b"),
}
_ARITHMETIC_SWAPS: dict[str, tuple[str, str, str]] = {
    "Add": ("+", "Sub", r"(?<![+])\+(?![+=])"),
    "Sub": ("-", "Add", r"(?<![-])-(?![-=])"),
    "Mult": ("*", "FloorDiv", r"(?<![*])\*(?![*=])"),
    "FloorDiv": ("//", "Mult", r"//"),
}


def _line_offsets(source: str) -> tuple[list[str], list[int]]:
    lines = source.splitlines(keepends=True)
    if not lines:
        lines = [""]
    starts: list[int] = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line)
    return lines, starts


def _character_span(
    source: str, lines: list[str], starts: list[int], node: ast.AST
) -> tuple[int, int] | None:
    lineno = getattr(node, "lineno", None)
    column = getattr(node, "col_offset", None)
    end_lineno = getattr(node, "end_lineno", None)
    end_column = getattr(node, "end_col_offset", None)
    if (
        not isinstance(lineno, int)
        or not isinstance(column, int)
        or not isinstance(end_lineno, int)
        or not isinstance(end_column, int)
        or lineno < 1
        or end_lineno < lineno
        or lineno > len(lines)
        or end_lineno > len(lines)
        or column < 0
        or end_column < 0
    ):
        return None
    try:
        start = starts[lineno - 1] + len(lines[lineno - 1].encode("utf-8")[:column].decode("utf-8"))
        end = starts[end_lineno - 1] + len(lines[end_lineno - 1].encode("utf-8")[:end_column].decode("utf-8"))
    except (UnicodeDecodeError, IndexError):
        return None
    if not 0 <= start < end <= len(source):
        return None
    return start, end


def _unique_token(gap: str, pattern: str) -> tuple[int, int] | None:
    matches = list(re.finditer(pattern, gap))
    if len(matches) != 1:
        return None
    return matches[0].start(), matches[0].end()


def _draft(
    start: int,
    end: int,
    operator: str,
    original: str,
    replacement: str,
    node: ast.AST,
) -> _DraftMutation:
    return _DraftMutation(
        start=start,
        end=end,
        operator=operator,
        line=max(1, int(getattr(node, "lineno", 1))),
        column=max(0, int(getattr(node, "col_offset", 0))),
        original=original,
        replacement=replacement,
    )


def _comparison_drafts(
    node: ast.Compare,
    source: str,
    lines: list[str],
    starts: list[int],
) -> list[_DraftMutation]:
    if len(node.ops) != 1 or len(node.comparators) != 1:
        return []
    left_span = _character_span(source, lines, starts, node.left)
    right_span = _character_span(source, lines, starts, node.comparators[0])
    if left_span is None or right_span is None or left_span[1] > right_span[0]:
        return []
    swap = _COMPARISON_SWAPS.get(type(node.ops[0]).__name__)
    if swap is None:
        return []
    original, replacement_operator, pattern = swap
    gap = source[left_span[1]:right_span[0]]
    token_span = _unique_token(gap, pattern)
    if token_span is None:
        return []
    return [
        _draft(
            left_span[1] + token_span[0],
            left_span[1] + token_span[1],
            f"compare:{type(node.ops[0]).__name__}->{replacement_operator}",
            original,
            {"LtE": "<=", "Lt": "<", "GtE": ">=", "Gt": ">", "NotEq": "!=", "Eq": "==",
             "NotIn": "not in", "In": "in", "IsNot": "is not", "Is": "is"}[replacement_operator],
            node,
        )
    ]


def _boolean_drafts(
    node: ast.BoolOp,
    source: str,
    lines: list[str],
    starts: list[int],
) -> list[_DraftMutation]:
    swap = _BOOLEAN_SWAPS.get(type(node.op).__name__)
    if swap is None or len(node.values) < 2:
        return []
    original, replacement_operator, pattern = swap
    drafts: list[_DraftMutation] = []
    for left, right in zip(node.values, node.values[1:]):
        left_span = _character_span(source, lines, starts, left)
        right_span = _character_span(source, lines, starts, right)
        if left_span is None or right_span is None or left_span[1] > right_span[0]:
            continue
        gap = source[left_span[1]:right_span[0]]
        token_span = _unique_token(gap, pattern)
        if token_span is None:
            continue
        drafts.append(
            _draft(
                left_span[1] + token_span[0],
                left_span[1] + token_span[1],
                f"boolean:{type(node.op).__name__}->{replacement_operator}",
                original,
                replacement_operator.lower(),
                node,
            )
        )
    return drafts


def _arithmetic_drafts(
    node: ast.BinOp,
    source: str,
    lines: list[str],
    starts: list[int],
) -> list[_DraftMutation]:
    swap = _ARITHMETIC_SWAPS.get(type(node.op).__name__)
    if swap is None:
        return []
    left_span = _character_span(source, lines, starts, node.left)
    right_span = _character_span(source, lines, starts, node.right)
    if left_span is None or right_span is None or left_span[1] > right_span[0]:
        return []
    original, replacement_operator, pattern = swap
    gap = source[left_span[1]:right_span[0]]
    token_span = _unique_token(gap, pattern)
    if token_span is None:
        return []
    return [
        _draft(
            left_span[1] + token_span[0],
            left_span[1] + token_span[1],
            f"arithmetic:{type(node.op).__name__}->{replacement_operator}",
            original,
            {"Sub": "-", "Add": "+", "FloorDiv": "//", "Mult": "*"}[replacement_operator],
            node,
        )
    ]


def _constant_draft(
    node: ast.Constant,
    source: str,
    lines: list[str],
    starts: list[int],
) -> _DraftMutation | None:
    span = _character_span(source, lines, starts, node)
    if span is None:
        return None
    value = node.value
    if type(value) is bool:
        original = "True" if value else "False"
        replacement = "False" if value else "True"
        operator = "literal:bool->bool"
    elif type(value) is int and abs(value) < 10**9:
        original = str(value)
        replacement = str(value + 1)
        operator = "literal:int->int"
    elif type(value) is float:
        if value != value or value in {float("inf"), float("-inf")}:
            return None
        original = repr(value)
        replacement = repr(value + 1.0)
        operator = "literal:float->float"
    elif type(value) is str:
        if value == "":
            return None
        original = value
        replacement = ""
        operator = "literal:str->empty"
    else:
        return None
    # Refuse semantically unchanged replacements such as a huge float rounding.
    if original == replacement:
        return None
    return _draft(span[0], span[1], operator, original, replacement, node)


def _collect_drafts(source: str, function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[_DraftMutation]:
    lines, starts = _line_offsets(source)
    drafts: list[_DraftMutation] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        if isinstance(node, ast.AnnAssign):
            # Do not mutate type annotations; only the assigned value is runtime code.
            if node.value is not None:
                visit(node.value)
            return
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            # A docstring is not an executable decision or output.
            return
        if isinstance(node, ast.Compare):
            drafts.extend(_comparison_drafts(node, source, lines, starts))
        elif isinstance(node, ast.BoolOp):
            drafts.extend(_boolean_drafts(node, source, lines, starts))
        elif isinstance(node, ast.BinOp):
            drafts.extend(_arithmetic_drafts(node, source, lines, starts))
        elif isinstance(node, ast.Constant):
            candidate = _constant_draft(node, source, lines, starts)
            if candidate is not None:
                drafts.append(candidate)
        for child in ast.iter_child_nodes(node):
            visit(child)

    for statement in function.body:
        visit(statement)
    drafts.sort(key=lambda item: (item.start, item.end, item.operator, item.original, item.replacement))
    return drafts


def plan_mutations(
    source: str,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    max_mutants: int,
) -> list[MutationCandidate]:
    """Return deterministic, compile-checked single-token mutants.

    Only the selected function body is considered. At most ``max_mutants``
    distinct source variants are returned, ordered by source position.
    """
    if not isinstance(max_mutants, int) or isinstance(max_mutants, bool):
        raise MutationPlanningError("Mutation budget must be an integer.")
    if not 1 <= max_mutants <= MAX_MUTANTS_PER_REQUEST:
        raise MutationPlanningError(
            f"Mutation budget must be between 1 and {MAX_MUTANTS_PER_REQUEST}."
        )
    if not isinstance(source, str) or not source.strip():
        raise MutationPlanningError("Mutation source must be non-empty text.")
    if len(source.encode("utf-8")) > MAX_MUTATED_SOURCE_BYTES:
        raise MutationPlanningError("Mutation source exceeds the safe source size limit.")
    if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise MutationPlanningError("A target function or method is required.")

    candidates: list[MutationCandidate] = []
    seen_hashes: set[str] = set()
    for draft in _collect_drafts(source, function):
        if len(candidates) >= max_mutants:
            break
        if draft.start >= draft.end:
            continue
        mutant_source = source[:draft.start] + draft.replacement + source[draft.end:]
        if mutant_source == source:
            continue
        try:
            compile(mutant_source, "<unai-mutant>", "exec")
        except (SyntaxError, ValueError):
            continue
        digest = hashlib.sha256(mutant_source.encode("utf-8")).hexdigest()
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        candidates.append(
            MutationCandidate(
                mutant_id=f"mut-{len(candidates) + 1:03d}",
                operator=draft.operator,
                line=draft.line,
                column=draft.column,
                original=draft.original[:80],
                replacement=draft.replacement[:80],
                description=(
                    f"line {draft.line}: {draft.operator} "
                    f"({draft.original[:40]!r} -> {draft.replacement[:40]!r})"
                )[:240],
                mutant_source=mutant_source,
                mutant_sha256=digest,
            )
        )
    return candidates


def _response_summary(
    status: str,
    baseline: GroundedTestResponse,
    mutants: list[GroundedMutantResult],
    *,
    max_mutants: int,
    reason: str | None = None,
) -> dict[str, Any]:
    killed = sum(1 for item in mutants if item.status == "killed")
    survived = sum(1 for item in mutants if item.status == "survived")
    unavailable = sum(1 for item in mutants if item.status == "unavailable")
    executed = killed + survived
    return {
        "schema": "unai.mutation-run.v1",
        "status": status,
        "baseline_status": baseline.status,
        "baseline_evidence_id": baseline.execution_evidence.evidence_id,
        "target_file": baseline.file_path,
        "target_function": baseline.symbol_name,
        "target_sha256": baseline.source_sha256,
        "max_mutants": max_mutants,
        "mutants_total": len(mutants),
        "mutants_killed": killed,
        "mutants_survived": survived,
        "mutants_unavailable": unavailable,
        "mutants_executed": executed,
        "mutation_score": (killed / executed) if executed else None,
        "mutant_ids": [item.mutant_id for item in mutants],
        "reason": reason,
    }


class MutationTestVerifier:
    """Run bounded mutation testing after a passing grounded oracle baseline."""

    def __init__(
        self,
        ledger: EvidenceLedger,
        patch_manager: PatchManager,
        test_runner: IsolatedPythonTestRunner,
        grounded_verifier: GroundedTestVerifier,
    ) -> None:
        self.ledger = ledger
        self.patch_manager = patch_manager
        self.test_runner = test_runner
        self.grounded_verifier = grounded_verifier

    def _summary_evidence(
        self,
        task_id: str,
        metadata: dict[str, Any],
        status: str,
    ) -> EvidenceRecord:
        passed = True if status == "passed" else False if status == "failed" else None
        evidence = EvidenceRecord(
            evidence_id=uuid4().hex,
            task_id=task_id,
            kind="mutation",
            source=MUTATION_SUMMARY_SOURCE,
            claim=(
                f"Mutation run {status}: {metadata.get('mutants_killed', 0)} killed, "
                f"{metadata.get('mutants_survived', 0)} survived, "
                f"{metadata.get('mutants_unavailable', 0)} unavailable."
            ),
            passed=passed,
            artifact_ref=f"task:{task_id}/mutation-tests",
            metadata=metadata,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.ledger.append_evidence(evidence)
        return evidence

    def run(
        self,
        task_id: str,
        request: GroundedMutationApprovalRequest,
        workspace_root,
    ) -> GroundedMutationResponse:
        snapshot = self.ledger.get_snapshot(task_id)
        if snapshot is None:
            raise KeyError(task_id)
        if snapshot.status != "inspection_complete":
            raise GroundedVerificationError(
                "Task must complete read-only inspection before mutation tests.", 409
            )
        if not {"workspace_read", "run_tests"}.issubset(set(snapshot.contract.permissions)):
            raise GroundedVerificationError(
                "This task must explicitly approve workspace_read and run_tests.", 403
            )
        for case in request.cases:
            if (
                case.oracle_property is not None
                and case.oracle_property.kind in _NON_DETERMINISTIC_PROPERTIES
            ):
                raise GroundedVerificationError(
                    "Mutation testing rejects replay and filesystem-audit property cases; "
                    "their failures do not identify a killed mutant.",
                    422,
                )

        baseline_request = GroundedTestApprovalRequest(
            confirm="run-grounded-oracle-tests",
            file_path=request.file_path,
            symbol_name=request.symbol_name,
            source_sha256=request.source_sha256,
            cases=request.cases,
        )
        baseline = self.grounded_verifier.run(task_id, baseline_request, workspace_root)

        if baseline.status != "passed":
            metadata = _response_summary(
                baseline.status,
                baseline,
                [],
                max_mutants=request.max_mutants,
                reason="baseline_grounded_suite_not_passed",
            )
            summary = self._summary_evidence(task_id, metadata, baseline.status)
            return GroundedMutationResponse(
                task_id=task_id,
                file_path=request.file_path,
                symbol_name=request.symbol_name,
                source_sha256=baseline.source_sha256,
                status=baseline.status,
                baseline_status=baseline.status,
                baseline_evidence=baseline.execution_evidence,
                mutants_total=0,
                mutants_killed=0,
                mutants_survived=0,
                mutants_unavailable=0,
                mutation_score=None,
                mutants=[],
                summary_evidence=summary,
                limitations=list(_LIMITATIONS),
            )

        # Re-read the exact source before planning mutants; the baseline did the
        # same immediately before its own staging pass.
        source_snapshot = self.patch_manager.read_file(
            task_id, request.file_path, workspace_root
        )
        if source_snapshot.sha256.lower() != request.source_sha256.lower():
            raise GroundedVerificationError(
                "The target source changed after the mutation request was prepared. "
                "Read the file and probes again.",
                409,
            )
        target = _target_descriptor(source_snapshot.content, request.symbol_name)
        normalized = _validate_cases(snapshot, baseline_request, target)
        # Re-check every pinned specification artifact before mutant rounds. A
        # changed artifact fails the harness and would otherwise look like a
        # killed mutant instead of a stale request.
        artifact_fingerprints: dict[str, str] = {}
        for case in normalized:
            artifact_path = case.get("oracle_artifact_path")
            if not artifact_path:
                continue
            if artifact_path not in artifact_fingerprints:
                artifact_snapshot = self.patch_manager.read_file(
                    task_id, artifact_path, workspace_root
                )
                artifact_fingerprints[artifact_path] = artifact_snapshot.sha256.lower()
            if artifact_fingerprints[artifact_path] != (
                case.get("oracle_artifact_sha256") or ""
            ).lower():
                raise GroundedVerificationError(
                    "A pinned specification artifact changed after the baseline run.",
                    409,
                )
        execution_cases = [
            {
                key: value
                for key, value in case.items()
                if key not in {
                    "oracle_attestation",
                    "oracle_publisher_attestation",
                    "oracle_attestation_provenance",
                    "oracle_publisher_attestation_provenance",
                }
            }
            for case in normalized
        ]
        try:
            mutants = plan_mutations(
                source_snapshot.content, target["function"], request.max_mutants
            )
        except MutationPlanningError as exc:
            raise GroundedVerificationError(str(exc), 422) from exc

        if not mutants:
            metadata = _response_summary(
                "unavailable",
                baseline,
                [],
                max_mutants=request.max_mutants,
                reason="no_applicable_mutations",
            )
            summary = self._summary_evidence(task_id, metadata, "unavailable")
            return GroundedMutationResponse(
                task_id=task_id,
                file_path=request.file_path,
                symbol_name=request.symbol_name,
                source_sha256=source_snapshot.sha256,
                status="unavailable",
                baseline_status=baseline.status,
                baseline_evidence=baseline.execution_evidence,
                mutants_total=0,
                mutants_killed=0,
                mutants_survived=0,
                mutants_unavailable=0,
                mutation_score=None,
                mutants=[],
                summary_evidence=summary,
                limitations=list(_LIMITATIONS),
            )

        function = target["function"]
        is_async = isinstance(function, ast.AsyncFunctionDef)
        invocation_kind = target["invocation_kind"]
        results: list[GroundedMutantResult] = []
        for mutant in mutants:
            generated_path = f"unai_generated_tests/test_mutation_{uuid4().hex}.py"
            generated_content = _generated_pytest(
                request.file_path,
                request.symbol_name,
                execution_cases,
                mutant.mutant_sha256,
                is_async=is_async,
                invocation_kind=invocation_kind,
                replay_trace_path=None,
            )
            metadata = {
                "schema": "unai.mutation.v1",
                "target_file": request.file_path,
                "target_function": request.symbol_name,
                "target_sha256": source_snapshot.sha256,
                "mutant_id": mutant.mutant_id,
                "mutation_operator": mutant.operator,
                "mutation_line": mutant.line,
                "mutation_column": mutant.column,
                "mutant_sha256": mutant.mutant_sha256,
                "generated_test_sha256": hashlib.sha256(
                    generated_content.encode("utf-8")
                ).hexdigest(),
                "baseline_evidence_id": baseline.execution_evidence.evidence_id,
                "oracle_case_ids": [case["case_id"] for case in normalized],
                "artifact_fingerprints_rechecked_before_mutants": sorted(
                    artifact_fingerprints
                ),
            }
            execution = self.test_runner.run_generated_test(
                task_id,
                workspace_root,
                generated_path,
                generated_content,
                metadata,
                evidence_source=MUTATION_EVIDENCE_SOURCE,
                source_overrides={request.file_path: mutant.mutant_source},
            )
            if execution.status == "unavailable":
                result_status = "unavailable"
            elif execution.status == "failed":
                result_status = "killed"
            else:
                result_status = "survived"
            results.append(
                GroundedMutantResult(
                    mutant_id=mutant.mutant_id,
                    description=mutant.description,
                    operator=mutant.operator,
                    line=mutant.line,
                    column=mutant.column,
                    mutant_sha256=mutant.mutant_sha256,
                    status=result_status,
                    exit_code=execution.exit_code,
                    timed_out=execution.timed_out,
                    sandboxed=execution.sandboxed,
                    execution_evidence=execution.evidence,
                )
            )

        killed = sum(1 for item in results if item.status == "killed")
        survived = sum(1 for item in results if item.status == "survived")
        unavailable = sum(1 for item in results if item.status == "unavailable")
        executed = killed + survived
        if survived:
            status = "failed"
        elif unavailable:
            status = "unavailable"
        else:
            status = "passed"
        metadata = _response_summary(
            status,
            baseline,
            results,
            max_mutants=request.max_mutants,
        )
        summary = self._summary_evidence(task_id, metadata, status)
        return GroundedMutationResponse(
            task_id=task_id,
            file_path=request.file_path,
            symbol_name=request.symbol_name,
            source_sha256=source_snapshot.sha256,
            status=status,
            baseline_status=baseline.status,
            baseline_evidence=baseline.execution_evidence,
            mutants_total=len(results),
            mutants_killed=killed,
            mutants_survived=survived,
            mutants_unavailable=unavailable,
            mutation_score=(killed / executed) if executed else None,
            mutants=results,
            summary_evidence=summary,
            limitations=list(_LIMITATIONS),
        )

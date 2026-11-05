"""Coverage-guided ranking of existing, still-unverified AST probe candidates.

This module never executes the selected program, synthesizes oracle outputs, or
marks a candidate as passed. It uses a source-bound coverage result to prioritize
candidate inputs that statically target an unobserved arm of a simple if.
"""
from __future__ import annotations

import ast
import math
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from unai_engine.models import (
    CoverageGuidedProbeCandidate,
    CoverageGuidedProbeRequest,
    CoverageGuidedProbeResponse,
    EvidenceRecord,
)
from unai_engine.runtime.patches import PatchManager
from unai_engine.verification.coverage import build_coverage_plan
from unai_engine.verification.ledger import EvidenceLedger
from unai_engine.verification.probes import _collect_functions


MAX_CANDIDATES = 60
MAX_VALUE_DEPTH = 8
MAX_CONTAINER_ITEMS = 128
MAX_STRING_CHARS = 10_000
MAX_INTEGER_BITS = 256
MAX_EXPRESSION_DEPTH = 16

_LIMITATIONS = [
    "Candidates remain unverified suggestions; no target code is executed by this endpoint.",
    "No expected outputs or property-oracle results are inferred.",
    "Static arm prediction supports only bounded expressions over the candidate's supplied JSON values.",
    "Only explicit if/else arms represented by the coverage plan are guided; implicit fall-through and loop termination are not claimed.",
    "A prioritized candidate must still receive an explicit user/specification oracle before grounded execution.",
]


class CoverageGuidanceError(ValueError):
    """Coverage/probe evidence is missing, stale, or not eligible for guidance."""


def _bounded_native(value: Any, depth: int = 0) -> bool:
    if depth > MAX_VALUE_DEPTH:
        return False
    if value is None or isinstance(value, (bool, str)):
        return not isinstance(value, str) or len(value) <= MAX_STRING_CHARS
    if isinstance(value, int):
        return value.bit_length() <= MAX_INTEGER_BITS
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, (list, tuple)):
        return len(value) <= MAX_CONTAINER_ITEMS and all(
            _bounded_native(item, depth + 1) for item in value
        )
    if isinstance(value, dict):
        return len(value) <= MAX_CONTAINER_ITEMS and all(
            isinstance(key, str)
            and len(key) <= MAX_STRING_CHARS
            and _bounded_native(item, depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, set):
        return len(value) <= MAX_CONTAINER_ITEMS and all(
            _bounded_native(item, depth + 1) for item in value
        )
    return False


def _safe_expression(node: ast.AST, values: dict[str, Any], depth: int = 0) -> Any:
    """Evaluate a deliberately small AST expression subset without eval/exec."""
    if depth > MAX_EXPRESSION_DEPTH:
        raise ValueError("expression_depth")
    if isinstance(node, ast.Constant):
        value = node.value
        if not _bounded_native(value):
            raise ValueError("constant_budget")
        return value
    if isinstance(node, ast.Name):
        if node.id not in values:
            raise ValueError("missing_name")
        value = values[node.id]
        if not _bounded_native(value):
            raise ValueError("value_budget")
        return value
    if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
        value = ast.literal_eval(node)
        if not _bounded_native(value):
            raise ValueError("literal_budget")
        return value
    if isinstance(node, ast.UnaryOp):
        operand = _safe_expression(node.operand, values, depth + 1)
        if isinstance(node.op, ast.Not):
            return not bool(operand)
        if isinstance(operand, bool) or not isinstance(operand, (int, float)):
            raise ValueError("unary_type")
        if isinstance(node.op, ast.UAdd):
            result = +operand
        elif isinstance(node.op, ast.USub):
            result = -operand
        else:
            raise ValueError("unary_operator")
        if not _bounded_native(result):
            raise ValueError("unary_budget")
        return result
    if isinstance(node, ast.BoolOp):
        if not node.values:
            raise ValueError("empty_boolean")
        result = _safe_expression(node.values[0], values, depth + 1)
        for item in node.values[1:]:
            if isinstance(node.op, ast.And) and not bool(result):
                return result
            if isinstance(node.op, ast.Or) and bool(result):
                return result
            result = _safe_expression(item, values, depth + 1)
        return result
    if isinstance(node, ast.BinOp):
        left = _safe_expression(node.left, values, depth + 1)
        right = _safe_expression(node.right, values, depth + 1)
        if (
            isinstance(left, bool)
            or isinstance(right, bool)
            or not isinstance(left, (int, float))
            or not isinstance(right, (int, float))
        ):
            raise ValueError("binary_type")
        if isinstance(node.op, ast.Add):
            result = left + right
        elif isinstance(node.op, ast.Sub):
            result = left - right
        elif isinstance(node.op, ast.Mult):
            result = left * right
        elif isinstance(node.op, ast.FloorDiv):
            result = left // right
        elif isinstance(node.op, ast.Mod):
            result = left % right
        else:
            raise ValueError("binary_operator")
        if not _bounded_native(result):
            raise ValueError("binary_budget")
        return result
    if isinstance(node, ast.IfExp):
        condition = _safe_expression(node.test, values, depth + 1)
        selected = node.body if bool(condition) else node.orelse
        return _safe_expression(selected, values, depth + 1)
    if isinstance(node, ast.Call):
        if (
            not isinstance(node.func, ast.Name)
            or node.func.id != "len"
            or len(node.args) != 1
            or node.keywords
        ):
            raise ValueError("call_not_supported")
        target = _safe_expression(node.args[0], values, depth + 1)
        if not isinstance(target, (str, list, tuple, dict, set)):
            raise ValueError("len_type")
        return len(target)
    if isinstance(node, ast.Compare):
        left = _safe_expression(node.left, values, depth + 1)
        for operator, comparator in zip(node.ops, node.comparators):
            right = _safe_expression(comparator, values, depth + 1)
            try:
                if isinstance(operator, ast.Eq):
                    passed = type(left) is type(right) and left == right
                elif isinstance(operator, ast.NotEq):
                    passed = not (type(left) is type(right) and left == right)
                elif isinstance(operator, ast.Lt):
                    passed = left < right
                elif isinstance(operator, ast.LtE):
                    passed = left <= right
                elif isinstance(operator, ast.Gt):
                    passed = left > right
                elif isinstance(operator, ast.GtE):
                    passed = left >= right
                elif isinstance(operator, ast.In):
                    passed = left in right
                elif isinstance(operator, ast.NotIn):
                    passed = left not in right
                elif isinstance(operator, ast.Is):
                    passed = left is right
                elif isinstance(operator, ast.IsNot):
                    passed = left is not right
                else:
                    raise ValueError("comparison_operator")
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("comparison_type") from exc
            if not passed:
                return False
            left = right
        return True
    raise ValueError("expression_not_supported")


def _target_function(
    source: str, symbol_name: str
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise CoverageGuidanceError("Current source cannot be parsed.") from exc
    matches = [
        (qualified, node)
        for qualified, node in _collect_functions(tree)
        if qualified == symbol_name
    ]
    if not matches:
        raise CoverageGuidanceError("The exact function/method is not present in the current source.")
    if len(matches) != 1:
        raise CoverageGuidanceError("The selected function/method is ambiguous.")
    return matches[0][1], matches[0][0]


def rank_coverage_guided_probes(
    source: str,
    symbol_name: str,
    candidates: list[dict[str, Any]],
    coverage_branches: list[dict[str, Any]],
    *,
    limit: int = 24,
) -> tuple[list[CoverageGuidedProbeCandidate], str]:
    """Prioritize old probe candidates against explicit uncovered if/else arms."""
    if not 1 <= limit <= MAX_CANDIDATES:
        raise CoverageGuidanceError("Probe ranking limit is outside the supported bound.")
    function, qualified_name = _target_function(source, symbol_name)
    plan = build_coverage_plan(source, function)
    branch_nodes: dict[tuple[int, str], ast.If] = {}

    def walk_target(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.If) and child.orelse:
                condition = ast.get_source_segment(source, child.test) or ast.unparse(child.test)
                branch_nodes[(child.lineno, condition.strip()[:300])] = child
            walk_target(child)

    walk_target(function)
    planned = {
        (branch.line, branch.condition): branch
        for branch in plan.branches
        if branch.kind == "if" and branch.else_lines is not None
    }
    measured: dict[tuple[int, str], dict[str, Any]] = {}
    for record in coverage_branches:
        if not isinstance(record, dict):
            continue
        line = record.get("line")
        condition = record.get("condition")
        if isinstance(line, int) and not isinstance(line, bool) and isinstance(condition, str):
            measured[(line, condition)] = record

    ranked: list[CoverageGuidedProbeCandidate] = []
    seen: set[str] = set()
    for candidate in candidates[:MAX_CANDIDATES]:
        if not isinstance(candidate, dict):
            continue
        candidate_id = candidate.get("candidate_id")
        if not isinstance(candidate_id, str) or candidate_id in seen:
            continue
        if candidate.get("function_name") != qualified_name:
            continue
        line = candidate.get("source_line")
        condition = candidate.get("condition")
        parameter_values = candidate.get("parameter_values")
        if (
            not isinstance(line, int)
            or isinstance(line, bool)
            or not isinstance(condition, str)
            or not isinstance(parameter_values, dict)
            or not _bounded_native(parameter_values)
        ):
            continue
        key = (line, condition.strip()[:300])
        branch = planned.get(key)
        node = branch_nodes.get(key)
        observation = measured.get(key)
        if branch is None or node is None or observation is None:
            continue
        then_observed = observation.get("then_observed")
        else_observed = observation.get("else_observed")
        if not isinstance(then_observed, bool) or not isinstance(else_observed, bool):
            continue
        unobserved: list[str] = []
        if not then_observed:
            unobserved.append("then")
        if not else_observed:
            unobserved.append("else")
        if not unobserved:
            continue
        try:
            predicted = bool(_safe_expression(node.test, parameter_values))
        except (TypeError, ValueError, OverflowError, RecursionError):
            predicted = None
        if predicted is True:
            predicted_arm = "then"
            already_observed = then_observed
            priority = 100 if not already_observed else 0
        elif predicted is False:
            predicted_arm = "else"
            already_observed = else_observed
            priority = 100 if not already_observed else 0
        else:
            predicted_arm = "unknown"
            priority = 40
        if priority <= 0:
            continue
        if predicted_arm == "unknown":
            note = "Condition could not be statically resolved from this candidate; it targets an if with an unobserved arm."
        else:
            note = (
                f"Static condition evaluation predicts the unobserved {predicted_arm} arm "
                f"of the if at source line {line}."
            )
        ranked.append(
            CoverageGuidedProbeCandidate(
                candidate_id=candidate_id,
                source_line=line,
                parameter_values=parameter_values,
                branch_id=branch.branch_id,
                branch_kind="if",
                predicted_arm=predicted_arm,
                unobserved_arms=unobserved,
                priority=priority,
                rationale=str(candidate.get("rationale", ""))[:300],
                note=note,
            )
        )
        seen.add(candidate_id)

    ranked.sort(key=lambda item: (-item.priority, item.source_line, item.candidate_id))
    status = "ranked" if ranked else "no_uncovered_candidates"
    return ranked[:limit], status


class CoverageGuidedProbePlanner:
    """Read-only evidence planner; it appends a ranked, unverified probe record."""

    def __init__(self, ledger: EvidenceLedger, patch_manager: PatchManager) -> None:
        self.ledger = ledger
        self.patch_manager = patch_manager

    def generate(
        self,
        task_id: str,
        request: CoverageGuidedProbeRequest,
        workspace_root,
    ) -> CoverageGuidedProbeResponse:
        snapshot = self.ledger.get_snapshot(task_id)
        if snapshot is None:
            raise KeyError(task_id)
        if snapshot.status != "inspection_complete":
            raise CoverageGuidanceError("Task must complete workspace inspection first.")
        if "workspace_read" not in snapshot.contract.permissions:
            raise CoverageGuidanceError("This task has not explicitly approved workspace_read.")

        current = self.patch_manager.read_file(task_id, request.file_path, workspace_root)
        if current.sha256.lower() != request.source_sha256.lower():
            raise CoverageGuidanceError("Source SHA-256 is stale; refresh probes and coverage first.")

        by_id = {item.evidence_id: item for item in snapshot.evidence}
        probe_evidence = by_id.get(request.probe_evidence_id)
        coverage_evidence = by_id.get(request.coverage_evidence_id)
        if (
            probe_evidence is None
            or probe_evidence.kind != "probe"
            or probe_evidence.source != "ast-boundary-probes"
            or coverage_evidence is None
            or coverage_evidence.kind != "coverage"
            or coverage_evidence.source != "grounded-coverage"
        ):
            raise CoverageGuidanceError("The supplied probe/coverage evidence IDs are not eligible.")
        expected_artifact = f"workspace-file:{request.file_path}#{request.symbol_name}"
        if probe_evidence.artifact_ref != expected_artifact:
            raise CoverageGuidanceError("Probe evidence does not belong to the requested file and symbol.")
        probe_metadata = probe_evidence.metadata
        coverage_metadata = coverage_evidence.metadata
        for metadata in (probe_metadata, coverage_metadata):
            if str(metadata.get("source_sha256", "")).lower() != request.source_sha256.lower():
                raise CoverageGuidanceError("Probe/coverage evidence is bound to a different source SHA-256.")
        if (
            coverage_metadata.get("status") != "complete"
            or coverage_metadata.get("target_file") != request.file_path
            or coverage_metadata.get("symbol_name") != request.symbol_name
            or not isinstance(coverage_metadata.get("trace_sha256"), str)
            or len(coverage_metadata["trace_sha256"]) != 64
        ):
            raise CoverageGuidanceError("Coverage evidence is incomplete or targets a different symbol.")
        execution_id = coverage_metadata.get("execution_evidence_id")
        execution_evidence = by_id.get(execution_id) if isinstance(execution_id, str) else None
        if execution_evidence is None or execution_evidence.kind != "execution":
            raise CoverageGuidanceError("Coverage evidence is not linked to a known execution event.")
        candidates = probe_metadata.get("candidates")
        branches = coverage_metadata.get("branches")
        if (
            probe_metadata.get("executed") is not False
            or probe_metadata.get("expected_outputs_inferred") is not False
            or not isinstance(candidates, list)
            or not isinstance(branches, list)
        ):
            raise CoverageGuidanceError("Probe/coverage evidence shape is invalid.")
        ranked, status = rank_coverage_guided_probes(
            current.content,
            request.symbol_name,
            candidates,
            branches,
            limit=request.limit,
        )
        evidence = EvidenceRecord(
            evidence_id=uuid4().hex,
            task_id=task_id,
            kind="probe",
            source="coverage-guided-probes",
            claim=f"Ranked {len(ranked)} unverified candidate(s) against source-hash-bound uncovered if/else arms.",
            passed=None,
            artifact_ref=expected_artifact,
            metadata={
                "schema": "unai.coverage-guided-probes.v1",
                "source_sha256": request.source_sha256.lower(),
                "probe_evidence_id": probe_evidence.evidence_id,
                "coverage_evidence_id": coverage_evidence.evidence_id,
                "execution_evidence_id": execution_evidence.evidence_id,
                "candidate_count": len(ranked),
                "candidates": [item.model_dump(mode="json") for item in ranked],
                "executed": False,
                "expected_outputs_inferred": False,
            },
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.ledger.append_evidence(evidence)
        return CoverageGuidedProbeResponse(
            task_id=task_id,
            file_path=request.file_path,
            symbol_name=request.symbol_name,
            source_sha256=request.source_sha256.lower(),
            status=status,
            candidates=ranked,
            probe_evidence_id=probe_evidence.evidence_id,
            coverage_evidence_id=coverage_evidence.evidence_id,
            evidence=evidence,
            limitations=list(_LIMITATIONS),
        )

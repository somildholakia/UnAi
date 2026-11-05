"""Bounded line and branch-arm coverage for grounded oracle executions.

The generated harness records source line numbers with ``sys.settrace`` while
the explicitly supplied oracle cases run. This module turns that trace into a
deterministic, bounded report for the selected target function.

It does not generate new oracle cases, infer expected outputs, or claim that an
observed line/arm is correct. Coverage is evidence about the supplied cases.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass

from unai_engine.models import GroundedCoverageBranch, GroundedCoverageResult


MAX_COVERABLE_LINES = 5_000
MAX_BRANCHES = 200
MAX_MISSING_LINES = 500
MAX_CONDITION_CHARS = 300
MAX_TRAVERSED_NODES = 50_000

_BRANCH_KINDS = {
    ast.If: "if",
    ast.While: "while",
    ast.For: "for",
    ast.AsyncFor: "async_for",
}
_SUPPORTED_BRANCH_KINDS = tuple(_BRANCH_KINDS)


class CoveragePlanningError(ValueError):
    """The selected target cannot be represented by the bounded coverage plan."""


@dataclass(frozen=True, slots=True)
class CoverageBranch:
    branch_id: str
    kind: str
    line: int
    column: int
    condition: str
    then_lines: frozenset[int]
    else_lines: frozenset[int] | None


@dataclass(frozen=True, slots=True)
class CoveragePlan:
    coverable_lines: tuple[int, ...]
    branches: tuple[CoverageBranch, ...]
    truncated: bool


def _is_docstring(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _collect_statement_lines(
    node: ast.AST,
    lines: set[int],
    *,
    visited: list[int],
) -> None:
    """Collect executable statement line numbers, excluding nested scopes."""
    visited[0] += 1
    if visited[0] > MAX_TRAVERSED_NODES or len(lines) >= MAX_COVERABLE_LINES:
        return
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        # The declaration line executes, but the nested body is not part of the
        # selected target's own coverage surface.
        lines.add(node.lineno)
        return
    if _is_docstring(node):
        return
    if isinstance(node, ast.AnnAssign) and node.value is None:
        return
    if isinstance(node, ast.ExceptHandler):
        lines.add(node.lineno)
    elif isinstance(node, ast.stmt):
        lines.add(node.lineno)
    for child in ast.iter_child_nodes(node):
        _collect_statement_lines(child, lines, visited=visited)


def _lines_for_statements(statements: list[ast.stmt]) -> frozenset[int]:
    lines: set[int] = set()
    visited = [0]
    for statement in statements:
        _collect_statement_lines(statement, lines, visited=visited)
    return frozenset(lines)


def _condition_text(source: str, node: ast.AST) -> str:
    if isinstance(node, (ast.If, ast.While)):
        text = ast.get_source_segment(source, node.test) or ast.unparse(node.test)
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        try:
            text = f"for {ast.unparse(node.target)} in {ast.unparse(node.iter)}"
        except (ValueError, TypeError):
            text = "for <unparseable target>"
    else:
        text = ast.unparse(node)
    return text.strip()[:MAX_CONDITION_CHARS]


def build_coverage_plan(
    source: str,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> CoveragePlan:
    """Return coverable lines and supported branch arms for one target body."""
    if not isinstance(source, str) or not source.strip():
        raise CoveragePlanningError("Coverage source must be non-empty text.")
    if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise CoveragePlanningError("A target function or method is required.")

    coverable: set[int] = set()
    visited = [0]
    for statement in function.body:
        _collect_statement_lines(statement, coverable, visited=visited)

    drafts: list[tuple[int, int, str, str, frozenset[int], frozenset[int] | None]] = []
    branch_truncated = False

    def visit(node: ast.AST) -> None:
        nonlocal branch_truncated
        if branch_truncated:
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node is not function:
                return
        if isinstance(node, _SUPPORTED_BRANCH_KINDS):
            then_lines = _lines_for_statements(node.body)
            else_lines = (
                _lines_for_statements(node.orelse) if node.orelse else None
            )
            if then_lines or else_lines:
                drafts.append(
                    (
                        int(getattr(node, "lineno", 1)),
                        int(getattr(node, "col_offset", 0)),
                        _BRANCH_KINDS[type(node)],
                        _condition_text(source, node),
                        then_lines,
                        else_lines,
                    )
                )
                if len(drafts) > MAX_BRANCHES:
                    branch_truncated = True
                    return
        for child in ast.iter_child_nodes(node):
            visit(child)

    for statement in function.body:
        visit(statement)

    drafts.sort(key=lambda item: (item[0], item[1], item[2]))
    branches: list[CoverageBranch] = []
    for index, (line, column, kind, condition, then_lines, else_lines) in enumerate(
        drafts[:MAX_BRANCHES], start=1
    ):
        branches.append(
            CoverageBranch(
                branch_id=f"branch-{index:03d}",
                kind=kind,
                line=max(1, line),
                column=max(0, column),
                condition=condition,
                then_lines=then_lines,
                else_lines=else_lines,
            )
        )

    return CoveragePlan(
        coverable_lines=tuple(sorted(coverable)),
        branches=tuple(branches),
        truncated=branch_truncated or len(coverable) >= MAX_COVERABLE_LINES,
    )


def analyze_coverage(
    plan: CoveragePlan,
    executed_lines: list[int] | set[int],
    case_lines: dict[str, list[int]] | None,
    *,
    target_file: str,
    symbol_name: str,
    source_sha256: str,
    trace_sha256: str,
) -> GroundedCoverageResult:
    """Build a bounded report from an observed, already-validated line trace."""
    executed = {
        line
        for line in executed_lines
        if isinstance(line, int) and not isinstance(line, bool) and line > 0
    }
    coverable = set(plan.coverable_lines)
    observed = coverable & executed
    missing = sorted(coverable - executed)

    branches: list[GroundedCoverageBranch] = []
    supported_arms = 0
    observed_arms = 0
    for branch in plan.branches:
        then_observed = bool(branch.then_lines & executed)
        else_observed = (
            bool(branch.else_lines & executed)
            if branch.else_lines is not None
            else None
        )
        supported_arms += 1 + (1 if branch.else_lines is not None else 0)
        observed_arms += int(then_observed)
        if else_observed is not None:
            observed_arms += int(else_observed)
        branches.append(
            GroundedCoverageBranch(
                branch_id=branch.branch_id,
                kind=branch.kind,
                line=branch.line,
                condition=branch.condition,
                then_observed=then_observed,
                else_observed=else_observed,
            )
        )

    measured_cases = 0
    if isinstance(case_lines, dict):
        measured_cases = sum(
            1
            for case_id, lines in case_lines.items()
            if isinstance(case_id, str) and isinstance(lines, list)
        )

    return GroundedCoverageResult(
        status="complete",
        reason=None,
        target_file=target_file,
        symbol_name=symbol_name,
        source_sha256=source_sha256,
        trace_sha256=trace_sha256,
        coverable_line_count=len(coverable),
        executed_line_count=len(observed),
        missing_line_count=len(missing),
        missing_lines=missing[:MAX_MISSING_LINES],
        line_coverage=(
            round(len(observed) / len(coverable), 6) if coverable else None
        ),
        supported_branch_arms=supported_arms,
        observed_branch_arms=observed_arms,
        branch_coverage=(
            round(observed_arms / supported_arms, 6) if supported_arms else None
        ),
        branches=branches,
        cases_measured=measured_cases,
        truncated=plan.truncated or len(missing) > MAX_MISSING_LINES,
    )


def unavailable_coverage(
    *,
    target_file: str,
    symbol_name: str,
    source_sha256: str,
    reason: str,
) -> GroundedCoverageResult:
    """Represent a requested coverage measurement that produced no usable trace."""
    return GroundedCoverageResult(
        status="unavailable",
        reason=reason[:300],
        target_file=target_file,
        symbol_name=symbol_name,
        source_sha256=source_sha256,
        trace_sha256=None,
        coverable_line_count=0,
        executed_line_count=0,
        missing_line_count=0,
        missing_lines=[],
        line_coverage=None,
        supported_branch_arms=0,
        observed_branch_arms=0,
        branch_coverage=None,
        branches=[],
        cases_measured=0,
        truncated=False,
    )

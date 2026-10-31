"""Conservative AST-derived boundary probes; candidates are never executed here."""
from __future__ import annotations

import ast
import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from unai_engine.models import (
    EvidenceRecord,
    ProbeCandidate,
    ProbeResponse,
)
from unai_engine.verification.ledger import EvidenceLedger


_LIMITATIONS = [
    "Generated inputs are unverified probes, not confirmed counterexamples.",
    "The generator does not infer expected outputs or assert that a candidate is valid for every parameter type.",
    "Only straightforward Python branch comparisons involving function parameters are considered.",
    "Candidates are not executed; a separate approved test workflow is required.",
]


class ProbeTargetError(ValueError):
    pass


def _collect_functions(tree: ast.AST) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def visit(body: list[ast.stmt], scope: list[str]) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                visit(node.body, [*scope, node.name])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = ".".join([*scope, node.name])
                found.append((qualified, node))
                visit(node.body, [*scope, node.name])

    if isinstance(tree, ast.Module):
        visit(tree.body, [])
    return found


def _parameters(function: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    args = [
        *function.args.posonlyargs,
        *function.args.args,
        *function.args.kwonlyargs,
    ]
    result: dict[str, str] = {}
    for arg in args:
        if arg.arg in {"self", "cls"}:
            continue
        result[arg.arg] = ast.unparse(arg.annotation) if arg.annotation else ""
    return result


def _annotation_kind(annotation: str) -> str:
    value = annotation.casefold().replace(" ", "")
    if any(token in value for token in ("str", "string")):
        return "str"
    if "bool" in value:
        return "bool"
    if any(token in value for token in ("list", "sequence", "tuple", "iterable")):
        return "list"
    if any(token in value for token in ("dict", "mapping")):
        return "dict"
    if "set" in value:
        return "set"
    if any(token in value for token in ("int", "integer")):
        return "int"
    if any(token in value for token in ("float", "decimal")):
        return "float"
    return "unknown"


def _truthiness_values(annotation: str) -> list[Any]:
    kind = _annotation_kind(annotation)
    if kind == "str":
        return ["", "probe-value"]
    if kind == "int":
        return [0, 1]
    if kind == "float":
        return [0.0, 1.0]
    if kind == "bool":
        return [False, True]
    if kind == "list":
        return [[], [None]]
    if kind == "dict":
        return [{}, {"probe": True}]
    if kind == "set":
        return [[], ["probe"]]
    return [None, False, 0, "", [], {}]


def _constant_boundary(value: Any) -> tuple[list[Any], str] | None:
    if isinstance(value, bool):
        return [False, True], "boolean branch boundary"
    if isinstance(value, int):
        return [value - 1, value, value + 1], f"numeric boundary around {value}"
    if isinstance(value, float):
        return [value - 1.0, value, value + 1.0], f"numeric boundary around {value}"
    if isinstance(value, str):
        return ["", value, value + "__other__"], "string equality/ordering boundary"
    if value is None:
        return [None, "__non_none__"], "None versus non-None boundary"
    return None


def _length_values(parameter: str, annotation: str, value: int) -> tuple[list[Any], str]:
    lengths = list(dict.fromkeys(max(0, item) for item in (value - 1, value, value + 1)))
    kind = _annotation_kind(annotation)
    if kind == "str":
        return ["x" * size for size in lengths], f"string lengths around {value}"
    return [[None] * size for size in lengths], f"collection lengths around {value}"


def _condition_text(source: str, node: ast.AST) -> str:
    return (ast.get_source_segment(source, node) or ast.dump(node))[:500]


def _probe_values(
    condition: ast.AST,
    source: str,
    parameters: dict[str, str],
) -> list[tuple[str, Any, str]]:
    probes: list[tuple[str, Any, str]] = []

    for comparison in (node for node in ast.walk(condition) if isinstance(node, ast.Compare)):
        left = comparison.left
        for operator, right in zip(comparison.ops, comparison.comparators):
            if isinstance(left, ast.Name) and left.id in parameters:
                name = left.id
                if isinstance(right, ast.Constant):
                    values = _constant_boundary(right.value)
                    if values is not None:
                        candidates, reason = values
                        probes.extend((name, value, reason) for value in candidates)
                elif isinstance(operator, (ast.In, ast.NotIn)) and isinstance(right, (ast.List, ast.Tuple, ast.Set)):
                    try:
                        literals = [ast.literal_eval(item) for item in right.elts]
                    except (ValueError, TypeError):
                        literals = []
                    if literals:
                        probes.extend((name, value, "membership candidate from branch literal") for value in literals)
                        probes.append((name, "__not_member__", "value outside branch membership literals"))

            if (
                isinstance(left, ast.Call)
                and isinstance(left.func, ast.Name)
                and left.func.id == "len"
                and len(left.args) == 1
                and isinstance(left.args[0], ast.Name)
                and left.args[0].id in parameters
                and isinstance(right, ast.Constant)
                and isinstance(right.value, int)
            ):
                name = left.args[0].id
                candidates, reason = _length_values(name, parameters[name], right.value)
                probes.extend((name, value, reason) for value in candidates)

            if isinstance(right, ast.Name) and right.id in parameters and isinstance(left, ast.Constant):
                values = _constant_boundary(left.value)
                if values is not None:
                    candidates, reason = values
                    probes.extend((right.id, value, reason) for value in candidates)

            left = right

    named_parameters = {
        node.id for node in ast.walk(condition)
        if isinstance(node, ast.Name) and node.id in parameters
    }
    if not probes:
        for name in sorted(named_parameters):
            probes.extend(
                (name, value, "truthiness boundary: falsy and truthy representatives")
                for value in _truthiness_values(parameters[name])
            )

    return probes


class BoundaryProbeGenerator:
    name = "ast-boundary-probes"

    def __init__(self, ledger: EvidenceLedger) -> None:
        self.ledger = ledger

    def generate(
        self,
        task_id: str,
        file_path: str,
        symbol_name: str,
        source: str,
        source_sha256: str,
        limit: int = 24,
    ) -> ProbeResponse:
        if not file_path.casefold().endswith(".py"):
            raise ProbeTargetError("AST boundary probes currently support Python files only.")
        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError as exc:
            evidence = self._evidence(
                task_id,
                file_path,
                symbol_name,
                source_sha256,
                "Python source could not be parsed for probe generation.",
                False,
                {"error": str(exc)[:500], "line": exc.lineno},
            )
            self.ledger.append_evidence(evidence)
            return ProbeResponse(
                task_id=task_id,
                file_path=file_path,
                symbol_name=symbol_name,
                source_sha256=source_sha256,
                status="parse_error",
                candidates=[],
                evidence=evidence,
                limitations=list(_LIMITATIONS),
            )

        matches = _collect_functions(tree)
        if "." in symbol_name:
            exact = [(name, node) for name, node in matches if name == symbol_name]
        else:
            exact = [(name, node) for name, node in matches if name.rsplit(".", 1)[-1] == symbol_name]
        if len(exact) != 1:
            if not exact:
                raise ProbeTargetError(f"Function or method not found: {symbol_name}")
            raise ProbeTargetError(f"Function name is ambiguous; use a qualified name: {symbol_name}")

        qualified_name, function = exact[0]
        parameters = _parameters(function)
        candidates: list[ProbeCandidate] = []
        seen: set[str] = set()
        max_candidates = max(1, min(limit, 60))

        for node in ast.walk(function):
            condition = None
            if isinstance(node, (ast.If, ast.While, ast.Assert)):
                condition = node.test
            if condition is None:
                continue

            condition_source = _condition_text(source, condition)
            for parameter, value, reason in _probe_values(condition, source, parameters):
                line = int(getattr(node, "lineno", 1))
                key = json.dumps([line, parameter, value], sort_keys=True, ensure_ascii=False, default=str)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    ProbeCandidate(
                        candidate_id=f"probe-{len(candidates) + 1}",
                        function_name=qualified_name,
                        source_line=line,
                        condition=condition_source,
                        parameter_values={parameter: value},
                        rationale=reason,
                    )
                )
                if len(candidates) >= max_candidates:
                    break
            if len(candidates) >= max_candidates:
                break

        status = "generated" if candidates else "no_supported_conditions"
        evidence = self._evidence(
            task_id,
            file_path,
            qualified_name,
            source_sha256,
            f"Generated {len(candidates)} unverified boundary probe candidates.",
            None,
            {
                "candidate_count": len(candidates),
                "candidates": [item.model_dump(mode="json") for item in candidates],
                "executed": False,
                "expected_outputs_inferred": False,
            },
        )
        self.ledger.append_evidence(evidence)
        return ProbeResponse(
            task_id=task_id,
            file_path=file_path,
            symbol_name=qualified_name,
            source_sha256=source_sha256,
            status=status,
            candidates=candidates,
            evidence=evidence,
            limitations=list(_LIMITATIONS),
        )

    @staticmethod
    def _evidence(
        task_id: str,
        file_path: str,
        symbol_name: str,
        source_sha256: str,
        claim: str,
        passed: bool | None,
        metadata: dict[str, Any],
    ) -> EvidenceRecord:
        return EvidenceRecord(
            evidence_id=uuid4().hex,
            task_id=task_id,
            kind="probe",
            source="ast-boundary-probes",
            claim=claim,
            passed=passed,
            artifact_ref=f"workspace-file:{file_path}#{symbol_name}",
            metadata={"source_sha256": source_sha256, **metadata},
            created_at=datetime.now(timezone.utc).isoformat(),
        )

"""Read-only task inspection stage for M3.

This stage compiles a task, checks an explicit workspace-read approval, indexes a
bounded Python code graph, records discovery and parse evidence, and persists an
immutable evidence snapshot. It does not edit files or execute commands.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import Any

from unai_engine.models import (
    EvidenceRecord,
    SymbolReference,
    TaskCompileRequest,
    TaskInspectRequest,
    TaskInspectionResponse,
)
from unai_engine.cognition.task_compiler import compile_task
from unai_engine.runtime.workspace import WorkspaceError, resolve_workspace_path
from unai_engine.verification.ledger import EvidenceLedger


_STOP_WORDS = {
    "the", "and", "for", "from", "with", "into", "this", "that", "please",
    "fix", "bug", "build", "create", "implement", "add", "make", "change",
    "modify", "feature", "review", "code", "using", "use", "test", "tests",
    "application", "app", "project", "system", "working", "existing",
}


def _search_terms(request: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", request.casefold()):
        term = raw.strip("_")
        if len(term) < 3 or term in _STOP_WORDS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= 8:
            break
    return terms


def _rank_symbols(graph: dict[str, Any], request: str, limit: int) -> list[SymbolReference]:
    terms = _search_terms(request)
    if not terms:
        return []

    ranked: list[tuple[int, str, SymbolReference]] = []
    for node in graph.get("nodes", []):
        if node.get("type") not in {"class", "function", "method"}:
            continue
        haystack = " ".join(
            str(node.get(key, ""))
            for key in ("name", "qualified_name", "path")
        ).casefold()
        matched = [term for term in terms if term in haystack]
        if not matched:
            continue
        reference = SymbolReference(
            path=str(node["path"]),
            name=str(node.get("qualified_name", node.get("name", ""))),
            kind=node["type"],
            start_line=int(node["start_line"]),
            end_line=int(node["end_line"]),
            score=len(matched),
            matched_terms=matched,
        )
        ranked.append((len(matched), reference.path, reference))

    ranked.sort(key=lambda item: (-item[0], item[1], item[2].start_line))
    return [item[2] for item in ranked[: max(1, min(limit, 100))]]


class TaskInspector:
    def __init__(self, indexer: Any, ledger: EvidenceLedger) -> None:
        self.indexer = indexer
        self.ledger = ledger

    async def inspect(
        self,
        request: TaskInspectRequest,
        workspace_root: Path,
    ) -> TaskInspectionResponse:
        contract = compile_task(
            TaskCompileRequest(
                request=request.request,
                constraints=request.constraints,
                permissions=request.permissions,
            )
        )
        evidence: list[EvidenceRecord] = []
        matches: list[SymbolReference] = []
        summary: dict[str, Any] = {}
        now = datetime.now(timezone.utc).isoformat()

        def add(
            kind: str,
            source: str,
            claim: str,
            passed: bool | None,
            artifact_ref: str | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> None:
            evidence.append(
                EvidenceRecord(
                    evidence_id=uuid4().hex,
                    task_id=contract.task_id,
                    kind=kind,
                    source=source,
                    claim=claim,
                    passed=passed,
                    artifact_ref=artifact_ref,
                    metadata=metadata or {},
                    created_at=now,
                )
            )

        if "workspace_read" not in contract.permissions:
            add(
                "precondition",
                "permission-gate",
                "Workspace read permission was explicitly approved",
                False,
                metadata={"required_permission": "workspace_read"},
            )
            status = "awaiting_workspace_read"
            next_gate = "workspace_access_required"
        else:
            try:
                root = resolve_workspace_path(workspace_root, request.workspace)
            except WorkspaceError:
                root = None

            if root is None:
                add(
                    "precondition",
                    "workspace-boundary",
                    "Selected workspace is contained by the configured workspace root",
                    False,
                    metadata={"workspace": request.workspace},
                )
                status = "workspace_unavailable"
                next_gate = "workspace_correction_required"
            elif not root.exists() or not root.is_dir():
                add(
                    "precondition",
                    "workspace-boundary",
                    "Selected workspace exists and is a directory",
                    False,
                    metadata={"workspace": request.workspace},
                )
                status = "workspace_unavailable"
                next_gate = "workspace_correction_required"
            else:
                add(
                    "precondition",
                    "workspace-boundary",
                    "Selected workspace is contained by the configured workspace root and is a directory",
                    True,
                    artifact_ref=request.workspace,
                )
                try:
                    graph = await self.indexer.index(str(root))
                    summary = dict(graph.get("summary", {}))
                    matches = _rank_symbols(graph, request.request, request.symbol_limit)
                    add(
                        "repository",
                        "python-code-graph",
                        "Bounded Python repository graph was generated",
                        True,
                        artifact_ref=f"workspace:{request.workspace}",
                        metadata=summary,
                    )
                    parse_errors = int(summary.get("parse_errors", 0))
                    add(
                        "quality",
                        "python-ast-parser",
                        f"Python files with parse errors: {parse_errors}",
                        parse_errors == 0,
                        metadata={
                            "files_discovered": summary.get("files_discovered", 0),
                            "files_parsed": summary.get("files_parsed", 0),
                            "errors": graph.get("errors", [])[:10],
                        },
                    )
                    add(
                        "discovery",
                        "symbol-retrieval",
                        f"Matched {len(matches)} candidate symbols from task wording",
                        None,
                        metadata={
                            "terms": _search_terms(request.request),
                            "matches": [item.model_dump(mode="json") for item in matches],
                            "note": "Discovery evidence is not proof that a symbol is the cause or correct edit location.",
                        },
                    )
                    status = "inspection_complete"
                    next_gate = (
                        "approval_required"
                        if contract.missing_permissions
                        else "execution_not_implemented"
                    )
                except (OSError, ValueError):
                    add(
                        "repository",
                        "python-code-graph",
                        "Repository graph could be generated",
                        False,
                        metadata={"error": "Indexing failed; detailed exception suppressed."},
                    )
                    status = "workspace_unavailable"
                    next_gate = "workspace_correction_required"

        self.ledger.record_inspection(contract, status, evidence, workspace=request.workspace)
        return TaskInspectionResponse(
            task_id=contract.task_id,
            status=status,
            next_gate=next_gate,
            contract=contract,
            workspace=request.workspace,
            graph_summary=summary,
            symbol_matches=matches,
            evidence=evidence,
            execution_started=False,
        )

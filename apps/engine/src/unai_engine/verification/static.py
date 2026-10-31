"""Cheap local verifiers. These never execute source code by themselves."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..plugins.contracts import Evidence, Proposal, TaskContext


class StaticEvidenceVerifier:
    name = "static-evidence"

    async def verify(self, context: TaskContext, proposal: Proposal) -> list[Evidence]:
        evidence: list[Evidence] = []
        paths = proposal.payload.get("changed_paths", [])
        if not isinstance(paths, list):
            return [Evidence(source=self.name, claim="changed_paths payload is a list", passed=False)]
        root = Path(context.workspace).resolve() if context.workspace else None
        for raw in paths:
            if not isinstance(raw, str) or root is None:
                evidence.append(Evidence(source=self.name, claim=f"workspace path available for {raw!r}", passed=False))
                continue
            candidate = (root / raw).resolve()
            try:
                candidate.relative_to(root)
                exists = candidate.exists()
            except ValueError:
                exists = False
            evidence.append(Evidence(source=self.name, claim=f"Changed path is inside workspace: {raw}", passed=exists))
        if not evidence:
            evidence.append(Evidence(source=self.name, claim="Proposal declares no changed paths; no path check applicable", passed=None))
        return evidence

"""A deterministic, zero-weight microcore used as a bootstrap and fallback.

This is not presented as a replacement for a language model. It demonstrates that
problem classification, budget decisions, and action sequencing can be handled without
allocating neural inference memory. Neural and non-neural cognition plugins share the
same contract and can be combined in later experiments.
"""
from __future__ import annotations

from ..plugins.contracts import Proposal, TaskContext


class RuleMicrocore:
    name = "rule-microcore"

    async def propose(self, context: TaskContext) -> list[Proposal]:
        objective = context.objective.lower()
        actions: list[str] = []
        if any(word in objective for word in ("bug", "error", "fails", "broken", "exception")):
            actions = ["inspect", "reproduce", "hypothesize", "test", "patch", "regression-test"]
        elif any(word in objective for word in ("build", "create", "implement", "develop")):
            actions = ["clarify-contract", "map-dependencies", "design", "implement-smallest-slice", "test", "review"]
        elif any(word in objective for word in ("review", "audit", "security")):
            actions = ["map-surface", "enumerate-threats", "inspect-evidence", "test-abuse-cases", "report"]
        else:
            actions = ["clarify-contract", "gather-evidence", "draft-options", "verify", "report"]
        return [Proposal(
            kind="action-sequence",
            payload={"actions": actions, "max_parallel_candidates": min(3, max(1, int(context.budget.get("candidate_width", 2))))},
            rationale="Deterministic fallback plan; each stage must produce evidence before proceeding.",
            assumptions=["Task classification is heuristic and must be checked against repository evidence."],
            risk_score=0.15,
        )]

"""Bounded proposal portfolio primitives (planning only; execution is separate)."""
from __future__ import annotations

from ..plugins.contracts import Proposal, TaskContext


class BoundedPortfolio:
    name = "bounded-portfolio"

    async def explore(self, context: TaskContext, proposals: list[Proposal], width: int = 3) -> list[Proposal]:
        cap = max(1, min(width, 5, int(context.budget.get("candidate_width", width))))
        # Stable deduplication by canonical payload: no hidden background execution.
        selected: list[Proposal] = []
        seen: set[str] = set()
        for proposal in sorted(proposals, key=lambda item: item.risk_score):
            key = repr(sorted(proposal.payload.items()))
            if key in seen:
                continue
            seen.add(key)
            selected.append(proposal)
            if len(selected) >= cap:
                break
        return selected

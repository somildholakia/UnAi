from pathlib import Path

import pytest

from unai_engine.cognition.rule_microcore import RuleMicrocore
from unai_engine.indexing.filesystem import FilesystemIndex
from unai_engine.plugins.builtin import build_registry
from unai_engine.plugins.contracts import Proposal, TaskContext
from unai_engine.search.portfolio import BoundedPortfolio


@pytest.mark.asyncio
async def test_registry_exposes_modular_categories() -> None:
    manifest = build_registry().manifest()
    assert manifest["cognition"] == ["rule-microcore"]
    assert manifest["memory"] == ["sqlite-trajectory"]
    assert manifest["indexing"] == ["filesystem-index"]
    assert manifest["verification"] == ["static-evidence"]
    assert manifest["search"] == ["bounded-portfolio"]


@pytest.mark.asyncio
async def test_rule_microcore_classifies_debugging_task() -> None:
    context = TaskContext(task_id="t", objective="Debug a failing test", budget={"candidate_width": 2})
    proposals = await RuleMicrocore().propose(context)
    assert proposals[0].payload["actions"][:2] == ["inspect", "reproduce"]
    assert proposals[0].payload["max_parallel_candidates"] == 2


@pytest.mark.asyncio
async def test_bounded_portfolio_deduplicates_and_caps_candidates() -> None:
    context = TaskContext(task_id="t", objective="Explore", budget={"candidate_width": 2})
    proposals = [
        Proposal(kind="plan", payload={"n": 1}, rationale="one"),
        Proposal(kind="plan", payload={"n": 1}, rationale="duplicate"),
        Proposal(kind="plan", payload={"n": 2}, rationale="two"),
        Proposal(kind="plan", payload={"n": 3}, rationale="three"),
    ]
    selected = await BoundedPortfolio().explore(context, proposals, width=4)
    assert [proposal.payload["n"] for proposal in selected] == [1, 2]


@pytest.mark.asyncio
async def test_filesystem_index_excludes_hidden_and_large_files(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def useful():\n    return True\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "secret.py").write_text("ignored", encoding="utf-8")
    summary = await FilesystemIndex().index(str(tmp_path))
    assert summary["files"] == 1
    hits = await FilesystemIndex().query(str(tmp_path), "return True")
    assert hits[0]["path"] == "src/main.py"

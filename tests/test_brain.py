from pathlib import Path

import pytest

from unai_engine.brain.vault import BrainError, MarkdownVault


def test_markdown_vault_indexes_obsidian_links_tags_and_backlinks(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "Project.md").write_text(
        "---\ntitle: Project Atlas\ntags: [project, research]\n---\n\n"
        "Build the graph brain. #active\nSee [[Memory Design|the memory note]].\n",
        encoding="utf-8",
    )
    (root / "Memory Design.md").write_text(
        "---\ntitle: Memory Design\n---\n\nSQLite and backlinks.", encoding="utf-8"
    )
    vault = MarkdownVault(root, tmp_path / "index.sqlite3")
    summary = vault.index()
    assert summary["notes"] == 2
    assert summary["links"] == 1
    results = vault.search("graph brain")
    assert results and results[0]["path"] == "Project.md"
    project = vault.read("Project.md")
    assert project["title"] == "Project Atlas"
    assert "active" in project["tags"]
    memory = vault.read("Memory Design.md")
    assert memory["backlinks"] == ["Project.md"]
    assert project["outgoing"][0]["target_path"] == "Memory Design.md"


def test_create_note_writes_frontmatter_and_reindexes(tmp_path: Path) -> None:
    vault = MarkdownVault(tmp_path / "vault", tmp_path / "index.sqlite3")
    note = vault.create("Decision Log", "We will use a local-first graph.", ["decision", "architecture"])
    assert note["path"] == "decision-log.md"
    assert note["tags"] == ["architecture", "decision"]
    assert "local-first graph" in note["body"]
    assert vault.status()["notes"] == 1


def test_vault_rejects_path_traversal_and_non_markdown(tmp_path: Path) -> None:
    vault = MarkdownVault(tmp_path / "vault", tmp_path / "index.sqlite3")
    with pytest.raises(BrainError, match="escapes"):
        vault.read("../../etc/passwd")
    with pytest.raises(BrainError, match=r"\.md"):
        vault.read("notes.txt")


def test_index_ignores_hidden_and_large_markdown(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "visible.md").write_text("hello memory", encoding="utf-8")
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "settings.md").write_text("private UI state", encoding="utf-8")
    (root / "huge.md").write_bytes(b"a" * 1_000_001)
    vault = MarkdownVault(root, tmp_path / "index.sqlite3")
    assert vault.index()["notes"] == 1


def test_wikilink_parser_handles_alias_and_heading(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "Index.md").write_text("See [[Concept#details|the concept]].", encoding="utf-8")
    (root / "Concept.md").write_text("# Concept\n\nBody", encoding="utf-8")
    vault = MarkdownVault(root, tmp_path / "index.sqlite3")
    vault.index()
    link = vault.read("Index.md")["outgoing"][0]
    assert link["target_path"] == "Concept.md"
    assert link["alias"] == "the concept"
    assert link["anchor"] == "details"


def test_wikilink_resolves_frontmatter_aliases(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "Start.md").write_text("See [[OLEI]] and [[Outcome Lattice]].", encoding="utf-8")
    (root / "Research.md").write_text(
        "---\ntitle: Outcome-Lattice Execution Intelligence\naliases: [OLEI, Outcome Lattice]\n---\n\nArchitecture.",
        encoding="utf-8",
    )
    vault = MarkdownVault(root, tmp_path / "index.sqlite3")
    vault.index()
    links = vault.read("Start.md")["outgoing"]
    assert [link["target_path"] for link in links] == ["Research.md", "Research.md"]

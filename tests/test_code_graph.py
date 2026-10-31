from pathlib import Path

import pytest

from unai_engine.indexing.code_graph import PythonCodeGraphIndexer


@pytest.mark.asyncio
async def test_graph_indexes_symbols_relative_imports_and_test_links(tmp_path: Path) -> None:
    package = tmp_path / "src" / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "helpers.py").write_text("def clean(value):\n    return value.strip()\n", encoding="utf-8")
    (package / "core.py").write_text(
        "from .helpers import clean\n\n"
        "class Client:\n"
        "    async def send(self, value):\n"
        "        return clean(value)\n\n"
        "def normalize(value):\n"
        "    return clean(value)\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_core.py").write_text(
        "from pkg.core import normalize\n\ndef test_normalize():\n    assert normalize(' x ') == 'x'\n",
        encoding="utf-8",
    )

    graph = await PythonCodeGraphIndexer().index(str(tmp_path))
    nodes = graph["nodes"]
    edges = graph["edges"]

    client = next(node for node in nodes if node.get("qualified_name") == "Client")
    send = next(node for node in nodes if node.get("qualified_name") == "Client.send")
    assert client["type"] == "class"
    assert send["type"] == "method"
    assert send["start_line"] == 4
    assert any(
        edge["type"] == "imports"
        and edge["source"] == "file:src/pkg/core.py"
        and edge["target"] == "file:src/pkg/helpers.py"
        for edge in edges
    )
    assert any(
        edge["type"] == "tests"
        and edge["source"] == "file:tests/test_core.py"
        and edge["target"] == "file:src/pkg/core.py"
        for edge in edges
    )
    assert graph["summary"]["parse_errors"] == 0


@pytest.mark.asyncio
async def test_graph_reports_syntax_errors_without_aborting(tmp_path: Path) -> None:
    (tmp_path / "good.py").write_text("def ok():\n    return True\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("def broken(:\n", encoding="utf-8")

    graph = await PythonCodeGraphIndexer().index(str(tmp_path))
    assert graph["summary"]["files_discovered"] == 2
    assert graph["summary"]["files_parsed"] == 1
    assert graph["summary"]["parse_errors"] == 1
    assert graph["errors"][0]["path"] == "bad.py"


@pytest.mark.asyncio
async def test_graph_query_returns_symbol_ranges(tmp_path: Path) -> None:
    (tmp_path / "logic.py").write_text(
        "def calculate_total(items):\n    return sum(items)\n",
        encoding="utf-8",
    )
    matches = await PythonCodeGraphIndexer().query(str(tmp_path), "calculate total")
    assert matches == [
        {
            "path": "logic.py",
            "name": "calculate_total",
            "kind": "function",
            "start_line": 1,
            "end_line": 2,
        }
    ]

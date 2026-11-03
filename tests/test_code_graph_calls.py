from pathlib import Path

import pytest

from unai_engine.indexing.code_graph import PythonCodeGraphIndexer


@pytest.mark.asyncio
async def test_graph_resolves_only_conservative_local_and_imported_call_edges(tmp_path: Path) -> None:
    workers = tmp_path / "workers.py"
    workers.write_text(
        "def normalize(value):\n"
        "    return value.strip()\n\n"
        "class Worker:\n"
        "    def validate(self, value):\n"
        "        return normalize(value)\n\n"
        "    def run(self, value):\n"
        "        return self.validate(value)\n",
        encoding="utf-8",
    )
    api = tmp_path / "api.py"
    api.write_text(
        "from workers import normalize as clean, Worker as Runner\n"
        "import workers as worker_module\n\n"
        "def dispatch(value):\n"
        "    cleaned = clean(value)\n"
        "    worker_module.normalize(cleaned)\n"
        "    runner = Runner()\n"
        "    runner.run(cleaned)\n"
        "    return Runner.validate(runner, cleaned)\n",
        encoding="utf-8",
    )

    graph = await PythonCodeGraphIndexer().index(str(tmp_path))
    nodes = {item["id"]: item for item in graph["nodes"] if item["type"] != "file"}
    call_edges = [edge for edge in graph["edges"] if edge["type"] == "calls"]
    resolved = {
        (
            nodes[edge["source"]]["path"],
            nodes[edge["source"]]["qualified_name"],
            nodes[edge["target"]]["path"],
            nodes[edge["target"]]["qualified_name"],
        )
        for edge in call_edges
    }

    assert (
        "workers.py", "Worker.validate", "workers.py", "normalize"
    ) in resolved
    assert (
        "workers.py", "Worker.run", "workers.py", "Worker.validate"
    ) in resolved
    assert (
        "api.py", "dispatch", "workers.py", "normalize"
    ) in resolved
    assert (
        "api.py", "dispatch", "workers.py", "Worker.validate"
    ) in resolved
    assert graph["summary"]["call_edges"] == 4
    assert graph["summary"]["instantiation_edges"] == 1
    assert graph["summary"]["call_sites_indexed"] == 7
    assert graph["summary"]["call_sites_unresolved"] == 2
    assert any(
        edge["type"] == "instantiates"
        and nodes[edge["source"]]["qualified_name"] == "dispatch"
        and nodes[edge["target"]]["qualified_name"] == "Worker"
        for edge in graph["edges"]
    )


@pytest.mark.asyncio
async def test_graph_does_not_guess_ambiguous_or_dynamic_targets(tmp_path: Path) -> None:
    (tmp_path / "ambiguous.py").write_text(
        "def helper(value):\n"
        "    return value\n\n"
        "def helper(value):\n"
        "    return value + 1\n\n"
        "def entry(value):\n"
        "    helper(value)\n"
        "    value.unknown(value)\n"
        "    return make(value).dynamic()\n",
        encoding="utf-8",
    )

    graph = await PythonCodeGraphIndexer().index(str(tmp_path))
    calls = [edge for edge in graph["edges"] if edge["type"] in {"calls", "instantiates"}]

    assert calls == []
    assert graph["summary"]["call_edges"] == 0
    assert graph["summary"]["call_sites_unresolved"] >= 2


@pytest.mark.asyncio
async def test_graph_preserves_recursive_call_edges(tmp_path: Path) -> None:
    (tmp_path / "recursive.py").write_text(
        "def countdown(value):\n"
        "    if value <= 0:\n"
        "        return 0\n"
        "    return countdown(value - 1)\n",
        encoding="utf-8",
    )

    graph = await PythonCodeGraphIndexer().index(str(tmp_path))
    node = next(item for item in graph["nodes"] if item.get("qualified_name") == "countdown")
    calls = [edge for edge in graph["edges"] if edge["type"] == "calls"]

    assert calls == [{"source": node["id"], "target": node["id"], "type": "calls"}]


@pytest.mark.asyncio
async def test_call_site_budget_is_reported_as_truncated(tmp_path: Path) -> None:
    calls = "\n".join("    target()" for _ in range(110))
    (tmp_path / "many_calls.py").write_text(
        "def target():\n    return None\n\n"
        "def caller():\n" + calls + "\n",
        encoding="utf-8",
    )

    graph = await PythonCodeGraphIndexer().index(str(tmp_path))

    assert graph["summary"]["call_sites_indexed"] == 100
    assert graph["summary"]["call_sites_truncated"] is True
    assert graph["summary"]["truncated"] is True

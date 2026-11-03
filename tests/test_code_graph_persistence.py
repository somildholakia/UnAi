import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from unai_engine.indexing.code_graph import PythonCodeGraphIndexer
from unai_engine.indexing.graph_store import GraphStoreError


def _root_key(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_sqlite_graph_cache_reuses_unchanged_parse_results(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        "def calculate(value):\n    return value + 1\n", encoding="utf-8"
    )
    (tmp_path / "test_service.py").write_text(
        "from service import calculate\n", encoding="utf-8"
    )
    indexer = PythonCodeGraphIndexer(cache_path=tmp_path / "graph.sqlite3")

    first = await indexer.index(str(tmp_path))
    second = await indexer.index(str(tmp_path))

    assert first["summary"]["cache"]["state"] == "miss"
    assert first["summary"]["cache"]["generation"] == 1
    assert first["summary"]["cache"]["files_reparsed"] == 2
    assert second["summary"]["cache"]["state"] == "fresh"
    assert second["summary"]["cache"]["generation"] == 1
    assert second["summary"]["cache"]["files_reparsed"] == 0
    assert second["summary"]["cache"]["files_reused"] == 2
    assert second["nodes"] == first["nodes"]
    assert second["edges"] == first["edges"]
    assert any(edge["type"] == "imports" for edge in second["edges"])
    assert second["summary"]["strategy"] == "bounded-python-ast-sqlite-incremental-no-source-body-cache"


@pytest.mark.asyncio
async def test_incremental_refresh_reparses_only_changed_and_new_files(tmp_path: Path) -> None:
    alpha = tmp_path / "alpha.py"
    beta = tmp_path / "beta.py"
    stable = tmp_path / "stable.py"
    alpha.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    beta.write_text("def beta():\n    return 2\n", encoding="utf-8")
    stable.write_text("def stable():\n    return 3\n", encoding="utf-8")
    indexer = PythonCodeGraphIndexer(cache_path=tmp_path / "graph.sqlite3")

    first = await indexer.index(str(tmp_path))
    alpha.write_text("def alpha():\n    return 10\n", encoding="utf-8")
    beta.unlink()
    (tmp_path / "gamma.py").write_text("def gamma():\n    return 4\n", encoding="utf-8")

    refreshed = await indexer.index(str(tmp_path))
    paths = {node["path"] for node in refreshed["nodes"] if node["type"] == "file"}

    assert refreshed["summary"]["cache"]["state"] == "stale"
    assert refreshed["summary"]["cache"]["generation"] == 2
    assert refreshed["summary"]["cache"]["files_reparsed"] == 2
    assert refreshed["summary"]["cache"]["files_reused"] == 1
    assert refreshed["summary"]["cache"]["files_removed"] == 1
    assert refreshed["summary"]["source_fingerprint"] != first["summary"]["source_fingerprint"]
    assert paths == {"alpha.py", "stable.py", "gamma.py"}
    assert any(node.get("qualified_name") == "gamma" for node in refreshed["nodes"])


@pytest.mark.asyncio
async def test_source_hash_detects_same_size_edit_with_restored_mtime(tmp_path: Path) -> None:
    source = tmp_path / "service.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    indexer = PythonCodeGraphIndexer(cache_path=tmp_path / "graph.sqlite3")
    first = await indexer.index(str(tmp_path))
    before = source.stat()

    source.write_text("def value():\n    return 2\n", encoding="utf-8")
    assert source.stat().st_size == before.st_size
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))

    refreshed = await indexer.index(str(tmp_path))
    assert refreshed["summary"]["cache"]["state"] == "stale"
    assert refreshed["summary"]["cache"]["files_reparsed"] == 1
    assert refreshed["summary"]["source_fingerprint"] != first["summary"]["source_fingerprint"]


@pytest.mark.asyncio
async def test_failed_atomic_refresh_preserves_previous_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "service.py"
    source.write_text("def before_refresh():\n    return 1\n", encoding="utf-8")
    database = tmp_path / "graph.sqlite3"
    indexer = PythonCodeGraphIndexer(cache_path=database)
    first = await indexer.index(str(tmp_path))
    original_fingerprint = first["summary"]["source_fingerprint"]

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            CREATE TRIGGER reject_graph_edges
            BEFORE INSERT ON code_graph_edges
            BEGIN SELECT RAISE(ABORT, 'injected refresh failure'); END
            """
        )
        connection.commit()
    finally:
        connection.close()

    source.write_text("def after_refresh():\n    return 2\n", encoding="utf-8")
    with pytest.raises(sqlite3.IntegrityError, match="injected refresh failure"):
        await indexer.index(str(tmp_path))

    meta, files, edges = indexer.store.load(_root_key(tmp_path), str(tmp_path.resolve()))
    assert meta is not None
    assert meta["source_fingerprint"] == original_fingerprint
    assert meta["generation"] == 1
    assert any(node.get("qualified_name") == "before_refresh" for node in files["service.py"]["nodes"])
    assert not any(node.get("qualified_name") == "after_refresh" for node in files["service.py"]["nodes"])


@pytest.mark.asyncio
async def test_cache_refuses_unknown_future_schema_version(tmp_path: Path) -> None:
    database = tmp_path / "graph.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA user_version = 99")
        connection.commit()
    finally:
        connection.close()

    indexer = PythonCodeGraphIndexer(cache_path=database)
    (tmp_path / "service.py").write_text("def ok():\n    return True\n", encoding="utf-8")
    with pytest.raises(GraphStoreError, match="newer than supported"):
        await indexer.index(str(tmp_path))


@pytest.mark.asyncio
async def test_v1_cache_migrates_and_reparses_to_add_static_call_records(tmp_path: Path) -> None:
    source = tmp_path / "service.py"
    source.write_text(
        "def helper():\n    return 1\n\n"
        "def entry():\n    return helper()\n",
        encoding="utf-8",
    )
    database = tmp_path / "graph.sqlite3"
    indexer = PythonCodeGraphIndexer(cache_path=database)
    first = await indexer.index(str(tmp_path))
    assert first["summary"]["call_edges"] == 1

    connection = sqlite3.connect(database)
    try:
        connection.execute("ALTER TABLE code_graph_files DROP COLUMN call_sites_json")
        connection.execute("ALTER TABLE code_graph_files DROP COLUMN call_sites_truncated")
        connection.execute("ALTER TABLE code_graph_files DROP COLUMN import_bindings_json")
        connection.execute("UPDATE code_graph_repositories SET schema_version = 1")
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
    finally:
        connection.close()

    migrated = await indexer.index(str(tmp_path))

    assert migrated["summary"]["cache"]["state"] == "stale"
    assert migrated["summary"]["cache"]["generation"] == 2
    assert migrated["summary"]["cache"]["files_reparsed"] == 1
    assert migrated["summary"]["call_edges"] == 1
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        connection.close()

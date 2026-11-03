"""Transactional SQLite persistence for incremental repository code graphs.

The database stores graph metadata and parsed graph records only; source bodies are
never cached. A workspace refresh is committed as one transaction so readers see
either the previous complete graph or the new complete graph.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


_SCHEMA_VERSION = 2


class GraphStoreError(RuntimeError):
    """The graph cache cannot be read or refreshed safely."""


class GraphStoreConflict(GraphStoreError):
    """Another indexer refreshed this workspace while this refresh was building."""


class SQLiteCodeGraphStore:
    schema_version = _SCHEMA_VERSION

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        self._ensure_schema(connection)
        return connection

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > _SCHEMA_VERSION:
            raise GraphStoreError(
                f"Code graph database schema {version} is newer than supported schema {_SCHEMA_VERSION}."
            )
        if version == _SCHEMA_VERSION:
            return

        try:
            connection.execute("BEGIN IMMEDIATE")
            if version == 0:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS code_graph_repositories (
                        root_key TEXT PRIMARY KEY,
                        root_path TEXT NOT NULL,
                        source_fingerprint TEXT NOT NULL,
                        generation INTEGER NOT NULL CHECK(generation > 0),
                        refreshed_at TEXT NOT NULL,
                        schema_version INTEGER NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS code_graph_files (
                        root_key TEXT NOT NULL REFERENCES code_graph_repositories(root_key)
                            ON DELETE CASCADE,
                        path TEXT NOT NULL,
                        source_sha256 TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
                        mtime_ns INTEGER NOT NULL,
                        parse_status TEXT NOT NULL CHECK(
                            parse_status IN ('parsed', 'syntax_error', 'decode_error')
                        ),
                        nodes_json TEXT NOT NULL,
                        structural_edges_json TEXT NOT NULL,
                        import_groups_json TEXT NOT NULL,
                        call_sites_json TEXT NOT NULL DEFAULT '[]',
                        call_sites_truncated INTEGER NOT NULL DEFAULT 0,
                        import_bindings_json TEXT NOT NULL DEFAULT '[]',
                        error_json TEXT,
                        PRIMARY KEY(root_key, path)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS code_graph_edges (
                        root_key TEXT NOT NULL REFERENCES code_graph_repositories(root_key)
                            ON DELETE CASCADE,
                        source_id TEXT NOT NULL,
                        target_id TEXT NOT NULL,
                        edge_type TEXT NOT NULL,
                        PRIMARY KEY(root_key, source_id, target_id, edge_type)
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_code_graph_files_root ON code_graph_files(root_key, path)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_code_graph_edges_root ON code_graph_edges(root_key, edge_type)"
                )
            elif version == 1:
                # Existing file graph rows are retained. The indexer sees the
                # repository's version=1 marker and reparses its files once to
                # populate these newly introduced static-call fields.
                connection.execute(
                    "ALTER TABLE code_graph_files ADD COLUMN call_sites_json TEXT NOT NULL DEFAULT '[]'"
                )
                connection.execute(
                    "ALTER TABLE code_graph_files ADD COLUMN call_sites_truncated INTEGER NOT NULL DEFAULT 0"
                )
                connection.execute(
                    "ALTER TABLE code_graph_files ADD COLUMN import_bindings_json TEXT NOT NULL DEFAULT '[]'"
                )
            connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def load(
        self, root_key: str, root_path: str
    ) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]], list[dict[str, str]]]:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            repository = connection.execute(
                """
                SELECT root_path, source_fingerprint, generation, refreshed_at, schema_version
                FROM code_graph_repositories WHERE root_key = ?
                """,
                (root_key,),
            ).fetchone()
            if repository is None or repository["root_path"] != root_path:
                connection.commit()
                return None, {}, []

            files: dict[str, dict[str, Any]] = {}
            rows = connection.execute(
                """
                SELECT path, source_sha256, size_bytes, mtime_ns, parse_status,
                       nodes_json, structural_edges_json, import_groups_json,
                       call_sites_json, call_sites_truncated, import_bindings_json, error_json
                FROM code_graph_files WHERE root_key = ? ORDER BY path
                """,
                (root_key,),
            ).fetchall()
            for row in rows:
                try:
                    files[row["path"]] = {
                        "path": row["path"],
                        "source_sha256": row["source_sha256"],
                        "size_bytes": row["size_bytes"],
                        "mtime_ns": row["mtime_ns"],
                        "parse_status": row["parse_status"],
                        "nodes": json.loads(row["nodes_json"]),
                        "structural_edges": json.loads(row["structural_edges_json"]),
                        "import_groups": json.loads(row["import_groups_json"]),
                        "call_sites": json.loads(row["call_sites_json"]),
                        "call_sites_truncated": bool(row["call_sites_truncated"]),
                        "import_bindings": json.loads(row["import_bindings_json"]),
                        "error": json.loads(row["error_json"]) if row["error_json"] else None,
                    }
                except (TypeError, ValueError, json.JSONDecodeError):
                    # A damaged row is treated as a cache miss and reparsed.
                    continue

            edge_rows = connection.execute(
                """
                SELECT source_id, target_id, edge_type FROM code_graph_edges
                WHERE root_key = ? ORDER BY source_id, target_id, edge_type
                """,
                (root_key,),
            ).fetchall()
            edges = [
                {"source": row["source_id"], "target": row["target_id"], "type": row["edge_type"]}
                for row in edge_rows
            ]
            meta = dict(repository)
            connection.commit()
            return meta, files, edges
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def save_snapshot(
        self,
        *,
        root_key: str,
        root_path: str,
        source_fingerprint: str,
        generation_expected: int,
        files: dict[str, dict[str, Any]],
        edges: list[dict[str, str]],
        refreshed_at: str,
    ) -> int:
        """Atomically replace one workspace snapshot using an optimistic generation check."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT generation FROM code_graph_repositories WHERE root_key = ?",
                (root_key,),
            ).fetchone()
            actual_generation = int(current["generation"]) if current is not None else 0
            if actual_generation != generation_expected:
                raise GraphStoreConflict(
                    "Repository graph changed during refresh; retry against the latest snapshot."
                )

            generation = actual_generation + 1
            connection.execute(
                """
                INSERT INTO code_graph_repositories(
                    root_key, root_path, source_fingerprint, generation, refreshed_at, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(root_key) DO UPDATE SET
                    root_path=excluded.root_path,
                    source_fingerprint=excluded.source_fingerprint,
                    generation=excluded.generation,
                    refreshed_at=excluded.refreshed_at,
                    schema_version=excluded.schema_version
                """,
                (
                    root_key, root_path, source_fingerprint, generation,
                    refreshed_at, _SCHEMA_VERSION,
                ),
            )

            existing_paths = {
                row["path"] for row in connection.execute(
                    "SELECT path FROM code_graph_files WHERE root_key = ?", (root_key,)
                ).fetchall()
            }
            removed_paths = existing_paths - set(files)
            connection.executemany(
                "DELETE FROM code_graph_files WHERE root_key = ? AND path = ?",
                [(root_key, path) for path in sorted(removed_paths)],
            )

            connection.executemany(
                """
                INSERT INTO code_graph_files(
                    root_key, path, source_sha256, size_bytes, mtime_ns, parse_status,
                    nodes_json, structural_edges_json, import_groups_json,
                    call_sites_json, call_sites_truncated, import_bindings_json, error_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(root_key, path) DO UPDATE SET
                    source_sha256=excluded.source_sha256,
                    size_bytes=excluded.size_bytes,
                    mtime_ns=excluded.mtime_ns,
                    parse_status=excluded.parse_status,
                    nodes_json=excluded.nodes_json,
                    structural_edges_json=excluded.structural_edges_json,
                    import_groups_json=excluded.import_groups_json,
                    call_sites_json=excluded.call_sites_json,
                    call_sites_truncated=excluded.call_sites_truncated,
                    import_bindings_json=excluded.import_bindings_json,
                    error_json=excluded.error_json
                """,
                [
                    (
                        root_key, path, item["source_sha256"], item["size_bytes"],
                        item["mtime_ns"], item["parse_status"],
                        json.dumps(item["nodes"], ensure_ascii=False, separators=(",", ":")),
                        json.dumps(item["structural_edges"], ensure_ascii=False, separators=(",", ":")),
                        json.dumps(item["import_groups"], ensure_ascii=False, separators=(",", ":")),
                        json.dumps(item["call_sites"], ensure_ascii=False, separators=(",", ":")),
                        int(bool(item.get("call_sites_truncated", False))),
                        json.dumps(item["import_bindings"], ensure_ascii=False, separators=(",", ":")),
                        json.dumps(item["error"], ensure_ascii=False, separators=(",", ":"))
                        if item["error"] is not None else None,
                    )
                    for path, item in sorted(files.items())
                ],
            )

            connection.execute("DELETE FROM code_graph_edges WHERE root_key = ?", (root_key,))
            connection.executemany(
                """
                INSERT INTO code_graph_edges(root_key, source_id, target_id, edge_type)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (root_key, edge["source"], edge["target"], edge["type"])
                    for edge in edges
                ],
            )
            connection.commit()
            return generation
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

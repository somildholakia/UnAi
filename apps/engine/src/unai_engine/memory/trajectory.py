"""Compact state-delta memory with bounded retention and no transcript replay."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class SQLiteTrajectoryMemory:
    name = "sqlite-trajectory"

    def __init__(self, path: str | Path = ".data/trajectories.sqlite3", max_items: int = 20_000) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_items = max_items
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                objective TEXT NOT NULL,
                event_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, id DESC)")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=3000")
        return connection

    async def remember(self, context: Any, event: dict[str, Any]) -> None:
        safe = {key: value for key, value in event.items() if key not in {"api_key", "token", "password", "secret"}}
        encoded = json.dumps(safe, ensure_ascii=False, default=str)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO events(task_id, objective, event_json) VALUES (?, ?, ?)",
                (context.task_id, context.objective[:2000], encoded),
            )
            connection.execute(
                "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT ?)",
                (self.max_items,),
            )

    async def recall(self, context: Any, limit: int = 8) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 50))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_json FROM events WHERE task_id = ? ORDER BY id DESC LIMIT ?",
                (context.task_id, safe_limit),
            ).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]

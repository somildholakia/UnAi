"""Low-memory filesystem indexer; avoids loading whole repositories into RAM."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

IGNORED = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".next"}
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".toml", ".yaml", ".yml", ".go", ".rs", ".java", ".cpp", ".h", ".css", ".html", ".sh"}


class FilesystemIndex:
    name = "filesystem-index"

    async def index(self, root: str) -> dict[str, Any]:
        base = Path(root).resolve(strict=True)
        counts: dict[str, int] = {}
        files = 0
        total_bytes = 0
        for current, dirs, names in os.walk(base):
            dirs[:] = [name for name in dirs if name not in IGNORED and not name.startswith(".")]
            for name in names:
                path = Path(current) / name
                try:
                    relative = path.relative_to(base)
                    if path.is_symlink() or path.suffix.lower() not in TEXT_SUFFIXES:
                        continue
                    size = path.stat().st_size
                    if size > 1_000_000:
                        continue
                    files += 1
                    total_bytes += size
                    counts[path.suffix.lower() or "[no-extension]"] = counts.get(path.suffix.lower() or "[no-extension]", 0) + 1
                except (OSError, ValueError):
                    continue
        return {"root": str(base), "files": files, "text_bytes": total_bytes, "extensions": counts, "strategy": "streaming-walk-no-file-cache"}

    async def query(self, root: str, query: str, limit: int = 12) -> list[dict[str, Any]]:
        base = Path(root).resolve(strict=True)
        terms = [term.lower() for term in query.split() if len(term) > 1]
        found: list[dict[str, Any]] = []
        for current, dirs, names in os.walk(base):
            dirs[:] = [name for name in dirs if name not in IGNORED and not name.startswith(".")]
            for name in names:
                path = Path(current) / name
                if path.is_symlink() or path.suffix.lower() not in TEXT_SUFFIXES:
                    continue
                try:
                    if path.stat().st_size > 250_000:
                        continue
                    with path.open("r", encoding="utf-8", errors="ignore") as handle:
                        for line_number, line in enumerate(handle, 1):
                            lowered = line.lower()
                            if terms and all(term in lowered for term in terms):
                                found.append({"path": str(path.relative_to(base)), "line": line_number, "excerpt": line.strip()[:500]})
                                if len(found) >= max(1, min(limit, 100)):
                                    return found
                except OSError:
                    continue
        return found

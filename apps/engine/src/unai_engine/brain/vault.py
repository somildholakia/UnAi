"""Obsidian-compatible Markdown vault with a rebuildable SQLite graph index.

Markdown stays canonical. SQLite contains a disposable index; no embeddings, model
calls, GPU, or whole-vault body cache are required.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

WIKI = re.compile(r"!?\[\[([^\]]+)\]\]")
INLINE_TAG = re.compile(r"(?<![\w/])#([A-Za-z][\w/-]{0,63})")
IGNORED = {".git", ".obsidian", ".trash", "node_modules", ".venv", "venv", "__pycache__"}
MAX_NOTE_BYTES = 1_000_000


class BrainError(ValueError):
    """Raised when a vault input or path is invalid."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    value = re.sub(r"[^\w\- ]", "", value, flags=re.UNICODE).strip().lower()
    value = re.sub(r"[\s_-]+", "-", value).strip("-")[:100]
    if not value:
        raise BrainError("A note title must contain at least one letter or number.")
    return value


def _tags(value: Any) -> list[str]:
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return sorted({str(tag).strip().lstrip("#") for tag in values if str(tag).strip()})


class MarkdownVault:
    """Low-memory Markdown vault with FTS, tags, wikilinks and backlinks."""

    def __init__(self, root: str | Path, index_path: str | Path | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        index = Path(index_path).expanduser() if index_path else Path(".unai/brain.sqlite3")
        self.index_path = (index if index.is_absolute() else self.root / index).resolve()
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.fts = True
        self._init()

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.index_path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _init(self) -> None:
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS notes(
                    path TEXT PRIMARY KEY,title TEXT NOT NULL,body TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,tags_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,mtime_ns INTEGER NOT NULL,updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS links(
                    source_path TEXT NOT NULL,target_ref TEXT NOT NULL,target_path TEXT,
                    alias TEXT,anchor TEXT,PRIMARY KEY(source_path,target_ref,alias)
                );
                CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_path);
                CREATE TABLE IF NOT EXISTS tags(
                    tag TEXT NOT NULL,note_path TEXT NOT NULL,PRIMARY KEY(tag,note_path)
                );
                CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
            """)
            try:
                db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(path UNINDEXED,title,body,tags)")
            except sqlite3.OperationalError:
                self.fts = False

    def _path(self, relative: str) -> Path:
        if not relative or "\x00" in relative:
            raise BrainError("A non-empty relative note path is required.")
        path = (self.root / relative).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise BrainError("The note path escapes the selected vault.") from exc
        if path.suffix.lower() != ".md":
            raise BrainError("Brain notes must use the .md extension.")
        return path

    @staticmethod
    def _parse(text: str, relative: str) -> tuple[str, str, dict[str, Any], list[str]]:
        meta: dict[str, Any] = {}
        body = text
        if text.startswith("---\n") or text.startswith("---\r\n"):
            match = re.search(r"\n---\s*\r?\n", text[4:])
            if match:
                end = 4 + match.start()
                try:
                    parsed = yaml.safe_load(text[4:end]) or {}
                    if isinstance(parsed, dict):
                        meta = parsed
                except yaml.YAMLError:
                    meta = {"frontmatter_warning": "YAML could not be parsed; body was preserved."}
                body = text[4 + match.end():]
        title = str(meta.get("title") or Path(relative).stem.replace("-", " ").title())[:200]
        tags = _tags(meta.get("tags"))
        tags.extend(tag for tag in INLINE_TAG.findall(body) if tag not in tags)
        return title, body, meta, sorted(set(tags))

    @staticmethod
    def _resolve(source: str, raw: str, paths: dict[str, str], stems: dict[str, str], aliases: dict[str, str]):
        target, _, alias = raw.strip().partition("|")
        target, _, anchor = target.partition("#")
        target = target.strip().replace("\\", "/")
        if not target:
            return None, alias or None, anchor or None
        if not target.lower().endswith(".md"):
            target += ".md"
        options = [(Path(source).parent / target).as_posix(), target]
        for option in options:
            key = Path(option).as_posix().lstrip("./").casefold()
            if key in paths:
                return paths[key], alias or None, anchor or None
        resolved = stems.get(Path(target).stem.casefold()) or aliases.get(target.removesuffix(".md").casefold())
        return resolved, alias or None, anchor or None

    def index(self) -> dict[str, Any]:
        """Index one Markdown file at a time; only paths/stems remain in memory."""
        paths: dict[str, str] = {}
        stems: dict[str, str] = {}
        aliases: dict[str, str] = {}
        seen: set[str] = {}
        counts = {"notes": 0, "links": 0, "tags": 0}
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.fts:
                db.execute("DELETE FROM notes_fts")
            db.execute("DELETE FROM links")
            db.execute("DELETE FROM tags")
            for current, dirs, names in os.walk(self.root):
                dirs[:] = [name for name in dirs if name not in IGNORED and not name.startswith(".")]
                for name in names:
                    file = Path(current) / name
                    if name.startswith(".") or file.is_symlink() or file.suffix.lower() != ".md":
                        continue
                    try:
                        stat = file.stat()
                        if stat.st_size > MAX_NOTE_BYTES:
                            continue
                        relative = file.relative_to(self.root).as_posix()
                        raw = file.read_text(encoding="utf-8", errors="replace")
                    except (OSError, ValueError):
                        continue
                    title, body, meta, tags = self._parse(raw, relative)
                    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                    targets = [m.group(1).strip() for m in WIKI.finditer(body)]
                    now = _now()
                    db.execute("""INSERT INTO notes(path,title,body,metadata_json,tags_json,content_hash,mtime_ns,updated_at)
                        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET title=excluded.title,
                        body=excluded.body,metadata_json=excluded.metadata_json,tags_json=excluded.tags_json,
                        content_hash=excluded.content_hash,mtime_ns=excluded.mtime_ns,updated_at=excluded.updated_at""",
                        (relative, title, body, json.dumps(meta, ensure_ascii=False), json.dumps(tags), digest, stat.st_mtime_ns, now))
                    if self.fts:
                        db.execute("INSERT INTO notes_fts(path,title,body,tags) VALUES(?,?,?,?)", (relative, title, body, " ".join(tags)))
                    for tag in tags:
                        db.execute("INSERT OR IGNORE INTO tags(tag,note_path) VALUES(?,?)", (tag, relative))
                    for target in targets:
                        target_part, _, alias = target.partition("|")
                        target_part, _, anchor = target_part.partition("#")
                        db.execute("INSERT OR IGNORE INTO links(source_path,target_ref,target_path,alias,anchor) VALUES(?,?,?,?,?)",
                                   (relative, target, None, alias or None, anchor or None))
                    paths[relative.casefold()] = relative
                    stems.setdefault(Path(relative).stem.casefold(), relative)
                    note_aliases = meta.get("aliases", meta.get("alias", []))
                    if isinstance(note_aliases, str):
                        note_aliases = [note_aliases]
                    if isinstance(note_aliases, list):
                        for note_alias in note_aliases:
                            if isinstance(note_alias, str) and note_alias.strip():
                                aliases.setdefault(note_alias.strip().casefold(), relative)
                    seen.add(relative)
                    counts["notes"] += 1
                    counts["links"] += len(targets)
                    counts["tags"] += len(tags)
            # Resolve links after the complete path index exists, including forward links.
            for row in db.execute("SELECT source_path,target_ref,alias,anchor FROM links").fetchall():
                resolved, alias, anchor = self._resolve(row["source_path"], row["target_ref"], paths, stems, aliases)
                db.execute("UPDATE links SET target_path=?,alias=?,anchor=? WHERE source_path=? AND target_ref=?",
                           (resolved, alias, anchor, row["source_path"], row["target_ref"]))
            existing = {row[0] for row in db.execute("SELECT path FROM notes")}
            for stale in existing - seen:
                db.execute("DELETE FROM notes WHERE path=?", (stale,))
                if self.fts:
                    db.execute("DELETE FROM notes_fts WHERE path=?", (stale,))
        return {**counts, "unresolved_links": self._unresolved(), "index": str(self.index_path), "fts": self.fts}

    def _unresolved(self) -> int:
        with self._db() as db:
            return int(db.execute("SELECT COUNT(*) FROM links WHERE target_path IS NULL").fetchone()[0])

    def status(self) -> dict[str, Any]:
        with self._db() as db:
            return {"notes": db.execute("SELECT COUNT(*) FROM notes").fetchone()[0],
                    "links": db.execute("SELECT COUNT(*) FROM links WHERE target_path IS NOT NULL").fetchone()[0],
                    "unresolved_links": db.execute("SELECT COUNT(*) FROM links WHERE target_path IS NULL").fetchone()[0],
                    "tags": db.execute("SELECT COUNT(DISTINCT tag) FROM tags").fetchone()[0],
                    "fts": self.fts, "vault_root": str(self.root)}

    @staticmethod
    def _excerpt(body: str, terms: list[str], width: int = 260) -> str:
        lower = body.lower()
        found = [lower.find(term) for term in terms if lower.find(term) >= 0]
        start = max(0, (min(found) if found else 0) - 70)
        return ("…" if start else "") + body[start:start + width].strip() + ("…" if start + width < len(body) else "")

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        terms = re.findall(r"[\w-]{2,}", query.lower())[:12]
        if not terms:
            return []
        cap = max(1, min(int(limit), 50))
        with self._db() as db:
            rows = []
            if self.fts:
                expr = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
                try:
                    rows = db.execute("SELECT n.path,n.title,n.body,n.tags_json,bm25(notes_fts) rank FROM notes_fts JOIN notes n ON n.path=notes_fts.path WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?", (expr, cap)).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            else:
                like = "%" + "%".join(terms) + "%"
                rows = db.execute("SELECT path,title,body,tags_json,0 rank FROM notes WHERE lower(title || ' ' || body) LIKE ? LIMIT ?", (like, cap)).fetchall()
        return [{"path": r["path"], "title": r["title"], "excerpt": self._excerpt(r["body"], terms),
                 "tags": json.loads(r["tags_json"]), "rank": float(r["rank"])} for r in rows]

    def read(self, relative: str) -> dict[str, Any]:
        path = self._path(relative).relative_to(self.root).as_posix()
        with self._db() as db:
            row = db.execute("SELECT * FROM notes WHERE path=?", (path,)).fetchone()
            if row is None:
                raise FileNotFoundError(path)
            outgoing = db.execute("SELECT target_ref,target_path,alias,anchor FROM links WHERE source_path=?", (path,)).fetchall()
            incoming = db.execute("SELECT source_path FROM links WHERE target_path=? ORDER BY source_path", (path,)).fetchall()
        return {"path": path, "title": row["title"], "body": row["body"],
                "metadata": json.loads(row["metadata_json"]), "tags": json.loads(row["tags_json"]),
                "outgoing": [dict(r) for r in outgoing], "backlinks": [r[0] for r in incoming]}

    def graph(self, limit: int = 500) -> dict[str, Any]:
        cap = max(1, min(int(limit), 2000))
        with self._db() as db:
            notes = db.execute("SELECT path,title,tags_json FROM notes ORDER BY title LIMIT ?", (cap,)).fetchall()
            paths = {row["path"] for row in notes}
            links = db.execute("SELECT source_path,target_path FROM links WHERE target_path IS NOT NULL").fetchall()
        edges = [{"source": r["source_path"], "target": r["target_path"]} for r in links if r["source_path"] in paths and r["target_path"] in paths]
        return {"nodes": [{"id": r["path"], "label": r["title"], "tags": json.loads(r["tags_json"])} for r in notes], "edges": edges}

    def create(self, title: str, body: str, tags: list[str] | None = None, source: str = "user") -> dict[str, Any]:
        if not title.strip() or len(title) > 200:
            raise BrainError("Title must be between 1 and 200 characters.")
        if len(body.encode("utf-8")) > MAX_NOTE_BYTES:
            raise BrainError("Note is larger than the 1 MB safety limit.")
        stem = _slug(title)
        relative = f"{stem}.md"
        target = self._path(relative)
        suffix = 2
        while target.exists():
            relative = f"{stem}-{suffix}.md"
            target = self._path(relative)
            suffix += 1
        metadata = {"title": title.strip(), "tags": _tags(tags or []), "source": source,
                    "created": _now(), "updated": _now()}
        header = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"---\n{header}\n---\n\n{body.rstrip()}\n", encoding="utf-8")
        self.index()
        return self.read(relative)

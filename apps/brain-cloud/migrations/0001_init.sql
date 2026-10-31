PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS notes (
  id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  tags_json TEXT NOT NULL DEFAULT '[]',
  source TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS note_fts USING fts5(
  id UNINDEXED, title, body, tags
);

CREATE TABLE IF NOT EXISTS links (
  source_id TEXT NOT NULL,
  target_id TEXT,
  target_ref TEXT NOT NULL,
  alias TEXT,
  PRIMARY KEY(source_id, target_ref, alias),
  FOREIGN KEY(source_id) REFERENCES notes(id) ON DELETE CASCADE,
  FOREIGN KEY(target_id) REFERENCES notes(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_id);

CREATE TABLE IF NOT EXISTS tags (
  tag TEXT NOT NULL,
  note_id TEXT NOT NULL,
  PRIMARY KEY(tag, note_id),
  FOREIGN KEY(note_id) REFERENCES notes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);

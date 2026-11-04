-- Existing hosted notes remain in a reserved single-installation tenant.
-- New API credentials always receive a distinct tenant namespace.
ALTER TABLE notes ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE links ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE tags ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';

CREATE INDEX IF NOT EXISTS idx_notes_tenant_title ON notes(tenant_id, title);
CREATE INDEX IF NOT EXISTS idx_links_tenant_source ON links(tenant_id, source_id);
CREATE INDEX IF NOT EXISTS idx_links_tenant_target ON links(tenant_id, target_id);
CREATE INDEX IF NOT EXISTS idx_tags_tenant_tag ON tags(tenant_id, tag);

CREATE TABLE IF NOT EXISTS api_tokens (
  token_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL,
  scopes_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_tokens_tenant ON api_tokens(tenant_id, created_at);

CREATE TABLE IF NOT EXISTS rate_buckets (
  bucket_key TEXT NOT NULL,
  window_start INTEGER NOT NULL,
  request_count INTEGER NOT NULL CHECK(request_count >= 0),
  PRIMARY KEY(bucket_key, window_start)
);

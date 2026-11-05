interface Env {
  DB: D1Database;
  ASSETS: Fetcher;
  // Installation administrator: required to issue/revoke tenant tokens.
  BRAIN_ADMIN_TOKEN: string;
  // Optional compatibility credentials scoped to the legacy "default" tenant.
  BRAIN_READ_TOKEN?: string;
  BRAIN_WRITE_TOKEN?: string;
  BRAIN_ALLOWED_ORIGIN?: string;
  BRAIN_RATE_LIMIT_PER_MINUTE?: string;
}

interface Identity {
  tenantId: string;
  tokenId: string;
  scopes: Set<string>;
  admin: boolean;
}

const VALID_SCOPES = new Set(["brain:read", "brain:write"]);
const WIKI = /!?\[\[([^\]]+)\]\]/g;
const DEFAULT_RATE_LIMIT = 120;

function json(data: unknown, status = 200, origin = "*"): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "access-control-allow-origin": origin,
      "access-control-allow-methods": "GET,POST,OPTIONS",
      "access-control-allow-headers": "Content-Type,Authorization",
      "vary": "Origin"
    }
  });
}

function constantTimeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, "0")).join("");
}

function slugify(value: string): string {
  return value.normalize("NFKC").toLowerCase()
    .replace(/[^\p{L}\p{N}-]+/gu, "-")
    .replace(/-+/g, "-").replace(/^-|-$/g, "").slice(0, 90);
}

function tagsFrom(value: unknown): string[] {
  const list = Array.isArray(value) ? value :
    typeof value === "string" ? [value] : [];
  return [...new Set(list.map(x => String(x).trim().replace(/^#/, ""))
    .filter(x => /^[\p{L}\p{N}_/-]{1,64}$/u.test(x)))].slice(0, 12);
}

function validTenantId(value: unknown): value is string {
  return typeof value === "string" && value !== "default" &&
    /^[a-z0-9][a-z0-9_-]{0,63}$/.test(value);
}

function authorizedStaticToken(
  token: string,
  configured: string | undefined,
  scope: string
): Identity | null {
  if (!configured || !constantTimeEqual(token, configured)) return null;
  return { tenantId: "default", tokenId: "legacy-" + scope.replace(":", "-"),
    scopes: new Set([scope]), admin: false };
}

async function authenticate(request: Request, env: Env): Promise<Identity | null> {
  const header = request.headers.get("authorization") || "";
  const match = /^Bearer ([^\s]+)$/i.exec(header);
  if (!match) return null;
  const rawToken = match[1];

  if (env.BRAIN_ADMIN_TOKEN && constantTimeEqual(rawToken, env.BRAIN_ADMIN_TOKEN)) {
    return { tenantId: "default", tokenId: "installation-admin",
      scopes: new Set(VALID_SCOPES), admin: true };
  }

  const legacyRead = authorizedStaticToken(rawToken, env.BRAIN_READ_TOKEN, "brain:read");
  const legacyWrite = authorizedStaticToken(rawToken, env.BRAIN_WRITE_TOKEN, "brain:write");
  if (legacyRead || legacyWrite) {
    const scopes = new Set<string>();
    if (legacyRead) scopes.add("brain:read");
    if (legacyWrite) scopes.add("brain:write");
    return {
      tenantId: "default",
      tokenId: legacyRead && legacyWrite ? "legacy-read-write" : (legacyRead || legacyWrite)!.tokenId,
      scopes,
      admin: false
    };
  }

  const tokenHash = await sha256(rawToken);
  const row = await env.DB.prepare(
    "SELECT token_id,tenant_id,token_hash,scopes_json,expires_at,revoked_at " +
    "FROM api_tokens WHERE token_hash=? LIMIT 1"
  ).bind(tokenHash).first<{
    token_id: string; tenant_id: string; token_hash: string; scopes_json: string;
    expires_at: string; revoked_at: string | null;
  }>();
  if (!row || row.revoked_at || !constantTimeEqual(row.token_hash, tokenHash)) return null;
  const expires = Date.parse(row.expires_at);
  if (!Number.isFinite(expires) || expires <= Date.now()) return null;

  let scopes: unknown;
  try { scopes = JSON.parse(row.scopes_json); }
  catch { return null; }
  if (!Array.isArray(scopes) || scopes.length === 0 ||
      scopes.some(scope => typeof scope !== "string" || !VALID_SCOPES.has(scope)) ||
      new Set(scopes).size !== scopes.length || !validTenantId(row.tenant_id)) return null;

  return { tenantId: row.tenant_id, tokenId: row.token_id, scopes: new Set(scopes), admin: false };
}

function requireScope(identity: Identity, scope: string): boolean {
  return identity.admin || identity.scopes.has(scope);
}

async function allowRequest(request: Request, env: Env, identity: Identity): Promise<boolean> {
  const parsed = Number.parseInt(env.BRAIN_RATE_LIMIT_PER_MINUTE || "", 10);
  const limit = Number.isFinite(parsed) ? Math.max(1, Math.min(parsed, 10_000)) : DEFAULT_RATE_LIMIT;
  const windowStart = Math.floor(Date.now() / 60_000) * 60_000;
  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  const ipHash = await sha256(ip);
  const keys = [
    "principal:" + identity.tenantId + ":" + identity.tokenId,
    "ip:" + ipHash
  ];
  await env.DB.batch(keys.map(key => env.DB.prepare(
    "INSERT INTO rate_buckets(bucket_key,window_start,request_count) VALUES(?,?,1) " +
    "ON CONFLICT(bucket_key,window_start) DO UPDATE SET request_count=request_count+1"
  ).bind(key, windowStart)));
  const counts = await Promise.all(keys.map(key => env.DB.prepare(
    "SELECT request_count FROM rate_buckets WHERE bucket_key=? AND window_start=?"
  ).bind(key, windowStart).first<{ request_count: number }>()));
  await env.DB.prepare("DELETE FROM rate_buckets WHERE window_start < ?").bind(windowStart - 120_000).run();
  return counts.every(row => Number(row?.request_count || 0) <= limit);
}

async function search(request: Request, env: Env, origin: string, tenantId: string): Promise<Response> {
  const url = new URL(request.url);
  const query = (url.searchParams.get("q") || "").trim().slice(0, 240);
  const terms = query.match(/[\p{L}\p{N}_-]{2,}/gu)?.slice(0, 10) || [];
  if (!terms.length) return json({ results: [] }, 200, origin);
  const match = terms.map(term => '"' + term.replaceAll('"', '""') + '"').join(" OR ");
  const limit = Math.max(1, Math.min(Number(url.searchParams.get("limit") || 10), 30));
  try {
    const result = await env.DB.prepare(
      "SELECT n.id,n.slug,n.title,n.tags_json,substr(n.body,1,320) AS excerpt " +
      "FROM note_fts f JOIN notes n ON n.id=f.id " +
      "WHERE note_fts MATCH ? AND n.tenant_id=? ORDER BY bm25(note_fts) LIMIT ?"
    ).bind(match, tenantId, limit).all();
    return json({ results: result.results || [] }, 200, origin);
  } catch {
    return json({ error: "Search failed. Rebuild the Brain index and retry." }, 500, origin);
  }
}

async function noteDetail(id: string, env: Env, origin: string, tenantId: string): Promise<Response> {
  const note = await env.DB.prepare(
    "SELECT id,slug,title,body,tags_json,source,created_at,updated_at " +
    "FROM notes WHERE tenant_id=? AND (id=? OR slug=?) LIMIT 1"
  ).bind(tenantId, id, id).first<Record<string, unknown>>();
  if (!note) return json({ error: "Note not found" }, 404, origin);
  const key = String(note.id);
  const [outgoing, backlinks] = await Promise.all([
    env.DB.prepare(
      "SELECT target_ref,target_id,alias FROM links WHERE tenant_id=? AND source_id=? ORDER BY target_ref"
    ).bind(tenantId, key).all(),
    env.DB.prepare(
      "SELECT l.source_id,n.slug,n.title FROM links l JOIN notes n ON n.id=l.source_id " +
      "WHERE l.tenant_id=? AND l.target_id=? AND n.tenant_id=? ORDER BY n.title"
    ).bind(tenantId, key, tenantId).all()
  ]);
  return json({
    ...note,
    tags: JSON.parse(String(note.tags_json || "[]")),
    outgoing: outgoing.results || [],
    backlinks: backlinks.results || []
  }, 200, origin);
}

async function graph(env: Env, origin: string, tenantId: string): Promise<Response> {
  const [nodes, edges] = await Promise.all([
    env.DB.prepare(
      "SELECT id,slug,title,tags_json FROM notes WHERE tenant_id=? ORDER BY title LIMIT 800"
    ).bind(tenantId).all(),
    env.DB.prepare(
      "SELECT source_id AS source,target_id AS target FROM links " +
      "WHERE tenant_id=? AND target_id IS NOT NULL LIMIT 4000"
    ).bind(tenantId).all()
  ]);
  return json({
    nodes: (nodes.results || []).map((row: Record<string, unknown>) => ({
      ...row, label: row.title, tags: JSON.parse(String(row.tags_json || "[]"))
    })),
    edges: edges.results || []
  }, 200, origin);
}

async function createNote(
  request: Request, env: Env, origin: string, tenantId: string
): Promise<Response> {
  let input: Record<string, unknown>;
  try { input = await request.json() as Record<string, unknown>; }
  catch { return json({ error: "Invalid JSON body" }, 400, origin); }

  const title = String(input.title || "").trim();
  const body = String(input.body || "");
  if (!title || title.length > 200 || new TextEncoder().encode(body).length > 850_000) {
    return json({ error: "Invalid title or note body" }, 400, origin);
  }
  const tags = tagsFrom(input.tags);
  const source = String(input.source || "model").slice(0, 120);
  const slug = (slugify(title) || "note") + "-" + crypto.randomUUID().slice(0, 8);
  const id = crypto.randomUUID();
  const now = new Date().toISOString();

  const statements: D1PreparedStatement[] = [
    env.DB.prepare(
      "INSERT INTO notes(id,slug,title,body,tags_json,source,created_at,updated_at,tenant_id) " +
      "VALUES(?,?,?,?,?,?,?,?,?)"
    ).bind(id, slug, title, body, JSON.stringify(tags), source, now, now, tenantId),
    env.DB.prepare("INSERT INTO note_fts(id,title,body,tags) VALUES(?,?,?,?)")
      .bind(id, title, body, tags.join(" "))
  ];
  for (const tag of tags) {
    statements.push(env.DB.prepare(
      "INSERT OR IGNORE INTO tags(tag,note_id,tenant_id) VALUES(?,?,?)"
    ).bind(tag, id, tenantId));
  }
  for (const match of [...body.matchAll(WIKI)].slice(0, 12)) {
    const raw = String(match[1] || "").trim();
    const [target, alias] = raw.split("|").map(part => part.trim());
    if (!target) continue;
    const targetSlug = slugify(target.replace(/#.*/, ""));
    const targetRow = await env.DB.prepare(
      "SELECT id FROM notes WHERE tenant_id=? AND (slug LIKE ? OR lower(title)=lower(?)) LIMIT 1"
    ).bind(tenantId, targetSlug + "-%", target).first<{ id: string }>();
    statements.push(env.DB.prepare(
      "INSERT OR IGNORE INTO links(source_id,target_id,target_ref,alias,tenant_id) VALUES(?,?,?,?,?)"
    ).bind(id, targetRow?.id || null, target, alias || null, tenantId));
  }
  await env.DB.batch(statements);
  return json({ id, slug, title, tags, created_at: now }, 201, origin);
}

async function issueToken(
  request: Request, env: Env, origin: string
): Promise<Response> {
  let input: Record<string, unknown>;
  try { input = await request.json() as Record<string, unknown>; }
  catch { return json({ error: "Invalid JSON body" }, 400, origin); }
  const tenantId = input.tenant_id;
  const label = typeof input.label === "string" ? input.label.trim() : "";
  const scopes = input.scopes;
  const expiresInDays = input.expires_in_days === undefined ? 30 : input.expires_in_days;
  if (!validTenantId(tenantId) || !label || label.length > 100 ||
      !Array.isArray(scopes) || scopes.length === 0 || scopes.length > VALID_SCOPES.size ||
      scopes.some(scope => typeof scope !== "string" || !VALID_SCOPES.has(scope)) ||
      new Set(scopes).size !== scopes.length ||
      !Number.isInteger(expiresInDays) || Number(expiresInDays) < 1 || Number(expiresInDays) > 365) {
    return json({ error: "Invalid tenant, label, scopes, or token expiry." }, 400, origin);
  }

  const rawToken = "unai_brain_" + crypto.randomUUID() + crypto.randomUUID();
  const tokenId = crypto.randomUUID();
  const now = new Date();
  const expiresAt = new Date(now.getTime() + Number(expiresInDays) * 86_400_000);
  const digest = await sha256(rawToken);
  await env.DB.prepare(
    "INSERT INTO api_tokens(token_id,tenant_id,token_hash,label,scopes_json,created_at,expires_at) " +
    "VALUES(?,?,?,?,?,?,?)"
  ).bind(tokenId, tenantId, digest, label, JSON.stringify(scopes), now.toISOString(), expiresAt.toISOString()).run();

  return json({
    token_id: tokenId, tenant_id: tenantId, label, token: rawToken, scopes,
    created_at: now.toISOString(), expires_at: expiresAt.toISOString()
  }, 201, origin);
}

async function revokeToken(tokenId: string, env: Env, origin: string): Promise<Response> {
  if (!/^[0-9a-f-]{36}$/i.test(tokenId)) return json({ error: "Token not found" }, 404, origin);
  const result = await env.DB.prepare(
    "UPDATE api_tokens SET revoked_at=? WHERE token_id=? AND revoked_at IS NULL"
  ).bind(new Date().toISOString(), tokenId).run();
  if ((result.meta?.changes || 0) !== 1) return json({ error: "Active token not found" }, 404, origin);
  return json({ token_id: tokenId, revoked: true }, 200, origin);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const origin = env.BRAIN_ALLOWED_ORIGIN || "*";
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: {
        "access-control-allow-origin": origin,
        "access-control-allow-methods": "GET,POST,OPTIONS",
        "access-control-allow-headers": "Content-Type,Authorization",
        "access-control-max-age": "86400",
        "vary": "Origin"
      }});
    }
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    if (!path.startsWith("/api/")) {
      if (request.method === "GET") return env.ASSETS.fetch(request);
      return json({ error: "Not found" }, 404, origin);
    }

    try {
      const configuredSecrets = [env.BRAIN_ADMIN_TOKEN, env.BRAIN_READ_TOKEN, env.BRAIN_WRITE_TOKEN]
        .filter((secret): secret is string => Boolean(secret));
      if (configuredSecrets.some(secret => secret.length < 32)) {
        return json({ error: "Configured Brain bearer secrets must be at least 32 characters." }, 503, origin);
      }
      const identity = await authenticate(request, env);
      if (!identity) return json({ error: "A valid bearer token is required for Brain API access." }, 401, origin);
      if (!await allowRequest(request, env, identity)) {
        const limited = json({ error: "API rate limit exceeded" }, 429, origin);
        limited.headers.set("retry-after", "60");
        return limited;
      }

      if (request.method === "POST" && path === "/api/auth/tokens") {
        if (!identity.admin) return json({ error: "Administrator credential required" }, 403, origin);
        return issueToken(request, env, origin);
      }
      const revokeMatch = /^\/api\/auth\/tokens\/([^/]+)\/revoke$/.exec(path);
      if (request.method === "POST" && revokeMatch) {
        if (!identity.admin) return json({ error: "Administrator credential required" }, 403, origin);
        return revokeToken(decodeURIComponent(revokeMatch[1]), env, origin);
      }

      if (request.method === "GET" && path === "/api/health") {
        if (!requireScope(identity, "brain:read")) return json({ error: "brain:read scope required" }, 403, origin);
        const row = await env.DB.prepare("SELECT COUNT(*) AS notes FROM notes WHERE tenant_id=?")
          .bind(identity.tenantId).first<{ notes: number }>();
        return json({ status: "ok", notes: row?.notes || 0, inference: false, gpu_required: false }, 200, origin);
      }
      if (request.method === "GET" && path === "/api/search") {
        if (!requireScope(identity, "brain:read")) return json({ error: "brain:read scope required" }, 403, origin);
        return search(request, env, origin, identity.tenantId);
      }
      if (request.method === "GET" && path === "/api/graph") {
        if (!requireScope(identity, "brain:read")) return json({ error: "brain:read scope required" }, 403, origin);
        return graph(env, origin, identity.tenantId);
      }
      if (request.method === "GET" && path.startsWith("/api/notes/")) {
        if (!requireScope(identity, "brain:read")) return json({ error: "brain:read scope required" }, 403, origin);
        return noteDetail(decodeURIComponent(path.slice("/api/notes/".length)), env, origin, identity.tenantId);
      }
      if (request.method === "POST" && path === "/api/notes") {
        if (!requireScope(identity, "brain:write")) return json({ error: "brain:write scope required" }, 403, origin);
        return createNote(request, env, origin, identity.tenantId);
      }
      return json({ error: "Not found" }, 404, origin);
    } catch {
      return json({ error: "Brain request failed" }, 500, origin);
    }
  }
};

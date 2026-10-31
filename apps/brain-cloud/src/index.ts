interface Env {
  DB: D1Database;
  ASSETS: Fetcher;
  BRAIN_WRITE_TOKEN: string;
  BRAIN_ALLOWED_ORIGIN?: string;
}

const WIKI = /!?\[\[([^\]]+)\]\]/g;

function json(data: unknown, status = 200, origin = "*"): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "access-control-allow-origin": origin,
      "access-control-allow-methods": "GET,POST,OPTIONS",
      "access-control-allow-headers": "Content-Type,Authorization"
    }
  });
}

function slugify(value: string): string {
  return value.normalize("NFKC").toLowerCase()
    .replace(/[^\p{L}\p{N}-]+/gu, "-")
    .replace(/-+/g, "-").replace(/^-|-$/g, "").slice(0, 90);
}

function authorised(request: Request, env: Env): boolean {
  return Boolean(env.BRAIN_WRITE_TOKEN) &&
    request.headers.get("authorization") === "Bearer " + env.BRAIN_WRITE_TOKEN;
}

function tagsFrom(value: unknown): string[] {
  const list = Array.isArray(value) ? value :
    typeof value === "string" ? [value] : [];
  return [...new Set(list.map(x => String(x).trim().replace(/^#/, ""))
    .filter(x => /^[\p{L}\p{N}_/-]{1,64}$/u.test(x)))].slice(0, 12);
}

async function search(request: Request, env: Env, origin: string): Promise<Response> {
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
      "WHERE note_fts MATCH ? ORDER BY bm25(note_fts) LIMIT ?"
    ).bind(match, limit).all();
    return json({ results: result.results || [] }, 200, origin);
  } catch {
    return json({ error: "Search failed. Rebuild the Brain index and retry." }, 500, origin);
  }
}

async function noteDetail(id: string, env: Env, origin: string): Promise<Response> {
  const note = await env.DB.prepare(
    "SELECT id,slug,title,body,tags_json,source,created_at,updated_at " +
    "FROM notes WHERE id=? OR slug=? LIMIT 1"
  ).bind(id, id).first<Record<string, unknown>>();
  if (!note) return json({ error: "Note not found" }, 404, origin);
  const key = String(note.id);
  const [outgoing, backlinks] = await Promise.all([
    env.DB.prepare(
      "SELECT target_ref,target_id,alias FROM links WHERE source_id=? ORDER BY target_ref"
    ).bind(key).all(),
    env.DB.prepare(
      "SELECT l.source_id,n.slug,n.title FROM links l JOIN notes n ON n.id=l.source_id " +
      "WHERE l.target_id=? ORDER BY n.title"
    ).bind(key).all()
  ]);
  return json({
    ...note,
    tags: JSON.parse(String(note.tags_json || "[]")),
    outgoing: outgoing.results || [],
    backlinks: backlinks.results || []
  }, 200, origin);
}

async function graph(env: Env, origin: string): Promise<Response> {
  const [nodes, edges] = await Promise.all([
    env.DB.prepare("SELECT id,slug,title,tags_json FROM notes ORDER BY title LIMIT 800").all(),
    env.DB.prepare("SELECT source_id AS source,target_id AS target FROM links WHERE target_id IS NOT NULL LIMIT 4000").all()
  ]);
  return json({
    nodes: (nodes.results || []).map((row: Record<string, unknown>) => ({
      ...row, label: row.title, tags: JSON.parse(String(row.tags_json || "[]"))
    })),
    edges: edges.results || []
  }, 200, origin);
}

async function createNote(request: Request, env: Env, origin: string): Promise<Response> {
  if (!authorised(request, env)) {
    return json({ error: "A valid BRAIN_WRITE_TOKEN is required" }, 401, origin);
  }
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
      "INSERT INTO notes(id,slug,title,body,tags_json,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)"
    ).bind(id, slug, title, body, JSON.stringify(tags), source, now, now),
    env.DB.prepare("INSERT INTO note_fts(id,title,body,tags) VALUES(?,?,?,?)")
      .bind(id, title, body, tags.join(" "))
  ];
  for (const tag of tags) {
    statements.push(env.DB.prepare("INSERT OR IGNORE INTO tags(tag,note_id) VALUES(?,?)").bind(tag, id));
  }
  for (const match of [...body.matchAll(WIKI)].slice(0, 12)) {
    const raw = String(match[1] || "").trim();
    const [target, alias] = raw.split("|").map(part => part.trim());
    if (!target) continue;
    const targetSlug = slugify(target.replace(/#.*/, ""));
    const targetRow = await env.DB.prepare(
      "SELECT id FROM notes WHERE slug LIKE ? OR lower(title)=lower(?) LIMIT 1"
    ).bind(targetSlug + "-%", target).first<{ id: string }>();
    statements.push(env.DB.prepare(
      "INSERT OR IGNORE INTO links(source_id,target_id,target_ref,alias) VALUES(?,?,?,?)"
    ).bind(id, targetRow?.id || null, target, alias || null));
  }
  await env.DB.batch(statements);
  return json({ id, slug, title, tags, created_at: now }, 201, origin);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const origin = env.BRAIN_ALLOWED_ORIGIN || "*";
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: {
        "access-control-allow-origin": origin,
        "access-control-allow-methods": "GET,POST,OPTIONS",
        "access-control-allow-headers": "Content-Type,Authorization",
        "access-control-max-age": "86400"
      }});
    }
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    try {
      if (request.method === "GET" && path === "/api/health") {
        const row = await env.DB.prepare("SELECT COUNT(*) AS notes FROM notes")
          .first<{ notes: number }>();
        return json({ status: "ok", notes: row?.notes || 0, inference: false, gpu_required: false }, 200, origin);
      }
      if (request.method === "GET" && path === "/api/search") return search(request, env, origin);
      if (request.method === "GET" && path === "/api/graph") return graph(env, origin);
      if (request.method === "GET" && path.startsWith("/api/notes/")) {
        return noteDetail(decodeURIComponent(path.slice("/api/notes/".length)), env, origin);
      }
      if (request.method === "POST" && path === "/api/notes") return createNote(request, env, origin);
      if (request.method === "GET" && !path.startsWith("/api/")) return env.ASSETS.fetch(request);
      return json({ error: "Not found" }, 404, origin);
    } catch {
      return json({ error: "Brain request failed" }, 500, origin);
    }
  }
};

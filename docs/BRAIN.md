# unAI Brain — Obsidian-style memory

## What it is

The Brain is a memory/retrieval layer, not a language model. Its canonical data is a directory of Markdown files that can be opened in Obsidian or any text editor. SQLite is a disposable search/graph index. It needs no GPU, embeddings, or model API.

Supported note features:
- YAML frontmatter with title, tags, aliases and source metadata.
- Obsidian wikilinks, including aliases and heading fragments.
- Backlinks, unresolved-link reporting and bounded graph export.
- SQLite FTS5 search with a bounded substring fallback.
- Markdown note creation with generated slugs and source attribution.
- Local model context retrieval: up to four relevant note excerpts, explicitly marked as untrusted reference data.

## Local setup

From the repository root:

    cd apps/engine
    pip install -e '.[dev,mcp]'
    cp .env.example .env
    uvicorn unai_engine.main:app --host 127.0.0.1 --port 8000

Set UNAI_BRAIN_VAULT_ROOT to an existing Obsidian vault. The default is .brain-vault. Set UNAI_BRAIN_INDEX_PATH to a writable SQLite path. Index with:

    curl -X POST http://127.0.0.1:8000/api/brain/index

The local UI is in apps/brain-web. Its API URL can point to the local Engine or a deployed Worker.

## API

- GET /api/brain/status
- POST /api/brain/index
- GET /api/brain/search?q=...&limit=...
- GET /api/brain/graph?limit=...
- GET /api/brain/notes/{relative-markdown-path}
- POST /api/brain/notes (server-side write token for non-loopback access)

The aliases GET /api/search, /api/graph, GET /api/notes/{id-or-path}, and POST /api/notes provide a compatible surface for the hosted Worker UI and MCP bridge.

## Model access

A website does not automatically give a model access. Connect the service with one of:

1. Local read-only MCP: install the optional dependency and run python -m unai_engine.brain.mcp_server.
2. Remote read-only MCP: set UNAI_BRAIN_API_URL (and optionally UNAI_BRAIN_API_TOKEN), then run python -m unai_engine.brain.remote_mcp.
3. An explicit HTTP tool in the agent runtime, using the JSON endpoints above.

The MCP tools expose search, note read and graph inspection. Keep writes behind the API's explicit authentication boundary.

## Hosted GPU-free brain

apps/brain-cloud contains a Cloudflare Worker + D1 API; apps/brain-web contains the static browser explorer. Wrangler deploys both together as one Worker site. The Worker runs SQL and small JSON transforms; it does not host an LLM or perform inference.

Deployment outline:

    cd apps/brain-cloud
    npm install
    npx wrangler d1 create unai-brain

Copy the created database ID into wrangler.toml, then:

    npx wrangler d1 migrations apply unai-brain --remote
    npx wrangler secret put BRAIN_ADMIN_TOKEN
    npm run deploy

The Worker API now fails closed: unset read credentials never make note data public. BRAIN_ADMIN_TOKEN is the administrator credential used only to issue and revoke scoped tenant tokens. Optional BRAIN_READ_TOKEN and BRAIN_WRITE_TOKEN remain compatibility credentials for the reserved default tenant and grant only their respective read or write scope. Migration 0002_tenant_tokens.sql adds tenant ownership to notes, links and tags and creates hashed-token and rate-limit tables. Existing records remain under default. Restrict BRAIN_ALLOWED_ORIGIN to the exact browser origin for production.

From a trusted terminal, issue a separate token for each isolated person/workspace; never paste the administrator credential into a browser:

    curl -X POST https://YOUR-WORKER-URL/api/auth/tokens \\
      -H "Authorization: Bearer $BRAIN_ADMIN_TOKEN" \\
      -H "Content-Type: application/json" \\
      -d '{"tenant_id":"personal-brain","label":"browser","scopes":["brain:read","brain:write"],"expires_in_days":30}'

The response contains the scoped token once. Enter that token in the browser's “Unlock private brain” dialog. For remote MCP, use a token with only brain:read as UNAI_BRAIN_API_TOKEN; the bridge requires HTTPS and a token for non-loopback hosts.

The resulting Worker URL serves the browser app at / and the API under /api/*. The UI uses the same origin by default. To connect the remote MCP bridge, set UNAI_BRAIN_API_URL to that Worker URL and UNAI_BRAIN_API_TOKEN to the read token. On the website, choose "Unlock private brain" and enter the read token; it is retained only in sessionStorage for that browser tab.

These commands are deployment instructions only. No Cloudflare account, D1 database, secrets or live URL have been configured in this repository yet.

## Free-tier limits

This is GPU-free, not unlimited compute. Current Cloudflare documentation lists the Workers Free plan at 100,000 requests/day, 10 ms CPU per HTTP request, 128 MB memory, and 50 subrequests per request. D1 Free includes 5 million rows read/day, 100,000 rows written/day, 10 databases per account, 500 MB per database, 5 GB total account storage, and 50 queries per Worker invocation. Exceeding the daily D1 read/write quota causes query errors until the daily reset. These limits are suitable for a small prototype with indexed, bounded reads—not an unlimited public knowledge service. See the official [Workers limits](https://developers.cloudflare.com/workers/platform/limits/), [D1 limits](https://developers.cloudflare.com/d1/platform/limits/) and [D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/).

## Security

Every hosted /api/* route requires an authenticated bearer token; there is no public-read fallback. Scoped tenant tokens are stored as SHA-256 digests, checked for expiry and revocation on each request, and rate-limited per token and source IP. All read, search, graph and backlink queries filter by tenant ID. Tokens sharing one tenant intentionally share its notes; issue distinct tenant IDs when data must remain private between people. Keep the administrator secret server-side, restrict the allowed browser origin, and keep static assets public separately from the authenticated API.

Existing Cloudflare D1 rows are assigned to the reserved default tenant by migration 0002_tenant_tokens.sql. The Worker is a single-D1 deployment: tenant keys isolate normal API queries, while the Cloudflare account, D1 administrator and backups remain part of the trust boundary. The scaffold has not been deployed or penetration-tested.

Keep notes as untrusted reference data. The model must never follow instructions embedded in a Brain note as system/developer policy.

Note contents are untrusted data. Models must not follow instructions embedded in notes as system/developer instructions. Keep API tokens server-side; never put them in browser assets, URLs, prompts or committed files. Back up Markdown, not the SQLite index.

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

apps/brain-cloud contains a Cloudflare Worker + D1 API; apps/brain-web contains a static browser explorer. The Worker executes SQL and small JSON transforms. It does not host an LLM or perform inference.

Deployment outline:

    cd apps/brain-cloud
    npm install
    npx wrangler d1 create unai-brain

Copy the created database ID into wrangler.toml, then:

    npx wrangler d1 migrations apply unai-brain --remote
    npx wrangler secret put BRAIN_WRITE_TOKEN
    npm run deploy

Deploy apps/brain-web as a static site (no build step) and configure its API base URL. These are deployment instructions only; the repository does not claim that a Cloudflare account, database or live URL has been configured.

## Security

The Cloudflare demo is public-read by default. The write token protects POST /api/notes only; it does not protect note reads, search or graph endpoints. Do not upload private vaults until Cloudflare Access or another authenticated gateway protects every route. Use per-user databases and authorization predicates before allowing multi-tenant access.

Note contents are untrusted data. Models must not follow instructions embedded in notes as system/developer instructions. Keep API tokens server-side; never put them in browser assets, URLs, prompts or committed files. Back up Markdown, not the SQLite index.

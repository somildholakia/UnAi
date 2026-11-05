# unAI API security and tenant isolation

## Engine authentication

The Engine accepts three identity modes:

1. **Installation administrator** — configured `UNAI_API_TOKEN` is an administrator credential for the reserved `default` tenant and may issue/revoke scoped API tokens. It is not a universal data-read shortcut across tenant stores.
2. **Loopback-local administrator** — when `UNAI_API_TOKEN` is unset, requests arriving from a loopback peer receive local administrator access to the `default` tenant. Forwarded-IP headers are never trusted.
3. **Scoped token** — an administrator-issued token is bound to one non-reserved tenant ID, a bounded set of scopes, and an expiry no longer than 365 days.

Issue a credential from a trusted terminal, not a browser bundle or model prompt:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
# Put the generated value into UNAI_API_TOKEN in the Engine process environment.
curl -X POST http://127.0.0.1:8000/api/auth/tokens \
  -H "Authorization: Bearer $UNAI_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"somil-personal","label":"local web UI","scopes":["chat:use","tasks:read","tasks:write","workspace:read","workspace:write","workspace:execute","brain:read","brain:write"],"expires_in_days":30}'
```

The response contains the opaque token exactly once. SQLite stores its SHA-256 digest, not the secret. Revoke it immediately with:

```bash
curl -X POST http://127.0.0.1:8000/api/auth/tokens/TOKEN_ID/revoke \
  -H "Authorization: Bearer $UNAI_API_TOKEN"
```

Rotation means creating a replacement token, updating the client secret store, then revoking the previous token. Plaintext tokens cannot be retrieved again.

## Scopes

| Scope | Routes / capability |
|---|---|
| `chat:use` | Base chat and configured specialist chat |
| `tasks:read` | Evidence, proof, proof bundle, source context and file snapshots |
| `tasks:write` | Compile/inspect tasks, create patches, probes, quality checks |
| `workspace:read` | Repository graph/search and task workspace reads |
| `workspace:write` | Patch proposal/application APIs, in addition to task-level approval |
| `workspace:execute` | Isolated test/oracle execution, in addition to task-level confirmation |
| `brain:read` | Brain status, search, graph and note reads |
| `brain:write` | Brain index refresh and note creation |

API scopes do not replace the task contract's own approval list or explicit patch/test confirmation. For example, workspace:execute on the API token does not grant task-level run_tests.

## Tenant data layout

Every non-default tenant receives its own workspace root under UNAI_TENANT_WORKSPACE_ROOT/{tenant_id}, Brain Markdown vault and SQLite index, evidence/task/patch ledger, and incremental Python code-graph SQLite cache. Tenant IDs are restricted lowercase identifiers; default is reserved. Databases are separate files, not merely tables filtered by a request parameter. Task IDs and note slugs are looked up only in the authenticated tenant store.

Tokens sharing one tenant intentionally share that tenant's data; assign a unique tenant ID where users must not see one another's records. The token registry is separate at UNAI_API_TOKEN_STORE_PATH and stores digests, labels, scopes, timestamps and revocation state. The rate limiter stores hashed source-IP identifiers and principal buckets. UNAI_API_RATE_LIMIT_PER_MINUTE applies per token and per source IP.

## Hosted Brain Worker

Cloudflare D1 migration 0002_tenant_tokens.sql adds a tenant key to notes, links and tags and creates token/rate-limit tables. Existing rows remain in the reserved default tenant. All API routes require bearer authentication; unset read secrets no longer mean public-read. Configure BRAIN_ADMIN_TOKEN before deployment and use it only from a trusted terminal to issue/revoke scoped credentials. Optional BRAIN_READ_TOKEN and BRAIN_WRITE_TOKEN are read-only/write-only credentials for the default tenant. Give the browser only a dedicated scoped token. Remote Brain MCP requires HTTPS and an explicit UNAI_BRAIN_API_TOKEN.

## Local and hosted transport

- The local Brain MCP service is stdio-only.
- The remote Brain MCP bridge refuses non-loopback HTTP and requires HTTPS plus a token for remote hosts.
- The task MCP server remains read-only and uses its process owner's fixed workspace; task mutation/execution still goes through scoped Engine HTTP routes.

## Not yet a release certification

This milestone adds application-level tenant separation and credential lifecycle controls. It does not certify a public multi-user deployment. Host firewall/TLS termination, D1 account permissions, secret rotation/backup procedures, disk encryption, resource quotas beyond request rate limiting, cross-process tenant cache management, dependency/supply-chain review, fuzzing/penetration testing, and Docker sandbox hardening remain review/validation tasks. Do not expose an unreviewed public deployment. Physical Android installation and device resource/performance testing remain later milestones.

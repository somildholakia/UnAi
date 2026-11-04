# Security policy

unAI is an experimental local-first agent. Report vulnerabilities privately to the repository owner. Do not include credentials, private source code, or personal data in public issues.

## Current controls

- The Engine requires a valid scoped bearer token, the installation administrator token, or a loopback-local identity. With UNAI_API_TOKEN unset, non-loopback access is denied unless a scoped token is presented.
- Scoped tokens are high-entropy opaque secrets; only SHA-256 hashes are stored. Tokens carry a tenant ID, explicit scopes, expiry (1–365 days), and revocation state. Token issuance and revocation are installation-admin-only.
- Engine requests are rate-limited independently by token principal and source IP. Scope checks apply to chat, task, workspace execution/write, and Brain routes.
- Each non-default tenant gets physically separate workspace files, Brain Markdown/index, evidence ledger, and repository graph cache. A token for one tenant cannot resolve another tenant's task IDs or notes.
- The hosted Brain Worker fails closed when credentials are absent. D1 notes, links, and tags are tenant-keyed; scoped tokens are hash-stored, revocable, expiring, and rate-limited.
- The local read-only Brain MCP server uses stdio; remote Brain MCP requires HTTPS and an explicit token.

Setup and scope details: docs/SECURITY.md and docs/BRAIN.md.

## Remaining trust boundaries

- A tenant is the isolation boundary. Tokens deliberately share data when assigned the same tenant ID. Issue a unique tenant ID per person/workspace when separate ownership is required.
- Loopback-local access is installation-admin access to the reserved default tenant. Protect local processes and do not expose the local server port beyond trusted peers.
- A configured installation administrator token can create other scoped tokens. Keep it server-side; never embed it in browser assets or send it to models.
- The Cloudflare Worker is a scaffold, not a deployed or penetration-tested service. Restrict CORS origins and secure D1/admin secrets before deployment.
- Host-level network policy, disk encryption, backup retention, dependency review, Docker sandbox hardening, and Android whole-process lifecycle tests remain deployment acceptance work.

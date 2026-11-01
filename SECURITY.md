# Security policy

unAI is an experimental agent. Report security issues privately to the repository owner. Do not include API keys, credentials, private source code, or personal data in public bug reports.

## Engine API access

- With `UNAI_API_TOKEN` set, every HTTP request except CORS preflight requires the matching `Authorization: Bearer <token>` header.
- Use a randomly generated secret of at least 32 characters. Do not commit it, expose it in browser bundles, or put it in logs.
- With `UNAI_API_TOKEN` unset, the Engine accepts loopback clients only and rejects non-loopback peer addresses. Forwarded-IP headers are not trusted.
- CORS preflight requests are allowed through so browser clients can negotiate the Authorization header; the actual request still requires authentication.
- `UNAI_BRAIN_WRITE_TOKEN` remains a separate optional write credential for Brain note creation. Send it with the `X-Brain-Write-Token` header; remote writes require both it and the Engine's `Authorization: Bearer` token.

This is an installation-wide shared bearer secret, not per-user identity or scoped API keys. There is no tenant isolation, rate limiting, token rotation/revocation registry, or production deployment threat model yet. Keep the Engine private and do not expose it directly to public traffic.

## Remaining prototype limits

Task-level workspace approvals and separate patch/test confirmations remain mandatory, but the API token does not replace those controls. The Docker test image and sandbox still require deployment-specific review and hardening.

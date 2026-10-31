# Development checkpoint

**Checkpoint:** M1 — Obsidian-style Brain and fine-tuning foundation
**Date:** 2026-11-01
**Repository:** https://github.com/somildholakia/UnAi (main)

## Implemented and published

- Modular local-first Engine with model adapter, typed task-state primitives and cognition/memory/index/search/verification plugin interfaces.
- Obsidian-compatible Markdown Brain with YAML frontmatter, tags, aliases, wikilinks, headings, backlinks and unresolved-link detection.
- SQLite FTS and graph index is rebuildable; Markdown remains the canonical portable source.
- Local chat automatically retrieves up to four relevant Brain excerpts and labels them as untrusted reference data.
- Local and remote read-only MCP bridges for model access.
- Local Brain FastAPI endpoints with loopback-only tokenless note writes; remote write calls require a server-side token.
- Cloudflare Worker + D1 API and static browser graph explorer can be deployed together as one site.
- Optional read and write secrets protect remote access; the web UI keeps the read token only in session storage.
- Cloudflare Worker supports a public-read demo mode when no read token is configured. Do not put private notes there without a read token or Cloudflare Access.
- Fine-tuning scaffold: consent/license/provenance/verification-gated data preparation, LoRA/QLoRA SFT and DPO preference training.
- GitHub Actions workflows for engine tests and Cloudflare Worker/UI checks.

## Verification

- GitHub Engine CI: **28 tests passed**.
- GitHub Python compileall step: passed.
- Local static UI script syntax was checked with Node earlier.
- Cloudflare Worker TypeScript/UI CI: added; latest run must be inspected before marking the cloud bundle verified.

## Not yet verified / not yet done

- No Cloudflare D1 database, secret, account binding or live URL has been configured. The D1 ID in wrangler.toml is still a placeholder.
- No trained adapter/model weights exist. Fine-tuning scripts are a scaffold; no dataset has been supplied or training job run.
- No physical 6 GB Android RAM, latency, energy or sustained-thermal measurements have been completed.
- No claim of Claude-level equivalence or frontier-level capability is made.

## Deployment

From apps/brain-cloud:

    npm install
    npx wrangler d1 create unai-brain

Replace database_id in wrangler.toml with the returned ID, then:

    npx wrangler d1 migrations apply unai-brain --remote
    npx wrangler secret put BRAIN_READ_TOKEN
    npx wrangler secret put BRAIN_WRITE_TOKEN
    npm run deploy

The Worker serves the static UI and API from the same URL. A read token is optional for a public demo but recommended for private notes. The write token is required for note creation.

## Next milestone

1. Complete Worker/UI CI and deploy the Brain to a real D1 database with read access protected.
2. Connect the selected model through remote MCP using the read token.
3. Add typed task compiler, persistent code graph and an evidence-producing execution loop.
4. Build a curated, consented and licensed coding-trajectory dataset; run LoRA/QLoRA on appropriate GPU compute, then DPO only if preference labels are evidence-backed.
5. Compare base model, adapters and full unAI system on held-out coding tasks under matched budgets, then test quantized inference on physical Android hardware.

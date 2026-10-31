# Development checkpoint

**Checkpoint:** M1 foundation — Brain memory and training scaffold
**Date:** 2026-11-01
**Repository:** https://github.com/somildholakia/UnAi (main)

## Implemented and published

- Modular Engine with model adapter, typed task-state primitives and plugin contracts.
- Obsidian-compatible Markdown Brain with YAML metadata, wikilinks, tags, aliases, backlinks, unresolved references and graph/search indexing.
- SQLite FTS index is rebuildable; Markdown remains the canonical data.
- Local chat retrieves up to four bounded Brain excerpts into model context; notes are labelled untrusted reference data.
- Read-only local and remote MCP bridges.
- Brain API with local loopback write boundary and server-side write-token option.
- Packaging declares PyYAML and optional MCP/training dependency groups.
- Initial Cloudflare Worker/D1 and static graph UI scaffolds are being prepared separately from the local Engine.
- Training harness is separated from inference: verified dataset preparation, LoRA/QLoRA SFT and DPO preference stage.

## Local verification

- Engine test suite: 28 passed.
- Python compileall for engine and training: passed.
- Static Brain UI JavaScript syntax check: passed.

## Not yet verified

- Cloudflare account/database deployment or production URL.
- Worker typecheck/build against a real D1 binding.
- Browser UI visual/functional integration test.
- MCP handshake in a live compatible client.
- Fine-tuning execution or trained adapter weights.
- 6 GB Android RAM, latency, energy or thermal benchmark.

## Next milestone

1. Finish and deploy the read-only hosted Brain with Access controls for private data.
2. Connect an actual model to the API using MCP or registered HTTP tools.
3. Add a typed task compiler, persistent repository/symbol graph and evidence-producing execution loop.
4. Collect only consented, licensed and verified trajectories; train a small specialist LoRA adapter on suitable GPU compute.
5. Compare base model, adapter and tool-augmented system on held-out tasks with matched budgets.

# unAI roadmap

## M0 — Modular foundation
- Local-first FastAPI engine and browser shell.
- Provider adapter, task state, plugin contracts and safe execution boundary.
- Initial tests and architecture/research documentation.

## M1 — Brain and training foundation
- Obsidian-compatible Markdown vault, backlinks, graph and low-memory search.
- Local chat retrieval plus MCP read tools.
- Cloudflare Worker/D1 scaffold and static graph explorer.
- Verified data preparation, LoRA/QLoRA SFT and DPO harness.

## M2 — Task compiler and code graph
- [x] Bounded Python AST graph for file/symbol/import/test relationships with source ranges.
- [x] Parse goals into typed acceptance checks, constraints and approval-aware permissions.
- [ ] Persist file/symbol/import/test graph with source-range references.
- [ ] Retrieve bounded, task-specific code slices rather than whole repositories.

## M3 — Evidence-producing agent loop
- [x] Installation-wide bearer-token gate; tokenless access is restricted to loopback peers.
- [ ] Per-user/scoped API credentials, revocation and tenant isolation.
- [x] Read-only inspection workflow with explicit workspace-read approval and append-only evidence snapshots.
- [x] Separately confirmed fixed pytest runner in Docker, using a filtered temporary copy and appended execution evidence.
- [x] Task-scoped source snapshots and SHA-256-bound, one-file patch proposals with a separate apply confirmation.
- [x] Evidence summary that requires a passing test event newer than the latest applied patch.
- [x] Post-patch Python parse-quality refresh; the proof summary treats pre-patch quality events as stale.
- [ ] Typed model-tool/client bridge; HTTP workflow endpoints currently require an external caller.
- [x] Isolated temporary Docker test workspace for fixed Python pytest invocation.
- [x] Append-only patch and test evidence with explicit approval gates; arbitrary commands and multi-file patch application remain disabled.
- [x] Explicit read/write/test permissions and a second confirmation for patch apply/test run.

## M4 — Adversarial verification
- [x] Conservative Python AST boundary-probe candidate generation for simple branch conditions (unverified only).
- [ ] Turn unverified AST boundary candidates into grounded tests and targeted counterexamples.
- Verify claims against execution traces and independent checks.
- Return proof bundles with unresolved risks, never just a confidence score.

## M5 — Specialist training
- Collect consented, licensed and verified trajectories.
- Train separate next-action, planner and verifier adapters.
- Compare base, adapter and tool-augmented systems on held-out tasks.
- Export only adapters that improve verified outcomes without exceeding mobile budgets.

## M6 — Mobile and deployment validation
- Benchmark on physical 6 GB Android hardware.
- Record peak PSS/RSS, load time, TTFT, tokens/sec, power and sustained thermals.
- Deploy optional hosted Brain services with authentication, quotas and tenant isolation.
- Keep local/offline inference functional without hosted model credits.

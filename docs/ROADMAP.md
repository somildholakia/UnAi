# unAI roadmap

## M0 — Modular foundation
- [x] Local-first FastAPI engine and browser shell.
- [x] Provider adapter, task state, plugin contracts and safe execution boundary.
- [x] Initial tests and architecture/research documentation.

## M1 — Brain and training foundation
- [x] Obsidian-compatible Markdown vault, backlinks, graph and low-memory search.
- [x] Local chat retrieval plus read-only local/remote MCP tools.
- [x] Cloudflare Worker/D1 scaffold and static graph explorer.
- [x] Verified data preparation, LoRA/QLoRA SFT and DPO harness (pipeline implemented; no weights trained).

## M2 — Task compiler and code graph
- [x] Bounded Python AST graph for file/symbol/import/test relationships with source ranges.
- [x] Parse goals into typed acceptance checks, constraints and approval-aware permissions.
- [ ] Persist file/symbol/import/test graph with source-range references.
- [ ] Retrieve bounded, task-specific code slices rather than whole repositories.

## M3 — Evidence-producing agent loop
- [x] Installation-wide bearer-token gate; tokenless access is restricted to loopback peers.
- [ ] Per-user/scoped API credentials, revocation, rate limiting and tenant isolation.
- [x] Read-only inspection workflow with explicit workspace-read approval and append-only evidence snapshots.
- [x] Separately confirmed fixed pytest runner in Docker, using a filtered temporary copy and appended execution evidence.
- [x] Task-scoped source snapshots and SHA-256-bound, one-file patch proposals with a separate apply confirmation.
- [x] Evidence summary that requires a passing test event newer than the latest applied patch.
- [x] Post-patch Python parse-quality refresh; the proof summary treats pre-patch quality results as stale.
- [x] Typed Engine client and local read-only task MCP bridge for compilation, configured inspection, file snapshots, boundary probes, evidence and proof bundles.
- [x] Isolated temporary Docker test workspace for fixed Python pytest invocation.
- [x] Append-only patch and test evidence with explicit approval gates; arbitrary commands and multi-file patch application remain disabled.
- [x] Explicit read/write/test permissions and a second confirmation for patch apply/test run.

## M4 — Adversarial verification (grounded first milestone delivered)
- [x] Conservative Python AST boundary-probe candidate generation for simple branch conditions (unverified only).
- [x] Attach explicit user/specification expected outputs to candidate probes without inferring expected values.
- [x] Bind grounded cases to the exact source SHA-256, selected file/function and probe-candidate provenance.
- [x] Run fixed generated pytest cases in the disposable Docker staging copy after workspace_read, run_tests and a separate exact confirmation.
- [x] Record output mismatches as source-hash-bound counterexample evidence.
- [x] Preserve bounded execution traces, output fingerprints and unresolved assumptions in proof bundles.
- [x] Expose proof-bundle retrieval through the typed Engine client and read-only MCP bridge.
- [x] Allow specification cases to bind an optional workspace Markdown/text artifact to a SHA-256 fingerprint and verify its bytes before execution.
- [ ] Independently authenticate the external publisher/author or a human-review attestation; a hash proves content integrity, not trust or correctness.
- [x] Add fixed, non-executable property-oracle contracts and opt-in, Docker-isolated integer shrinking capped at 24 candidates per request; report smallest observed failures without claiming global minimality.
- [ ] Extend shrinking to multiple-argument cumulative reduction, floats, strings and collections only with separate correctness/resource policies; add broader trace/discriminating analysis.
- [x] Extend grounded execution to undecorated module-level async Python functions using a fixed asyncio.run harness.
- [ ] Extend grounded tests to class methods, decorated functions and broader Python signatures with explicit invocation rules.
- [ ] Add coverage-guided and mutation-based verification only with explicit resource/sandbox policies.

## M5 — Specialist training
- [ ] Collect consented, licensed and verified trajectories.
- [ ] Train separate next-action, planner and verifier adapters.
- [ ] Compare base, adapter and tool-augmented systems on held-out coding tasks.
- [ ] Export only adapters that improve verified outcomes without exceeding mobile budgets.

## M6 — Mobile and deployment validation
- [ ] Benchmark on physical Android hardware.
- [ ] Record peak PSS/RSS, load time, TTFT, tokens/sec, power and sustained thermals.
- [ ] Deploy optional hosted Brain services with authentication, quotas and tenant isolation.
- [ ] Keep local/offline inference functional without hosted model credits.

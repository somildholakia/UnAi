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
- [x] Verify optional Ed25519 human-review attestations against an operator-configured trusted public-key ring; bind each signature to the exact task, workspace, target source, artifact, case inputs and expected output/property.
- [x] Verify optional external publisher Ed25519 artifact manifests against operator-configured issuer identities and exact HTTPS hostname allowlists; support multiple key IDs per issuer, timezone-aware issuance windows and immediate key revocation.
- [x] Add offline root-signed publisher trust-bundle snapshots with bounded size, exact-domain entries, expiry, Ed25519 verification and an operator-controlled minimum sequence.
- [ ] Add public key-transparency inclusion/consistency proofs or organizational certificate/identity discovery; the current signed snapshots are not a public transparency log and do not establish legal identity.
- [x] Add fixed, non-executable property-oracle contracts, including allowlisted expected-exception checks with exact built-in type identity, and opt-in Docker-isolated integer shrinking capped at 24 candidates per request; report smallest observed failures without claiming global minimality.
- [x] Add greedy cumulative reduction across multiple integer arguments using successive, source/specification-pinned Docker rounds; cap at 24 candidate executions and 6 rounds per request.
- [x] Add finite-float shrinking with deterministic toward-zero candidates, finite-value checks and the existing 24-candidate/6-round sandbox budgets.
- [x] Add bounded string shrinking by deterministic contiguous-chunk deletion, with strict length reduction and the existing global candidate/round budgets.
- [x] Add bounded top-level list and string-keyed dictionary shrinking by deterministic contiguous chunk deletion, preserving nested values and existing global candidate/round budgets.
- [x] Add bounded recursive shrinking inside JSON-compatible nested lists/dictionaries, with 4-level depth, 128-node traversal and 12-candidate-per-argument limits under the global 24-candidate/6-round Docker budget.
- [x] Add structured counterexample traces with canonical JSON result hashes, exception-message hashes and target source line/function, preserving bounded redacted previews.
- [x] Add opt-in per-case same-process replay stability checks for 2–4 identical JSON inputs, with fresh input copies, hash-only replay traces, a 24-invocation request budget and no automatic shrinking of replay-divergence cases.
- [x] Add an opt-in workspace_filesystem_unchanged property that compares bounded before/after manifests in the disposable Docker workspace; hashes regular-file contents and symlink targets, does not follow symlinks, and reports unavailable on entry/byte/read limits.
- [ ] Extend discriminating checks beyond the filtered workspace to explicitly managed external side effects only with dedicated fixtures, isolation and resource policies.
- [ ] Extend deeper or broader recursive reductions only with additional per-type correctness/resource policies.
- [x] Extend grounded execution to undecorated module-level async Python functions using a fixed asyncio.run harness.
- [x] Support explicitly decorated top-level-class @staticmethod and @classmethod targets with bound invocation, async support, signature validation and generated-harness tests.
- [x] Support ordinary instance methods on top-level classes with JSON-only constructor fixtures, explicit self/constructor signatures, no inheritance/metaclass/custom __new__, and the existing Docker-only runner; bind fixture hashes into reviewer attestations.
- [ ] Extend grounded tests to nested classes, other decorators and broader Python signatures only with separately reviewed construction/invocation rules.
- [ ] Add coverage-guided and mutation-based verification only with explicit resource/sandbox policies.

## M5 — Specialist training
- [x] Build consent/license/verification-gated corpus preparation with sanitized deduplication, hashed repository groups and deterministic repository-disjoint specialist train/eval splits.
- [x] Add separate next-action, planner and verifier LoRA/QLoRA training runs with provenance checks, leakage rejection and reproducible data/library/hardware manifests.
- [x] Add a matched held-out evaluator for base, specialist-adapter and tool-augmented outcomes, including canonical task-content fingerprints, unavailable-run rejection, paired regression reporting and exact two-sided McNemar significance (minimum 30 held-out cases, alpha no higher than 0.05).
- [x] Add a release gate that independently recomputes exact paired significance and sample thresholds, requires a verified success-rate gain with zero regressions, verifies evaluation input digests, and requires a matching physical-device memory/TTFT/throughput/thermal report.
- [ ] Collect a real, consented/licensed multi-repository trajectory corpus; none has been supplied to this repository.
- [ ] Train all three specialist adapters on suitable GPU hardware and retain reproducible artifacts.
- [ ] Run the held-out comparison and physical Android measurements for each adapter.
- [ ] Package/promote only adapters that pass both gates. No weights have been trained or released.

## M6 — Mobile and deployment validation
- [ ] Benchmark on physical Android hardware.
- [ ] Record peak PSS/RSS, load time, TTFT, tokens/sec, power and sustained thermals.
- [ ] Deploy optional hosted Brain services with authentication, quotas and tenant isolation.
- [ ] Keep local/offline inference functional without hosted model credits.

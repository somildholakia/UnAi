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
- [x] Persist file/symbol/import/test graph with source-range references in an incremental SQLite cache; refresh atomically and reparse only changed source fingerprints.
- [x] Add bounded conservative static `calls` and class `instantiates` edges for uniquely resolved local/direct-import targets; keep ambiguous, dynamic and shadowed targets unresolved and report call-site truncation.
- [x] Retrieve bounded, task-specific code slices only from symbols found during approved inspection, with source-hash freshness checks and strict line/character budgets.

## M3 — Evidence-producing agent loop
- [x] Installation-wide bearer-token gate; tokenless access is restricted to loopback peers.
- [x] Per-user/scoped API credentials with one-time token disclosure, hashed storage, expiry and immediate revocation.
- [x] Enforce per-token and source-IP rate limits and explicit API scopes across chat, task/evidence, patch/test, workspace and Brain routes.
- [x] Isolate each tenant's workspace, Brain vault/index, task/evidence ledger and repository graph cache; reject cross-tenant task/note access.
- [x] Require authentication for all hosted Brain API routes; tenant-filter D1 notes, links, tags, search and graph data; validate migration and rate-limit setup in CI.
- [x] Require HTTPS plus server-side tokens for remote MCP bridges; run local MCP bridges over stdio.

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
- [x] Add offline-verifiable publisher transparency primitives: operator-pinned Ed25519 signed tree heads, RFC 6962 Merkle inclusion/consistency proofs, canonical publisher-manifest leaf binding and append-only SQLite checkpoints. This verifies supplied proofs; it does not fetch/discover logs, provide witness gossip, or establish publisher legal identity.
- [x] Integrate publisher-manifest transparency proofs into publisher-oracle acceptance/evidence: signed tree-head verification, exact manifest inclusion, monotonic per-log consistency checkpoints, operator-pinned log-key lifecycle, independent witness signatures/quorum, and provenance retained in append-only evidence. Regression coverage includes tampered inclusion, cross-log key substitution and checkpoint rollback. Organizational certificate/identity discovery remains separate future work.
- [x] Add fixed, non-executable property-oracle contracts, including allowlisted expected-exception checks with exact built-in type identity, and opt-in Docker-isolated integer shrinking capped at 24 candidates per request; report smallest observed failures without claiming global minimality.
- [x] Add greedy cumulative reduction across multiple integer arguments using successive, source/specification-pinned Docker rounds; cap at 24 candidate executions and 6 rounds per request.
- [x] Add finite-float shrinking with deterministic toward-zero candidates, finite-value checks and the existing 24-candidate/6-round sandbox budgets.
- [x] Add bounded string shrinking by deterministic contiguous-chunk deletion, with strict length reduction and the existing global candidate/round budgets.
- [x] Add bounded top-level list and string-keyed dictionary shrinking by deterministic contiguous chunk deletion, preserving nested values and existing global candidate/round budgets.
- [x] Add bounded recursive shrinking inside JSON-compatible nested lists/dictionaries, with 4-level depth, 128-node traversal and 12-candidate-per-argument limits under the global 24-candidate/6-round Docker budget.
- [x] Add structured counterexample traces with canonical JSON result hashes, exception-message hashes and target source line/function, preserving bounded redacted previews.
- [x] Add opt-in per-case same-process replay stability checks for 2–4 identical JSON inputs, with fresh input copies, hash-only replay traces, a 24-invocation request budget and no automatic shrinking of replay-divergence cases.
- [x] Add an opt-in workspace_filesystem_unchanged property that compares bounded before/after manifests in the disposable Docker workspace; hashes regular-file contents and symlink targets, does not follow symlinks, and reports unavailable on entry/byte/read limits.
- [x] Add opt-in `external_fixture_filesystem_unchanged` verification for a dedicated 16 MiB Docker tmpfs at `/unai_side_effects` (`noexec`, `nosuid`, `nodev`, no network, no host-backed volume); hash every entry in the fixture without following symlinks, with 1,024-entry, 2 MiB/file and 16 MiB/request audit limits, and report unavailable rather than passing when the fixture or budget is unavailable. This covers only the managed filesystem fixture, not databases, queues or remote services.
- [x] Extend recursive reductions to eight nested JSON container levels with a 256-node traversal ceiling; preserve per-type deterministic reducers, 12 candidates per argument, 24 candidate executions and 6 isolated rounds, with regression tests for depth boundaries, node ceilings, determinism, immutability and JSON type safety.
- [x] Extend grounded execution to undecorated module-level async Python functions using a fixed asyncio.run harness.
- [x] Support explicitly decorated top-level-class @staticmethod and @classmethod targets with bound invocation, async support, signature validation and generated-harness tests.
- [x] Support ordinary instance methods on top-level classes with JSON-only constructor fixtures, explicit self/constructor signatures, no inheritance/metaclass/custom __new__, and the existing Docker-only runner; bind fixture hashes into reviewer attestations.
- [x] Support one explicit nested class namespace level (`Outer.Inner.method`) for static, class and ordinary instance methods when each class in the chain is directly declared, undecorated and has no bases or metaclass keywords; instance methods retain hash-bound fresh constructor fixtures.
- [x] Add bounded `typing.final` identity-marker support with strict import binding, decorator-order and built-in shadow checks; add positional-only, `*args` and `**kwargs` signature binding for functions, methods and instance constructors with JSON-only argument containers, fixed generated invocation and explicit rejection for unsafe shrink/probe combinations.
- [x] Add bounded mutation-based test-strength verification: deterministic single-token mutants inside the selected target body, a required passing grounded baseline, at most 8 mutants and 9 disposable Docker rounds, per-mutant and summary evidence, and proof-bundle exposure of survivors. A mutation score is test-strength evidence for those mutants, not coverage or proof of correctness.
- [x] Add opt-in, source-hash-bound coverage measurement for supplied grounded oracle cases: bounded AST statement/branch-arm planning, exact-target line tracing, strict trace validation, append-only coverage evidence and proof-bundle risks; keep oracle verdicts independent from coverage availability. This audits the supplied cases only and does not automatically generate tests or fuzz.
- [x] Add read-only coverage-guided ranking for existing unverified AST probes: bind probe and complete coverage evidence to the same task/file/symbol/source hash and matching execution event, safely predict supported if/else arms, prioritize uncovered candidates, and append passed=null evidence without running target code or inferring expected outputs.

**M4 decorator/signature expansion validation (2026-11-05):** validated on `38c2b0b697f334d754ebf8fd573a4e7b6da0da09`; Engine/Web run #363 passed 400 tests in 20.88s, built the disposable Docker sandbox image and exercised a generated harness through `DockerSandbox`, plus compileall, six shell syntax checks and Vite production build. Brain Worker run #46 passed typecheck, D1 migration smoke test and browser JavaScript syntax. See `docs/CHECKPOINT.md` and `docs/VERIFICATION.md` for supported invocation rules and limits.

**M4 mutation-verification validation (2026-11-05):** validated on `81ed3576ab9c64b464c2d9d2bce3c518e9967fbc`; Engine/Web run #366 passed 421 tests in 20.42s, built the disposable Docker sandbox image and executed the Docker-backed mutation integration test, plus compileall, six shell syntax checks and Vite production build. Brain Worker run 41999448612 passed typecheck, D1 migration smoke test and browser JavaScript syntax. The initial run #365 failed one new Docker fixture assertion (420 passed, 1 failed) because its cases did not exercise the first two planned mutants; the corrected fixture passed on the validated commit. The subsequent bounded coverage-measurement increment is recorded immediately below.
**M4 bounded coverage-measurement validation (2026-11-05):** validated code/test HEAD `e6af02a48fa03ace04627b9910c39f6c2bd57233`; Engine/Web Actions run #372 passed **433 tests in 17.19s**, including the real Docker-backed coverage integration test; Python compileall, all six Termux shell syntax checks and the Web production build passed. Brain Worker run #55 passed Worker typecheck, D1 migration validation and browser JavaScript syntax. The trace writer now persists after each measured invocation, and Docker runs as the staging process UID/GID. See `docs/VERIFICATION.md` for trace/AST limits and the boundary between case coverage and automatic coverage-guided fuzzing. The complete docs-updated branch was revalidated on Engine/Web run #378 (433 tests passed in 23.74s; Docker image, compileall, shell syntax and Web build passed) and Brain Worker run #61 (all checks passed).
**M4 coverage-guided probe-ranking validation (2026-11-05):** validated code/test HEAD `4920622429f31dbed780f325efcbcb7f2bfdeae9`; Engine/Web Actions run #395 passed **442 tests in 18.82s**, including the new planner/API/evidence-boundary tests; Docker sandbox build, Python compileall, all six Termux shell syntax checks and the Web production build passed. Brain Worker run #78 passed Worker typecheck, D1 migration validation and browser JavaScript syntax. The first attempt (#391) found a syntax error in newly added API test fixture escaping; it was corrected before the passing run. See `docs/VERIFICATION.md` for exact evidence and no-execution limits.

## M5 — Specialist training
- [x] Add a read-only, metadata-only corpus preflight with a 2 GiB input ceiling, bounded JSONL record handling and no raw conversation/repository/provenance values in its report.
- [x] Expand credential sanitization for provider tokens, cloud access-key IDs, bearer/JWT forms, database URLs, PEM private keys and signed URL signatures, with regression tests.
- [x] Build consent/license/verification-gated corpus preparation with sanitized deduplication, hashed repository groups and deterministic repository-disjoint specialist train/eval splits.
- [x] Record the raw corpus input SHA-256/byte count and preflight all specialist splits before writing outputs, preserving previous artifacts on split-validation failures.
- [x] Add separate next-action, planner and verifier LoRA/QLoRA training runs with provenance checks, leakage rejection and reproducible data/library/hardware manifests.
- [x] Verify the prepared corpus manifest, split filenames/hashes/counts, repository disjointness and canonical record IDs before model loading.
- [x] Hash and parse the same train/eval byte snapshots so manifest verification covers the exact in-memory rows consumed by training.
- [x] Add a matched held-out evaluator for base, specialist-adapter and tool-augmented outcomes, requiring the exact prompt/acceptance contract and recomputing its canonical SHA-256, rejecting unavailable runs, reporting paired regressions and exact two-sided McNemar significance (minimum 30 held-out cases, alpha no higher than 0.05).
- [x] Add a release gate that independently recomputes exact paired significance and sample thresholds, requires a verified success-rate gain with zero regressions, verifies evaluation input digests, and requires a matching physical-device memory/TTFT/throughput/thermal report.
- [x] Add server-configured OpenAI-compatible runtime routing for the three named specialists; unavailable roles fail closed instead of falling back to the base model.
- [ ] Collect a real, consented/licensed multi-repository trajectory corpus; none has been supplied to this repository.
- [ ] Train all three specialist adapters on suitable GPU hardware and retain reproducible artifacts.
- [ ] Run the held-out comparison and physical Android measurements for each adapter.
- [ ] Package/promote only adapters that pass both gates. No weights have been trained or released.

## M6 — Mobile and deployment validation
- [ ] Benchmark on physical Android hardware.
- [ ] Record peak PSS/RSS, load time, TTFT, tokens/sec, power and sustained thermals.
- [x] Implement authenticated, tenant-scoped hosted Brain Worker routes, expiring/revocable hashed tokens, per-token/IP request limits and a D1 tenant migration (CI-validated; not deployed).
- [ ] Provision and deploy the optional hosted Brain service; configure D1, administrator secrets, scoped browser/MCP tokens, production CORS origin, backups and operational quotas.

- [ ] Keep local/offline inference functional without hosted model credits.


## M6 — local packaging checkpoint

- [x] Add a repeatable Android/Termux install script for a CPU `llama.cpp` runtime, local GGUF model, Engine venv and Web UI dependencies.
- [x] Add a single tmux-based local launcher with model/Engine/UI windows bound to loopback.
- [x] Add CI checks for Python, web production build and shell syntax.
- [x] Add a Termux doctor for prerequisite/model-file checks, model/Engine/Web health probes and an optional direct local-inference smoke test; make startup wait for and diagnose all three services.
- [x] Add bounded non-zero-exit supervision for model/Engine/Web processes, exponential retry backoff, persistent rotated logs, tmux pane retention and doctor log tails; Android whole-app process recovery remains unverified.
- [x] Add safe `status`/`recover` controls that check health before respawning dead/missing tmux panes, leave healthy endpoints and live supervisors untouched, and refuse full-stack launch when any listener may still be alive.
- [x] Forward shutdown signals to active service child processes; test exact model/Engine/Web launch arguments and working directories in CI.
- [ ] Verify install and offline chat on a physical Android device; record RSS/PSS, TTFT, throughput and thermal behavior.
- [ ] Validate memory, process lifecycle and recovery after Android background/low-memory kills.
# Development checkpoint

**Checkpoint:** M4 — property-oracle shrinking delivered; broader verification remains in progress
**Date:** 2026-11-01
**Repository:** https://github.com/somildholakia/UnAi
**Branch:** main
**M4 grounded-verification implementation PR:** https://github.com/somildholakia/UnAi/pull/2 (merged)
**M4 first-milestone merge commit:** 1c0e82d1bb2a75789170278e4e31c81f68d74297
**M4 async extension PR:** https://github.com/somildholakia/UnAi/pull/3 (merged)
**M4 async extension merge commit:** 6a95421d171433fe52d6f54722dc8924e33f3c66
**M4 pinned-specification artifact PR:** https://github.com/somildholakia/UnAi/pull/4 (merged)
**M4 pinned-specification artifact merge commit:** b4ce3fae5030c95bbc0ff4b509d37626d89707ff
**M4 property-oracle and shrinking PR:** https://github.com/somildholakia/UnAi/pull/5 (merged)
**M4 property-oracle and shrinking merge commit:** 0b2f0736bb9162af32e1e77c09e4ef2536989913
**M4 expected-exception oracle PR:** https://github.com/somildholakia/UnAi/pull/6 (merged)
**M4 expected-exception oracle merge commit:** c52f2e33e97e43cd54b2d85d60b14cd861293b90
**M4 cumulative multi-argument shrinking PR:** https://github.com/somildholakia/UnAi/pull/7 (merged)
**M4 cumulative multi-argument shrinking merge commit:** 8c2ff696c33efd905374e6c11527465a5deff111
**M4 cumulative multi-argument shrinking PR:** https://github.com/somildholakia/UnAi/pull/7 (merged)
**M4 cumulative multi-argument shrinking merge commit:** 8c2ff696c33efd905374e6c11527465a5deff111
**M3.2 implementation PR:** https://github.com/somildholakia/UnAi/pull/1 (merged)
**M3.2 code merge commit:** 4b83a48927939b7b8fb2ea811f7d26f7aa6313aa

## What is implemented

### M0 — modular foundation

- Local-first FastAPI engine, provider adapter, browser shell, task-state primitives and plugin contracts.
- CPU-first architecture target; model/provider backends are replaceable.
- Docker-only execution boundary exists; no host-shell fallback is intended.

### M1 — Brain and training foundation

- Obsidian-compatible Markdown Brain with YAML frontmatter, tags, aliases, wikilinks, headings, backlinks and unresolved-link detection.
- Markdown notes are canonical; SQLite FTS/graph index is rebuildable.
- Local chat retrieves up to four relevant Brain excerpts into context as untrusted reference material.
- Local Brain API and read-only local/remote MCP bridges.
- Cloudflare Worker + D1 API and static graph explorer are present as deployment scaffolding.
- Training scripts cover provenance/license/consent-gated SFT and preference-data preparation, LoRA/QLoRA SFT and DPO.
- No actual adapter/weights have been trained and no user dataset has been supplied.

### M2 — task compiler and repository graph (partial)

- Deterministic task compiler returns a provisional typed contract with objective/category, constraints, assumptions, unknowns, generic acceptance checks, plan steps and permission accounting.
- Bounded Python AST graph discovers files, classes, functions, methods, imports and heuristic test-to-source links, with source ranges and parse diagnostics.
- The graph is rebuilt on demand; persistent/incremental graph storage and richer task-specific code slicing are not complete.
- Task classification and acceptance checks are deterministic heuristics, not semantic guarantees.

### M3 — evidence-producing workflow (implemented prototype)

- POST /api/tasks/inspect compiles a task and indexes a selected workspace only after explicit workspace_read approval.
- An append-only SQLite evidence ledger stores inspection, discovery, quality, patch and execution events. Evidence rows have update/delete guards; this is an audit aid, not a cryptographically tamper-proof log.
- GET /api/tasks/{task_id}/evidence retrieves the task snapshot.
- GET /api/tasks/{task_id}/files/{file_path} returns eligible UTF-8 source and SHA-256 after read approval.
- POST /api/tasks/{task_id}/patch-proposals stores a one-file replacement proposal and diff without modifying the workspace.
- Applying requires explicit workspace_write permission and a separate {"confirm":"apply-patch"} request. The engine checks the live base hash, validates the stored proposed-content hash, rejects symlink/sensitive targets and applies via same-directory temporary file + atomic rename.
- POST /api/tasks/{task_id}/run-tests requires explicit workspace_read and run_tests permissions plus a separate {"confirm":"run-python-tests"} request.
- Tests run inside Docker using a filtered temporary copy, with no network, read-only container root, dropped capabilities and resource/time limits. The original project directory is not mounted. The supplied image includes pytest but not arbitrary project dependencies.
- POST /api/tasks/{task_id}/quality-check refreshes bounded Python parse-quality evidence after a change.
- GET /api/tasks/{task_id}/proof distinguishes pending patches, stale tests, failed/unavailable tests and test/quality evidence freshness. It does not certify semantic correctness.
- The client in unai_engine.client.EngineClient provides typed methods for the workflow and requires user_confirmed=True before patch application or test execution.

### M3.1 — Engine API perimeter authentication

- The Engine accepts an installation-wide bearer token through UNAI_API_TOKEN; when configured, every request must carry Authorization: Bearer <token>.
- With no token configured, the middleware accepts loopback peers only and denies non-loopback peer addresses. It deliberately does not trust forwarded-IP headers.
- CORS permits the Authorization header; preflight is allowed, while the actual request remains authenticated.
- The token must be at least 32 characters and should be generated with a cryptographically secure random generator.
- This is a shared installation credential, not per-user identity, scoped tokens, tenant isolation, revocation, or rate limiting. Keep public deployment disabled pending a full threat model.
- The Brain write token remains separate; remote Brain note creation still requires UNAI_BRAIN_WRITE_TOKEN.

### M3.2 — read-only task workflow MCP bridge

- unai_engine.task_mcp_server exposes local stdio MCP tools for task compilation, approved workspace inspection, source snapshots, unexecuted boundary probes, evidence and proof.
- unai-task-mcp is the installed command entry point. The server uses the typed EngineClient and keeps Engine API credentials in its process environment.
- Workspace inspection is disabled by default. When enabled, the process owner must configure one fixed UNAI_MCP_WORKSPACE and set UNAI_MCP_ALLOW_WORKSPACE_READ=true.
- The model cannot choose a different workspace or self-grant workspace_read; that permission is added only by process configuration.
- The MCP server intentionally does not expose patch proposals/application, test execution, shell access or permission escalation. Those remain host-mediated actions through the Engine API with separate approval.
- The bridge is local tooling; it does not make a website or model automatically connected. An MCP-compatible host must launch/configure the server.


### M4 — grounded adversarial verification (first milestone implemented)

- The AST probe generator still produces unverified candidates; it never infers expected outputs.
- POST /api/tasks/{task_id}/grounded-tests accepts explicit user- or specification-supplied expected JSON outputs, a human-readable oracle reference, exact source SHA-256, and optional probe candidate IDs.
- A case linked to a probe must preserve that candidate's parameter assignments and exact JSON types. The selected synchronous or async, undecorated, module-level Python function's signature is inspected; unknown/missing arguments are rejected.
- The target is re-read and its source fingerprint is checked immediately before test generation. A stale source/probe returns a conflict and does not execute.
- Caller data is JSON-serialized into a fixed generated pytest harness. The generated test is added only to the disposable filtered workspace under unai_generated_tests; it is never written to the original workspace. Per-value and aggregate oracle payload sizes are bounded.
- Execution requires both workspace_read and run_tests permissions already approved on the task, as well as the separate exact run-grounded-oracle-tests confirmation. Only the fixed python -m pytest -q command runs in the Docker sandbox; no model-supplied shell command is accepted.
- The verifier records oracle provenance, source and generated-test hashes, execution status, output hashes and bounded redacted traces. It does not store the raw oracle arguments/expected values in the ledger.
- Specification cases may optionally pin a workspace Markdown/text artifact to a SHA-256 fingerprint. The verifier re-reads it before generating tests, checks it again before staging, and the generated harness verifies the copied artifact (and target source) before importing the target. This confirms document-byte integrity in the sandboxed copy, not authorship, publisher authority or truth.
- Property-oracle cases may use one fixed non-executable predicate instead of an exact expected output. Supported predicates include allowlisted expected-exception types: the exact selected built-in exception class must be raised; a wrong exception or a normal return is a counterexample. On a sandboxed property counterexample, unAI tries at most 24 deterministic integer-argument reductions over at most 6 separate Docker rounds per request. The smallest observed failing input becomes the base for the next round, allowing cumulative reduction across multiple integer arguments. Exact-output cases are never shrunk; the result is not a global-minimality proof.
- Return mismatches and unexpected function exceptions that emit the generated case marker are recorded as counterexample evidence tied to the exact execution evidence ID and source SHA-256. Async functions are invoked using asyncio.run inside the generated sync pytest wrapper.
- GET /api/tasks/{task_id}/proof-bundle combines task patch/test freshness, oracle events, counterexamples, traces, unverified probe candidates and unresolved risks.
- The typed Engine client and read-only task MCP bridge expose proof-bundle retrieval; the MCP server does not gain test execution, patch-apply or arbitrary-code tools.
- Proof summaries keep historical counterexamples while deriving the current overall status from the latest grounded run. A candidate is only marked verified when a completed sandbox run actually exercises its linked oracle case; an unavailable run leaves it unverified.
- Grounded tests currently support synchronous and async, undecorated, module-level Python functions with keyword-addressable parameters. Return-value oracles require JSON-compatible values; expected-exception oracles use a fixed allowlist and exact exception-type matching. Async calls use asyncio.run in the generated synchronous pytest wrapper. Methods, decorators and custom exception classes remain unsupported.
- Oracle references and property contracts remain requester-supplied. A workspace artifact hash can pin document bytes and ensure the staged document matches that pin, but does not authenticate authorship or publisher authority. A mismatch is evidence against the implementation *assuming the oracle is correct*; passing examples are not a proof of general correctness.
- Shrinking is opt-in and applies only to explicit property oracles; reductions support integers and finite floats, greedily cumulative across multiple arguments, capped at 24 candidate executions and 6 Docker rounds per request. Results are smallest-observed, not proven globally minimal; booleans, NaN/infinity, strings and collections are not reduced.

## CI verification

- M3.2 PR #1 was squash-merged to main as 4b83a48927939b7b8fb2ea811f7d26f7aa6313aa. Its PR and post-merge workflows each reported 131 tests passed.
- M3.2 PR CI: https://github.com/somildholakia/UnAi/actions/runs/41287213961
- M3.2 post-merge CI: https://github.com/somildholakia/UnAi/actions/runs/41287275378
- M4 PR #2 was squash-merged to main as 1c0e82d1bb2a75789170278e4e31c81f68d74297.
- M4 PR CI: https://github.com/somildholakia/UnAi/actions/runs/41289393795
- M4 post-merge CI: https://github.com/somildholakia/UnAi/actions/runs/41289447443
- Both M4 PR #2 workflows reported 146 tests passed; python -m compileall -q apps/engine/src training passed.
- M4 async extension PR #3 was squash-merged as 6a95421d171433fe52d6f54722dc8924e33f3c66.
- M4 async extension PR CI: https://github.com/somildholakia/UnAi/actions/runs/41290095843
- M4 async extension post-merge CI: https://github.com/somildholakia/UnAi/actions/runs/41290137057
- Both PR #3 workflows reported 149 tests passed; python -m compileall -q apps/engine/src training passed.
- M4 pinned-specification artifact PR #4 was squash-merged as b4ce3fae5030c95bbc0ff4b509d37626d89707ff.
- M4 pinned-specification artifact PR CI: https://github.com/somildholakia/UnAi/actions/runs/41290776370
- PR #4 CI reported 155 tests passed; python -m compileall -q apps/engine/src training passed.
- M4 property-oracle and shrinking PR #5 was squash-merged as 0b2f0736bb9162af32e1e77c09e4ef2536989913.
- M4 property-oracle PR CI: https://github.com/somildholakia/UnAi/actions/runs/41291943934
- M4 property-oracle post-merge CI: https://github.com/somildholakia/UnAi/actions/runs/41292043642
- Both PR #5 workflows reported 162 tests passed; python -m compileall -q apps/engine/src training passed.
- M4 expected-exception oracle PR #6 was squash-merged as c52f2e33e97e43cd54b2d85d60b14cd861293b90.
- M4 expected-exception PR CI: https://github.com/somildholakia/UnAi/actions/runs/41292712102
- M4 expected-exception post-merge CI: https://github.com/somildholakia/UnAi/actions/runs/41292788047
- Both PR #6 workflows reported 168 tests passed; python -m compileall -q apps/engine/src training passed.
- M4 cumulative multi-argument shrinking PR #7 was squash-merged as 8c2ff696c33efd905374e6c11527465a5deff111.
- M4 cumulative shrinking PR CI: https://github.com/somildholakia/UnAi/actions/runs/41293248588
- M4 cumulative shrinking post-merge CI: https://github.com/somildholakia/UnAi/actions/runs/41293313409
- Both PR #7 workflows reported 169 tests passed; python -m compileall -q apps/engine/src training passed.
- M4 finite-float shrinking PR #9 was squash-merged as db6641b0ec0f75f33c62037672fee16dac6343d1.
- M4 finite-float PR CI: https://github.com/somildholakia/UnAi/actions/runs/41293595602
- M4 finite-float post-merge CI: https://github.com/somildholakia/UnAi/actions/runs/41293647015
- Both PR #9 workflows reported 171 tests passed; python -m compileall -q apps/engine/src training passed.
- Earlier M4 CI failures were fixed before merge: generated-source injection assertion now inspects AST imports, the probe fixture is retained in the proof test, and oracle payload values are size-bounded. The async harness newline escaping was also caught and corrected before the successful PR run.

## Important limits and safety boundaries

- Engine authentication is an installation-wide shared token only. There is no per-user identity, scoped credentials, tenant isolation, token revocation or rate limiting.
- The task MCP bridge exposes read-only tools. Do not add patch-apply, test-run, shell, network or permission-grant tools without a host-mediated approval design and security tests.
- The test runner currently understands Python pytest only. Project-specific dependencies must be added deliberately to a reviewed sandbox image; it does not install packages at runtime.
- No generic shell command, arbitrary code execution, network access from the test container, multi-file patch transaction, file create/delete workflow, or automatic candidate execution is exposed through these routes.
- The patch hash check is optimistic concurrency protection, not proof against a privileged local attacker or a hostile process racing the final filesystem operation.
- The task compiler uses heuristics. Generic acceptance checks and current proof status can be incomplete or misleading if callers omit relevant requirements.
- No live Cloudflare D1/database/secrets/URL deployment has been verified.
- No model weights have been trained; the training pipeline remains an unexecuted scaffold.
- No physical Android memory, latency, energy or sustained thermal measurements have been performed. Mobile limits remain targets, not benchmark results.
- No claim of Claude-level or frontier-model equivalence is made.

## Next work after this checkpoint

1. Strengthen specification provenance beyond byte integrity: add trusted publisher signatures or explicit human-review attestations; a workspace SHA-256 pin does not identify the author or authority.
2. Extend shrinking to strings and collections only with explicit per-type reduction semantics and resource budgets; keep smallest-observed versus globally-minimal claims distinct.
3. Add execution-trace capture and independent/discriminating checks for more than JSON-returning pure functions.
4. Expand grounded tests to supported class/static methods, exception oracles and more languages only with dedicated invocation rules, execution isolation and tests.
5. Persist and incrementally refresh the repository graph; add bounded task-specific code-slice retrieval.
6. Design per-user/scoped Engine credentials, revocation, rate limits and tenant isolation before network-facing Engine deployment.
7. Deploy Brain to a real Cloudflare D1 account with read/write secrets, access controls and quotas, then connect the remote MCP bridge.
8. Curate consented/licensed verified trajectories, train specialist adapters on suitable GPU compute, and compare them against matched baselines.
9. Benchmark inference, memory, energy and thermals on physical Android hardware.

## Local setup reminders

Start the Engine from apps/engine:

    pip install -e '.[dev,mcp]'
    cp .env.example .env
    uvicorn unai_engine.main:app --host 127.0.0.1 --port 8000

Build the pytest-enabled image before enabling the isolated test endpoint:

    docker build -f Dockerfile.sandbox -t unai-python-test:latest .

To run the local task MCP server, configure UNAI_ENGINE_API_URL, UNAI_ENGINE_API_TOKEN, UNAI_MCP_WORKSPACE and UNAI_MCP_ALLOW_WORKSPACE_READ in the server process environment, then run:

    unai-task-mcp

Leave UNAI_MCP_ALLOW_WORKSPACE_READ=false unless the process owner explicitly intends to grant read access to the one configured workspace. Keep API tokens out of browser bundles and prompts.

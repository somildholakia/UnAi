# Development checkpoint

**Checkpoint:** M3.2 — read-only task workflow MCP bridge merged
**Date:** 2026-11-01
**Repository:** https://github.com/somildholakia/UnAi
**Branch:** main
**M3.2 code merge commit:** 4b83a48927939b7b8fb2ea811f7d26f7aa6313aa
**Implementation PR:** https://github.com/somildholakia/UnAi/pull/1
**Implementation commit before squash merge:** 7b10caccd780658a12619b331075da82e3e45fad

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

## CI verification

- PR #1 (Add read-only task workflow MCP bridge) passed the Engine test workflow on commit 7b10caccd780658a12619b331075da82e3e45fad.
- PR GitHub Actions run: https://github.com/somildholakia/UnAi/actions/runs/41287213961
- Post-merge GitHub Actions run: https://github.com/somildholakia/UnAi/actions/runs/41287275378
- Both runs reported 131 tests passed; python -m compileall -q apps/engine/src training passed.
- PR #1 was squash-merged to main as 4b83a48927939b7b8fb2ea811f7d26f7aa6313aa.

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

1. Design per-user/scoped Engine credentials, revocation, rate limits and tenant isolation before any network-facing Engine deployment.
2. Persist and incrementally refresh the repository graph; add bounded task-specific code-slice retrieval.
3. Turn unverified AST boundary candidates into reproducible tests only when expected outcomes come from specifications, existing tests or user-approved oracles.
4. Add trace capture, failure minimization and independent/discriminating checks; keep unknowns explicit.
5. Build a reviewed dependency-aware test image and expand safe language/test support only with dedicated tests.
6. Deploy Brain to a real Cloudflare D1 account with read/write secrets, access controls and quotas, then connect the remote MCP bridge.
7. Curate consented/licensed verified trajectories, train specialist adapters on appropriate GPU compute, and compare them against matched baselines.
8. Benchmark inference, memory, energy and thermals on physical Android hardware.

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

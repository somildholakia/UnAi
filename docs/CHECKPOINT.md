# Development checkpoint

**Checkpoint:** M3 evidence loop complete; M4 boundary-probe prototype started
**Date:** 2026-11-01
**Repository:** https://github.com/somildholakia/UnAi
**Branch:** main
**Code verification commit:** 002814b23a1668995afbace56ad30dadd381f31d

## What is implemented

### M0 — modular foundation

- Local-first FastAPI engine, provider adapter, browser shell, task-state primitives and plugin contracts.
- CPU-first architecture target; model/provider backends are replaceable.
- Docker-only execution boundary exists; no host-shell fallback is intended.

### M1 — Brain and training foundation

- Obsidian-compatible Markdown Brain with YAML frontmatter, tags, aliases, wikilinks, headings, backlinks and unresolved-link detection.
- Markdown notes are canonical; SQLite FTS/graph index is rebuildable.
- Local chat retrieves up to four relevant Brain excerpts as untrusted reference material.
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
- POST /api/tasks/{task_id}/run-tests requires explicit workspace_read and run_tests permissions plus a separate {"confirm":"run-python-tests"} request. It exposes only python -m pytest -q.
- Tests run inside Docker using a filtered temporary copy, with no network, read-only container root, dropped capabilities and resource/time limits. The original project directory is not mounted. The supplied image includes pytest but not arbitrary project dependencies.
- POST /api/tasks/{task_id}/quality-check refreshes the bounded Python parse-quality event after a change.
- GET /api/tasks/{task_id}/proof distinguishes pending patches, stale tests, failed/unavailable tests and test/quality evidence freshness. It does not certify semantic correctness.

### M4 — early boundary-probe prototype

- POST /api/tasks/{task_id}/probes reads a selected approved Python source file and produces bounded, AST-derived candidate values for simple if, while and assert branches.
- Initial patterns include numeric/string/boolean/None boundaries, truthiness representatives, literal membership and len(parameter) edges.
- Candidates retain function name, condition, source line, parameter values, rationale and source hash. They are recorded as unverified (passed=null, executed=false).
- The generator does not run probes, infer expected results, create tests, or claim bug discovery. It is a probe-input suggester, not a full adversarial verifier.

## CI verification

- Latest completed engine run for code commit 002814b23a1668995afbace56ad30dadd381f31d: 114 tests passed; python -m compileall -q apps/engine/src training passed.
- Engine CI run: https://github.com/somildholakia/UnAi/actions/runs/41237140062
- Earlier probe-focused engine run: 112 tests passed; later quality-refresh changes were covered by the 114-test run above.
- Cloudflare Worker/UI CI previously passed type generation, TypeScript check and browser JavaScript syntax validation: https://github.com/somildholakia/UnAi/actions/runs/41233718972
- Documentation-only edits after the code verification commit do not change the verified engine code. Re-run CI when the next code change is made.

## Important limits and safety boundaries

- The Engine HTTP API currently has no request authentication. Bind Uvicorn to 127.0.0.1; do not expose it directly to the internet until authentication, authorization and a deployment threat model exist.
- Workflow endpoints are HTTP APIs, not native model tool calls yet. An external client/UI must invoke them explicitly.
- The test runner currently understands Python pytest only. Project-specific dependencies must be added deliberately to a reviewed sandbox image; it does not install packages at runtime.
- No generic shell command, arbitrary code execution, network access from the test container, multi-file patch transaction, file create/delete workflow, or automatic candidate execution is exposed through these routes.
- The patch hash check is optimistic concurrency protection, not proof against a privileged local attacker or a hostile process racing the final filesystem operation.
- The task compiler uses heuristics. Generic acceptance checks and current proof status can be incomplete or misleading if callers omit relevant requirements.
- No live Cloudflare D1/database/secrets/URL deployment has been verified.
- No model weights have been trained; the training pipeline remains an unexecuted scaffold.
- No physical 6 GB Android RAM, latency, energy or sustained thermal measurements have been performed. Mobile limits remain targets, not benchmark results.
- No claim of Claude-level or frontier-model equivalence is made.

## Next work after this checkpoint

1. Add authentication and scoped authorization before any network-facing Engine deployment.
2. Add a typed client/tool adapter so the model can request inspection, file snapshots, patch proposals and proof through explicit host-mediated actions.
3. Persist and incrementally refresh the repository graph; improve bounded code-slice retrieval.
4. Turn selected probe candidates into reproducible tests only when expected outcomes are grounded in specifications, existing tests or user-approved oracles.
5. Add trace capture, failure minimization and independent/discriminating checks; keep unknowns explicit.
6. Build a reviewed dependency-aware test image and expand safe language/test support only with dedicated tests.
7. Deploy Brain to a real Cloudflare D1 account with access controls, then connect the MCP bridge.
8. Curate consented/licensed verified trajectories and train adapters on appropriate GPU compute.
9. Benchmark inference, memory, energy and thermals on physical Android hardware.

## Local setup reminders

Build the pytest-enabled image before enabling the isolated test endpoint:

    docker build -f apps/engine/Dockerfile.sandbox -t unai-python-test:latest .

The Engine defaults to UNAI_DOCKER_IMAGE=unai-python-test:latest. Configure the workspace and evidence paths in apps/engine/.env; use the example file as a starting point. Keep the service loopback-only until authentication is implemented.

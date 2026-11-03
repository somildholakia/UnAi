# Development checkpoint

**Checkpoint:** M2 incremental SQLite graph persistence and conservative static call/instantiation edges delivered; schema migration and regression coverage verified by CI (311 tests)
**Date:** 2026-11-03
**Repository:** https://github.com/somildholakia/UnAi
**Branch:** feat/m4-instance-methods-m5-specialists
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

## 2026-11-03 continuation checkpoint

**Current integration PR:** https://github.com/somildholakia/UnAi/pull/28 (combined PR against main; includes the publisher trust-bundle work formerly in PR #27)
**Earlier publisher trust-bundle PR:** https://github.com/somildholakia/UnAi/pull/27 (overlapping predecessor; do not merge separately while PR #28 is the integration PR)
**Current feature branch:** `feat/m4-instance-methods-m5-specialists`
**Latest validated code commit:** `674726cbcc2b554bf564186f5fe49a7abb323bf4`
**CI:** run #243 — 289 tests passed in 6.10s; Python compile check passed.
**Working tree:** GitHub commits only; no local model training has been run.

### M4 additions in this continuation

- Added ordinary instance-method targets on undecorated top-level classes with no inheritance, class decorators or metaclass keywords.
- Added explicit JSON `instance_arguments` per oracle case; constructor and method signatures are validated separately. Custom `__new__`, async/decorated `__init__`, positional-only parameters, `*args` and `**kwargs` are rejected.
- Generated tests construct one fresh instance per case/replay inside the disposable Docker workspace. Method lookup uses the class namespace directly; caller text is never interpolated as Python source.
- Constructor fixture SHA-256 is included in append-only oracle evidence and optional reviewer-attestation statements. Existing module/static/class-method signatures remain compatible.
- Added sync/async instance-method, unsafe-constructor, signature and fixture-attestation tests. CI has not yet run on this continuation branch.

### M5 runtime integration in this continuation

- Added optional server-side model IDs for `next_action`, `planner` and `verifier` in Engine settings and `.env.example`.
- Added a typed specialist chat API and per-specialist prompt profiles. The OpenAI-compatible provider accepts an internal model override selected only from server configuration.
- Missing specialist mappings return HTTP 503; there is no silent base-model fallback. The endpoint remains behind the existing Engine token/loopback middleware.
- Added tests for configured routing, missing-adapter behavior, invalid role rejection and provider model-override payloads.
- Runtime routing is ready for served adapters, but no specialist weights are available yet.

### M5 benchmark-contract integrity in this continuation

- Added the exact `case_contract` object to every held-out result record: canonical prompt plus explicit acceptance criteria.
- The evaluator recomputes SHA-256 from canonical JSON at input validation and rejects a supplied digest that does not match its own prompt/criteria. A matched trio must still agree on the same case digest.
- Updated the strict JSON Schema and the benchmark guide with the new field; tests cover content edits with stale hashes and malformed contract structure.
- CI run #243 passed all 289 tests and Python compilation. A later documentation-only cleanup removes a redundant digest example.

### M5 evaluation hardening in this continuation

- Added SHA-256 task/acceptance-content fingerprints to each matched base/adapter/tool result; all three systems must run the same exact case bytes.
- Added exact two-sided McNemar paired significance reporting for both adapter and tool-augmented comparisons.
- Made the release floor explicit: at least 30 matched held-out cases, alpha no higher than 0.05, a positive adapter success-rate delta and zero adapter regressions.
- The release gate independently recomputes p-values from paired discordant counts and verifies consistency of the sample/count/rate report fields. Unavailable runs and contradictory verification statuses are rejected.
- Added tests for small-sample non-promotion, case-hash mismatch, failed/all-passing contradictions, significance calculations and tampered reported p-values.

### M5 additions in this continuation

- Added consent/license/verification-gated `training/prepare_corpus.py`, with hashed repository grouping, deterministic train/eval splits by project, deduplication, common-secret redaction and per-specialist corpus manifests.
- Reworked `training/train_lora.py` to train one named specialist at a time, require repository-disjoint train/eval files, mask only assistant continuation tokens, preserve responses during prompt truncation, pad labels correctly and emit reproducible data/library/hardware/adapter artifact hashes.
- Added `training/evaluate_results.py` for matched base/adapter/tool-augmented held-out verification outcomes and `training/release_gate.py` for paired quality gains, zero regression, matching adapter artifacts and physical-device RAM/TTFT/throughput/thermal limits.
- Added training-corpus and evaluator/release-gate tests plus a complete training guide.

**M5 is not a completed model-training milestone:** no dataset has been supplied, no specialist weights have been trained, and no physical Android benchmark has been measured. The infrastructure is ready for those external inputs. The 6 GB memory ceiling is a release budget, not a measured result.

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

- [x] Added an opt-in offline publisher trust-bundle verifier on
  `feat/m4-publisher-trust-bundles`: operator-pinned Ed25519 roots sign a
  canonical JSON snapshot with publisher keys, issuer labels and exact host
  allowlists. It enforces a 256 KiB bundle limit, 256-entry limit, 30-day
  maximum validity, timezone-aware timestamps, revocation, and an operator-
  supplied minimum sequence. Oracle evidence carries the verified bundle
  sequence, root ID and payload digest. Direct publisher-key configuration
  remains supported and is mutually exclusive with bundle mode. Tests and CI
  passed GitHub Actions run #193 (235 tests passed; Python compile check passed). PR #27 remains open for review and has not been merged.

- The AST probe generator still produces unverified candidates; it never infers expected outputs.
- POST /api/tasks/{task_id}/grounded-tests accepts explicit user- or specification-supplied expected JSON outputs, a human-readable oracle reference, exact source SHA-256, and optional probe candidate IDs.
- A case linked to a probe must preserve that candidate's parameter assignments and exact JSON types. The selected module-level function or explicitly bound ClassName.method target is inspected; unknown/missing arguments are rejected, and a classmethod's implicit cls parameter is not caller-controlled.
- The target is re-read and its source fingerprint is checked immediately before test generation. A stale source/probe returns a conflict and does not execute.
- Caller data is JSON-serialized into a fixed generated pytest harness. The generated test is added only to the disposable filtered workspace under unai_generated_tests; it is never written to the original workspace. Per-value and aggregate oracle payload sizes are bounded.
- Execution requires both workspace_read and run_tests permissions already approved on the task, as well as the separate exact run-grounded-oracle-tests confirmation. Only the fixed python -m pytest -q command runs in the Docker sandbox; no model-supplied shell command is accepted.
- The verifier records oracle provenance, source and generated-test hashes, execution status, output hashes and bounded redacted traces. It does not store the raw oracle arguments/expected values in the ledger. Opt-in replay-stability cases capture only bounded, hash-only replay records from repeated calls in the same module process.
- Specification cases may optionally pin a workspace Markdown/text artifact to a SHA-256 fingerprint. The verifier re-reads it before generating tests, checks it again before staging, and the generated harness verifies the copied artifact (and target source) before importing the target.
- A specification case may include an Ed25519 publisher artifact-manifest signature, separate from the reviewer assertion signature. The Engine checks its configured issuer, exact HTTPS hostname allowlist, artifact URI, revision and SHA-256 bytes before accepting it. Publisher key entries support timezone-aware not_before/not_after issuance windows and an immediate revoked flag; multiple key IDs per issuer support rotation while retaining historical validation.
- Publisher key entries may include timezone-aware not_before/not_after issuance bounds and a revoked flag. Multiple key IDs may share one issuer for rotation; revoked=true immediately rejects historical and new signatures.
- A specification case may include an Ed25519 reviewer attestation. The Engine verifies it against the operator-configured UNAI_ORACLE_TRUSTED_PUBLIC_KEYS keyring and binds it to the exact task/workspace, source hash, artifact hash, case inputs and expected output/property. Invalid/unknown signatures are rejected before oracle evidence or execution. A valid signature authenticates the configured reviewer key's approval, not logical truth or publisher identity.
- A specification artifact may also include a publisher-signed manifest. The Engine verifies Ed25519 against UNAI_ORACLE_TRUSTED_PUBLISHER_KEYS, checks the configured issuer and exact HTTPS hostname allowlist, and binds the signature to artifact URI, revision and verified SHA-256 bytes. Optional not_before/not_after timestamps bound the signed issued_at value; revoked=true immediately rejects all signatures from that key. Publisher and reviewer keyrings rotate independently; a publisher signature authenticates the document origin but not the derived expected-output assertion.
- Property-oracle cases may use one fixed non-executable predicate instead of an exact expected output. Supported predicates include allowlisted expected-exception types, opt-in output stability across 2–4 same-process replays of the same JSON inputs, and opt-in workspace_filesystem_unchanged checks. Replay checking is capped at 24 function invocations per request and records hash-only per-replay results and bounded exception/source data. Workspace checks compare before/after manifests in the disposable workspace, cap scans at 1,024 entries, 2 MiB per file and 16 MiB total bytes, and return unavailable rather than passed when a snapshot is incomplete. Replay and filesystem divergence are not automatically shrunk. Other sandboxed property counterexamples may be shrunk over at most 24 candidate executions and 6 separate Docker rounds per request, with nested values bounded to depth 4, 128 visited nodes and 12 candidates per argument per round. Exact-output cases are never shrunk; results are not global-minimality proofs.

- Return mismatches and unexpected function exceptions that emit the generated case marker are recorded as counterexample evidence tied to the exact execution evidence ID and source SHA-256. Structured markers include a canonical JSON result hash when available, an exception-message hash and target source line/function; raw previews remain bounded and redacted. These are diagnostic fields, not a full process replay. Async functions are invoked using asyncio.run inside the generated sync pytest wrapper.
- GET /api/tasks/{task_id}/proof-bundle combines task patch/test freshness, oracle events, counterexamples, traces, unverified probe candidates and unresolved risks.
- The typed Engine client and read-only task MCP bridge expose proof-bundle retrieval; the MCP server does not gain test execution, patch-apply or arbitrary-code tools.
- Proof summaries keep historical counterexamples while deriving the current overall status from the latest grounded run. A candidate is only marked verified when a completed sandbox run actually exercises its linked oracle case; an unavailable run leaves it unverified.
- Grounded tests support synchronous and async, undecorated module-level functions plus explicitly decorated @staticmethod and @classmethod methods on top-level classes. Class targets use ClassName.method syntax; classmethod's bound cls parameter is omitted from caller arguments. Ordinary instance methods, nested classes, other decorators, positional-only parameters, *args and **kwargs remain unsupported. Return-value oracles require JSON-compatible values; expected-exception oracles use a fixed allowlist and exact exception-type matching.
- User oracle references and unattested specification/property contracts remain requester-supplied. A reviewer signature authenticates an operator-configured reviewer key's exact test assertion; a publisher signature authenticates the configured publisher's exact artifact manifest. Neither proves logical correctness or legal identity beyond operator-managed trust configuration. A mismatch is evidence against the implementation *assuming the oracle is correct*; passing examples are not a proof of general correctness.
- Shrinking is opt-in and applies only to explicit property oracles; reductions support integers, finite floats, non-empty strings, lists and string-keyed dictionaries at the top level and within JSON-compatible nested values. Recursive traversal is capped at 4 levels, 128 nodes and 12 candidates per argument, with a global cap of 24 candidate executions and 6 Docker rounds per request. Results are smallest-observed, not proven globally minimal; nested booleans/null, non-finite floats and non-string mapping keys remain unsupported.

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
- M4 bounded string shrinking PR #11 was squash-merged as 6773b22ca7df5b64d70f86d94179b4e247d339e6.
- M4 string-shrinking PR CI: https://github.com/somildholakia/UnAi/actions/runs/41294014101
- M4 string-shrinking post-merge CI: https://github.com/somildholakia/UnAi/actions/runs/41294070349
- Both PR #11 workflows reported 173 tests passed; python -m compileall -q apps/engine/src training passed.
- M4 top-level collection shrinking PR #13 was squash-merged as 958d3c11655213ceeafe37d7b0ebe01e3b0c61e1.
- M4 collection-shrinking PR CI: https://github.com/somildholakia/UnAi/actions/runs/41294275727
- M4 collection-shrinking post-merge CI: https://github.com/somildholakia/UnAi/actions/runs/41294344686
- Both PR #13 workflows reported 175 tests passed; python -m compileall -q apps/engine/src training passed.
- M4 recursive nested-value shrinking PR #16 was squash-merged as beea101073df8abb2d0c7bd50095658862d5e0ec.
- M4 recursive-shrinking PR CI: https://github.com/somildholakia/UnAi/actions/runs/41295629226
- M4 recursive-shrinking post-merge CI: https://github.com/somildholakia/UnAi/actions/runs/41295688338
- Both PR #16 workflows reported 182 tests passed; python -m compileall -q apps/engine/src training passed.
- M4 static/class method invocation PR #17 was squash-merged as 3464578c71a15ebf10f3d169fc753d93c84a8be4.
- M4 method-invocation PR CI: https://github.com/somildholakia/UnAi/actions/runs/41296156280
- M4 method-invocation post-merge CI: https://github.com/somildholakia/UnAi/actions/runs/41296223884
- Both PR #17 workflows reported 188 tests passed; python -m compileall -q apps/engine/src training passed.
- M4 publisher-signed specification artifact PR #19 was squash-merged as 22b243300a04e7a7e0ac08aef2772d219cc30ecc.
- M4 publisher-manifest PR CI: https://github.com/somildholakia/UnAi/actions/runs/41296563513
- M4 publisher-manifest post-merge CI: https://github.com/somildholakia/UnAi/actions/runs/41296625707
- Both PR #19 workflows reported 197 tests passed; python -m compileall -q apps/engine/src training passed.
- M4 publisher-key lifecycle PR #21 was squash-merged as 4931bdb7494660ab51b53c4bcda5342dc9c27d4b.
- M4 publisher-key lifecycle PR CI: https://github.com/somildholakia/UnAi/actions/runs/41296894788
- M4 publisher-key lifecycle post-merge CI: https://github.com/somildholakia/UnAi/actions/runs/41296961455
- Both PR #21 workflows reported 201 tests passed; python -m compileall -q apps/engine/src training passed.
- M4 per-case replay stability traces PR #24 was squash-merged as 8889cdb61601baf2fc16b49865bd6befd9c312e1.
- M4 replay-trace PR CI: https://github.com/somildholakia/UnAi/actions/runs/41300724885
- M4 replay-trace post-merge CI: https://github.com/somildholakia/UnAi/actions/runs/41300791616
- Both PR #24 workflows reported 213 tests passed; python -m compileall -q apps/engine/src training passed.
- M4 workspace filesystem side-effect audit PR #25 was squash-merged as e5fb39edb621f28e9286212dc8da46894a66d273.
- M4 workspace-state PR CI: https://github.com/somildholakia/UnAi/actions/runs/41301773620
- M4 workspace-state post-merge CI: https://github.com/somildholakia/UnAi/actions/runs/41301848383
- Both PR #25 workflows reported 217 tests passed; python -m compileall -q apps/engine/src training passed.
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

1. Add organizational publisher identity discovery or signed key-transparency records; current issuer mappings remain operator-managed and do not prove a public legal entity.
2. Add richer execution traces and independent/discriminating checks for stateful functions, side effects and more than JSON-returning pure functions.
3. Expand grounded invocation to ordinary instance methods and nested classes only with explicit constructors/factories, fixture policies and isolation rules; support broader Python signatures only with deterministic invocation rules.
4. Add coverage-guided and mutation-based verification only with deterministic budgets, Docker restrictions and evidence provenance.
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


## 2026-11-03 — M5 release-gate hardening / handoff

**Canonical resume point for the next chat**

- Repository: https://github.com/somildholakia/UnAi
- Active branch: `feat/m4-instance-methods-m5-specialists`
- Latest branch/PR head: `3494bb1ffffe8af063d3469340f118ff60f2d46d`
- Integration PR: https://github.com/somildholakia/UnAi/pull/28 (open, mergeable; do not merge unless explicitly requested).
- Main remains unchanged at `fef00e16fbe721e7c7946aee89fcc8e44fb0e896`.
- Latest CI run #247: https://github.com/somildholakia/UnAi/actions/runs/41659235207 — **290 tests passed in 5.72s**, Python compileall passed. This run validates the current branch head exactly.

### Latest changes in this continuation

- `4a66efde06b56f0c085c2f314f0fd20f497b83f3`: fixed M5 release-gate numeric validation so a negative adapter success-rate delta is accepted as a valid measured value and results in a blocked quality gate, rather than raising a validation exception.
- `3494bb1ffffe8af063d3469340f118ff60f2d46d`: added a regression test proving a worse-than-base adapter is reported as non-releasable, not as a tool error.
- CI #247 completed successfully with 290 passing tests and compileall.

### State / safe next actions

M4 instance-method fixture support and M5 corpus-preparation, LoRA/QLoRA training code, matched held-out evaluation, statistical quality gate, physical-device release gate, and fail-closed specialist routing are implemented. No user-approved consented/licensed training corpus has been supplied, no specialist adapter weights have been trained, and no physical Android measurements have been collected. Do not claim those execution milestones are complete.

PR #28 is the combined integration PR and includes the publisher trust-bundle work previously tracked by PR #27. PR #27 remains open but overlaps; do not merge it separately while #28 is the integration candidate. Do not merge PR #28 unless the user explicitly asks.

**Next chat instruction:** continue from branch `feat/m4-instance-methods-m5-specialists`, exact HEAD `3494bb1ffffe8af063d3469340f118ff60f2d46d`. First inspect PR #28, branch and latest CI; read this section and `docs/CONTINUITY.md` / `docs/ROADMAP.md`. Preserve main. Continue with M5 real-data/training readiness and remaining M4 security/verification work without fabricating datasets, training runs or device metrics.


### Handoff documentation commit references

The code validated by CI #247 is `3494bb1ffffe8af063d3469340f118ff60f2d46d`. Cross-chat handoff documentation was then refreshed in:
- `d6ac7380dd79919348ae6f4d3844212a929f41cb` — this checkpoint file.
- `9b3ae7e8add28b675fdf8c161fa63f892969456d` — continuity handoff (followed by a clarification-only continuity update).
These commits change documentation only; CI #247 validates the unchanged code head above. Fetch the branch before resuming to get the exact newest documentation commit.


## 2026-11-03 M5 corpus integrity continuation

**Validated code HEAD:** `c0600017504df5f5ac541bfd4b7a1d539834d04a`  
**GitHub Actions:** [run #252](https://github.com/somildholakia/UnAi/actions/runs/41660871637) — 295 tests passed in 7.28s; Python compile check passed.  
**Prior M5 integrity commit:** `a8b538a31bc9bea00fff506d1cc0e684fc639567` — [run #251](https://github.com/somildholakia/UnAi/actions/runs/41660522248), 294 tests passed in 6.84s; compile check passed.

### Training-boundary manifest verification

- The specialist trainer now requires the adjacent `corpus_manifest.json` before importing/loading model weights.
- It verifies specialist identity, exact train/eval filenames, split file SHA-256 values, example counts, repository-group counts and disjoint train/eval groups.
- Each prepared row is validated against bounded message/role constraints, explicit consent/license/verification metadata, a SHA-256 group ID, provenance/group consistency and a recomputed canonical record ID.
- Added regression tests for stale/tampered files, manifests, split metadata, malformed records and missing manifests. Updated `training/README.md`.

### Corpus-preparation lineage and preflight

- The corpus manifest now records the raw source JSONL SHA-256 and byte count without copying raw input records into the manifest.
- Preparation validates and serializes all three specialist splits before writing any output. If a later specialist lacks enough independent repository/project groups, existing split artifacts and manifest remain unchanged.
- Added tests for source-file lineage and preservation of prior outputs on split-validation failure.

### Honest M5 status

These changes harden the training data boundary; they do not represent a training run. No consented/licensed real corpus has been supplied, no specialist adapters have been trained, no held-out model comparison has been run, and no physical Android memory/latency/throughput/thermal measurements have been collected. M5 remains infrastructure-complete but not trained or released.

PR #28 remains open and unmerged. Main remains unchanged. Do not merge PR #28 or overlapping PR #27 without explicit user instruction.


### M5 exact-snapshot integrity follow-up

**Code commit:** `2173651b1fd84da4104088a6240f749bc7edab2f`  
**GitHub Actions:** [run #254](https://github.com/somildholakia/UnAi/actions/runs/41661499858) — 297 tests passed in 7.70s; Python compile check passed.

- The trainer now reads each JSONL split into one in-memory byte snapshot, computes SHA-256 from those exact bytes, decodes/parses that same snapshot, then compares its digest with the prepared corpus manifest before model loading.
- This removes the prior two-read time-of-check/time-of-use gap where the file could change after parsing but before the separate hash operation.
- Added regression tests for a file changed after loading (the manifest is checked against the loaded snapshot actually used) and for a changed snapshot that no longer matches the manifest.
- Updated the training guide to explain that corpus digests are tied to the exact data snapshot used for training.

The change does not alter M5's external prerequisites: no approved real corpus, trained weights, held-out model comparison or physical Android performance measurements exist yet.



## M2 task-specific context retrieval

**Code commit:** `7950c8a65ef4151530b599053079e94b8fdd0a32`  
**GitHub Actions:** [run #256](https://github.com/somildholakia/UnAi/actions/runs/41668817672) — 300 tests passed in 7.10s; `python -m compileall -q apps/engine/src training` passed.  
**Branch:** `feat/m4-instance-methods-m5-specialists`  
**Integration PR:** [#28](https://github.com/somildholakia/UnAi/pull/28), still open and unmerged.

- The bounded Python AST graph now attaches SHA-256 fingerprints of the exact source bytes parsed to each file/symbol node.
- Task inspection carries those fingerprints into its persisted symbol-retrieval evidence.
- Added `GET /api/tasks/{task_id}/context`. It does not accept arbitrary paths or symbols; it returns only symbols discovered during that task's approved inspection.
- Every excerpt is re-read through the existing permissioned, traversal/symlink/sensitive-file guarded file reader. The current source digest must match the original inspection digest; otherwise the endpoint returns HTTP 409 and asks for re-inspection.
- Excerpts are line-numbered and bounded to 10 symbols, 120 source lines and 6,000 characters per slice, with a 24,000-character aggregate budget.
- Added regression tests for SHA propagation, successful context retrieval, the missing workspace-read approval path and stale-source rejection.
- Updated README, code-graph documentation and the M2 roadmap checklist.

This delivers the M2 task-specific retrieval item. At that code checkpoint, the graph was still Python-only and rebuilt on demand; the later M2 SQLite and M2.1 static-call checkpoints below supersede that implementation status. No model weights or mobile performance claims are implied by this work.


## Handoff verification

- Latest validated code commit: `f083452af8bd16ed8cbfec70fb1157e8b76f7084`; GitHub Actions #264 passed (311 tests, compileall).
- Previous handoff documentation revision: `e752cecb10cc2c3eb0d44c5938f6fc316880a80d`; documentation CI #257 passed. This checkpoint has since received another documentation-only refresh; fetch the live branch head before continuing.
- Main remains `fef00e16fbe721e7c7946aee89fcc8e44fb0e896`.
- PR #28 remains open and unmerged. No merge was performed.
- For a new chat, fetch the branch and CI again, then read this file, `docs/CONTINUITY.md`, and `docs/ROADMAP.md` before continuing.


## 2026-11-03 — M2 incremental SQLite repository graph persistence

**Code commit:** `c68e300ff7b7c754608dffbc611b729daee44de3`  
**GitHub Actions:** [run #260](https://github.com/somildholakia/UnAi/actions/runs/41671966813) — **305 tests passed in 7.96s**; `python -m compileall -q apps/engine/src training` passed.  
**Branch:** `feat/m4-instance-methods-m5-specialists`  
**Integration PR:** [#28](https://github.com/somildholakia/UnAi/pull/28), open and unmerged.

### Delivered

- Added `SQLiteCodeGraphStore` with versioned schema for workspace metadata, per-file graph records and derived edges. WAL, foreign keys, busy timeout and full synchronous writes are enabled.
- Enabled the cache in the Engine with `UNAI_CODE_GRAPH_DB_PATH` (default `.data/code-graph.sqlite3`); direct indexer use remains possible without persistence.
- Each eligible Python file is read as one bounded byte snapshot. Before/after stat and inode checks reject changes during the read. SHA-256 is calculated from those exact bytes on every refresh, so same-size edits with restored timestamps are still detected.
- Unchanged file hashes reuse stored AST symbol ranges, parse diagnostics and import inputs. Changed/new files are reparsed; deleted file rows disappear from the new snapshot. Global imports and heuristic test relationships are rebuilt from the current candidate set.
- Added a deterministic repository fingerprint over sorted relative paths and source digests. Graph responses now report cache state, generation, hashed/reparsed/reused/removed counts.
- All repository metadata, file rows and derived edges publish together inside one `BEGIN IMMEDIATE` transaction. A failure rolls back to the preceding complete graph; an optimistic generation check detects concurrent refreshes, with one retry at the indexer boundary.
- Unknown future SQLite schema versions fail closed. The database contains graph metadata and source fingerprints, not source bodies.
- Added tests for cold/warm cache behavior, incremental changed/new/deleted files, same-size/same-mtime source edits, atomic rollback and unknown schema versions.

### M2 status and limits

The roadmap item for persistent file/symbol/import/test graph storage is now complete for the bounded Python AST graph. Every refresh still reads and hashes the bounded eligible source files; the optimization avoids re-parsing unchanged files, not filesystem traversal or hashing. The graph includes the conservative static `calls`/`instantiates` edges added in M2.1 below; it is not a complete runtime call graph. Test-to-source links remain heuristic, and the graph is not a complete semantic model. No model training, mobile benchmarks or production-readiness claims follow from this work.

### Safe continuation

- Continue on `feat/m4-instance-methods-m5-specialists`; preserve `main`.
- PR #28 remains the integration PR and must not be merged without explicit permission. PR #27 overlaps and must not be merged separately.
- M5 still requires a real consented/licensed corpus, actual specialist training, held-out comparison and physical Android measurements. None is represented as complete by this checkpoint.


## 2026-11-03 — M2.1 conservative static call graph

**Code commit:** `f083452af8bd16ed8cbfec70fb1157e8b76f7084`  
**GitHub Actions:** [run #264](https://github.com/somildholakia/UnAi/actions/runs/41674645703) — **311 tests passed in 7.50s**; `python -m compileall -q apps/engine/src training` passed.  
**Branch:** `feat/m4-instance-methods-m5-specialists`  
**Integration PR:** [#28](https://github.com/somildholakia/UnAi/pull/28), open and unmerged.

### Delivered

- Added bounded AST call-site collection inside function/method bodies: up to 100 call sites per file and 12,000 per repository refresh.
- Added conservative `calls` edges for uniquely resolved same-module functions, `self`/`cls` methods, local class-qualified methods, directly imported function aliases and explicitly imported module aliases.
- Added `instantiates` edges for uniquely identified local or directly imported classes invoked as constructors.
- Added lexical shadowing checks for function arguments, local assignments, nested definitions and local imports. Imported-call resolution only uses module-scope imports. Ambiguous duplicate definitions, arbitrary receiver attributes, computed calls and dynamic dispatch remain unresolved.
- Added per-file persisted call-site data and module import bindings. SQLite schema version 2 adds the required columns; version-1 graph caches are migrated and each eligible file is reparsed once to backfill static call data. Unknown future schema versions still fail closed.
- Added graph summary counters for call edges, instantiation edges, indexed/unresolved call sites and call-site truncation.
- Added tests for same-module calls, `self` methods, imported function/module/class aliases, class instantiation, ambiguous and dynamic calls, recursive functions, shadowed aliases, call-site budgets and version-1 cache migration.

### Semantics and limits

These are conservative source-level AST relationships, not runtime traces or a complete call graph. Dynamic dispatch, reflection, monkey-patching, arbitrary instance attributes, inheritance-based resolution and call order are not inferred. Unresolved call-site counts include ordinary built-ins and external calls; unresolved does not mean invalid. The graph remains Python-only, and heuristic test-to-source links are still not guaranteed.

### Verification and safe continuation

- GitHub Actions run #264 validated the exact code commit above: 311 tests passed and Python compilation passed.
- Two earlier CI runs exposed test expectation mistakes (call-site count and an unresolved built-in constructor); both were corrected before the green run. No known test failure remains at this checkpoint.
- Continue on `feat/m4-instance-methods-m5-specialists`; preserve `main`.
- PR #28 remains open and must not be merged without explicit permission. PR #27 overlaps and must not be merged separately.
- M5 still requires a real consented/licensed corpus, actual specialist training, held-out comparison and physical Android measurements; none is claimed complete.

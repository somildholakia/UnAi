# Development checkpoint

**Checkpoint:** M4 implementation and M5 engineering foundation validated through the bounded streaming corpus reader/audit and expanded credential redaction; code/test HEAD `6b6fca0c8c7401dae0f932c2bf2f2048c06b5c04`; 449 Engine tests passed and both Engine/Web and Brain Worker workflows are green.
**Date:** 2026-11-05
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

## 2026-11-05 M5 first-party dataset collection increment

**Implementation:** deterministic synthetic collector, synthetic-contract validation, source-provenance preservation, public-source revision/license-evidence checks, aggregate source-type audit, end-to-end pipeline tests and local-data ignore rules.
**Status:** implementation pushed to `feat/m4-instance-methods-m5-specialists`; CI validation pending at the time of this note.
**Entry point:** `python -m training.collect_synthetic --output data/raw/unai-synthetic.jsonl`.
**Default size:** 20 repository/project groups × 12 records × 3 specialist roles = 720 synthetic records.
**Data flow:** collect → read-only audit → repository-disjoint preparation → trainer manifest verification.
**Trust boundary:** generated answers are checked against deterministic generator contracts only. They are synthetic, not real repository trajectories, and do not demonstrate model quality. Public-source rows require HTTPS source/license evidence and an immutable revision. No adapter weights, held-out model results or device metrics are claimed.

### Delivered behavior

- The standard-library-only collector produces deterministic `next_action`, `planner` and `verifier` examples, validates the response contract before writing, and emits a sidecar manifest with seed, generator version, license, counts, bytes and SHA-256.
- It writes generated JSONL via a temporary file and atomic rename; a failed contract check leaves an existing dataset untouched.
- The existing bounded audit now reports synthetic/public/legacy source classes only as aggregates.
- Preparation preserves source type, generator/version, rights basis, and public source/revision/license URLs after validation. Public URLs must use HTTPS; local authorized imports require an authorization reference.
- Provenance source and license evidence are sanitized before prepared output. Regression tests cover redaction and malformed source metadata.
- Training files and generated artifacts remain local through `.gitignore`.

### Current boundaries

The generator bootstraps the data pipeline and creates modest synthetic examples. It does not replace a diverse, independently verified real-world corpus. Specialist adapters have not been trained; matched held-out evaluations and physical Android measurements remain separate release gates.

## 2026-11-05 M5 corpus-preflight and credential-redaction checkpoint

**Branch:** `feat/m4-instance-methods-m5-specialists`
**M5 read-only corpus audit:** `c2ffb4ad40e801b21c1121d3ccc6e9e44c158bc9`
**Expanded secret redaction:** `0a6800a312042c209464cbb07b793235de6bd277`
**CI regression-test correction:** `90b75a8db13defaf9e76084e2c8c8bd228d8f256`
**Engine/Web Actions run #405:** success — **447 tests passed in 23.51s**; disposable Docker sandbox image build, Python compileall, six Termux shell syntax checks and Vite production build passed.
**Brain Worker Actions run #88:** success — Worker typecheck, D1 migration validation and browser JavaScript syntax passed.
**Integration PR #28:** open and unmerged. **main:** `fef00e16fbe721e7c7946aee89fcc8e44fb0e896`, unchanged.

### Delivered

- Added `python -m training.audit_corpus --input PATH`, a read-only, metadata-only preflight before corpus preparation. It reports the input digest/byte count, aggregate eligible/rejected/duplicate counts and per-specialist distinct-group split readiness.
- The audit omits conversation bodies, raw repository/project identifiers and provenance-source strings from its output. It enforces the 2 GiB input ceiling and the corpus preparer's 1 MiB per-record ceiling; readiness means only that a structural split is possible, not that the data is sufficiently large or that consent/license statements are independently authenticated.
- The audit and corpus preparer now read each JSONL record with a bounded `readline(1 MiB + 1 byte)` buffer. Oversized lines are drained in bounded chunks without retaining their full contents; the exact bytes still contribute to the input SHA-256 and byte count. The 2 GiB total-input budget is enforced both before reading and during streaming, including files that grow after the initial stat.
- Expanded secret scrubbing to provider-specific tokens, cloud access key IDs, webhook/package/model-hosting tokens, bearer/JWT forms, database connection strings, PEM private-key blocks and signed URL signatures. Added focused regression coverage for those forms.
- Fixed the oversized-input audit test so it changes the audit module's limit with a tiny real file rather than mocking `Path.stat()` with an incomplete stat object. This tests the intended guard without depending on pathlib implementation details.

### Remaining external gates

No approved real-world training corpus, trained adapter artifacts, held-out model comparison or physical Android benchmark has been supplied or run. These are still release prerequisites, not missing code primitives. Keep all three specialists unrouted until their adapters are trained and validated; the server-side routes intentionally fail closed for missing models.


## 2026-11-05 M4 bounded coverage-measurement checkpoint

**Repository:** https://github.com/somildholakia/UnAi
**Branch:** `feat/m4-instance-methods-m5-specialists`
**Validated code/test HEAD:** `e6af02a48fa03ace04627b9910c39f6c2bd57233`
**Coverage implementation:** `c1e01b5fef276b7dda14fe2a59b1efb18bfef6b7`
**Trace lifecycle fix:** `b86c9307371613ad2214413d59f109d633c587f7`
**Docker workspace-owner fix:** `9cf91687954ed1d5ae8978625a809590b6d5d7bc`
**Sandbox regression assertion:** `e6af02a48fa03ace04627b9910c39f6c2bd57233`
**Engine/Web Actions run #372:** success — [433 tests passed in 17.19s](https://github.com/somildholakia/UnAi/actions/runs/42006411694); the job built `unai-python-test:latest`, ran the real Docker-backed coverage integration test, passed Python compileall, all six Termux shell syntax checks and the Web production build.
**Brain Worker Actions run #55:** success — [Worker checks](https://github.com/somildholakia/UnAi/actions/runs/42006411841), including typecheck, D1 migration validation and browser JavaScript syntax.
**Latest branch-head Engine/Web validation:** run #378 on docs-only branch HEAD `8318d190b7403a6eac77773fa64bb1f9199e1838` also passed — [433 tests in 23.74s](https://github.com/somildholakia/UnAi/actions/runs/42007140164), Docker sandbox build, compileall, six Termux shell syntax checks and Web production build.
**Latest Brain Worker validation:** run #61 passed — [Worker checks](https://github.com/somildholakia/UnAi/actions/runs/42007140229).
**Integration PR #28:** open, unmerged. **main:** `fef00e16fbe721e7c7946aee89fcc8e44fb0e896`, unchanged.

### Delivered

- Added opt-in `measure_coverage: true` to the existing source-hash-bound grounded-test request. It measures the supplied cases in the same approved, disposable Docker run; it does not create new oracle inputs or expected outputs.
- Added a deterministic AST plan for target-body statement lines and supported `if`, `while`, `for` and `async for` branch arms. Bounds include 50,000 AST nodes, 5,000 coverable lines, 200 branch descriptions, 300 condition characters and 500 returned missing-line entries.
- The generated harness traces only the selected target's exact code object and writes a hash-bound JSON trace with case IDs and source line numbers only. Trace size is capped at 256 KiB; the runner validates its path, schema, source hash, case IDs and line-number data before scoring.
- Coverage is append-only `kind=coverage`, `source=grounded-coverage` evidence linked to the execution event. Grounded responses and proof bundles expose the coverage report, evidence count/latest summary and unresolved availability risk.
- A missing or invalid coverage trace leaves the oracle verdict unchanged and reports coverage as unavailable. Coverage is an audit of observed statements/arms, not semantic correctness or automatic coverage-guided fuzzing.
- Fixed the Docker integration failure where the expected trace was missing: the harness now persists the aggregate trace after every measured invocation (with exit-time fallback), and the container runs as the staging process UID/GID so files in the bind-mounted staging workspace keep the correct owner.
- Regression coverage includes AST bounds and branch planning, trace-file hardening, real local generated-harness tracing, verifier/evidence/proof behavior and the Docker-backed end-to-end test.

### Boundaries and next step

Coverage measurement completes the pending M4 roadmap item. It does not implement automatic test generation, coverage-guided fuzzing, symbolic execution or universal branch semantics. Do not treat a high coverage ratio as proof of correctness; the supplied oracle and its provenance remain decisive.

M5 model training remains blocked until a real consented/licensed multi-repository trajectory corpus and suitable GPU compute are available. No specialist weights or held-out evaluation are claimed. Physical Android setup and measurement remain deferred until the software roadmap is complete. Do not merge PR #28 or modify `main` without explicit permission.

## 2026-11-05 M4 coverage-guided probe-ranking checkpoint

**Repository:** https://github.com/somildholakia/UnAi
**Branch:** `feat/m4-instance-methods-m5-specialists`
**Validated code/test HEAD:** `4920622429f31dbed780f325efcbcb7f2bfdeae9`
**Coverage-guided planner:** `8f831dd527153e720f4a256def3fdbacf17bd941`
**Response schemas:** `69349514151c5240acf799a3baa1db1e5792d9f1`
**API route:** `0cf27a483bd81974153cc1f8867e01178b203a26`
**Focused tests/API tests:** `07d1d88c8e9b4e2f870fdd00280d8f69a72c24c4`, `f0b84486001ba4a69eb89bc0e22642b8b5f01268`
**Evidence-binding hardening:** `11764c04958ec9db3197e48fdef4f499b14041c2`
**Engine/Web Actions run #395:** success — [442 tests passed in 18.82s](https://github.com/somildholakia/UnAi/actions/runs/42009350095); the job built the Docker sandbox image, passed Python compileall, all six Termux shell syntax checks and the Web production build.
**Brain Worker Actions run #78:** success — [Worker checks](https://github.com/somildholakia/UnAi/actions/runs/42009349905), including typecheck, D1 migration validation and browser JavaScript syntax.
**Integration PR #28:** open, unmerged. **main:** `fef00e16fbe721e7c7946aee89fcc8e44fb0e896`, unchanged.

### Delivered

- Added read-only `POST /api/tasks/{task_id}/coverage-guided-probes`. It consumes an existing AST-probe evidence ID and a complete grounded-coverage evidence ID, verifies both belong to the same task/file/symbol/source SHA-256, and requires the coverage event to link to the matching grounded execution evidence.
- Ranks only existing unverified AST candidates against explicit if/else arms with unobserved coverage. It uses a bounded static expression evaluator for native JSON values, comparisons, boolean operations, arithmetic, conditional expressions and `len`; unsupported expressions are marked `unknown` rather than executed.
- Returns the predicted arm, unobserved arms and a deterministic priority. The result is appended as `kind=probe`, `source=coverage-guided-probes`, with `passed=null`, `executed=false` and `expected_outputs_inferred=false`.
- The endpoint requires `tasks:write` and `workspace:read` API scopes plus the task's `workspace_read` approval. It does not require `workspace:execute`, invoke Docker or import/run the target.
- Bounds: 60 candidates, 16 expression levels, 8 nested JSON value levels, 128 container items, 10,000 characters per string, 256-bit integers and the existing 200-branch coverage-plan ceiling.
- Regression tests cover ranking of an unobserved else arm, omission of already-covered arms, refusal to execute unsupported calls, stale source hashes, mismatched evidence, evidence append-only behavior and the API's no-execution path.
- The first CI attempt exposed incorrectly escaped test-fixture source strings during test collection; those fixture literals were corrected before the passing run #395.

### Boundaries and next step

This is coverage-guided prioritization of existing unverified probes, not automatic test synthesis, fuzzing or a correctness proof. It does not produce expected outputs. Every suggestion still needs a caller-supplied oracle and separately approved grounded execution. Only explicit if/else branches with both arms represented by the coverage plan are currently ranked.

M5 training remains blocked on an actual consented/licensed multi-repository trajectory corpus and suitable GPU compute. No specialist weights or held-out results are claimed. Do not merge PR #28 or modify `main` without explicit permission; defer Android setup until software work is complete.

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


## Local-first Android install path — initial testable package

**Validated install/code + docs checkpoint:** `d2a388a3944eb81eb37a86f4e942303b08e2c0e1`.  
**CI:** [run #41677105754](https://github.com/somildholakia/UnAi/actions/runs/41677105754) — 311 tests passed in 6.96s; Python compileall, Termux `bash -n`, and Vite production build passed.  
**Branch:** `feat/m4-instance-methods-m5-specialists`.

### Installability changes

- Added `scripts/termux-install.sh`: installs Termux build/runtime packages, builds CPU `llama-server`, resumably downloads Qwen2.5-Coder 1.5B Q4_K_M GGUF, creates the Python virtual environment, installs Engine dependencies and installs the web UI.
- Added `scripts/termux-start.sh`: starts model, Engine and Vite UI in separate tmux windows, bound to loopback. Default context is 2K and CPU thread count defaults to four.
- Fixed malformed `@vitejs/plugin-react` version syntax in `apps/web/package.json`; browser/API loopback defaults and CORS origins now agree.
- Changed Engine's default model endpoint/ID to the local llama.cpp alias, and removed Uvicorn's optional `standard` extras from mandatory dependencies to avoid unnecessarily requiring uvloop/httptools on Android.
- Added a web production build and shell syntax validation to the CI workflow.
- Added `docs/LOCAL_INSTALL.md` and replaced the README's manual-only setup with a Termux first-run path.

### Boundaries

- This is a local inference path using an existing open-weight base model. No custom unAI weights or specialist adapters are trained by this change.
- Local inference removes hosted API quotas and per-token billing, but is limited by RAM, storage, battery, thermal throttling and speed.
- The 1.5B Q4_K_M starter model is a smoke-test model, not proof of large-model capability or quality parity.
- A successful CI build does not prove the scripts have been executed on a physical Android device. M6 still requires on-device installation, offline test, PSS/RSS, TTFT, throughput and thermal measurements.


## Install download-resume correction — validated

- **Code commit:** `7e93f8b5f8f0079b103c0504f458451e664e5e64`.
- **CI:** [run #41677589811](https://github.com/somildholakia/UnAi/actions/runs/41677589811) — 311 tests passed in 8.28s; Python compileall, Termux script syntax and web production build passed.
- The Termux installer now detects an incomplete model file and resumes the Hugging Face range download on rerun; it requires at least 1,000,000,000 bytes before accepting the file as complete.
- This remains CI-validated only. A physical Termux build/inference session has not yet been run.

## 2026-11-04 M2 incomplete-discovery persistence hardening

**Validated code commit:** `ac75a68ba6d6805c3470216596995c3d0282cd43`  
**GitHub Actions:** [run #273](https://github.com/somildholakia/UnAi/actions/runs/41820372989) — **313 tests passed in 6.59s**; Python compileall, Termux shell syntax and the web production build passed.  
**Branch:** `feat/m4-instance-methods-m5-specialists`  
**PR #28:** remains open and unmerged; `main` was not modified.

### Change

- Hardened incremental SQLite graph refreshes when the bounded repository walk reaches `MAX_FILES`.
- An incomplete file-discovery pass still returns its bounded partial graph, but marks `summary.scan_complete=false` and the persisted-cache state as `incomplete`.
- The partial graph is not published to SQLite. The previous complete generation, file rows, edges and repository fingerprint remain untouched; the partial pass reports `snapshot_persisted=false` and zero confirmed file removals.
- A subsequent complete scan reconciles the full eligible file set and publishes the next generation atomically.
- Documented the distinction between incomplete file discovery and the separately reported symbol/edge/call-site budgets in `docs/CODE_GRAPH.md`.

### Regression coverage

- A truncated refresh over an existing cache cannot evict unseen files or replace its generation; a complete retry then incorporates newly discovered files.
- An initial truncated scan cannot create a partial persisted cache.
- Existing tests continue to cover unchanged-cache reuse, content-hash stale detection (including same-size/same-mtime edits), additions/changes/deletions, transactional rollback, future-schema rejection and v1-to-v2 migration.

No model training, specialist weights, physical Android benchmarks or production-readiness claims are added by this milestone.

## 2026-11-04 Termux diagnostics checkpoint

**Validated code commit:** `aff60b72a9dcb1a909bd918677505a0251c6964a`  
**GitHub Actions:** [run #283](https://github.com/somildholakia/UnAi/actions/runs/41821364149) — **318 tests passed in 8.39s**; Python compileall, all Termux shell syntax checks and the web production build passed.  
**Branch:** `feat/m4-instance-methods-m5-specialists`  
**PR #28:** open and unmerged; `main` remains unchanged.

### Changes

- Added `scripts/termux-doctor.sh` to verify the Termux environment, required commands, executable `llama-server`, minimum model-file size, Engine virtual environment, Web dependencies and model/Engine/Web health endpoints.
- Added optional `--smoke-chat`, which sends a short request directly to the configured local OpenAI-compatible model endpoint and requires a non-empty assistant completion. It does not call a hosted provider.
- Updated `scripts/termux-start.sh` to wait for the model, Engine API and Web UI individually, print recent tmux output for any service that times out, and run diagnostics before attaching to an existing session.
- Added automated mock-install tests for healthy endpoints, valid inference, empty completions, unreachable Engine API and partial model downloads.
- Updated the install guide and README quick start. CI now syntax-checks the doctor script as well.

### Verification and boundary

The new tests use mocked services and a sparse fixture model file; CI does not download the 1.12 GB GGUF, run llama.cpp, or execute the install on Android. This makes the diagnostic workflow testable but is not a physical-device smoke test. On-device offline inference and RSS/PSS, TTFT, throughput and thermal measurements remain pending.

## 2026-11-04 Termux service crash-recovery checkpoint

**Validated code/test commit:** `9c997b52fd016924583042dbd1ffc46e439e7263`  
**GitHub Actions:** [run #296](https://github.com/somildholakia/UnAi/actions/runs/41822272142) — **324 tests passed in 12.25s**; Python compileall, Termux shell syntax (including the supervisor) and Web production build passed.  
**Branch:** `feat/m4-instance-methods-m5-specialists`  
**PR #28:** remains open and unmerged; `main` remains unchanged.

### Implementation

- Added `scripts/termux-supervise.sh`, a per-service runner with bounded retries after non-zero exits, exponential delay (2 seconds initially, capped at 30 seconds), and a configurable retry budget (five by default).
- A clean process exit (code 0) is treated as intentional and is not restarted. After the retry budget is exhausted, the supervisor exits with the last process code.
- The model, Engine and Web tmux panes now run through this supervisor. Tmux panes remain visible after the process exits so the last output can be inspected.
- Service output is mirrored to `$HOME/.local/state/unai/{model,engine,web}.log` by default. Logs rotate to one `.log.1` backup after exceeding 1 MiB.
- The doctor checks that the supervisor is present and prints recent service log lines when a health endpoint is unreachable.
- Added supervisor regression tests for transient failures/recovery, clean exits, bounded restart exhaustion, log rotation and invalid configuration; added doctor coverage for crash-log visibility.
- Documented configuration overrides and recovery boundaries in `docs/LOCAL_INSTALL.md` and `README.md`; updated the M6 roadmap.

### Verification boundaries

CI uses controlled fixture commands and mocked service endpoints. It does not run llama.cpp, install on a physical Android device, or measure mobile resource usage. The supervisor can recover a child service while the Termux/tmux session remains alive; it cannot revive Termux after Android terminates all of its processes. Physical Android process-lifecycle and low-memory recovery remain open.

## 2026-11-04 Termux status and safe recovery checkpoint

**Validated code/test commit:** `d731c0a0b16d00e5fced5bb0d4551c5d4d5126ec`  
**GitHub Actions:** [run #307](https://github.com/somildholakia/UnAi/actions/runs/41822981471) — **331 tests passed in 13.12s**; Python compileall, syntax validation for all six Termux scripts and Web production build passed.  
**Branch:** `feat/m4-instance-methods-m5-specialists`  
**PR #28:** open and unmerged; `main` unchanged.

### Changes

- Added `scripts/termux-service.sh` as the single source of model/Engine/Web launch commands. Both initial startup and recovery use this helper, avoiding drift in model flags, paths or service commands.
- Added `scripts/termux-control.sh status` to report endpoint health alongside whether each named tmux pane is alive, dead or missing.
- Added `scripts/termux-control.sh recover [model|engine|web|all]`. It checks health before taking action, leaves healthy listeners untouched even if their pane is dead, leaves a live supervisor alone while it retries, and respawns only a dead pane or missing service window.
- If the entire tmux session is missing, recovery starts the full stack only when all three endpoints are unreachable. It refuses full-stack launch if any endpoint still responds, avoiding duplicate listeners from unmanaged/orphaned processes.
- Updated the doctor to check for the shared service launcher and control command. Updated README, install guide and roadmap.
- Added mocked tests for healthy status, no-op recovery of a healthy listener, dead-pane respawn, no duplicate restart of a live supervisor, missing-window creation, and refusal/safe startup when the tmux session is absent.

### Validation boundaries

CI validates the scripts with mocked tmux/HTTP endpoints and synthetic panes; it does not run a real Termux session or llama.cpp server. The command prevents common duplicate-start cases but does not discover arbitrary processes that bind a different address/port. Android whole-app termination, real process ownership and low-memory recovery remain unverified.



## 2026-11-04 continuation checkpoint — service shutdown and launch contracts

**Validated code commit:** `cd522d27cb00ee01168cc4b0f46e6c337884f4ab`  
**GitHub Actions:** [run #314](https://github.com/somildholakia/UnAi/actions/runs/41824210661) — **339 tests passed in 12.84s**; Python compileall, Termux shell syntax checks and Web production build passed.  
**Integration PR:** https://github.com/somildholakia/UnAi/pull/28 — open and unmerged. Keep `main` unchanged.

### Changes

- `scripts/termux-supervise.sh` now tracks the active direct child and forwards `TERM`, `INT` and `HUP` before exiting with the corresponding signal status.
- A clean, signal-driven shutdown does not enter the automatic crash-retry loop.
- Added a supervisor regression test proving that TERM reaches the active child and the child can complete its shutdown handler.
- Added service-launcher tests for the exact llama.cpp/model command, Engine venv/uvicorn command, Web/Vite command, working directories, missing dependencies and unknown service names.
- Removed duplicate `termux-service.sh` and `termux-control.sh` entries from the workflow shell syntax command.

### Validation boundaries

- CI tests run against Linux fixtures and fake launch dependencies; they do not execute llama.cpp, the Engine or Vite on Android.
- The supervisor forwards signals to its direct child. This is not a guarantee that every descendant started by a wrapper process is terminated.
- No physical Android setup, offline inference, RSS/PSS, TTFT, throughput, battery or thermal measurements have been performed.

### Next development priority

Continue software engineering before mobile installation. The next major security milestone is a complete design for per-user/scoped credentials, revocation, rate limiting and tenant-isolated storage. Do not add an authentication facade and call it tenant isolation: the workspace, evidence ledger, Brain vault/index and repository graph cache must be reviewed together. Mobile installation and physical-device validation remain the final integration stage, after the remaining software work is complete.


## 2026-11-04 chat handoff — software-first continuation

**Current branch HEAD:** `a64e2f54bdbdff5b3422035e5cb48f673dc26cde` (documentation-only handoff commit after the validated code checkpoint below).  
**Validated code checkpoint:** `cd522d27cb00ee01168cc4b0f46e6c337884f4ab`.  
**Latest CI:** [run #315](https://github.com/somildholakia/UnAi/actions/runs/41824420044) — 339 tests passed in 12.54s; Engine compileall, Termux shell syntax checks and Web production build all passed. Run #315 validates the current HEAD and contains the same code as the validated code checkpoint.  
**PR #28:** open, unmerged, mergeable; base `main` SHA `fef00e16fbe721e7c7946aee89fcc8e44fb0e896`. **Do not merge or modify main.**

### User instruction — mandatory sequencing

The user explicitly wants all software development done first. Do not ask them to install, run, or test UnAi on their phone yet. Physical Android setup, offline inference, and device benchmarking are deferred to the final integration stage only.

### Current milestone

Termux process supervisor, health/status/recovery helpers, shared service launch contract, signal forwarding, and CI tests are in place. The code checkpoint includes:
- bounded restart/backoff and rotated per-service logs;
- safe status/recovery behavior designed not to duplicate healthy listeners;
- direct-child TERM/INT/HUP forwarding;
- tests for service commands, working directories, missing dependencies, supervisor restarts, and shutdown;
- CI workflow checks for Engine tests/compile, Termux shell syntax, and Web build.

No physical Android behavior or performance has been validated. Do not claim it has.

### Next software milestone

Begin with a full security/data-boundary review before implementing tenant auth:
1. workspace path resolution and task ownership;
2. task/evidence/patch/test/proof ledger read and write routes;
3. Brain vault files and SQLite index;
4. repository graph cache namespaces;
5. MCP server/remote bridge credentials and permissions.

Design scoped identities, token issuance/rotation/revocation, rate limits and storage isolation together. Add adversarial cross-tenant tests for every read/write path. A bearer token or scope field alone is not tenant isolation. Preserve loopback-only local operation; do not expose public hosting until security is complete and tested.

### Still blocked by external inputs

- No consented/licensed multi-repository training corpus has been supplied.
- No specialist adapters have been trained or released.
- Suitable GPU training, held-out comparisons and physical Android measurements remain pending.
- Do not fabricate data or claim trained weights.

### Start the next chat here

Read `docs/CONTINUITY.md`, `docs/CHECKPOINT.md`, `docs/ROADMAP.md`, then fetch the live branch/PR/CI state. Continue directly with the security/data-boundary audit and software implementation. Keep all work on `feat/m4-instance-methods-m5-specialists`; do not merge PR #28 or modify `main`. Mobile setup is last, only after software development is complete.


## 2026-11-04 security and tenant-isolation milestone — CI validated

**Repository:** https://github.com/somildholakia/UnAi  
**Branch:** `feat/m4-instance-methods-m5-specialists`  
**Validated code checkpoint:** `7371253c22c4cde6f2c64108c56f4086e5d4e62e`  
**Engine/Web Actions:** [run #322](https://github.com/somildholakia/UnAi/actions/runs/41828328808) — **356 Python tests passed in 13.98s**; Engine compilation, Termux shell syntax and Web production build passed.  
**Brain Worker Actions:** [run #5](https://github.com/somildholakia/UnAi/actions/runs/41828328703) — TypeScript/Worker checks and the SQLite D1 migration smoke test passed.  
**PR #28:** open, unmerged, mergeable. Base `main` remains `fef00e16fbe721e7c7946aee89fcc8e44fb0e896`. No main-branch write or PR merge was performed.

### Delivered

- Installation-admin and loopback-local identities remain restricted to the reserved `default` data area. Scoped API tokens are generated once, stored as SHA-256 hashes, bound to one tenant and explicit scopes, expire within 365 days, and can be revoked immediately.
- Engine requests are rate-limited per token and per source IP. Routes now enforce chat, task, workspace, execution and Brain scopes independently of task-level approvals/confirmations.
- Non-default tenants receive distinct workspace roots, Brain Markdown vault/index, SQLite evidence/task/patch ledger and Python code-graph cache. Symlink-resolved tenant roots are checked to remain under their configured roots.
- Adversarial tests show that a second tenant cannot read another tenant's Brain note or task/evidence and sees only its own repository graph; missing scopes and token-management attempts are denied.
- The hosted Brain Worker now fails closed for all API routes, supports admin-issued scoped credentials with expiry/revocation, stores only token hashes, rate-limits by token/IP, and tenant-filters notes, tags, links, search, graph and backlinks. D1 migration `0002_tenant_tokens.sql` preserves pre-existing records in the reserved `default` tenant.
- Worker CI applies migrations to a temporary SQLite database and verifies that legacy records remain in `default` and that token/rate-limit tables and tenant columns exist.
- Local MCP servers launch over stdio. Remote Engine/Brain bridges require HTTPS and explicit server-side bearer tokens. Invalid/corrupt token scope records fail closed. Configured hosted Worker secrets shorter than 32 characters are rejected.

### Important trust boundaries

- Application-level tenant isolation and token lifecycle are implemented and CI-tested. The hosted Worker has **not** been deployed; no D1 account, remote migration, production secrets, CORS origin, backup policy or live endpoint has been configured.
- This is not a public multi-tenant release certification or penetration test. Host networking/TLS, D1 account administration, secret rotation, disk encryption, operational quotas, backup/retention, dependency review and Docker sandbox hardening still require deployment review.
- Tokens deliberately share data when issued for the same tenant ID. Assign a unique tenant ID to each independently isolated person/workspace.
- CI is Linux/fake-service evidence. No physical Android setup, offline inference, memory, latency, throughput, battery, thermal or lifecycle measurement has been performed.

### Next software priority

Continue with the remaining M4 verification/trust backlog, beginning with the pending public-key transparency inclusion/consistency proofs (or an explicitly scoped organizational identity-discovery design). Do not call the current offline signed trust snapshots a public transparency log or legal identity proof. Continue M5 only with an explicitly consented/licensed multi-repository corpus and suitable training compute; no specialist adapter weights have been trained. Keep M6 phone setup and physical Android benchmarks as the final integration stage after software development is complete.

## 2026-11-04 M4 offline publisher-transparency verification — CI validated

**Validated code commit:** `75578f2bd222874b575005f1097f8f610afd471a`  
**Engine/Web Actions:** [run #329](https://github.com/somildholakia/UnAi/actions/runs/41830246493) — success; **356 tests passed in 13.09s**, Python compileall, all six Termux shell syntax checks and Web production build passed.  
**Brain Worker Actions:** [run #12](https://github.com/somildholakia/UnAi/actions/runs/41830246970) — success; Worker type checks, D1 migration/legacy-tenant smoke test and browser JavaScript syntax passed.  
**Branch:** `feat/m4-instance-methods-m5-specialists`  
**PR #28:** open, mergeable and unmerged. Base `main` remains `fef00e16fbe721e7c7946aee89fcc8e44fb0e896`.

### Delivered

- Added `unai_engine.verification.transparency` with RFC 6962-style Merkle leaf/node hashing and bounded inclusion/consistency proof verification.
- Added Ed25519 verification for canonical-JSON signed tree heads using operator-pinned log keys.
- Added canonical publisher-manifest leaf encoding and a helper that verifies exact manifest inclusion under a trusted signed tree head.
- Added a SQLite transparency observation ledger with append-only update/delete guards and monotonic per-log checkpoint checks. It rejects rollback, timestamp regression, same-size root equivocation, missing/invalid consistency proofs and untrusted signatures.
- Added regression coverage for left/right inclusion paths, wrong leaves/indices, proof tampering, signed tree heads, publisher-manifest binding, checkpoint growth/rollback and append-only storage.
- Documented the format and its trust boundaries in `docs/VERIFICATION.md`; updated the M4 roadmap.

### Explicit limitations

- The implementation verifies proofs supplied by a caller; it does not operate or discover a public log, fetch proofs, perform witness gossip, or establish publisher legal identity.
- The tree-head signature uses the documented unAI canonical-JSON envelope, not a claim of universal public-log wire compatibility.
- Existing publisher-oracle request/evidence flow is not yet wired to require transparency proofs. Key rotation/revocation and independent witness/checkpoint policy also remain open.
- CI is Linux automation, not Android runtime or performance validation. Mobile installation remains deferred until all software milestones are complete.

### Next software task

Integrate verified publisher-manifest inclusion and monotonic checkpoint acceptance into the publisher-oracle evidence workflow, preserving the existing separate reviewer-attestation semantics. Add route-level tamper, stale-checkpoint and cross-log tests before considering the M4 transparency integration complete. Continue the remaining bounded M4 verification backlog and M5 training only when an approved corpus and suitable compute are available. Do not begin phone setup yet.

### M4 transparency CI test-discovery correction — 2026-11-04

- The initial transparency regression file was placed under `apps/engine/tests`, which is not the directory invoked by the repository workflow (`pytest -q tests` from the repository root). The CI log therefore did not include those tests.
- Moved the suite to the active top-level `tests/test_transparency.py` directory and removed the inactive duplicate.
- **Latest validated code commit:** `0131ae54ec9f8bdd19635585d0d18c2c281dcf2a`.
- **Engine/Web Actions:** [run #335](https://github.com/somildholakia/UnAi/actions/runs/41830680030) — success; **363 tests passed in 13.37s**, compileall, all six Termux shell syntax checks and Web production build passed. The 363 count includes the transparency regression suite.
- **Brain Worker Actions:** [run #18](https://github.com/somildholakia/UnAi/actions/runs/41830680795) — success; Worker checks, D1 migration smoke test and browser JavaScript syntax passed.
- PR #28 remains open, mergeable and unmerged. Base `main` remains `fef00e16fbe721e7c7946aee89fcc8e44fb0e896`.
- Next: integrate the proof verifier and checkpoint store into publisher-oracle acceptance/evidence. Keep phone setup deferred until all software work is complete.


## 2026-11-04 M4 publisher-transparency integration — CI validated

**Validated code commit:** `1fd8df11144672471318e3db9df9e8fe0b28b92c` (`fix(m4): keep transparency proofs out of generated tests`).  
**Engine/Web Actions:** [run #344](https://github.com/somildholakia/UnAi/actions/runs/41833570092) — success; **367 tests passed in 14.91s**, Python compileall and Termux shell syntax checks passed; Web production build passed.  
**Brain Worker Actions:** [run #27](https://github.com/somildholakia/UnAi/actions/runs/41833570066) — success.  
**Branch:** `feat/m4-instance-methods-m5-specialists`.  
**PR #28:** open, unmerged. **main:** `fef00e16fbe721e7c7946aee89fcc8e44fb0e896`, unchanged.

### Delivered

- Wired publisher-manifest inclusion verification into grounded publisher-oracle acceptance.
- Require a trusted Ed25519 signed tree head and verify the exact canonical publisher manifest leaf against the supplied inclusion proof.
- Enforce per-log monotonic checkpoint acceptance with consistency proofs; reject rollback, timestamp regression, same-size equivocation and invalid/missing consistency material.
- Verify operator-pinned transparency log keys and validity/revocation windows.
- Verify independent witness signatures, distinct witness identities and configured minimum witness quorum.
- Persist accepted checkpoint observations append-only and attach tree-head, proof, witness and checkpoint provenance to publisher-oracle evidence.
- Preserve the distinct publisher-artifact signature and reviewer-assertion signature roles. Publisher and transparency signatures/proofs are evidence only and are removed from generated pytest input.
- Regression coverage in `tests/test_grounded.py` includes required transparency, successful evidence recording, tampered inclusion rejection before execution, cross-log key substitution and checkpoint rollback. `tests/test_transparency.py` covers Merkle inclusion/consistency and append-only checkpoint storage.
- Corrected the earlier stale handoff: the transparency integration is now implemented and CI-tested; it is no longer the next task.

### Trust boundaries

- Proofs are caller-supplied and verified offline; the Engine does not operate/discover a public log or fetch proofs.
- Operator-pinned log and witness keys, key lifecycle configuration and local checkpoint storage remain trust dependencies. Witness quorum reduces single-log trust but is not a universal consensus protocol.
- This does not establish publisher legal identity, certificate-chain authority or factual correctness of a specification. Reviewer attestation remains separate and optional/configuration-dependent.
- CI is Linux automation. Physical Android installation/performance and hosted Brain deployment remain unverified.

### Next software work

Continue the remaining bounded M4 verification backlog (external side-effect fixtures, deeper recursive shrinking policies, nested classes/signature expansion, and coverage/mutation-based verification only with explicit resource and sandbox policies). Continue M5 infrastructure hardening and evaluation tooling, but real adapter training remains blocked on an explicitly approved consented/licensed multi-repository corpus and suitable GPU compute. No weights have been trained or released. Keep phone setup deferred until all software development is complete.


## 2026-11-04 M4 nested-class and managed external-side-effect fixture checkpoint

**Nested-class code commit:** `4c0921c3faf9d608cf563b9641b54279b638e446`  
**External-fixture code commit:** `be4849df4bb4fe0bb3590a07519d2e70e5e4f51e`  
**Branch:** `feat/m4-instance-methods-m5-specialists`  
**PR #28:** open and unmerged. **main:** `fef00e16fbe721e7c7946aee89fcc8e44fb0e896`, unchanged.

### Nested class support

- Added one bounded nested namespace level using `Outer.Inner.method` for static, class and ordinary instance targets.
- Every class in the nested path must be directly declared in its parent namespace, undecorated, and have no bases or metaclass keywords. Deeper nesting, inheritance, decorated namespaces and dynamic resolution remain unsupported.
- The generated harness resolves each class path component explicitly. Ordinary instance methods continue to require JSON constructor fixtures, fresh instances and fixture-hash evidence.
- Regression tests execute nested static and instance method harnesses and reject nested inheritance.

### Managed external side-effect fixture

- Added the opt-in property `external_fixture_filesystem_unchanged`.
- Only requests containing this property cause DockerSandbox to add the dedicated `/unai_side_effects` tmpfs (16 MiB, `noexec`, `nosuid`, `nodev`) and set `UNAI_SIDE_EFFECTS_ROOT`. The container remains network-disabled, its root filesystem read-only, and the fixture is not a host-backed volume.
- The verifier snapshots the managed fixture before and after the target invocation, records manifest hashes and change counts (not raw paths), and never follows symlinks. Unlike the workspace audit, it excludes no directory names.
- Limits: 1,024 entries, 2 MiB per file and 16 MiB of aggregate file bytes across the request. Missing fixtures, unsupported entries, read races or exceeded limits produce an unavailable audit; a missing/invalid trace path also fails closed rather than yielding a pass.
- This is a persistent filesystem-state comparison for one managed temporary directory. It does not capture transient write-then-delete activity, or provide database, queue, host-file, network or remote-service fixtures. The separate `workspace_filesystem_unchanged` property is needed to check workspace state too.

### Validation

The exact code contents in commit `be4849d` passed the remote Linux CI-equivalent checks:

- **380 Python tests passed** (`PYTHONPATH=apps/engine/src:. pytest -q tests`, 32.24s).
- Python `compileall` passed for Engine source and training code.
- All six Termux scripts passed `bash -n`.
- Web TypeScript check and Vite production build passed.
- `git diff --check` passed.
- Focused sandbox/runner/grounded suite passed **117 tests** after the fail-closed trace-path regression was added.

The Docker command-line contract is covered with a mocked Docker invocation; generated-harness behavior is exercised with local temporary fixture directories. A live Docker-container integration run was not performed in this validation. GitHub Actions for code commit `be4849d` (Engine/Web run #349 and Brain Worker run #32) were queued at checkpoint recording time; do not represent them as completed.

### Remaining software work

Continue M4 with broader recursive shrinking only where each type has its own correctness/resource policy, and other decorators/signatures or coverage/mutation verification only with separately reviewed sandbox and execution budgets. M5 specialist adapters remain untrained: no explicitly approved consented/licensed multi-repository corpus or reproducible GPU training run has been supplied. Keep Android install, offline inference and physical-device benchmarks for the final integration phase.

## 2026-11-05 M4 bounded recursive-shrinking extension — CI validated

**Repository:** https://github.com/somildholakia/UnAi  
**Branch:** feat/m4-instance-methods-m5-specialists  
**Implementation commit:** 9ecb52918d0455b9bb71f40adaa65dc73554bd8e — depth/node budget expansion and three regression tests.  
**Test-correction commit:** cab14a827b2821445e566271ecbc89436c1d4465 — corrected the depth-boundary test to distinguish removed collection paths from changed values beyond the traversal limit.  
**Validated branch HEAD at test time:** cab14a827b2821445e566271ecbc89436c1d4465.  
**Main SHA:** fef00e16fbe721e7c7946aee89fcc8e44fb0e896 (unchanged).  
**PR #28:** open, mergeable and unmerged.

### Delivered

- Expanded recursive JSON input shrinking from four to eight container levels.
- Raised the deterministic traversal ceiling from 128 to 256 visited JSON nodes per argument.
- Preserved the existing 12-candidate-per-argument ceiling, 24 candidate executions and 6 isolated Docker rounds per request, and the 10 KB per-value oracle input cap.
- Kept deterministic per-type shrinking, strict recursive reduction ordering, JSON type integrity, source/specification freshness checks and Docker isolation.
- Added regression tests for the depth-8/depth-9 boundary, a hard visited-node ceiling, deterministic output, input immutability, strict reduction ordering and bool/int/float/string handling.

### CI evidence

- **Engine/Web Actions run #352:** success — https://github.com/somildholakia/UnAi/actions/runs/41986190439. The Engine suite passed **383 tests in 17.12s**; Python compileall and all six Termux shell syntax checks passed. The Web TypeScript check and Vite production build passed.
- **Brain Worker Actions run #35:** success — https://github.com/somildholakia/UnAi/actions/runs/41986190243. Worker checks, D1 migration smoke test and browser JavaScript syntax validation passed.
- The first run after implementation, Engine/Web run #351, exposed one incorrect assertion in the newly added depth-boundary test (382 passed, one failed). The test was corrected in cab14a8; run #352 passed on that exact validation HEAD.
- The previous code HEAD ae191d633d5414d211a35787dae459b50f2392ac had also completed Engine/Web run #350 (380 tests passed) and Worker run #33 successfully before this change.

### Limits and next task

- Values deeper than eight container levels are not traversed; bounded collection deletion may remove a path as one candidate. Supported nested JSON types remain unchanged; arbitrary objects and non-string mapping keys are not introduced.
- The result remains a smallest-observed failing input, not a global minimality proof. Exact-output failures and replay/filesystem divergence cases are not automatically shrunk.
- **Next M4 task:** review and safely expand decorator and Python signature support. Each accepted decorator/signature form must have explicit construction/binding/invocation semantics, fail-closed rejection for ambiguous cases, and generated-harness security/regression tests. Coverage-guided or mutation-based verification remains a later M4 item requiring fixed deterministic targets, isolation and hard execution budgets.
- M5 specialist weights remain untrained because no approved consented/licensed corpus and reproducible training artifacts exist. Android setup and physical-device validation remain deferred until the software roadmap is complete.

## 2026-11-05 M4 safe Python decorators and signature expansion — validation pending

- Feature branch: `feat/m4-instance-methods-m5-specialists`; base commit: `a04a9a848c224ff0826a2f9ecd9805d50ee58b31`.
- Added fail-closed support for the standard-library `typing.final` identity marker with direct, unshadowed module imports only. Decorator calls, unknown/custom decorators, alias shadowing and unsafe `staticmethod`/`classmethod` order are rejected.
- Added signature plans and fixed generated invocation for positional-only, positional-or-keyword and keyword-only parameters plus declared `*args` JSON lists and `**kwargs` JSON objects. Constructor fixture binding follows the same rules.
- Optional positional-only parameters may be omitted only as a trailing suffix. Non-empty `*args` requires explicit values for every positional slot. Variadic probes and shrinking are rejected because existing probe/shrink structures cannot safely preserve expanded calls.
- Added tests for alias/decorator safety, parameter container validation, positional-only holes, generated variadic module calls, generated variadic instance construction/method calls, and preflight rejection without a runner call.
- Focused/full validation and exact commit SHA are pending; do not treat this checkpoint as complete until GitHub Actions succeeds.

## 2026-11-05 M4 safe Python decorators and signature expansion — CI validated

- **Repository:** https://github.com/somildholakia/UnAi
- **Branch:** `feat/m4-instance-methods-m5-specialists`
- **Validated code/test HEAD:** `38c2b0b697f334d754ebf8fd573a4e7b6da0da09`
- **Starting checkpoint:** `a04a9a848c224ff0826a2f9ecd9805d50ee58b31`
- **PR #28:** open, mergeable and unmerged. **main:** `fef00e16fbe721e7c7946aee89fcc8e44fb0e896`, unchanged.

### Delivered

- Restricted decorator expansion to the standard-library `typing.final` identity marker with direct, unique module-scope imports. Reassigned, reused, late-bound or otherwise ambiguous aliases are rejected; decorator calls/custom decorators and unsafe stack order are rejected before target import. The built-in `staticmethod`/`classmethod` names must be unshadowed.
- Added signature plans for positional-only, positional-or-keyword, keyword-only, `*args` and `**kwargs` for module functions and static/class/instance methods, including synchronous instance constructor fixtures.
- Variadic containers are JSON-only: the declared vararg name maps to a list; the declared varkw name maps to a string-keyed object. Required parameters, positional-only default suffixes, variadic positional slots and explicit keyword collisions are preflight-validated.
- Generated harness invocation uses a fixed helper and server-derived signature plans; caller-controlled values stay in JSON and are not emitted into executable source. Fresh instances are constructed for every case/replay.
- Probe-linked and shrinking cases on variadic target/constructor signatures are rejected rather than partially binding or shrinking an incomplete call.

### Validation

- **Engine/Web Actions run #363:** success — [run details](https://github.com/somildholakia/UnAi/actions/runs/41994458856). **400 Python tests passed in 20.88s**. CI built `unai-python-test:latest`, the generated-harness Docker integration test passed, Python compileall and all six Termux shell syntax checks passed, and the Web dependency install/Vite production build passed.
- **Brain Worker Actions run #46:** success — [run details](https://github.com/somildholakia/UnAi/actions/runs/41994458829). Worker typecheck, D1 migration/legacy tenant smoke test and browser JavaScript syntax passed.
- Added generated-harness regression tests for async module functions decorated with `typing.final`, variadic static/class methods, variadic module functions and variadic instance constructors/methods, plus preflight rejection, unsafe decorators, malformed containers and positional-only gaps.
- Generated-harness regression tests execute in isolated temporary staging workspaces, and an additional end-to-end test now executes the variadic harness through the actual `IsolatedPythonTestRunner` and `DockerSandbox`. The CI job builds the repository sandbox image first; the Docker-backed test passed on run #363. No physical Android test is claimed by this checkpoint.

### Next incomplete M4 roadmap task

Add coverage-guided and mutation-based verification only with explicit resource/sandbox policies. Keep physical Android/Termux setup deferred until software roadmap work is complete.


## 2026-11-05 M4 bounded mutation-based test-strength verification — CI validated

**Repository:** https://github.com/somildholakia/UnAi
**Branch:** `feat/m4-instance-methods-m5-specialists`
**Implementation commit:** `49825e84cfafe7dccdaaf043b3a828f6f350a755` — mutation planner, verifier, API route, evidence/proof-bundle integration and tests.
**Validated code/test HEAD:** `81ed3576ab9c64b464c2d9d2bce3c518e9967fbc` — corrected the Docker integration fixture's oracle cases.
**Starting checkpoint:** `38c2b0b697f334d754ebf8fd573a4e7b6da0da09`
**PR #28:** open, mergeable, unmerged. **main:** `fef00e16fbe721e7c7946aee89fcc8e44fb0e896`, unchanged.

### Delivered

- Added `POST /api/tasks/{task_id}/mutation-tests`, separate from grounded tests, requiring the exact `run-grounded-mutation-tests` confirmation plus the existing task permissions and API scopes.
- Added a deterministic planner for one-token mutants inside the selected function or method body: comparison, boolean, arithmetic and bool/int/float/string literal changes. Candidates are ordered by source position, deduplicated by resulting source SHA-256, compile-checked and capped.
- The verifier runs the ordinary grounded suite as a baseline first. If the baseline does not pass, no mutants run. A pytest failure against a mutant is a kill; exit success is a survivor; sandbox unavailability is reported as unavailable.
- Each mutant is written only into the disposable Docker staging copy and bound to a regenerated harness with that mutant's source SHA-256. No caller-supplied command or path is accepted, and source overrides are restricted to the new mutation evidence source.
- Added append-only per-mutant evidence (`source=grounded-mutation-sandbox`) and a summary record (`kind=mutation`). Proof bundles expose `mutation_run_count`, `latest_mutation_summary`, `mutation_evidence` and a survivor risk.
- Budgets: at most 8 mutants and 9 Docker rounds per request (1 baseline + 8 mutants); 2 override files and 300,000 bytes per override. Non-deterministic replay/filesystem property cases are rejected, and pinned specification artifacts are re-read before mutant rounds.
- Added 21 mutation tests covering planning, runner override boundaries, baseline failure, kills/survivors/unavailable, stale source/artifact rejection, proof-bundle exposure and API confirmation.

### Validation

- Local: `pytest -q tests/test_mutation.py` -> **20 passed, 1 skipped** (Docker unavailable locally); full suite -> **419 passed, 2 skipped**; Python `compileall` and `git diff --check` passed.
- **Engine/Web Actions run #366:** success — https://github.com/somildholakia/UnAi/actions/runs/41999448683. **421 tests passed in 20.42s**; CI built `unai-python-test:latest` and executed the Docker-backed mutation integration test. Python compileall, all six Termux shell syntax checks and the Web production build passed.
- **Brain Worker Actions:** success — https://github.com/somildholakia/UnAi/actions/runs/41999448612. Worker checks, D1 migration/legacy tenant smoke test and browser JavaScript syntax passed.
- **Initial mutation run #365 failed one fixture assertion (420 passed, 1 failed):** the Docker test's supplied cases did not exercise the first two planned mutants, so both correctly survived. The fixture now includes the zero boundary case and asserts two kills with `mutation_score == 1.0`; run #366 passed on the corrected HEAD.

### Limits and next task

- Mutation scores cover only the selected target body, the fixed mutation allowlist and the supplied oracle cases. They are not line coverage, semantic correctness or a complete test-quality metric; equivalent mutants may survive.
- Nested functions, lambdas, classes, chained comparisons, annotations and docstrings are not mutated.
- **Next incomplete M4 task:** add coverage-guided verification with deterministic fixed targets, explicit candidate/time/memory budgets and the same sandbox/evidence boundaries.
- M5 still needs an approved consented/licensed multi-repository corpus and GPU training; no specialist weights, held-out model results or physical Android measurements are claimed. Keep Android setup deferred until the software roadmap is complete.

## 2026-11-05 M5 bounded corpus-streaming validation

**Bounded JSONL reader and streaming total-size enforcement:** code/test HEAD `6b6fca0c8c7401dae0f932c2bf2f2048c06b5c04`.
**Engine/Web Actions run #409:** success — **449 tests passed in 21.40s**; Docker sandbox build, Python compileall, all six Termux shell syntax checks and Web production build passed.
**Brain Worker Actions run #92:** success — Worker typecheck, D1 migration validation and browser JavaScript syntax passed.
**PR #28:** open and unmerged. **main:** `fef00e16fbe721e7c7946aee89fcc8e44fb0e896`, unchanged.

### Delivered

- Introduced a shared bounded JSONL line reader for the corpus preparer and read-only audit. It reads at most 1 MiB + 1 byte for a record chunk, detects oversized lines, and drains the rest of that line with bounded reads rather than allocating the whole record.
- The reader hashes every consumed byte. The audit's reported source byte count now comes from that same stream, so the digest and byte count describe exactly the bytes consumed.
- The 2 GiB cap is checked from the file size before reading and enforced again as bytes stream in. This covers files that grow after the initial stat.
- Added tests for oversized-line rejection/draining, exact source digest/byte counts and the read-only audit behavior. The initial red CI runs exposed malformed test line-ending fixtures; the fixtures now contain actual JSONL newline bytes and the current run #409 passed all tests.

### Boundaries

The audit remains a metadata-only structural preflight. It cannot independently authenticate consent/license claims or replay verification. A split-ready result means only that there are at least two repository/project groups for deterministic train/eval separation; it does not establish adequate training scale or quality.

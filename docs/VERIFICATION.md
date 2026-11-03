# Verification and adversarial-probe workflow

unAI M4 distinguishes three different things:

1. **Probe candidate:** an AST-derived input worth checking. It is unverified.
2. **Grounded oracle case:** a caller-supplied input and expected output with a user/specification reference.
3. **Counterexample:** an observed output mismatch against that supplied oracle on a specific source hash.

A mismatch is evidence against the current implementation *assuming the oracle is correct*. It is not proof that the specification itself is correct.

## 1. Generate unverified AST candidates

The probe endpoint is:

    POST /api/tasks/{task_id}/probes

Example request:

    {
      "file_path": "src/service.py",
      "symbol_name": "clamp",
      "limit": 24
    }

The task must have explicitly approved workspace_read. The endpoint reads a task-scoped file snapshot, computes SHA-256, and records candidates against that source fingerprint.

Supported patterns remain intentionally narrow:

- Numeric, string, boolean and None constants in simple comparisons.
- Values immediately below, equal to, and above numeric thresholds.
- String equality/ordering representatives.
- Falsy/truthy representatives for parameter truthiness.
- Members and one sentinel non-member for literal list/tuple/set membership.
- String or collection lengths around len(parameter) comparisons.
- if, while and assert condition nodes inside a selected function or method.

Candidates contain the function name, source line, source condition, parameter assignments and a rationale. They remain passed=null and executed=false. The probe generator does not infer expected outputs.

## 2. Attach an explicit oracle and execute a test

The grounded test endpoint is:

    POST /api/tasks/{task_id}/grounded-tests

It requires both workspace_read and run_tests to have been explicitly approved on the task, plus a separate exact confirmation value in the request.

Example request:

    {
      "confirm": "run-grounded-oracle-tests",
      "file_path": "src/service.py",
      "symbol_name": "clamp",
      "source_sha256": "<sha256 returned by the file/probe snapshot>",
      "cases": [
        {
          "case_id": "below-minimum",
          "arguments": {"value": 9},
          "expected_output": 0,
          "oracle_kind": "specification",
          "oracle_reference": "product-spec.md#clamp-lower-bound",
          "oracle_artifact_path": "docs/product-spec.md",
          "oracle_artifact_sha256": "<sha256 from the approved workspace file snapshot>",
          "probe_candidate_id": "probe-1"
        }
      ]
    }

Every case must include:

- A unique short case ID.
- Explicit keyword arguments for the selected function.
- An explicit JSON-compatible expected return value.
- oracle_kind of user or specification.
- A human-readable oracle_reference such as an acceptance criterion or specification section.
- Optionally, oracle_artifact_path and oracle_artifact_sha256 to bind a specification case to a UTF-8 Markdown/text document in the approved workspace. Obtain the fingerprint from the task file-snapshot endpoint; the verifier re-reads the file, rejects a mismatch before execution, and embeds a second hash check in the generated pytest harness so the disposable staged copy is checked before the target module is imported.
- Optionally, a probe_candidate_id to bind the case to a probe generated for the exact file, function and source hash.

When a probe ID is supplied, every input/value pair generated for that candidate must be preserved exactly (including JSON type). Additional required function arguments may be supplied in the same arguments object. Cases are rejected when they use unknown parameters or omit required parameters.

The target is re-read immediately before execution. If its SHA-256 differs from the request or the referenced probe, execution is rejected as stale. The verifier supports synchronous and async, undecorated module-level Python functions, plus explicitly decorated @staticmethod and @classmethod methods on top-level classes. Class targets use the exact ClassName.method symbol syntax; the generated harness resolves the class and bound method explicitly. For classmethods, the first bound class parameter is supplied by Python and is not exposed as a caller oracle argument. Async targets are awaited through asyncio.run inside the generated synchronous pytest case. Ordinary instance methods, nested classes, positional-only arguments, *args, **kwargs and all other decorators are rejected. Expected-exception cases are supported only through the fixed `raises_exception` property and the exception allowlist below; custom exception classes and arbitrary exception names remain unsupported.

A matching artifact hash proves the workspace document bytes match the supplied fingerprint; by itself it does not prove who authored or published the document. A specification case may additionally include an oracle attestation signed by a reviewer whose Ed25519 public key is configured by the Engine operator. The signature covers the task/workspace, target file/function/source hash, case ID, oracle reference, artifact path/hash, argument hash, expected-output/property hash, reviewer key ID and issuance time. Therefore the signature approves one exact test assertion and cannot be replayed for a different input, expected value, source version, artifact or task.

Configure trusted reviewer keys in the UNAI_ORACLE_TRUSTED_PUBLIC_KEYS setting as a JSON object mapping stable key IDs to base64-encoded 32-byte raw Ed25519 public keys. Keep private keys outside the Engine and workspace. Removing a key from this operator-managed keyring revokes trust in signatures from that key. An unknown key or invalid signature is rejected before oracle evidence is appended or tests execute. User oracles and un-attested specification references remain usable but visibly untrusted.

The reviewer signs the canonical UTF-8 JSON assertion produced by the Engine's build_oracle_attestation_statement and canonical_attestation_message helpers in unai_engine.verification.attestations. The assertion binds the exact case inputs, expected output or property, target source hash, workspace, task and pinned specification artifact hash. A valid signature authenticates approval by the configured key, not the truth of the requirement itself; trusted reviewers must inspect the specification and expected assertion before signing.

Example request field:

    "oracle_attestation": {
      "key_id": "reviewer-release",
      "issued_at": "2026-11-01T09:00:00Z",
      "signature_b64": "<base64 Ed25519 signature>"
    }

The signature is public verification material, not a secret. The Engine stores it with append-only oracle evidence and does not embed it into generated pytest input.

### Publisher-signed specification artifact

A specification case may additionally include an oracle_publisher_attestation from the publisher of the specification document. This is distinct from the reviewer attestation above: the publisher signs an artifact manifest (URI, revision and exact SHA-256 bytes), while the reviewer signs the derived test assertion (inputs and expected result/property).

Configure publisher trust roots with UNAI_ORACLE_TRUSTED_PUBLISHER_KEYS as a JSON object mapping stable key IDs to entries:

    {
      "acme-specs-2026": {
        "public_key_b64": "<base64 raw 32-byte Ed25519 public key>",
        "issuer": "Acme Specifications",
        "allowed_domains": ["specs.acme.example"]
      }
    }

The Engine checks the signature key ID, exact configured issuer identity, exact allowed HTTPS hostname and artifact bytes. Hostnames are exact matches, not wildcards. Publisher artifact URIs must use HTTPS without credentials, query strings, fragments or non-standard ports, and must use a DNS hostname rather than an IP address. The local Markdown/text artifact must still match oracle_artifact_sha256 before a publisher manifest is accepted.

Add this field to the oracle case:

    "oracle_publisher_attestation": {
      "key_id": "acme-specs-2026",
      "issuer": "Acme Specifications",
      "artifact_uri": "https://specs.acme.example/releases/2026-11/product-spec.md",
      "artifact_revision": "release-2026.11",
      "issued_at": "2026-11-01T09:00:00Z",
      "signature_b64": "<base64 Ed25519 signature>"
    }

The publisher signs canonical UTF-8 JSON from build_publisher_artifact_statement and canonical_attestation_message in unai_engine.verification.attestations. The manifest schema is unai.spec-publisher-manifest.v1 and binds the key ID, issuer, issuance time, HTTPS artifact URI, artifact revision and verified artifact SHA-256. The signature proves that the configured publisher key signed those exact bytes and metadata; it does not prove the document is factually correct or that a derived test assertion is the right interpretation.

Key rotation is operator-managed. Multiple key IDs may share one issuer. Add a replacement key ID and public key before switching the publisher, then retain the old key with a not_after issuance cutoff for historical verification. Optional not_before and not_after fields are timezone-aware ISO-8601 timestamps and bound the attestation's signed issued_at time. Setting revoked to true immediately rejects all signatures from that key, including historical ones; removing the key ID also revokes trust. Keep retired keys only for as long as historical re-verification is required. The publisher keyring and reviewer keyring are independent.

The expected values and request fields are serialized as JSON data embedded in a fixed generated pytest template. Caller text is never interpolated as Python statements. For async targets the fixed harness imports asyncio and invokes the coroutine with asyncio.run; it does not accept caller-provided event-loop code or commands. The generated test is placed in unai_generated_tests/ inside the disposable filtered copy only; it is not written to the original workspace. The existing fixed command python -m pytest -q is executed through Docker with networking disabled and the existing CPU, memory, PID and timeout limits. There is no host-shell fallback.

### Operator-rooted publisher trust-bundle snapshots

For deployments that manage many publisher keys, the Engine can load one
offline signed trust-bundle snapshot instead of the direct
`UNAI_ORACLE_TRUSTED_PUBLISHER_KEYS` registry. Configure an operator-pinned
Ed25519 root keyring and the signed snapshot:

```dotenv
UNAI_ORACLE_TRUSTED_PUBLISHER_KEYS={}
UNAI_ORACLE_PUBLISHER_TRUST_ROOTS={"offline-root-2026":"<base64 raw 32-byte Ed25519 public key>"}
UNAI_ORACLE_PUBLISHER_TRUST_BUNDLE=<compact JSON envelope>
UNAI_ORACLE_PUBLISHER_TRUST_MIN_SEQUENCE=42
```

The envelope contains exactly `payload` and `signature_b64`. The root signs
the canonical UTF-8 JSON payload (sorted keys, compact separators, no NaN)
with Ed25519. The payload schema is `unai.publisher-trust-bundle.v1` and
contains exactly:

- `schema`, `root_key_id`, positive 64-bit `sequence`,
  timezone-aware `issued_at` and `expires_at`, and a `publishers` object.
- Each publisher entry contains a stable key ID, raw 32-byte Ed25519 public key
  in base64, issuer label, exact DNS `allowed_domains`, and optional
  timezone-aware `not_before`/`not_after` plus a boolean `revoked`.

The verifier rejects duplicate JSON members, unsupported fields, invalid keys,
wildcard/localhost/IP-literal domains, more than 256 publishers, bundles larger than
256 KiB, expired/future-issued bundles, validity periods longer than 30 days,
untrusted root IDs, invalid signatures and sequences below the configured
minimum. The verified bundle's sequence, root key ID, expiry and canonical
payload digest are included in publisher-attestation provenance in the
append-only oracle evidence.

Choose exactly one configuration mode: direct publisher keys or a signed
bundle. The direct keyring remains supported for backward compatibility.
Rollback protection is operator-managed: raise
`UNAI_ORACLE_PUBLISHER_TRUST_MIN_SEQUENCE` to the last accepted sequence when
advancing the snapshot. The Engine does not persist that floor automatically,
fetch bundles, discover roots, query a transparency log, validate certificate
chains or establish an issuer's legal identity. The root key and minimum
sequence must be provisioned through a trusted operator channel. A valid root
signature authenticates the exact key snapshot, not the factual correctness
of publisher metadata, a specification, or a derived test assertion.

### Property-oracle case

An exact oracle case supplies `expected_output`. A property-oracle case omits `expected_output` and supplies a fixed `oracle_property` object instead. The two forms are mutually exclusive.

Example:

    {
      "case_id": "non-negative-result",
      "arguments": {"value": 9},
      "oracle_property": {"kind": "output_non_negative"},
      "oracle_kind": "user",
      "oracle_reference": "User supplied non-negative result requirement"
    }

The supported, non-executable property kinds are:

- `output_non_negative`: return a finite integer/float (not bool) greater than or equal to zero.
- `output_positive`: return a finite integer/float (not bool) greater than zero.
- `output_truthy` / `output_falsy`: satisfy Python truthiness / falsiness.
- `output_is_none` / `output_not_none`: be None / not None.
- `output_is_empty` / `output_not_empty`: be an empty / non-empty string, list or dict.
- `output_equals_argument`: strictly match one named input argument in JSON type and value; set `argument_name`.
- `raises_exception`: require the function to raise exactly the selected allowlisted exception class; set `exception_name`.
- `output_stable_across_replays`: invoke the same target 2–4 times in the same imported module process with a fresh deep copy of the identical JSON arguments for each invocation. Every return must be a native JSON value with the same strict type/value as the first; any exception, non-JSON return or differing output is a counterexample. Set `replay_count` to 2–4.
- `workspace_filesystem_unchanged`: invoke the target once and require its disposable workspace filesystem to remain unchanged during that call. The audit detects added, removed or modified files/directories and symlinks; file contents and symlink targets are compared by hashes. It does not inspect external paths or other external side effects.

No caller-supplied Python expression, lambda, module, or command is evaluated as an oracle. The property contract is serialized as JSON and evaluated by fixed generated helper code. For `raises_exception`, only the selected exact exception type passes. A different exception type or a normal return emits a counterexample marker. For every other property kind, any function exception remains an unexpected counterexample.

Replay checks are explicitly opt-in and capped at 24 total function invocations across all replay cases in one request. Each case is limited to four invocations. The module is imported once so module-level state persists between calls, while each call receives a fresh copy of the supplied JSON arguments. This separates global/process state changes from in-place mutation of the input object. Async functions are still called through the fixed asyncio runner. The generated harness records only replay indices, result types, canonical JSON hashes and bounded exception hashes/source locations; it does not write raw return values to the replay trace file. The trace is size-limited and loaded from the disposable Docker workspace into append-only evidence. Replay-stability failures are not automatically shrunk because changing the input would change the replay question. This check detects only state changes observable in return values or exceptions; it is not a snapshot of arbitrary filesystem or external side effects, and the sandbox has no network access.
 

The workspace filesystem audit is also opt-in and runs once per case. It compares a deterministic manifest before and after the target call inside the disposable Docker workspace. The manifest includes regular-file content hashes, directory entries and symlink-target hashes; it never follows symlinks and never stores raw paths in the trace. Cache directories generated by pytest/Python are excluded. The scan is capped at 1,024 entries, 2 MiB per file and 16 MiB of total file bytes across the request. An unsupported entry, read race or exceeded budget records an explicit unavailable audit and the overall run is marked unavailable rather than passed. A detected workspace mutation emits a source-hash-bound counterexample with before/after manifest hashes, changed-entry count and a digest of changed paths. This proves only that the filtered workspace changed during the invocation; it does not inspect paths outside the workspace, network calls, external services or kernel-level state.

The exception allowlist is `ValueError`, `TypeError`, `KeyError`, `IndexError`, `ZeroDivisionError`, `OverflowError`, `AssertionError`, `RuntimeError`, `LookupError`, `FileNotFoundError`, `PermissionError`, and `TimeoutError`. No module import, lookup or dynamic class construction is driven by caller text.

## 2A. Bounded property-oracle shrinking

Shrinking runs only after a sandboxed initial run finds a counterexample for a property-oracle case. Exact expected-output failures are never shrunk because their exact expected output is not assumed to apply to changed inputs.

The shrinker supports non-zero integers, finite floats, non-empty strings, lists and string-keyed dictionaries at the top level or nested inside JSON-compatible arguments. It walks at most 128 JSON nodes and four container levels per argument, emits at most 12 deterministic candidates per argument, and changes one value or removes one contiguous collection chunk per candidate. Integer/float candidates move toward zero; string candidates remove bounded contiguous chunks; list/dictionary candidates remove bounded contiguous element/key chunks, including the empty collection. Nested booleans, null and non-finite floats are never mutated; non-string mapping keys are not traversed. Candidates change one path per trial and are ordered by a recursive reduction measure plus canonical JSON tie-breaker in a disposable Docker pytest staging copy. When one or more candidates still violate the same property, the smallest observed failing candidate becomes the base for the next round. This greedy process can cumulatively reduce multiple values over successive rounds. Decimal-based numeric measures avoid overflow when comparing large finite values.

A single request is capped at 24 candidate executions and 6 isolated Docker rounds across all counterexamples. Per-argument candidate generation is capped at 12, recursive traversal at 128 visited nodes and depth 4. The target source and any pinned specification artifact are rechecked before every staging operation and again in the generated harness. A candidate is retained only when the same fixed property still emits a counterexample marker. The response returns the smallest observed failing argument object, its hash, total attempt count, round history and status. Counterexample evidence also records a canonical JSON result hash when available, a hash of exception-message bytes, and the target source line/function for exceptions; raw previews remain bounded and redacted. These fields improve diagnosis but do not provide a complete process replay. This is not a proof of global minimality. Booleans, null, NaN/infinity and arbitrary mapping key types are not reduced. Shrink execution, hashes and outcome metadata are kept in separate append-only shrink evidence; shrink runs cannot overwrite the original grounded-test status.

## 3. Counterexamples and execution traces

The generated test compares return values using strict recursive JSON type/value equality. On a mismatch or unexpected exception, it emits a case-ID marker. The verifier joins that marker to the supplied oracle record and appends a counterexample evidence event.

The execution evidence stores:

- Target file/function and source SHA-256.
- Generated test SHA-256 and generated temporary path.
- Case IDs and oracle references (not raw argument or expected-output values).
- Fixed command, exit code, timeout and sandbox status.
- SHA-256 fingerprints of captured stdout and stderr.

A separate trace evidence event stores bounded redacted output previews and a trace fingerprint. The ledger intentionally does not persist raw case inputs or expected outputs; their hashes and references are retained. Do not include secrets in test arguments or expected values. Preview redaction covers common token/password formats but is not a guarantee of secret detection.

Statuses are distinct:

- passed: pytest completed successfully.
- failed: pytest completed with test failures or another non-zero result.
- unavailable: Docker, the test image or workspace staging could not execute the tests.

A response reports a counterexample only when the generated marker is found in the captured preview. A failed test run without a marker is a failed/inconclusive execution, not a fabricated counterexample.

## 4. Proof bundle

Retrieve the structured bundle with:

    GET /api/tasks/{task_id}/proof-bundle

The bundle combines the existing patch/test freshness summary with the grounded oracle records, counterexamples, bounded shrink evidence, execution traces, unverified probe counts, unresolved risks and limitations. A grounded test run is also included in the existing /proof test-event sequence.

The local read-only task MCP server exposes task_get_proof_bundle for model-side inspection. It does not expose grounded-test execution, patch application, arbitrary commands or permission grants. An MCP host can read evidence, but an explicit caller must invoke the HTTP execution endpoint.

## What M4 does not claim

- An oracle reference is requester-supplied; unAI does not fetch/authenticate a specification or establish that the expected output is correct.
- Passing supplied cases validates only those concrete inputs and outputs. It does not prove general correctness or complete acceptance criteria.
- The test process executes the selected project's Python module. Docker isolation limits its access; it is not a formal sandbox certification.
- The filtered workspace may omit dependencies, assets or environment state. These conditions can make a run unavailable or inconclusive.
- Captured trace hashes/previews and workspace manifests are bounded diagnostic evidence, not a deterministic replay or a complete snapshot of external process state.
- No automatic counterexample shrinking, symbolic execution, coverage-guided fuzzing, independent model review or multi-language oracle execution is implemented yet.

The intended progression is evidence first: retain the source hash, oracle provenance, execution trace and unresolved assumptions rather than replacing them with a confidence score.

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
- Optionally, a probe_candidate_id to bind the case to a probe generated for the exact file, function and source hash.

When a probe ID is supplied, every input/value pair generated for that candidate must be preserved exactly (including JSON type). Additional required function arguments may be supplied in the same arguments object. Cases are rejected when they use unknown parameters or omit required parameters.

The target is re-read immediately before execution. If its SHA-256 differs from the request or the referenced probe, execution is rejected as stale. The initial implementation supports only synchronous, undecorated, module-level Python functions with keyword-addressable parameters; positional-only arguments, *args, **kwargs, methods, async functions, decorated functions and exception-as-expected-result cases are not supported.

The expected values and request fields are serialized as JSON data embedded in a fixed generated pytest template. Caller text is never interpolated as Python statements. The generated test is placed in unai_generated_tests/ inside the disposable filtered copy only; it is not written to the original workspace. The existing fixed command python -m pytest -q is executed through Docker with networking disabled and the existing CPU, memory, PID and timeout limits. There is no host-shell fallback.

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

The bundle combines the existing patch/test freshness summary with the grounded oracle records, counterexamples, execution traces, unverified probe counts, unresolved risks and limitations. A grounded test run is also included in the existing /proof test-event sequence.

The local read-only task MCP server exposes task_get_proof_bundle for model-side inspection. It does not expose grounded-test execution, patch application, arbitrary commands or permission grants. An MCP host can read evidence, but an explicit caller must invoke the HTTP execution endpoint.

## What M4 does not claim

- An oracle reference is requester-supplied; unAI does not fetch/authenticate a specification or establish that the expected output is correct.
- Passing supplied cases validates only those concrete inputs and outputs. It does not prove general correctness or complete acceptance criteria.
- The test process executes the selected project's Python module. Docker isolation limits its access; it is not a formal sandbox certification.
- The filtered workspace may omit dependencies, assets or environment state. These conditions can make a run unavailable or inconclusive.
- Captured trace hashes/previews are not a deterministic replay of process state.
- No automatic counterexample shrinking, symbolic execution, coverage-guided fuzzing, independent model review or multi-language oracle execution is implemented yet.

The intended progression is evidence first: retain the source hash, oracle provenance, execution trace and unresolved assumptions rather than replacing them with a confidence score.

# Task compiler

The task compiler is an intentionally deterministic first pass at M2. It is exposed as:

    POST /api/tasks/compile
    Content-Type: application/json

Example request:

    {
      "request": "Fix the login crash and add a regression test",
      "constraints": ["Keep the existing API response shape"],
      "permissions": ["workspace_read"]
    }

The response includes a task ID, normalized objective, heuristic task category,
preserved constraints, provisional assumptions and unknowns, acceptance checks,
a bounded sequence of typed plan steps, and permission accounting.

The permissions field means permissions the caller explicitly approved. The compiler
does not automatically grant anything. required_permissions describes what the
generated plan would need; missing_permissions is the difference. This endpoint
only compiles a plan: it does not read a workspace, modify files, execute tests,
install packages, access the network, or run shell commands.

## Current boundaries

- Classification uses transparent keyword rules and can be wrong.
- Acceptance checks are generic baselines, not task-specific proof.
- Constraints are caller-supplied; the compiler does not claim to reliably extract
  them from arbitrary prose.
- Unknowns are heuristic warnings, not an exhaustive ambiguity detector.
- A future execution layer must enforce permissions itself. The contract is not an
  authorization token and cannot replace sandbox or policy checks.


## Inspection API

Request:

    POST /api/tasks/inspect

Example:

    {
      "request": "Fix calculate_total and add a regression test",
      "workspace": "my-project",
      "permissions": ["workspace_read"]
    }

The route returns a task contract, selected graph summary, ranked symbol references
with line ranges, parse diagnostics, evidence records, and a next-gate status.
Workspace reads are skipped unless workspace_read is explicitly approved. The API
persists a snapshot in the SQLite evidence ledger; fetch it later with:

    GET /api/tasks/{task_id}/evidence

Set UNAI_EVIDENCE_DB_PATH to move the ledger. Evidence rows are append-only through
database triggers. The ledger is an audit aid, not a cryptographic tamper-proof log.

This inspection stage does not edit files, run tests, install packages, access the
network, or execute shell commands. Approving workspace_read is not approval for
those operations. Separately confirmed Python test execution is described below;
file editing, package installation and arbitrary command execution remain disabled.


## Explicitly confirmed Python tests

The inspection contract must contain both workspace_read and run_tests in its
explicit permissions list. Then call:

    POST /api/tasks/{task_id}/run-tests
    Content-Type: application/json

    {"confirm": "run-python-tests"}

The route exposes one fixed command: python -m pytest -q. There is no free-form
shell command parameter. The engine stages a temporary copy of selected Python
source/config files, excludes common secret and generated directories/files, and
runs only that copy inside Docker with networking disabled and resource limits.
It appends an execution evidence record containing the exit code, timeout flag,
staging summary, and stdout/stderr hashes. Output previews are redacted.

Build the pytest-enabled image as described in apps/engine/SANDBOX.md and set
UNAI_DOCKER_IMAGE to that image. If Docker or pytest is unavailable, the result is
unavailable; it is never treated as a passing test. Project-specific dependencies
must be baked into a reviewed sandbox image before running tests. The original
workspace is not mounted into the test container.


## Read-only source snapshots and approved patch replacement

After inspection, read one eligible UTF-8 source file:

    GET /api/tasks/{task_id}/files/{file_path}

The task must have explicit workspace_read approval. The response returns the file
content, byte length and SHA-256 digest. File reads append a discovery record with
the digest to the task evidence ledger.

Create a single-file replacement proposal:

    POST /api/tasks/{task_id}/patch-proposals

    {
      "file_path": "src/service.py",
      "expected_sha256": "<sha256-from-file-snapshot>",
      "proposed_content": "<complete new file content>",
      "description": "Explain the intended change"
    }

The inspected task must explicitly approve workspace_read and workspace_write.
Tasks classified as requiring destructive_changes also need that permission.
The proposal is saved in SQLite and a unified diff preview is returned; no file is
changed during proposal creation. Patch targets must already exist, be eligible
text files, not be symlinks or sensitive paths, and be at most 1 MB.

Apply only after a separate approval action:

    POST /api/tasks/{task_id}/patch-proposals/{proposal_id}/apply

    {"confirm": "apply-patch"}

Before replacement, the engine re-checks that the live file still matches the
proposal's base SHA-256 and that the stored proposal content matches its recorded
digest. A changed base returns HTTP 409 instead of overwriting the user's edit.
The accepted replacement is made through a temporary file in the same directory
and an atomic rename. Apply evidence is appended to the ledger.

This is a single-file replacement interface, not a general shell or git tool. It
does not create/delete files or run tests automatically. Run tests through the
separately confirmed Docker test endpoint. The current hash check is an optimistic
concurrency guard, not a cryptographic proof against a privileged local attacker.

The HTTP workflow endpoints are not yet exposed as native tool calls to the chat
model; an external client or UI must call them explicitly. The API currently has
no request authentication. Keep Uvicorn bound to loopback and do not expose it
directly to a network until an authentication layer and deployment threat model
are in place.


## Evidence-sequence summary

    GET /api/tasks/{task_id}/proof

The response joins applied-patch evidence with the latest Docker test event and
the Python parse-quality result. It distinguishes pending proposals, applied
patches with no later test, failed/unavailable test runs, and a passing test run
that occurred after the latest applied patch. A later patch makes an older test
result stale for this summary.

The status tests_passed_after_patch does not mean the task is fully correct. It
only means a recorded test run passed after the latest applied patch and the
recorded parse-quality check did not fail. The response includes generic acceptance
checks, remaining compiler unknowns, evidence IDs, and limitations. Adversarial
counterexample generation, independent semantic review, and criterion-specific
verification remain future work.


## Post-change quality refresh

    POST /api/tasks/{task_id}/quality-check

This endpoint requires explicit workspace_read permission. It rebuilds the bounded
Python AST graph for the inspected workspace and appends a new parse-quality event.
After a patch is applied, the old parse-quality event is stale; refresh it before
the proof summary can describe quality as current.

## AST boundary-probe candidates (M4 prototype)

    POST /api/tasks/{task_id}/probes

    {
      "file_path": "src/service.py",
      "symbol_name": "clamp",
      "limit": 24
    }

The task must have workspace_read approval. The selected source file is read through
the task-scoped file snapshot path and its SHA-256 digest is attached to the evidence.
Function names may be qualified (for example, Client.send); an ambiguous short name
is rejected. This endpoint currently supports Python only.

The generator considers simple branch conditions in if, while and assert nodes.
It proposes boundary values around numeric/string/boolean/None comparisons,
truthy/falsy representatives, list membership literals, and string/collection
lengths around len(parameter) comparisons. It returns the source condition,
source line, parameter assignment, and rationale for each candidate.

Every candidate is marked as unverified. The engine does not execute these inputs,
infer expected outputs, or claim that any candidate is a bug. Complex expressions,
type compatibility beyond basic annotations, side effects, fixtures, and environment
state are not modeled. See docs/VERIFICATION.md.

## Proof freshness rule

The proof endpoint requires a test run newer than the latest applied patch.
It also treats parse-quality evidence as fresh only when a quality-check event
occurred after the latest patch. Thus a passing test result with stale or missing
quality evidence is reported as tests_passed_but_quality_check_unknown rather than
being promoted to a clean status. Even a clean status is not a semantic correctness
certificate.

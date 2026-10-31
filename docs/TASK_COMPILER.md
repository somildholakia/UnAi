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

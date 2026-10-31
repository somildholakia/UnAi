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

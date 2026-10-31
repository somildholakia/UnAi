# Verification and adversarial-probe status

## Implemented now

The current first M4 component is a conservative Python AST boundary-probe
generator exposed as:

    POST /api/tasks/{task_id}/probes

Example:

    {
      "file_path": "src/service.py",
      "symbol_name": "clamp",
      "limit": 24
    }

The task must have explicitly approved workspace_read. The endpoint reads the
source through the task-scoped file snapshot mechanism, computes SHA-256, and
records a probe evidence event against that source fingerprint.

Supported patterns are intentionally narrow:

- Numeric, string, boolean and None constants in simple comparisons.
- Values immediately below, equal to, and above numeric thresholds.
- String equality/ordering representatives.
- Falsy/truthy representatives when a branch depends on a parameter's truthiness.
- Members and one sentinel non-member for literal list/tuple/set membership checks.
- String or collection lengths immediately below, equal to, and above
  len(parameter) comparisons.
- if, while, and assert condition nodes inside a selected function or method.

Candidates contain the function name, source line, source condition, parameter
assignments and a short rationale. A plain function name must identify exactly one
function; otherwise use a qualified name such as Client.send. The endpoint only
supports .py files.

## What it does not do

- It does not execute a candidate input.
- It does not infer an expected output or judge whether a result is correct.
- It does not claim to have discovered a bug or a true counterexample.
- It does not understand arbitrary types, annotations, fixtures, mocks, global
  state, I/O side effects, async scheduling, database contents or environment state.
- It does not automatically turn candidates into pytest tests.
- It does not provide a verifier independent from the code or model that proposed
  a patch.

All generated candidates are recorded with passed=null and executed=false. A parse
failure is recorded separately as a failed probe-generation event.

## Post-patch quality and proof freshness

After applying a patch, request a new parse-quality event:

    POST /api/tasks/{task_id}/quality-check

This requires workspace_read permission, rebuilds the bounded Python graph, and
records the current parse-error count. A quality event created before the latest
applied patch is stale.

The proof summary endpoint is:

    GET /api/tasks/{task_id}/proof

It requires a test event newer than the latest applied patch. It also requires a
quality event newer than the latest applied patch to report quality as fresh.
A passing test run with stale/missing quality evidence is reported as
tests_passed_but_quality_check_unknown. A clean result is still not semantic proof:
the acceptance criteria may be incomplete and the tests may not cover the defect.

## Next M4 work

1. Turn selected candidates into explicit, reproducible test cases with expected
   outcomes supplied by the task contract or user—not guessed from branch code.
2. Execute only in the approved disposable Docker workspace.
3. Preserve failure traces and shrink failing inputs without hiding assumptions.
4. Compare candidate and patch behavior, generate discriminating regressions, and
   seek independent verification.
5. Keep unresolved claims and skipped checks in the evidence bundle.

The current system is a probe-input suggester and evidence recorder, not a full
adversarial verifier.

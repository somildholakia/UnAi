#!/usr/bin/env python3
"""Generate deterministic, first-party synthetic specialist trajectories.

The generated examples are deliberately labeled synthetic. Their verification
checks validate the generator's own decision contracts; they do not represent
real repository work or independently measured model capability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any

GENERATOR = "training.collect_synthetic"
GENERATOR_VERSION = "1.0.0"
SPECIALISTS = ("next_action", "planner", "verifier")
_ALLOWED_OUTPUT_LICENSES = {"CC0-1.0", "MIT", "Apache-2.0", "BSD-3-Clause"}

_NEXT_ACTIONS = (
    {
        "condition": "workspace_read_approval_missing",
        "decision": "request_approval",
        "approval": "workspace_read",
        "action": "Request explicit workspace_read approval before inspecting project files.",
        "reason": "Inspection is not permitted until the task-level read approval is granted.",
    },
    {
        "condition": "source_fingerprint_is_stale",
        "decision": "refresh_source",
        "approval": None,
        "action": "Re-read the selected source and regenerate source-bound probes and evidence.",
        "reason": "The previous source fingerprint no longer identifies the current file bytes.",
    },
    {
        "condition": "oracle_expectation_missing",
        "decision": "request_oracle",
        "approval": None,
        "action": "Ask the requester for an expected result and a human-readable oracle reference.",
        "reason": "Expected outputs must come from an explicit oracle, not the implementation under test.",
    },
    {
        "condition": "patch_confirmation_missing",
        "decision": "request_patch_confirmation",
        "approval": "apply_patch",
        "action": "Present the hash-bound one-file patch and request its separate exact confirmation.",
        "reason": "A proposed patch is not permission to apply it.",
    },
    {
        "condition": "post_patch_tests_not_run",
        "decision": "request_test_approval",
        "approval": "run_tests",
        "action": "Request the task-level test approval and exact run confirmation, then use the fixed Docker pytest runner.",
        "reason": "Tests must run after the latest patch in the disposable staging copy.",
    },
    {
        "condition": "acceptance_tests_failed",
        "decision": "report_failure",
        "approval": None,
        "action": "Report the failing checks with their evidence and do not mark the task complete.",
        "reason": "A failing acceptance check is a concrete unresolved result.",
    },
    {
        "condition": "test_runner_unavailable",
        "decision": "report_unavailable",
        "approval": None,
        "action": "Report verification as unavailable and repair the approved runner environment before retrying.",
        "reason": "An unavailable test run is neither a pass nor a failure of the target.",
    },
    {
        "condition": "fresh_tests_passed",
        "decision": "summarize_evidence",
        "approval": None,
        "action": "Summarize the exact checks that passed and retain the remaining acceptance gaps.",
        "reason": "Passing supplied checks is evidence about those checks, not proof of general correctness.",
    },
)

_PLANS = (
    {
        "kind": "bounded_api_change",
        "request": "Add a read-only endpoint that returns a bounded summary for one task.",
        "scope": "one API route and its focused tests",
        "checks": ["reject unknown task IDs", "cap returned fields and bytes", "prove the endpoint does not mutate task state"],
        "approvals": ["workspace_read", "run_tests"],
    },
    {
        "kind": "regression_fix",
        "request": "Fix a reported off-by-one defect without changing unrelated behavior.",
        "scope": "the selected function and its regression test",
        "checks": ["reproduce the boundary defect", "pass the new boundary test", "pass the existing focused suite"],
        "approvals": ["workspace_read", "run_tests", "apply_patch"],
    },
    {
        "kind": "documentation_update",
        "request": "Document the supported input format and the explicit fail-closed cases.",
        "scope": "one documentation page and link validation",
        "checks": ["examples match current schemas", "unsupported behavior is stated", "no secrets or private identifiers appear"],
        "approvals": ["workspace_read"],
    },
    {
        "kind": "data_pipeline",
        "request": "Make a local JSONL preparation command reject malformed and duplicated records.",
        "scope": "the data-preparation module and focused unit tests",
        "checks": ["malformed records are counted and skipped", "duplicates are deterministic", "input bytes are hashed exactly"],
        "approvals": ["workspace_read", "run_tests"],
    },
    {
        "kind": "security_boundary",
        "request": "Harden a permission boundary and preserve its fail-closed behavior.",
        "scope": "one security-sensitive boundary and negative tests",
        "checks": ["missing approval is rejected", "stale evidence is rejected", "existing authorized flow remains valid"],
        "approvals": ["workspace_read", "run_tests"],
    },
    {
        "kind": "test_quality",
        "request": "Add tests that detect a plausible mutation in a selected function.",
        "scope": "the selected target and its bounded mutation tests",
        "checks": ["grounded baseline passes", "mutation count stays within the declared cap", "survivors are reported"],
        "approvals": ["workspace_read", "run_tests"],
    },
)

_VERIFIER_CASES = (
    {
        "condition": "fresh_all_checks_pass",
        "test_status": "passed",
        "test_after_latest_patch": True,
        "all_acceptance_checks_pass": True,
        "evidence_available": True,
        "verdict": "passed",
        "reason": "All supplied acceptance checks passed in a test run newer than the latest patch.",
        "next_action": "Report the scoped pass and list any acceptance gaps that were not tested.",
    },
    {
        "condition": "one_acceptance_check_fails",
        "test_status": "failed",
        "test_after_latest_patch": True,
        "all_acceptance_checks_pass": False,
        "evidence_available": True,
        "verdict": "failed",
        "reason": "At least one supplied acceptance check failed in the current test run.",
        "next_action": "Keep the task open and report the failing check and its evidence.",
    },
    {
        "condition": "tests_precede_latest_patch",
        "test_status": "passed",
        "test_after_latest_patch": False,
        "all_acceptance_checks_pass": True,
        "evidence_available": True,
        "verdict": "unavailable",
        "reason": "The passing test event predates the latest patch and is stale.",
        "next_action": "Request the required approval and rerun the fixed test suite after the patch.",
    },
    {
        "condition": "no_test_event",
        "test_status": "not_run",
        "test_after_latest_patch": False,
        "all_acceptance_checks_pass": False,
        "evidence_available": False,
        "verdict": "unavailable",
        "reason": "No test execution evidence is available for this task.",
        "next_action": "Do not infer success; obtain approval and run the required checks.",
    },
    {
        "condition": "runner_cannot_start",
        "test_status": "unavailable",
        "test_after_latest_patch": False,
        "all_acceptance_checks_pass": False,
        "evidence_available": True,
        "verdict": "unavailable",
        "reason": "The test runner could not execute, so no acceptance verdict exists.",
        "next_action": "Repair the runner and repeat the approved verification.",
    },
    {
        "condition": "grounded_output_mismatch",
        "test_status": "failed",
        "test_after_latest_patch": True,
        "all_acceptance_checks_pass": False,
        "evidence_available": True,
        "verdict": "failed",
        "reason": "The observed result differs from the requester-supplied expected result.",
        "next_action": "Preserve the source-bound counterexample and review the target and oracle separately.",
    },
    {
        "condition": "partial_checks_only",
        "test_status": "passed",
        "test_after_latest_patch": True,
        "all_acceptance_checks_pass": False,
        "evidence_available": True,
        "verdict": "unavailable",
        "reason": "Some checks passed, but the full acceptance contract was not exercised.",
        "next_action": "List untested checks and do not mark the overall task complete.",
    },
    {
        "condition": "evidence_hash_mismatch",
        "test_status": "passed",
        "test_after_latest_patch": True,
        "all_acceptance_checks_pass": True,
        "evidence_available": False,
        "verdict": "unavailable",
        "reason": "The evidence does not bind to the current source snapshot.",
        "next_action": "Reject the stale evidence and regenerate source-bound verification.",
    },
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_next_action(group: int, index: int, rng: random.Random) -> tuple[list[dict[str, str]], bool]:
    scenario = _NEXT_ACTIONS[index % len(_NEXT_ACTIONS)]
    facts = {
        "task_id": f"synthetic-{group:03d}-{index:03d}",
        "target": rng.choice(("apps/engine/src/module.py", "training/prepare_corpus.py", "tests/test_api.py", "apps/web/src/App.tsx")),
        "source_sha_current": scenario["condition"] != "source_fingerprint_is_stale",
        "workspace_read_approved": scenario["condition"] != "workspace_read_approval_missing",
        "oracle_expected_output_present": scenario["condition"] != "oracle_expectation_missing",
        "patch_confirmation_present": scenario["condition"] != "patch_confirmation_missing",
        "post_patch_test_status": (
            "failed" if scenario["condition"] == "acceptance_tests_failed"
            else "unavailable" if scenario["condition"] == "test_runner_unavailable"
            else "passed" if scenario["condition"] == "fresh_tests_passed"
            else "not_run"
        ),
    }
    prompt = (
        "Choose the safest next action from this task snapshot. Do not assume permissions, "
        "invent oracle values, or treat unavailable checks as passing.\n"
        "Task snapshot: " + _canonical(facts)
    )
    answer = {
        "decision": scenario["decision"],
        "required_approval": scenario["approval"],
        "next_action": scenario["action"],
        "reason": scenario["reason"],
    }
    parsed = json.loads(_canonical(answer))
    valid = (
        parsed["decision"] == scenario["decision"]
        and parsed["required_approval"] == scenario["approval"]
        and parsed["next_action"] == scenario["action"]
        and bool(parsed["reason"])
    )
    return [
        {"role": "system", "content": "You are a cautious task-execution specialist. Respect explicit approvals and source freshness."},
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": _canonical(answer)},
    ], valid


def _build_planner(group: int, index: int, rng: random.Random) -> tuple[list[dict[str, str]], bool]:
    case = _PLANS[index % len(_PLANS)]
    task_id = f"plan-{group:03d}-{index:03d}"
    brief = {
        "task_id": task_id,
        "request": case["request"],
        "allowed_scope": case["scope"],
        "required_acceptance_checks": list(case["checks"]),
        "available_permissions": list(case["approvals"]),
        "constraints": [
            rng.choice(("keep changes minimal", "prefer deterministic behavior", "preserve backward compatibility")),
            "do not run arbitrary shell commands",
            "do not claim completion before fresh verification",
        ],
    }
    plan = {
        "plan": [
            "Inspect only the approved files and record their exact source fingerprints.",
            "Map the task contract, constraints and existing focused tests before editing.",
            "Make the smallest scoped change and add or update the required checks.",
            "Run only the separately approved fixed test workflow and review its fresh evidence.",
        ],
        "acceptance_checks": list(case["checks"]),
        "required_approvals": list(case["approvals"]),
        "safety_limits": [
            "one-file patch proposals must be hash-bound and separately confirmed",
            "never infer expected outputs from the implementation",
            "stop and report unavailable when a required permission or tool is missing",
        ],
        "scope": case["scope"],
    }
    prompt = (
        "Create a bounded execution plan for this task contract. Return JSON with plan, "
        "acceptance_checks, required_approvals, safety_limits, and scope. Do not add permissions.\n"
        "Task contract: " + _canonical(brief)
    )
    encoded = _canonical(plan)
    parsed = json.loads(encoded)
    valid = (
        isinstance(parsed["plan"], list) and len(parsed["plan"]) >= 3
        and parsed["acceptance_checks"] == case["checks"]
        and parsed["required_approvals"] == case["approvals"]
        and isinstance(parsed["safety_limits"], list) and len(parsed["safety_limits"]) >= 2
        and parsed["scope"] == case["scope"]
    )
    return [
        {"role": "system", "content": "You are a planner. Produce scoped, permission-aware steps and measurable acceptance checks."},
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": encoded},
    ], valid


def _build_verifier(group: int, index: int, rng: random.Random) -> tuple[list[dict[str, str]], bool]:
    case = _VERIFIER_CASES[index % len(_VERIFIER_CASES)]
    evidence = {
        "case_id": f"verify-{group:03d}-{index:03d}",
        "test_status": case["test_status"],
        "test_event_is_newer_than_latest_patch": case["test_after_latest_patch"],
        "all_acceptance_checks_passed": case["all_acceptance_checks_pass"],
        "evidence_binds_to_current_source": case["evidence_available"],
        "reported_check_count": rng.randint(1, 7),
    }
    answer = {
        "verdict": case["verdict"],
        "reason": case["reason"],
        "next_action": case["next_action"],
        "limitations": "This verdict applies only to the supplied evidence and acceptance contract.",
    }
    prompt = (
        "Evaluate the task evidence. Use only the supplied status and freshness fields. "
        "A stale or unavailable test event cannot be a pass.\n"
        "Evidence: " + _canonical(evidence)
    )
    parsed = json.loads(_canonical(answer))
    valid = (
        parsed["verdict"] == case["verdict"]
        and bool(parsed["reason"])
        and bool(parsed["next_action"])
        and "only" in parsed["limitations"].lower()
    )
    return [
        {"role": "system", "content": "You are a verifier. Distinguish passed, failed and unavailable evidence."},
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": _canonical(answer)},
    ], valid


_BUILDERS = {
    "next_action": _build_next_action,
    "planner": _build_planner,
    "verifier": _build_verifier,
}


def generate(
    output: Path,
    *,
    groups: int = 20,
    examples_per_group: int = 12,
    seed: int = 20261105,
    license_name: str = "CC0-1.0",
) -> dict[str, Any]:
    if not 2 <= groups <= 5000:
        raise ValueError("groups must be between 2 and 5000.")
    if not 1 <= examples_per_group <= 1000:
        raise ValueError("examples_per_group must be between 1 and 1000.")
    if groups * examples_per_group * len(SPECIALISTS) > 100_000:
        raise ValueError("Requested synthetic dataset exceeds the 100,000-record safety limit.")
    if license_name not in _ALLOWED_OUTPUT_LICENSES:
        raise ValueError(
            "Unsupported output license. Choose one of: " + ", ".join(sorted(_ALLOWED_OUTPUT_LICENSES))
        )

    records: list[dict[str, Any]] = []
    counts = {role: 0 for role in SPECIALISTS}
    for role_index, specialist in enumerate(SPECIALISTS):
        builder = _BUILDERS[specialist]
        for group in range(groups):
            rng = random.Random(f"{seed}:{specialist}:{group}")
            for index in range(examples_per_group):
                messages, verified = builder(group, index, rng)
                if not verified:
                    raise RuntimeError(f"Synthetic contract verification failed for {specialist}.")
                records.append(
                    {
                        "specialist": specialist,
                        "messages": messages,
                        "outcome": "passed",
                        "verification": {
                            "status": "passed",
                            "method": "deterministic_synthetic_contract_v1",
                            "checks": [
                                {"name": "role-specific response contract", "passed": True},
                                {"name": "canonical JSON and decision invariants", "passed": True},
                            ],
                        },
                        "provenance": {
                            "source": "UnAi first-party synthetic trajectory generator",
                            "source_type": "synthetic",
                            "generator": GENERATOR,
                            "generator_version": GENERATOR_VERSION,
                            "rights_basis": "original synthetic examples generated locally without third-party corpus inputs",
                            "license": license_name,
                            "consented": True,
                            "repository": f"unai-synthetic-{specialist}-project-{group:04d}",
                        },
                    }
                )
                counts[specialist] += 1

    payload = "".join(_canonical(record) + "\n" for record in records).encode("utf-8")
    digest = _sha256(payload)
    manifest = {
        "schema": "unai.synthetic-collection.v1",
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "groups_per_specialist": groups,
        "examples_per_group": examples_per_group,
        "records_per_specialist": counts,
        "total_records": len(records),
        "license": license_name,
        "source_type": "synthetic",
        "source_file": output.name,
        "source_file_bytes": len(payload),
        "source_file_sha256": digest,
        "limitations": [
            "These are first-party synthetic examples, not real repository trajectories.",
            "Verification checks validate deterministic generator contracts, not real code changes.",
            "Synthetic data is a bootstrap corpus and cannot establish real-world coding capability.",
        ],
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(output)
    manifest_path = output.with_name(output.name + ".manifest.json")
    manifest_temporary = manifest_path.with_name(manifest_path.name + ".tmp")
    manifest_temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest_temporary.replace(manifest_path)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--groups", type=int, default=20)
    parser.add_argument("--examples-per-group", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20261105)
    parser.add_argument("--license", dest="license_name", default="CC0-1.0")
    args = parser.parse_args()
    manifest = generate(
        args.output,
        groups=args.groups,
        examples_per_group=args.examples_per_group,
        seed=args.seed,
        license_name=args.license_name,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

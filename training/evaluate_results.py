#!/usr/bin/env python3
"""Score matched base/adapter/tool-augmented verifier outcomes on held-out tasks."""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

SPECIALISTS = ("next_action", "planner", "verifier")

SYSTEMS = ("base", "adapter", "tool_augmented")


def _number(value: Any, name: str, *, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric.")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{name} must be finite and at least {minimum}.")
    return result


def _validated_result(row: Any, line_number: int) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError(f"Line {line_number}: benchmark result must be an object.")
    suite_id = row.get("suite_id")
    case_id = row.get("case_id")
    specialist = row.get("specialist")
    system = row.get("system")
    model_id = row.get("model_id")
    base_model = row.get("base_model")
    verification = row.get("verification")
    provenance = row.get("provenance")
    if (
        not all(isinstance(value, str) and value.strip() for value in (suite_id, case_id, model_id, base_model))
        or specialist not in SPECIALISTS
        or system not in SYSTEMS
    ):
        raise ValueError(f"Line {line_number}: invalid suite/case/specialist/system/model identity.")
    if not isinstance(verification, dict):
        raise ValueError(f"Line {line_number}: verification object is required.")
    status = verification.get("status")
    checks = verification.get("checks")
    evidence_id = verification.get("evidence_id")
    verifier = verification.get("verifier")
    if (
        status not in {"passed", "failed", "unavailable"}
        or not isinstance(checks, list)
        or not checks
        or not isinstance(evidence_id, str)
        or not evidence_id.strip()
        or not isinstance(verifier, str)
        or not verifier.strip()
    ):
        raise ValueError(f"Line {line_number}: verification needs status, checks, evidence_id and verifier.")
    if any(
        not isinstance(check, dict)
        or not isinstance(check.get("name"), str)
        or not check["name"].strip()
        or not isinstance(check.get("passed"), bool)
        for check in checks
    ):
        raise ValueError(f"Line {line_number}: each verifier check needs a name and boolean passed flag.")
    derived_pass = status == "passed" and all(check["passed"] for check in checks)
    if status == "passed" and not all(check["passed"] for check in checks):
        raise ValueError(f"Line {line_number}: passed status contradicts a failed verification check.")
    if not isinstance(provenance, dict) or provenance.get("consented") is not True:
        raise ValueError(f"Line {line_number}: explicit consent provenance is required.")
    if not isinstance(provenance.get("license"), str) or not provenance["license"].strip():
        raise ValueError(f"Line {line_number}: license provenance is required.")

    tool_trace = row.get("tool_trace")
    if system == "tool_augmented":
        if (
            not isinstance(tool_trace, dict)
            or not isinstance(tool_trace.get("tools"), list)
            or not tool_trace["tools"]
            or not isinstance(tool_trace.get("evidence_ids"), list)
            or not tool_trace["evidence_ids"]
        ):
            raise ValueError(f"Line {line_number}: tool_augmented rows require tool names and evidence IDs.")
    elif tool_trace is not None:
        raise ValueError(f"Line {line_number}: base/adapter rows must not include tool_trace.")

    latency = _number(row.get("latency_ms"), f"Line {line_number} latency_ms")
    tokens = _number(row.get("tokens"), f"Line {line_number} tokens")
    if int(tokens) != tokens:
        raise ValueError(f"Line {line_number}: tokens must be an integer.")
    return {
        "suite_id": suite_id,
        "case_id": case_id,
        "specialist": specialist,
        "system": system,
        "model_id": model_id,
        "base_model": base_model,
        "passed": derived_pass,
        "status": status,
        "latency_ms": latency,
        "tokens": int(tokens),
        "evidence_id": evidence_id,
        "verifier": verifier,
    }


def evaluate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    matrix: dict[tuple[str, str, str], dict[str, Any]] = {}
    for index, raw in enumerate(rows, 1):
        item = _validated_result(raw, index)
        key = (item["suite_id"], item["case_id"], item["specialist"])
        slot = matrix.setdefault(key, {})
        if item["system"] in slot:
            raise ValueError(f"Duplicate result for suite/case/system: {key}/{item['system']}.")
        slot[item["system"]] = item

    if not matrix:
        raise ValueError("No benchmark results were supplied.")
    incomplete = [key for key, systems in matrix.items() if set(systems) != set(SYSTEMS)]
    if incomplete:
        raise ValueError(f"Every held-out case must have base, adapter and tool_augmented results; incomplete: {incomplete[:5]}.")

    specialists = {key[2] for key in matrix}
    if len(specialists) != 1:
        raise ValueError("One benchmark report must evaluate exactly one specialist adapter.")
    for key, systems in matrix.items():
        base_model = systems["base"]["base_model"]
        for system in SYSTEMS:
            if systems[system]["base_model"] != base_model:
                raise ValueError(f"Base model mismatch within matched case {key}.")
        if systems["adapter"]["model_id"] == systems["base"]["model_id"]:
            raise ValueError(f"Adapter row does not identify a distinct adapter-backed system for {key}.")

    metrics: dict[str, dict[str, Any]] = {}
    for system in SYSTEMS:
        entries = [systems[system] for systems in matrix.values()]
        successes = sum(1 for entry in entries if entry["passed"])
        metrics[system] = {
            "cases": len(entries),
            "verified_successes": successes,
            "verified_success_rate": successes / len(entries),
            "mean_latency_ms": statistics.fmean(entry["latency_ms"] for entry in entries),
            "mean_tokens": statistics.fmean(entry["tokens"] for entry in entries),
            "evidence_ids": [entry["evidence_id"] for entry in entries],
        }

    improvements = 0
    regressions = 0
    unchanged = 0
    tool_improvements = 0
    for systems in matrix.values():
        base_pass = systems["base"]["passed"]
        adapter_pass = systems["adapter"]["passed"]
        tool_pass = systems["tool_augmented"]["passed"]
        improvements += int(not base_pass and adapter_pass)
        regressions += int(base_pass and not adapter_pass)
        unchanged += int(base_pass == adapter_pass)
        tool_improvements += int(not base_pass and tool_pass)

    adapter_rate = metrics["adapter"]["verified_success_rate"]
    base_rate = metrics["base"]["verified_success_rate"]
    quality_improved = adapter_rate > base_rate and regressions == 0
    return {
        "schema": "unai.heldout-evaluation.v1",
        "specialist": next(iter(specialists)),
        "matched_cases": len(matrix),
        "systems": metrics,
        "paired_comparison": {
            "adapter_improvements_over_base": improvements,
            "adapter_regressions_vs_base": regressions,
            "adapter_unchanged_vs_base": unchanged,
            "tool_augmented_improvements_over_base": tool_improvements,
            "adapter_success_rate_delta": adapter_rate - base_rate,
            "adapter_quality_improved_without_regressions": quality_improved,
        },
        "claim_boundary": "These are paired verifier outcomes on the supplied held-out suite only; they are not proof of general coding capability or a substitute for physical-device measurements.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="JSONL captured from the same held-out task set")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    rows = []
    for line_number, line in enumerate(args.input.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{args.input}:{line_number} is not valid JSON.") from exc
    report = evaluate_rows(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Score matched base/adapter/tool-augmented verifier outcomes on held-out tasks."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import re
import statistics
from pathlib import Path
from typing import Any

try:
    from .metrics import exact_mcnemar_pvalue
except ImportError:  # Support direct execution: python training/evaluate_results.py
    from metrics import exact_mcnemar_pvalue

SPECIALISTS = ("next_action", "planner", "verifier")

SYSTEMS = ("base", "adapter", "tool_augmented")


def _number(value: Any, name: str, *, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric.")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{name} must be finite and at least {minimum}.")
    return result


def _case_contract_sha256(value: Any, line_number: int) -> str:
    if not isinstance(value, dict) or set(value) != {"prompt", "acceptance_criteria"}:
        raise ValueError(
            f"Line {line_number}: case_contract must contain only prompt and acceptance_criteria."
        )
    prompt = value.get("prompt")
    criteria = value.get("acceptance_criteria")
    if (
        not isinstance(prompt, str)
        or not prompt.strip()
        or len(prompt) > 50_000
        or not isinstance(criteria, list)
        or not 1 <= len(criteria) <= 100
        or not all(
            isinstance(item, str) and item.strip() and len(item) <= 2_000
            for item in criteria
        )
    ):
        raise ValueError(
            f"Line {line_number}: case_contract requires a non-empty prompt and 1-100 non-empty acceptance criteria."
        )
    try:
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Line {line_number}: case_contract is not canonical JSON.") from exc
    return hashlib.sha256(canonical).hexdigest()


def _validated_result(row: Any, line_number: int) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError(f"Line {line_number}: benchmark result must be an object.")
    if row.get("schema") != "unai.benchmark-result.v1":
        raise ValueError(f"Line {line_number}: benchmark result schema must be unai.benchmark-result.v1.")
    allowed_fields = {
        "schema", "suite_id", "case_id", "case_contract", "case_sha256", "specialist", "system",
        "model_id", "base_model", "latency_ms", "tokens", "verification",
        "provenance", "tool_trace",
    }
    unexpected_fields = set(row) - allowed_fields
    if unexpected_fields:
        raise ValueError(f"Line {line_number}: unexpected benchmark fields: {', '.join(sorted(unexpected_fields))}.")
    suite_id = row.get("suite_id")
    case_id = row.get("case_id")
    case_contract = row.get("case_contract")
    case_sha256 = row.get("case_sha256")
    specialist = row.get("specialist")
    system = row.get("system")
    model_id = row.get("model_id")
    base_model = row.get("base_model")
    verification = row.get("verification")
    provenance = row.get("provenance")
    if (
        not all(isinstance(value, str) and value.strip() for value in (suite_id, case_id, model_id, base_model))
        or not isinstance(case_sha256, str)
        or re.fullmatch(r"[a-fA-F0-9]{64}", case_sha256) is None
        or specialist not in SPECIALISTS
        or system not in SYSTEMS
    ):
        raise ValueError(f"Line {line_number}: invalid suite/case/specialist/system/model identity.")
    computed_case_sha256 = _case_contract_sha256(case_contract, line_number)
    if not hmac.compare_digest(case_sha256.lower(), computed_case_sha256):
        raise ValueError(
            f"Line {line_number}: case_sha256 does not match the canonical prompt and acceptance criteria."
        )
    if not isinstance(verification, dict):
        raise ValueError(f"Line {line_number}: verification object is required.")
    if set(verification) - {"status", "checks", "evidence_id", "verifier"}:
        raise ValueError(f"Line {line_number}: verification contains unexpected fields.")
    status = verification.get("status")
    checks = verification.get("checks")
    evidence_id = verification.get("evidence_id")
    verifier = verification.get("verifier")
    if status == "unavailable":
        raise ValueError(
            f"Line {line_number}: unavailable execution cannot be counted as a verified held-out outcome."
        )
    if (
        status not in {"passed", "failed"}
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
        or set(check) != {"name", "passed"}
        or not isinstance(check.get("name"), str)
        or not check["name"].strip()
        or not isinstance(check.get("passed"), bool)
        for check in checks
    ):
        raise ValueError(f"Line {line_number}: each verifier check needs a name and boolean passed flag.")
    derived_pass = status == "passed" and all(check["passed"] for check in checks)
    if status == "passed" and not all(check["passed"] for check in checks):
        raise ValueError(f"Line {line_number}: passed status contradicts a failed verification check.")
    if status == "failed" and all(check["passed"] for check in checks):
        raise ValueError(f"Line {line_number}: failed status contradicts all-passing verification checks.")
    if (
        not isinstance(provenance, dict)
        or set(provenance) - {"consented", "source", "license"}
        or provenance.get("consented") is not True
        or not isinstance(provenance.get("source"), str)
        or not provenance["source"].strip()
    ):
        raise ValueError(f"Line {line_number}: explicit consent and source provenance are required.")
    if not isinstance(provenance.get("license"), str) or not provenance["license"].strip():
        raise ValueError(f"Line {line_number}: license provenance is required.")

    tool_trace = row.get("tool_trace")
    if system == "tool_augmented":
        if (
            not isinstance(tool_trace, dict)
            or set(tool_trace) - {"tools", "evidence_ids"}
            or not isinstance(tool_trace.get("tools"), list)
            or not tool_trace["tools"]
            or not all(isinstance(tool, str) and tool.strip() for tool in tool_trace["tools"])
            or not isinstance(tool_trace.get("evidence_ids"), list)
            or not tool_trace["evidence_ids"]
            or not all(isinstance(item, str) and item.strip() for item in tool_trace["evidence_ids"])
        ):
            raise ValueError(f"Line {line_number}: tool_augmented rows require named tools and evidence IDs.")
    elif tool_trace is not None:
        raise ValueError(f"Line {line_number}: base/adapter rows must not include tool_trace.")

    latency = _number(row.get("latency_ms"), f"Line {line_number} latency_ms")
    tokens = _number(row.get("tokens"), f"Line {line_number} tokens")
    if int(tokens) != tokens:
        raise ValueError(f"Line {line_number}: tokens must be an integer.")
    return {
        "suite_id": suite_id,
        "case_id": case_id,
        "case_sha256": case_sha256.lower(),
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


def evaluate_rows(
    rows: list[dict[str, Any]],
    *,
    minimum_heldout_cases: int = 30,
    significance_alpha: float = 0.05,
) -> dict[str, Any]:
    if not isinstance(minimum_heldout_cases, int) or isinstance(minimum_heldout_cases, bool) or minimum_heldout_cases < 30:
        raise ValueError("minimum_heldout_cases must be an integer of at least 30.")
    if not isinstance(significance_alpha, (int, float)) or isinstance(significance_alpha, bool) or not math.isfinite(significance_alpha) or not 0 < significance_alpha <= 0.05:
        raise ValueError("significance_alpha must be finite, greater than zero and no higher than 0.05.")
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
    model_ids: dict[str, str] = {}
    expected_base_model: str | None = None
    for key, systems in matrix.items():
        base_model = systems["base"]["base_model"]
        case_hash = systems["base"]["case_sha256"]
        if expected_base_model is None:
            expected_base_model = base_model
        elif base_model != expected_base_model:
            raise ValueError("A single evaluation report must use one immutable base model.")
        for system in SYSTEMS:
            if systems[system]["base_model"] != base_model:
                raise ValueError(f"Base model mismatch within matched case {key}.")
            if systems[system]["case_sha256"] != case_hash:
                raise ValueError(f"Case content hash mismatch within matched case {key}.")
            current_model_id = systems[system]["model_id"]
            if system in model_ids and model_ids[system] != current_model_id:
                raise ValueError(f"Model identity changed between held-out cases for system {system}.")
            model_ids[system] = current_model_id
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
    tool_regressions = 0
    tool_unchanged = 0
    for systems in matrix.values():
        base_pass = systems["base"]["passed"]
        adapter_pass = systems["adapter"]["passed"]
        tool_pass = systems["tool_augmented"]["passed"]
        improvements += int(not base_pass and adapter_pass)
        regressions += int(base_pass and not adapter_pass)
        unchanged += int(base_pass == adapter_pass)
        tool_improvements += int(not base_pass and tool_pass)
        tool_regressions += int(base_pass and not tool_pass)
        tool_unchanged += int(base_pass == tool_pass)

    adapter_rate = metrics["adapter"]["verified_success_rate"]
    base_rate = metrics["base"]["verified_success_rate"]
    quality_improved = adapter_rate > base_rate and regressions == 0
    minimum_heldout_cases = minimum_heldout_cases
    significance_alpha = significance_alpha
    adapter_p_value = exact_mcnemar_pvalue(improvements, regressions)
    tool_p_value = exact_mcnemar_pvalue(tool_improvements, tool_regressions)
    statistical_passed = (
        len(matrix) >= minimum_heldout_cases
        and quality_improved
        and adapter_p_value <= significance_alpha
    )
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
            "tool_augmented_regressions_vs_base": tool_regressions,
            "tool_augmented_unchanged_vs_base": tool_unchanged,
            "adapter_success_rate_delta": adapter_rate - base_rate,
            "adapter_quality_improved_without_regressions": quality_improved,
        },
        "statistical_gate": {
            "minimum_heldout_cases": minimum_heldout_cases,
            "significance_alpha": significance_alpha,
            "adapter_mcnemar_exact_p_value": adapter_p_value,
            "tool_augmented_mcnemar_exact_p_value": tool_p_value,
            "adapter_passed": statistical_passed,
            "tool_augmented_passed": (
                len(matrix) >= minimum_heldout_cases
                and tool_improvements > tool_regressions
                and tool_p_value <= significance_alpha
            ),
        },
        "claim_boundary": "These are paired verifier outcomes on the supplied held-out suite only; they are not proof of general coding capability or a substitute for physical-device measurements.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="JSONL captured from the same held-out task set")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--minimum-heldout-cases", type=int, default=30)
    parser.add_argument("--significance-alpha", type=float, default=0.05)
    args = parser.parse_args()
    if args.minimum_heldout_cases < 30:
        raise SystemExit("--minimum-heldout-cases cannot be below the 30-case release floor.")
    if not 0 < args.significance_alpha <= 0.05:
        raise SystemExit("--significance-alpha must be > 0 and <= 0.05.")
    rows = []
    for line_number, line in enumerate(args.input.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{args.input}:{line_number} is not valid JSON.") from exc
    report = evaluate_rows(
        rows,
        minimum_heldout_cases=args.minimum_heldout_cases,
        significance_alpha=args.significance_alpha,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

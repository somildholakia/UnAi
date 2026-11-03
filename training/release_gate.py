#!/usr/bin/env python3
"""Apply the evidence and physical-device gates before an adapter can be released."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

MAX_UNAI_MEMORY_BUDGET_MB = 6144


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric.")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} must be finite and non-negative.")
    return number


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decide(
    manifest: dict[str, Any],
    evaluation: dict[str, Any],
    mobile: dict[str, Any] | None,
    *,
    manifest_sha256: str | None = None,
    evaluation_sha256: str | None = None,
    mobile_metrics_sha256: str | None = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    if manifest.get("schema") != "unai.specialist-adapter.v1":
        raise ValueError("Unsupported or missing specialist adapter manifest schema.")
    if evaluation.get("schema") != "unai.heldout-evaluation.v1":
        raise ValueError("Unsupported or missing held-out evaluation report schema.")
    adapter_artifact_hash = manifest.get("adapter_artifacts_sha256")
    if not isinstance(adapter_artifact_hash, str) or re.fullmatch(r"[a-fA-F0-9]{64}", adapter_artifact_hash) is None:
        raise ValueError("Adapter manifest must contain a valid SHA-256 digest of the deployable adapter artifacts.")
    matched_cases = evaluation.get("matched_cases")
    if not isinstance(matched_cases, int) or isinstance(matched_cases, bool) or matched_cases < 1:
        raise ValueError("Evaluation report must contain at least one matched held-out case.")
    specialist = manifest.get("specialist")
    if specialist not in {"next_action", "planner", "verifier"} or evaluation.get("specialist") != specialist:
        raise ValueError("Adapter manifest and evaluation specialist roles do not match.")

    paired = evaluation.get("paired_comparison")
    systems = evaluation.get("systems")
    if not isinstance(paired, dict) or not isinstance(systems, dict):
        raise ValueError("Evaluation report is missing paired comparison metrics.")
    if any(not isinstance(systems.get(system), dict) for system in ("base", "adapter", "tool_augmented")):
        raise ValueError("Evaluation report is missing one or more matched system metrics.")
    validated_rates: dict[str, float] = {}
    for system in ("base", "adapter", "tool_augmented"):
        entry = systems[system]
        cases = entry.get("cases")
        successes = entry.get("verified_successes")
        evidence_ids = entry.get("evidence_ids")
        if (
            cases != matched_cases
            or not isinstance(successes, int)
            or isinstance(successes, bool)
            or not 0 <= successes <= matched_cases
            or not isinstance(evidence_ids, list)
            or len(evidence_ids) != matched_cases
            or not all(isinstance(item, str) and item.strip() for item in evidence_ids)
            or len(set(evidence_ids)) != matched_cases
        ):
            raise ValueError(f"Evaluation report has inconsistent {system} case/evidence counts.")
        rate = _finite_number(entry.get("verified_success_rate"), f"{system} verified success rate")
        expected_rate = successes / matched_cases
        if not math.isclose(rate, expected_rate, rel_tol=0, abs_tol=1e-12):
            raise ValueError(f"Evaluation report {system} success rate does not match its verified counts.")
        validated_rates[system] = rate
    base_rate = validated_rates["base"]
    adapter_rate = validated_rates["adapter"]
    regressions = paired.get("adapter_regressions_vs_base")
    improvements = paired.get("adapter_improvements_over_base")
    unchanged = paired.get("adapter_unchanged_vs_base")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in (regressions, improvements, unchanged)
    ) or regressions + improvements + unchanged != matched_cases:
        raise ValueError("Evaluation report has inconsistent paired-comparison counts.")
    reported_delta = _finite_number(
        abs(paired.get("adapter_success_rate_delta")), "absolute adapter success-rate delta"
    )
    if not math.isclose(
        paired.get("adapter_success_rate_delta"),
        adapter_rate - base_rate,
        rel_tol=0,
        abs_tol=1e-12,
    ) or not math.isclose(
        reported_delta,
        abs(adapter_rate - base_rate),
        rel_tol=0,
        abs_tol=1e-12,
    ):
        raise ValueError("Evaluation report paired success-rate delta is inconsistent.")
    quality_passed = adapter_rate > base_rate and regressions == 0
    if not quality_passed:
        reasons.append(
            "The adapter did not improve the matched verified success rate over base without regressions."
        )

    mobile_checks: dict[str, Any] = {
        "present": mobile is not None,
        "passed": False,
        "failures": [],
    }
    if mobile is None:
        mobile_checks["failures"].append("No physical-device mobile benchmark was supplied.")
    else:
        if mobile.get("schema") != "unai.mobile-benchmark.v1":
            mobile_checks["failures"].append("Unsupported mobile benchmark schema.")
        if mobile.get("specialist") != specialist:
            mobile_checks["failures"].append("Mobile benchmark specialist does not match the adapter.")
        if mobile.get("base_model") != manifest.get("base_model"):
            mobile_checks["failures"].append("Mobile benchmark base model does not match the adapter manifest.")
        if mobile.get("adapter_artifacts_sha256") != manifest.get("adapter_artifacts_sha256"):
            mobile_checks["failures"].append("Mobile benchmark adapter artifact hash does not match the trained adapter.")
        for field in ("device_model", "android_version", "measured_at", "measurement_method"):
            if not isinstance(mobile.get(field), str) or not mobile[field].strip():
                mobile_checks["failures"].append(f"Mobile benchmark is missing {field}.")
        try:
            memory_budget = _finite_number(mobile.get("memory_budget_mb"), "mobile memory budget")
            peak_rss = _finite_number(mobile.get("peak_rss_mb"), "mobile peak RSS")
            ttft = _finite_number(mobile.get("ttft_ms"), "mobile TTFT")
            ttft_budget = _finite_number(mobile.get("ttft_budget_ms"), "mobile TTFT budget")
            sustained_tps = _finite_number(
                mobile.get("sustained_tokens_per_second"), "mobile sustained tokens/second"
            )
            min_tps = _finite_number(mobile.get("minimum_tokens_per_second"), "mobile minimum tokens/second")
            if memory_budget <= 0 or memory_budget > MAX_UNAI_MEMORY_BUDGET_MB:
                mobile_checks["failures"].append(
                    f"Memory budget must be positive and no higher than {MAX_UNAI_MEMORY_BUDGET_MB} MiB."
                )
            if peak_rss > memory_budget:
                mobile_checks["failures"].append("Measured peak RSS exceeds the configured memory budget.")
            if ttft_budget <= 0 or ttft > ttft_budget:
                mobile_checks["failures"].append("Measured time-to-first-token exceeds its configured budget.")
            if min_tps <= 0 or sustained_tps < min_tps:
                mobile_checks["failures"].append("Sustained tokens/second is below the configured minimum.")
        except ValueError as exc:
            mobile_checks["failures"].append(str(exc))
        if mobile.get("thermal_throttling") is not False:
            mobile_checks["failures"].append("Thermal throttling must be explicitly measured as false.")
        mobile_checks["passed"] = not mobile_checks["failures"]

    if not mobile_checks["passed"]:
        reasons.extend(mobile_checks["failures"])
    releasable = quality_passed and mobile_checks["passed"]
    if not releasable:
        reasons.append("Adapter release is blocked until both the held-out quality and mobile gates pass.")

    return {
        "schema": "unai.adapter-release-decision.v1",
        "specialist": specialist,
        "adapter_manifest_sha256": manifest_sha256,
        "evaluation_report_sha256": evaluation_sha256,
        "mobile_metrics_sha256": mobile_metrics_sha256,
        "adapter_artifacts_sha256": manifest.get("adapter_artifacts_sha256"),
        "heldout_quality_gate": {
            "passed": quality_passed,
            "base_verified_success_rate": base_rate,
            "adapter_verified_success_rate": adapter_rate,
            "adapter_regressions_vs_base": regressions,
        },
        "mobile_gate": mobile_checks,
        "releasable": releasable,
        "reasons": reasons,
        "claim_boundary": "A passing decision applies only to the recorded held-out suite and the named physical device/budgets; it is not a general capability claim.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter-manifest", required=True, type=Path)
    parser.add_argument("--evaluation-report", required=True, type=Path)
    parser.add_argument("--mobile-metrics", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.adapter_manifest.read_text(encoding="utf-8"))
    evaluation = json.loads(args.evaluation_report.read_text(encoding="utf-8"))
    mobile = (
        json.loads(args.mobile_metrics.read_text(encoding="utf-8"))
        if args.mobile_metrics
        else None
    )
    decision = decide(
        manifest,
        evaluation,
        mobile,
        manifest_sha256=_file_sha256(args.adapter_manifest),
        evaluation_sha256=_file_sha256(args.evaluation_report),
        mobile_metrics_sha256=_file_sha256(args.mobile_metrics) if args.mobile_metrics else None,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(decision, indent=2))
    if not decision["releasable"]:
        raise SystemExit("Adapter release is blocked; see release decision reasons.")


if __name__ == "__main__":
    main()

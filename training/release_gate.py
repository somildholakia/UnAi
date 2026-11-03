#!/usr/bin/env python3
"""Apply the evidence and physical-device gates before an adapter can be released."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
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
) -> dict[str, Any]:
    reasons: list[str] = []
    if manifest.get("schema") != "unai.specialist-adapter.v1":
        raise ValueError("Unsupported or missing specialist adapter manifest schema.")
    if evaluation.get("schema") != "unai.heldout-evaluation.v1":
        raise ValueError("Unsupported or missing held-out evaluation report schema.")
    specialist = manifest.get("specialist")
    if specialist not in {"next_action", "planner", "verifier"} or evaluation.get("specialist") != specialist:
        raise ValueError("Adapter manifest and evaluation specialist roles do not match.")

    paired = evaluation.get("paired_comparison")
    systems = evaluation.get("systems")
    if not isinstance(paired, dict) or not isinstance(systems, dict):
        raise ValueError("Evaluation report is missing paired comparison metrics.")
    base_rate = _finite_number(systems.get("base", {}).get("verified_success_rate"), "base verified success rate")
    adapter_rate = _finite_number(systems.get("adapter", {}).get("verified_success_rate"), "adapter verified success rate")
    regressions = paired.get("adapter_regressions_vs_base")
    if not isinstance(regressions, int) or isinstance(regressions, bool) or regressions < 0:
        raise ValueError("Evaluation report has an invalid adapter regression count.")
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
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(decision, indent=2))
    if not decision["releasable"]:
        raise SystemExit("Adapter release is blocked; see release decision reasons.")


if __name__ == "__main__":
    main()

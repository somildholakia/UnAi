from __future__ import annotations

import copy
import pytest

from training.evaluate_results import evaluate_rows
from training.release_gate import decide


def _row(case_id: str, system: str, passed: bool, *, specialist: str = "next_action") -> dict:
    row = {
        "suite_id": "heldout-v1",
        "case_id": case_id,
        "specialist": specialist,
        "system": system,
        "model_id": {
            "base": "acme/small-code-model",
            "adapter": "acme/small-code-model+unai-next-action",
            "tool_augmented": "acme/small-code-model+unai-tools",
        }[system],
        "base_model": "acme/small-code-model",
        "verification": {
            "status": "passed" if passed else "failed",
            "checks": [{"name": "isolated task acceptance", "passed": passed}],
            "evidence_id": f"evidence-{case_id}-{system}",
            "verifier": "unai-test-runner-v1",
        },
        "provenance": {
            "consented": True,
            "source": "consented held-out evaluation",
            "license": "Apache-2.0",
        },
        "latency_ms": 1000 if system == "base" else 900,
        "tokens": 120,
    }
    if system == "tool_augmented":
        row["tool_trace"] = {
            "tools": ["repository-search", "pytest"],
            "evidence_ids": [f"tool-evidence-{case_id}"],
        }
    return row


def _mobile(manifest: dict) -> dict:
    return {
        "schema": "unai.mobile-benchmark.v1",
        "specialist": "next_action",
        "base_model": "acme/small-code-model",
        "adapter_artifacts_sha256": manifest["adapter_artifacts_sha256"],
        "device_model": "Android reference device",
        "android_version": "Android 16",
        "measured_at": "2026-11-03T15:00:00+05:30",
        "measurement_method": "instrumented sustained local inference",
        "memory_budget_mb": 6144,
        "peak_rss_mb": 5400,
        "ttft_ms": 2200,
        "ttft_budget_ms": 5000,
        "sustained_tokens_per_second": 7.5,
        "minimum_tokens_per_second": 5.0,
        "thermal_throttling": False,
    }


def _manifest() -> dict:
    return {
        "schema": "unai.specialist-adapter.v1",
        "specialist": "next_action",
        "base_model": "acme/small-code-model",
        "adapter_artifacts_sha256": "a" * 64,
    }


def test_evaluate_rows_compares_three_systems_on_matched_heldout_cases() -> None:
    rows = [
        _row("case-1", "base", False),
        _row("case-1", "adapter", True),
        _row("case-1", "tool_augmented", True),
        _row("case-2", "base", True),
        _row("case-2", "adapter", True),
        _row("case-2", "tool_augmented", True),
    ]

    report = evaluate_rows(rows)

    assert report["schema"] == "unai.heldout-evaluation.v1"
    assert report["matched_cases"] == 2
    assert report["systems"]["base"]["verified_success_rate"] == 0.5
    assert report["systems"]["adapter"]["verified_success_rate"] == 1.0
    assert report["paired_comparison"]["adapter_improvements_over_base"] == 1
    assert report["paired_comparison"]["adapter_regressions_vs_base"] == 0
    assert report["paired_comparison"]["adapter_quality_improved_without_regressions"] is True


def test_evaluate_rows_rejects_missing_systems_duplicate_rows_and_unverified_tool_traces() -> None:
    incomplete = [_row("case-1", system, True) for system in ("base", "adapter")]
    with pytest.raises(ValueError, match="base, adapter and tool_augmented"):
        evaluate_rows(incomplete)

    duplicate = [_row("case-1", system, True) for system in ("base", "adapter", "tool_augmented")]
    duplicate.append(copy.deepcopy(duplicate[0]))
    with pytest.raises(ValueError, match="Duplicate result"):
        evaluate_rows(duplicate)

    no_trace = [_row("case-1", system, True) for system in ("base", "adapter", "tool_augmented")]
    no_trace[-1].pop("tool_trace")
    with pytest.raises(ValueError, match="require named tools and evidence IDs"):
        evaluate_rows(no_trace)


def test_evaluate_rows_rejects_bad_evidence_and_base_model_mismatch() -> None:
    rows = [_row("case-1", system, True) for system in ("base", "adapter", "tool_augmented")]
    rows[1]["verification"]["checks"][0]["passed"] = False
    with pytest.raises(ValueError, match="contradicts a failed"):
        evaluate_rows(rows)

    rows = [_row("case-1", system, True) for system in ("base", "adapter", "tool_augmented")]
    rows[2]["base_model"] = "different/base"
    with pytest.raises(ValueError, match="Base model mismatch"):
        evaluate_rows(rows)



def test_evaluate_rows_rejects_unavailable_runs_and_missing_source_provenance() -> None:
    rows = [_row("case-1", system, True) for system in ("base", "adapter", "tool_augmented")]
    rows[1]["verification"]["status"] = "unavailable"
    with pytest.raises(ValueError, match="unavailable execution"):
        evaluate_rows(rows)

    rows = [_row("case-1", system, True) for system in ("base", "adapter", "tool_augmented")]
    rows[0]["provenance"].pop("source")
    with pytest.raises(ValueError, match="source provenance"):
        evaluate_rows(rows)



def test_evaluate_rows_reports_adapter_regression_instead_of_hiding_it() -> None:
    rows = [
        _row("case-1", "base", True),
        _row("case-1", "adapter", False),
        _row("case-1", "tool_augmented", True),
    ]
    report = evaluate_rows(rows)
    assert report["paired_comparison"]["adapter_regressions_vs_base"] == 1
    assert report["paired_comparison"]["adapter_quality_improved_without_regressions"] is False


def test_release_gate_requires_quality_gain_and_physical_device_pass() -> None:
    rows = [
        _row("case-1", "base", False),
        _row("case-1", "adapter", True),
        _row("case-1", "tool_augmented", True),
        _row("case-2", "base", True),
        _row("case-2", "adapter", True),
        _row("case-2", "tool_augmented", True),
    ]
    evaluation = evaluate_rows(rows)
    manifest = _manifest()

    decision = decide(manifest, evaluation, _mobile(manifest), manifest_sha256="b" * 64)

    assert decision["releasable"] is True
    assert decision["heldout_quality_gate"]["passed"] is True
    assert decision["mobile_gate"]["passed"] is True
    assert decision["adapter_artifacts_sha256"] == "a" * 64


def test_release_gate_blocks_missing_or_over_budget_mobile_measurements() -> None:
    rows = [
        _row("case-1", "base", False),
        _row("case-1", "adapter", True),
        _row("case-1", "tool_augmented", True),
    ]
    evaluation = evaluate_rows(rows)
    manifest = _manifest()

    missing = decide(manifest, evaluation, None)
    assert missing["releasable"] is False
    assert missing["mobile_gate"]["passed"] is False

    over_budget = _mobile(manifest)
    over_budget["peak_rss_mb"] = 7000
    decision = decide(manifest, evaluation, over_budget)
    assert decision["releasable"] is False
    assert any("peak RSS" in reason for reason in decision["mobile_gate"]["failures"])


def test_release_gate_blocks_wrong_adapter_hash_and_thermal_throttling() -> None:
    rows = [
        _row("case-1", "base", False),
        _row("case-1", "adapter", True),
        _row("case-1", "tool_augmented", True),
    ]
    evaluation = evaluate_rows(rows)
    manifest = _manifest()
    mobile = _mobile(manifest)
    mobile["adapter_artifacts_sha256"] = "f" * 64
    mobile["thermal_throttling"] = True

    decision = decide(manifest, evaluation, mobile)

    assert decision["releasable"] is False
    assert any("artifact hash" in reason for reason in decision["mobile_gate"]["failures"])
    assert any("Thermal throttling" in reason for reason in decision["mobile_gate"]["failures"])


def test_release_gate_blocks_adapter_without_strict_verified_improvement() -> None:
    rows = [
        _row("case-1", "base", True),
        _row("case-1", "adapter", True),
        _row("case-1", "tool_augmented", True),
    ]
    evaluation = evaluate_rows(rows)
    manifest = _manifest()
    decision = decide(manifest, evaluation, _mobile(manifest))

    assert decision["releasable"] is False
    assert decision["heldout_quality_gate"]["passed"] is False

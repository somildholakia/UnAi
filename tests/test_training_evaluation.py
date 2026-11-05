from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import pytest

from training.evaluate_results import evaluate_rows
from training.metrics import exact_mcnemar_pvalue
from training.release_gate import decide


def _row(case_id: str, system: str, passed: bool, *, specialist: str = "next_action") -> dict:
    row = {
        "schema": "unai.benchmark-result.v1",
        "suite_id": "heldout-v1",
        "case_id": case_id,
        "case_contract": {
            "prompt": f"Task for {case_id}",
            "acceptance_criteria": ["isolated task acceptance"],
        },
        "case_sha256": hashlib.sha256(
            json.dumps(
                {
                    "prompt": f"Task for {case_id}",
                    "acceptance_criteria": ["isolated task acceptance"],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest(),
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


def test_benchmark_result_schema_is_valid_json_and_requires_case_identity() -> None:
    schema_path = Path(__file__).resolve().parents[1] / "training" / "schemas" / "benchmark-result.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["properties"]["schema"]["const"] == "unai.benchmark-result.v1"
    assert "case_contract" in schema["required"]
    assert "case_sha256" in schema["required"]
    assert "provenance" in schema["required"]
    assert schema["properties"]["case_sha256"]["pattern"] == "^[a-fA-F0-9]{64}$"


def test_evaluate_rows_rejects_fields_outside_benchmark_result_schema() -> None:
    rows = [_row("schema-extra", system, True) for system in ("base", "adapter", "tool_augmented")]
    rows[0]["unreviewed_metadata"] = {"free_form": True}
    with pytest.raises(ValueError, match="unexpected benchmark fields"):
        evaluate_rows(rows)

    rows = [_row("schema-nested-extra", system, True) for system in ("base", "adapter", "tool_augmented")]
    rows[0]["verification"]["raw_log"] = "raw data"
    with pytest.raises(ValueError, match="verification contains unexpected fields"):
        evaluate_rows(rows)


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


def test_exact_mcnemar_requires_sufficient_paired_evidence() -> None:
    rows = []
    for index in range(30):
        base_pass = index >= 10
        for system in ("base", "adapter", "tool_augmented"):
            passed = True if system != "base" and index < 10 else base_pass
            rows.append(_row(f"stat-case-{index}", system, passed))
    report = evaluate_rows(rows)
    gate = report["statistical_gate"]
    assert gate["adapter_passed"] is True
    assert gate["adapter_mcnemar_exact_p_value"] == pytest.approx(2 / 1024)

    small = evaluate_rows([
        _row("tiny-1", "base", False),
        _row("tiny-1", "adapter", True),
        _row("tiny-1", "tool_augmented", True),
    ])
    assert small["paired_comparison"]["adapter_quality_improved_without_regressions"] is True
    assert small["statistical_gate"]["adapter_passed"] is False


def test_evaluate_rows_recomputes_case_hash_from_prompt_and_acceptance_criteria() -> None:
    rows = [_row("case-hash", system, True) for system in ("base", "adapter", "tool_augmented")]
    rows[1]["case_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="does not match the canonical prompt"):
        evaluate_rows(rows)

    rows = [_row("case-content-change", system, True) for system in ("base", "adapter", "tool_augmented")]
    rows[2]["case_contract"]["acceptance_criteria"] = ["a changed criterion"]
    with pytest.raises(ValueError, match="does not match the canonical prompt"):
        evaluate_rows(rows)


def test_evaluate_rows_rejects_invalid_case_contract_structure() -> None:
    rows = [_row("bad-case-contract", system, True) for system in ("base", "adapter", "tool_augmented")]
    rows[0]["case_contract"]["unexpected"] = "value"
    with pytest.raises(ValueError, match="case_contract must contain only"):
        evaluate_rows(rows)


def test_evaluate_rows_rejects_failed_status_with_all_checks_passing() -> None:
    rows = [_row("case-status", system, True) for system in ("base", "adapter", "tool_augmented")]
    rows[0]["verification"]["status"] = "failed"
    with pytest.raises(ValueError, match="contradicts all-passing"):
        evaluate_rows(rows)


def test_release_gate_recomputes_exact_p_value_instead_of_trusting_reported_boolean() -> None:
    rows = []
    for index in range(30):
        base_pass = index >= 10
        for system in ("base", "adapter", "tool_augmented"):
            passed = True if system != "base" and index < 10 else base_pass
            rows.append(_row(f"tamper-case-{index}", system, passed))
    evaluation = evaluate_rows(rows)
    evaluation["statistical_gate"]["adapter_mcnemar_exact_p_value"] = 0.0
    with pytest.raises(ValueError, match="does not match paired outcome counts"):
        decide(_manifest(), evaluation, _mobile(_manifest()))


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



def test_evaluate_rows_rejects_model_identity_changes_between_cases() -> None:
    rows = []
    for case_id in ("case-a", "case-b"):
        for system in ("base", "adapter", "tool_augmented"):
            rows.append(_row(case_id, system, True))
    rows[-2]["model_id"] = "different-adapter-build"
    with pytest.raises(ValueError, match="Model identity changed"):
        evaluate_rows(rows)


def test_evaluate_rows_rejects_weaker_than_release_minimum_thresholds() -> None:
    rows = [_row("case-threshold", system, True) for system in ("base", "adapter", "tool_augmented")]
    with pytest.raises(ValueError, match="at least 30"):
        evaluate_rows(rows, minimum_heldout_cases=10)
    with pytest.raises(ValueError, match="no higher than 0.05"):
        evaluate_rows(rows, significance_alpha=0.1)


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
    rows = []
    for index in range(30):
        base_pass = index >= 10
        for system in ("base", "adapter", "tool_augmented"):
            passed = True if system != "base" and index < 10 else base_pass
            rows.append(_row(f"case-{index}", system, passed))
    evaluation = evaluate_rows(rows)
    manifest = _manifest()

    assert evaluation["statistical_gate"]["minimum_heldout_cases"] == 30
    assert evaluation["statistical_gate"]["adapter_mcnemar_exact_p_value"] < 0.05
    decision = decide(manifest, evaluation, _mobile(manifest), manifest_sha256="b" * 64)

    assert decision["releasable"] is True
    assert decision["heldout_quality_gate"]["passed"] is True
    assert decision["mobile_gate"]["passed"] is True
    assert decision["adapter_artifacts_sha256"] == "a" * 64


def test_release_gate_reconciles_paired_counts_with_success_rates() -> None:
    rows = []
    for index in range(30):
        base_pass = index >= 10
        for system in ("base", "adapter", "tool_augmented"):
            passed = True if system != "base" and index < 10 else base_pass
            rows.append(_row(f"paired-count-{index}", system, passed))
    evaluation = evaluate_rows(rows)
    paired = evaluation["paired_comparison"]
    paired["adapter_improvements_over_base"] = 9
    paired["adapter_unchanged_vs_base"] = 21
    evaluation["statistical_gate"]["adapter_mcnemar_exact_p_value"] = exact_mcnemar_pvalue(9, 0)

    with pytest.raises(ValueError, match="do not reconcile with system success counts"):
        decide(_manifest(), evaluation, _mobile(_manifest()))


def test_release_gate_rejects_tampered_statistical_pass_flag() -> None:
    rows = []
    for index in range(30):
        base_pass = index >= 10
        for system in ("base", "adapter", "tool_augmented"):
            passed = True if system != "base" and index < 10 else base_pass
            rows.append(_row(f"stat-flag-{index}", system, passed))
    evaluation = evaluate_rows(rows)
    evaluation["statistical_gate"]["adapter_passed"] = False

    with pytest.raises(ValueError, match="adapter statistical-pass flag is inconsistent"):
        decide(_manifest(), evaluation, _mobile(_manifest()))


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



def test_release_gate_reports_negative_adapter_delta_as_blocked_not_exception() -> None:
    rows = []
    for index in range(30):
        base_pass = True
        adapter_pass = index != 0
        for system in ("base", "adapter", "tool_augmented"):
            passed = base_pass if system in ("base", "tool_augmented") else adapter_pass
            rows.append(_row(f"negative-delta-{index}", system, passed))
    evaluation = evaluate_rows(rows)
    manifest = _manifest()

    assert evaluation["paired_comparison"]["adapter_success_rate_delta"] < 0
    decision = decide(manifest, evaluation, _mobile(manifest))

    assert decision["releasable"] is False
    assert decision["heldout_quality_gate"]["passed"] is False
    assert decision["heldout_quality_gate"]["adapter_verified_success_rate"] < decision["heldout_quality_gate"]["base_verified_success_rate"]
    assert decision["mobile_gate"]["passed"] is True

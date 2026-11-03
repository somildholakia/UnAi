from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from training.prepare_corpus import SPECIALISTS, prepare


def _record(specialist: str, group: str, text: str, *, passed: bool = True, consented: bool = True) -> dict:
    return {
        "specialist": specialist,
        "messages": [
            {"role": "user", "content": f"{text} request"},
            {"role": "assistant", "content": f"{text} response"},
        ],
        "outcome": "passed" if passed else "failed",
        "verification": {
            "status": "passed" if passed else "failed",
            "checks": [{"name": "isolated regression suite", "passed": passed}],
        },
        "provenance": {
            "source": "consented internal evaluation",
            "license": "Apache-2.0",
            "consented": consented,
            "repository": group,
        },
    }


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_prepare_splits_each_specialist_by_repository_without_leakage(tmp_path: Path) -> None:
    rows = []
    for specialist in SPECIALISTS:
        for index in range(6):
            rows.append(
                _record(
                    specialist,
                    f"repo-{index}",
                    f"{specialist} example {index}",
                )
            )
    source = tmp_path / "raw.jsonl"
    output = tmp_path / "prepared"
    _write_rows(source, rows)

    manifest = prepare(source, output, seed=19, eval_fraction=0.25)

    assert manifest["schema"] == "unai.specialist-sft-corpus.v1"
    assert manifest["accepted_records"] == 18
    assert manifest["rejected_records"] == 0
    assert manifest["source_file_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["source_file_bytes"] == source.stat().st_size
    for specialist in SPECIALISTS:
        split = manifest["specialists"][specialist]
        train = [json.loads(line) for line in (output / split["train_file"]).read_text().splitlines()]
        evaluation = [json.loads(line) for line in (output / split["eval_file"]).read_text().splitlines()]
        assert train and evaluation
        assert {row["specialist"] for row in train + evaluation} == {specialist}
        assert {row["group_id"] for row in train}.isdisjoint(
            {row["group_id"] for row in evaluation}
        )
        assert all(row["provenance"]["consented"] is True for row in train + evaluation)
        assert all(row["verification"]["status"] == "passed" for row in train + evaluation)
        assert all(row["record_id"] for row in train + evaluation)


def test_prepare_is_deterministic_and_preserves_licensing_and_verification_metadata(tmp_path: Path) -> None:
    rows = [
        _record(role, f"repository-{index}", f"{role}-{index}")
        for role in SPECIALISTS
        for index in range(4)
    ]
    source = tmp_path / "raw.jsonl"
    _write_rows(source, rows)

    first = prepare(source, tmp_path / "first", seed=123)
    second = prepare(source, tmp_path / "second", seed=123)

    for role in SPECIALISTS:
        assert first["specialists"][role]["train_sha256"] == second["specialists"][role]["train_sha256"]
        assert first["specialists"][role]["eval_sha256"] == second["specialists"][role]["eval_sha256"]
    example = json.loads((tmp_path / "first" / "next_action.train.jsonl").read_text().splitlines()[0])
    assert example["provenance"]["license"] == "Apache-2.0"
    assert example["verification"]["check_names"] == ["isolated regression suite"]


def test_prepare_rejects_unconsented_unlicensed_and_unverified_records(tmp_path: Path) -> None:
    rows = [
        _record(role, f"repo-{index}", f"{role}-{index}")
        for role in SPECIALISTS
        for index in range(5)
    ]
    rows[0]["provenance"]["consented"] = False
    rows[1]["provenance"]["license"] = "unknown"
    rows[2]["verification"]["checks"][0]["passed"] = False
    rows[2]["outcome"] = "failed"

    source = tmp_path / "raw.jsonl"
    _write_rows(source, rows)
    manifest = prepare(source, tmp_path / "prepared")

    assert manifest["rejected_records"] == 3
    assert manifest["accepted_records"] == len(rows) - 3


def test_prepare_redacts_secrets_and_deduplicates_messages(tmp_path: Path) -> None:
    rows = [
        _record(role, f"repo-{index}", f"{role}-{index}")
        for role in SPECIALISTS
        for index in range(3)
    ]
    rows[0]["messages"][1]["content"] = "Result used api_key = 'sk-abcdefghijklmnopqrstuvwxyz1234'"
    rows.append(dict(rows[0]))
    rows[-1] = json.loads(json.dumps(rows[0]))

    source = tmp_path / "raw.jsonl"
    _write_rows(source, rows)
    manifest = prepare(source, tmp_path / "prepared")

    assert manifest["redactions"] >= 1
    assert manifest["duplicate_records"] == 1
    all_output = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "prepared").glob("*.jsonl")
    )
    assert "sk-abcdefghijklmnopqrstuvwxyz1234" not in all_output
    assert "[REDACTED_SECRET]" in all_output


def test_prepare_requires_two_repository_groups_per_specialist(tmp_path: Path) -> None:
    rows = [_record(role, "one-repository", f"{role}-single") for role in SPECIALISTS]
    source = tmp_path / "raw.jsonl"
    _write_rows(source, rows)

    with pytest.raises(ValueError, match="at least two distinct"):
        prepare(source, tmp_path / "prepared")


def test_prepare_rejects_invalid_eval_fraction_and_missing_source(tmp_path: Path) -> None:
    source = tmp_path / "raw.jsonl"
    _write_rows(
        source,
        [
            _record(role, f"repo-{index}", f"{role}-{index}")
            for role in SPECIALISTS
            for index in range(2)
        ],
    )
    with pytest.raises(ValueError, match="between 0.05 and 0.5"):
        prepare(source, tmp_path / "output", eval_fraction=0.8)
    with pytest.raises(FileNotFoundError):
        prepare(tmp_path / "missing.jsonl", tmp_path / "other")



def test_prepare_validates_all_specialists_before_replacing_existing_outputs(tmp_path: Path) -> None:
    valid_rows = [
        _record(specialist, f"repo-{index}", f"{specialist}-{index}")
        for specialist in SPECIALISTS
        for index in range(4)
    ]
    source = tmp_path / "raw.jsonl"
    output = tmp_path / "prepared"
    _write_rows(source, valid_rows)
    prepare(source, output, seed=31)

    before = {
        path.name: path.read_bytes()
        for path in output.iterdir()
        if path.is_file()
    }
    invalid_rows = [
        _record(specialist, f"repo-{index}", f"changed-{specialist}-{index}")
        for specialist in SPECIALISTS
        for index in (range(4) if specialist != "verifier" else range(1))
    ]
    _write_rows(source, invalid_rows)

    with pytest.raises(ValueError, match="at least two distinct"):
        prepare(source, output, seed=31)

    after = {
        path.name: path.read_bytes()
        for path in output.iterdir()
        if path.is_file()
    }
    assert after == before

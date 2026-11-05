from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from training.audit_corpus import audit
from training.collect_synthetic import generate
from training.prepare_corpus import SPECIALISTS, prepare


def test_synthetic_collection_is_deterministic_and_has_auditable_provenance(tmp_path: Path) -> None:
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    first_manifest = generate(first, groups=4, examples_per_group=8, seed=77)
    second_manifest = generate(second, groups=4, examples_per_group=8, seed=77)

    assert first.read_bytes() == second.read_bytes()
    assert first_manifest["source_file_sha256"] == hashlib.sha256(first.read_bytes()).hexdigest()
    assert first_manifest["total_records"] == 4 * 8 * len(SPECIALISTS)
    assert first_manifest["records_per_specialist"] == {role: 32 for role in SPECIALISTS}
    assert (tmp_path / "first.jsonl.manifest.json").is_file()
    assert second_manifest["limitations"] == first_manifest["limitations"]

    records = [json.loads(line) for line in first.read_text(encoding="utf-8").splitlines()]
    assert {row["specialist"] for row in records} == set(SPECIALISTS)
    assert all(row["provenance"]["source_type"] == "synthetic" for row in records)
    assert all(row["provenance"]["generator"] == "training.collect_synthetic" for row in records)
    assert all(row["verification"]["method"] == "deterministic_synthetic_contract_v1" for row in records)
    assert all(row["outcome"] == "passed" for row in records)


def test_generated_corpus_passes_audit_and_repository_disjoint_preparation(tmp_path: Path) -> None:
    source = tmp_path / "synthetic.jsonl"
    generate(source, groups=6, examples_per_group=8, seed=991)

    report = audit(source)
    assert report["all_specialists_split_ready"] is True
    assert report["eligible_records"] == 6 * 8 * len(SPECIALISTS)
    assert report["source_types"] == {"synthetic": 144}

    output = tmp_path / "prepared"
    manifest = prepare(source, output, seed=991, eval_fraction=0.25)
    assert manifest["accepted_records"] == 144
    assert manifest["rejected_records"] == 0

    for role in SPECIALISTS:
        item = manifest["specialists"][role]
        train = [json.loads(line) for line in (output / item["train_file"]).read_text().splitlines()]
        evaluation = [json.loads(line) for line in (output / item["eval_file"]).read_text().splitlines()]
        assert train and evaluation
        assert {row["group_id"] for row in train}.isdisjoint({row["group_id"] for row in evaluation})
        assert all(row["provenance"]["source_type"] == "synthetic" for row in train + evaluation)


def test_synthetic_generator_rejects_too_few_groups_bad_license_and_oversized_request(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="groups"):
        generate(tmp_path / "one.jsonl", groups=1)
    with pytest.raises(ValueError, match="output license"):
        generate(tmp_path / "bad-license.jsonl", groups=2, license_name="unknown")
    with pytest.raises(ValueError, match="100,000-record"):
        generate(tmp_path / "too-large.jsonl", groups=5000, examples_per_group=1000)


def test_synthetic_generator_does_not_overwrite_existing_output_when_contract_fails(tmp_path: Path, monkeypatch) -> None:
    from training import collect_synthetic

    output = tmp_path / "existing.jsonl"
    output.write_text("keep this file\n", encoding="utf-8")
    monkeypatch.setitem(collect_synthetic._BUILDERS, "planner", lambda *_args: ([], False))

    with pytest.raises(RuntimeError, match="contract verification failed"):
        generate(output, groups=2, examples_per_group=2)

    assert output.read_text(encoding="utf-8") == "keep this file\n"

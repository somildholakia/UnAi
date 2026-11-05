from __future__ import annotations

import json

from training.audit_corpus import audit


def _record(specialist: str, group: str, prompt: str) -> dict:
    return {
        "specialist": specialist,
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "Inspect the approved evidence first."},
        ],
        "outcome": "passed",
        "verification": {
            "status": "passed",
            "checks": [{"name": "independent acceptance suite", "passed": True}],
        },
        "provenance": {
            "source": "consented fixture source",
            "license": "Apache-2.0",
            "consented": True,
            "repository": group,
        },
    }


def test_audit_reports_split_readiness_without_writing_outputs(tmp_path):
    source = tmp_path / "trajectories.jsonl"
    rows = [
        _record(role, group, f"{role} task from {group}")
        for role in ("next_action", "planner", "verifier")
        for group in ("repo-one", "repo-two")
    ]
    source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    report = audit(source)

    assert report["schema"] == "unai.specialist-corpus-audit.v1"
    assert report["all_specialists_split_ready"] is True
    assert report["ready_specialists"] == 3
    assert report["eligible_records"] == 6
    assert all(item["distinct_groups"] == 2 for item in report["specialists"].values())
    assert report["read_only"] is True
    assert sorted(path.name for path in tmp_path.iterdir()) == ["trajectories.jsonl"]


def test_audit_rejects_unconsented_and_unverified_records(tmp_path):
    source = tmp_path / "trajectories.jsonl"
    invalid = _record("next_action", "repo-private", "private prompt")
    invalid["provenance"]["consented"] = False
    invalid2 = _record("planner", "repo-unverified", "unverified prompt")
    invalid2["verification"]["checks"][0]["passed"] = False
    source.write_text(
        json.dumps(invalid) + "\n" + json.dumps(invalid2) + "\n",
        encoding="utf-8",
    )

    report = audit(source)

    assert report["eligible_records"] == 0
    assert report["rejected_records"] == 2
    assert report["all_specialists_split_ready"] is False
    assert report["rejected_reasons"]["missing_consent_or_provenance"] == 1
    assert report["rejected_reasons"]["missing_passing_verification"] == 1
    rendered = json.dumps(report)
    assert "private prompt" not in rendered
    assert "repo-private" not in rendered


def test_audit_counts_duplicates_without_leaking_source_content(tmp_path):
    source = tmp_path / "trajectories.jsonl"
    row = _record("next_action", "repo-one", "same prompt")
    source.write_text(
        json.dumps(row) + "\n" + json.dumps(row) + "\n",
        encoding="utf-8",
    )

    report = audit(source)

    assert report["eligible_records"] == 1
    assert report["duplicate_records"] == 1
    assert report["rejected_records"] == 0
    assert report["rejected_reasons"] == {}


def test_audit_fails_closed_for_oversized_input(tmp_path, monkeypatch):
    import training.audit_corpus as corpus_audit

    source = tmp_path / "oversized.jsonl"
    source.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(corpus_audit, "_MAX_INPUT_BYTES", 1)

    try:
        audit(source)
    except ValueError as exc:
        assert "2 GiB" in str(exc)
    else:
        raise AssertionError("oversized corpus should be rejected")



def test_audit_drains_oversized_lines_in_bounded_chunks_and_hashes_all_bytes(tmp_path, monkeypatch):
    import hashlib
    import training.prepare_corpus as corpus

    monkeypatch.setattr(corpus, "_MAX_LINE_BYTES", 32)
    source = tmp_path / "long-line.jsonl"
    payload = b"x" * 100 + b"\\n"
    source.write_bytes(payload)

    report = audit(source)

    assert report["input_records"] == 1
    assert report["oversized_records"] == 1
    assert report["rejected_records"] == 1
    assert report["source_file_bytes"] == len(payload)
    assert report["source_file_sha256"] == hashlib.sha256(payload).hexdigest()

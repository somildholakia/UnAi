import json
from pathlib import Path

from training.prepare_dataset import prepare
from training.prepare_preferences import prepare as prepare_preferences


def test_dataset_preparation_requires_verification_consent_and_license(tmp_path: Path) -> None:
    source, output = tmp_path / "raw.jsonl", tmp_path / "prepared.jsonl"
    accepted = {
        "messages": [{"role": "user", "content": "Fix this"},
                     {"role": "assistant", "content": "Use api_key=abcdefghijklmnop and rerun tests."}],
        "outcome": "passed",
        "verification": {"status": "passed", "checks": [{"name": "tests", "passed": True}]},
        "provenance": {"source": "synthetic owned sample", "license": "CC0-1.0", "consented": True},
    }
    rejected = {**accepted, "provenance": {"source": "unknown", "license": "unknown", "consented": False}}
    source.write_text("\n".join(json.dumps(row) for row in (accepted, rejected)), encoding="utf-8")

    report = prepare(source, output)
    assert report["kept"] == 1
    assert report["rejected"] == 1
    assert report["redactions"] == 1
    prepared = json.loads(output.read_text(encoding="utf-8"))
    assert "[REDACTED_SECRET]" in prepared["messages"][1]["content"]


def test_preference_preparation_requires_a_verified_chosen_answer(tmp_path: Path) -> None:
    source, output = tmp_path / "preferences.jsonl", tmp_path / "clean.jsonl"
    row = {
        "prompt": [{"role": "user", "content": "Fix bug"}],
        "chosen": [{"role": "assistant", "content": "Patch and test."}],
        "rejected": [{"role": "assistant", "content": "Guess without testing."}],
        "chosen_verification": {"passed": True},
        "rejected_verification": {"passed": False},
        "provenance": {"source": "own synthetic task", "license": "CC0-1.0", "consented": True},
    }
    source.write_text(json.dumps(row), encoding="utf-8")
    report = prepare_preferences(source, output)
    assert report["kept"] == 1
    assert len(output.read_text(encoding="utf-8").splitlines()) == 1

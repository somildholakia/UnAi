from __future__ import annotations

from training.prepare_dataset import sanitise


def test_sanitise_redacts_provider_tokens_connection_urls_and_private_keys() -> None:
    examples = [
        "github_pat_" + "G" * 32,
        "AKIA" + "A" * 16,
        "sk_" + "live_" + "S" * 24,
        "npm_" + "N" * 40,
        "hf_" + "H" * 32,
        "xoxb-" + "1" * 12 + "-" + "2" * 12 + "-" + "S" * 24,
        "Bearer " + "B" * 40,
        "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 20,
        "postgres://" + "unai:" + "p" * 24 + "@db.invalid:5432/app",
        "-----BEGIN PRIVATE KEY-----" + chr(10) + "K" * 50
        + chr(10) + "-----END PRIVATE KEY-----",
        "https://storage.invalid/object?sig=" + "S" * 40,
    ]
    source = "Keep this sentence.\n" + "\n".join(examples)

    cleaned, count = sanitise(source)

    assert count == len(examples)
    assert "Keep this sentence." in cleaned
    assert cleaned.count("[REDACTED_SECRET]") == len(examples)
    assert all(secret not in cleaned for secret in examples)

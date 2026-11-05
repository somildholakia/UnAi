from __future__ import annotations

from training.prepare_corpus import _provenance


def _base() -> dict:
    return {
        "source": "fixture source",
        "license": "Apache-2.0",
        "consented": True,
        "repository": "sample-repository",
    }


def test_public_source_requires_immutable_revision_and_https_license_evidence() -> None:
    row = _base()
    row.update(
        {
            "source_type": "public",
            "source_url": "https://example.org/datasets/trajectories",
            "source_revision": "0123456789abcdef0123456789abcdef01234567",
            "license_url": "https://example.org/datasets/LICENSE",
        }
    )
    assert _provenance(row) is not None

    mutable_revision = dict(row, source_revision="main")
    assert _provenance(mutable_revision) is None

    no_revision = dict(row)
    no_revision.pop("source_revision")
    assert _provenance(no_revision) is None

    insecure_source = dict(row, source_url="http://example.org/data")
    assert _provenance(insecure_source) is None

    insecure_license = dict(row, license_url="javascript:alert(1)")
    assert _provenance(insecure_license) is None


def test_synthetic_source_requires_generator_and_rights_basis() -> None:
    row = _base()
    row.update(
        {
            "source_type": "synthetic",
            "generator": "training.collect_synthetic",
            "generator_version": "1.0.0",
            "rights_basis": "original locally generated examples",
        }
    )
    assert _provenance(row) is not None

    incomplete = dict(row)
    incomplete.pop("rights_basis")
    assert _provenance(incomplete) is None


def test_provenance_source_is_sanitized_before_it_reaches_prepared_outputs() -> None:
    row = _base()
    row["source"] = "fixture source api_key=sk-abcdefghijklmnopqrstuvwxyz1234"
    clean = _provenance(row)
    assert clean is not None
    assert "sk-abcdefghijklmnopqrstuvwxyz1234" not in clean["source"]
    assert "[REDACTED_SECRET]" in clean["source"]

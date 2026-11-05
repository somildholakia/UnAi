from __future__ import annotations

import pytest

from training.metrics import exact_mcnemar_pvalue


def test_exact_mcnemar_uses_two_sided_paired_discordant_tail() -> None:
    assert exact_mcnemar_pvalue(10, 0) == pytest.approx(2 / 1024)
    assert exact_mcnemar_pvalue(0, 10) == pytest.approx(2 / 1024)
    assert exact_mcnemar_pvalue(6, 0) == pytest.approx(2 / 64)
    assert exact_mcnemar_pvalue(5, 0) == pytest.approx(2 / 32)


def test_exact_mcnemar_treats_ties_as_no_directional_evidence() -> None:
    assert exact_mcnemar_pvalue(0, 0) == 1.0
    assert exact_mcnemar_pvalue(8, 8) == 1.0
    assert exact_mcnemar_pvalue(3, 2) == exact_mcnemar_pvalue(2, 3)


@pytest.mark.parametrize(
    "improvements,regressions",
    [(-1, 0), (0, -1), (True, 0), (0, False), (1.5, 0)],
)
def test_exact_mcnemar_rejects_invalid_counts(improvements, regressions) -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        exact_mcnemar_pvalue(improvements, regressions)

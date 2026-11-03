"""Small-sample paired outcome statistics used by M5 evaluation and release gates."""
from __future__ import annotations

import math


def exact_mcnemar_pvalue(improvements: int, regressions: int) -> float:
    """Two-sided exact McNemar/binomial p-value for paired discordant outcomes.

    The null hypothesis assigns equal probability to an improvement and a
    regression among discordant pairs. A tie contributes no evidence.
    """
    for name, value in (("improvements", improvements), ("regressions", regressions)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer.")
    discordant = improvements + regressions
    if discordant == 0 or improvements == regressions:
        return 1.0
    lower = min(improvements, regressions)
    # Sum the lower binomial tail in log space to avoid overflow/underflow.
    logs = [
        math.lgamma(discordant + 1)
        - math.lgamma(k + 1)
        - math.lgamma(discordant - k + 1)
        - discordant * math.log(2.0)
        for k in range(lower + 1)
    ]
    pivot = max(logs)
    log_tail = pivot + math.log(math.fsum(math.exp(value - pivot) for value in logs))
    return min(1.0, 2.0 * math.exp(log_tail))

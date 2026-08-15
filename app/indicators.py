from __future__ import annotations

import statistics
from collections.abc import Sequence


def ema(values: Sequence[float], length: int) -> float | None:
    if not values:
        return None
    alpha = 2.0 / (length + 1.0)
    out = float(values[0])
    for x in values[1:]:
        out = alpha * float(x) + (1.0 - alpha) * out
    return out


def median_positive(values: Sequence[float], floor: float = 1.0) -> float:
    vals = [float(x) for x in values if x >= 0]
    if not vals:
        return floor
    return max(floor, statistics.median(vals))


def pct_change(a: float, b: float) -> float:
    if not a:
        return 0.0
    return (b / a - 1.0) * 100.0

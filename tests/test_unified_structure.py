from app.models import Bucket
from app.unified_structure import unified_structure_profile


def row(index: int, low: float, high: float, close: float, volume: float) -> Bucket:
    return Bucket(float(index * 15), low, high, low, close, volume, 10)


def test_tightening_high_base_is_front_side_supply_evidence():
    rows = []
    for index in range(12):
        width = 0.08 if index < 4 else 0.05 if index < 8 else 0.025
        low = 1.0 + index * 0.004
        rows.append(row(index, low, low + width, low + width * 0.8, 1200 - index * 60))
    profile = unified_structure_profile(rows, price=rows[-1].close, vwap=1.01)
    assert profile["compression_quality"] >= 70
    assert profile["supply"] >= 60
    assert profile["phase"] == "FRONT_SIDE"


def test_deep_fade_with_lower_highs_is_backside():
    rows = [row(index, 2.0 - index * 0.08, 2.1 - index * 0.08, 2.02 - index * 0.08, 1000) for index in range(10)]
    profile = unified_structure_profile(rows, price=rows[-1].close, vwap=1.8)
    assert profile["phase"] == "BACKSIDE"
    assert profile["lifecycle"] < 50

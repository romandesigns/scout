from app.momentum_zones import find_momentum_zones, match_detections_to_zones


def bar(start_ts, price, high=None, low=None, volume=1000):
    return {"start_ts": start_ts, "open": price, "close": price,
            "high": high if high is not None else price, "low": low if low is not None else price,
            "volume": volume}


def test_finds_a_clear_expansion_from_a_flat_base():
    bars = [bar(i * 60, 10.0) for i in range(5)]  # flat base
    bars += [bar((5 + i) * 60, 10.0 + i * 0.15, high=10.0 + i * 0.15) for i in range(6)]  # rise to ~10.75 (+7.5%)
    zones = find_momentum_zones(bars, expansion_pct=2.0, base_window_seconds=300, horizon_seconds=900, dedupe_seconds=600)
    assert len(zones) == 1
    zone = zones[0]
    assert zone["expansion_pct"] > 2.0
    assert zone["peak_price"] > zone["base_price"]


def test_no_zone_on_flat_or_declining_price():
    bars = [bar(i * 60, 10.0 - i * 0.01) for i in range(20)]
    assert find_momentum_zones(bars) == []


def test_does_not_flag_every_bar_of_one_fast_thrust_separately():
    # A quick thrust that completes well inside one base window (5 min) must
    # not be counted as a separate zone for every bar that individually
    # clears the threshold once the rise is underway.
    bars = [bar(i * 60, 10.0) for i in range(3)]
    bars += [bar((3 + i) * 60, 10.0 + i * 0.2, high=10.0 + i * 0.2) for i in range(4)]  # 10.0 -> 10.8 in 4 min
    bars += [bar((7 + i) * 60, 10.8) for i in range(5)]  # plateau
    zones = find_momentum_zones(bars, expansion_pct=2.0, base_window_seconds=300, dedupe_seconds=600)
    assert len(zones) == 1  # not one zone per bar that clears the threshold


def test_two_separated_expansions_produce_two_zones():
    bars = [bar(i * 60, 10.0) for i in range(5)]
    bars += [bar((5 + i) * 60, 10.0 + i * 0.15, high=10.0 + i * 0.15) for i in range(5)]  # first expansion
    bars += [bar((10 + i) * 60, 10.75) for i in range(15)]  # flat plateau, long enough to reset the rolling base
    bars += [bar((25 + i) * 60, 10.75 + i * 0.15, high=10.75 + i * 0.15) for i in range(5)]  # second expansion
    zones = find_momentum_zones(bars, expansion_pct=2.0, base_window_seconds=300, dedupe_seconds=600)
    assert len(zones) == 2


def test_match_detections_to_zones_finds_the_earliest_qualifying_hit_within_the_lead_window():
    zones = [{"onset_at": 1000.0, "peak_at": 1300.0, "base_price": 10.0, "peak_price": 10.8, "expansion_pct": 8.0}]
    detections = [
        {"id": 1, "detected_at": 850.0},   # before the lead window -- too early, doesn't count
        {"id": 2, "detected_at": 950.0},   # inside [onset-120, peak] -- earliest qualifying hit
        {"id": 3, "detected_at": 1100.0},  # also inside, but later than id 2
    ]
    result = match_detections_to_zones(zones, detections, lead_seconds=120.0)
    assert len(result) == 1
    assert result[0]["caught"] is True
    assert result[0]["matched_finding_id"] == 2
    assert result[0]["lead_seconds"] == 50.0  # onset 1000 - detected_at 950


def test_match_detections_to_zones_reports_a_miss_when_nothing_qualifies_in_window():
    zones = [{"onset_at": 1000.0, "peak_at": 1300.0, "base_price": 10.0, "peak_price": 10.8, "expansion_pct": 8.0}]
    detections = [{"id": 1, "detected_at": 1400.0}]  # after the zone's peak
    result = match_detections_to_zones(zones, detections, lead_seconds=120.0)
    assert result[0]["caught"] is False
    assert result[0]["matched_finding_id"] is None
    assert result[0]["lead_seconds"] is None


def test_match_detections_to_zones_handles_no_detections_at_all():
    zones = [{"onset_at": 1000.0, "peak_at": 1300.0, "base_price": 10.0, "peak_price": 10.8, "expansion_pct": 8.0}]
    result = match_detections_to_zones(zones, [])
    assert result[0]["caught"] is False

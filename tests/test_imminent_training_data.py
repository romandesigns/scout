from scripts.build_imminent_training_data import feature_row, build_rows


def quotes():
    return [
        {"ts": float(ts), "bid": bid, "ask": ask, "bid_size": bs, "ask_size": ass}
        for ts, bid, ask, bs, ass in [
            (100, 9.99, 10.01, 100, 100),
            (115, 10.04, 10.06, 300, 100),
            (120, 10.09, 10.11, 400, 100),
            (140, 10.20, 10.22, 500, 100),
        ]
    ]


def test_feature_row_uses_only_information_available_at_sample_time():
    trades = [(100.0, 10.0, 10.0), (115.0, 10.05, 20.0), (120.0, 10.10, 30.0), (140.0, 10.21, 99.0)]
    row = feature_row("TEST", "2026-08-21", 120.0, trades, quotes())
    assert row is not None
    assert row["price"] == 10.10
    assert row["trades_30s"] == 3
    assert row["dollar_30s"] == 10 * 10 + 10.05 * 20 + 10.10 * 30
    assert row["bid_ask_imbalance"] > 0


def test_positive_label_requires_completion_15_to_30_seconds_ahead():
    trades = [(100.0, 10.0, 10.0), (120.0, 10.05, 10.0), (140.0, 10.21, 10.0)]
    rows = build_rows(
        "TEST", "2026-08-21", trades, quotes(), sample_seconds=5,
        expansion_pct=2, horizon_seconds=60, lead_min=15, lead_max=30,
        max_pre_move_extension_pct=0.5, negative_ratio=10, seed="test",
    )
    positives = {row["sample_at"] for row in rows if row["label"]}
    assert positives == {110.0, 115.0}


def test_zero_negative_ratio_retains_all_available_windows():
    trades = [(100.0, 10.0, 10.0), (120.0, 10.05, 10.0), (140.0, 10.21, 10.0)]
    rows = build_rows(
        "TEST", "2026-08-21", trades, quotes(), sample_seconds=5,
        expansion_pct=2, horizon_seconds=60, lead_min=15, lead_max=30,
        max_pre_move_extension_pct=0.5, negative_ratio=0, seed="test",
    )
    assert len(rows) == 9
    assert sum(row["label"] for row in rows) == 2


def test_label_searches_all_completions_in_target_window(monkeypatch):
    trades = [(100.0, 10.0, 10.0), (120.0, 10.05, 10.0), (130.0, 9.9, 10.0), (140.0, 10.21, 10.0)]
    candidate_moves = [
        {"base_at": 130.0, "base_price": 9.9, "completed_at": 135.0},
        {"base_at": 100.0, "base_price": 10.0, "completed_at": 140.0},
    ]
    monkeypatch.setattr("scripts.build_imminent_training_data.objective_moves", lambda *args, **kwargs: candidate_moves)
    rows = build_rows(
        "TEST", "2026-08-21", trades, quotes(), sample_seconds=5,
        expansion_pct=2, horizon_seconds=60, lead_min=15, lead_max=30,
        max_pre_move_extension_pct=0.5, negative_ratio=0, seed="test",
    )
    row = next(item for item in rows if item["sample_at"] == 110.0)
    assert row["label"] == 1
    assert row["target_completion_at"] == 140.0

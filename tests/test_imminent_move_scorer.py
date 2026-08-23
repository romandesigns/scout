from scripts.imminent_move_scorer import objective_moves, score


def test_objective_move_is_completed_from_rolling_low_within_horizon():
    moves = objective_moves("TEST", [(0, 10.0), (20, 10.1), (40, 10.21)], expansion_pct=2, horizon_seconds=60)
    assert len(moves) == 1
    assert moves[0]["base_price"] == 10.0
    assert moves[0]["duration_seconds"] == 40


def test_strict_window_excludes_old_and_late_alerts(tmp_path):
    dataset = tmp_path / "TEST-2026-08-21-sip.ndjson"
    rows = [
        {"event_type": "trade", "source_ts": 100, "payload": {"price": 10.0}},
        {"event_type": "trade", "source_ts": 140, "payload": {"price": 10.21}},
    ]
    dataset.write_text("\n".join(__import__("json").dumps(row) for row in rows), encoding="utf-8")
    replay = [{
        "ticker": "TEST", "date": "2026-08-21",
        "findings": [
            {"detected_at": 105, "actionable_rank": "A", "quality_label": "CLEAN"},
            {"detected_at": 120, "actionable_rank": "A", "quality_label": "CLEAN"},
            {"detected_at": 135, "actionable_rank": "A", "quality_label": "CLEAN"},
        ],
    }]
    report = score(replay, tmp_path)
    assert report["objective_moves"] == 1
    assert report["moves_hit"] == 1
    assert report["moves"][0]["alert_at"] == 120
    assert report["moves"][0]["lead_seconds"] == 20
    assert report["strict_window_precision"] == 1 / 3


def test_impossible_short_moves_are_excluded_from_recall(tmp_path):
    dataset = tmp_path / "FAST-2026-08-21-sip.ndjson"
    rows = [
        {"event_type": "trade", "source_ts": 100, "payload": {"price": 10.0}},
        {"event_type": "trade", "source_ts": 110, "payload": {"price": 10.21}},
    ]
    dataset.write_text("\n".join(__import__("json").dumps(row) for row in rows), encoding="utf-8")
    replay = [{
        "ticker": "FAST", "date": "2026-08-21",
        "findings": [{"detected_at": 90, "actionable_rank": "A", "quality_label": "CLEAN"}],
    }]
    report = score(replay, tmp_path)
    assert report["objective_moves"] == 0
    assert report["moves_hit"] == 0
    assert report["actionable_findings"] == 1
    assert report["strict_window_precision"] == 0


def test_alert_before_measured_move_base_is_not_a_hit(tmp_path):
    dataset = tmp_path / "TEST-2026-08-21-sip.ndjson"
    rows = [
        {"event_type": "trade", "source_ts": 100, "payload": {"price": 10.1}},
        {"event_type": "trade", "source_ts": 110, "payload": {"price": 10.0}},
        {"event_type": "trade", "source_ts": 140, "payload": {"price": 10.21}},
    ]
    dataset.write_text("\n".join(__import__("json").dumps(row) for row in rows), encoding="utf-8")
    replay = [{
        "ticker": "TEST", "date": "2026-08-21",
        "findings": [{"detected_at": 105, "actionable_rank": "A", "quality_label": "CLEAN"}],
    }]
    report = score(replay, tmp_path)
    assert report["objective_moves"] == 1
    assert report["moves_hit"] == 0
    assert report["strict_window_precision"] == 0


def test_same_ticker_on_multiple_dates_is_scored_independently(tmp_path):
    for session_date in ("2026-08-20", "2026-08-21"):
        dataset = tmp_path / f"TEST-{session_date}-sip.ndjson"
        rows = [
            {"event_type": "trade", "source_ts": 100, "payload": {"price": 10.0}},
            {"event_type": "trade", "source_ts": 140, "payload": {"price": 10.21}},
        ]
        dataset.write_text("\n".join(__import__("json").dumps(row) for row in rows), encoding="utf-8")
    replay = [
        {"ticker": "TEST", "date": "2026-08-20", "findings": [{"detected_at": 120, "actionable_rank": "A", "quality_label": "CLEAN"}]},
        {"ticker": "TEST", "date": "2026-08-21", "findings": []},
    ]
    report = score(replay, tmp_path)
    assert report["objective_moves"] == 2
    assert report["moves_hit"] == 1
    assert report["actionable_findings"] == 1
    assert report["strict_window_precision"] == 1

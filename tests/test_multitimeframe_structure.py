from app.market import evaluate_multitimeframe_structure


def test_multitimeframe_structure_qualifies_aligned_setup():
    result = evaluate_multitimeframe_structure(
        five_minute_samples=20, five_minute_change_pct=2.0,
        one_minute_change_pct=0.4, one_minute_higher_low_ratio=2 / 3,
        change_30s_pct=0.25, change_15s_pct=0.15, change_5s_pct=0.05,
        above_vwap=True, ema_up=True, ema_bull=True,
        trades_30s=20, dollar_volume_30s=10_000,
    )
    assert result["qualified"] is True
    assert result["blockers"] == []


def test_fast_tape_can_only_veto_otherwise_aligned_setup():
    result = evaluate_multitimeframe_structure(
        five_minute_samples=20, five_minute_change_pct=2.0,
        one_minute_change_pct=0.4, one_minute_higher_low_ratio=2 / 3,
        change_30s_pct=0.25, change_15s_pct=0.15, change_5s_pct=-0.5,
        above_vwap=True, ema_up=True, ema_bull=True,
        trades_30s=20, dollar_volume_30s=10_000,
    )
    assert result["qualified"] is False
    assert result["blockers"] == ["fast_tape_clear"]

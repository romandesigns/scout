from app.market import evaluate_breakout_continuation_quality, evaluate_reentry_safety


def test_breakout_requires_immediate_freshness():
    d = evaluate_breakout_continuation_quality({
        "change5": 0.03, "change15": 0.30, "change30": 0.40
    })
    assert d["ready"] is False
    assert "fresh_5s_continuation" in d["blockers"]


def test_breakout_rejects_decelerating_tape():
    d = evaluate_breakout_continuation_quality({
        "change5": 0.20, "change15": 0.35, "change30": 0.30
    })
    assert d["ready"] is False
    assert "breakout_deceleration" in d["blockers"]


def test_breakout_accepts_fresh_persistent_move():
    d = evaluate_breakout_continuation_quality({
        "change5": 0.30, "change15": 0.25, "change30": 0.20
    })
    assert d["ready"] is True


def test_breakout_accepts_afterhours_single_bucket_freshness():
    d = evaluate_breakout_continuation_quality({
        "change5": 0.18, "change15": 0.0, "change30": 0.0
    })
    assert d["ready"] is True


def test_reentry_blocks_immediate_fade():
    d = evaluate_reentry_safety("VWAP_RECLAIM", {
        "change5": -0.01, "base_extension_pct": 0.2, "extension": 0.3
    })
    assert d["ready"] is False
    assert "fresh_5s_continuation" in d["blockers"]


def test_reentry_blocks_late_risk():
    d = evaluate_reentry_safety("REARM", {
        "change5": 0.8, "base_extension_pct": 1.0, "extension": 1.0
    })
    assert d["ready"] is False
    assert "late_risk" in d["blockers"]


def test_reentry_accepts_fresh_nonextended_reclaim():
    d = evaluate_reentry_safety("EMA_RECLAIM", {
        "change5": 0.12, "base_extension_pct": 0.2, "extension": 0.3
    })
    assert d["ready"] is True

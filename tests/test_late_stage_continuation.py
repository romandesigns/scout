from app.market import (
    evaluate_late_stage_continuation_quality,
    should_suppress_late_fresh_promotion,
)


def metrics(**overrides):
    m = {
        "change5": 0.20,
        "change15": 1.00,
        "base_extension_pct": 0.30,
        "extension": 0.60,
    }
    m.update(overrides)
    return m


def test_ignition_rejects_stale_15s_impulse_when_last_5s_is_flat():
    d = evaluate_late_stage_continuation_quality(
        "IGNITION",
        metrics(change5=0.0, change15=0.97),
    )
    assert d["ready"] is False
    assert "fresh_5s_continuation" in d["blockers"]


def test_ignition_preserves_genuinely_fresh_impulse():
    d = evaluate_late_stage_continuation_quality(
        "IGNITION",
        metrics(change5=0.35, change15=1.10),
    )
    assert d["ready"] is True
    assert d["blockers"] == []


def test_halt_pressure_is_blocked_when_already_late_risk():
    m = metrics(change5=0.80, change15=2.0, base_extension_pct=0.90)
    d = evaluate_late_stage_continuation_quality("HALT_PRESSURE", m)
    assert d["ready"] is False
    assert "late_risk" in d["blockers"]
    assert should_suppress_late_fresh_promotion("HALT_PRESSURE", m) is True


def test_halt_pressure_requires_immediate_5s_and_15s_continuation():
    d = evaluate_late_stage_continuation_quality(
        "HALT_PRESSURE",
        metrics(change5=0.10, change15=0.50),
    )
    assert d["ready"] is False
    assert "fresh_5s_continuation" in d["blockers"]
    assert "fresh_15s_continuation" in d["blockers"]


def test_halt_pressure_preserves_fresh_nonextended_pressure():
    d = evaluate_late_stage_continuation_quality(
        "HALT_PRESSURE",
        metrics(change5=0.50, change15=1.20, base_extension_pct=0.50, extension=1.2),
    )
    assert d["ready"] is True


def test_breakout_is_not_changed_by_late_stage_gate():
    d = evaluate_late_stage_continuation_quality(
        "BREAKOUT",
        metrics(change5=-0.5, change15=-0.5),
    )
    assert d["ready"] is True

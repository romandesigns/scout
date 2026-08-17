from app.market import evaluate_early_continuation_quality


def test_gate_rejects_decelerating_uncontextual_fast_path():
    d = evaluate_early_continuation_quality(
        first_leg_candidate=False, relative_activity=False,
        quality_score=100, velocity_pct=0.65, acceleration_pct=-0.65,
    )
    assert not d["ready"]


def test_gate_accepts_high_quality_reacceleration():
    d = evaluate_early_continuation_quality(
        first_leg_candidate=False, relative_activity=False,
        quality_score=100, velocity_pct=0.14, acceleration_pct=0.04,
    )
    assert d["ready"]

from app.market import evaluate_early_signal


def metrics():
    return {
        "full_warmup": True,
        "quality_score": 100,
        "base_extension_pct": 0.30,
        "change3": 0.12,
        "change5": 0.16,
        "change15": 0.14,
    }


def test_early_signal_can_fire_before_fresh_impulse_release():
    d = evaluate_early_signal(
        metrics(),
        first_leg_candidate=True,
        quality_actionable=True,
        participation_ok=True,
        structure_ok=True,
        bullish_confirmed=True,
        bearish_short=False,
        structural_failure=False,
        relative_activity=True,
        trigger_distance_pct=0.05,
        candidate_age_seconds=3.0,
    )
    assert d["ready"] is True
    assert d["score"] >= d["min_score"]


def test_early_signal_never_bypasses_quality():
    d = evaluate_early_signal(
        metrics(), first_leg_candidate=True, quality_actionable=False,
        participation_ok=True, structure_ok=True, bullish_confirmed=True,
        bearish_short=False, structural_failure=False, relative_activity=True,
        trigger_distance_pct=0.05, candidate_age_seconds=3.0,
    )
    assert d["ready"] is False
    assert "quality_actionable" in d["hard_blockers"]


def test_early_signal_never_bypasses_structure_or_participation():
    d = evaluate_early_signal(
        metrics(), first_leg_candidate=True, quality_actionable=True,
        participation_ok=False, structure_ok=False, bullish_confirmed=True,
        bearish_short=False, structural_failure=False, relative_activity=True,
        trigger_distance_pct=0.05, candidate_age_seconds=3.0,
    )
    assert d["ready"] is False
    assert "participation" in d["hard_blockers"]
    assert "structure_ok" in d["hard_blockers"]


def test_early_signal_blocks_extension():
    m = metrics(); m["base_extension_pct"] = 2.0
    d = evaluate_early_signal(
        m, first_leg_candidate=True, quality_actionable=True,
        participation_ok=True, structure_ok=True, bullish_confirmed=True,
        bearish_short=False, structural_failure=False, relative_activity=True,
        trigger_distance_pct=0.05, candidate_age_seconds=3.0,
    )
    assert d["ready"] is False
    assert "not_extended" in d["hard_blockers"]


def test_early_signal_blocks_eypt_style_canonical_late_risk():
    m = metrics()
    m["base_extension_pct"] = 0.815
    m["extension"] = 1.084
    d = evaluate_early_signal(
        m, first_leg_candidate=True, quality_actionable=True,
        participation_ok=True, structure_ok=True, bullish_confirmed=True,
        bearish_short=False, structural_failure=False, relative_activity=True,
        trigger_distance_pct=0.05, candidate_age_seconds=0.0,
    )
    assert d["ready"] is False
    assert "not_late_risk" in d["hard_blockers"]


def test_early_signal_preserves_fresh_non_late_candidate():
    m = metrics()
    m["base_extension_pct"] = 0.347
    m["extension"] = 0.347
    d = evaluate_early_signal(
        m, first_leg_candidate=True, quality_actionable=True,
        participation_ok=True, structure_ok=True, bullish_confirmed=True,
        bearish_short=False, structural_failure=False, relative_activity=True,
        trigger_distance_pct=0.12, candidate_age_seconds=0.0,
    )
    assert d["ready"] is True
    assert "not_late_risk" not in d["hard_blockers"]

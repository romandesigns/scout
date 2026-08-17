from app.market import evaluate_early_signal, evaluate_early_continuation_quality


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


def test_continuation_quality_preserves_context_backed_signal():
    d = evaluate_early_continuation_quality(
        first_leg_candidate=False,
        relative_activity=True,
        quality_score=82,
        velocity_pct=0.10,
        acceleration_pct=-0.02,
    )
    assert d["ready"] is True
    assert d["contextual"] is True


def test_continuation_quality_preserves_snwv_style_reacceleration_without_relative_activity():
    # Production USEFUL example: no first-leg/relative-activity context, but the
    # fast tape was genuinely reaccelerating.
    d = evaluate_early_continuation_quality(
        first_leg_candidate=False,
        relative_activity=False,
        quality_score=82,
        velocity_pct=0.2037,
        acceleration_pct=0.2037,
    )
    assert d["ready"] is True
    assert d["impulse_reacceleration"] is True


def test_continuation_quality_blocks_rskd_style_weak_decelerating_fast_path():
    d = evaluate_early_continuation_quality(
        first_leg_candidate=False,
        relative_activity=False,
        quality_score=82,
        velocity_pct=0.1486,
        acceleration_pct=-0.0679,
    )
    assert d["ready"] is False
    assert d["blockers"] == ["continuation_quality"]


def test_continuation_quality_blocks_eypt_style_weak_unbacked_fast_path():
    d = evaluate_early_continuation_quality(
        first_leg_candidate=False,
        relative_activity=False,
        quality_score=100,
        velocity_pct=0.1064,
        acceleration_pct=0.0021,
    )
    assert d["ready"] is False


def test_early_signal_surfaces_continuation_quality_blocker():
    m = metrics()
    m["quality_score"] = 82
    m["change3"] = 0.10
    m["change5"] = 0.1486
    m["change15"] = 0.2165
    m["base_extension_pct"] = 0.08
    d = evaluate_early_signal(
        m,
        first_leg_candidate=False,
        quality_actionable=True,
        participation_ok=True,
        structure_ok=True,
        bullish_confirmed=True,
        bearish_short=False,
        structural_failure=False,
        relative_activity=False,
        trigger_distance_pct=0.0,
        candidate_age_seconds=0.0,
    )
    assert d["ready"] is False
    assert "continuation_quality" in d["hard_blockers"]
    assert d["continuation_quality"]["ready"] is False


def test_v667_fresh_early_actionable_requires_a_rank_by_default():
    from app.market import should_allow_fresh_early_actionable
    assert should_allow_fresh_early_actionable("A") is True
    assert should_allow_fresh_early_actionable("B") is False
    assert should_allow_fresh_early_actionable("C") is False
    assert should_allow_fresh_early_actionable(None) is False

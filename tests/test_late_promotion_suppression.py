from app.market import is_late_promotion_risk, should_suppress_late_fresh_promotion


def metrics(base_extension=0.25, extension=0.25):
    return {"base_extension_pct": base_extension, "extension": extension}


def test_fresh_expansion_stages_are_suppressed_when_late():
    late = metrics(base_extension=0.85, extension=0.85)
    for stage in ("SURGE", "BREAKOUT", "IGNITION"):
        assert should_suppress_late_fresh_promotion(stage, late) is True


def test_fresh_expansion_stages_remain_eligible_when_not_late():
    fresh = metrics(base_extension=0.22, extension=0.22)
    for stage in ("SURGE", "BREAKOUT", "IGNITION"):
        assert should_suppress_late_fresh_promotion(stage, fresh) is False


def test_explicit_reentry_paths_are_not_blocked_by_fresh_stage_policy():
    late = metrics(base_extension=1.25, extension=2.25)
    for stage in ("REARM", "VWAP_RECLAIM", "EMA_RECLAIM"):
        assert should_suppress_late_fresh_promotion(stage, late) is False


def test_trace_boundary_and_suppression_boundary_match():
    assert is_late_promotion_risk(metrics(base_extension=0.75, extension=2.0)) is False
    assert is_late_promotion_risk(metrics(base_extension=0.7501, extension=2.0)) is True
    assert is_late_promotion_risk(metrics(base_extension=0.10, extension=2.0001)) is True


def test_early_stage_is_never_removed_by_late_fresh_stage_policy():
    # EARLY has its own stricter extension/trigger hard blockers; v6.6.3 must
    # preserve the optimized v6.6.2 early-signal architecture unchanged.
    assert should_suppress_late_fresh_promotion("EARLY", metrics(1.0, 2.5)) is False

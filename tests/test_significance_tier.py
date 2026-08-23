from app.config import settings
from app.models import Finding
from app.significance_tier import classify_tier, would_notify


def finding(**overrides) -> Finding:
    base = dict(
        ticker="TEST", stage="BREAKOUT", detected_at=1.0, price=2.0, score=settings.ignition_score + 3,
        vol_ratio_15s=settings.vol_ratio_trigger * 2, vol_ratio_30s=settings.vol_ratio_trigger * 2,
        change_60s_pct=settings.price_60s_trigger_pct * 2, change_30s_pct=settings.price_60s_trigger_pct * 2,
        accel_15s_pp=settings.price_60s_trigger_pct * 2, extension_pct=0.5,
        ema9=2.0, ema21=1.9, ema9_slope=0.1, vwap=1.95, above_vwap=True, quiet_break=False, evidence=[],
        actionable_rank="A", quality_label="CLEAN", engine_source="python",
        directional_efficiency=0.9, direction_reversals=0, active_bucket_ratio=0.95,
        timeliness_label=None, leg_context=None, shadow_mode=False,
        candidate_profile={"edge_validation": {"validated": True}},
    )
    base.update(overrides)
    return Finding(**base)


def row_from(f: Finding) -> dict:
    """Simulate a stored finding dict (the shape Store.list_findings returns)."""
    return dict(f.__dict__)


# --- classify_tier ---------------------------------------------------------

def test_tier1_structural_breakout():
    result = classify_tier(finding())
    assert result["tier"] == 1
    assert result["opportunity_class"] == "FIRST_MOVE"
    assert result["reaction_bounce"] is False


def test_tier1_reads_identically_from_a_stored_row_dict():
    f = finding()
    assert classify_tier(row_from(f)) == classify_tier(f)


def test_tier2_continuation_pulse_below_magnitude_bar():
    # Confirmed and clean, but none of the magnitude confirmations clear the
    # Tier 1 bar -- an ordinary continuation, not a major breakout.
    f = finding(
        vol_ratio_15s=settings.vol_ratio_trigger, vol_ratio_30s=settings.vol_ratio_trigger,
        score=settings.ignition_score, change_60s_pct=0.1, change_30s_pct=0.1, accel_15s_pp=0.0,
    )
    result = classify_tier(f)
    assert result["tier"] == 2
    assert result["reaction_bounce"] is False


def test_tier2_reclaim_continuation_breaking_a_local_high():
    f = finding(stage="RECLAIM", leg_context="RECLAIM_CONTINUATION")
    result = classify_tier(f)
    assert result["tier"] == 2
    assert result["opportunity_class"] == "SECONDARY_ENTRY"


def test_tier3_reaction_bounce_despite_clean_rank_a():
    # Rank A / quality CLEAN alone should not be enough: a choppy path with
    # excess reversals and weak sustained participation must still be
    # downgraded, per the JUNS review ("several are reaction bounces inside
    # broader rotation").
    f = finding(
        directional_efficiency=settings.quality_min_directional_efficiency * 1.1,
        direction_reversals=settings.quality_max_direction_reversals,
        active_bucket_ratio=settings.quality_min_active_ratio * 1.05,
    )
    result = classify_tier(f)
    assert result["tier"] == 3
    assert result["reaction_bounce"] is True


def test_tier3_weak_rank_or_quality():
    assert classify_tier(finding(actionable_rank="C"))["tier"] == 3
    assert classify_tier(finding(quality_label="CHOPPY"))["tier"] == 3


def test_tier3_late_information_only():
    result = classify_tier(finding(extension_pct=12.0))
    assert result["tier"] == 3
    assert result["opportunity_class"] == "LATE_INFORMATION_ONLY"


def test_tier_never_raises_on_missing_optional_fields():
    f = finding(directional_efficiency=None, direction_reversals=None, active_bucket_ratio=None)
    result = classify_tier(f)
    assert result["tier"] in {1, 2, 3}


# --- would_notify ------------------------------------------------------

def test_would_notify_true_for_a_clean_confirmed_first_move():
    result = would_notify(finding())
    assert result["would_notify"] is True


def test_would_notify_matches_between_finding_and_stored_row():
    f = finding()
    assert would_notify(row_from(f)) == would_notify(f)


def test_would_notify_false_when_shadow_mode():
    result = would_notify(finding(shadow_mode=True))
    assert result["would_notify"] is False
    assert result["reason"] == "shadow_mode"


def test_would_notify_false_when_opportunity_gate_rejects():
    result = would_notify(finding(actionable_rank="C"))
    assert result["would_notify"] is False
    assert result["reason"] == "opportunity_gate"


def test_would_notify_false_when_stage_not_user_facing():
    result = would_notify(finding(stage="EMA_RECLAIM"))
    assert result["would_notify"] is False
    assert result["reason"] == "stage_not_user_facing"


def test_would_notify_false_when_edge_not_validated():
    result = would_notify(finding(candidate_profile={"edge_validation": {"validated": False}}))
    assert result["would_notify"] is False
    assert result["reason"] == "edge_not_validated"


def test_would_notify_false_when_quality_not_clean():
    result = would_notify(finding(quality_label="DEVELOPING", actionable_rank="A"))
    # actionable_rank A alone cannot happen with non-CLEAN quality in the real
    # detector (see market.py), but would_notify must still fail closed if it
    # ever does, rather than assume the opportunity gate already covered it.
    assert result["would_notify"] is False


def test_would_notify_never_raises_on_a_bare_minimal_row():
    result = would_notify({"ticker": "X", "stage": "EARLY"})
    assert result["would_notify"] is False

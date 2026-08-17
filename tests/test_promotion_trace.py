from app.market import build_promotion_trace
from scripts.promotion_trace import summarize


def base_metrics(**overrides):
    m = {
        "full_warmup": True,
        "staircase": False,
        "quality_label": "DEVELOPING",
        "bullish_confirmed": False,
        "first_leg_release": False,
        "rejection_reasons": ["BULLISH STRUCTURE UNCONFIRMED"],
        "quality_score": 58,
        "score": 8,
        "base_extension_pct": 0.4,
        "extension": 0.5,
        "change3": 0.1,
        "change5": 0.2,
        "change15": 0.3,
        "change30": 0.4,
        "micro_resistance": 10.05,
        "price": 10.0,
    }
    m.update(overrides)
    return m


def test_trace_names_quality_blockers_without_changing_gate_result():
    trace = build_promotion_trace(
        base_metrics(), relative_activity=True, fast_single_bucket=False,
        regular_participation=True, sudden_impulse=True, bearish_short=False,
        structural_failure=False, structure_ok=True, quality_actionable=False,
        first_leg_candidate=True, candidate_age_seconds=12.5,
    )
    assert trace["gates"]["quality_actionable"] is False
    assert "quality_clean" in trace["blockers"]
    assert "bullish_confirmed" in trace["blockers"]
    assert trace["candidate_age_seconds"] == 12.5
    assert trace["late_risk"] is False


def test_trace_flags_extension_late_risk():
    trace = build_promotion_trace(
        base_metrics(base_extension_pct=1.1, quality_label="CLEAN", bullish_confirmed=True),
        relative_activity=True, fast_single_bucket=False, regular_participation=True,
        sudden_impulse=True, bearish_short=False, structural_failure=False,
        structure_ok=True, quality_actionable=True, first_leg_candidate=True,
        candidate_age_seconds=25,
    )
    assert trace["late_risk"] is True
    assert trace["gates"]["quality_actionable"] is True


def test_summary_counts_blockers_and_promotions():
    rows = [
        {"stage": "PRE_IGNITION", "quality_label": "DEVELOPING", "actionable_rank": "C", "candidate_profile": {"promotion_trace": {"blockers": ["quality_clean"], "rejection_reasons": ["CHOPPY PATH"], "late_risk": True}}},
        {"stage": "EARLY", "quality_label": "CLEAN", "actionable_rank": "B", "candidate_profile": {"promotion_trace": {"blockers": [], "rejection_reasons": [], "late_risk": False, "promoted": True, "promotion_delay_seconds": 4.0}}},
    ]
    s = summarize(rows)
    assert s["traced"] == 2
    assert s["promoted"] == 1
    assert s["late_risk"] == 1
    assert s["top_blockers"][0] == ("quality_clean", 1)
    assert s["average_promotion_delay_seconds"] == 4.0

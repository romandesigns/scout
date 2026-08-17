from app.market import evaluate_early_release
from app.config import settings


def base_metrics():
    return {
        "full_warmup": True,
        "quality_score": 100,
        "base_extension_pct": 0.25,
        "change3": 0.15,
        "change5": 0.18,
        "change15": 0.22,
        "change30": 0.20,
    }


def test_early_release_accepts_clean_first_leg_before_legacy_impulse():
    m = base_metrics()
    decision = evaluate_early_release(
        m,
        first_leg_candidate=True,
        quality_actionable=True,
        participation_ok=True,
        trigger_distance_pct=-0.10,
        candidate_age_seconds=2.0,
    )
    assert decision["ready"] is True
    assert decision["blockers"] == []
    assert decision["fresh_velocity_pct"] >= settings.early_release_min_fresh_velocity_pct


def test_early_release_does_not_relax_quality():
    m = base_metrics()
    decision = evaluate_early_release(
        m,
        first_leg_candidate=True,
        quality_actionable=False,
        participation_ok=True,
        trigger_distance_pct=0.05,
        candidate_age_seconds=1.0,
    )
    assert decision["ready"] is False
    assert "quality_actionable" in decision["blockers"]


def test_early_release_rejects_extended_or_stale_candidate():
    m = base_metrics()
    m["base_extension_pct"] = settings.early_release_max_base_extension_pct + 0.25
    decision = evaluate_early_release(
        m,
        first_leg_candidate=True,
        quality_actionable=True,
        participation_ok=True,
        trigger_distance_pct=0.05,
        candidate_age_seconds=settings.early_release_max_candidate_age_seconds + 1,
    )
    assert decision["ready"] is False
    assert "base_not_extended" in decision["blockers"]
    assert "fresh_candidate" in decision["blockers"]


def test_early_release_rejects_weak_velocity():
    m = base_metrics()
    m.update({"change3": 0.01, "change5": 0.02, "change15": 0.03, "change30": 0.04})
    decision = evaluate_early_release(
        m,
        first_leg_candidate=True,
        quality_actionable=True,
        participation_ok=True,
        trigger_distance_pct=0.05,
        candidate_age_seconds=1.0,
    )
    assert decision["ready"] is False
    assert "fresh_velocity" in decision["blockers"]

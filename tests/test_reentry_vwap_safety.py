"""
2026-08-19: EXPERIMENT_REENTRY_VWAP_SAFETY_GATE regression tests.

Real production case that motivated this (see MILESTONES/2026-08-19-008): BIVI ran from
~$1.10 to $1.96 earlier in the day, then faded for hours. A two-second EMA9/EMA21 flicker
inside that fade produced a `rank=B, quality=CLEAN, EMA_RECLAIM` finding labeled "STRONG
MOMENTUM" / "FRESH ENTRY" on the live dashboard -- because the existing reentry safety
gate's `is_late_promotion_risk` check only measures extension from the *local* base, which
had reset to near-zero after hours of fading, and had no separate check against the
session's VWAP. This locks in the fix: a reclaim/rearm that fires while still meaningfully
below VWAP gets blocked, but only when the experiment flag is enabled (default off,
pending validation) -- must not change default behavior.
"""
import dataclasses
from unittest.mock import patch

from app.config import settings
from app.market import evaluate_reentry_safety


# The BIVI case, reconstructed from the live diagnostics pulled 2026-08-19: price $1.4868,
# vwap $1.5679 -> vwap_gap_pct roughly -5.17%, small positive change5, not locally extended
# (a fresh, tight base had formed near the depressed price after the fade).
BIVI_FADE_RECLAIM_METRICS = {
    "change5": 0.35, "base_extension_pct": -0.21, "extension": 0.11,
    "vwap_gap_pct": -5.17,
}


def test_vwap_safety_gate_off_by_default_reproduces_the_bivi_bug():
    """Documents the bug as it stood in production: with the flag at its default (off),
    a deep-fade reclaim is NOT blocked by the VWAP check -- this is the exact gap that let
    the BIVI finding through. Confirms the fix is additive/opt-in, not already live."""
    d = evaluate_reentry_safety("EMA_RECLAIM", BIVI_FADE_RECLAIM_METRICS)
    assert d["ready"] is True
    assert "deeply_below_vwap" not in d["blockers"]


def test_vwap_safety_gate_blocks_the_bivi_case_when_enabled():
    with patch("app.market.settings", dataclasses.replace(settings, experiment_reentry_vwap_safety_gate=True)):
        d = evaluate_reentry_safety("EMA_RECLAIM", BIVI_FADE_RECLAIM_METRICS)
    assert d["ready"] is False
    assert "deeply_below_vwap" in d["blockers"]
    assert d["vwap_gap_pct"] == -5.17


def test_vwap_safety_gate_still_allows_genuine_near_vwap_reclaim_when_enabled():
    """A reclaim that's only slightly below VWAP (within the tolerance) should not be
    penalized -- this must not become a blanket ban on EMA_RECLAIM, only on reclaims that
    are still deep in a fade relative to the session's own volume-weighted reference."""
    metrics = {**BIVI_FADE_RECLAIM_METRICS, "vwap_gap_pct": -0.8}
    with patch("app.market.settings", dataclasses.replace(settings, experiment_reentry_vwap_safety_gate=True)):
        d = evaluate_reentry_safety("EMA_RECLAIM", metrics)
    assert d["ready"] is True
    assert "deeply_below_vwap" not in d["blockers"]


def test_vwap_safety_gate_does_not_apply_to_non_reentry_stages():
    """The blocker is scoped to REARM/VWAP_RECLAIM/EMA_RECLAIM only -- a stage outside that
    set (e.g. a fresh EARLY qualification) must never see this blocker, even with the flag
    enabled and a deeply-negative vwap_gap_pct."""
    with patch("app.market.settings", dataclasses.replace(settings, experiment_reentry_vwap_safety_gate=True)):
        d = evaluate_reentry_safety("EARLY", BIVI_FADE_RECLAIM_METRICS)
    assert d["blockers"] == []


def test_vwap_safety_gate_respects_configurable_threshold():
    """A -1.5% gap should pass at the default 2.0% tolerance but fail once the tolerance
    is tightened below it."""
    metrics = {**BIVI_FADE_RECLAIM_METRICS, "vwap_gap_pct": -1.5}
    with patch("app.market.settings", dataclasses.replace(settings, experiment_reentry_vwap_safety_gate=True)):
        d = evaluate_reentry_safety("REARM", metrics)
    assert d["ready"] is True

    with patch("app.market.settings", dataclasses.replace(
        settings, experiment_reentry_vwap_safety_gate=True, reentry_max_below_vwap_pct=1.0,
    )):
        d = evaluate_reentry_safety("REARM", metrics)
    assert d["ready"] is False
    assert "deeply_below_vwap" in d["blockers"]


# The CDTG case, reconstructed from the retroactive full-day validation: two REARM/
# VWAP_RECLAIM findings at +20-22% above VWAP, each with a real -7 to -8% forward outcome
# -- together the majority of that session's entire actionable-cohort net loss.
CDTG_CHASE_METRICS = {
    "change5": 0.4, "base_extension_pct": 0.3, "extension": 0.5,
    "vwap_gap_pct": 21.9,
}


def test_vwap_safety_gate_off_by_default_reproduces_the_cdtg_bug():
    d = evaluate_reentry_safety("VWAP_RECLAIM", CDTG_CHASE_METRICS)
    assert d["ready"] is True
    assert "chasing_above_vwap" not in d["blockers"]


def test_vwap_safety_gate_blocks_the_cdtg_case_when_enabled():
    with patch("app.market.settings", dataclasses.replace(settings, experiment_reentry_vwap_safety_gate=True)):
        d = evaluate_reentry_safety("VWAP_RECLAIM", CDTG_CHASE_METRICS)
    assert d["ready"] is False
    assert "chasing_above_vwap" in d["blockers"]


def test_vwap_safety_gate_allows_modest_above_vwap_reclaim_when_enabled():
    """A reclaim modestly above VWAP (genuine strength, not a chase) must not be
    penalized -- e.g. the real OSRH case that day: +2.38% above VWAP, +7.89% outcome."""
    metrics = {**CDTG_CHASE_METRICS, "vwap_gap_pct": 2.38}
    with patch("app.market.settings", dataclasses.replace(settings, experiment_reentry_vwap_safety_gate=True)):
        d = evaluate_reentry_safety("VWAP_RECLAIM", metrics)
    assert d["ready"] is True
    assert "chasing_above_vwap" not in d["blockers"]

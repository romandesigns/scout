from __future__ import annotations

from .models import Finding


SPECIAL_EVENTS = {"CATALYST", "CATALYST_WATCH", "CATALYST_ACTIVE", "HALT", "HALT_WATCH", "HALT_PRESSURE", "RESUME"}
SECONDARY_STAGES = {"REARM", "EMA_RECLAIM", "VWAP_RECLAIM", "RECLAIM"}


def opportunity_class(f: Finding) -> str:
    """Classify the decision value of a finding, independently of its stage."""
    if f.stage in SPECIAL_EVENTS:
        return "EVENT"
    timely = str(f.timeliness_label or "").upper()
    extension = max(float(f.extension_pct or 0), float(f.base_extension_at_detection_pct or 0))
    if timely in {"LATE", "TOO_LATE", "EXTENDED", "LATE_RISK"} or extension >= 8:
        return "LATE_INFORMATION_ONLY"
    context = str(f.leg_context or "").upper()
    if f.stage in SECONDARY_STAGES or any(token in context for token in ("RECLAIM", "REENTRY", "PULLBACK", "CONSOLIDATION")):
        return "SECONDARY_ENTRY"
    return "FIRST_MOVE"


def is_continuation_watch(f: Finding) -> bool:
    if f.stage != "REVERSAL_WATCH":
        return False
    profile = f.candidate_profile or {}
    multi_timeframe = profile.get("multi_timeframe") or {}
    promotion = profile.get("promotion_trace") or {}
    gates = promotion.get("gates") or {}
    box = profile.get("box") or {}
    rejection_reasons = {str(reason).upper() for reason in f.rejection_reasons or []}
    return bool(
        multi_timeframe.get("qualified") is True
        and float(profile.get("velocity") or 0) >= 80
        and float(profile.get("participation") or 0) >= 80
        and float(profile.get("structure") or 0) >= 80
        and bool(box.get("breakout"))
        and gates.get("fresh_impulse") is True
        and gates.get("bullish_confirmed") is True
        and gates.get("not_bearish_short") is True
        and not rejection_reasons.intersection({"LOW PARTICIPATION", "SPARSE PRINTS", "STALE TRADES"})
    )


def can_notify_opportunity(f: Finding) -> bool:
    classification = opportunity_class(f)
    if classification == "EVENT":
        return True
    if is_continuation_watch(f):
        return True
    return is_group_a(f)


def is_group_a(f: Finding, *, confirmed_only: bool = False) -> bool:
    """The single contract for user-facing and executable opportunities."""
    if f.shadow_mode or str(f.actionable_rank or "").upper() != "A":
        return False
    if str(f.quality_label or "").upper() != "CLEAN":
        return False
    multi_timeframe = (f.candidate_profile or {}).get("multi_timeframe") or {}
    if multi_timeframe and multi_timeframe.get("qualified") is not True:
        return False
    if opportunity_class(f) not in {"FIRST_MOVE", "SECONDARY_ENTRY"}:
        return False
    if confirmed_only and f.stage not in {"IGNITION", "BREAKOUT", "SURGE"}:
        return False
    return True

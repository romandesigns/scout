from __future__ import annotations

from .models import Finding


SPECIAL_EVENTS = {"CATALYST", "CATALYST_WATCH", "CATALYST_ACTIVE", "HALT", "RESUME"}
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


def can_notify_opportunity(f: Finding) -> bool:
    return opportunity_class(f) in {"FIRST_MOVE", "SECONDARY_ENTRY", "EVENT"}

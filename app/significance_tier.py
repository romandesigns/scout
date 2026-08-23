"""Deterministic bullish-momentum significance tiering and notify-preview.

Operationalizes the JUNS/WEN chart-review framework agreed with the user (see
IMPLEMENTATION_DECISIONS.md, 2026-08-22 entries "JUNS audit notification
interpretation" and "WEN audit and sub-minute ignition-phase notification
timing"): not every upward impulse Scout detects is notification-worthy.
Several are the same momentum sequence continuing, or a reaction bounce
inside broader rotation, and should be classified below a genuine structural
breakout rather than alerted on identically.

Two things live here, both advisory only -- neither changes what Scout
actually detects or sends. They exist so a human can review, on the
Scout Development chart, which detections Scout treats as significant and
which ones its real notification gate would have fired on, before any of
this is wired into live gating:

- `classify_tier`: Tier 1 (structural breakout) / Tier 2 (continuation pulse)
  / Tier 3 (reaction bounce -- should usually be suppressed).
- `would_notify`: a preview of Scout's real notification gate
  (`opportunity.can_notify_opportunity`, the same contract `notifiers.py`
  uses to actually decide whether to send) evaluated against a stored or
  in-flight detection. It does not model channel-level rate limiting or
  delivery mechanics -- only opportunity-worthiness.

Both functions accept either a live `Finding` (attribute access) or a stored
finding dict (as returned by `Store.list_findings`/`get_finding`), so the
same logic can tag a detection at dispatch time and re-evaluate historical
detections for chart review.
"""
from __future__ import annotations

from typing import Any

from .config import settings
from .notifiers import SPECIAL_STAGES, USER_NOTIFY_STAGES
from .opportunity import can_notify_opportunity, opportunity_class

CONFIRMED_IMPULSE_STAGES = {"BREAKOUT", "IGNITION", "SURGE"}


def _get(source: Any, name: str, default: Any = None) -> Any:
    """Read a field from either a Finding instance or a stored finding dict."""
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


class _FindingView:
    """Duck-typed adapter so opportunity.py's Finding-shaped functions accept
    either a live Finding or a stored finding dict unmodified."""

    __slots__ = ("_source",)

    def __init__(self, source: Any):
        self._source = source

    def __getattr__(self, name: str) -> Any:
        return _get(self._source, name)


def would_notify(source: Any) -> dict[str, Any]:
    """Preview whether Scout's real notification gate would fire for this
    detection, mirroring `notifiers._allowed_platform_agnostic` minus the
    human's own preference toggles (quiet hours, minimum score, session, etc.)
    -- those describe what the user wants to hear about, not whether the
    moment itself was notify-worthy, which is what this preview is for.
    Fail-open to False on bad or missing input; never raises, since this only
    feeds chart/UI review, not real delivery."""
    view = _FindingView(source)
    try:
        if bool(_get(source, "shadow_mode")):
            return {"would_notify": False, "opportunity_class": None, "reason": "shadow_mode"}
        if not can_notify_opportunity(view):
            return {"would_notify": False, "opportunity_class": opportunity_class(view), "reason": "opportunity_gate"}
        stage = str(_get(source, "stage") or "").upper()
        if stage not in USER_NOTIFY_STAGES:
            return {"would_notify": False, "opportunity_class": opportunity_class(view), "reason": "stage_not_user_facing"}
        candidate_profile = _get(source, "candidate_profile") or {}
        if stage not in SPECIAL_STAGES and not bool((candidate_profile.get("edge_validation") or {}).get("validated")):
            return {"would_notify": False, "opportunity_class": opportunity_class(view), "reason": "edge_not_validated"}
        quality = str(_get(source, "quality_label") or "").upper()
        if stage not in {"CATALYST", "CATALYST_WATCH", "CATALYST_ACTIVE", "HALT", "RESUME"} and quality != "CLEAN":
            return {"would_notify": False, "opportunity_class": opportunity_class(view), "reason": "quality_not_clean"}
        classification = opportunity_class(view)
    except Exception as exc:
        return {"would_notify": False, "opportunity_class": None, "error": f"{type(exc).__name__}: {exc}"}
    return {"would_notify": True, "opportunity_class": classification}


def classify_tier(source: Any) -> dict[str, Any]:
    """Tier 1 (structural breakout) / Tier 2 (continuation pulse) / Tier 3
    (reaction bounce), per the JUNS chart review."""
    view = _FindingView(source)
    rank = str(_get(source, "actionable_rank") or "C").upper()
    quality = str(_get(source, "quality_label") or "").upper()
    stage = str(_get(source, "stage") or "").upper()
    try:
        opp_class = opportunity_class(view)
    except Exception:
        opp_class = None

    directional_efficiency = _get(source, "directional_efficiency")
    direction_reversals = _get(source, "direction_reversals")
    active_bucket_ratio = _get(source, "active_bucket_ratio")

    # Reaction-bounce signature: participation/structure sits in the marginal
    # zone -- past Scout's hard quality-reject bar (so it wasn't rejected
    # outright) but well short of clean/confirmed. This is what the JUNS
    # review meant by "reaction bounces inside broader rotation" that should
    # be classified below major breakouts rather than alerted on the same way.
    bounce_signals = 0
    if directional_efficiency is not None and float(directional_efficiency) < settings.quality_min_directional_efficiency * 1.5:
        bounce_signals += 1
    if direction_reversals is not None and int(direction_reversals) >= max(2, settings.quality_max_direction_reversals - 1):
        bounce_signals += 1
    if active_bucket_ratio is not None and float(active_bucket_ratio) < settings.quality_min_active_ratio * 1.15:
        bounce_signals += 1
    reaction_bounce = bounce_signals >= 2 or (opp_class == "SECONDARY_ENTRY" and bounce_signals >= 1 and rank != "A")

    if opp_class == "LATE_INFORMATION_ONLY":
        return {
            "tier": 3, "label": "Late/extended",
            "reasons": ["already extended past the actionable window"],
            "reaction_bounce": True, "opportunity_class": opp_class,
        }

    if rank != "A" or quality != "CLEAN" or reaction_bounce:
        reasons = []
        if rank != "A":
            reasons.append(f"actionable_rank {rank or 'C'} below A")
        if quality != "CLEAN":
            reasons.append(f"quality_label {quality or 'unset'} below CLEAN")
        if reaction_bounce:
            reasons.append("reaction-bounce signature (choppy path / weak participation / excess reversals)")
        return {
            "tier": 3, "label": "Reaction bounce", "reasons": reasons,
            "reaction_bounce": reaction_bounce, "opportunity_class": opp_class,
        }

    # From here: actionable_rank A, quality CLEAN, no bounce signature.
    # Distinguish a Tier 1 structural breakout from an ordinary Tier 2
    # continuation by requiring a confirmed impulse stage plus real magnitude
    # -- not just that the quality gates passed. Calibrated against real
    # stored detections (IMPLEMENTATION_DECISIONS.md 2026-08-22): score and
    # 30-60s price-change are already saturated near the rank-A floor for
    # most real confirmed findings (score rarely exceeds ignition_score by
    # more than 1; 30-60s windows are too short to show a large % move even
    # for a genuine breakout), so an earlier "N of 4 signals each at 1.5x
    # their trigger" version never fired on real data. Relative volume is the
    # one signal with real dynamic range here, so it carries Tier 1 alone,
    # reusing the same "extreme volume anomaly" bar market.py already scores
    # evidence with; the other two paths exist for tickers/datasets where
    # score or sustained multi-window price expansion is the standout signal
    # instead.
    vol15 = float(_get(source, "vol_ratio_15s") or 0)
    vol30 = float(_get(source, "vol_ratio_30s") or 0)
    score = float(_get(source, "score") or 0)
    change30 = float(_get(source, "change_30s_pct") or 0)
    change60 = float(_get(source, "change_60s_pct") or 0)
    extreme_volume = max(vol15, vol30) >= settings.vol_ratio_trigger * 2
    exceptional_score = score >= settings.ignition_score + 3
    sustained_expansion = change60 >= settings.price_60s_trigger_pct and change30 >= settings.price_60s_trigger_pct * 0.5
    magnitude_reason = (
        f"extreme relative volume ({max(vol15, vol30):.1f}x baseline)" if extreme_volume else
        f"exceptional composite score ({score:.0f})" if exceptional_score else
        "sustained 30s+60s price expansion" if sustained_expansion else None
    )
    if opp_class == "FIRST_MOVE" and stage in CONFIRMED_IMPULSE_STAGES and magnitude_reason:
        return {
            "tier": 1, "label": "Structural breakout",
            "reasons": [f"{magnitude_reason} at a confirmed impulse stage ({stage})"],
            "reaction_bounce": False, "opportunity_class": opp_class,
        }

    return {
        "tier": 2, "label": "Continuation pulse",
        "reasons": ["confirmed and clean, but below Tier 1 magnitude, or a continuation/reclaim off a local high"],
        "reaction_bounce": False, "opportunity_class": opp_class,
    }

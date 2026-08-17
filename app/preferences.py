from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_NOTIFICATION_PREFERENCES: dict[str, Any] = {
    "master_enabled": True,
    "platforms": {
        "android": {
            "enabled": True,
            "sound": True,
            "vibration": True,
            "priority": "high",
        },
        "windows": {
            "enabled": False,
            "sound": True,
            "toast": True,
            "priority": "high",
        },
        "email": {
            "enabled": False,
        },
    },
    "signals": {
        "ACTIVITY_WATCH": "silent",
        "REVERSAL_WATCH": "silent",
        "FIRST_LEG_WATCH": "silent",
        "PRE_IGNITION": "silent",
        "AWAKENING": "notify",
        "FIRST_LEG": "notify",
        "RECLAIM": "notify",
        "EMA_RECLAIM": "notify",
        "VWAP_RECLAIM": "notify",
        "FIRST_PULLBACK": "silent",
        "EARLY": "notify",
        "SURGE": "notify",
        "BREAKOUT": "notify",
        "STAIRCASE": "notify",
        "IGNITION": "notify",
        "CATALYST": "notify",
        "CATALYST_WATCH": "notify",
        "CATALYST_ACTIVE": "notify",
        "HALT": "notify",
        "HALT_WATCH": "notify",
        "HALT_PRESSURE": "notify",
        "RESUME": "notify",
        "REARM": "notify",
        "EXTENDED": "silent",
        "FAILED": "silent",
    },
    "sessions": {
        "overnight": True,
        "premarket": True,
        "regular": True,
        "afterhours": True,
    },
    "quiet_hours": {
        "enabled": False,
        "start": "22:00",
        "end": "06:00",
        "allow_critical": True,
    },
    "minimum_score": 0,
    "only_stage_escalations": True,
    "group_by_ticker": True,
    "market_quality_profile": "balanced",
}


def _merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def normalize_notification_preferences(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return deepcopy(DEFAULT_NOTIFICATION_PREFERENCES)
    merged = _merge(DEFAULT_NOTIFICATION_PREFERENCES, value)

    merged["master_enabled"] = bool(merged.get("master_enabled", True))
    merged["minimum_score"] = max(0, min(10, int(merged.get("minimum_score", 0))))
    merged["only_stage_escalations"] = bool(merged.get("only_stage_escalations", True))
    merged["group_by_ticker"] = bool(merged.get("group_by_ticker", True))
    quality_profile = str(merged.get("market_quality_profile", "balanced")).lower()
    merged["market_quality_profile"] = quality_profile if quality_profile in {"strict", "balanced", "permissive"} else "balanced"

    allowed_signal_modes = {"notify", "silent", "off"}
    for signal, mode in list(merged["signals"].items()):
        if str(mode).lower() not in allowed_signal_modes:
            merged["signals"][signal] = "notify"
        else:
            merged["signals"][signal] = str(mode).lower()

    for session in ("overnight", "premarket", "regular", "afterhours"):
        merged["sessions"][session] = bool(merged["sessions"].get(session, True))

    allowed_priorities = {"low", "normal", "high", "critical"}
    for platform in ("android", "windows", "email"):
        merged["platforms"][platform]["enabled"] = bool(
            merged["platforms"][platform].get("enabled", True)
        )

    for platform in ("android", "windows"):
        platform_prefs = merged["platforms"][platform]
        priority = str(platform_prefs.get("priority", "high")).lower()
        platform_prefs["priority"] = priority if priority in allowed_priorities else "high"
        platform_prefs["sound"] = bool(platform_prefs.get("sound", True))

    merged["platforms"]["android"]["vibration"] = bool(
        merged["platforms"]["android"].get("vibration", True)
    )
    merged["platforms"]["windows"]["toast"] = bool(
        merged["platforms"]["windows"].get("toast", True)
    )

    qh = merged["quiet_hours"]
    qh["enabled"] = bool(qh.get("enabled", False))
    qh["allow_critical"] = bool(qh.get("allow_critical", True))
    qh["start"] = str(qh.get("start", "22:00"))[:5]
    qh["end"] = str(qh.get("end", "06:00"))[:5]
    return merged

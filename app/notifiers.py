from __future__ import annotations

import base64
import html
import json
import logging
import random
import threading
import time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

import requests

from .config import settings
from .models import Finding

log = logging.getLogger("scout.notify")


_health_lock = threading.Lock()
_delivery_health: dict[str, dict[str, Any]] = {
    "ntfy": {"last_attempt_at": None, "last_success_at": None, "last_error": None, "rate_limited_until": None},
    "resend": {"last_attempt_at": None, "last_success_at": None, "last_error": None, "rate_limited_until": None},
    "webpush": {"last_attempt_at": None, "last_success_at": None, "last_error": None, "rate_limited_until": None},
}
_channel_locks = {"ntfy": threading.Lock(), "resend": threading.Lock()}
_channel_next_at = {"ntfy": 0.0, "resend": 0.0}


def delivery_health() -> dict[str, dict[str, Any]]:
    with _health_lock:
        return {channel: dict(values) for channel, values in _delivery_health.items()}


def _health(channel: str, **values: Any) -> None:
    with _health_lock:
        _delivery_health[channel].update(values)


def _retry_after(response: requests.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _post_with_backoff(
    channel: str,
    url: str,
    *,
    min_interval: float,
    timeout: float,
    json: dict[str, Any] | None = None,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    """Serialize a provider channel and respect its rate-limit response.

    Only 429 and transient 5xx responses are retried. Other 4xx responses are
    configuration errors and fail immediately. The lock intentionally spans
    retry waits so parallel findings cannot stampede the provider.
    """
    attempts = max(1, settings.notification_retry_attempts)
    with _channel_locks[channel]:
        for attempt in range(attempts):
            wait = _channel_next_at[channel] - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            now = int(time.time())
            _health(channel, last_attempt_at=now)
            try:
                response = requests.post(url, json=json, data=data, headers=headers, timeout=timeout)
            except requests.RequestException as exc:
                if attempt + 1 >= attempts:
                    _health(channel, last_error=str(exc))
                    raise
                delay = min(
                    settings.notification_retry_max_seconds,
                    settings.notification_retry_base_seconds * (2 ** attempt) + random.uniform(0, 0.5),
                )
                _channel_next_at[channel] = time.monotonic() + delay
                _health(channel, last_error=str(exc))
                log.warning("%s transport retry %s/%s in %.1fs", channel, attempt + 1, attempts, delay)
                continue

            _channel_next_at[channel] = time.monotonic() + min_interval
            if response.status_code < 400:
                _health(channel, last_success_at=int(time.time()), last_error=None, rate_limited_until=None)
                return response

            retryable = response.status_code == 429 or response.status_code >= 500
            detail = response.text[:300].replace("\n", " ")
            error = f"HTTP {response.status_code}: {detail}".strip()
            if not retryable or attempt + 1 >= attempts:
                _health(channel, last_error=error)
                response.raise_for_status()

            provider_wait = _retry_after(response)
            exponential = min(
                settings.notification_retry_max_seconds,
                settings.notification_retry_base_seconds * (2 ** attempt),
            )
            delay = provider_wait if provider_wait is not None else exponential + random.uniform(0, 0.5)
            delay = min(settings.notification_retry_max_seconds, max(min_interval, delay))
            _channel_next_at[channel] = time.monotonic() + delay
            if response.status_code == 429:
                _health(channel, last_error=error, rate_limited_until=int(time.time() + delay))
            log.warning("%s delivery retry %s/%s in %.1fs (%s)", channel, attempt + 1, attempts, delay, error)
    raise RuntimeError(f"{channel} delivery exhausted retries")


CRITICAL_STAGES = {"FIRST_LEG", "SURGE", "IGNITION", "HALT_WATCH", "HALT_PRESSURE", "CATALYST_ACTIVE", "HALT"}


def _is_critical(f: Finding) -> bool:
    return any(signal in CRITICAL_STAGES for signal in dict.fromkeys([f.stage, *(f.signals or [])]))


def _session_name(ts: float) -> str:
    local = datetime.fromtimestamp(ts, ZoneInfo(settings.timezone))
    minutes = local.hour * 60 + local.minute
    if minutes >= 20 * 60 or minutes < 4 * 60:
        return "overnight"
    if minutes < 9 * 60 + 30:
        return "premarket"
    if minutes < 16 * 60:
        return "regular"
    return "afterhours"

def _signal_mode(f: Finding, prefs: dict[str, Any] | None) -> str:
    """Resolve delivery mode across the fused Scout signal set.

    A finding can carry several simultaneous signals (for example SURGE +
    BREAKOUT + CATALYST).  Per-signal notification controls therefore apply to
    all of them rather than only the primary stage.  Any explicitly enabled
    signal may surface the finding; otherwise silent wins over off.
    """
    if not prefs:
        return "notify"
    configured = prefs.get("signals", {})
    signals = list(dict.fromkeys([f.stage, *(f.signals or [])]))
    modes = [str(configured.get(signal, "notify")).lower() for signal in signals]
    if "notify" in modes:
        return "notify"
    if "silent" in modes:
        return "silent"
    return "off"

def _quiet_now(f: Finding, prefs: dict[str, Any] | None) -> bool:
    if not prefs:
        return False
    quiet = prefs.get("quiet_hours", {})
    if not quiet.get("enabled"):
        return False
    try:
        local = datetime.fromtimestamp(f.detected_at, ZoneInfo(settings.timezone))
        now_min = local.hour * 60 + local.minute
        sh, sm = (int(x) for x in str(quiet.get("start", "22:00")).split(":", 1))
        eh, em = (int(x) for x in str(quiet.get("end", "06:00")).split(":", 1))
        start = sh * 60 + sm
        end = eh * 60 + em
        inside = start <= now_min < end if start < end else (now_min >= start or now_min < end)
        if inside and quiet.get("allow_critical", True) and _is_critical(f):
            return False
        return inside
    except Exception:
        return False

def _allowed(f: Finding, prefs: dict[str, Any] | None, platform: str) -> bool:
    if f.stage not in {"CATALYST", "CATALYST_WATCH", "CATALYST_ACTIVE", "HALT", "RESUME"} and f.quality_label != "CLEAN":
        return False
    if not prefs:
        return True
    if not prefs.get("master_enabled", True):
        return False
    if int(f.score) < int(prefs.get("minimum_score", 0)):
        return False
    if not bool(prefs.get("sessions", {}).get(_session_name(f.detected_at), True)):
        return False
    if _signal_mode(f, prefs) in {"silent", "off"}:
        return False
    if _quiet_now(f, prefs):
        return False
    return bool(prefs.get("platforms", {}).get(platform, {}).get("enabled", True))


def notification_allowed(f: Finding, prefs: dict[str, Any] | None, platform: str) -> bool:
    """Public pre-queue eligibility check used by the dispatcher."""
    return _allowed(f, prefs, platform)


def _message(f: Finding) -> str:
    catalyst = f.catalyst_category or "searching / not yet confirmed"
    signals = " · ".join(f.signals or [f.stage])
    velocity = []
    if f.change_3s_pct is not None:
        velocity.append(f"3s {f.change_3s_pct:+.2f}%")
    if f.change_5s_pct is not None:
        velocity.append(f"5s {f.change_5s_pct:+.2f}%")
    if f.change_15s_pct is not None:
        velocity.append(f"15s {f.change_15s_pct:+.2f}%")
    if f.change_30s_pct is not None:
        velocity.append(f"30s {f.change_30s_pct:+.2f}%")
    lines = [
        f"${f.price:.4f} | rank {f.actionable_rank} | quality {f.quality_label} {f.quality_score}/100 | {signals}",
        " | ".join(velocity) if velocity else f"60s move {f.change_60s_pct:+.1f}%",
        f"15s RVOL {f.vol_ratio_15s:.1f}× | 30s RVOL {f.vol_ratio_30s:.1f}×",
        (f"30s ${f.dollar_volume_30s:,.0f} | {f.trades_30s} trades" if f.dollar_volume_30s is not None and f.trades_30s is not None else None),
        (f"Breakout: ${f.breakout_level:.4f} ({f.breakout_window})" if f.breakout_level is not None else None),
        f"EMA9 {'>' if f.ema9 and f.ema21 and f.ema9 > f.ema21 else '≤'} EMA21 | VWAP {'above' if f.above_vwap else 'below/unknown'}",
        f"Catalyst: {catalyst}",
        " • ".join(f.evidence[:6]),
        (f"Suppressed by: {', '.join(f.rejection_reasons)}" if f.rejection_reasons else None),
    ]
    return "\n".join(x for x in lines if x)


def send_ntfy(f: Finding, prefs: dict[str, Any] | None = None) -> None:
    if not _allowed(f, prefs, "android"):
        return
    if not settings.ntfy_topic:
        log.warning("NTFY_TOPIC not configured; push suppressed")
        return

    android_prefs = (prefs or {}).get("platforms", {}).get("android", {})
    priority_name = str(android_prefs.get("priority", "high")).lower()
    priority_map = {"low": 2, "normal": 3, "high": 4, "critical": 5}
    priority = priority_map.get(priority_name, 4)
    if "CATALYST" in (f.signals or []) or f.stage == "CATALYST":
        priority = max(priority, 4)
    if _is_critical(f):
        priority = 5
    icons = {
        "FIRST_LEG": "⚡",
        "FIRST_LEG_WATCH": "◌",
        "EARLY": "⚡",
        "SURGE": "🚀",
        "BREAKOUT": "↗",
        "STAIRCASE": "↗",
        "IGNITION": "🚨",
        "CATALYST": "📰",
        "CATALYST_WATCH": "📰",
        "CATALYST_ACTIVE": "🚨",
        "HALT": "⏸",
        "HALT_WATCH": "⚠",
        "HALT_PRESSURE": "⚠",
        "RESUME": "▶",
        "REARM": "↻",
        "RECLAIM": "↗",
        "EMA_RECLAIM": "↗",
        "VWAP_RECLAIM": "↗",
        "REVERSAL_WATCH": "◌",
    }
    title_icon = icons.get(f.stage, "●")

    payload = {
        "topic": settings.ntfy_topic,
        "title": f"{title_icon} {f.ticker} | {f.stage}{f' · {f.leg_context}' if f.leg_context else ''}",
        "message": _message(f),
        "priority": priority,
        "tags": (
            ["rotating_light", "chart_with_upwards_trend"]
            if (f.stage == "IGNITION" or "IGNITION" in (f.signals or []))
            else ["zap", "eyes"]
        ),
    }

    if settings.scout_client_base_url and f.finding_id:
        payload["click"] = f"{settings.scout_client_base_url}/?finding={f.finding_id}&ticker={f.ticker}"
    elif f.catalyst_url:
        payload["click"] = f.catalyst_url

    try:
        r = _post_with_backoff(
            "ntfy",
            settings.ntfy_server.rstrip("/") + "/",
            json=payload,
            timeout=8,
            min_interval=settings.ntfy_min_interval_seconds,
        )
        r.raise_for_status()
    except Exception:
        log.exception("ntfy publish failed")
        raise


def send_ntfy_chart(f: Finding, prefs: dict[str, Any] | None = None) -> None:
    if not _allowed(f, prefs, "android"):
        return
    if not (settings.ntfy_chart_followup and settings.ntfy_topic and f.chart_path):
        return
    p = Path(f.chart_path)
    if not p.exists():
        return
    try:
        headers = {
            "Title": f"{f.ticker} | detection chart",
            "Filename": p.name,
            "Priority": "default",
        }
        r = _post_with_backoff("ntfy", f"{settings.ntfy_server}/{settings.ntfy_topic}", data=p.read_bytes(), headers=headers, timeout=15, min_interval=settings.ntfy_min_interval_seconds)
        r.raise_for_status()
    except Exception:
        log.exception("ntfy chart follow-up failed")


def _web_push_payload(f: Finding) -> dict[str, Any]:
    critical = _is_critical(f) or f.stage == "CATALYST_ACTIVE"
    return {
        "title": f"{f.ticker} · {f.stage.replace('_', ' ')}",
        "body": _message(f),
        "ticker": f.ticker,
        "findingId": f.finding_id,
        "stage": f.stage,
        "urgency": f.urgency,
        "url": f"/?finding={f.finding_id}&ticker={f.ticker}",
        "tag": f"scout-{f.ticker}" if settings.scout_client_base_url else f"scout-{f.ticker}-{f.stage}",
        "renotify": critical,
        "requireInteraction": critical,
        "vibrate": [180, 80, 180] if critical else [120],
    }


def send_web_push_all(store: Any, f: Finding, prefs: dict[str, Any] | None = None) -> int:
    if not _allowed(f, prefs, "android"):
        return 0
    if not (settings.vapid_public_key and settings.vapid_private_key):
        raise RuntimeError("VAPID Web Push is not configured")
    try:
        from pywebpush import webpush
    except ImportError as exc:
        raise RuntimeError("pywebpush is not installed") from exc

    subscriptions = store.list_web_push_subscriptions()
    if not subscriptions:
        raise RuntimeError("No active Web Push subscriptions")
    delivered = 0
    payload = json.dumps(_web_push_payload(f), separators=(",", ":"))
    for subscription in subscriptions:
        endpoint = subscription["endpoint"]
        _health("webpush", last_attempt_at=int(time.time()))
        try:
            webpush(
                subscription_info={"endpoint": endpoint, "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]}},
                data=payload,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_subject},
                ttl=120,
            )
            delivered += 1
            _health("webpush", last_success_at=int(time.time()), last_error=None)
        except Exception as exc:
            response = getattr(exc, "response", None)
            if getattr(response, "status_code", None) in {404, 410}:
                store.delete_web_push_subscription(endpoint)
                log.info("Removed expired Web Push subscription")
                continue
            _health("webpush", last_error=str(exc))
            log.exception("Web Push delivery failed")
    if delivered == 0:
        raise RuntimeError("No active Web Push subscription accepted the notification")
    return delivered


def send_web_push_test(store: Any) -> tuple[bool, str]:
    subscriptions = store.list_web_push_subscriptions()
    if not subscriptions:
        return False, "No phone has enabled installed-app notifications"
    finding = Finding(
        ticker="SCOUT", stage="CATALYST_WATCH", detected_at=time.time(), price=0, score=10,
        vol_ratio_15s=0, vol_ratio_30s=0, change_60s_pct=0, extension_pct=0,
        ema9=None, ema21=None, ema9_slope=None, vwap=None, above_vwap=False,
        quiet_break=False, evidence=["Web Push background delivery is connected"],
        catalyst_category="Notification test", catalyst_headline="Scout is listening 24/7",
        signals=["CATALYST_WATCH"], quality_label="DEVELOPING", urgency="NOW",
    )
    try:
        count = send_web_push_all(store, finding, None)
        return True, f"Web Push test accepted by {count} device{'s' if count != 1 else ''}"
    except Exception as exc:
        return False, str(exc)


def send_resend_email(f: Finding, prefs: dict[str, Any] | None = None) -> None:
    if not _allowed(f, prefs, "email"):
        return
    if not settings.email_every_finding:
        return
    if not (settings.resend_api_key and settings.resend_from and settings.resend_to):
        log.warning("Resend is not fully configured; email suppressed")
        return

    ev = "<br>".join(html.escape(x) for x in f.evidence[:10])
    cat = html.escape(f.catalyst_category or "Not yet confirmed")
    headline = html.escape(f.catalyst_headline or "")
    signals = html.escape(" · ".join(f.signals or [f.stage]))
    velocity = " / ".join(
        x for x in [
            f"3s {f.change_3s_pct:+.2f}%" if f.change_3s_pct is not None else "",
            f"5s {f.change_5s_pct:+.2f}%" if f.change_5s_pct is not None else "",
            f"15s {f.change_15s_pct:+.2f}%" if f.change_15s_pct is not None else "",
            f"30s {f.change_30s_pct:+.2f}%" if f.change_30s_pct is not None else "",
        ] if x
    ) or f"60s {f.change_60s_pct:+.2f}%"
    breakout = (
        f"${f.breakout_level:.4f} ({html.escape(f.breakout_window or 'range')})"
        if f.breakout_level is not None else "n/a"
    )
    body = f"""
    <div style='font-family:Arial,sans-serif;max-width:760px'>
      <h2>{html.escape(f.ticker)} — {html.escape(f.stage)}</h2>
      <p><strong>Signals:</strong> {signals}</p>
      <p><strong>Price:</strong> ${f.price:.4f} &nbsp; <strong>Score:</strong> {f.score}/10 &nbsp; <strong>Extension:</strong> {f.extension_pct:+.2f}%</p>
      <p><strong>Velocity:</strong> {html.escape(velocity)}<br>
         <strong>15s RVOL:</strong> {f.vol_ratio_15s:.2f}× &nbsp; <strong>30s RVOL:</strong> {f.vol_ratio_30s:.2f}×<br>
         <strong>30s participation:</strong> {('$' + format(f.dollar_volume_30s, ',.0f')) if f.dollar_volume_30s is not None else 'n/a'} &nbsp;
         <strong>Trades:</strong> {f.trades_30s if f.trades_30s is not None else 'n/a'}</p>
      <p><strong>EMA9:</strong> {f.ema9 if f.ema9 is not None else 'n/a'} &nbsp;
         <strong>EMA21:</strong> {f.ema21 if f.ema21 is not None else 'n/a'} &nbsp;
         <strong>VWAP:</strong> {f.vwap if f.vwap is not None else 'n/a'} &nbsp;
         <strong>Breakout:</strong> {breakout}</p>
      <p><strong>Catalyst:</strong> {cat}<br>{headline}</p>
      <p><strong>Evidence:</strong><br>{ev}</p>
      <p><em>The attached chart is frozen at the detection moment; it contains no future candles.</em></p>
    </div>
    """
    payload: dict = {
        "from": settings.resend_from,
        "to": list(settings.resend_to),
        "subject": f"{f.stage} {f.ticker} — ${f.price:.4f} — 15s RVOL {f.vol_ratio_15s:.1f}x",
        "html": body,
    }
    if f.chart_path and Path(f.chart_path).exists():
        payload["attachments"] = [{
            "filename": Path(f.chart_path).name,
            "content": base64.b64encode(Path(f.chart_path).read_bytes()).decode("ascii"),
        }]
    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
        "Idempotency-Key": f"scout-{f.ticker}-{f.stage}-{int(f.detected_at)}",
    }
    try:
        r = _post_with_backoff("resend", "https://api.resend.com/emails", json=payload, headers=headers, timeout=15, min_interval=settings.resend_min_interval_seconds)
        r.raise_for_status()
    except Exception:
        log.exception("Resend send failed")
        raise

def send_ntfy_test() -> tuple[bool, str]:
    if not settings.ntfy_topic:
        return False, "NTFY_TOPIC is not configured"
    try:
        r = _post_with_backoff(
            "ntfy",
            settings.ntfy_server.rstrip("/") + "/",
            json={
                "topic": settings.ntfy_topic,
                "title": "Scout notification test",
                "message": "Android push delivery is connected to StockHunter Scout.",
                "priority": 4,
                "tags": ["white_check_mark", "satellite"],
            },
            timeout=8,
            min_interval=settings.ntfy_min_interval_seconds,
        )
        r.raise_for_status()
        return True, "Android/ntfy test sent"
    except Exception as exc:
        log.exception("ntfy test failed")
        return False, str(exc)


def send_resend_test() -> tuple[bool, str]:
    if not (settings.resend_api_key and settings.resend_from and settings.resend_to):
        return False, "Resend is not fully configured"
    payload = {
        "from": settings.resend_from,
        "to": list(settings.resend_to),
        "subject": "Scout notification test",
        "html": "<p><strong>StockHunter Scout</strong> email notifications are connected.</p>",
    }
    try:
        r = _post_with_backoff(
            "resend",
            "https://api.resend.com/emails",
            json=payload,
            headers={"Authorization": f"Bearer {settings.resend_api_key}", "Content-Type": "application/json"},
            timeout=15,
            min_interval=settings.resend_min_interval_seconds,
        )
        r.raise_for_status()
        return True, "Email test sent"
    except Exception as exc:
        log.exception("Resend test failed")
        return False, str(exc)

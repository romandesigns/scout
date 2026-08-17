from __future__ import annotations

import asyncio
from pathlib import Path

from aiohttp import web

from .config import settings
from .db import Store
from .events import EventHub
from .market import MarketWatcher
from .notifiers import delivery_health, send_ntfy_test, send_resend_test, send_web_push_test
from .replay import replay_status


def _int(value: str | None, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value or default)
    except Exception:
        parsed = default
    return max(low, min(high, parsed))


def _cors_headers(request: web.Request) -> dict[str, str]:
    origin = request.headers.get("Origin", "")
    allowed = settings.allowed_origins
    if not origin:
        return {}
    if "*" in allowed or origin in allowed:
        return {
            "Access-Control-Allow-Origin": "*" if "*" in allowed else origin,
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Allow-Methods": "GET, PUT, POST, PATCH, DELETE, OPTIONS",
        }
    return {}


@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=_cors_headers(request))
    response = await handler(request)
    response.headers.update(_cors_headers(request))
    return response


class ScoutApi:
    def __init__(self, store: Store, market: MarketWatcher, events: EventHub, catalysts=None, dispatcher=None):
        self.store = store
        self.market = market
        self.events = events
        self.catalyst_watcher = catalysts
        self.dispatcher = dispatcher

    async def health(self, request: web.Request) -> web.Response:
        hybrid_status = self.market.rust_bridge.status() if self.market.rust_bridge else {"enabled": False, "running": False}
        return web.json_response({
            "ok": True,
            "hybrid_ready": (not hybrid_status.get("enabled", False)) or bool(hybrid_status.get("running", False)),
            "app": settings.app_name,
            "version": settings.app_version,
            "feed": settings.alpaca_feed,
            "overnight_feed": settings.alpaca_overnight_feed if settings.enable_overnight_stream else None,
            "universe": len(self.market._desired),
            "sip_subscribed": len(self.market.subscribed),
            "overnight_subscribed": len(self.market.overnight_subscribed),
            "states": len(self.market.states),
            "halts": len(self.market.current_halts()),
            "dashboard": settings.web_out_dir.exists(),
            "hybrid": hybrid_status,
            "feed_health": self.market.feed_health,
            "engines": ["RUST_PRIMARY", "AWAKENING", "PRE_IGNITION_SHADOW", "FIRST_LEG", "EARLY", "SURGE", "BREAKOUT", "STAIRCASE", "IGNITION", "REVERSAL_WATCH", "EMA_RECLAIM", "VWAP_RECLAIM", "REARM", "HALT_PRESSURE", "CATALYST_WATCH", "CATALYST_ACTIVE", "HALT"],
        })

    async def status(self, request: web.Request) -> web.Response:
        prefs = await asyncio.to_thread(self.store.get_notification_preferences)
        latest = await asyncio.to_thread(self.store.list_findings, 12)
        return web.json_response({
            "ok": True,
            "app": settings.app_name,
            "version": settings.app_version,
            "environment": settings.env,
            "feeds": {
                "sip": bool(self.market.ws),
                "boats": bool(self.market.overnight_ws) if settings.enable_overnight_stream else None,
                "news": bool(getattr(self.catalyst_watcher, "news_connected", False)) if self.catalyst_watcher is not None else True,
                "health": self.market.feed_health,
            },
            "universe": len(self.market._desired),
            "sip_subscribed": len(self.market.subscribed),
            "overnight_subscribed": len(self.market.overnight_subscribed),
            "tracked_states": len(self.market.states),
            "active_halts": len(self.market.current_halts()),
            "price_range": {"min": self.market.min_price, "max": self.market.max_price},
            "market_quality": {
                "profile": self.market.quality_profile,
                "min_active_ratio": settings.quality_min_active_ratio,
                "min_trades_30s": settings.quality_min_trades_30s,
                "min_dollar_30s": settings.quality_min_dollar_30s,
                "min_directional_efficiency": settings.quality_min_directional_efficiency,
            },
            "notifications": {
                "master_enabled": prefs["master_enabled"],
                "android_enabled": prefs["platforms"]["android"]["enabled"],
                "windows_enabled": prefs["platforms"]["windows"]["enabled"],
                "email_enabled": prefs["platforms"]["email"]["enabled"],
                "android_delivery_configured": bool((settings.vapid_public_key and settings.vapid_private_key) or (settings.ntfy_server and settings.ntfy_topic)),
                "webpush_configured": bool(settings.vapid_public_key and settings.vapid_private_key),
                "webpush_subscriptions": await asyncio.to_thread(self.store.web_push_subscription_count),
                "email_delivery_configured": bool(settings.email_every_finding and settings.resend_api_key and settings.resend_from and settings.resend_to),
                "windows_delivery_available": True,
                "queues": self.dispatcher.notification_queue_status() if self.dispatcher else {},
                "delivery": delivery_health(),
            },
            "hybrid": {
                "rust_bridge": self.market.rust_bridge.status() if self.market.rust_bridge else {"enabled": False, "running": False},
                "precision": await asyncio.to_thread(self.store.hybrid_precision_stats, settings.hybrid_precision_threshold_pct),
                "notification_latency": await asyncio.to_thread(self.store.notification_latency_stats),
                "architecture": "rust-primary+python-specialist",
            },
            "engines": {
                "rust_primary": bool(self.market.rust_bridge and self.market.rust_bridge.enabled),
                "awakening": True,
                "early": True,
                "first_leg": True,
                "surge": True,
                "breakout": True,
                "staircase": True,
                "ignition": True,
                "reversal_reclaim": True,
                "rearm": True,
                "catalyst": True,
                "halts": True,
            },
            "catalyst_sources": {
                "news_connected": bool(getattr(self.catalyst_watcher, "news_connected", False)) if self.catalyst_watcher is not None else None,
                "last_news_at": getattr(self.catalyst_watcher, "last_news_at", None),
                "last_sec_ok_at": getattr(self.catalyst_watcher, "last_sec_ok_at", None),
                "last_rss_ok_at": getattr(self.catalyst_watcher, "last_rss_ok_at", None),
                "rss_configured": bool(settings.rss_feeds),
                "watchlist_size": len(settings.catalyst_watchlist),
                "source_stale_seconds": settings.catalyst_source_stale_seconds,
                "health": getattr(self.catalyst_watcher, "source_health", {}) if self.catalyst_watcher is not None else {},
            },
            "replay": await asyncio.to_thread(replay_status),
            "latest_findings": latest,
        })

    async def replay_status(self, request: web.Request) -> web.Response:
        return web.json_response(await asyncio.to_thread(replay_status))

    async def findings(self, request: web.Request) -> web.Response:
        limit = _int(request.query.get("limit"), 100, 1, 500)
        ticker = request.query.get("ticker")
        stage = request.query.get("stage")
        episodes = str(request.query.get("episodes", "0")).lower() in {"1", "true", "yes"}
        if episodes and not ticker and not stage:
            rows = await asyncio.to_thread(self.store.list_episodes, limit)
        else:
            rows = await asyncio.to_thread(self.store.list_findings, limit, ticker, stage)
        rows = [row for row in rows if self.market.min_price <= float(row.get("price") or 0) <= self.market.max_price]
        return web.json_response({"items": rows})

    async def finding(self, request: web.Request) -> web.Response:
        finding_id = _int(request.match_info.get("finding_id"), 0, 1, 2_147_483_647)
        row = await asyncio.to_thread(self.store.get_finding, finding_id)
        if not row:
            raise web.HTTPNotFound(text="finding not found")
        return web.json_response(row)

    async def finding_verification(self, request: web.Request) -> web.Response:
        finding_id = _int(request.match_info.get("finding_id"), 0, 1, 2_147_483_647)
        row = await asyncio.to_thread(self.store.finding_verification, finding_id)
        if not row:
            raise web.HTTPNotFound(text="finding not found")
        return web.json_response(row)

    async def update_finding_review(self, request: web.Request) -> web.Response:
        finding_id = _int(request.match_info.get("finding_id"), 0, 1, 2_147_483_647)
        payload = await request.json()
        raw_grade = payload.get("user_grade")
        user_grade = None if raw_grade is None else max(1, min(5, int(raw_grade)))
        agrees = payload.get("user_agrees")
        row = await asyncio.to_thread(
            self.store.save_finding_review, finding_id, user_grade,
            None if agrees is None else bool(agrees),
            [str(x)[:100] for x in payload.get("reason_tags", [])][:20], str(payload.get("notes", "")),
        )
        if not row:
            raise web.HTTPNotFound(text="finding not found")
        self.events.publish("verification", row)
        return web.json_response(row)

    async def catalysts(self, request: web.Request) -> web.Response:
        limit = _int(request.query.get("limit"), 100, 1, 500)
        ticker = request.query.get("ticker")
        rows = await asyncio.to_thread(self.store.list_catalysts, limit, ticker)
        return web.json_response({"items": rows})

    async def gainers(self, request: web.Request) -> web.Response:
        top = _int(request.query.get("top"), 20, 1, 50)
        try:
            payload = await asyncio.to_thread(self.market.top_movers_sync, top)
        except Exception as exc:
            return web.json_response({"items": [], "error": str(exc)}, status=502)
        items = [row for row in payload.get("gainers", []) if row.get("price") is None or self.market.min_price <= float(row.get("price") or 0) <= self.market.max_price]
        return web.json_response({"items": items})

    async def halts(self, request: web.Request) -> web.Response:
        recent = await asyncio.to_thread(self.store.recent_market_status_events, 100)
        return web.json_response({"active": self.market.current_halts(), "recent": recent})

    async def snapshot(self, request: web.Request) -> web.Response:
        ticker = str(request.match_info.get("ticker", "")).upper()
        detected_at_raw = request.query.get("detected_at")
        finding_id_raw = request.query.get("finding_id")
        try:
            detected_at = float(detected_at_raw) if detected_at_raw else None
            bucket_seconds = _int(request.query.get("bucket_seconds"), 15, 15, 300)
            finding_id = int(finding_id_raw) if finding_id_raw else None
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text="invalid chart window")
        payload = self.market.snapshot_payload(ticker)
        live_rows = payload.get("buckets", []) if payload else []
        live_covers_detection = bool(
            detected_at and live_rows
            and float(live_rows[0]["start_ts"]) - bucket_seconds <= detected_at <= float(live_rows[-1]["start_ts"]) + bucket_seconds
        )
        if detected_at and not live_covers_detection:
            try:
                payload = await asyncio.to_thread(self.market.historical_snapshot_sync, ticker, detected_at, bucket_seconds)
            except Exception as exc:
                if not payload:
                    raise web.HTTPBadGateway(text=f"historical chart unavailable: {exc}")
        if not payload:
            raise web.HTTPNotFound(text="symbol has no live or historical chart data")
        findings, catalysts, statuses, delivery = await asyncio.gather(
            asyncio.to_thread(self.store.list_findings, 30, ticker, None),
            asyncio.to_thread(self.store.list_catalysts, 20, ticker),
            asyncio.to_thread(self.store.recent_market_status_events, 20, ticker),
            asyncio.to_thread(self.store.finding_delivery, finding_id) if finding_id else asyncio.sleep(0, result=[]),
        )
        payload["findings"] = findings
        payload["catalysts"] = catalysts
        payload["statuses"] = statuses
        payload["delivery"] = delivery
        return web.json_response(payload)

    async def diagnostics(self, request: web.Request) -> web.Response:
        ticker = str(request.match_info.get("ticker", "")).upper()
        return web.json_response(self.market.diagnostics(ticker))

    async def validation(self, request: web.Request) -> web.Response:
        limit = _int(request.query.get("limit"), 100, 1, 500)
        rows = await asyncio.to_thread(self.store.list_validation, limit)
        return web.json_response({"items": rows})

    async def timeline(self, request: web.Request) -> web.Response:
        ticker = request.query.get("ticker")
        limit = _int(request.query.get("limit"), 100, 1, 300)
        findings, catalysts, statuses = await asyncio.gather(
            asyncio.to_thread(self.store.list_findings, limit, ticker, None),
            asyncio.to_thread(self.store.list_catalysts, limit, ticker),
            asyncio.to_thread(self.store.recent_market_status_events, limit, ticker),
        )
        items = []
        for row in findings:
            items.append({"type": "finding", "at": row["detected_at"], "ticker": row["ticker"], "payload": row})
        for row in catalysts:
            items.append({"type": "catalyst", "at": row["published_at"], "ticker": row["ticker"], "payload": row})
        for row in statuses:
            items.append({"type": "halt" if row["is_halted"] else "resume", "at": row["event_at"], "ticker": row["ticker"], "payload": row})
        items.sort(key=lambda x: x["at"], reverse=True)
        return web.json_response({"items": items[:limit]})

    async def attention(self, request: web.Request) -> web.Response:
        limit = _int(request.query.get("limit"), 100, 1, 300)
        status = request.query.get("status")
        rows = await asyncio.to_thread(self.store.list_attention, limit, status)
        return web.json_response({"items": rows})

    async def update_attention(self, request: web.Request) -> web.Response:
        attention_id = _int(request.match_info.get("attention_id"), 0, 1, 2_147_483_647)
        try:
            payload = await request.json()
            status = str(payload.get("status", "")).lower()
            row = await asyncio.to_thread(self.store.update_attention, attention_id, status)
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc))
        if not row:
            raise web.HTTPNotFound(text="attention item not found")
        self.events.publish("attention", row)
        return web.json_response(row)

    async def notification_preferences(self, request: web.Request) -> web.Response:
        prefs = await asyncio.to_thread(self.store.get_notification_preferences)
        return web.json_response(prefs)

    async def scanner_settings(self, request: web.Request) -> web.Response:
        return web.json_response(await asyncio.to_thread(self.store.get_scanner_settings))

    async def update_scanner_settings(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
            minimum = float(payload.get("min_price"))
            maximum = float(payload.get("max_price"))
            if minimum < 0.01 or maximum > 1000 or minimum >= maximum:
                raise ValueError("use a valid range between $0.01 and $1,000")
            value = await self.market.apply_scanner_range(minimum, maximum)
        except (TypeError, ValueError) as exc:
            raise web.HTTPBadRequest(text=str(exc))
        self.events.publish("scanner-settings", value)
        return web.json_response(value)

    async def update_notification_preferences(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception as exc:
            raise web.HTTPBadRequest(text=f"invalid JSON: {exc}")
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="preference payload must be an object")
        prefs = await asyncio.to_thread(self.store.set_notification_preferences, payload)
        self.market.set_quality_profile(prefs.get("market_quality_profile", "balanced"))
        self.events.publish("notification-preferences", prefs)
        return web.json_response(prefs)

    async def test_notification(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        platform = str(payload.get("platform", "")).lower()
        if platform == "android":
            if settings.vapid_public_key and settings.vapid_private_key and await asyncio.to_thread(self.store.web_push_subscription_count):
                ok, message = await asyncio.to_thread(send_web_push_test, self.store)
                return web.json_response({"ok": ok, "platform": "webpush", "message": message})
            ok, message = await asyncio.to_thread(send_ntfy_test)
            return web.json_response({"ok": ok, "platform": platform, "message": message})
        if platform == "email":
            ok, message = await asyncio.to_thread(send_resend_test)
            return web.json_response({"ok": ok, "platform": platform, "message": message})
        if platform == "windows":
            return web.json_response({
                "ok": True,
                "platform": platform,
                "native": True,
                "message": "Use the installed Scout client to test a Windows native toast.",
            })
        raise web.HTTPBadRequest(text="platform must be android, windows, or email")

    async def push_config(self, request: web.Request) -> web.Response:
        return web.json_response({
            "enabled": bool(settings.vapid_public_key and settings.vapid_private_key),
            "public_key": settings.vapid_public_key,
            "subscriptions": await asyncio.to_thread(self.store.web_push_subscription_count),
        })

    async def subscribe_push(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
            subscription = payload.get("subscription", payload)
            keys = subscription.get("keys", {})
            saved = await asyncio.to_thread(
                self.store.upsert_web_push_subscription,
                str(subscription.get("endpoint", "")), str(keys.get("p256dh", "")), str(keys.get("auth", "")),
                request.headers.get("User-Agent", ""),
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise web.HTTPBadRequest(text=str(exc))
        return web.json_response({"ok": True, **saved})

    async def unsubscribe_push(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
            endpoint = str(payload.get("endpoint", ""))
        except Exception as exc:
            raise web.HTTPBadRequest(text=f"invalid JSON: {exc}")
        removed = await asyncio.to_thread(self.store.delete_web_push_subscription, endpoint)
        return web.json_response({"ok": True, "removed": removed})

    async def event_stream(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                **_cors_headers(request),
            },
        )
        await response.prepare(request)
        await response.write(b"event: ready\ndata: {\"ok\":true}\n\n")
        try:
            async with self.events.subscribe() as queue:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=20)
                        await response.write(self.events.encode_sse(event))
                    except asyncio.TimeoutError:
                        await response.write(b": keepalive\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return response

    async def chart(self, request: web.Request) -> web.StreamResponse:
        name = Path(request.match_info["name"]).name
        path = settings.chart_dir / name
        if not path.exists() or not path.is_file():
            raise web.HTTPNotFound(text="chart not found")
        return web.FileResponse(path)

    async def dashboard(self, request: web.Request) -> web.StreamResponse:
        index = settings.web_out_dir / "index.html"
        if not index.exists():
            return web.json_response({
                "ok": True,
                "app": settings.app_name,
                "message": "Scout API is running; dashboard bundle is not present in this image.",
            })
        return web.FileResponse(index)

    async def pwa_asset(self, request: web.Request) -> web.StreamResponse:
        name = Path(request.match_info["name"]).name
        path = settings.web_out_dir / name
        if name not in {"manifest.webmanifest", "sw.js"} or not path.is_file():
            raise web.HTTPNotFound(text="asset not found")
        response = web.FileResponse(path)
        if name == "sw.js":
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Service-Worker-Allowed"] = "/"
        return response


def create_app(store: Store, market: MarketWatcher, events: EventHub, catalysts=None, dispatcher=None) -> web.Application:
    api = ScoutApi(store, market, events, catalysts, dispatcher)
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_get("/healthz", api.health)
    app.router.add_get("/api/status", api.status)
    app.router.add_get("/api/replay/status", api.replay_status)
    app.router.add_get("/api/findings", api.findings)
    app.router.add_get("/api/findings/{finding_id:\\d+}", api.finding)
    app.router.add_get("/api/findings/{finding_id:\\d+}/verification", api.finding_verification)
    app.router.add_put("/api/findings/{finding_id:\\d+}/review", api.update_finding_review)
    app.router.add_get("/api/catalysts", api.catalysts)
    app.router.add_get("/api/market/gainers", api.gainers)
    app.router.add_get("/api/market/halts", api.halts)
    app.router.add_get("/api/market/snapshot/{ticker}", api.snapshot)
    app.router.add_get("/api/market/diagnostics/{ticker}", api.diagnostics)
    app.router.add_get("/api/validation", api.validation)
    app.router.add_get("/api/timeline", api.timeline)
    app.router.add_get("/api/attention", api.attention)
    app.router.add_patch("/api/attention/{attention_id:\\d+}", api.update_attention)
    app.router.add_get("/api/notifications/preferences", api.notification_preferences)
    app.router.add_put("/api/notifications/preferences", api.update_notification_preferences)
    app.router.add_post("/api/notifications/test", api.test_notification)
    app.router.add_get("/api/push/config", api.push_config)
    app.router.add_post("/api/push/subscriptions", api.subscribe_push)
    app.router.add_delete("/api/push/subscriptions", api.unsubscribe_push)
    app.router.add_get("/api/settings/scanner", api.scanner_settings)
    app.router.add_put("/api/settings/scanner", api.update_scanner_settings)
    app.router.add_get("/api/events", api.event_stream)
    app.router.add_get("/charts/{name}", api.chart)

    next_dir = settings.web_out_dir / "_next"
    if next_dir.exists():
        app.router.add_static("/_next/", next_dir, show_index=False)
    icons_dir = settings.web_out_dir / "icons"
    if icons_dir.exists():
        app.router.add_static("/icons/", icons_dir, show_index=False)
    app.router.add_get("/{name:manifest\\.webmanifest|sw\\.js}", api.pwa_asset)
    app.router.add_get("/", api.dashboard)
    return app

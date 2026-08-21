from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from datetime import datetime
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from .config import settings
from .db import Store
from .models import Finding
from .opportunity import is_group_a

log = logging.getLogger("scout.trader")
ACTIVE_ORDER_STATES = {"new", "accepted", "pending_new", "partially_filled", "filled"}


def _price_tick(price: float) -> Decimal:
    return Decimal("0.0001") if Decimal(str(price)) < Decimal("1") else Decimal("0.01")


def _round_order_price(price: float, *, upward: bool) -> float:
    value = Decimal(str(price))
    tick = _price_tick(price)
    rounding = ROUND_CEILING if upward else ROUND_FLOOR
    return float((value / tick).to_integral_value(rounding=rounding) * tick)


def _price_text(price: float) -> str:
    places = 4 if price < 1 else 2
    return f"{price:.{places}f}"


class PaperTrader:
    """Alpaca paper-only execution for confirmed Scout episodes.

    The hostname guard is deliberately non-configurable in spirit: even if an
    environment variable is wrong, this component refuses any non-paper host.
    """

    def __init__(self, store: Store):
        self.store = store
        self.base = settings.alpaca_trading_base.rstrip("/")
        self.paper_safe = urlparse(self.base).hostname == "paper-api.alpaca.markets"
        self.configured = bool(settings.alpaca_key and settings.alpaca_secret and self.paper_safe)
        self._lock = asyncio.Lock()
        self.last_error: str | None = None
        self.last_order_at: int | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {"APCA-API-KEY-ID": settings.alpaca_key, "APCA-API-SECRET-KEY": settings.alpaca_secret}

    def settings(self) -> dict[str, Any]:
        value = self.store.get_trader_settings()
        return {**value, "mode": "paper", "configured": self.configured, "paper_safe": self.paper_safe}

    def status(self) -> dict[str, Any]:
        return {**self.settings(), "last_error": self.last_error, "last_order_at": self.last_order_at, "performance": self.store.paper_trade_stats()}

    def update_settings(self, value: dict[str, Any]) -> dict[str, Any]:
        if bool(value.get("enabled")) and not self.configured:
            raise ValueError("Alpaca paper credentials are not configured or the endpoint is not paper-only")
        if bool(value.get("enabled")):
            try:
                account = self._request("GET", "/v2/account")
            except Exception as exc:
                raise ValueError(f"Alpaca paper account validation failed: {exc}") from exc
            if str(account.get("status", "")).upper() != "ACTIVE" or bool(account.get("trading_blocked")):
                raise ValueError("Alpaca paper account is not active or is trading-blocked")
        return {**self.store.set_trader_settings(value), "mode": "paper", "configured": self.configured, "paper_safe": self.paper_safe}

    @staticmethod
    def _regular_session(ts: float) -> bool:
        local = datetime.fromtimestamp(ts, ZoneInfo("America/New_York"))
        minutes = local.hour * 60 + local.minute
        return local.weekday() < 5 and 9 * 60 + 30 <= minutes < 16 * 60

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self.paper_safe:
            raise RuntimeError("paper trader refused a non-paper Alpaca endpoint")
        response = requests.request(method, self.base + path, headers=self.headers, timeout=10, **kwargs)
        if not response.ok:
            try:
                detail = response.json()
            except Exception:
                detail = {"message": response.text[:1000]}
            raise RuntimeError(f"Alpaca {response.status_code}: {json.dumps(detail, separators=(',', ':'))}")
        return response.json() if response.content else {}

    async def on_finding(self, finding_id: int, finding: Finding) -> None:
        if not is_group_a(finding, confirmed_only=True):
            return
        cfg = self.store.get_trader_settings()
        if not cfg["enabled"] or not self.configured:
            return
        if not self._regular_session(finding.detected_at) or finding.price <= 0:
            return
        async with self._lock:
            try:
                await asyncio.to_thread(self._submit_confirmed, finding_id, finding, cfg)
                self.last_error = None
            except Exception as exc:
                self.last_error = str(exc)[:500]
                log.exception("paper order failed for %s %s", finding.ticker, finding.stage)

    def _submit_confirmed(self, finding_id: int, finding: Finding, cfg: dict[str, Any]) -> None:
        stats = self.store.paper_trade_stats()
        if stats["open"] >= cfg["max_positions"]:
            raise RuntimeError("paper position limit reached")
        now_et = datetime.now(ZoneInfo("America/New_York"))
        day_start = now_et.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        daily_pl = sum(float(row.get("realized_pl") or 0) for row in self.store.list_paper_trades(500) if float(row.get("closed_at") or 0) >= day_start)
        if daily_pl <= -cfg["daily_loss_limit"]:
            raise RuntimeError("paper daily loss limit reached")
        asset = self._request("GET", f"/v2/assets/{finding.ticker}")
        if not asset.get("tradable") or asset.get("status") != "active":
            raise RuntimeError("symbol is not Alpaca-tradable")
        episode = finding.hybrid_key or f"{finding.ticker}:{int(finding.episode_id)}"
        client_id = f"scout-paper-{finding.ticker.lower()}-{abs(hash(episode)) % 10**12}"
        signal = round(float(finding.price), 4)
        floor_stop = signal * (1 - cfg["max_stop_pct"] / 100)
        structural = float(finding.invalidation_level) if finding.invalidation_level and 0 < finding.invalidation_level < signal else floor_stop
        # Alpaca advanced orders require a sell stop at least $0.01 below the
        # current base price. Cap the structural stop accordingly, then round
        # down to the security's accepted price increment.
        stop_ceiling = signal - 0.01
        stop = _round_order_price(min(max(floor_stop, structural), stop_ceiling), upward=False)
        signal_decimal = Decimal(str(signal))
        stop_decimal = Decimal(str(stop))
        risk_decimal = signal_decimal - stop_decimal
        if risk_decimal <= 0:
            raise RuntimeError("invalid paper trade risk geometry")
        target = _round_order_price(
            float(signal_decimal + risk_decimal * Decimal(str(cfg["risk_reward"]))), upward=True
        )
        quantity = max(1, math.floor(cfg["position_notional"] / signal))
        pending = {
            "episode_key": episode, "finding_id": finding_id, "ticker": finding.ticker,
            "client_order_id": client_id, "status": "submitting", "quantity": quantity,
            "signal_price": signal, "stop_price": stop, "target_price": target,
            "submitted_at": int(time.time()),
        }
        if not self.store.create_paper_trade(pending):
            return
        payload = {
            "symbol": finding.ticker, "qty": str(quantity), "side": "buy", "type": "market",
            "time_in_force": "day", "order_class": "bracket", "client_order_id": client_id,
            "take_profit": {"limit_price": _price_text(target)}, "stop_loss": {"stop_price": _price_text(stop)},
        }
        try:
            order = self._request("POST", "/v2/orders", json=payload)
        except Exception as exc:
            self.store.update_paper_trade(
                client_id, status="submit_failed", exit_reason="alpaca_rejected",
                raw_json=json.dumps({"error": str(exc), "payload": payload}, separators=(",", ":")),
            )
            raise
        self.store.update_paper_trade(client_id, alpaca_order_id=order.get("id"), status=order.get("status", "accepted"), raw_json=json.dumps(order))
        self.last_order_at = int(time.time())
        log.warning("PAPER ORDER %s qty=%s signal=%.4f stop=%.4f target=%.4f rr=1:%.2f", finding.ticker, quantity, signal, stop, target, cfg["risk_reward"])

    async def reconcile_loop(self) -> None:
        while True:
            try:
                if self.configured:
                    await asyncio.to_thread(self._reconcile)
                    self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)[:500]
                log.exception("paper trader reconciliation failed")
            await asyncio.sleep(10)

    def _reconcile(self) -> None:
        for trade in self.store.list_paper_trades(200):
            if trade["status"] not in ACTIVE_ORDER_STATES or not trade.get("alpaca_order_id"):
                continue
            order = self._request("GET", f"/v2/orders/{trade['alpaca_order_id']}?nested=true")
            status = str(order.get("status", trade["status"]))
            filled = float(order["filled_avg_price"]) if order.get("filled_avg_price") else trade.get("entry_price")
            updates: dict[str, Any] = {"status": status, "entry_price": filled, "raw_json": json.dumps(order)}
            if order.get("filled_at") and not trade.get("filled_at"):
                updates["filled_at"] = int(time.time())
            exit_leg = next((leg for leg in order.get("legs", []) if leg.get("side") == "sell" and leg.get("status") == "filled"), None)
            if exit_leg and filled:
                exit_price = float(exit_leg.get("filled_avg_price") or 0)
                quantity = float(order.get("filled_qty") or trade.get("quantity") or 0)
                updates.update({
                    "status": "closed", "exit_price": exit_price, "closed_at": int(time.time()),
                    "exit_reason": "target" if exit_price >= float(trade["target_price"]) * 0.999 else "stop",
                    "realized_pl": round((exit_price - float(filled)) * quantity, 4),
                })
            if status in {"canceled", "expired", "rejected", "replaced"}:
                updates["closed_at"] = int(time.time())
                updates["exit_reason"] = status
            self.store.update_paper_trade(trade["client_order_id"], **updates)

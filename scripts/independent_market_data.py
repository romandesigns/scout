from __future__ import annotations

import json
import math
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any

ET = ZoneInfo("America/New_York")
HORIZONS = (30, 60, 120, 300, 900)


def _f(value: Any) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or a == 0:
        return None
    return (b / a - 1.0) * 100.0


@dataclass
class IndependentBar:
    ts: float
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


class IndependentProvider:
    name = "none"

    @property
    def configured(self) -> bool:
        return False

    def bars(self, symbol: str, detected_at: float) -> list[IndependentBar]:
        raise NotImplementedError


class AlphaVantageProvider(IndependentProvider):
    """Independent intraday cross-check using Alpha Vantage TIME_SERIES_INTRADAY.

    This provider is validation-only. It is not used by Scout's live detector.
    Alpha Vantage's current intraday endpoint requires an API key and access to
    intraday data. Results are cached per ticker/day by the caller.
    """
    name = "alphavantage"

    def __init__(self, api_key: str | None = None, timeout: float = 30.0):
        self.api_key = (api_key or os.getenv("ALPHAVANTAGE_API_KEY") or "").strip()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def bars(self, symbol: str, detected_at: float) -> list[IndependentBar]:
        if not self.configured:
            raise RuntimeError("ALPHAVANTAGE_API_KEY is not configured")
        query = urllib.parse.urlencode({
            "function": "TIME_SERIES_INTRADAY",
            "symbol": symbol,
            "interval": "1min",
            "adjusted": "false",
            "extended_hours": "true",
            "outputsize": "full",
            "apikey": self.api_key,
        })
        req = urllib.request.Request(
            "https://www.alphavantage.co/query?" + query,
            headers={"User-Agent": "ScoutIndependentValidation/6.7.3", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))

        if not isinstance(payload, dict):
            raise RuntimeError("Alpha Vantage returned an invalid response")
        for error_key in ("Error Message", "Information", "Note"):
            if payload.get(error_key):
                raise RuntimeError(str(payload[error_key]))

        series_key = next((k for k in payload if k.lower().startswith("time series")), None)
        series = payload.get(series_key) if series_key else None
        if not isinstance(series, dict):
            raise RuntimeError("Alpha Vantage intraday series missing")

        target_day = datetime.fromtimestamp(detected_at, timezone.utc).astimezone(ET).date()
        out: list[IndependentBar] = []
        for stamp, values in series.items():
            if not isinstance(values, dict):
                continue
            try:
                dt_et = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ET)
            except ValueError:
                continue
            if dt_et.date() != target_day:
                continue
            op = _f(values.get("1. open"))
            hi = _f(values.get("2. high"))
            lo = _f(values.get("3. low"))
            cl = _f(values.get("4. close"))
            vol = _f(values.get("5. volume"))
            if None in (op, hi, lo, cl):
                continue
            out.append(IndependentBar(dt_et.astimezone(timezone.utc).timestamp(), op, hi, lo, cl, vol))
        out.sort(key=lambda b: b.ts)
        return out


class IndependentCrossChecker:
    def __init__(self, provider: IndependentProvider, tolerance_pct: float = 0.75):
        self.provider = provider
        self.tolerance_pct = max(0.0, float(tolerance_pct))
        self._cache: dict[tuple[str, str], list[IndependentBar] | Exception] = {}

    def _day_key(self, detected_at: float) -> str:
        return datetime.fromtimestamp(detected_at, timezone.utc).astimezone(ET).date().isoformat()

    def _bars(self, ticker: str, detected_at: float) -> list[IndependentBar]:
        key = (ticker.upper(), self._day_key(detected_at))
        cached = self._cache.get(key)
        if isinstance(cached, Exception):
            raise cached
        if cached is None:
            try:
                cached = self.provider.bars(ticker.upper(), detected_at)
            except Exception as exc:
                self._cache[key] = exc
                raise
            self._cache[key] = cached
        return cached

    @staticmethod
    def _nearest(bars: list[IndependentBar], target: float, tolerance: float = 90.0) -> IndependentBar | None:
        if not bars:
            return None
        bar = min(bars, key=lambda b: abs(b.ts - target))
        return bar if abs(bar.ts - target) <= tolerance else None

    def metrics(self, ticker: str, detected_at: float, scout_detection_price: float) -> dict[str, Any]:
        if not self.provider.configured:
            return {
                "provider": self.provider.name,
                "status": "NOT_CONFIGURED",
                "configured": False,
            }

        try:
            bars = self._bars(ticker, detected_at)
        except Exception as exc:
            return {
                "provider": self.provider.name,
                "status": "ERROR",
                "configured": True,
                "error": str(exc),
            }

        entry = self._nearest(bars, detected_at, tolerance=90.0)
        if entry is None:
            return {
                "provider": self.provider.name,
                "status": "NO_ENTRY_BAR",
                "configured": True,
            }

        # Use the independent minute close as its own entry anchor. We also expose
        # its delta from Scout's detection price to catch source disagreement.
        entry_price = entry.close
        result: dict[str, Any] = {
            "provider": self.provider.name,
            "status": "OK",
            "configured": True,
            "entry_ts": entry.ts,
            "entry_price": entry_price,
            "scout_detection_price": scout_detection_price,
            "entry_price_delta_pct": _pct(scout_detection_price, entry_price),
        }
        for horizon in HORIZONS:
            target = detected_at + horizon
            end = self._nearest(bars, target, tolerance=max(90.0, horizon * 0.20))
            window = [b for b in bars if detected_at <= b.ts <= target]
            result[f"return_{horizon}s_pct"] = _pct(entry_price, end.close) if end else None
            result[f"mfe_{horizon}s_pct"] = _pct(entry_price, max((b.high for b in window), default=entry_price))
            result[f"mae_{horizon}s_pct"] = _pct(entry_price, min((b.low for b in window), default=entry_price))
        return result

    def compare(self, scout_metrics: dict[str, Any], independent: dict[str, Any]) -> dict[str, Any]:
        if independent.get("status") != "OK":
            return {"status": independent.get("status"), "within_tolerance": None, "deltas": {}}
        deltas: dict[str, float | None] = {}
        comparisons: list[bool] = []
        for field in ("return_300s_pct", "return_900s_pct", "mfe_300s_pct", "mae_300s_pct"):
            a = _f(scout_metrics.get(field))
            b = _f(independent.get(field))
            delta = (a - b) if a is not None and b is not None else None
            deltas[field] = delta
            if delta is not None:
                comparisons.append(abs(delta) <= self.tolerance_pct)
        return {
            "status": "OK" if comparisons else "INSUFFICIENT_OVERLAP",
            "within_tolerance": all(comparisons) if comparisons else None,
            "tolerance_pct": self.tolerance_pct,
            "deltas": deltas,
        }


def make_provider(name: str | None, api_key: str | None = None) -> IndependentProvider:
    normalized = (name or "none").strip().lower()
    if normalized in {"", "none", "off", "disabled"}:
        return IndependentProvider()
    if normalized in {"alphavantage", "alpha_vantage", "alpha-vantage"}:
        return AlphaVantageProvider(api_key=api_key)
    raise ValueError(f"Unsupported independent provider: {name}")

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime
from urllib.parse import urljoin

import feedparser
import requests
import websockets
from bs4 import BeautifulSoup

from .classifier import classify_bullish, clean_text
from .config import settings
from .db import Store
from .dispatch import Dispatcher
from .market import MarketWatcher

log = logging.getLogger("scout.catalysts")
SEC_CURRENT_ATOM = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&count=100&output=atom"
SEC_TICKERS_JSON = "https://www.sec.gov/files/company_tickers.json"
HIGH_SIGNAL_FORMS = {"8-K", "8-K/A", "6-K", "6-K/A", "SC TO-T", "SC TO-I", "SC TO-C", "SC 14D9", "SCHEDULE 13D", "SCHEDULE 13D/A", "SC 13D", "SC 13D/A"}


def _key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8", "ignore")).hexdigest()


def _headers(sec: bool = False) -> dict[str, str]:
    return {"User-Agent": settings.sec_user_agent if sec else f"{settings.app_name}/3.0", "Accept-Encoding": "gzip, deflate"}


def _fetch(url: str, sec: bool = False, timeout: int = 12) -> requests.Response:
    r = requests.get(url, headers=_headers(sec), timeout=timeout)
    r.raise_for_status()
    return r


class CatalystWatcher:
    def __init__(self, store: Store, dispatcher: Dispatcher, market: MarketWatcher):
        self.store = store
        self.dispatcher = dispatcher
        self.market = market
        self.cik_to_ticker: dict[str, str] = {}
        self.news_connected = False
        self.last_news_at: int | None = None
        self.last_sec_ok_at: int | None = None
        self.last_rss_ok_at: int | None = None
        self.source_health: dict[str, dict[str, int | str | None]] = {
            "alpaca_news": {"last_ok_at": None, "last_error": None},
            "sec": {"last_ok_at": None, "last_error": None},
        }

    async def _emit(self, ticker: str, headline: str, category: str, score: int, url: str, source: str, published_at: int | None = None, *, verified: bool = False, verification_method: str = ""):
        ticker = ticker.upper().strip()
        if not ticker:
            return
        if ticker in settings.catalyst_watchlist:
            score = max(score, 5)
            category = f"WATCHLIST · {category}"
        ts = int(published_at or time.time())
        self.store.save_catalyst(ticker, headline, category, score, url, source, ts, verified=verified, verification_method=verification_method)
        if not verified:
            log.info("Unverified catalyst retained for audit only: %s source=%s", ticker, source)
            return
        f, buckets, current = self.market.make_catalyst_finding(ticker, headline, category, score, url, time.time())
        await self.dispatcher.emit(f, buckets, current)
        log.info("Bullish catalyst %s %s score=%d source=%s", ticker, category, score, source)

    def refresh_sec_map_sync(self):
        data = _fetch(SEC_TICKERS_JSON, sec=True, timeout=15).json()
        mapping = {}
        for row in data.values():
            cik = str(row.get("cik_str", "")).lstrip("0")
            ticker = str(row.get("ticker", "")).upper().strip()
            if cik and ticker:
                mapping[cik] = ticker
        self.cik_to_ticker = mapping
        log.info("SEC map loaded: %d", len(mapping))

    def _sec_form(self, entry) -> str:
        title = clean_text(getattr(entry, "title", ""))
        for form in sorted(HIGH_SIGNAL_FORMS, key=len, reverse=True):
            if title.upper().startswith(form.upper() + " ") or title.upper().startswith(form.upper() + "-"):
                return form
        return ""

    def _sec_ticker(self, entry) -> str:
        title = clean_text(getattr(entry, "title", ""))
        m = re.search(r"\((\d{10})\)", title)
        if m:
            return self.cik_to_ticker.get(m.group(1).lstrip("0"), "")
        link = str(getattr(entry, "link", ""))
        m = re.search(r"/data/(\d+)/", link)
        return self.cik_to_ticker.get(m.group(1).lstrip("0"), "") if m else ""

    def _filing_text(self, index_url: str) -> str:
        r = _fetch(index_url, sec=True)
        soup = BeautifulSoup(r.text, "html.parser")
        texts = [soup.get_text(" ", strip=True)]
        candidates: list[str] = []
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            label = clean_text(a.get_text(" ", strip=True)).lower()
            h = href.lower()
            if not h.endswith((".htm", ".html")) or "-index.htm" in h or "-index.html" in h:
                continue
            url = urljoin(index_url, href)
            if any(x in label for x in ("99.1", "ex-99", "exhibit 99", "press release")):
                candidates.insert(0, url)
            elif len(candidates) < 4:
                candidates.append(url)
        for url in list(dict.fromkeys(candidates))[:3]:
            try:
                rr = _fetch(url, sec=True)
                texts.append(BeautifulSoup(rr.text, "html.parser").get_text(" ", strip=True))
                time.sleep(0.11)
            except Exception:
                log.debug("SEC document fetch failed: %s", url, exc_info=True)
        return " ".join(texts)[:250_000]

    def sec_poll_sync(self) -> list[tuple[str, str, str, int, str, str, int]]:
        r = _fetch(SEC_CURRENT_ATOM, sec=True)
        feed = feedparser.parse(r.content)
        out = []
        for entry in reversed(feed.entries):
            eid = str(getattr(entry, "id", "") or getattr(entry, "link", "") or getattr(entry, "title", ""))
            k = _key("sec", eid)
            if not self.store.claim_seen(k, "sec"):
                continue
            form = self._sec_form(entry).upper()
            if form not in HIGH_SIGNAL_FORMS:
                continue
            ticker = self._sec_ticker(entry)
            if not ticker:
                continue
            link = str(getattr(entry, "link", ""))
            headline = clean_text(getattr(entry, "title", "")) or f"{ticker} {form}"
            base = headline + " " + clean_text(getattr(entry, "summary", ""))
            try:
                filing = self._filing_text(link) if link else ""
            except Exception:
                filing = ""
                log.exception("SEC filing fetch failed %s", link)
            score, cats, _, risks, bullish = classify_bullish(base + " " + filing)
            if not bullish:
                continue
            category = ", ".join(cats[:2]) or "Bullish SEC catalyst"
            out.append((ticker, headline, category, score, link, "SEC", int(time.time())))
        return out

    async def sec_loop(self):
        await asyncio.to_thread(self.refresh_sec_map_sync)
        while True:
            started = time.monotonic()
            try:
                rows = await asyncio.to_thread(self.sec_poll_sync)
                self.last_sec_ok_at = int(time.time())
                self.source_health["sec"] = {"last_ok_at": self.last_sec_ok_at, "last_error": None}
                for row in rows:
                    await self._emit(*row, verified=True, verification_method="sec-cik-filing")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.source_health["sec"] = {"last_ok_at": self.last_sec_ok_at, "last_error": str(exc)}
                log.exception("SEC poll failed")
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(1, settings.sec_poll_seconds - elapsed))

    def rss_poll_sync(self, url: str) -> list[tuple[str, str, str, int, str, str, int]]:
        r = _fetch(url)
        feed = feedparser.parse(r.content)
        source = clean_text(getattr(feed.feed, "title", "RSS")) or "RSS"
        out = []
        for entry in reversed(feed.entries[:100]):
            link = str(getattr(entry, "link", ""))
            title = clean_text(getattr(entry, "title", ""))
            summary = clean_text(getattr(entry, "summary", ""))
            eid = str(getattr(entry, "id", "") or link or title)
            k = _key("rss", url, eid)
            if not self.store.claim_seen(k, "rss"):
                continue
            score, cats, _, _, bullish = classify_bullish(title + " " + summary)
            if not bullish:
                continue
            tickers = []
            for pat in [r"(?:NASDAQ|NYSE|NYSEAMERICAN|NYSE AMERICAN|AMEX)\s*[:：]\s*([A-Z][A-Z0-9.\-]{0,9})", r"\bTicker\s*[:：]\s*([A-Z][A-Z0-9.\-]{0,9})\b"]:
                tickers.extend(re.findall(pat, title + " " + summary, flags=re.I))
            category = ", ".join(cats[:2]) or "Bullish catalyst"
            for ticker in list(dict.fromkeys(t.upper() for t in tickers))[:8]:
                out.append((ticker, title, category, score, link, source, int(time.time())))
        return out

    async def rss_loop(self):
        if not settings.rss_feeds:
            return
        while True:
            for url in settings.rss_feeds:
                health_key = f"rss:{url}"
                try:
                    rows = await asyncio.to_thread(self.rss_poll_sync, url)
                    self.last_rss_ok_at = int(time.time())
                    self.source_health[health_key] = {"last_ok_at": self.last_rss_ok_at, "last_error": None}
                    for row in rows:
                        await self._emit(*row)
                except Exception as exc:
                    previous = self.source_health.get(health_key, {})
                    self.source_health[health_key] = {"last_ok_at": previous.get("last_ok_at"), "last_error": str(exc)}
                    log.exception("RSS poll failed: %s", url)
            await asyncio.sleep(settings.sec_poll_seconds)

    async def alpaca_news_loop(self):
        # Real-time news is supplemental to the explicit 10-second SEC/RSS catalyst polling.
        if not settings.alpaca_key or not settings.alpaca_secret:
            return
        backoff = 2
        while True:
            try:
                async with websockets.connect(settings.alpaca_news_ws, ping_interval=20, ping_timeout=20, close_timeout=5, max_size=8_000_000) as ws:
                    await ws.send(json.dumps({"action": "auth", "key": settings.alpaca_key, "secret": settings.alpaca_secret}))
                    log.info("Alpaca news auth: %s", str(await asyncio.wait_for(ws.recv(), timeout=10))[:250])
                    await ws.send(json.dumps({"action": "subscribe", "news": ["*"]}))
                    self.news_connected = True
                    self.source_health["alpaca_news"] = {"last_ok_at": int(time.time()), "last_error": None}
                    backoff = 2
                    async for raw in ws:
                        try:
                            messages = json.loads(raw)
                        except Exception:
                            continue
                        if isinstance(messages, dict):
                            messages = [messages]
                        for msg in messages:
                            if not isinstance(msg, dict) or msg.get("T") != "n":
                                continue
                            eid = str(msg.get("id", ""))
                            k = _key("alpaca-news", eid)
                            if not await asyncio.to_thread(self.store.claim_seen, k, "alpaca-news"):
                                continue
                            headline = clean_text(str(msg.get("headline", "")))
                            summary = clean_text(str(msg.get("summary", "")))
                            content = clean_text(str(msg.get("content", "")))
                            score, cats, _, _, bullish = classify_bullish(headline + " " + summary + " " + content)
                            if not bullish:
                                continue
                            category = ", ".join(cats[:2]) or "Bullish news catalyst"
                            url = str(msg.get("url", ""))
                            source = str(msg.get("source", "Alpaca News"))
                            published = int(time.time())
                            self.last_news_at = published
                            try:
                                published = int(datetime.fromisoformat(str(msg.get("created_at", "")).replace("Z", "+00:00")).timestamp())
                            except Exception:
                                pass
                            for ticker in msg.get("symbols", []) or []:
                                if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", str(ticker).upper()):
                                    await self._emit(str(ticker).upper(), headline, category, score, url, source, published, verified=True, verification_method="alpaca-symbol-metadata")
            except asyncio.CancelledError:
                self.news_connected = False
                raise
            except Exception as exc:
                self.news_connected = False
                previous = self.source_health.get("alpaca_news", {})
                self.source_health["alpaca_news"] = {"last_ok_at": previous.get("last_ok_at"), "last_error": str(exc)}
                log.exception("Alpaca news stream disconnected; retry in %ss", backoff)
                await asyncio.sleep(backoff)
                backoff = min(60, backoff * 2)

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Finding
from .preferences import normalize_notification_preferences


FINDING_COLUMNS: list[tuple[str, str]] = [
    ("change_3s_pct", "REAL"),
    ("change_5s_pct", "REAL"),
    ("change_10s_pct", "REAL"),
    ("change_15s_pct", "REAL"),
    ("change_30s_pct", "REAL"),
    ("accel_15s_pp", "REAL"),
    ("dollar_volume_15s", "REAL"),
    ("dollar_volume_30s", "REAL"),
    ("trades_15s", "INTEGER"),
    ("trades_30s", "INTEGER"),
    ("breakout_level", "REAL"),
    ("breakout_window", "TEXT"),
    ("signals_json", "TEXT"),
    ("quality_label", "TEXT"),
    ("quality_score", "INTEGER"),
    ("actionable_rank", "TEXT"),
    ("rejection_reasons_json", "TEXT"),
    ("directional_efficiency", "REAL"),
    ("active_bucket_ratio", "REAL"),
    ("direction_reversals", "INTEGER"),
    ("previous_close", "REAL"),
    ("gap_pct", "REAL"),
    ("day_volume", "REAL"),
    ("projected_session_volume", "REAL"),
    ("volume_rate_per_minute", "REAL"),
    ("float_shares", "REAL"),
    ("float_turnover", "REAL"),
    ("candidate_profile_json", "TEXT"),
    ("episode_id", "INTEGER"),
    ("reversal_phase", "TEXT"),
    ("reversal_low", "REAL"),
    ("reversal_drawdown_pct", "REAL"),
    ("leg_context", "TEXT"),
    ("ross_match", "INTEGER"),
    ("ross_score", "INTEGER"),
    ("detection_timeframe_seconds", "INTEGER"),
    ("formation_start_at", "INTEGER"),
    ("formation_end_at", "INTEGER"),
    ("formation_low", "REAL"),
    ("formation_high", "REAL"),
    ("trigger_level", "REAL"),
    ("invalidation_level", "REAL"),
    ("halt_pressure_score", "INTEGER"),
    ("urgency", "TEXT"),
    ("engine_version", "TEXT"),
    ("lifecycle_phase", "TEXT"),
    ("shadow_mode", "INTEGER"),
    ("recipe_score", "INTEGER"),
    ("recipe_present_json", "TEXT"),
    ("recipe_missing_json", "TEXT"),
    ("trigger_distance_pct", "REAL"),
    ("base_extension_at_detection_pct", "REAL"),
    ("timeliness_label", "TEXT"),
    ("precursor_finding_id", "INTEGER"),
    ("engine_source", "TEXT"),
    ("hybrid_sources_json", "TEXT"),
    ("hybrid_score", "INTEGER"),
    ("hybrid_key", "TEXT"),
    ("notification_reason", "TEXT"),
    ("notification_delivered_at", "INTEGER"),
]


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self._init()

    def close(self) -> None:
        """Close the SQLite connection deterministically.

        SQLite files cannot be removed while a connection is open on Windows,
        so tests, replay jobs, and application shutdown must explicitly release
        the handle instead of relying on garbage collection.
        """
        with self.lock:
            db, self.db = self.db, None
            if db is not None:
                db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _ensure_columns(self, table: str, columns: list[tuple[str, str]]) -> None:
        existing = {str(row[1]) for row in self.db.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, sql_type in columns:
            if name not in existing:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")

    def _init(self) -> None:
        with self.lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS seen (
                    key TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    seen_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS catalysts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    headline TEXT NOT NULL,
                    category TEXT,
                    score INTEGER,
                    url TEXT,
                    source TEXT,
                    published_at INTEGER NOT NULL,
                    UNIQUE(ticker, headline)
                );
                CREATE INDEX IF NOT EXISTS ix_catalysts_ticker_time ON catalysts(ticker,published_at DESC);
                CREATE TABLE IF NOT EXISTS findings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    detected_at INTEGER NOT NULL,
                    price REAL NOT NULL,
                    score INTEGER NOT NULL,
                    vol_ratio_15s REAL,
                    vol_ratio_30s REAL,
                    change_60s_pct REAL,
                    extension_pct REAL,
                    ema9 REAL,
                    ema21 REAL,
                    ema9_slope REAL,
                    vwap REAL,
                    above_vwap INTEGER,
                    quiet_break INTEGER,
                    evidence_json TEXT,
                    catalyst_headline TEXT,
                    catalyst_category TEXT,
                    catalyst_score INTEGER,
                    catalyst_url TEXT,
                    chart_path TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_findings_time ON findings(detected_at DESC);
                CREATE INDEX IF NOT EXISTS ix_findings_ticker_time ON findings(ticker,detected_at DESC);
                CREATE TABLE IF NOT EXISTS outcomes (
                    finding_id INTEGER PRIMARY KEY,
                    max_1m_pct REAL,
                    max_5m_pct REAL,
                    max_15m_pct REAL,
                    max_session_pct REAL,
                    time_to_peak_seconds REAL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notification_preferences (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scanner_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trader_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    episode_key TEXT NOT NULL UNIQUE,
                    finding_id INTEGER NOT NULL,
                    ticker TEXT NOT NULL,
                    client_order_id TEXT NOT NULL UNIQUE,
                    alpaca_order_id TEXT,
                    status TEXT NOT NULL,
                    quantity REAL,
                    signal_price REAL NOT NULL,
                    entry_price REAL,
                    stop_price REAL NOT NULL,
                    target_price REAL NOT NULL,
                    exit_price REAL,
                    submitted_at INTEGER NOT NULL,
                    filled_at INTEGER,
                    closed_at INTEGER,
                    exit_reason TEXT,
                    realized_pl REAL,
                    raw_json TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_paper_trades_status_time ON paper_trades(status,submitted_at DESC);
                CREATE TABLE IF NOT EXISTS market_status_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    status_code TEXT,
                    status_message TEXT,
                    reason_code TEXT,
                    reason_message TEXT,
                    event_at INTEGER NOT NULL,
                    is_halted INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS ix_market_status_time ON market_status_events(event_at DESC);
                CREATE INDEX IF NOT EXISTS ix_market_status_ticker_time ON market_status_events(ticker,event_at DESC);
                CREATE TABLE IF NOT EXISTS opportunity_attention (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    episode_key TEXT NOT NULL UNIQUE,
                    ticker TEXT NOT NULL,
                    first_finding_id INTEGER NOT NULL,
                    latest_finding_id INTEGER NOT NULL,
                    stage_priority INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'unread',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_attention_status_time ON opportunity_attention(status,updated_at DESC);
                CREATE TABLE IF NOT EXISTS notification_delivery_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    finding_id INTEGER NOT NULL,
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL,
                    event_at INTEGER NOT NULL,
                    detail TEXT,
                    provider_id TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_delivery_finding_time ON notification_delivery_events(finding_id,event_at);
                CREATE TABLE IF NOT EXISTS finding_reviews (
                    finding_id INTEGER PRIMARY KEY,
                    automatic_grade INTEGER,
                    automatic_label TEXT,
                    user_grade INTEGER,
                    user_agrees INTEGER,
                    reason_tags_json TEXT,
                    notes TEXT,
                    reviewed_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS web_push_subscriptions (
                    endpoint TEXT PRIMARY KEY,
                    p256dh TEXT NOT NULL,
                    auth TEXT NOT NULL,
                    user_agent TEXT,
                    created_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL
                );
                """
            )
            self._ensure_columns("findings", FINDING_COLUMNS)
            self._ensure_columns("catalysts", [
                ("verified", "INTEGER NOT NULL DEFAULT 0"),
                ("verification_method", "TEXT"),
            ])
            # hybrid_key is a migrated column, so create its index only after
            # _ensure_columns has guaranteed that the column exists on older DBs.
            self.db.execute(
                "CREATE INDEX IF NOT EXISTS ix_findings_hybrid_key_time "
                "ON findings(hybrid_key, detected_at, id)"
            )
            self.db.commit()

    def upsert_web_push_subscription(self, endpoint: str, p256dh: str, auth: str, user_agent: str = "") -> dict[str, Any]:
        endpoint, p256dh, auth = endpoint.strip(), p256dh.strip(), auth.strip()
        if not endpoint.startswith("https://") or not p256dh or not auth:
            raise ValueError("invalid Web Push subscription")
        now = int(time.time())
        with self.lock:
            self.db.execute(
                "INSERT INTO web_push_subscriptions(endpoint,p256dh,auth,user_agent,created_at,last_seen_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh,auth=excluded.auth,user_agent=excluded.user_agent,last_seen_at=excluded.last_seen_at",
                (endpoint[:4000], p256dh[:1000], auth[:1000], user_agent[:500], now, now),
            )
            self.db.commit()
        return {"endpoint": endpoint, "created_at": now}

    def list_web_push_subscriptions(self) -> list[dict[str, str]]:
        with self.lock:
            rows = self.db.execute("SELECT endpoint,p256dh,auth,user_agent FROM web_push_subscriptions ORDER BY last_seen_at DESC").fetchall()
        return [dict(zip(["endpoint", "p256dh", "auth", "user_agent"], row)) for row in rows]

    def delete_web_push_subscription(self, endpoint: str) -> bool:
        with self.lock:
            cursor = self.db.execute("DELETE FROM web_push_subscriptions WHERE endpoint=?", (endpoint.strip(),))
            self.db.commit()
        return cursor.rowcount > 0

    def web_push_subscription_count(self) -> int:
        with self.lock:
            return int(self.db.execute("SELECT COUNT(*) FROM web_push_subscriptions").fetchone()[0])

    def seen(self, key: str) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone() is not None

    def claim_seen(self, key: str, source: str) -> bool:
        """Atomically claim a dedupe key. Return True only for the first caller.

        This replaces the hot seen()+mark_seen() two-lock sequence in catalyst
        ingestion and cuts both lock contention and SQLite round trips.
        """
        with self.lock:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO seen(key,source,seen_at) VALUES(?,?,?)",
                (key, source, int(time.time())),
            )
            self.db.commit()
            return cursor.rowcount > 0

    def mark_seen(self, key: str, source: str) -> None:
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO seen(key,source,seen_at) VALUES(?,?,?)", (key, source, int(time.time())))
            self.db.commit()

    def save_catalyst(self, ticker: str, headline: str, category: str, score: int, url: str, source: str, published_at: int | None = None, *, verified: bool = False, verification_method: str = "") -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO catalysts(ticker,headline,category,score,url,source,published_at,verified,verification_method) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(ticker,headline) DO UPDATE SET category=excluded.category,score=excluded.score,url=excluded.url,source=excluded.source,published_at=excluded.published_at,verified=excluded.verified,verification_method=excluded.verification_method",
                (ticker.upper(), headline[:1000], category[:200], int(score), url[:2000], source[:100], int(published_at or time.time()), int(verified), verification_method[:100]),
            )
            self.db.commit()

    def get_scanner_settings(self) -> dict[str, float]:
        defaults = {"min_price": 0.15, "max_price": 10.0}
        with self.lock:
            row = self.db.execute("SELECT value_json FROM scanner_settings WHERE key='range'").fetchone()
        if not row:
            return defaults
        try:
            value = json.loads(row[0])
            minimum = max(0.01, float(value.get("min_price", defaults["min_price"])))
            maximum = min(1000.0, float(value.get("max_price", defaults["max_price"])))
            return {"min_price": minimum, "max_price": maximum} if minimum < maximum else defaults
        except Exception:
            return defaults

    def set_scanner_settings(self, minimum: float, maximum: float) -> dict[str, float]:
        minimum = max(0.01, round(float(minimum), 4))
        maximum = min(1000.0, round(float(maximum), 4))
        if minimum >= maximum:
            raise ValueError("minimum price must be below maximum price")
        value = {"min_price": minimum, "max_price": maximum}
        with self.lock:
            self.db.execute(
                "INSERT INTO scanner_settings(key,value_json,updated_at) VALUES('range',?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                (json.dumps(value), int(time.time())),
            )
            self.db.commit()
        return value

    def get_trader_settings(self) -> dict[str, Any]:
        defaults = {
            "enabled": False, "risk_reward": 3.0, "position_notional": 100.0,
            "max_positions": 3, "daily_loss_limit": 25.0, "max_stop_pct": 3.0,
        }
        with self.lock:
            row = self.db.execute("SELECT value_json FROM trader_settings WHERE key='paper'").fetchone()
        if not row:
            return defaults
        try:
            value = {**defaults, **json.loads(row[0])}
            return {
                "enabled": bool(value["enabled"]),
                "risk_reward": max(1.0, min(10.0, float(value["risk_reward"]))),
                "position_notional": max(10.0, min(10000.0, float(value["position_notional"]))),
                "max_positions": max(1, min(20, int(value["max_positions"]))),
                "daily_loss_limit": max(1.0, min(10000.0, float(value["daily_loss_limit"]))),
                "max_stop_pct": max(0.25, min(10.0, float(value["max_stop_pct"]))),
            }
        except Exception:
            return defaults

    def set_trader_settings(self, value: dict[str, Any]) -> dict[str, Any]:
        current = self.get_trader_settings()
        merged = {**current, **value}
        normalized = {
            "enabled": bool(merged["enabled"]),
            "risk_reward": round(max(1.0, min(10.0, float(merged["risk_reward"]))), 2),
            "position_notional": round(max(10.0, min(10000.0, float(merged["position_notional"]))), 2),
            "max_positions": max(1, min(20, int(merged["max_positions"]))),
            "daily_loss_limit": round(max(1.0, min(10000.0, float(merged["daily_loss_limit"]))), 2),
            "max_stop_pct": round(max(0.25, min(10.0, float(merged["max_stop_pct"]))), 2),
        }
        with self.lock:
            self.db.execute(
                "INSERT INTO trader_settings(key,value_json,updated_at) VALUES('paper',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                (json.dumps(normalized, separators=(",", ":")), int(time.time())),
            )
            self.db.commit()
        return normalized

    def create_paper_trade(self, value: dict[str, Any]) -> bool:
        with self.lock:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO paper_trades(episode_key,finding_id,ticker,client_order_id,alpaca_order_id,status,quantity,signal_price,entry_price,stop_price,target_price,submitted_at,filled_at,closed_at,exit_reason,realized_pl,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (value["episode_key"], value["finding_id"], value["ticker"], value["client_order_id"], value.get("alpaca_order_id"), value["status"], value.get("quantity"), value["signal_price"], value.get("entry_price"), value["stop_price"], value["target_price"], value["submitted_at"], value.get("filled_at"), value.get("closed_at"), value.get("exit_reason"), value.get("realized_pl"), json.dumps(value.get("raw", {}))),
            )
            self.db.commit()
            return cursor.rowcount > 0

    def update_paper_trade(self, client_order_id: str, **values: Any) -> None:
        allowed = {"alpaca_order_id", "status", "quantity", "entry_price", "exit_price", "filled_at", "closed_at", "exit_reason", "realized_pl", "raw_json"}
        clean = {key: value for key, value in values.items() if key in allowed}
        if not clean:
            return
        sql = ",".join(f"{key}=?" for key in clean)
        with self.lock:
            self.db.execute(f"UPDATE paper_trades SET {sql} WHERE client_order_id=?", (*clean.values(), client_order_id))
            self.db.commit()

    def list_paper_trades(self, limit: int = 100) -> list[dict[str, Any]]:
        keys = ["id","episode_key","finding_id","ticker","client_order_id","alpaca_order_id","status","quantity","signal_price","entry_price","stop_price","target_price","exit_price","submitted_at","filled_at","closed_at","exit_reason","realized_pl"]
        with self.lock:
            rows = self.db.execute(f"SELECT {','.join(keys)} FROM paper_trades ORDER BY submitted_at DESC LIMIT ?", (max(1, min(500, int(limit))),)).fetchall()
        return [dict(zip(keys, row)) for row in rows]

    def paper_trade_stats(self) -> dict[str, Any]:
        with self.lock:
            rows = self.db.execute("SELECT status,realized_pl FROM paper_trades").fetchall()
        closed = [float(pl) for status, pl in rows if status in {"filled", "closed", "canceled", "rejected"} and pl is not None]
        open_count = sum(1 for status, _ in rows if status in {"new", "accepted", "pending_new", "partially_filled", "filled"})
        wins = sum(1 for value in closed if value > 0)
        return {"total": len(rows), "open": open_count, "closed": len(closed), "wins": wins, "win_rate": round(wins / len(closed), 4) if closed else None, "realized_pl": round(sum(closed), 2)}

    def recent_catalyst(self, ticker: str, max_age_minutes: int = 360):
        cutoff = int(time.time()) - max_age_minutes * 60
        with self.lock:
            return self.db.execute(
                "SELECT headline,category,score,url,published_at FROM catalysts WHERE ticker=? AND published_at>=? AND verified=1 ORDER BY published_at DESC LIMIT 1",
                (ticker.upper(), cutoff),
            ).fetchone()

    def save_finding(self, f: Finding) -> int:
        with self.lock:
            cur = self.db.execute(
                """
                INSERT INTO findings(
                    ticker,stage,detected_at,price,score,vol_ratio_15s,vol_ratio_30s,change_60s_pct,extension_pct,
                    ema9,ema21,ema9_slope,vwap,above_vwap,quiet_break,evidence_json,catalyst_headline,catalyst_category,
                    catalyst_score,catalyst_url,chart_path,change_3s_pct,change_5s_pct,change_10s_pct,change_15s_pct,
                    change_30s_pct,accel_15s_pp,dollar_volume_15s,dollar_volume_30s,trades_15s,trades_30s,
                    breakout_level,breakout_window,signals_json,quality_label,quality_score,actionable_rank,
                    rejection_reasons_json,directional_efficiency,active_bucket_ratio,direction_reversals,
                    previous_close,gap_pct,day_volume,projected_session_volume,volume_rate_per_minute,float_shares,float_turnover,candidate_profile_json,
                    episode_id,reversal_phase,reversal_low,reversal_drawdown_pct,leg_context,ross_match,ross_score,
                    detection_timeframe_seconds,formation_start_at,formation_end_at,formation_low,formation_high,trigger_level,
                    invalidation_level,halt_pressure_score,urgency,engine_version,lifecycle_phase,shadow_mode,recipe_score,
                    recipe_present_json,recipe_missing_json,trigger_distance_pct,base_extension_at_detection_pct,timeliness_label,precursor_finding_id,
                    engine_source,hybrid_sources_json,hybrid_score,hybrid_key,notification_reason
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f.ticker, f.stage, int(f.detected_at), f.price, f.score, f.vol_ratio_15s, f.vol_ratio_30s,
                    f.change_60s_pct, f.extension_pct, f.ema9, f.ema21, f.ema9_slope, f.vwap,
                    int(f.above_vwap), int(f.quiet_break), json.dumps(f.evidence), f.catalyst_headline,
                    f.catalyst_category, f.catalyst_score, f.catalyst_url, f.chart_path,
                    f.change_3s_pct, f.change_5s_pct, f.change_10s_pct, f.change_15s_pct, f.change_30s_pct,
                    f.accel_15s_pp, f.dollar_volume_15s, f.dollar_volume_30s, f.trades_15s, f.trades_30s,
                    f.breakout_level, f.breakout_window, json.dumps(f.signals or []), f.quality_label, f.quality_score,
                    f.actionable_rank, json.dumps(f.rejection_reasons or []), f.directional_efficiency,
                    f.active_bucket_ratio, f.direction_reversals,
                    f.previous_close, f.gap_pct, f.day_volume, f.projected_session_volume, f.volume_rate_per_minute,
                    f.float_shares, f.float_turnover, json.dumps(f.candidate_profile or {}),
                    f.episode_id, f.reversal_phase, f.reversal_low, f.reversal_drawdown_pct,
                    f.leg_context, int(f.ross_match), f.ross_score,
                    f.detection_timeframe_seconds, int(f.formation_start_at) if f.formation_start_at else None,
                    int(f.formation_end_at) if f.formation_end_at else None, f.formation_low, f.formation_high,
                    f.trigger_level, f.invalidation_level, f.halt_pressure_score, f.urgency, f.engine_version,
                    f.lifecycle_phase, int(f.shadow_mode), f.recipe_score, json.dumps(f.recipe_present or []),
                    json.dumps(f.recipe_missing or []), f.trigger_distance_pct, f.base_extension_at_detection_pct,
                    f.timeliness_label, f.precursor_finding_id, f.engine_source, json.dumps(f.hybrid_sources or []),
                    f.hybrid_score, f.hybrid_key, f.notification_reason,
                ),
            )
            self.db.commit()
            finding_id = int(cur.lastrowid)
            self._upsert_attention_locked(finding_id, f)
            self.db.commit()
            return finding_id

    @staticmethod
    def _attention_priority(stage: str) -> int:
        return {
            "FIRST_LEG": 100, "CATALYST": 95, "CATALYST_WATCH": 95, "CATALYST_ACTIVE": 110, "HALT": 95,
            "EMA_RECLAIM": 85, "VWAP_RECLAIM": 85, "REARM": 85,
            "IGNITION": 80, "BREAKOUT": 75, "SURGE": 70, "AWAKENING": 65, "EARLY": 60,
        }.get(stage, 0)

    def _upsert_attention_locked(self, finding_id: int, f: Finding) -> None:
        if (f.candidate_profile or {}).get("opportunity_class") == "LATE_INFORMATION_ONLY":
            return
        priority = self._attention_priority(f.stage)
        if priority <= 0 or (f.stage not in {"CATALYST", "CATALYST_WATCH", "CATALYST_ACTIVE", "HALT", "RESUME"} and f.quality_label != "CLEAN"):
            return
        day = datetime.fromtimestamp(f.detected_at).strftime("%Y%m%d")
        episode_key = f"{f.ticker.upper()}:{day}:{int(f.episode_id or 0)}"
        now = int(time.time())
        row = self.db.execute(
            "SELECT id,stage_priority,status FROM opportunity_attention WHERE episode_key=?",
            (episode_key,),
        ).fetchone()
        if row:
            status = "unread" if priority > int(row[1]) and row[2] != "watching" else row[2]
            self.db.execute(
                "UPDATE opportunity_attention SET latest_finding_id=?,stage_priority=?,status=?,updated_at=? WHERE id=?",
                (finding_id, max(priority, int(row[1])), status, now, int(row[0])),
            )
        else:
            self.db.execute(
                "INSERT INTO opportunity_attention(episode_key,ticker,first_finding_id,latest_finding_id,stage_priority,status,created_at,updated_at) VALUES(?,?,?,?,?,'unread',?,?)",
                (episode_key, f.ticker.upper(), finding_id, finding_id, priority, now, now),
            )

    def list_attention(self, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(300, int(limit)))
        allowed = {"unread", "opened", "watching", "acknowledged", "dismissed", "expired"}
        where = "WHERE a.status=?" if status in allowed else ""
        params: list[Any] = [status] if status in allowed else []
        params.append(limit)
        sql = f"""
            SELECT a.id,a.episode_key,a.ticker,a.first_finding_id,a.latest_finding_id,a.stage_priority,a.status,a.created_at,a.updated_at
            FROM opportunity_attention a {where}
            ORDER BY CASE a.status WHEN 'unread' THEN 0 WHEN 'watching' THEN 1 WHEN 'opened' THEN 2 ELSE 3 END,
                     a.stage_priority DESC,a.updated_at DESC LIMIT ?
        """
        with self.lock:
            rows = self.db.execute(sql, params).fetchall()
        items = []
        for row in rows:
            finding = self.get_finding(int(row[4]))
            if finding:
                items.append({
                    "id": row[0], "episode_key": row[1], "ticker": row[2],
                    "first_finding_id": row[3], "latest_finding_id": row[4],
                    "priority": row[5], "status": row[6], "created_at": row[7], "updated_at": row[8],
                    "finding": finding,
                })
        return items

    def update_attention(self, attention_id: int, status: str) -> dict[str, Any] | None:
        allowed = {"unread", "opened", "watching", "acknowledged", "dismissed", "expired"}
        if status not in allowed:
            raise ValueError("invalid attention status")
        with self.lock:
            self.db.execute(
                "UPDATE opportunity_attention SET status=?,updated_at=? WHERE id=?",
                (status, int(time.time()), int(attention_id)),
            )
            self.db.commit()
        return next((row for row in self.list_attention(300) if row["id"] == int(attention_id)), None)

    def update_chart_path(self, finding_id: int, chart_path: str) -> None:
        with self.lock:
            self.db.execute("UPDATE findings SET chart_path=? WHERE id=?", (chart_path, finding_id))
            self.db.commit()

    def record_delivery(self, finding_id: int, channel: str, status: str, detail: str | None = None, provider_id: str | None = None) -> int:
        with self.lock:
            event_at = int(time.time())
            cur = self.db.execute(
                "INSERT INTO notification_delivery_events(finding_id,channel,status,event_at,detail,provider_id) VALUES(?,?,?,?,?,?)",
                (int(finding_id), channel[:32], status[:32], event_at, (detail or "")[:1000], (provider_id or "")[:200]),
            )
            if status == "provider_accepted":
                self.db.execute(
                    "UPDATE findings SET notification_delivered_at=COALESCE(notification_delivered_at,?) WHERE id=?",
                    (event_at, int(finding_id)),
                )
            self.db.commit()
            return int(cur.lastrowid)

    def finding_delivery(self, finding_id: int) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT id,finding_id,channel,status,event_at,detail,provider_id FROM notification_delivery_events WHERE finding_id=? ORDER BY event_at,id",
                (int(finding_id),),
            ).fetchall()
        keys = ["id","finding_id","channel","status","event_at","detail","provider_id"]
        return [dict(zip(keys, row)) for row in rows]

    @staticmethod
    def _automatic_grade(finding: dict[str, Any], outcome: dict[str, Any] | None) -> tuple[int, str, list[str]]:
        if not outcome or outcome.get("max_5m_pct") is None:
            return 0, "PROVISIONAL", ["Outcome is still maturing"]
        favorable = max(float(outcome.get("max_5m_pct") or 0), float(outcome.get("max_15m_pct") or 0))
        extension = float(finding.get("extension_pct") or 0)
        reasons: list[str] = []
        if extension >= 8:
            reasons.append("Detected after vertical extension")
        if favorable >= 12 and extension <= 4:
            return 5, "EXCEPTIONAL", ["Early structure with strong follow-through"]
        if favorable >= 6 and extension <= 7:
            return 4, "STRONG", ["Timely detection with useful continuation"]
        if favorable >= 2:
            return 3, "VALID", ["Positive follow-through, but timing or strength was limited"]
        if favorable > 0:
            return 2, "POOR", reasons or ["Limited favorable movement after detection"]
        return 1, "FALSE SIGNAL", reasons or ["No favorable follow-through"]

    def finding_verification(self, finding_id: int) -> dict[str, Any] | None:
        finding = self.get_finding(finding_id)
        if not finding:
            return None
        with self.lock:
            outcome_row = self.db.execute(
                "SELECT max_1m_pct,max_5m_pct,max_15m_pct,max_session_pct,time_to_peak_seconds,updated_at FROM outcomes WHERE finding_id=?",
                (int(finding_id),),
            ).fetchone()
            review_row = self.db.execute(
                "SELECT automatic_grade,automatic_label,user_grade,user_agrees,reason_tags_json,notes,reviewed_at FROM finding_reviews WHERE finding_id=?",
                (int(finding_id),),
            ).fetchone()
        outcome = dict(zip(["max_1m_pct","max_5m_pct","max_15m_pct","max_session_pct","time_to_peak_seconds","updated_at"], outcome_row)) if outcome_row else None
        grade, label, reasons = self._automatic_grade(finding, outcome)
        review = None
        if review_row:
            review = dict(zip(["automatic_grade","automatic_label","user_grade","user_agrees","reason_tags_json","notes","reviewed_at"], review_row))
            try: review["reason_tags"] = json.loads(review.pop("reason_tags_json") or "[]")
            except Exception: review["reason_tags"] = []
        return {"finding": finding, "outcome": outcome, "automatic_grade": grade, "automatic_label": label, "grade_reasons": reasons, "delivery": self.finding_delivery(finding_id), "review": review, "legacy_delivery_audit": not bool(self.finding_delivery(finding_id))}

    def save_finding_review(self, finding_id: int, user_grade: int | None, user_agrees: bool | None, reason_tags: list[str], notes: str) -> dict[str, Any] | None:
        verification = self.finding_verification(finding_id)
        if not verification:
            return None
        grade = verification["automatic_grade"]
        label = verification["automatic_label"]
        with self.lock:
            self.db.execute(
                "INSERT INTO finding_reviews(finding_id,automatic_grade,automatic_label,user_grade,user_agrees,reason_tags_json,notes,reviewed_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(finding_id) DO UPDATE SET automatic_grade=excluded.automatic_grade,automatic_label=excluded.automatic_label,user_grade=excluded.user_grade,user_agrees=excluded.user_agrees,reason_tags_json=excluded.reason_tags_json,notes=excluded.notes,reviewed_at=excluded.reviewed_at",
                (int(finding_id), grade, label, user_grade, None if user_agrees is None else int(user_agrees), json.dumps(reason_tags), notes[:4000], int(time.time())),
            )
            self.db.commit()
        return self.finding_verification(finding_id)

    def last_findings(self, limit: int = 10) -> list[tuple[Any, ...]]:
        with self.lock:
            return self.db.execute(
                "SELECT id,ticker,stage,detected_at,price,score,vol_ratio_15s,vol_ratio_30s,change_60s_pct,extension_pct,chart_path FROM findings ORDER BY detected_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def _decode_finding(self, row: tuple[Any, ...], keys: list[str]) -> dict[str, Any]:
        item = dict(zip(keys, row))
        try:
            item["evidence"] = json.loads(item.pop("evidence_json") or "[]")
        except Exception:
            item["evidence"] = []
            item.pop("evidence_json", None)
        try:
            item["signals"] = json.loads(item.pop("signals_json") or "[]")
        except Exception:
            item["signals"] = []
            item.pop("signals_json", None)
        try:
            item["rejection_reasons"] = json.loads(item.pop("rejection_reasons_json") or "[]")
        except Exception:
            item["rejection_reasons"] = []
            item.pop("rejection_reasons_json", None)
        try:
            item["candidate_profile"] = json.loads(item.pop("candidate_profile_json") or "{}")
        except Exception:
            item["candidate_profile"] = {}
            item.pop("candidate_profile_json", None)
        item["opportunity_class"] = item["candidate_profile"].get("opportunity_class")
        try:
            item["hybrid_sources"] = json.loads(item.pop("hybrid_sources_json") or "[]")
        except Exception:
            item["hybrid_sources"] = []
            item.pop("hybrid_sources_json", None)
        for source, target in (("recipe_present_json", "recipe_present"), ("recipe_missing_json", "recipe_missing")):
            try:
                item[target] = json.loads(item.pop(source) or "[]")
            except Exception:
                item[target] = []
                item.pop(source, None)
        item["above_vwap"] = bool(item.get("above_vwap"))
        item["quiet_break"] = bool(item.get("quiet_break"))
        item["ross_match"] = bool(item.get("ross_match"))
        item["shadow_mode"] = bool(item.get("shadow_mode"))
        item["chart_url"] = f"/charts/{Path(item['chart_path']).name}" if item.get("chart_path") else None
        return item

    def list_findings(self, limit: int = 100, ticker: str | None = None, stage: str | None = None, before: float | None = None, actionable_only: bool = False, engine_version: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        where = []
        params: list[Any] = []
        if ticker:
            where.append("ticker=?")
            params.append(ticker.upper())
        if stage:
            where.append("stage=?")
            params.append(stage.upper())
        if before is not None:
            where.append("detected_at<=?")
            params.append(float(before))
        if actionable_only:
            where.append("UPPER(COALESCE(actionable_rank,'')) IN ('A','B')")
        if engine_version:
            where.append("engine_version=?")
            params.append(str(engine_version))
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        keys = [
            "id","ticker","stage","detected_at","price","score","vol_ratio_15s","vol_ratio_30s","change_60s_pct",
            "extension_pct","ema9","ema21","ema9_slope","vwap","above_vwap","quiet_break","evidence_json",
            "catalyst_headline","catalyst_category","catalyst_score","catalyst_url","chart_path",
            "change_3s_pct","change_5s_pct","change_10s_pct","change_15s_pct","change_30s_pct","accel_15s_pp",
            "dollar_volume_15s","dollar_volume_30s","trades_15s","trades_30s","breakout_level","breakout_window","signals_json",
            "quality_label","quality_score","actionable_rank","rejection_reasons_json","directional_efficiency","active_bucket_ratio","direction_reversals",
            "previous_close","gap_pct","day_volume","projected_session_volume","volume_rate_per_minute","float_shares","float_turnover","candidate_profile_json",
            "episode_id","reversal_phase","reversal_low","reversal_drawdown_pct","leg_context","ross_match","ross_score",
            "detection_timeframe_seconds","formation_start_at","formation_end_at","formation_low","formation_high","trigger_level","invalidation_level","halt_pressure_score","urgency","engine_version",
            "lifecycle_phase","shadow_mode","recipe_score","recipe_present_json","recipe_missing_json","trigger_distance_pct","base_extension_at_detection_pct","timeliness_label","precursor_finding_id",
            "engine_source","hybrid_sources_json","hybrid_score","hybrid_key","notification_reason","notification_delivered_at",
        ]
        sql = f"SELECT {','.join(keys)} FROM findings {clause} ORDER BY detected_at DESC LIMIT ?"
        params.append(limit)
        with self.lock:
            rows = self.db.execute(sql, params).fetchall()
        return [self._decode_finding(row, keys) for row in rows]

    def get_finding(self, finding_id: int) -> dict[str, Any] | None:
        keys = [
            "id","ticker","stage","detected_at","price","score","vol_ratio_15s","vol_ratio_30s","change_60s_pct",
            "extension_pct","ema9","ema21","ema9_slope","vwap","above_vwap","quiet_break","evidence_json",
            "catalyst_headline","catalyst_category","catalyst_score","catalyst_url","chart_path",
            "change_3s_pct","change_5s_pct","change_10s_pct","change_15s_pct","change_30s_pct","accel_15s_pp",
            "dollar_volume_15s","dollar_volume_30s","trades_15s","trades_30s","breakout_level","breakout_window","signals_json",
            "quality_label","quality_score","actionable_rank","rejection_reasons_json","directional_efficiency","active_bucket_ratio","direction_reversals",
            "previous_close","gap_pct","day_volume","projected_session_volume","volume_rate_per_minute","float_shares","float_turnover","candidate_profile_json",
            "episode_id","reversal_phase","reversal_low","reversal_drawdown_pct","leg_context","ross_match","ross_score",
            "detection_timeframe_seconds","formation_start_at","formation_end_at","formation_low","formation_high","trigger_level","invalidation_level","halt_pressure_score","urgency","engine_version",
            "lifecycle_phase","shadow_mode","recipe_score","recipe_present_json","recipe_missing_json","trigger_distance_pct","base_extension_at_detection_pct","timeliness_label","precursor_finding_id",
            "engine_source","hybrid_sources_json","hybrid_score","hybrid_key","notification_reason","notification_delivered_at",
        ]
        with self.lock:
            row = self.db.execute(f"SELECT {','.join(keys)} FROM findings WHERE id=?", (int(finding_id),)).fetchone()
        return self._decode_finding(row, keys) if row else None

    def notification_latency_stats(self, limit: int = 500) -> dict[str, Any]:
        """Recent provider-accepted latency measured from detection to delivery."""
        with self.lock:
            rows = self.db.execute(
                """
                SELECT d.channel, (d.event_at - f.detected_at) AS latency
                FROM notification_delivery_events d
                JOIN findings f ON f.id=d.finding_id
                WHERE d.status='provider_accepted' AND d.event_at>=f.detected_at
                ORDER BY d.event_at DESC LIMIT ?
                """,
                (max(1, min(5000, int(limit))),),
            ).fetchall()
        grouped: dict[str, list[float]] = {}
        for channel, latency in rows:
            grouped.setdefault(str(channel), []).append(float(latency or 0))
        result: dict[str, Any] = {}
        for channel, values in grouped.items():
            ordered = sorted(values)
            result[channel] = {
                "samples": len(ordered),
                "median_seconds": ordered[len(ordered)//2],
                "p95_seconds": ordered[min(len(ordered)-1, int(len(ordered)*0.95))],
                "max_seconds": ordered[-1],
            }
        return result

    def hybrid_precision_stats(self, threshold_pct: float = 5.0) -> dict[str, Any]:
        """Outcome precision for persisted hybrid episode keys.

        This becomes exact for the live merged stream because all Rust/Python
        findings in the same lifecycle episode share a hybrid_key. Episodes
        without a completed 15-minute outcome are excluded from the denominator.
        """
        with self.lock:
            rows = self.db.execute(
                """
                WITH ranked AS (
                    SELECT f.id, f.hybrid_key, f.detected_at,
                           ROW_NUMBER() OVER (PARTITION BY f.hybrid_key ORDER BY f.detected_at, f.id) AS rn
                    FROM findings f
                    WHERE f.hybrid_key IS NOT NULL
                ),
                sources AS (
                    SELECT hybrid_key,
                           GROUP_CONCAT(DISTINCT COALESCE(engine_source,'python')) AS source_mix
                    FROM findings
                    WHERE hybrid_key IS NOT NULL
                    GROUP BY hybrid_key
                )
                SELECT r.hybrid_key, s.source_mix, o.max_15m_pct
                FROM ranked r
                JOIN sources s ON s.hybrid_key=r.hybrid_key
                JOIN outcomes o ON o.finding_id=r.id
                WHERE r.rn=1 AND o.max_15m_pct IS NOT NULL
                """
            ).fetchall()
        completed = len(rows)
        successful = sum(1 for _, _, max_pct in rows if float(max_pct or 0) >= float(threshold_pct))
        source_counts = {"rust": 0, "python": 0, "both": 0}
        for _, sources, _ in rows:
            parts = set(str(sources or "").split(","))
            if {"rust", "python"}.issubset(parts):
                source_counts["both"] += 1
            elif "rust" in parts:
                source_counts["rust"] += 1
            elif "python" in parts:
                source_counts["python"] += 1
        return {
            "threshold_pct": float(threshold_pct),
            "completed_episodes": completed,
            "successful_episodes": successful,
            "precision": round(successful / completed, 6) if completed else None,
            "source_mix": source_counts,
        }

    def list_episodes(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the latest lifecycle row per ticker without truncating the source history.

        The previous implementation first loaded only 500 global findings. During a busy
        session a valid ticker could therefore disappear from Radar simply because 500
        newer findings existed for other symbols. Rank in SQLite first, then apply limit.
        """
        limit = max(1, min(500, int(limit)))
        keys = [
            "id","ticker","stage","detected_at","price","score","vol_ratio_15s","vol_ratio_30s","change_60s_pct",
            "extension_pct","ema9","ema21","ema9_slope","vwap","above_vwap","quiet_break","evidence_json",
            "catalyst_headline","catalyst_category","catalyst_score","catalyst_url","chart_path",
            "change_3s_pct","change_5s_pct","change_10s_pct","change_15s_pct","change_30s_pct","accel_15s_pp",
            "dollar_volume_15s","dollar_volume_30s","trades_15s","trades_30s","breakout_level","breakout_window","signals_json",
            "quality_label","quality_score","actionable_rank","rejection_reasons_json","directional_efficiency","active_bucket_ratio","direction_reversals",
            "previous_close","gap_pct","day_volume","projected_session_volume","volume_rate_per_minute","float_shares","float_turnover","candidate_profile_json",
            "episode_id","reversal_phase","reversal_low","reversal_drawdown_pct","leg_context","ross_match","ross_score",
            "detection_timeframe_seconds","formation_start_at","formation_end_at","formation_low","formation_high","trigger_level","invalidation_level","halt_pressure_score","urgency","engine_version",
            "lifecycle_phase","shadow_mode","recipe_score","recipe_present_json","recipe_missing_json","trigger_distance_pct","base_extension_at_detection_pct","timeliness_label","precursor_finding_id",
            "engine_source","hybrid_sources_json","hybrid_score","hybrid_key","notification_reason","notification_delivered_at",
        ]
        columns = ",".join(keys)
        sql = f"""
            SELECT {columns} FROM (
                SELECT {columns}, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY detected_at DESC, id DESC) AS rn
                FROM findings
            ) ranked
            WHERE rn=1
            ORDER BY detected_at DESC, id DESC
            LIMIT ?
        """
        with self.lock:
            rows = self.db.execute(sql, (limit,)).fetchall()
        return [self._decode_finding(row, keys) for row in rows]

    def latest_findings_by_ticker(self, tickers: list[str]) -> dict[str, dict[str, Any]]:
        if not tickers:
            return {}
        symbols = [x.upper() for x in tickers]
        marks = ",".join("?" for _ in symbols)
        sql = f"""
            SELECT f.id,f.ticker,f.stage,f.detected_at,f.price,f.score,f.signals_json
            FROM findings f
            JOIN (
                SELECT ticker, MAX(detected_at) AS max_time
                FROM findings
                WHERE ticker IN ({marks})
                GROUP BY ticker
            ) latest ON latest.ticker=f.ticker AND latest.max_time=f.detected_at
        """
        with self.lock:
            rows = self.db.execute(sql, symbols).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                signals = json.loads(row[6] or "[]")
            except Exception:
                signals = []
            out[row[1]] = {"id": row[0], "ticker": row[1], "stage": row[2], "detected_at": row[3], "price": row[4], "score": row[5], "signals": signals}
        return out

    def list_catalysts(self, limit: int = 100, ticker: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        params: list[Any] = []
        where = "WHERE verified=1"
        if ticker:
            where += " AND ticker=?"
            params.append(ticker.upper())
        params.append(limit)
        with self.lock:
            rows = self.db.execute(
                f"SELECT id,ticker,headline,category,score,url,source,published_at,verified,verification_method FROM catalysts {where} ORDER BY published_at DESC LIMIT ?",
                params,
            ).fetchall()
        keys = ["id","ticker","headline","category","score","url","source","published_at","verified","verification_method"]
        result = [dict(zip(keys, row)) for row in rows]
        for item in result:
            item["verified"] = bool(item["verified"])
        return result

    def get_notification_preferences(self) -> dict[str, Any]:
        with self.lock:
            row = self.db.execute("SELECT value_json FROM notification_preferences WHERE key='global'").fetchone()
        if not row:
            return normalize_notification_preferences(None)
        try:
            return normalize_notification_preferences(json.loads(row[0]))
        except Exception:
            return normalize_notification_preferences(None)

    def set_notification_preferences(self, value: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_notification_preferences(value)
        encoded = json.dumps(normalized, separators=(",", ":"))
        with self.lock:
            self.db.execute(
                "INSERT INTO notification_preferences(key,value_json,updated_at) VALUES('global',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                (encoded, int(time.time())),
            )
            self.db.commit()
        return normalized

    def save_market_status(self, ticker: str, status_code: str, status_message: str, reason_code: str, reason_message: str, event_at: int, is_halted: bool) -> int:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO market_status_events(ticker,status_code,status_message,reason_code,reason_message,event_at,is_halted) VALUES(?,?,?,?,?,?,?)",
                (ticker.upper(), status_code[:32], status_message[:200], reason_code[:32], reason_message[:500], int(event_at), int(is_halted)),
            )
            self.db.commit()
            return int(cur.lastrowid)

    def recent_market_status_events(self, limit: int = 100, ticker: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        params: list[Any] = []
        where = ""
        if ticker:
            where = "WHERE ticker=?"
            params.append(ticker.upper())
        params.append(limit)
        with self.lock:
            rows = self.db.execute(
                f"SELECT id,ticker,status_code,status_message,reason_code,reason_message,event_at,is_halted FROM market_status_events {where} ORDER BY event_at DESC LIMIT ?",
                params,
            ).fetchall()
        keys = ["id","ticker","status_code","status_message","reason_code","reason_message","event_at","is_halted"]
        out = []
        for row in rows:
            item = dict(zip(keys, row))
            item["is_halted"] = bool(item["is_halted"])
            out.append(item)
        return out

    def upsert_outcome(
        self,
        finding_id: int,
        max_1m_pct: float | None,
        max_5m_pct: float | None,
        max_15m_pct: float | None,
        max_session_pct: float | None,
        time_to_peak_seconds: float | None,
    ) -> None:
        with self.lock:
            self.db.execute(
                """
                INSERT INTO outcomes(finding_id,max_1m_pct,max_5m_pct,max_15m_pct,max_session_pct,time_to_peak_seconds,updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(finding_id) DO UPDATE SET
                    max_1m_pct=excluded.max_1m_pct,
                    max_5m_pct=excluded.max_5m_pct,
                    max_15m_pct=excluded.max_15m_pct,
                    max_session_pct=excluded.max_session_pct,
                    time_to_peak_seconds=excluded.time_to_peak_seconds,
                    updated_at=excluded.updated_at
                """,
                (int(finding_id), max_1m_pct, max_5m_pct, max_15m_pct, max_session_pct, time_to_peak_seconds, int(time.time())),
            )
            self.db.commit()

    def list_validation(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        with self.lock:
            rows = self.db.execute(
                """
                SELECT f.id,f.ticker,f.stage,f.detected_at,f.price,f.extension_pct,f.score,f.signals_json,
                       o.max_1m_pct,o.max_5m_pct,o.max_15m_pct,o.max_session_pct,o.time_to_peak_seconds,o.updated_at
                FROM findings f
                LEFT JOIN outcomes o ON o.finding_id=f.id
                ORDER BY f.detected_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        keys = [
            "id","ticker","stage","detected_at","price","move_at_detection_pct","score","signals_json",
            "max_1m_pct","max_5m_pct","max_15m_pct","max_session_pct","time_to_peak_seconds","updated_at",
        ]
        out=[]
        now = time.time()
        for row in rows:
            item=dict(zip(keys,row))
            try:
                item["signals"] = json.loads(item.pop("signals_json") or "[]")
            except Exception:
                item["signals"] = []
                item.pop("signals_json",None)
            age = max(0.0, now - float(item["detected_at"]))
            for field, maturity in (("max_1m_pct", 60), ("max_5m_pct", 300), ("max_15m_pct", 900)):
                if age < maturity:
                    item[field] = None
                elif item.get(field) is not None:
                    item[field] = max(0.0, float(item[field]))
            if item.get("max_session_pct") is not None:
                item["max_session_pct"] = max(0.0, float(item["max_session_pct"]))
            out.append(item)
        return out

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from app.config import settings
from app.db import Store
from app.dispatch import Dispatcher
from app.models import Bucket, Finding


def synthetic_buckets() -> list[Bucket]:
    now = time.time()
    start = now - 24 * settings.bucket_seconds
    rows = []
    px = 1.00
    for i in range(24):
        vol = 400 + (i % 3) * 60
        if i >= 20:
            vol = [900, 2100, 5400, 8800][i - 20]
            px *= [1.004, 1.009, 1.018, 1.028][i - 20]
        rows.append(Bucket(start + i * settings.bucket_seconds, px * 0.998, px * 1.003, px * 0.997, px, vol, 25 + i))
    return rows


async def main():
    store = Store(settings.data_dir / "state.db")
    dispatcher = Dispatcher(store)
    rows = synthetic_buckets()
    last = rows[-1]
    f = Finding(
        ticker="TEST", stage="EARLY", detected_at=last.start_ts + settings.bucket_seconds - 1,
        price=last.close, score=8, vol_ratio_15s=8.8, vol_ratio_30s=6.4, change_60s_pct=4.7,
        extension_pct=5.9, ema9=1.041, ema21=1.019, ema9_slope=0.006, vwap=1.014,
        above_vwap=True, quiet_break=True,
        evidence=["15s volume 8.8× baseline", "5s price acceleration", "3m resistance broken", "EMA9 slope rising", "price > session VWAP"],
        catalyst_headline="Synthetic smoke-test catalyst", catalyst_category="Major contract / order", catalyst_score=5,
        catalyst_url="https://example.com/smoke-test",
        change_3s_pct=.42, change_5s_pct=.78, change_10s_pct=1.12, change_15s_pct=1.66, change_30s_pct=2.42,
        accel_15s_pp=.44, dollar_volume_15s=14400, dollar_volume_30s=27100, trades_15s=28, trades_30s=49,
        breakout_level=1.03, breakout_window="3m", signals=["EARLY", "SURGE", "BREAKOUT", "CATALYST"],
    )
    await dispatcher.emit(f, rows[:-1], last)
    # Let async chart/email task finish.
    await asyncio.sleep(4)
    print("Smoke test emitted. Check ntfy, Resend inbox, /charts, and SQLite findings.")


if __name__ == "__main__":
    asyncio.run(main())

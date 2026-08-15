from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .config import settings
from .indicators import ema
from .models import Bucket, Finding

ET = ZoneInfo(settings.timezone)


def _ema_series(values: list[float], length: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (length + 1.0)
    out = [values[0]]
    for x in values[1:]:
        out.append(alpha * x + (1 - alpha) * out[-1])
    return out


def render_detection_chart(finding: Finding, buckets: list[Bucket], current: Bucket | None, out_dir: Path | None = None) -> str:
    rows = list(buckets[-80:])
    if current is not None:
        rows.append(Bucket(current.start_ts, current.open, current.high, current.low, current.close, current.volume, current.trades))
    if len(rows) < 2:
        raise ValueError("not enough buckets to render chart")

    closes = [b.close for b in rows]
    ema9 = _ema_series(closes, 9)
    ema21 = _ema_series(closes, 21)

    # VWAP series from the visible frozen window. The finding's session VWAP is separately annotated.
    pv = 0.0
    vv = 0.0
    vwaps: list[float] = []
    for b in rows:
        typical = (b.high + b.low + b.close) / 3.0
        pv += typical * b.volume
        vv += b.volume
        vwaps.append(pv / vv if vv > 0 else b.close)

    x = list(range(len(rows)))
    fig = plt.figure(figsize=(12, 7), dpi=140)
    gs = fig.add_gridspec(4, 1, height_ratios=[3, 3, 3, 2], hspace=0.05)
    ax = fig.add_subplot(gs[:3, 0])
    av = fig.add_subplot(gs[3, 0], sharex=ax)

    for i, b in enumerate(rows):
        up = b.close >= b.open
        body_bottom = min(b.open, b.close)
        body_h = max(abs(b.close - b.open), max(1e-6, b.close * 0.0002))
        alpha = 0.90 if up else 0.70
        ax.vlines(i, b.low, b.high, linewidth=1.0, alpha=0.75)
        ax.add_patch(Rectangle((i - 0.32, body_bottom), 0.64, body_h, alpha=alpha))
        av.bar(i, b.volume, width=0.70, alpha=alpha)

    ax.plot(x, ema9, linewidth=1.4, label="EMA 9")
    ax.plot(x, ema21, linewidth=1.4, label="EMA 21")
    ax.plot(x, vwaps, linewidth=1.2, linestyle="--", label="VWAP (visible window)")
    ax.axvline(len(rows) - 1, linewidth=1.4, linestyle=":", label="Scout detection")
    if finding.breakout_level is not None:
        ax.axhline(finding.breakout_level, linewidth=1.0, linestyle="--", alpha=0.7, label=f"{finding.breakout_window or 'range'} breakout")
    ax.legend(loc="upper left", ncols=5, fontsize=8)
    ax.grid(alpha=0.15)
    av.grid(alpha=0.12)

    times = [datetime.fromtimestamp(b.start_ts, ET).strftime("%H:%M:%S") for b in rows]
    step = max(1, len(rows) // 8)
    ticks = list(range(0, len(rows), step))
    av.set_xticks(ticks)
    av.set_xticklabels([times[i] for i in ticks], rotation=0, fontsize=8)
    plt.setp(ax.get_xticklabels(), visible=False)
    av.set_ylabel("Volume")

    catalyst = finding.catalyst_category or "not yet confirmed"
    signals = " · ".join(finding.signals or [finding.stage])
    velocity = " / ".join(
        part for part in [
            f"5s {finding.change_5s_pct:+.2f}%" if finding.change_5s_pct is not None else "",
            f"15s {finding.change_15s_pct:+.2f}%" if finding.change_15s_pct is not None else "",
            f"30s {finding.change_30s_pct:+.2f}%" if finding.change_30s_pct is not None else "",
        ] if part
    ) or f"60s {finding.change_60s_pct:+.1f}%"
    fig.suptitle(
        f"{finding.ticker} — {signals} — frozen at detection\n"
        f"${finding.price:.4f} | score {finding.score}/10 | {velocity} | 15s RVOL {finding.vol_ratio_15s:.1f}× | "
        f"ext {finding.extension_pct:+.1f}% | catalyst: {catalyst}",
        fontsize=11,
    )
    fig.text(0.01, 0.01, "Chart contains no candles after the finding timestamp.", fontsize=8)

    out_dir = out_dir or settings.chart_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(finding.detected_at, ET).strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{stamp}-{finding.ticker}-{finding.stage.lower()}.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return str(path)

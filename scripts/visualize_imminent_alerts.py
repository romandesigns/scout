#!/usr/bin/env python3
"""Render marked historical charts for Scout's 15-30 second alert evaluation."""
from __future__ import annotations

import argparse
import html
import json
from bisect import bisect_left, bisect_right
from pathlib import Path

import joblib
import matplotlib.pyplot as plt
import numpy as np

from scripts.imminent_move_scorer import load_jsonl, load_trades
from scripts.train_imminent_alert_gate import FEATURES, feature_vector, labeled_alerts


def _downsample(points: list[tuple[float, float]], maximum: int = 4000) -> list[tuple[float, float]]:
    if len(points) <= maximum:
        return points
    indices = np.linspace(0, len(points) - 1, maximum, dtype=int)
    return [points[index] for index in indices]


def main() -> int:
    parser = argparse.ArgumentParser(description="Create marked charts for historical imminent-alert verification")
    parser.add_argument("--findings", action="append", required=True, help="Repeat for replay shards")
    parser.add_argument("--cache-dir", default="data/replay-datasets/backtest")
    parser.add_argument("--model", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seconds-before", type=float, default=60.0)
    parser.add_argument("--seconds-after", type=float, default=60.0)
    args = parser.parse_args()

    replay_rows = []
    for findings_path in args.findings:
        replay_rows.extend(load_jsonl(Path(findings_path)))
    cache_dir = Path(args.cache_dir)
    alerts = labeled_alerts(replay_rows, cache_dir)

    artifact = joblib.load(args.model)
    if tuple(artifact.get("features") or ()) != FEATURES:
        raise SystemExit("Model feature contract mismatch")
    probabilities = artifact["model"].predict_proba(
        np.asarray([feature_vector(row) for row in alerts], dtype=float)
    )[:, 1]
    threshold = float(artifact["threshold"])

    output_dir = Path(args.output_dir)
    image_dir = output_dir / "charts"
    image_dir.mkdir(parents=True, exist_ok=True)
    trade_cache: dict[tuple[str, str], list[tuple[float, float]]] = {}
    cards = []
    summary = {"alerts": len(alerts), "passed": 0, "true": 0, "true_passed": 0}

    ordered = sorted(zip(alerts, probabilities), key=lambda item: (item[0]["date"], item[0]["detected_at"]))
    for number, (alert, probability) in enumerate(ordered, 1):
        ticker, session_date = alert["ticker"], alert["date"]
        key = (ticker, session_date)
        if key not in trade_cache:
            trade_cache[key] = load_trades(cache_dir / f"{ticker}-{session_date}-sip.ndjson")
        trades = trade_cache[key]
        timestamps = [point[0] for point in trades]
        detected_at = float(alert["detected_at"])
        start = bisect_left(timestamps, detected_at - args.seconds_before)
        end = bisect_right(timestamps, detected_at + args.seconds_after)
        points = _downsample(trades[start:end])
        relative = [timestamp - detected_at for timestamp, _ in points]
        prices = [price for _, price in points]
        passed = bool(probability >= threshold)
        is_true = bool(alert["label"])
        summary["passed"] += int(passed)
        summary["true"] += int(is_true)
        summary["true_passed"] += int(passed and is_true)

        fig, axis = plt.subplots(figsize=(11, 5.4))
        if points:
            axis.plot(relative, prices, color="#2563eb", linewidth=1.25, label="Trade price")
        axis.axvline(0, color="#dc2626", linewidth=2, label="Scout detection")
        axis.axvspan(15, 30, color="#f59e0b", alpha=0.2, label="Required +15s to +30s completion window")
        if is_true:
            base_x = float(alert["target_base_at"]) - detected_at
            completion_x = float(alert["target_completion_at"]) - detected_at
            axis.scatter([base_x], [alert["target_base_price"]], color="#7c3aed", s=65, zorder=5, label="Move base")
            axis.scatter([completion_x], [alert["target_completion_price"]], color="#16a34a", marker="*", s=180, zorder=6, label="+2% completion")
            axis.plot(
                [base_x, completion_x], [alert["target_base_price"], alert["target_completion_price"]],
                color="#16a34a", linewidth=2.2, linestyle="--",
            )
        verdict = "PASS" if passed else "REJECT"
        outcome = "TRUE 15-30s ALERT" if is_true else "FALSE ALERT"
        axis.set_title(
            f"{ticker} · {session_date} · {alert.get('stage')} · {outcome} · gate {verdict}\n"
            f"probability {probability:.3f} / threshold {threshold:.3f}"
        )
        axis.set_xlabel("Seconds relative to Scout detection")
        axis.set_ylabel("Price ($)")
        axis.grid(alpha=0.22)
        axis.legend(loc="best", fontsize=8)
        fig.tight_layout()
        filename = f"{number:03d}-{ticker}-{int(detected_at)}.png"
        fig.savefig(image_dir / filename, dpi=145)
        plt.close(fig)

        border = "#16a34a" if is_true else ("#dc2626" if passed else "#94a3b8")
        cards.append(
            f'<article style="border:3px solid {border};padding:12px;margin:18px 0;border-radius:10px">'
            f'<h2>{html.escape(ticker)} — {html.escape(outcome)} — gate {verdict}</h2>'
            f'<p>{html.escape(session_date)} · {html.escape(str(alert.get("stage")))} · '
            f'probability {probability:.3f} · threshold {threshold:.3f}</p>'
            f'<img src="charts/{html.escape(filename)}" style="max-width:100%;height:auto" loading="lazy">'
            '</article>'
        )

    rejected_false = summary["alerts"] - summary["true"] - (summary["passed"] - summary["true_passed"])
    document = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Scout marked detection verification</title></head>
<body style="font-family:system-ui;max-width:1200px;margin:24px auto;padding:0 16px">
<h1>Scout 15–30 second marked detection verification</h1>
<p><strong>{summary['alerts']}</strong> production-contract alerts ·
<strong>{summary['passed']}</strong> gate passes · <strong>{summary['true']}</strong> true alerts ·
<strong>{rejected_false}</strong> false alerts rejected.</p>
<p>Red line: detection. Orange band: required completion window. Purple dot and green star: measured qualifying move.</p>
{''.join(cards)}
</body></html>"""
    (output_dir / "index.html").write_text(document, encoding="utf-8")
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({**summary, "report": str((output_dir / 'index.html').resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

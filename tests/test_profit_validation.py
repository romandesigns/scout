from __future__ import annotations

import time

from app.db import Store
from app.models import Finding


def finding(index: int = 0) -> Finding:
    return Finding(
        ticker=f"T{index}", stage="BREAKOUT", detected_at=time.time(), price=2.0,
        score=10, vol_ratio_15s=8, vol_ratio_30s=6, change_60s_pct=1,
        extension_pct=.5, ema9=2, ema21=1.99, ema9_slope=.01, vwap=1.98,
        above_vwap=True, quiet_break=True, evidence=[], quality_label="CLEAN",
        quality_score=100, actionable_rank="A", episode_id=index,
    )


def add_outcome(store: Store, index: int, *, won: bool) -> None:
    item = finding(index)
    finding_id = store.save_finding(item)
    client_id = f"edge-{index}"
    store.create_paper_trade({
        "episode_key": f"edge:{index}", "finding_id": finding_id,
        "ticker": item.ticker, "client_order_id": client_id, "status": "closed",
        "quantity": 10, "signal_price": 2, "entry_price": 2,
        "stop_price": 1.9, "target_price": 2.3, "exit_price": 2.3 if won else 1.9,
        "submitted_at": int(time.time()), "closed_at": int(time.time()),
        "exit_reason": "target" if won else "stop", "realized_pl": 3 if won else -1,
    })


def test_edge_stays_evaluating_without_minimum_sample(tmp_path):
    store = Store(tmp_path / "edge.db")
    try:
        for index in range(20):
            add_outcome(store, index, won=index < 10)
        result = store.paper_edge_validation(finding(99))
        assert result["samples"] == 20
        assert result["status"] == "EVALUATING"
        assert result["validated"] is False
    finally:
        store.close()


def test_edge_requires_positive_expectancy_and_confident_win_rate(tmp_path):
    store = Store(tmp_path / "edge.db")
    try:
        for index in range(100):
            add_outcome(store, index, won=index < 50)
        result = store.paper_edge_validation(finding(999))
        assert result["samples"] == 100
        assert result["average_r"] == 1.0
        assert result["wilson_lower"] > result["break_even_rate"]
        assert result["status"] == "PROFIT_VALIDATED"
    finally:
        store.close()

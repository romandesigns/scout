from scripts.e2e_validate import independent_bucket_metrics, compare_metric


def _rows():
    return [
        {"start_ts": 0, "close": 10.0, "volume": 100, "trades": 10},
        {"start_ts": 15, "close": 10.1, "volume": 200, "trades": 20},
        {"start_ts": 30, "close": 10.3, "volume": 300, "trades": 30},
        {"start_ts": 45, "close": 10.6, "volume": 400, "trades": 40},
        {"start_ts": 60, "close": 11.0, "volume": 500, "trades": 50},
    ]


def test_independent_bucket_metrics_recomputes_without_market_internals():
    metrics = independent_bucket_metrics(_rows(), 60, 15)
    assert round(metrics["change_15s_pct"], 6) == round((11.0 / 10.6 - 1) * 100, 6)
    assert round(metrics["change_30s_pct"], 6) == round((11.0 / 10.3 - 1) * 100, 6)
    assert round(metrics["change_60s_pct"], 6) == 10.0
    assert metrics["volume_30s"] == 900
    assert metrics["trades_30s"] == 90


def test_compare_metric_uses_tolerance():
    assert compare_metric("x", 5.0, 5.05, abs_tolerance=.1)["status"] == "PASS"
    assert compare_metric("x", 5.0, 5.2, abs_tolerance=.1)["status"] == "WARN"

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app import live_replay
from app.models import Finding


def _fake_settings(tmp_path: Path, feed: str = "sip", key: str = "test-key", secret: str = "test-secret"):
    return SimpleNamespace(alpaca_key=key, alpaca_secret=secret, alpaca_feed=feed, data_dir=tmp_path)


def _trade_row(ts: datetime, price: float, size: float = 100.0) -> dict:
    return {"t": ts.isoformat().replace("+00:00", "Z"), "p": price, "s": size, "x": "V", "c": []}


def test_run_live_detector_rejects_an_overlong_window(tmp_path, monkeypatch):
    monkeypatch.setattr(live_replay, "settings", _fake_settings(tmp_path))
    try:
        live_replay.run_live_detector("TEST", 0.0, live_replay.MAX_LIVE_REPLAY_SECONDS + 1, output_root=tmp_path)
    except ValueError as exc:
        assert "cannot exceed" in str(exc)
    else:
        raise AssertionError("an overlong replay window was accepted")


def test_run_live_detector_requires_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(live_replay, "settings", _fake_settings(tmp_path, key="", secret=""))
    try:
        live_replay.run_live_detector("TEST", 0.0, 60.0, output_root=tmp_path)
    except ValueError as exc:
        assert "ALPACA_API_KEY" in str(exc)
    else:
        raise AssertionError("missing credentials were accepted")


def test_run_live_detector_reports_no_trades(tmp_path, monkeypatch):
    monkeypatch.setattr(live_replay, "settings", _fake_settings(tmp_path))
    monkeypatch.setattr(live_replay, "_get_trades", lambda ticker, start, end, feed: [])
    result = live_replay.run_live_detector("TEST", 0.0, 60.0, output_root=tmp_path)
    assert result == {"status": "NO_TRADES", "findings": [], "processed_events": 0}


def test_run_live_detector_feeds_real_trades_through_the_actual_detector(tmp_path, monkeypatch):
    monkeypatch.setattr(live_replay, "settings", _fake_settings(tmp_path))
    base = datetime(2026, 8, 12, 14, 30, tzinfo=timezone.utc)
    trades = [_trade_row(base.replace(second=index), 10.0 + index * 0.001) for index in range(30)]
    captured = {}

    def fake_get_trades(ticker, start, end, feed):
        captured["args"] = (ticker, start, end, feed)
        return trades

    monkeypatch.setattr(live_replay, "_get_trades", fake_get_trades)
    result = live_replay.run_live_detector("test", base.timestamp(), base.timestamp() + 60, output_root=tmp_path)

    assert captured["args"][0] == "TEST"  # normalized to uppercase before fetching
    assert result["status"] == "OK"
    assert result["processed_events"] == len(trades)
    assert isinstance(result["findings"], list)
    for row in result["findings"]:
        assert "id" in row and row["id"] is not None
        assert row["candidate_profile"] == {} or isinstance(row["candidate_profile"], dict)
    # The temporary NDJSON dataset must not be left behind after a successful replay.
    assert not any(tmp_path.glob("live-*.ndjson"))


def test_finding_from_row_reconstructs_a_real_finding_from_a_replay_row():
    original = Finding(
        ticker="TEST", stage="BREAKOUT", detected_at=1.0, price=2.0, score=9,
        vol_ratio_15s=5.0, vol_ratio_30s=4.0, change_60s_pct=1.5, extension_pct=0.5,
        ema9=2.0, ema21=1.9, ema9_slope=0.1, vwap=1.95, above_vwap=True, quiet_break=False,
        evidence=["test evidence"], actionable_rank="A",
    )
    row = asdict(original)
    row["mode"] = "SIMULATION"
    row["id"] = 1

    rebuilt = live_replay.finding_from_row(row)

    assert isinstance(rebuilt, Finding)
    assert rebuilt.ticker == "TEST"
    assert rebuilt.stage == "BREAKOUT"
    assert rebuilt.vol_ratio_15s == 5.0
    assert rebuilt.evidence == ["test evidence"]

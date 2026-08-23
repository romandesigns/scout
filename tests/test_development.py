import base64
import json
from pathlib import Path
from types import SimpleNamespace

from app.db import Store
from app import development


class FakeMarket:
    def historical_snapshot_sync(self, ticker: str, center: float, bucket_seconds: int,
                                 range_start_ts=None, range_end_ts=None):
        rows = []
        for index in range(-20, 21):
            price = 10.0 + max(0, index) * .03
            rows.append({
                "start_ts": center + index * bucket_seconds,
                "open": price - .01, "high": price + .03, "low": price - .02,
                "close": price, "volume": 1000 + index * 5, "trades": 12,
            })
        return {"buckets": rows, "source": "test-alpaca"}


def test_development_evaluation_persists_chart_and_outcomes(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "state.db")
    detected_at = 1_700_000_000.0
    monkeypatch.setattr(development, "settings", SimpleNamespace(chart_dir=tmp_path / "charts"))
    monkeypatch.setattr(store, "list_findings", lambda *args, **kwargs: [{
        "id": 17, "ticker": "TEST", "detected_at": detected_at, "price": 10.0,
        "stage": "CONFIRMED", "actionable_rank": "A", "quality_label": "CLEAN", "score": 9,
        "trigger_level": 10.0, "invalidation_level": 9.9, "detection_timeframe_seconds": 60,
        "catalyst_headline": "Verified test catalyst", "notification_delivered_at": detected_at + 60,
        "candidate_profile": {"imminent_move_gate": {"would_pass": True, "probability": .72}},
    }])
    result = development.evaluate_ticker(store, FakeMarket(), "test", timeframe_seconds=60)
    assert result["status"] == "complete"
    assert result["finding_id"] == 17
    assert Path(result["chart_path"]).exists()
    assert result["chart_url"].startswith("/charts/dev-")
    assert result["metrics"]["source"] == "test-alpaca"
    assert result["metrics"]["max_favorable_r"] > 1
    assert result["metrics"]["notifications_marked"] == 1
    assert result["metrics"]["detections_marked"] == 1
    assert result["metrics"]["gate_passes_marked"] == 1
    assert result["metrics"]["detection_markers"][0]["gate_status"] == "PASS"
    assert result["metrics"]["notification_markers"][0]["finding_id"] == 17
    saved = store.list_development_evaluations()
    assert saved[0]["metrics"]["formation"]["rank"] == "A"
    store.close()


def test_development_evaluation_rejects_unsupported_timeframe(tmp_path: Path):
    store = Store(tmp_path / "state.db")
    try:
        development.evaluate_ticker(store, FakeMarket(), "TEST", 1_700_000_000, 15, False)
    except ValueError as exc:
        assert "30, 60, or 300" in str(exc)
    else:
        raise AssertionError("unsupported timeframe was accepted")
    store.close()


def test_development_evaluation_uses_selected_inspection_range(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "state.db")
    detected_at = 1_700_000_000.0
    range_start = detected_at - 600
    range_end = detected_at + 1_200
    market = FakeMarket()
    calls = []
    original_snapshot = market.historical_snapshot_sync

    def capture_snapshot(ticker, center, bucket_seconds, range_start_ts=None, range_end_ts=None):
        calls.append((ticker, center, bucket_seconds, range_start_ts, range_end_ts))
        return original_snapshot(ticker, center, bucket_seconds, range_start_ts, range_end_ts)

    market.historical_snapshot_sync = capture_snapshot
    monkeypatch.setattr(development, "settings", SimpleNamespace(chart_dir=tmp_path / "charts"))
    monkeypatch.setattr(store, "list_findings", lambda *args, **kwargs: [
        {"id": 18, "ticker": "TEST", "detected_at": range_end + 60, "price": 10.0,
         "notification_delivered_at": range_end + 60},
        {"id": 17, "ticker": "TEST", "detected_at": detected_at, "price": 10.0,
         "notification_delivered_at": detected_at + 60, "stage": "CONFIRMED"},
    ])

    result = development.evaluate_ticker(
        store, market, "TEST", timeframe_seconds=60,
        inspection_start=range_start, inspection_end=range_end,
    )

    assert result["finding_id"] == 17
    assert calls == [("TEST", detected_at, 60, range_start, range_end)]
    assert result["metrics"]["inspection_start"] == range_start
    assert result["metrics"]["inspection_end"] == range_end
    assert result["metrics"]["notifications_marked"] == 1
    assert result["metrics"]["notification_markers"][0]["stage"] == "CONFIRMED"
    store.close()


def test_selected_range_without_detection_still_renders_chart(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "state.db")
    center = 1_700_000_000.0
    monkeypatch.setattr(development, "settings", SimpleNamespace(chart_dir=tmp_path / "charts"))
    monkeypatch.setattr(store, "list_findings", lambda *args, **kwargs: [])

    result = development.evaluate_ticker(
        store, FakeMarket(), "NONE", timeframe_seconds=30,
        inspection_start=center - 300, inspection_end=center + 300,
    )

    assert result["status"] == "complete"
    assert result["finding_id"] is None
    assert result["metrics"]["detection_match"] is False
    assert result["metrics"]["detections_marked"] == 0
    assert Path(result["chart_path"]).exists()
    store.close()


def test_evaluate_ticker_uses_the_live_detector_when_requested(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "state.db")
    range_start = 1_700_000_000.0
    range_end = range_start + 600
    monkeypatch.setattr(development, "settings", SimpleNamespace(chart_dir=tmp_path / "charts"))
    monkeypatch.setattr(store, "list_findings", lambda *args, **kwargs: [])  # nothing stored for this ticker
    live_finding = {
        "id": None, "finding_id": 1, "ticker": "TEST", "stage": "BREAKOUT",
        "detected_at": range_start + 60, "price": 10.5, "score": 9,
        "actionable_rank": "A", "quality_label": "CLEAN", "candidate_profile": {},
        "vol_ratio_15s": 5.0, "vol_ratio_30s": 4.0, "change_60s_pct": 1.0, "extension_pct": 0.5,
        "ema9": 10.4, "ema21": 10.2, "ema9_slope": 0.1, "vwap": 10.3,
        "above_vwap": True, "quiet_break": False, "evidence": [],
    }
    monkeypatch.setattr(development, "run_live_detector", lambda *args, **kwargs: {
        "status": "OK", "findings": [dict(live_finding)], "processed_events": 42,
    })
    monkeypatch.setattr(store, "paper_edge_validation", lambda finding: {"validated": True, "sample_size": 40})

    result = development.evaluate_ticker(
        store, FakeMarket(), "TEST", timeframe_seconds=60, use_latest_finding=False,
        inspection_start=range_start, inspection_end=range_end, use_live_detector=True,
    )

    assert result["status"] == "complete"
    assert result["metrics"]["use_live_detector"] is True
    assert result["metrics"]["live_replay"] == {"status": "OK", "processed_events": 42, "findings_count": 1, "engine": "python"}
    assert result["metrics"]["detections_marked"] == 1
    marker = result["metrics"]["detection_markers"][0]
    assert marker["stage"] == "BREAKOUT"
    assert marker["tier"] in {1, 2, 3}
    store.close()


def test_evaluate_ticker_requires_an_inspection_range_for_live_detector(tmp_path: Path):
    store = Store(tmp_path / "state.db")
    try:
        development.evaluate_ticker(store, FakeMarket(), "TEST", timeframe_seconds=60, use_live_detector=True)
    except ValueError as exc:
        assert "inspection start and end" in str(exc)
    else:
        raise AssertionError("live detector replay without an inspection range was accepted")
    store.close()


def test_annotation_artifact_preserves_png_and_notes(tmp_path: Path):
    png = b"\x89PNG\r\n\x1a\n" + b"annotated-chart"
    data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")

    context = {"id": 42, "ticker": "S.DOT", "metrics": {"momentum_catch_rate_pct": 50.0}}
    result = development.save_annotation_artifact(42, "s.dot", data_url, "Review this breakout", tmp_path, context)

    image_path = Path(result["workspace_path"])
    assert image_path.read_bytes() == png
    assert image_path.parent.name == "annotations"
    review = json.loads(Path(result["review_path"]).read_text(encoding="utf-8"))
    assert review["notes"] == "Review this breakout"
    assert review["evaluation"] == context
    assert result["workspace_path"] in result["share_prompt"]
    assert result["review_path"] in result["share_prompt"]
    assert result["chart_url"].startswith("/charts/annotations/dev-annotation-42-S.DOT-")


def test_annotation_artifact_rejects_non_png(tmp_path: Path):
    invalid = "data:image/png;base64," + base64.b64encode(b"not a png").decode("ascii")
    try:
        development.save_annotation_artifact(1, "TEST", invalid, out_dir=tmp_path)
    except ValueError as exc:
        assert "not a PNG" in str(exc)
    else:
        raise AssertionError("non-PNG annotation was accepted")

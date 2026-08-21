from __future__ import annotations

import dataclasses
import asyncio
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.db import Store
from app.models import Finding
from app.trader import PaperTrader, _price_text, _round_order_price


def confirmed(**overrides) -> Finding:
    base = dict(
        ticker="TEST", stage="BREAKOUT",
        detected_at=datetime(2026, 8, 21, 10, 0, tzinfo=ZoneInfo("America/New_York")).timestamp(),
        price=2.0, score=10, vol_ratio_15s=8, vol_ratio_30s=6,
        change_60s_pct=2, extension_pct=1, ema9=2.0, ema21=1.99,
        ema9_slope=.01, vwap=1.98, above_vwap=True, quiet_break=True,
        evidence=["clean confirmation"], quality_label="CLEAN", actionable_rank="A",
        invalidation_level=1.96, trigger_level=1.99, episode_id=7,
        hybrid_key="TEST:2026-08-21:7",
    )
    base.update(overrides)
    return Finding(**base)


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "trader.db")
    yield value
    value.close()


def trader_settings(**overrides):
    value = dataclasses.replace(
        settings, alpaca_key="paper-key", alpaca_secret="paper-secret",
        alpaca_trading_base="https://paper-api.alpaca.markets",
    )
    return patch("app.trader.settings", dataclasses.replace(value, **overrides))


def test_refuses_non_paper_endpoint(store):
    with trader_settings(alpaca_trading_base="https://api.alpaca.markets"):
        trader = PaperTrader(store)
        assert not trader.paper_safe
        assert not trader.configured
        with pytest.raises(ValueError, match="paper"):
            trader.update_settings({"enabled": True})


def test_default_is_disabled_with_three_to_one_reward(store):
    with trader_settings():
        trader = PaperTrader(store)
        value = trader.status()
    assert value["enabled"] is False
    assert value["risk_reward"] == 3.0
    assert value["max_positions"] == 100
    assert value["mode"] == "paper"


def test_evaluation_capacity_accepts_up_to_one_hundred_positions(store):
    value = store.set_trader_settings({"max_positions": 100})
    assert value["max_positions"] == 100
    assert store.set_trader_settings({"max_positions": 101})["max_positions"] == 100


def test_enable_validates_active_paper_account(store):
    with trader_settings():
        trader = PaperTrader(store)
        with patch.object(trader, "_request", return_value={"status": "ACTIVE", "trading_blocked": False}) as request:
            value = trader.update_settings({"enabled": True})
    request.assert_called_once_with("GET", "/v2/account")
    assert value["enabled"] is True


def test_confirmed_signal_submits_three_r_bracket_once(store):
    with trader_settings():
        trader = PaperTrader(store)
        cfg = store.set_trader_settings({"enabled": True, "risk_reward": 3})
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET":
                return {"status": "active", "tradable": True}
            return {"id": "paper-order-1", "status": "accepted"}

        trader._request = fake_request
        finding = confirmed()
        trader._submit_confirmed(42, finding, cfg)
        trader._submit_confirmed(42, finding, cfg)

    posts = [call for call in calls if call[0] == "POST"]
    assert len(posts) == 1
    payload = posts[0][2]["json"]
    assert payload["order_class"] == "bracket"
    assert payload["stop_loss"]["stop_price"] == "1.96"
    assert payload["take_profit"]["limit_price"] == "2.12"
    assert payload["qty"] == "50"
    rows = store.list_paper_trades()
    assert len(rows) == 1
    assert rows[0]["status"] == "accepted"


def test_order_prices_follow_alpaca_tick_rules_and_stop_buffer(store):
    assert _round_order_price(3.1625, upward=False) == 3.16
    assert _round_order_price(3.2194, upward=True) == 3.22
    assert _price_text(3.22) == "3.22"
    assert _round_order_price(0.93219, upward=False) == 0.9321
    assert _round_order_price(0.95781, upward=True) == 0.9579
    assert _price_text(0.9321) == "0.9321"

    with trader_settings():
        trader = PaperTrader(store)
        cfg = store.set_trader_settings({"enabled": True, "risk_reward": 3})
        with patch.object(trader, "_request", side_effect=[
            {"status": "active", "tradable": True},
            {"id": "paper-order-2", "status": "accepted"},
        ]) as request:
            trader._submit_confirmed(43, confirmed(price=2.0, invalidation_level=1.995, hybrid_key="BUFFER:1"), cfg)
    payload = request.call_args_list[-1].kwargs["json"]
    assert payload["stop_loss"]["stop_price"] == "1.99"
    assert payload["take_profit"]["limit_price"] == "2.03"


def test_alpaca_rejection_body_is_persisted(store):
    with trader_settings():
        trader = PaperTrader(store)
        cfg = store.set_trader_settings({"enabled": True})
        with patch.object(trader, "_request", side_effect=[
            {"status": "active", "tradable": True},
            RuntimeError('Alpaca 422: {"code":42210000,"message":"invalid stop_price"}'),
        ]):
            with pytest.raises(RuntimeError, match="invalid stop_price"):
                trader._submit_confirmed(44, confirmed(hybrid_key="REJECT:1"), cfg)
    row = store.list_paper_trades()[0]
    assert row["status"] == "submit_failed"
    assert row["exit_reason"] == "alpaca_rejected"


def test_only_clean_a_rank_confirmations_are_eligible(store):
    with trader_settings():
        trader = PaperTrader(store)
        store.set_trader_settings({"enabled": True})
        with patch.object(trader, "_submit_confirmed") as submit:
            asyncio.run(trader.on_finding(1, confirmed(stage="EARLY")))
            asyncio.run(trader.on_finding(2, confirmed(quality_label="CHOPPY")))
            asyncio.run(trader.on_finding(3, confirmed(actionable_rank="B")))
        submit.assert_not_called()

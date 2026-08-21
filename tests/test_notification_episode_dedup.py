from app.dispatch import Dispatcher
from app.models import Finding
from app.preferences import DEFAULT_NOTIFICATION_PREFERENCES


def finding(stage: str, episode: int = 4) -> Finding:
    return Finding(
        ticker="TEST", stage=stage, detected_at=1_800_000_000, price=2.0, score=9,
        vol_ratio_15s=8, vol_ratio_30s=6, change_60s_pct=2, extension_pct=1,
        ema9=2.0, ema21=1.99, ema9_slope=.01, vwap=1.98, above_vwap=True,
        quiet_break=True, evidence=["orderly participation"], quality_label="CLEAN",
        episode_id=episode, hybrid_key=f"TEST:2026-08-21:{episode}",
    )


def test_only_one_setup_and_confirmation_per_channel_episode():
    dispatcher = Dispatcher(store=None)  # claim logic does not access the store
    prefs = DEFAULT_NOTIFICATION_PREFERENCES

    assert dispatcher._claim_episode_phase("ntfy", finding("EARLY"), prefs)
    assert not dispatcher._claim_episode_phase("ntfy", finding("EARLY"), prefs)
    assert dispatcher._claim_episode_phase("ntfy", finding("BREAKOUT"), prefs)
    assert not dispatcher._claim_episode_phase("ntfy", finding("IGNITION"), prefs)
    assert not dispatcher._claim_episode_phase("ntfy", finding("SURGE"), prefs)

    # A different channel and a new episode each receive their own two decisions.
    assert dispatcher._claim_episode_phase("email", finding("EARLY"), prefs)
    assert dispatcher._claim_episode_phase("ntfy", finding("EARLY", episode=5), prefs)


def test_special_events_are_not_episode_suppressed():
    dispatcher = Dispatcher(store=None)
    prefs = DEFAULT_NOTIFICATION_PREFERENCES
    assert dispatcher._claim_episode_phase("ntfy", finding("HALT"), prefs)
    assert dispatcher._claim_episode_phase("ntfy", finding("HALT"), prefs)

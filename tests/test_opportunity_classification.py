import time

from app.db import Store
from app.models import Finding
from app.opportunity import can_notify_opportunity, opportunity_class


def finding(**overrides):
    values = dict(
        ticker="TEST", stage="EARLY", detected_at=time.time(), price=1.0, score=8,
        vol_ratio_15s=4, vol_ratio_30s=3, change_60s_pct=1, extension_pct=1,
        ema9=1, ema21=.99, ema9_slope=.01, vwap=.98, above_vwap=True,
        quiet_break=True, evidence=[], quality_label="CLEAN", actionable_rank="A",
    )
    values.update(overrides)
    return Finding(**values)


def test_first_secondary_and_late_contract():
    assert opportunity_class(finding()) == "FIRST_MOVE"
    assert opportunity_class(finding(leg_context="CONSOLIDATION_RELEASE")) == "SECONDARY_ENTRY"
    late = finding(timeliness_label="LATE", extension_pct=3)
    assert opportunity_class(late) == "LATE_INFORMATION_ONLY"
    assert not can_notify_opportunity(late)


def test_provider_acceptance_is_persisted_on_finding(tmp_path):
    store = Store(tmp_path / "state.db")
    try:
        item = finding()
        item.candidate_profile["opportunity_class"] = opportunity_class(item)
        finding_id = store.save_finding(item)
        store.record_delivery(finding_id, "ntfy", "provider_accepted")
        saved = store.get_finding(finding_id)
        assert saved["notification_delivered_at"] is not None
        assert saved["opportunity_class"] == "FIRST_MOVE"
    finally:
        store.close()

from scripts.live_full_day_scorer import first_per_episode_phase, notification_eligible, score_recall


def finding(ts=100, **changes):
    row = {"id": ts, "ticker": "TEST", "stage": "BREAKOUT", "detected_at": ts,
           "price": 1.0, "actionable_rank": "A", "quality_label": "CLEAN", "shadow_mode": False,
           "hybrid_key": "TEST:2026-08-26:0", "candidate_profile": {"edge_validation": {"validated": True}}}
    row.update(changes)
    return row


def test_notification_eligible_uses_production_contract():
    assert notification_eligible(finding())
    assert not notification_eligible(finding(actionable_rank="B"))
    assert not notification_eligible(finding(shadow_mode=True))


def test_episode_dedup_keeps_first_setup_and_confirmation():
    rows = [finding(102, stage="SURGE"), finding(100, stage="EARLY"), finding(101, stage="BREAKOUT")]
    assert [(r["stage"], r["detected_at"]) for r in first_per_episode_phase(rows)] == [("EARLY", 100), ("BREAKOUT", 101)]


def test_recall_separates_awareness_eligibility_and_delivery():
    rows = [finding(90, actionable_rank="B"), finding(95, notification_delivered_at=96)]
    report = score_recall(
        [{"ticker": "TEST", "is_mover": True, "max_pct": 10, "crossings": {"5": {"at": 100}}}],
        {"TEST": rows},
    )["by_threshold"]["5"]
    assert report["seen_before_cross_pct"] == 100.0
    assert report["notification_eligible_before_cross_pct"] == 100.0
    assert report["delivered_before_cross_pct"] == 100.0

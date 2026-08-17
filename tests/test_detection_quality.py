from scripts.detection_quality import actionable, classification, coverage, forward_metrics, select_findings


def _rows(t=1000.0, price=10.0, seconds=1200):
    rows=[]
    for i in range(0, seconds//15 + 1):
        ts=t+i*15
        close=price*(1+i*0.001)
        rows.append({"start_ts":ts,"open":close,"high":close*1.001,"low":close*0.999,"close":close,"volume":100,"trades":1})
    return rows


def test_actionable_is_strict_ab_only():
    assert actionable({"actionable_rank":"A","notification_reason":"x"})
    assert actionable({"actionable_rank":"B"})
    assert not actionable({"actionable_rank":"C","notification_reason":"Python specialist intelligence"})
    assert not actionable({"quality_label":"DEVELOPING","notification_reason":"x"})


def test_forward_metrics_full_horizons():
    m=forward_metrics(_rows(),1000.0,10.0)
    assert round(m["return_30s_pct"],3)==0.2
    assert round(m["return_300s_pct"],3)==2.0
    assert round(m["return_900s_pct"],3)==6.0
    assert m["mfe_300s_pct"] > m["return_300s_pct"]
    assert coverage(m)=="FINAL_15M"


def test_missing_5m_is_unmatured_and_unclassified():
    m=forward_metrics(_rows(seconds=120),1000.0,10.0)
    assert coverage(m)=="UNMATURED"
    assert classification(m)=="UNMATURED"


def test_complete_5m_without_15m_is_provisional():
    m=forward_metrics(_rows(seconds=360),1000.0,10.0)
    assert coverage(m)=="PROVISIONAL_5M"
    assert classification(m).startswith("PROVISIONAL_")


def test_final_early_label_requires_maturity():
    m={
        "return_30s_pct":0.5,"return_120s_pct":1.0,"return_300s_pct":3.1,"return_900s_pct":4.2,
        "mfe_300s_pct":4.0,"mae_300s_pct":-0.5,"mfe_900s_pct":5.0,"mae_900s_pct":-0.7,
    }
    assert coverage(m)=="FINAL_15M"
    assert classification(m)=="EARLY"


def test_select_findings_keeps_mature_actionable_and_reports_exclusions():
    now = 10_000.0
    findings = [
        {"id": 1, "ticker": "GOOD", "actionable_rank": "A", "detected_at": now - 600, "price": 5.0},
        {"id": 2, "ticker": "FRESH", "actionable_rank": "B", "detected_at": now - 60, "price": 4.0},
        {"id": 3, "ticker": "WATCH", "actionable_rank": "C", "detected_at": now - 600, "price": 3.0},
        {"id": 4, "ticker": "BADPX", "actionable_rank": "A", "detected_at": now - 600, "price": 0},
    ]
    selected, excluded = select_findings(
        findings, now=now, min_age_seconds=300, include_developing=False
    )
    assert [(cohort, f["ticker"]) for cohort, f in selected] == [("ACTIONABLE", "GOOD")]
    assert excluded["too_young"] == 1
    assert excluded["not_in_requested_cohort"] == 1
    assert excluded["invalid_price"] == 1


def test_synthetic_promoted_early_produces_nonzero_quality_sample():
    detected_at = 1000.0
    finding = {
        "id": 99, "ticker": "EARLY", "stage": "EARLY", "actionable_rank": "B",
        "quality_label": "CLEAN", "detected_at": detected_at, "price": 10.0,
    }
    selected, excluded = select_findings(
        [finding], now=detected_at + 1000, min_age_seconds=300, include_developing=False
    )
    assert len(selected) == 1
    metrics = forward_metrics(_rows(t=detected_at, price=10.0, seconds=1200), detected_at, 10.0)
    assert coverage(metrics) == "FINAL_15M"
    assert metrics["return_300s_pct"] is not None
    assert metrics["mfe_300s_pct"] is not None
    assert metrics["mae_300s_pct"] is not None

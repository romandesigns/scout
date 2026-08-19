import importlib.util
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "recall_opportunity.py"
spec = importlib.util.spec_from_file_location("recall_opportunity", MODULE)
m = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(m)


def sample(at, gainers, findings):
    return {"sampled_at": at, "gainers": gainers, "twenty_four_hour": [], "findings": findings}


def test_monster_recall_records_early_seen_before_threshold():
    samples = [
        sample(100, [{"symbol":"WFF","price":2.00,"percent_change":3.0}], [
            {"id":1,"ticker":"WFF","stage":"PRE_IGNITION","detected_at":100,"price":2.00,"actionable_rank":"C","quality_label":"DEVELOPING"}
        ]),
        sample(130, [{"symbol":"WFF","price":2.35,"percent_change":8.0}], [
            {"id":1,"ticker":"WFF","stage":"PRE_IGNITION","detected_at":100,"price":2.00,"actionable_rank":"C","quality_label":"DEVELOPING"},
            {"id":2,"ticker":"WFF","stage":"IGNITION","detected_at":125,"price":2.25,"actionable_rank":"A","quality_label":"CLEAN"},
        ]),
        sample(400, [{"symbol":"WFF","price":4.00,"percent_change":60.0}], []),
    ]
    r=m.report(samples)
    assert r["threshold_recall"]["5"]["movers"] == 1
    assert r["threshold_recall"]["5"]["seen_before_threshold"] == 1
    assert r["rows"][0]["first_scout_price"] == 2.00
    assert r["rows"][0]["first_actionable_price"] == 2.25


def test_ipst_pattern_exposes_large_actionable_delay():
    samples = [
        sample(100, [{"symbol":"IPST","price":2.76,"percent_change":6.0}], [
            {"id":10,"ticker":"IPST","stage":"ACTIVITY_WATCH","detected_at":100,"price":2.76,"actionable_rank":"C","quality_label":"ILLIQUID"}
        ]),
        sample(200, [{"symbol":"IPST","price":5.00,"percent_change":90.0}], []),
        sample(300, [{"symbol":"IPST","price":8.01,"percent_change":190.0}], [
            {"id":11,"ticker":"IPST","stage":"EARLY","detected_at":295,"price":8.01,"actionable_rank":"A","quality_label":"CLEAN"}
        ]),
        sample(400, [{"symbol":"IPST","price":10.00,"percent_change":260.0}], []),
    ]
    r=m.report(samples)
    row=next(x for x in r["rows"] if x["ticker"]=="IPST")
    assert row["first_scout_price"] == 2.76
    assert row["first_actionable_price"] == 8.01
    assert row["move_consumed_at_first_actionable_pct"] > 60


def test_missed_monster_is_explicitly_reported():
    samples = [
        sample(100, [{"symbol":"MISS","price":1.00,"percent_change":4.0}], []),
        sample(200, [{"symbol":"MISS","price":1.50,"percent_change":55.0}], []),
    ]
    r=m.report(samples)
    assert r["threshold_recall"]["50"]["movers"] == 1
    assert r["threshold_recall"]["50"]["scout_seen"] == 0
    assert r["largest_missed_movers"][0]["ticker"] == "MISS"


def test_naive_baseline_and_scout_returns_are_kept_separate():
    samples = [
        sample(100, [{"symbol":"ABC","price":1.00,"percent_change":5.0}], [
            {"id":1,"ticker":"ABC","stage":"EARLY","detected_at":100,"price":1.00,"actionable_rank":"A","quality_label":"CLEAN"}
        ]),
        sample(400, [{"symbol":"ABC","price":1.10,"percent_change":15.0}], []),
        sample(1000, [{"symbol":"ABC","price":1.20,"percent_change":25.0}], []),
    ]
    r=m.report(samples)
    row=r["rows"][0]
    assert round(row["scout_5m_return_pct"], 6) == 10.0
    assert round(row["baseline_5m_return_pct"], 6) == 10.0


def test_preexisting_session_gainer_is_not_counted_as_fresh_crossing():
    samples=[
        sample(100,[{"symbol":"OLD","price":2.0,"percent_change":60.0}],[]),
        sample(130,[{"symbol":"OLD","price":2.1,"percent_change":65.0}],[]),
    ]
    r=m.report(samples)
    assert r["threshold_recall"]["50"]["fresh_crossings"]==0
    assert r["threshold_recall"]["50"]["preexisting_at_monitor_start"]==1


def test_scout_history_before_monitor_does_not_fake_seen_before_cross():
    samples=[
        sample(100,[{"symbol":"ABC","price":1.0,"percent_change":2.0}],[
            {"id":1,"ticker":"ABC","stage":"PRE_IGNITION","detected_at":50,"price":0.9,
             "actionable_rank":"C","quality_label":"DEVELOPING"}
        ]),
        sample(130,[{"symbol":"ABC","price":1.1,"percent_change":6.0}],[]),
    ]
    r=m.report(samples)
    assert r["threshold_recall"]["5"]["fresh_crossings"]==1
    assert r["threshold_recall"]["5"]["seen_before_fresh_cross"]==0

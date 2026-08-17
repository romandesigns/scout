import sqlite3
from app.db import Store


def test_list_findings_can_query_mature_actionable_rows_directly(tmp_path):
    path = tmp_path / "state.db"
    store = Store(path)
    # Insert through the real schema with the minimum fields needed by save_finding
    # is unnecessarily coupled; use the live findings table directly for query semantics.
    with store.lock:
        store.db.execute(
            """INSERT INTO findings(ticker,stage,detected_at,price,score,evidence_json,signals_json,
               quality_label,quality_score,actionable_rank,rejection_reasons_json,hybrid_sources_json,
               recipe_present_json,recipe_missing_json,candidate_profile_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("OLD_A","EARLY",1000.0,5.0,1.0,"[]","[]","CLEAN",100,"A","[]","[]","[]","[]","{}"),
        )
        store.db.execute(
            """INSERT INTO findings(ticker,stage,detected_at,price,score,evidence_json,signals_json,
               quality_label,quality_score,actionable_rank,rejection_reasons_json,hybrid_sources_json,
               recipe_present_json,recipe_missing_json,candidate_profile_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("NEW_A","EARLY",2000.0,5.0,1.0,"[]","[]","CLEAN",100,"A","[]","[]","[]","[]","{}"),
        )
        store.db.execute(
            """INSERT INTO findings(ticker,stage,detected_at,price,score,evidence_json,signals_json,
               quality_label,quality_score,actionable_rank,rejection_reasons_json,hybrid_sources_json,
               recipe_present_json,recipe_missing_json,candidate_profile_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("OLD_C","ACTIVITY_WATCH",900.0,5.0,1.0,"[]","[]","DEVELOPING",80,"C","[]","[]","[]","[]","{}"),
        )
        store.db.commit()

    rows = store.list_findings(limit=10, before=1500.0, actionable_only=True)
    assert [r["ticker"] for r in rows] == ["OLD_A"]


def test_list_findings_can_filter_engine_version(tmp_path):
    from app.db import Store
    path = tmp_path / "state.db"
    store = Store(path)
    with store.lock:
        for ticker, version in (("OLD", "6.6.6"), ("NEW", "6.6.7")):
            store.db.execute(
                """INSERT INTO findings(ticker,stage,detected_at,price,score,evidence_json,signals_json,
                   quality_label,quality_score,actionable_rank,rejection_reasons_json,hybrid_sources_json,
                   recipe_present_json,recipe_missing_json,candidate_profile_json,engine_version)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ticker,"EARLY",1000.0,5.0,1.0,"[]","[]","CLEAN",100,"A","[]","[]","[]","[]","{}",version),
            )
        store.db.commit()
    rows = store.list_findings(limit=10, before=1500.0, actionable_only=True, engine_version="6.6.7")
    assert [r["ticker"] for r in rows] == ["NEW"]

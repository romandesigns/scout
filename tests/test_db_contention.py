from pathlib import Path

from app.db import Store


def test_hybrid_key_index_exists_after_schema_migration(tmp_path: Path):
    with Store(tmp_path / "state.db") as store:
        indexes = {row[1] for row in store.db.execute("PRAGMA index_list(findings)").fetchall()}
    assert "ix_findings_hybrid_key_time" in indexes


def test_claim_seen_is_atomic(tmp_path: Path):
    with Store(tmp_path / "state.db") as store:
        assert store.claim_seen("news:1", "test") is True
        assert store.claim_seen("news:1", "test") is False
        assert store.seen("news:1") is True


def test_hybrid_precision_query_uses_hybrid_index_without_correlated_scan(tmp_path: Path):
    with Store(tmp_path / "state.db") as store:
        plan = store.db.execute(
            """
            EXPLAIN QUERY PLAN
            WITH ranked AS (
                SELECT f.id, f.hybrid_key, f.detected_at,
                       ROW_NUMBER() OVER (PARTITION BY f.hybrid_key ORDER BY f.detected_at, f.id) AS rn
                FROM findings f
                WHERE f.hybrid_key IS NOT NULL
            ),
            sources AS (
                SELECT hybrid_key,
                       GROUP_CONCAT(DISTINCT COALESCE(engine_source,'python')) AS source_mix
                FROM findings
                WHERE hybrid_key IS NOT NULL
                GROUP BY hybrid_key
            )
            SELECT r.hybrid_key, s.source_mix, o.max_15m_pct
            FROM ranked r
            JOIN sources s ON s.hybrid_key=r.hybrid_key
            JOIN outcomes o ON o.finding_id=r.id
            WHERE r.rn=1 AND o.max_15m_pct IS NOT NULL
            """
        ).fetchall()
    details = "\n".join(str(row[3]) for row in plan)
    assert "ix_findings_hybrid_key_time" in details
    assert "CORRELATED SCALAR SUBQUERY" not in details
    assert "SCAN f2" not in details

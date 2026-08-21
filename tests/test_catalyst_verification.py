import time

from app.db import Store


def test_only_verified_catalysts_are_associated_or_exposed(tmp_path):
    store = Store(tmp_path / "scout.db")
    try:
        now = int(time.time())
        store.save_catalyst("TEST", "Unverified headline", "News", 5, "https://example.test/a", "RSS", now)
        assert store.recent_catalyst("TEST") is None
        assert store.list_catalysts(ticker="TEST") == []

        store.save_catalyst(
            "TEST", "Verified filing", "Contract", 8,
            "https://www.sec.gov/Archives/test", "SEC", now,
            verified=True, verification_method="sec-cik-filing",
        )
        associated = store.recent_catalyst("TEST")
        assert associated and associated[0] == "Verified filing"
        exposed = store.list_catalysts(ticker="TEST")
        assert exposed[0]["verified"] is True
        assert exposed[0]["verification_method"] == "sec-cik-filing"
    finally:
        store.close()


def test_legacy_schema_migrates_catalysts_to_unverified(tmp_path):
    path = tmp_path / "legacy.db"
    store = Store(path)
    try:
        columns = {row[1] for row in store.db.execute("PRAGMA table_info(catalysts)")}
        assert {"verified", "verification_method"}.issubset(columns)
    finally:
        store.close()

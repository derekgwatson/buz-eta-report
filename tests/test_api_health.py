"""Tests for API health check endpoint."""
import sqlite3


def test_health_ok(client, app):
    """Health check returns ok when DB is accessible."""
    with app.app_context():
        from services.database import get_db

        db = get_db()
        db.execute(
            "CREATE TABLE IF NOT EXISTS cache "
            "(key TEXT PRIMARY KEY, payload TEXT NOT NULL, "
            "meta TEXT, updated_at_utc DATETIME NOT NULL)"
        )
        db.commit()

    r = client.get("/api/v1/health")
    assert r.status_code == 200
    j = r.get_json()
    assert j["data"]["status"] == "ok"
    assert j["data"]["db"] is True
    assert "cache_entries" in j["data"]
    assert "running_jobs" in j["data"]


def test_health_db_failure(client, monkeypatch):
    """Health check returns 500 when DB is unreachable."""
    monkeypatch.setattr(
        "api.health.get_db",
        lambda: (_ for _ in ()).throw(sqlite3.OperationalError("nope")),
        raising=True,
    )
    r = client.get("/api/v1/health")
    assert r.status_code == 500
    j = r.get_json()
    assert j["code"] == "SERVER_ERROR"

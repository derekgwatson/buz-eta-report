"""Tests for API key authentication."""


def test_missing_api_key_returns_401(client):
    r = client.get("/api/v1/customers")
    assert r.status_code == 401
    j = r.get_json()
    assert j["code"] == "UNAUTHORIZED"


def test_invalid_api_key_returns_403(client):
    r = client.get("/api/v1/customers", headers={"X-API-Key": "wrong-key"})
    assert r.status_code == 403
    j = r.get_json()
    assert j["code"] == "FORBIDDEN"


def test_valid_api_key_succeeds(client, monkeypatch, api_headers):
    monkeypatch.setattr(
        "api.customers.query_db", lambda *a, **k: [], raising=True
    )
    r = client.get("/api/v1/customers", headers=api_headers)
    assert r.status_code == 200


def test_no_buz_api_key_configured_returns_500(client, monkeypatch):
    monkeypatch.delenv("BUZ_API_KEY")
    r = client.get(
        "/api/v1/customers", headers={"X-API-Key": "anything"}
    )
    assert r.status_code == 500
    j = r.get_json()
    assert j["code"] == "SERVER_CONFIG_ERROR"


def test_health_does_not_require_api_key(client, monkeypatch):
    monkeypatch.setattr(
        "api.health.get_db",
        lambda: _FakeConn(),
        raising=True,
    )
    r = client.get("/api/v1/health")
    assert r.status_code == 200


class _FakeConn:
    """Minimal fake DB connection for health check."""

    def execute(self, sql, *a):
        return self

    def fetchone(self):
        if "cache" in getattr(self, "_last_sql", ""):
            return (5,)
        return (1,)

    def __getattr__(self, name):
        # Capture last SQL for context
        if name == "execute":
            return self._exec
        return super().__getattribute__(name)

    def _exec(self, sql, *a):
        self._last_sql = sql
        return self

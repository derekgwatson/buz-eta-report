"""Tests for API status mapping endpoints."""


def test_list_statuses(client, monkeypatch, api_headers):
    fake_mappings = [
        (1, "Work in Progress", "WIP", True),
        (2, "Cancelled", "Cancelled", False),
    ]
    monkeypatch.setattr(
        "api.statuses.get_status_mappings",
        lambda conn: fake_mappings,
        raising=True,
    )
    monkeypatch.setattr(
        "api.statuses.get_db", lambda: None, raising=True
    )
    r = client.get("/api/v1/statuses", headers=api_headers)
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert len(data) == 2
    assert data[0]["odata_status"] == "Work in Progress"
    assert data[0]["active"] is True
    assert data[1]["active"] is False


def test_refresh_statuses_success(client, monkeypatch, api_headers):
    class FakeConn:
        def cursor(self):
            return self

        def execute(self, sql, *a):
            return self

        def fetchone(self):
            return (5,)

    monkeypatch.setattr(
        "api.statuses.get_db", lambda: FakeConn(), raising=True
    )
    monkeypatch.setattr(
        "api.statuses.populate_status_mapping_table",
        lambda conn: None,
        raising=True,
    )
    r = client.post("/api/v1/statuses/refresh", headers=api_headers)
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["refreshed"] is True
    assert data["active_count"] == 5


def test_refresh_statuses_failure(client, monkeypatch, api_headers):
    monkeypatch.setattr(
        "api.statuses.get_db", lambda: None, raising=True
    )
    monkeypatch.setattr(
        "api.statuses.populate_status_mapping_table",
        lambda conn: (_ for _ in ()).throw(RuntimeError("OData down")),
        raising=True,
    )
    r = client.post("/api/v1/statuses/refresh", headers=api_headers)
    assert r.status_code == 500
    assert r.get_json()["code"] == "SERVER_ERROR"

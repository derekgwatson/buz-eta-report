"""Tests for API customer CRUD endpoints."""
import json


def _make_row(id=1, dd="Acme DD", cbr="Acme CBR", obf="abc123", ft="Customer Name", dn="Acme"):
    """Create a fake sqlite3.Row-like dict for monkeypatching."""

    class Row(dict):
        def __getitem__(self, key):
            if isinstance(key, int):
                return list(self.values())[key]
            return super().__getitem__(key)

    return Row(
        id=id, dd_name=dd, cbr_name=cbr,
        obfuscated_id=obf, field_type=ft, display_name=dn,
    )


# ---------- LIST ----------

def test_list_customers_empty(client, monkeypatch, api_headers):
    monkeypatch.setattr("api.customers.query_db", lambda *a, **k: [], raising=True)
    r = client.get("/api/v1/customers", headers=api_headers)
    assert r.status_code == 200
    assert r.get_json()["data"] == []


def test_list_customers_returns_all(client, monkeypatch, api_headers):
    rows = [
        _make_row(id=1, dd="A", cbr="B", obf="x1", dn="Alpha"),
        _make_row(id=2, dd="C", cbr="D", obf="x2", dn="Beta"),
    ]
    monkeypatch.setattr("api.customers.query_db", lambda *a, **k: rows, raising=True)
    r = client.get("/api/v1/customers", headers=api_headers)
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert len(data) == 2
    assert data[0]["display_name"] == "Alpha"
    assert set(data[0].keys()) == {"id", "dd_name", "cbr_name", "obfuscated_id", "field_type", "display_name"}


# ---------- GET ----------

def test_get_customer_found(client, monkeypatch, api_headers):
    row = _make_row()
    calls = []

    def fake_query(sql, args=(), one=False, **kw):
        calls.append(sql)
        if one:
            return row
        return [row]

    monkeypatch.setattr("api.customers.query_db", fake_query, raising=True)
    r = client.get("/api/v1/customers/abc123", headers=api_headers)
    assert r.status_code == 200
    assert r.get_json()["data"]["obfuscated_id"] == "abc123"


def test_get_customer_not_found(client, monkeypatch, api_headers):
    monkeypatch.setattr(
        "api.customers.query_db", lambda *a, **k: None, raising=True
    )
    r = client.get("/api/v1/customers/nonexistent", headers=api_headers)
    assert r.status_code == 404
    assert r.get_json()["code"] == "NOT_FOUND"


# ---------- CREATE ----------

def test_create_customer_success(client, monkeypatch, api_headers):
    created_row = _make_row(obf="new-obf-id", dd="Test DD", cbr=None, dn="Test DD")
    call_count = {"n": 0}

    def fake_query(sql, args=(), one=False, **kw):
        call_count["n"] += 1
        if "INSERT" in sql:
            return None
        if one:
            return created_row
        return [created_row]

    monkeypatch.setattr("api.customers.query_db", fake_query, raising=True)
    r = client.post(
        "/api/v1/customers",
        headers=api_headers,
        data=json.dumps({"dd_name": "Test DD", "field_type": "Customer Name"}),
    )
    assert r.status_code == 201
    assert r.get_json()["data"]["dd_name"] == "Test DD"


def test_create_customer_auto_display_name_from_cbr(client, monkeypatch, api_headers):
    """When no display_name provided and both names given, cbr_name is preferred."""
    inserted = {}

    def fake_query(sql, args=(), one=False, **kw):
        if "INSERT" in sql:
            inserted["args"] = args
            return None
        return _make_row(dd="DD", cbr="CBR", dn="CBR")

    monkeypatch.setattr("api.customers.query_db", fake_query, raising=True)
    r = client.post(
        "/api/v1/customers",
        headers=api_headers,
        data=json.dumps({"dd_name": "DD", "cbr_name": "CBR"}),
    )
    assert r.status_code == 201
    # display_name (index 2 in INSERT args) should be cbr_name
    assert inserted["args"][2] == "CBR"


def test_create_customer_missing_names(client, api_headers):
    r = client.post(
        "/api/v1/customers",
        headers=api_headers,
        data=json.dumps({}),
        content_type="application/json",
    )
    assert r.status_code == 422
    assert r.get_json()["code"] == "VALIDATION_ERROR"


def test_create_customer_invalid_field_type(client, api_headers):
    r = client.post(
        "/api/v1/customers",
        headers=api_headers,
        data=json.dumps({"dd_name": "X", "field_type": "Invalid"}),
        content_type="application/json",
    )
    assert r.status_code == 422


def test_create_customer_no_json_body(client, api_headers):
    r = client.post("/api/v1/customers", headers={"X-API-Key": api_headers["X-API-Key"]})
    assert r.status_code == 400
    assert r.get_json()["code"] == "BAD_REQUEST"


# ---------- UPDATE ----------

def test_update_customer_success(client, monkeypatch, api_headers):
    existing = _make_row()
    updated = _make_row(dd="New DD", dn="New DD")

    call_n = {"n": 0}

    def fake_query(sql, args=(), one=False, **kw):
        call_n["n"] += 1
        if "UPDATE" in sql:
            return None
        if one:
            return existing if call_n["n"] == 1 else updated
        return []

    monkeypatch.setattr("api.customers.query_db", fake_query, raising=True)
    r = client.put(
        "/api/v1/customers/abc123",
        headers=api_headers,
        data=json.dumps({"dd_name": "New DD"}),
    )
    assert r.status_code == 200


def test_update_customer_not_found(client, monkeypatch, api_headers):
    monkeypatch.setattr(
        "api.customers.query_db", lambda *a, **k: None, raising=True
    )
    r = client.put(
        "/api/v1/customers/nope",
        headers=api_headers,
        data=json.dumps({"dd_name": "X"}),
    )
    assert r.status_code == 404


# ---------- DELETE ----------

def test_delete_customer_success(client, monkeypatch, api_headers):
    existing = _make_row()
    call_n = {"n": 0}

    def fake_query(sql, args=(), one=False, **kw):
        call_n["n"] += 1
        if "DELETE" in sql:
            return None
        if one:
            return existing
        return []

    monkeypatch.setattr("api.customers.query_db", fake_query, raising=True)
    r = client.delete("/api/v1/customers/abc123", headers=api_headers)
    assert r.status_code == 204
    assert r.data == b""


def test_delete_customer_not_found(client, monkeypatch, api_headers):
    monkeypatch.setattr(
        "api.customers.query_db", lambda *a, **k: None, raising=True
    )
    r = client.delete("/api/v1/customers/nope", headers=api_headers)
    assert r.status_code == 404

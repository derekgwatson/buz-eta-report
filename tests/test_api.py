import json
import pytest


API_KEY = "test-api-key-123"
HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    monkeypatch.setenv("BUZ_API_KEY", API_KEY)


# ---------- Auth ----------

def test_missing_api_key_returns_401(client):
    r = client.get("/api/v1/customers")
    assert r.status_code == 401


def test_wrong_api_key_returns_401(client):
    r = client.get("/api/v1/customers", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


# ---------- GET /api/v1/customers ----------

def test_list_customers_empty(client, monkeypatch):
    monkeypatch.setattr("routes.api.query_db", lambda *a, **k: [])
    r = client.get("/api/v1/customers", headers=HEADERS)
    assert r.status_code == 200
    assert r.get_json()["data"] == []


def test_list_customers_returns_data(client, monkeypatch):
    from unittest.mock import MagicMock
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": 1, "dd_name": "Acme DD", "cbr_name": None,
        "display_name": "Acme", "obfuscated_id": "abc123",
        "field_type": "Customer Name",
    }[key]
    monkeypatch.setattr("routes.api.query_db", lambda *a, **k: [row])
    r = client.get("/api/v1/customers", headers=HEADERS)
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert len(data) == 1
    assert data[0]["display_name"] == "Acme"


# ---------- POST /api/v1/customers ----------

def test_create_customer_success(client, monkeypatch):
    monkeypatch.setattr("routes.api.query_db", lambda *a, **k: None)
    r = client.post("/api/v1/customers", headers=HEADERS,
                    data=json.dumps({"dd_name": "Acme", "cbr_name": "Acme CBR"}))
    assert r.status_code == 201
    data = r.get_json()["data"]
    assert data["dd_name"] == "Acme"
    assert data["cbr_name"] == "Acme CBR"
    assert data["display_name"] == "Acme CBR"  # cbr_name takes priority
    assert data["field_type"] == "Customer Name"
    assert len(data["obfuscated_id"]) == 32
    assert "report_url" in data


def test_create_customer_dd_only(client, monkeypatch):
    monkeypatch.setattr("routes.api.query_db", lambda *a, **k: None)
    r = client.post("/api/v1/customers", headers=HEADERS,
                    data=json.dumps({"dd_name": "Acme DD"}))
    assert r.status_code == 201
    data = r.get_json()["data"]
    assert data["display_name"] == "Acme DD"
    assert data["cbr_name"] is None


def test_create_customer_custom_display_name(client, monkeypatch):
    monkeypatch.setattr("routes.api.query_db", lambda *a, **k: None)
    r = client.post("/api/v1/customers", headers=HEADERS,
                    data=json.dumps({"dd_name": "Acme", "display_name": "My Custom Name"}))
    assert r.status_code == 201
    assert r.get_json()["data"]["display_name"] == "My Custom Name"


def test_create_customer_missing_names_returns_400(client):
    r = client.post("/api/v1/customers", headers=HEADERS,
                    data=json.dumps({"display_name": "No names"}))
    assert r.status_code == 400
    assert "dd_name or cbr_name" in r.get_json()["error"]


def test_create_customer_invalid_field_type_returns_400(client):
    r = client.post("/api/v1/customers", headers=HEADERS,
                    data=json.dumps({"dd_name": "Acme", "field_type": "Bad Type"}))
    assert r.status_code == 400
    assert "field_type" in r.get_json()["error"]


def test_create_customer_no_auth_returns_401(client):
    r = client.post("/api/v1/customers",
                    headers={"Content-Type": "application/json"},
                    data=json.dumps({"dd_name": "Acme"}))
    assert r.status_code == 401

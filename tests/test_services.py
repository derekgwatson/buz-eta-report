import pytest
import requests
from unittest.mock import MagicMock

from services.odata_client import ODataClient
from services.buz_data import (
    get_open_orders,
    get_statuses,
)
from services.odata_utils import odata_quote


# ---------------------------
# Test doubles / fixtures
# ---------------------------

class MockResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP Error: {self.status_code}")


class MockHttpClient:
    """
    Drop-in replacement for requests.Session used by ODataClient.
    - Keyed by (url, frozenset(params.items())).
    - Supports status_code and raising exceptions (e.g., Timeout).
    - Ignores auth/timeout kwargs (so prod code can pass them).
    """
    def __init__(self):
        self._responses = {}
        self._errors = {}

    def set_mock_response(self, url, params, json_data, status_code=200):
        key = (url, frozenset(params.items()) if params else None)
        self._responses[key] = (json_data, status_code)

    def set_mock_error(self, url, params, exc):
        key = (url, frozenset(params.items()) if params else None)
        self._errors[key] = exc

    def get(self, url, params=None, auth=None, timeout=None):
        key = (url, frozenset(params.items()) if params else None)
        if key in self._errors:
            raise self._errors[key]
        if key not in self._responses:
            raise AssertionError(f"No mock response set for {url} with {params}")
        json_data, status_code = self._responses[key]
        return MockResponse(json_data, status_code=status_code)


@pytest.fixture
def mock_http_client():
    return MockHttpClient()


# ---------------------------
# OData quoting tests
# ---------------------------

def test_odata_quoting_simple():
    assert odata_quote("O'Malley") == "'O''Malley'"
    name = "O'Malley"
    assert f"Customer eq {odata_quote(name)}" == "Customer eq 'O''Malley'"


def test_odata_quoting_more():
    n = "D'Angelo's"
    assert odata_quote(n) == "'D''Angelo''s'"
    assert f"Customer eq {odata_quote(n)}" == "Customer eq 'D''Angelo''s'"


# ---------------------------
# ODataClient tests
# ---------------------------

def test_odata_client_get_success(mock_http_client):
    base = "https://api.buzmanager.com/reports/DESDR"
    endpoint = "JobsScheduleDetailed"
    url = f"{base}/{endpoint}"
    params = {"$filter": "OrderStatus eq 'Work in Progress'"}

    mock_http_client.set_mock_response(
        url,
        params,
        {
            "value": [
                {
                    "RefNo": "123",
                    "DateScheduled": "2025-01-01T10:00:00Z",
                    "ProductionStatus": "In Progress",
                },
                {
                    "RefNo": "456",
                    "DateScheduled": "2025-01-02T11:00:00Z",
                    "ProductionStatus": "Completed",
                },
            ]
        },
        status_code=200,
    )

    client = ODataClient(source="DD", http_client=mock_http_client)
    result = client.get(endpoint, ["OrderStatus eq 'Work in Progress'"])

    expected = [
        {
            "RefNo": "123",
            "DateScheduled": "01 Jan 2025",
            "ProductionStatus": "In Progress",
            "Instance": "DD",
        },
        {
            "RefNo": "456",
            "DateScheduled": "02 Jan 2025",
            "ProductionStatus": "Completed",
            "Instance": "DD",
        },
    ]
    assert result == expected


def test_odata_client_get_failure_http_error(mock_http_client):
    base = "https://api.buzmanager.com/reports/WATSO"
    endpoint = "JobsScheduleDetailed"
    url = f"{base}/{endpoint}"
    params = {"$filter": "OrderStatus eq 'Invalid'"}

    # Return 400 so raise_for_status triggers
    mock_http_client.set_mock_response(
        url,
        params,
        {"error": "Bad Request"},
        status_code=400,
    )

    client = ODataClient(source="CBR", http_client=mock_http_client)
    with pytest.raises(requests.HTTPError):
        client.get(endpoint, ["OrderStatus eq 'Invalid'"])


def test_odata_client_timeout_propagates(mock_http_client):
    base = "https://api.buzmanager.com/reports/DESDR"
    endpoint = "JobsScheduleDetailed"
    url = f"{base}/{endpoint}"
    params = {"$filter": "OrderStatus eq 'Work in Progress'"}

    # Simulate a network timeout from the session.get()
    mock_http_client.set_mock_error(url, params, requests.Timeout("connect/read timeout"))

    client = ODataClient(source="DD", http_client=mock_http_client)
    with pytest.raises(requests.Timeout):
        client.get(endpoint, ["OrderStatus eq 'Work in Progress'"])


# ---------------------------
# Higher-level helpers (buz_data) tests
# ---------------------------

def test_get_statuses(monkeypatch):
    """
    Ensures we request 'ne null' (no quotes) and return a set of known statuses.
    """
    calls = {}

    class _StubClient:
        def __init__(self, instance):
            calls["instance"] = instance

        def get(self, endpoint, filters):
            calls["endpoint"] = endpoint
            calls["filters"] = filters
            return [
                {"ProductionStatus": "In Progress"},
                {"ProductionStatus": "Completed"},
                {"ProductionStatus": None},
            ]

    # Monkeypatch constructor used by get_statuses(...) to our stub
    monkeypatch.setattr("services.buz_data.ODataClient", _StubClient)

    statuses = get_statuses("test_instance")
    assert set(statuses["data"]) == {"In Progress", "Completed"}
    assert calls["endpoint"] == "JobsScheduleDetailed"
    # Expect unquoted null per OData
    assert "ProductionStatus ne null" in calls["filters"]


def test_get_open_orders_dedup_and_mapping(monkeypatch):
    # Stub OData client
    class _StubClient:
        def __init__(self, instance):
            pass

        def get(self, endpoint, filters):
            return [
                {
                    "RefNo": "ORD001",
                    "Descn": "Order 1",
                    "DateScheduled": "2024-12-01",
                    "ProductionLine": "Line 1",
                    "InventoryItem": "Item 1",
                    "ProductionStatus": "In Progress",
                    "FixedLine": 1,
                },
                # Duplicate row to test drop_duplicates
                {
                    "RefNo": "ORD001",
                    "Descn": "Order 1",
                    "DateScheduled": "2024-12-01",
                    "ProductionLine": "Line 1",
                    "InventoryItem": "Item 1",
                    "ProductionStatus": "In Progress",
                    "FixedLine": 1,
                },
            ]

    monkeypatch.setattr("services.buz_data.ODataClient", _StubClient)

    # Fake DB connection to return a custom status mapping
    class _Cursor:
        def execute(self, *_a, **_k): return self
        def fetchall(self):           return [("In Progress", "Active")]  # two cols are fine for mapping

    class _Conn:
        def cursor(self): return _Cursor()

        def execute(self, *_args, **_kwargs):
            return _Cursor()

    result = get_open_orders(_Conn(), "Customer A", "TestInstance")
    assert result["source"] == "live"
    assert result["data"] == [
        {
            "RefNo": "ORD001",
            "Descn": "Order 1",
            "DateScheduled": "2024-12-01",
            "ProductionLine": "Line 1",
            "InventoryItem": "Item 1",
            "ProductionStatus": "Active",  # mapped from "In Progress"
            "FixedLine": 1,
        }
    ]


def test_get_open_orders_empty(monkeypatch):
    class _StubClient:
        def __init__(self, instance):
            pass

        def get(self, endpoint, filters):
            return []

    monkeypatch.setattr("services.buz_data.ODataClient", _StubClient)

    class _Conn:
        def execute(self, *_args, **_kwargs):
            class _C:
                def execute(self, *_a, **_k): return self
                def fetchall(self): return []
            return _C()

    assert get_open_orders(_Conn(), "NonExistingCustomer", "TestInstance") == {
        "data": [],
        "source": "live",
    }

import pytest
import requests

# OData client lives here
from services.odata_client import ODataClient
# App-facing helpers live here
from services.buz_data import (
    get_statuses,
    get_open_orders,
    get_open_orders_by_group,
    get_data_by_order_no,
    fetch_and_process_orders,
    fetch_or_cached
)
from services.odata_utils import odata_quote
from services.fetcher import ensure_cache_table, set_cache


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
    Drop-in stand-in for requests.Session used by ODataClient.
    Keyed by (url, frozenset(params.items()) or None).
    Ignored kwargs: auth, timeout.
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
            raise AssertionError(f"No mock for {url} with {params}")
        json_data, status_code = self._responses[key]
        return MockResponse(json_data, status_code=status_code)


@pytest.fixture
def mock_http_client():
    return MockHttpClient()


# ---------------------------
# OData quoting (utility) tests
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
# ODataClient (integration-ish) tests
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
                {"RefNo": "123", "DateScheduled": "2025-01-01T10:00:00Z", "ProductionStatus": "In Progress"},
                {"RefNo": "456", "DateScheduled": "2025-01-02T11:00:00Z", "ProductionStatus": "Completed"},
            ]
        },
        status_code=200,
    )

    client = ODataClient(source="DD", http_client=mock_http_client)
    out = client.get(endpoint, ["OrderStatus eq 'Work in Progress'"])

    assert out == [
        {"RefNo": "123", "DateScheduled": "01 Jan 2025", "ProductionStatus": "In Progress", "Instance": "DD"},
        {"RefNo": "456", "DateScheduled": "02 Jan 2025", "ProductionStatus": "Completed", "Instance": "DD"},
    ]


def test_odata_client_get_failure_http_error(mock_http_client):
    base = "https://api.buzmanager.com/reports/WATSO"
    endpoint = "JobsScheduleDetailed"
    url = f"{base}/{endpoint}"
    params = {"$filter": "OrderStatus eq 'Invalid'"}

    mock_http_client.set_mock_response(url, params, {"error": "Bad Request"}, status_code=400)

    client = ODataClient(source="CBR", http_client=mock_http_client)
    with pytest.raises(requests.HTTPError):
        client.get(endpoint, ["OrderStatus eq 'Invalid'"])


def test_odata_client_timeout(mock_http_client):
    base = "https://api.buzmanager.com/reports/DESDR"
    endpoint = "JobsScheduleDetailed"
    url = f"{base}/{endpoint}"
    params = {"$filter": "OrderStatus eq 'Work in Progress'"}

    mock_http_client.set_mock_error(url, params, requests.Timeout("connect/read timeout"))

    client = ODataClient(source="DD", http_client=mock_http_client)
    with pytest.raises(requests.Timeout):
        client.get(endpoint, ["OrderStatus eq 'Work in Progress'"])


# ---------------------------
# fetch_and_process_orders unit test
# ---------------------------

def test_fetch_and_process_orders_dedup_and_mapping(monkeypatch):
    class _StubClient:
        def get(self, endpoint, filters):
            assert endpoint == "JobsScheduleDetailed"
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
                # duplicate row
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

    # Fake DB connection returning a mapping
    class _C:
        def fetchall(self):
            return [("In Progress", "Active")]
    class _Conn:
        def cursor(self):
            class _Cur:
                def execute(self, *_a, **_k): return _C()
                def fetchall(self): return _C().fetchall()
            return _Cur()

    out = fetch_and_process_orders(_Conn(), _StubClient(), ["OrderStatus eq 'Work in Progress'"])
    assert out == [
        {
            "RefNo": "ORD001",
            "Descn": "Order 1",
            "DateScheduled": "2024-12-01",
            "ProductionLine": "Line 1",
            "InventoryItem": "Item 1",
            "ProductionStatus": "Active",  # mapped
            "FixedLine": 1,
        }
    ]


def test_fetch_and_process_orders_filters_fully_invoiced():
    """Orders where ALL non-null job tracking statuses are invoiced should be excluded"""
    class _StubClient:
        def get(self, endpoint, filters):
            return [
                {
                    "RefNo": "ORD001",
                    "Descn": "Fully Invoiced Order",
                    "DateScheduled": "2024-12-01",
                    "ProductionLine": "Line 1",
                    "InventoryItem": "Item 1",
                    "ProductionStatus": "Completed",
                    "FixedLine": 1,
                    "Workflow_Job_Tracking_Status": "Invoiced",
                },
                {
                    "RefNo": "ORD001",
                    "Descn": "Fully Invoiced Order",
                    "DateScheduled": "2024-12-01",
                    "ProductionLine": "Line 2",
                    "InventoryItem": "Item 2",
                    "ProductionStatus": "Completed",
                    "FixedLine": 2,
                    "Workflow_Job_Tracking_Status": "Invoiced",
                },
            ]

    class _Conn:
        def cursor(self):
            class _Cur:
                def execute(self, *_a, **_k): pass
                def fetchall(self): return []
            return _Cur()

    out = fetch_and_process_orders(_Conn(), _StubClient(), ["OrderStatus eq 'Work in Progress'"])
    assert out == []  # Order should be filtered out


def test_fetch_and_process_orders_filters_fully_cancelled():
    """Orders where ALL non-null job tracking statuses are cancelled should be excluded"""
    class _StubClient:
        def get(self, endpoint, filters):
            return [
                {
                    "RefNo": "ORD002",
                    "Descn": "Cancelled Order",
                    "DateScheduled": "2024-12-01",
                    "ProductionLine": "Line 1",
                    "InventoryItem": "Item 1",
                    "ProductionStatus": "Cancelled",
                    "FixedLine": 1,
                    "Workflow_Job_Tracking_Status": "Cancelled",
                },
            ]

    class _Conn:
        def cursor(self):
            class _Cur:
                def execute(self, *_a, **_k): pass
                def fetchall(self): return []
            return _Cur()

    out = fetch_and_process_orders(_Conn(), _StubClient(), ["OrderStatus eq 'Work in Progress'"])
    assert out == []  # Order should be filtered out


def test_fetch_and_process_orders_keeps_mixed_status():
    """Orders with some lines in progress should be kept"""
    class _StubClient:
        def get(self, endpoint, filters):
            return [
                {
                    "RefNo": "ORD003",
                    "Descn": "Mixed Status Order",
                    "DateScheduled": "2024-12-01",
                    "ProductionLine": "Line 1",
                    "InventoryItem": "Item 1",
                    "ProductionStatus": "Completed",
                    "FixedLine": 1,
                    "Workflow_Job_Tracking_Status": "Invoiced",
                },
                {
                    "RefNo": "ORD003",
                    "Descn": "Mixed Status Order",
                    "DateScheduled": "2024-12-01",
                    "ProductionLine": "Line 2",
                    "InventoryItem": "Item 2",
                    "ProductionStatus": "In Progress",
                    "FixedLine": 2,
                    "Workflow_Job_Tracking_Status": "In Progress",
                },
            ]

    class _Conn:
        def cursor(self):
            class _Cur:
                def execute(self, *_a, **_k): pass
                def fetchall(self): return []
            return _Cur()

    out = fetch_and_process_orders(_Conn(), _StubClient(), ["OrderStatus eq 'Work in Progress'"])
    assert len(out) == 2  # Order should be kept with both lines


def test_fetch_and_process_orders_ignores_null_statuses():
    """Orders with null job tracking statuses should be ignored (neither counted as finished nor unfinished)"""
    class _StubClient:
        def get(self, endpoint, filters):
            return [
                {
                    "RefNo": "ORD004",
                    "Descn": "Order with Nulls",
                    "DateScheduled": "2024-12-01",
                    "ProductionLine": "Line 1",
                    "InventoryItem": "Item 1",
                    "ProductionStatus": "In Progress",
                    "FixedLine": 1,
                    "Workflow_Job_Tracking_Status": None,  # null status
                },
                {
                    "RefNo": "ORD004",
                    "Descn": "Order with Nulls",
                    "DateScheduled": "2024-12-01",
                    "ProductionLine": "Line 2",
                    "InventoryItem": "Item 2",
                    "ProductionStatus": "Completed",
                    "FixedLine": 2,
                    "Workflow_Job_Tracking_Status": "Invoiced",
                },
            ]

    class _Conn:
        def cursor(self):
            class _Cur:
                def execute(self, *_a, **_k): pass
                def fetchall(self): return []
            return _Cur()

    out = fetch_and_process_orders(_Conn(), _StubClient(), ["OrderStatus eq 'Work in Progress'"])
    # Should be filtered out because the only non-null status is "Invoiced"
    assert out == []


def test_fetch_and_process_orders_keeps_all_null_statuses():
    """Orders where all job tracking statuses are null should be kept"""
    class _StubClient:
        def get(self, endpoint, filters):
            return [
                {
                    "RefNo": "ORD005",
                    "Descn": "All Null Order",
                    "DateScheduled": "2024-12-01",
                    "ProductionLine": "Line 1",
                    "InventoryItem": "Item 1",
                    "ProductionStatus": "In Progress",
                    "FixedLine": 1,
                    "Workflow_Job_Tracking_Status": None,
                },
            ]

    class _Conn:
        def cursor(self):
            class _Cur:
                def execute(self, *_a, **_k): pass
                def fetchall(self): return []
            return _Cur()

    out = fetch_and_process_orders(_Conn(), _StubClient(), ["OrderStatus eq 'Work in Progress'"])
    assert len(out) == 1  # Order should be kept


def test_fetch_and_process_orders_without_workflow_field():
    """Orders without Workflow_Job_Tracking_Status field should not be filtered"""
    class _StubClient:
        def get(self, endpoint, filters):
            return [
                {
                    "RefNo": "ORD006",
                    "Descn": "No Workflow Field",
                    "DateScheduled": "2024-12-01",
                    "ProductionLine": "Line 1",
                    "InventoryItem": "Item 1",
                    "ProductionStatus": "In Progress",
                    "FixedLine": 1,
                },
            ]

    class _Conn:
        def cursor(self):
            class _Cur:
                def execute(self, *_a, **_k): pass
                def fetchall(self): return []
            return _Cur()

    out = fetch_and_process_orders(_Conn(), _StubClient(), ["OrderStatus eq 'Work in Progress'"])
    assert len(out) == 1  # Order should be kept (no filtering applied)


# ---------------------------
# High-level helpers with caching shim
# ---------------------------

def _live_fetch(monkeypatch):
    """
    Shim fetch_or_cached so tests don't depend on cache timing.
    It simply calls fetch_fn() and returns (data, "live").
    """
    def _shim(**kwargs):
        data = kwargs["fetch_fn"]()
        return data, "live"
    monkeypatch.setattr("services.buz_data.fetch_or_cached", _shim)


def test_get_statuses_live(monkeypatch):
    _live_fetch(monkeypatch)

    calls = {}
    class _StubClient:
        def __init__(self, instance): calls["instance"] = instance
        def get(self, endpoint, filters):
            calls["endpoint"] = endpoint
            calls["filters"] = filters
            return [
                {"ProductionStatus": "Completed"},
                {"ProductionStatus": "In Progress"},
                {"ProductionStatus": None},
            ]

    monkeypatch.setattr("services.buz_data.ODataClient", _StubClient)

    out = get_statuses("TestInst")
    assert out["source"] == "live"
    assert out["data"] == ["Completed", "In Progress"]
    assert calls["endpoint"] == "JobsScheduleDetailed"
    assert "OrderStatus eq 'Work in Progress'" in calls["filters"]
    assert "ProductionStatus ne null" in calls["filters"]  # unquoted null


def test_get_open_orders_live(monkeypatch):
    _live_fetch(monkeypatch)

    class _StubClient:
        def __init__(self, instance): pass
        def get(self, endpoint, filters):
            # Expect the quoted customer
            assert any(f.startswith("Customer eq 'Acme Pty Ltd'") for f in filters)
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
                # dup to test drop_duplicates path via fetch_and_process_orders
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

    # Minimal DB conn with one mapping row
    class _C:
        def fetchall(self): return [("In Progress", "Active")]
    class _Conn:
        def cursor(self):
            class _Cur:
                def execute(self, *_a, **_k): return _C()
                def fetchall(self): return _C().fetchall()
            return _Cur()

    out = get_open_orders(_Conn(), "Acme Pty Ltd", "TestInst")
    assert out["source"] == "live"
    assert out["data"] == [
        {
            "RefNo": "ORD001",
            "Descn": "Order 1",
            "DateScheduled": "2024-12-01",
            "ProductionLine": "Line 1",
            "InventoryItem": "Item 1",
            "ProductionStatus": "Active",
            "FixedLine": 1,
        }
    ]


def test_get_open_orders_by_group_live(monkeypatch):
    _live_fetch(monkeypatch)

    # 1) Group lookup returns two customers
    monkeypatch.setattr(
        "services.buz_data.get_customers_by_group",
        lambda group, inst: [{"Customer": "Acme"}, {"Customer": "Bravo"}],
    )

    # 2) OData client returns one row irrespective of customer batch
    class _StubClient:
        def __init__(self, instance): pass
        def get(self, endpoint, filters):
            # Ensure batched filter shape contains "Customer in (...)" + required predicates
            filt = " and ".join(filters)
            assert "OrderStatus eq 'Work in Progress'" in filt
            assert "ProductionStatus ne null" in filt
            assert "Customer in (" in filt
            return [
                {
                    "RefNo": "X1",
                    "Descn": "Order X",
                    "DateScheduled": "2024-12-02",
                    "ProductionLine": "Line A",
                    "InventoryItem": "Item A",
                    "ProductionStatus": "In Progress",
                    "FixedLine": 1,
                }
            ]

    monkeypatch.setattr("services.buz_data.ODataClient", _StubClient)

    # DB mapping: map "In Progress" -> "Active"
    class _C:
        def fetchall(self): return [("In Progress", "Active")]
    class _Conn:
        def cursor(self):
            class _Cur:
                def execute(self, *_a, **_k): return _C()
                def fetchall(self): return _C().fetchall()
            return _Cur()

    out = get_open_orders_by_group(_Conn(), "SomeGroup", "TestInst")
    assert out["source"] == "live"
    assert out["data"] == [
        {
            "RefNo": "X1",
            "Descn": "Order X",
            "DateScheduled": "2024-12-02",
            "ProductionLine": "Line A",
            "InventoryItem": "Item A",
            "ProductionStatus": "Active",
            "FixedLine": 1,
        }
    ]


def test_get_data_by_order_no_live(monkeypatch):
    _live_fetch(monkeypatch)

    class _StubClient:
        def __init__(self, instance): pass
        def get(self, endpoint, filters):
            assert endpoint == "SomeEndpoint"
            assert filters == ["RefNo eq 'ORD-123'"]
            return [{"RefNo": "ORD-123", "Foo": "Bar"}]

    monkeypatch.setattr("services.buz_data.ODataClient", _StubClient)

    out = get_data_by_order_no("ORD-123", "SomeEndpoint", "TestInst")
    assert out == {"data": [{"RefNo": "ORD-123", "Foo": "Bar"}], "source": "live"}


def test_fallback_on_500(monkeypatch):
    ensure_cache_table()
    # Prewarm cache for key "k"
    set_cache("k", [{"RefNo": "R1"}], meta={"note": "prewarmed"})

    def boom():
        import requests
        raise requests.HTTPError(response=type("R", (), {"status_code": 500})())

    data, source = fetch_or_cached(
        cache_key="k",
        fetch_fn=boom,
        force_refresh=True,
        max_age_minutes_when_open=0,
        fallback_http_statuses=(500, 503),
        fallback_on_timeouts=True,
        fallback_on_conn_errors=True,
    )

    assert data == [{"RefNo": "R1"}]
    assert source.startswith("cache")

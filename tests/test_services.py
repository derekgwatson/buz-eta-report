import requests
from services.buz_data import get_open_orders, get_data_by_order_no, get_statuses, ODataClient
from services.database import query_db
import pytest


class MockHttpClient:
    def __init__(self):
        self.responses = {}

    def set_mock_response(self, url, params, response):
        self.responses[(url, frozenset(params.items()))] = response

    def get(self, url, params, auth=None):
        key = (url, frozenset(params.items()))
        if key not in self.responses:
            raise ValueError(f"No mock response set for {key}")
        return MockResponse(self.responses[key])


class MockResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP Error: {self.status_code}")


@pytest.fixture
def mock_http_client():
    """Fixture to provide a reusable MockHttpClient."""
    return MockHttpClient()


def test_odata_client_get_success(mock_http_client):
    # Set up mock response
    mock_http_client.set_mock_response(
        "https://api.buzmanager.com/reports/DESDR/JobsScheduleDetailed",
        {"$filter": "OrderStatus eq 'Work in Progress'"},
        {
            "value": [
                {"RefNo": "123", "DateScheduled": "2025-01-01T10:00:00Z", "ProductionStatus": "In Progress"},
                {"RefNo": "456", "DateScheduled": "2025-01-02T11:00:00Z", "ProductionStatus": "Completed"},
            ]
        },
    )

    # Initialize ODataClient with mock HTTP client
    client = ODataClient(source="DD", http_client=mock_http_client)

    # Call get method
    result = client.get("JobsScheduleDetailed", ["OrderStatus eq 'Work in Progress'"])

    # Assertions
    expected = [
        {"RefNo": "123", "DateScheduled": "01 Jan 2025", "ProductionStatus": "In Progress", "Instance": "DD"},
        {"RefNo": "456", "DateScheduled": "02 Jan 2025", "ProductionStatus": "Completed", "Instance": "DD"},
    ]
    assert result == expected


def test_odata_client_get_failure(mock_http_client):
    # Set up mock response with HTTP error
    mock_http_client.set_mock_response(
        "https://api.buzmanager.com/reports/WATSO/JobsScheduleDetailed",
        {"$filter": "OrderStatus eq 'Invalid'"},
        {"error": "Bad Request"},
    )

    # Initialize ODataClient with mock HTTP client
    client = ODataClient(source="CBR", http_client=mock_http_client)

    # Call get method and expect an exception
    with pytest.raises(requests.HTTPError):
        client.get("JobsScheduleDetailed", ["OrderStatus eq 'Invalid'"])


def test_query_db(app):
    with app.app_context():
        result = query_db("SELECT 1")
        assert result is not None


def test_get_statuses(mock_odata_client):
    # Mock ODataClient
    mock_instance = "test_instance"
    mock_data = [
        {"ProductionStatus": "In Progress"},
        {"ProductionStatus": "Completed"},
        {"ProductionStatus": None},
    ]
    mock_odata_client.return_value.get.return_value = mock_data

    # Call the function
    statuses = get_statuses(mock_instance)

    # Assertions
    assert statuses == {"In Progress", "Completed"}
    mock_odata_client.assert_called_once_with(mock_instance)
    mock_odata_client.return_value.get.assert_called_once_with(
        "JobsScheduleDetailed",
        ["OrderStatus eq 'Work in Progress'", "ProductionStatus ne 'null'"]
    )


def test_get_open_orders(self, mock_cursor, mock_odata_client):
    # Mock response data
    mock_data = [
        {
            "RefNo": "ORD001",
            "Descn": "Order 1",
            "DateScheduled": "2024-12-01",
            "ProductionLine": "Line 1",
            "InventoryItem": "Item 1",
            "ProductionStatus": "In Progress",
            "FixedLine": 1,
        },
        {
            "RefNo": "ORD001",
            "Descn": "Order 1",
            "DateScheduled": "2024-12-01",
            "ProductionLine": "Line 1",
            "InventoryItem": "Item 1",
            "ProductionStatus": "In Progress",
            "FixedLine": 1,  # Duplicate to test drop_duplicates
        },
    ]
    mock_odata_client.return_value.get.return_value = mock_data

    # Mock the database cursor
    mock_cursor.return_value.fetchall.return_value = [
        ("In Progress", "Active")  # Sample status mapping
    ]

    # Call the function
    result = get_open_orders(MagicMock(), "Customer A", "TestInstance")

    # Expected result after deduplication and sorting
    expected = [
        {
            "RefNo": "ORD001",
            "Descn": "Order 1",
            "DateScheduled": "2024-12-01",
            "ProductionLine": "Line 1",
            "InventoryItem": "Item 1",
            "ProductionStatus": "Active",  # Custom status mapped
            "FixedLine": 1,
        }
    ]

    self.assertEqual(result, expected)


def test_get_open_orders_empty(self, mock_odata_client):
    mock_odata_client.return_value.get.return_value = []
    result = get_open_orders(MagicMock(), "NonExistingCustomer", "TestInstance")
    self.assertEqual(result, [])


def test_get_schedule_jobs_details(self, mock_odata_client):
    mock_data = [
        {"RefNo": "ORD001", "JobDetails": "Detail A"},
        {"RefNo": "ORD001", "JobDetails": "Detail B"},
    ]
    mock_odata_client.return_value.get.return_value = mock_data

    result = get_schedule_jobs_details("ORD001", "JobsScheduleDetailed", "TestInstance")
    self.assertEqual(result, mock_data)


def test_get_schedule_jobs_details_error(self, mock_odata_client):
    mock_odata_client.return_value.get.side_effect = requests.exceptions.RequestException("Network error")
    result = get_schedule_jobs_details("ORD001", "JobsScheduleDetailed", "TestInstance")
    expected = {"error": "Failed to fetch JobsScheduleDetails: Network error"}
    self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()

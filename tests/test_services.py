import unittest
from unittest.mock import patch, MagicMock
import requests
from services.buz_data import get_open_orders, get_schedule_jobs_details
from services.buz_data import query_db


def test_query_db(app):
    with app.app_context():
        result = query_db("SELECT 1")
        assert result is not None

class TestOpenOrders(unittest.TestCase):

    @patch("services.buz_data.ODataClient")  # Ensure the correct path for patching
    @patch("services.buz_data.conn.cursor")  # Mock the database connection
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

    @patch("services.buz_data.ODataClient")
    def test_get_open_orders_empty(self, mock_odata_client):
        mock_odata_client.return_value.get.return_value = []
        result = get_open_orders(MagicMock(), "NonExistingCustomer", "TestInstance")
        self.assertEqual(result, [])

    @patch("services.buz_data.ODataClient")
    def test_get_schedule_jobs_details(self, mock_odata_client):
        mock_data = [
            {"RefNo": "ORD001", "JobDetails": "Detail A"},
            {"RefNo": "ORD001", "JobDetails": "Detail B"},
        ]
        mock_odata_client.return_value.get.return_value = mock_data

        result = get_schedule_jobs_details("ORD001", "JobsScheduleDetailed", "TestInstance")
        self.assertEqual(result, mock_data)

    @patch("services.buz_data.ODataClient")
    def test_get_schedule_jobs_details_error(self, mock_odata_client):
        mock_odata_client.return_value.get.side_effect = requests.exceptions.RequestException("Network error")
        result = get_schedule_jobs_details("ORD001", "JobsScheduleDetailed", "TestInstance")
        expected = {"error": "Failed to fetch JobsScheduleDetails: Network error"}
        self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()

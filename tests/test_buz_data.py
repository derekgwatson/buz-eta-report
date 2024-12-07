import unittest
from unittest.mock import patch, MagicMock
import pandas as pd
import requests

from services.odata_client import ODataClient
from services.buz_data import get_open_orders, get_schedule_jobs_details


class TestOpenOrders(unittest.TestCase):

    @patch("your_module.ODataClient")
    def test_get_open_orders(self, mock_odata_client):
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

        # Set mock return value
        mock_odata_client.return_value.get.return_value = mock_data

        # Call the function
        result = get_open_orders("Customer A", "TestInstance")

        # Expected result after deduplication and sorting
        expected = [
            {
                "RefNo": "ORD001",
                "Descn": "Order 1",
                "DateScheduled": "2024-12-01",
                "ProductionLine": "Line 1",
                "InventoryItem": "Item 1",
                "ProductionStatus": "In Progress",
                "FixedLine": 1,
            }
        ]

        self.assertEqual(result, expected)

    @patch("your_module.ODataClient")
    def test_get_open_orders_empty(self, mock_odata_client):
        mock_odata_client.return_value.get.return_value = []
        result = get_open_orders("NonExistingCustomer", "TestInstance")
        self.assertEqual(result, [])

    @patch("your_module.ODataClient")
    def test_get_schedule_jobs_details(self, mock_odata_client):
        mock_data = [
            {"RefNo": "ORD001", "JobDetails": "Detail A"},
            {"RefNo": "ORD001", "JobDetails": "Detail B"},
        ]
        mock_odata_client.return_value.get.return_value = mock_data

        result = get_schedule_jobs_details("ORD001", "JobsScheduleDetailed", "TestInstance")
        self.assertEqual(result, mock_data)

    @patch("your_module.ODataClient")
    def test_get_schedule_jobs_details_error(self, mock_odata_client):
        mock_odata_client.return_value.get.side_effect = requests.exceptions.RequestException("Network error")
        result = get_schedule_jobs_details("ORD001", "JobsScheduleDetailed", "TestInstance")
        expected = {"error": "Failed to fetch JobsScheduleDetails: Network error"}
        self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()

from datetime import datetime
import os
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
import logging


class ODataClient:
    """
    Encapsulates a connection to an OData source.

    Attributes:
        root_url (str): The root URL of the OData service.
        username (str): The username for authentication.
        password (str): The password for authentication.
    """

    def __init__(self, source: str, http_client=None):
        """
        Initializes the ODataClient instance with the root URL and credentials.

        Args:
            source (str): The OData source we're getting data from
        """
        # Load environment variables from .env file
        load_dotenv()

        if source == 'DD':
            self.root_url = "https://api.buzmanager.com/reports/DESDR"
            self.username = os.getenv("BUZ_DD_USERNAME")
            self.password = os.getenv("BUZ_DD_PASSWORD")
        elif source == 'CBR':
            self.root_url = "https://api.buzmanager.com/reports/WATSO"
            self.username = os.getenv("BUZ_CBR_USERNAME")
            self.password = os.getenv("BUZ_CBR_PASSWORD")
        else:
            raise ValueError(f"Unrecognised source: {source}")

        # Check if required environment variables are loaded
        if not self.username or not self.password:
            raise ValueError(f"Missing credentials for source: {source}")

        self.auth = HTTPBasicAuth(self.username, self.password)
        self.source = source
        self.http_client = http_client or requests

    def get(self, endpoint: str, params: list) -> list:
        """
        Sends a GET request to the OData service.

        Args:
            endpoint (str): The endpoint to append to the root URL.
            params (list): Query parameters for the GET request.

        Returns:
            list: The JSON response from the OData service.

        Raises:
            requests.RequestException: If the response contains an HTTP error status or other request-related issues.
        """
        url = f"{self.root_url}/{endpoint.lstrip('/')}"

        # Join the conditions with " and " and encode spaces as %20
        filter_query = " and ".join(params)
        encoded_filter = {"$filter": filter_query}

        # If additional query parameters are needed, merge them
        other_params = {}  # Add other query parameters if needed
        query_params = {**encoded_filter, **other_params}

        try:
            # Send the GET request
            response = self.http_client.get(url, params=query_params, auth=self.auth)
            response.raise_for_status()  # Raise an exception for HTTP errors

            # Process and reformat dates
            data = response.json()
            return self._format_data(data.get("value", []))

        except requests.exceptions.RequestException as e:
            # Catch all request-related exceptions
            logging.error(f"Request failed: {e}")
            raise  # Re-raise the exception to propagate it up

        except Exception as e:
            # Catch unexpected exceptions
            logging.error(f"An unexpected error occurred: {e}")
            raise  # Re-raise the exception to propagate it up

    def _format_data(self, data: list) -> list:
        """
        Formats and processes the response data, updating all lines in an order to the latest DateScheduled.

        Args:
            data (list): Raw data from the OData service.

        Returns:
            list: Formatted data with updated DateScheduled for all lines.
        """
        # Step 1: Find the latest DateScheduled for each order
        latest_dates = {}
        for item in data:
            order_id = item.get("RefNo")
            date_scheduled = item.get("DateScheduled")
            if order_id and date_scheduled:
                parsed_date = datetime.strptime(date_scheduled, "%Y-%m-%dT%H:%M:%SZ")
                if order_id not in latest_dates or parsed_date > latest_dates[order_id]:
                    latest_dates[order_id] = parsed_date

        # Step 2: Update each line's DateScheduled to the latest date for its order
        formatted = []
        for item in data:
            order_id = item.get("RefNo")
            latest_date = latest_dates.get(order_id)
            if latest_date:
                item["DateScheduled"] = latest_date.strftime("%d %b %Y")
            item["Instance"] = self.source
            formatted.append(item)

        return formatted

from datetime import datetime
from urllib.parse import urlencode, quote
import os
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv


class ODataClient:
    """
    Encapsulates a connection to an OData source.

    Attributes:
        root_url (str): The root URL of the OData service.
        username (str): The username for authentication.
        password (str): The password for authentication.
    """

    def __init__(self, source: str):
        """
        Initializes the ODataClient instance with the root URL and credentials.

        Args:
            source (str): The oData source we're getting data from
            username (str): The username for authentication.
            password (str): The password for authentication.
        """
        # Load environment variables from .env file
        load_dotenv()

        if source == 'DD':
            root_url = "http://api.buzmanager.com/reports/DESDR"
            username = os.getenv("BUZ_DD_USERNAME")
            password = os.getenv("BUZ_DD_PASSWORD")
        elif source == 'CBR':
            root_url = "http://api.buzmanager.com/reports/WATSO"
            username = os.getenv("BUZ_CBR_USERNAME")
            password = os.getenv("BUZ_CBR_PASSWORD")
        else:
            raise f"Unrecognised source: {source}"

        self.root_url = root_url.rstrip('/')  # Ensure no trailing slash
        self.auth = HTTPBasicAuth(username, password)
        self.source = source

    def get(self, endpoint: str, params: list) -> list:
        """
        Sends a GET request to the OData service.

        Args:
            endpoint (str): The endpoint to append to the root URL.
            params (list): Query parameters for the GET request.

        Returns:
            list: The JSON response from the OData service.

        Raises:
            requests.HTTPError: If the response contains an HTTP error status.
        """
        url = f"{self.root_url}/{endpoint.lstrip('/')}"

        # Join the conditions with " and " and encode spaces as %20
        filter_query = " and ".join(params)
        encoded_filter = "$filter=" + quote(filter_query, safe="()'")

        # Use urlencode for other parameters if necessary
        other_params = {}  # Add other query parameters if needed
        encoded_params = urlencode(other_params)

        # Combine $filter with other parameters
        query_string = f"{encoded_filter}&{encoded_params}" if other_params else encoded_filter

        # Send the GET request
        full_url = f"{url}?{query_string}"
        response = requests.get(full_url, auth=self.auth)
        response.raise_for_status()  # Raise an exception for HTTP errors

        # Process and reformat dates
        formatted_data = []
        for item in response.json().get("value", []):
            item['Instance'] = self.source
            if "DateScheduled" in item:
                try:
                    # Parse and format the date
                    original_date = item["DateScheduled"]
                    parsed_date = datetime.strptime(original_date, "%Y-%m-%dT%H:%M:%SZ")
                    item["DateScheduled"] = parsed_date.strftime("%d %b %Y")  # e.g., "27 Nov 2024"
                except ValueError:
                    pass  # Keep the original date if parsing fails
            formatted_data.append(item)

        return formatted_data


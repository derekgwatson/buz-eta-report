from datetime import datetime
import os
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DEFAULT_TIMEOUT = (5, 20)  # (connect, read) seconds


class TimeoutSession(requests.Session):
    def __init__(self, timeout=DEFAULT_TIMEOUT):
        super().__init__()
        self._timeout = timeout

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", self._timeout)
        return super().request(*args, **kwargs)


def _session_with_retries() -> requests.Session:
    s = TimeoutSession()
    retry = Retry(
        total=1,               # 1 retry max (fast fail)
        connect=1,             # retry only on connect errors
        read=0,                # do NOT retry long reads (keeps time bounded)
        status=1,              # retry on 5xx/429 once
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


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
        self.http = http_client or _session_with_retries()

    def get(self, endpoint: str, params: list) -> list:
        """
        Sends a GET request or a POST request to the OData service, depending on URL length.

        Args:
            :param endpoint: (str): The endpoint to append to the root URL.
            :param params: (list): Query parameters for the request.
            :param timeout:

        Returns:
            list: The JSON response from the OData service.

        Raises:
            requests.RequestException: If the response contains an HTTP error status or other request-related issues.
        """
        url = f"{self.root_url}/{endpoint.lstrip('/')}"

        # Join the conditions with " and "
        filter_query = " and ".join(params)
        encoded_filter = {"$filter": filter_query}

        # Log the full URL for debugging
        try:
            from flask import current_app
            if current_app:
                current_app.logger.info(f"OData GET: {url}?$filter={filter_query}")
        except:
            pass

        try:
            response = self.http.get(url, params=encoded_filter, auth=self.auth)
            response.raise_for_status()

        except requests.exceptions.RequestException as e:
            # Log the error with the URL for debugging
            try:
                from flask import current_app
                if current_app:
                    current_app.logger.error(f"OData request failed: {url}?$filter={filter_query} - Error: {e}")
            except:
                pass
            raise

        # Process and reformat dates
        data = response.json()
        return self._format_data(data.get("value", []))

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

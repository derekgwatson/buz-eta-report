from datetime import datetime
from urllib.parse import urlencode, quote
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

    def __init__(self, source: str):
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
            response = requests.get(url, params=query_params, auth=self.auth)
            response.raise_for_status()  # Raise an exception for HTTP errors

            # Process and reformat dates
            formatted_data = []
            response_json = response.json()
            if "value" not in response_json:
                logging.warning(f"No 'value' key in response from {url}.")
                return formatted_data  # Return an empty list if 'value' is not present

            for item in response_json["value"]:
                item['Instance'] = self.source

                # Ensure DateScheduled is present and valid
                original_date = item.get("DateScheduled")
                if original_date:
                    try:
                        parsed_date = datetime.strptime(original_date, "%Y-%m-%dT%H:%M:%SZ")
                        item["DateScheduled"] = parsed_date.strftime("%d %b %Y")  # Format as "27 Nov 2024"
                    except ValueError:
                        logging.warning(f"Failed to parse date: {original_date}. Keeping original value.")
                        pass  # Keep the original date if parsing fails

                formatted_data.append(item)

            return formatted_data

        except requests.exceptions.RequestException as e:
            # Catch all request-related exceptions
            logging.error(f"Request failed: {e}")
            raise  # Re-raise the exception to propagate it up

        except Exception as e:
            # Catch unexpected exceptions
            logging.error(f"An unexpected error occurred: {e}")
            raise  # Re-raise the exception to propagate it up

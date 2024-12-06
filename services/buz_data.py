from datetime import datetime
import urllib.parse
import os
import requests
import pandas as pd
from flask import request, jsonify


def get_data(customer, instance, username, password):
    # Base URL for SalesReport
    root_url = f"http://api.buzmanager.com/reports/{instance}"
    sales_report_url = f"{root_url}/JobsScheduleDetailed"

    # Build the OData filter
    filter_conditions = [
        "OrderStatus eq 'Work in Progress'",
        "ProductionStatus ne null",
        f"Customer eq '{customer}'"  # Add Customer filter to OData
    ]
    odata_filter = " and ".join(filter_conditions)
    encoded_filter = urllib.parse.quote(odata_filter)  # Encode the filter for the URL
    filtered_sales_report_url = f"{sales_report_url}?$filter={encoded_filter}"

    # Fetch filtered SalesReport data
    response = requests.get(filtered_sales_report_url, auth=(username, password))
    response.raise_for_status()
    sales_report_data = response.json()

    # Convert SalesReport data to Pandas DataFrame
    _sales_report = pd.DataFrame(sales_report_data.get("value", []))

    # Ensure DataFrame is not empty
    if _sales_report.empty:
        return []

    # Drop rows where ProductionStatus is null (redundant now as it's in the OData filter)
    _sales_report = _sales_report[_sales_report["ProductionStatus"].notnull()]

    # Remove duplicate rows based on the displayed columns
    displayed_columns = ["RefNo", "Descn", "DateScheduled", "ProductionLine", "InventoryItem",
                         "ProductionStatus", "FixedLine"]
    _sales_report = _sales_report.drop_duplicates(subset=displayed_columns)

    # Sort by RefNo and FixedLine
    _sales_report = _sales_report.sort_values(by=["RefNo", "FixedLine"], ascending=[True, True])

    # Convert the DataFrame to a list of dictionaries
    _sales_report_dict = _sales_report.to_dict(orient="records")

    # Process and reformat dates
    for item in _sales_report_dict:
        item["Instance"] = instance
        if "DateScheduled" in item:
            try:
                # Parse and format the date
                original_date = item["DateScheduled"]
                parsed_date = datetime.strptime(original_date, "%Y-%m-%dT%H:%M:%SZ")
                item["DateScheduled"] = parsed_date.strftime("%d %b %Y")  # e.g., "27 Nov 2024"
            except ValueError:
                pass  # Keep the original date if parsing fails

    return _sales_report_dict



def get_schedule_jobs_details(order_no):
    """Fetch and return JobsScheduleDetails data for a given order number."""
    root_url = "http://api.buzmanager.com/reports/DESDR"
    jobs_schedule_details_url = f"{root_url}/JobsScheduleDetailed"

    # Authentication
    username = os.getenv("BUZMANAGER_USERNAME")
    password = os.getenv("BUZMANAGER_PASSWORD")

    try:
        # Build the filtered URL
        odata_filter = f"RefNo eq '{order_no}'"  # Adjust the field name if needed
        encoded_filter = urllib.parse.quote(odata_filter)
        filtered_url = f"{jobs_schedule_details_url}?$filter={encoded_filter}"

        # Fetch the filtered data
        response = requests.get(filtered_url, auth=(username, password))
        response.raise_for_status()
        jobs_schedule_data = response.json()

        # Process and reformat dates
        formatted_data = []
        for item in jobs_schedule_data.get("value", []):
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

    except requests.exceptions.RequestException as e:
        return {"error": f"Failed to fetch JobsScheduleDetails: {str(e)}"}

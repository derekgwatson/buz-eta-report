import requests
import pandas as pd
from services.odata_client import ODataClient


def get_open_orders(customer, instance):
    # Base URL for SalesReport
    odata_client = ODataClient(instance)

    # Build the OData filter
    filter_conditions = [
        "OrderStatus eq 'Work in Progress'",
        "ProductionStatus ne null",
        f"Customer eq '{customer}'"  # Add Customer filter to OData
    ]

    # Fetch filtered SalesReport data
    sales_report_data = odata_client.get("JobsScheduleDetailed", filter_conditions)

    # Convert SalesReport data to Pandas DataFrame
    _sales_report = pd.DataFrame(sales_report_data)

    # Ensure DataFrame is not empty
    if _sales_report.empty:
        return []

    # Remove duplicate rows based on the displayed columns
    displayed_columns = ["RefNo", "Descn", "DateScheduled", "ProductionLine", "InventoryItem",
                         "ProductionStatus", "FixedLine"]
    _sales_report = _sales_report.drop_duplicates(subset=displayed_columns)

    # Sort by RefNo and FixedLine
    _sales_report = _sales_report.sort_values(by=["RefNo", "FixedLine"], ascending=[True, True])

    # Convert the DataFrame to a list of dictionaries
    return _sales_report.to_dict(orient="records")


def get_schedule_jobs_details(order_no, endpoint, instance):
    """Fetch and return JobsScheduleDetails data for a given order number."""
    odata_client = ODataClient(instance)
    # Build the OData filter
    filter_conditions = [
        f"RefNo eq '{order_no}'",
    ]

    try:
        # Build the filtered URL
        return odata_client.get(endpoint, filter_conditions)

    except requests.exceptions.RequestException as e:
        return {"error": f"Failed to fetch JobsScheduleDetails: {str(e)}"}

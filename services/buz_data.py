import requests
from services.odata_client import ODataClient
import pandas as pd


def get_statuses(instance):
    # Base URL for SalesReport
    odata_client = ODataClient(instance)

    # Define instance-specific filters
    filter_conditions = [
            "OrderStatus eq 'Work in Progress'",
            "ProductionStatus ne 'null'",
        ]

    # Fetch filtered SalesReport data
    report_data = odata_client.get("JobsScheduleDetailed", filter_conditions)

    statuses = {item["ProductionStatus"] for item in report_data if "ProductionStatus" in item}
    return statuses


def fetch_and_process_orders(conn, odata_client, filter_conditions):
    # Fetch filtered SalesReport data
    sales_report_data = odata_client.get("JobsScheduleDetailed", filter_conditions)

    # Convert SalesReport data to Pandas DataFrame
    _sales_report = pd.DataFrame(sales_report_data)

    # Ensure DataFrame is not empty
    if _sales_report.empty:
        return []

    # Fetch active status mappings from the database
    cursor = conn.cursor()
    cursor.execute(''' 
    SELECT odata_status, custom_status
    FROM status_mapping 
    WHERE active = TRUE
    ''')
    status_mappings = dict(cursor.fetchall())  # odata_status as keys, custom_status as values

    # Remove rows where the ProductionStatus is not in the active status mappings
    _sales_report = _sales_report[_sales_report['ProductionStatus'].isin(status_mappings.keys())]

    # Map ProductionStatus using the status mappings
    _sales_report.loc[:, 'ProductionStatus'] = _sales_report['ProductionStatus'].map(
        lambda x: status_mappings.get(x, x)
    )

    # Remove duplicate rows based on the displayed columns
    displayed_columns = ["RefNo", "Descn", "DateScheduled", "ProductionLine",
                         "InventoryItem", "ProductionStatus", "FixedLine"]
    _sales_report = _sales_report.drop_duplicates(subset=displayed_columns)

    # Sort by RefNo and FixedLine
    _sales_report = _sales_report.sort_values(by=["RefNo", "FixedLine"], ascending=[True, True])

    # Convert the DataFrame to a list of dictionaries
    return _sales_report.to_dict(orient="records")


def get_customers_by_group(customer_group, instance):
    # Base URL for SalesReport
    odata_client = ODataClient(instance)

    # Build the OData filter
    filter_conditions = [
        "Order_Status eq 'Work in Progress'",
        f"CustomerGroup eq '{customer_group}'"  # Add Customer filter to OData
    ]

    # Fetch filtered SalesReport data
    customers = odata_client.get("SalesReport", filter_conditions)
    return customers


def get_open_orders(conn, customer, instance):
    # Base URL for SalesReport
    odata_client = ODataClient(instance)

    # Build the OData filter
    filter_conditions = [
        "OrderStatus eq 'Work in Progress'",
        "ProductionStatus ne null",
        f"Customer eq '{customer}'"  # Filter by single customer
    ]

    # Use the shared helper function
    return fetch_and_process_orders(conn, odata_client, filter_conditions)


def get_open_orders_by_group(conn, customer_group, instance):
    # Base URL for SalesReport
    odata_client = ODataClient(instance)

    # Fetch all customers in the specified group
    customers = get_customers_by_group(customer_group, instance)
    if not customers:
        print(f"No customers found for the specified group in {instance}.")
        return []

    # Extract unique customer names
    customer_names = sorted({customer['Customer'].strip() for customer in customers})

    # Set a maximum URL length threshold
    MAX_URL_LENGTH = 1000  # Adjust if needed

    # Prepare batched queries
    results = []
    batch = []
    query_base_length = len("OrderStatus eq 'Work in Progress' and ProductionStatus ne null and Customer in ()")

    for name in customer_names:
        formatted_name = f"'{name}'"  # Properly format names
        test_batch = batch + [formatted_name]  # Simulate adding a new name

        # Estimate the query length
        estimated_length = query_base_length + len(", ".join(test_batch))
        if estimated_length > MAX_URL_LENGTH:
            # Send the current batch and reset it
            customer_filter = f"Customer in ({', '.join(batch)})"
            filter_conditions = [
                "OrderStatus eq 'Work in Progress'",
                "ProductionStatus ne null",
                customer_filter
            ]
            print(f"filter_condition: {filter_conditions}")
            results.extend(fetch_and_process_orders(conn, odata_client, filter_conditions))
            batch = [formatted_name]  # Start a new batch
        else:
            batch.append(formatted_name)

    # Send the last batch if it has data
    if batch:
        customer_filter = f"Customer in ({', '.join(batch)})"
        filter_conditions = [
            "OrderStatus eq 'Work in Progress'",
            "ProductionStatus ne null",
            customer_filter
        ]
        results.extend(fetch_and_process_orders(conn, odata_client, filter_conditions))

    return results


def get_data_by_order_no(order_no, endpoint, instance):
    """Fetch and return JobsScheduleDetails data for a given order number."""
    return ODataClient(instance).get(endpoint, [f"RefNo eq '{order_no}'"])

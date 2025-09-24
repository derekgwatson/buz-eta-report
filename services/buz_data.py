from __future__ import annotations

from typing import Dict, Any, List
import pandas as pd
from services.odata_client import ODataClient
from services.fetcher import fetch_or_cached
from services.odata_utils import odata_quote


def get_statuses(instance: str) -> dict:
    """
    Returns a dict with:
      - data: list[str] of statuses
      - source: 'live' | 'cache' | 'cache-503' | 'cache-timeout' | 'cache-error'
    """
    odata_client = ODataClient(instance)

    def _fetch() -> List[str]:
        rows = odata_client.get(
            "JobsScheduleDetailed",
            ["OrderStatus eq 'Work in Progress'", "ProductionStatus ne null"],
        ) or []
        uniq = {
            (r.get("ProductionStatus") or "").strip()
            for r in rows
            if r.get("ProductionStatus")
        }
        return sorted(uniq)

    data, source = fetch_or_cached(
        cache_key=f"statuses:{instance}",
        fetch_fn=_fetch,
        # Always try live first; fall back on 503/timeouts/conn errors
        force_refresh=True,
        max_age_minutes_when_open=0,   # ignored because force_refresh=True
        fallback_http_statuses=(503,), # treat 503 as blackout
        fallback_on_timeouts=True,
        fallback_on_conn_errors=True,
        cooldown_on_503_minutes=10,    # avoid hammering during blackout
    )

    return {"data": data, "source": source}


def fetch_and_process_orders(conn, odata_client, filter_conditions):

    sales_report_data = odata_client.get("JobsScheduleDetailed", filter_conditions)
    if not sales_report_data:
        return []
    df = pd.DataFrame(sales_report_data)
    if df.empty:
        return []

    required = {"RefNo", "Descn", "DateScheduled", "ProductionLine", "InventoryItem", "ProductionStatus", "FixedLine"}
    for col in required:
        if col not in df.columns:
            df[col] = None

    # Fetch active status mappings from the database
    cursor = conn.cursor()
    cursor.execute(''' 
    SELECT odata_status, custom_status
    FROM status_mapping 
    WHERE active = TRUE
    ''')
    status_mappings = dict(cursor.fetchall())  # odata_status as keys, custom_status as values

    mask = df['ProductionStatus'].isin(status_mappings.keys())
    _sales_report = df.loc[mask].copy()  # ← copy
    _sales_report['ProductionStatus'] = (
        _sales_report['ProductionStatus'].map(lambda x: status_mappings.get(x, x))
    )

    # Remove duplicate rows based on the displayed columns
    displayed_columns = ["RefNo", "Descn", "DateScheduled", "ProductionLine",
                         "InventoryItem", "ProductionStatus", "FixedLine"]
    _sales_report = _sales_report.drop_duplicates(subset=displayed_columns)

    # Sort by RefNo and FixedLine
    _sales_report = _sales_report.sort_values(by=["RefNo", "FixedLine"], ascending=[True, True])

    # Convert the DataFrame to a list of dictionaries
    return _sales_report.to_dict(orient="records")


def get_customers_by_group(customer_group: str, instance: str) -> list[dict]:
    """
    Return customers in a given Buz customer group.
    NOTE: Use 'OrderStatus' (no underscore) to match other queries in this file.
    """
    odata_client = ODataClient(instance)
    filter_conditions = [
        "OrderStatus eq 'Work in Progress'",
        f"CustomerGroup eq '{customer_group}'",
    ]
    return odata_client.get("SalesReport", filter_conditions) or []


def get_open_orders(conn, customer: str, instance: str) -> dict:
    """
    Live-first; on 503/timeout/conn error, serve cached.
    Returns {"data": list[dict], "source": "..."} like the group version.
    """
    odata_client = ODataClient(instance)

    def _fetch() -> List[Dict[str, Any]]:
        filter_conditions = [
            "OrderStatus eq 'Work in Progress'",
            "ProductionStatus ne null",
            f"Customer eq {odata_quote(customer)}",
        ]
        return fetch_and_process_orders(conn, odata_client, filter_conditions)

    cache_key = f"open_orders:{instance}:customer:{customer}"

    orders, source = fetch_or_cached(
        cache_key=cache_key,
        fetch_fn=_fetch,
        force_refresh=True,             # try live first
        max_age_minutes_when_open=0,    # ignored when force_refresh=True
        fallback_http_statuses=(503,),  # blackout
        fallback_on_timeouts=True,
        fallback_on_conn_errors=True,
        cooldown_on_503_minutes=10,
    )
    return {"data": orders, "source": source}


def get_open_orders_by_group(conn, customer_group: str, instance: str) -> dict:
    """
    Fetch open orders for all customers in the given group.
    - Tries live first.
    - If the API returns 503 (blackout) or times out / connection error, serves cached.
    - Result is JSON-serialisable (list[dict]) and safe to store in cache.
    """
    odata_client = ODataClient(instance)

    def _fetch() -> List[Dict[str, Any]]:
        # 1) Gather customers in the group
        customers = get_customers_by_group(customer_group, instance)
        if not customers:
            return []

        # 2) Unique, sorted customer names
        customer_names = sorted({c["Customer"].strip() for c in customers if c.get("Customer")})

        # 3) Batch to avoid overly long OData query strings
        MAX_URL_LENGTH = 1000  # same threshold you used; adjust if needed
        results: list[dict] = []
        batch: list[str] = []
        # Approximate base length of the query without names
        base_len = len("OrderStatus eq 'Work in Progress' and ProductionStatus ne null and Customer in ()")

        for name in customer_names:
            quoted = odata_quote(name)
            test_batch = batch + [quoted]
            est_len = base_len + len(", ".join(test_batch))
            if est_len > MAX_URL_LENGTH:
                # flush current batch
                customer_filter = f"Customer in ({', '.join(batch)})"
                filter_conditions = [
                    "OrderStatus eq 'Work in Progress'",
                    "ProductionStatus ne null",
                    customer_filter,
                ]
                results.extend(fetch_and_process_orders(conn, odata_client, filter_conditions))
                batch = [quoted]  # start new batch
            else:
                batch.append(quoted)

        # 4) Flush the final batch
        if batch:
            customer_filter = f"Customer in ({', '.join(batch)})"
            filter_conditions = [
                "OrderStatus eq 'Work in Progress'",
                "ProductionStatus ne null",
                customer_filter,
            ]
            results.extend(fetch_and_process_orders(conn, odata_client, filter_conditions))

        return results

    # Cache key includes instance + group
    cache_key = f"open_orders_by_group:{instance}:{customer_group}"

    orders, source = fetch_or_cached(
        cache_key=cache_key,
        fetch_fn=_fetch,
        # Always attempt live first; fall back if 503/timeout/conn error
        force_refresh=True,
        max_age_minutes_when_open=0,    # ignored because force_refresh=True
        fallback_http_statuses=(503,),  # treat 503 as blackout
        fallback_on_timeouts=True,
        fallback_on_conn_errors=True,
        cooldown_on_503_minutes=10,     # avoid hammering during blackout
    )

    return {"data": orders, "source": source}


def get_data_by_order_no(order_no: str, endpoint: str, instance: str) -> dict:
    """
    Fetch order data for a specific order number.
    - Tries live first.
    - If the API returns 503 (blackout) or times out / connection error, serves cached.
    - Result is JSON-serialisable (list[dict]).
    """
    odata_client = ODataClient(instance)

    def _fetch() -> List[dict[str, Any]]:
        filter_conditions = [f"RefNo eq {odata_quote(order_no)}"]
        rows = odata_client.get(endpoint, filter_conditions) or []
        # Normalise to JSON-friendly list[dict]
        return [dict(r) for r in rows]

    cache_key = f"order:{instance}:{endpoint}:{order_no}"

    orders, source = fetch_or_cached(
        cache_key=cache_key,
        fetch_fn=_fetch,
        force_refresh=True,             # always attempt live first
        max_age_minutes_when_open=0,    # ignored when force_refresh=True
        fallback_http_statuses=(503,),  # 503 = blackout
        fallback_on_timeouts=True,
        fallback_on_conn_errors=True,
        cooldown_on_503_minutes=10,     # avoid hammering API during blackout
    )
    return {"data": orders, "source": source}

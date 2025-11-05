# services/eta_report.py
from datetime import datetime
from typing import Dict, List, Tuple, Callable, Optional, Iterable

from services.database import get_db
from services.buz_data import get_open_orders, get_open_orders_by_group

ProgressFn = Callable[[str, Optional[int]], None]


def _normalize_and_sort(values: List[str], case: str = "title") -> List[str]:
    """
    Normalizes a list of strings: trims, de-dupes, removes 'N/A',
    and sorts. Case controls final casing.
    """
    cleaned = [v.strip() for v in values if v and v != "N/A"]
    if case == "upper":
        return sorted({v.upper() for v in cleaned})
    elif case == "lower":
        return sorted({v.lower() for v in cleaned})
    else:  # title
        return sorted({v.lower().title() for v in cleaned})


def _fetch_customer_row(obfuscated_id: str, db=None):
    if db is None:
        db = get_db()
    cur = db.execute(
        "SELECT dd_name, cbr_name, field_type FROM customers WHERE obfuscated_id = ?",
        (obfuscated_id,),
    )
    return cur.fetchone()


def _combine_and_group(combined_data: List[Dict]) -> List[Dict]:
    """Group items by RefNo and sort groups by DateScheduled."""
    grouped_data: List[Dict] = []
    refno_to_date: Dict[str, datetime] = {}

    for item in combined_data:
        ref_no = item.get("RefNo")
        date_str = item.get("DateScheduled", "N/A")

        if ref_no not in refno_to_date:
            try:
                refno_to_date[ref_no] = (
                    datetime.strptime(date_str, "%d %b %Y") if date_str != "N/A" else datetime.min
                )
            except ValueError:
                refno_to_date[ref_no] = datetime.min

        group_entry = next((g for g in grouped_data if g["RefNo"] == ref_no), None)
        if not group_entry:
            group_entry = {"RefNo": ref_no, "group_items": [], "DateScheduled": date_str}
            grouped_data.append(group_entry)
        group_entry["group_items"].append(item)

    grouped_data.sort(key=lambda g: refno_to_date.get(g["RefNo"], datetime.min))
    return grouped_data


def _make_customer_name(dd_name: str, cbr_name: str) -> str:
    if dd_name == cbr_name or (cbr_name or "") == "":
        return dd_name or cbr_name or ""
    if (dd_name or "") == "":
        return cbr_name or ""
    return f"{dd_name} / {cbr_name}"


def _prog(progress: ProgressFn, msg: str, pct: Optional[int] = None) -> None:
    """Call progress if provided; ignore otherwise."""
    try:
        if callable(progress):
            progress(msg, pct)
    except Exception:
        # Never let progress updates break the request/job
        pass


def _to_list_of_dicts(x) -> List[Dict]:
    """Coerce various shapes (None, dict, list, tuple, generators, API envelopes) into a list[dict]."""
    if x is None:
        return []
    if isinstance(x, list):
        return x
    if isinstance(x, dict):
        # common API envelopes
        for key in ("data", "rows", "items", "results"):
            v = x.get(key)
            if isinstance(v, list):
                return v
        # single record dict → wrap
        return [x]
    if isinstance(x, tuple):
        return list(x)
    if isinstance(x, Iterable):
        return list(x)  # generator / cursor
    # last resort
    return [x]


def build_eta_report_context(
    obfuscated_id: str,
    db=None,
    progress: ProgressFn = None,
) -> Tuple[str, Dict, int]:
    """
    Build the context for the ETA report page.

    Returns: (template_name, context_dict, http_status)
    Never raises; returns 404 template when customer not found.
    """
    if db is None:
        db = get_db()

    _prog(progress, "Loading customer…", 5)
    row = _fetch_customer_row(obfuscated_id=obfuscated_id, db=db)
    if not row:
        return "404.html", {"message": f"No report found for ID: {obfuscated_id}"}, 404

    # sqlite3.Row with named columns due to explicit SELECT
    dd_name = row["dd_name"]
    cbr_name = row["cbr_name"]
    field_type = row["field_type"]

    # Determine if we need to fetch from both sources
    has_both = bool(dd_name) and bool(cbr_name)

    _prog(progress, "Fetching orders from API…", 10)

    # Fetch data from DD (if configured)
    # NOTE: Will sit at 10% during this long API call
    if dd_name:
        if field_type == "Customer Group":
            data_dd_raw = get_open_orders_by_group(db, dd_name, "DD")
        else:
            data_dd_raw = get_open_orders(db, dd_name, "DD")
        # After DD fetch completes, jump to 50% if we have both, or 80% if DD only
        _prog(progress, "DD data received", 50 if has_both else 80)
    else:
        data_dd_raw = {"data": [], "source": "live"}

    # Fetch data from CBR (if configured)
    # NOTE: Will sit at 50% during this long API call (if both sources)
    if cbr_name:
        if field_type == "Customer Group":
            data_cbr_raw = get_open_orders_by_group(db, cbr_name, "CBR")
        else:
            data_cbr_raw = get_open_orders(db, cbr_name, "CBR")
        # After CBR fetch completes, jump to 80%
        _prog(progress, "CBR data received", 80)
    else:
        data_cbr_raw = {"data": [], "source": "live"}

    _prog(progress, "Processing data…", 85)

    # Extract data and source information
    data_dd = _to_list_of_dicts(data_dd_raw)
    data_cbr = _to_list_of_dicts(data_cbr_raw)

    # Track data source (live vs cached)
    source_dd = data_dd_raw.get("source", "live") if isinstance(data_dd_raw, dict) else "live"
    source_cbr = data_cbr_raw.get("source", "live") if isinstance(data_cbr_raw, dict) else "live"

    # Overall source: if either is cached, show cached
    if source_dd == "live" and source_cbr == "live":
        overall_source = "live"
    elif source_dd != "live":
        overall_source = source_dd
    else:
        overall_source = source_cbr

    # In dev, fail fast with a clear message if not lists
    assert isinstance(data_dd, list) and isinstance(data_cbr, list), \
        f"get_open_orders* must return list; got {type(data_dd_raw)} and {type(data_cbr_raw)}"

    combined_data = data_cbr + data_dd

    _prog(progress, "Grouping data…", 90)
    grouped_data = _combine_and_group(combined_data)

    _prog(progress, "Finalizing…", 95)
    customer_name = _make_customer_name(dd_name or "", cbr_name or "")
    unique_statuses = _normalize_and_sort([i.get("ProductionStatus", "N/A") for i in combined_data])
    unique_groups = _normalize_and_sort([i.get("ProductionLine", "N/A") for i in combined_data])
    unique_suppliers = _normalize_and_sort([i.get("Instance", "N/A").upper() for i in combined_data], case="upper")

    ctx = {
        "customer_name": customer_name,
        "data": grouped_data if combined_data else None,
        "statuses": unique_statuses,
        "groups": unique_groups,
        "suppliers": unique_suppliers,
        "obfuscated_id": obfuscated_id,
        "source": overall_source,
        "last_dd": None,  # TODO: Extract from cache metadata if needed
        "last_cbr": None,  # TODO: Extract from cache metadata if needed
    }

    _prog(progress, "Complete", 100)
    return "report.html", ctx, 200

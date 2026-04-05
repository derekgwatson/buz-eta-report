"""Direct tests for scrub_sensitive() — verify sensitive columns are actually removed."""
from services.export import scrub_sensitive, DROP_KEY_RE


def test_removes_cost_columns():
    rows = [{"RefNo": "R1", "UnitCost": 10.5, "TotalCost": 100}]
    result = scrub_sensitive(rows)
    assert result == [{"RefNo": "R1"}]


def test_removes_margin_columns():
    rows = [{"RefNo": "R1", "Margin": 0.2, "MarginPct": "20%"}]
    result = scrub_sensitive(rows)
    assert result == [{"RefNo": "R1"}]


def test_removes_buy_price():
    rows = [{"RefNo": "R1", "BuyPrice": 5.0, "BuyQty": 10}]
    result = scrub_sensitive(rows)
    # BuyPrice matches "buy", BuyQty also matches "buy"
    assert "BuyPrice" not in result[0]


def test_removes_wholesale():
    rows = [{"RefNo": "R1", "WholesalePrice": 50}]
    result = scrub_sensitive(rows)
    assert result == [{"RefNo": "R1"}]


def test_removes_supplier_price():
    rows = [{"RefNo": "R1", "SupplierPrice": 25, "Supplier_Price": 25}]
    result = scrub_sensitive(rows)
    assert result == [{"RefNo": "R1"}]


def test_removes_markup():
    rows = [{"RefNo": "R1", "Markup": 1.5}]
    result = scrub_sensitive(rows)
    assert result == [{"RefNo": "R1"}]


def test_removes_cogs():
    rows = [{"RefNo": "R1", "COGS": 100}]
    result = scrub_sensitive(rows)
    assert result == [{"RefNo": "R1"}]


def test_removes_pkid():
    rows = [{"RefNo": "R1", "PKID": 999}]
    result = scrub_sensitive(rows)
    assert result == [{"RefNo": "R1"}]


def test_preserves_safe_columns():
    rows = [{"RefNo": "R1", "DateScheduled": "01 Jan 2025",
             "ProductionStatus": "Open", "ProductionLine": "Cutting",
             "InventoryItem": "ITEM1", "Instance": "DD"}]
    result = scrub_sensitive(rows)
    assert result == rows


def test_case_insensitive_matching():
    rows = [{"RefNo": "R1", "UNITCOST": 10, "unitcost": 5, "UnitCost": 8}]
    result = scrub_sensitive(rows)
    assert result == [{"RefNo": "R1"}]


def test_empty_rows():
    assert scrub_sensitive([]) == []


def test_multiple_rows():
    rows = [
        {"RefNo": "R1", "Cost": 10, "Name": "A"},
        {"RefNo": "R2", "Cost": 20, "Name": "B"},
    ]
    result = scrub_sensitive(rows)
    assert result == [
        {"RefNo": "R1", "Name": "A"},
        {"RefNo": "R2", "Name": "B"},
    ]

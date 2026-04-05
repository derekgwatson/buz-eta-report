"""Tests for services/eta_report.py helpers and build_eta_report_context."""
import sqlite3
import pytest
from services.eta_report import (
    _normalize_and_sort,
    _combine_and_group,
    _make_customer_name,
    _to_list_of_dicts,
    build_eta_report_context,
)


# ---------- _normalize_and_sort ----------

class TestNormalizeAndSort:
    def test_title_case_default(self):
        assert _normalize_and_sort(["banana", "APPLE", "cherry"]) == [
            "Apple", "Banana", "Cherry"
        ]

    def test_upper_case(self):
        assert _normalize_and_sort(["banana", "apple"], case="upper") == [
            "APPLE", "BANANA"
        ]

    def test_lower_case(self):
        assert _normalize_and_sort(["Banana", "APPLE"], case="lower") == [
            "apple", "banana"
        ]

    def test_deduplicates(self):
        assert _normalize_and_sort(["apple", "Apple", "APPLE"]) == ["Apple"]

    def test_strips_whitespace(self):
        assert _normalize_and_sort(["  apple ", " banana"]) == ["Apple", "Banana"]

    def test_removes_na(self):
        assert _normalize_and_sort(["apple", "N/A", "banana"]) == ["Apple", "Banana"]

    def test_removes_empty_and_none(self):
        assert _normalize_and_sort(["apple", "", None, "banana"]) == ["Apple", "Banana"]

    def test_empty_input(self):
        assert _normalize_and_sort([]) == []


# ---------- _combine_and_group ----------

class TestCombineAndGroup:
    def test_groups_by_refno(self):
        items = [
            {"RefNo": "R1", "DateScheduled": "01 Jan 2025", "Item": "A"},
            {"RefNo": "R1", "DateScheduled": "01 Jan 2025", "Item": "B"},
            {"RefNo": "R2", "DateScheduled": "15 Feb 2025", "Item": "C"},
        ]
        result = _combine_and_group(items)
        assert len(result) == 2
        r1 = next(g for g in result if g["RefNo"] == "R1")
        assert len(r1["group_items"]) == 2
        r2 = next(g for g in result if g["RefNo"] == "R2")
        assert len(r2["group_items"]) == 1

    def test_sorts_by_date(self):
        items = [
            {"RefNo": "LATE", "DateScheduled": "15 Mar 2025"},
            {"RefNo": "EARLY", "DateScheduled": "01 Jan 2025"},
        ]
        result = _combine_and_group(items)
        assert result[0]["RefNo"] == "EARLY"
        assert result[1]["RefNo"] == "LATE"

    def test_na_date_sorted_first(self):
        items = [
            {"RefNo": "DATED", "DateScheduled": "01 Jan 2025"},
            {"RefNo": "NODATE", "DateScheduled": "N/A"},
        ]
        result = _combine_and_group(items)
        assert result[0]["RefNo"] == "NODATE"

    def test_invalid_date_treated_as_min(self):
        items = [
            {"RefNo": "GOOD", "DateScheduled": "01 Jan 2025"},
            {"RefNo": "BAD", "DateScheduled": "not-a-date"},
        ]
        result = _combine_and_group(items)
        assert result[0]["RefNo"] == "BAD"

    def test_empty_input(self):
        assert _combine_and_group([]) == []


# ---------- _make_customer_name ----------

class TestMakeCustomerName:
    def test_both_same(self):
        assert _make_customer_name("Acme", "Acme") == "Acme"

    def test_both_different(self):
        assert _make_customer_name("DD Co", "CBR Co") == "DD Co / CBR Co"

    def test_dd_only(self):
        assert _make_customer_name("DD Co", "") == "DD Co"

    def test_cbr_only(self):
        assert _make_customer_name("", "CBR Co") == "CBR Co"

    def test_dd_none_cbr_set(self):
        assert _make_customer_name(None, "CBR Co") == "CBR Co"

    def test_both_empty(self):
        assert _make_customer_name("", "") == ""


# ---------- _to_list_of_dicts ----------

class TestToListOfDicts:
    def test_none(self):
        assert _to_list_of_dicts(None) == []

    def test_list_passthrough(self):
        data = [{"a": 1}]
        assert _to_list_of_dicts(data) is data

    def test_dict_with_data_key(self):
        assert _to_list_of_dicts({"data": [{"a": 1}]}) == [{"a": 1}]

    def test_dict_with_rows_key(self):
        assert _to_list_of_dicts({"rows": [{"a": 1}]}) == [{"a": 1}]

    def test_single_dict_wrapped(self):
        assert _to_list_of_dicts({"a": 1, "b": 2}) == [{"a": 1, "b": 2}]

    def test_tuple(self):
        assert _to_list_of_dicts(({"a": 1},)) == [{"a": 1}]

    def test_generator(self):
        gen = (x for x in [{"a": 1}, {"b": 2}])
        assert _to_list_of_dicts(gen) == [{"a": 1}, {"b": 2}]


# ---------- build_eta_report_context ----------

@pytest.fixture
def report_db(tmp_path):
    """In-memory DB with customers table seeded."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("""
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            dd_name TEXT,
            cbr_name TEXT,
            obfuscated_id TEXT UNIQUE,
            field_type TEXT DEFAULT 'Customer Name',
            display_name TEXT
        )
    """)
    db.execute(
        "INSERT INTO customers (dd_name, cbr_name, obfuscated_id, field_type, display_name) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Acme DD", "Acme CBR", "a" * 32, "Customer Name", "Acme"),
    )
    db.commit()
    return db


def test_build_context_customer_not_found(report_db):
    template, ctx, status = build_eta_report_context("b" * 32, db=report_db)
    assert template == "404.html"
    assert status == 404


def test_build_context_happy_path(report_db, monkeypatch):
    fake_orders = {"data": [
        {"RefNo": "R1", "DateScheduled": "01 Jan 2025",
         "ProductionStatus": "Open", "ProductionLine": "Cutting",
         "Instance": "DD"},
    ], "source": "live"}

    monkeypatch.setattr(
        "services.eta_report.get_open_orders",
        lambda conn, name, inst: fake_orders,
    )

    template, ctx, status = build_eta_report_context("a" * 32, db=report_db)
    assert template == "report.html"
    assert status == 200
    assert ctx["customer_name"] == "Acme"
    assert ctx["source"] == "live"
    assert ctx["obfuscated_id"] == "a" * 32
    assert len(ctx["data"]) == 1  # one group (R1)
    assert "Open" in ctx["statuses"]


def test_build_context_cached_source(report_db, monkeypatch):
    fake_dd = {"data": [{"RefNo": "R1", "DateScheduled": "01 Jan 2025",
                         "ProductionStatus": "Open", "ProductionLine": "Cut",
                         "Instance": "DD"}], "source": "cache-503"}
    fake_cbr = {"data": [], "source": "live"}

    monkeypatch.setattr("services.eta_report.get_open_orders",
                        lambda conn, name, inst: fake_dd if inst == "DD" else fake_cbr)

    _, ctx, _ = build_eta_report_context("a" * 32, db=report_db)
    assert ctx["source"] == "cache-503"


def test_build_context_no_orders(report_db, monkeypatch):
    monkeypatch.setattr("services.eta_report.get_open_orders",
                        lambda conn, name, inst: {"data": [], "source": "live"})

    _, ctx, status = build_eta_report_context("a" * 32, db=report_db)
    assert status == 200
    assert ctx["data"] is None  # no data

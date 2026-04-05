"""Tests for services/update_status_mapping.py — populate, get, edit mappings."""
import sqlite3
import pytest
from services.update_status_mapping import (
    populate_status_mapping_table,
    get_status_mappings,
    get_status_mapping,
    edit_status_mapping,
    ensure_status_mapping_table,
)


@pytest.fixture
def mapping_db():
    """In-memory DB with status_mapping table ready."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE status_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            odata_status TEXT UNIQUE NOT NULL,
            custom_status TEXT,
            active BOOLEAN NOT NULL DEFAULT TRUE
        )
    """)
    conn.commit()
    return conn


# ---------- populate_status_mapping_table ----------

def test_populate_inserts_statuses(mapping_db, monkeypatch):
    monkeypatch.setattr(
        "services.update_status_mapping.get_statuses",
        lambda inst: {"data": ["Open", "Closed"] if inst == "DD" else ["Open", "Shipped"]},
    )
    populate_status_mapping_table(mapping_db)

    rows = mapping_db.execute("SELECT odata_status, active FROM status_mapping ORDER BY odata_status").fetchall()
    statuses = {r["odata_status"] for r in rows}
    assert statuses == {"Closed", "Open", "Shipped"}
    assert all(r["active"] for r in rows)


def test_populate_marks_missing_inactive(mapping_db, monkeypatch):
    """Statuses that were in the table but not in OData get marked inactive."""
    # Seed an existing status
    mapping_db.execute(
        "INSERT INTO status_mapping (odata_status, custom_status, active) VALUES (?, ?, TRUE)",
        ("OldStatus", "OldStatus"),
    )
    mapping_db.commit()

    monkeypatch.setattr(
        "services.update_status_mapping.get_statuses",
        lambda inst: {"data": ["NewStatus"]},
    )
    populate_status_mapping_table(mapping_db)

    old = mapping_db.execute(
        "SELECT active FROM status_mapping WHERE odata_status = ?", ("OldStatus",)
    ).fetchone()
    assert old["active"] == 0  # marked inactive

    new = mapping_db.execute(
        "SELECT active FROM status_mapping WHERE odata_status = ?", ("NewStatus",)
    ).fetchone()
    assert new["active"] == 1


def test_populate_preserves_custom_status(mapping_db, monkeypatch):
    """Re-populating doesn't overwrite custom_status on existing rows."""
    mapping_db.execute(
        "INSERT INTO status_mapping (odata_status, custom_status, active) VALUES (?, ?, TRUE)",
        ("Open", "My Custom Name"),
    )
    mapping_db.commit()

    monkeypatch.setattr(
        "services.update_status_mapping.get_statuses",
        lambda inst: {"data": ["Open"]},
    )
    populate_status_mapping_table(mapping_db)

    row = mapping_db.execute(
        "SELECT custom_status FROM status_mapping WHERE odata_status = ?", ("Open",)
    ).fetchone()
    assert row["custom_status"] == "My Custom Name"


def test_populate_handles_empty_odata(mapping_db, monkeypatch):
    """Empty OData response marks all existing statuses inactive."""
    mapping_db.execute(
        "INSERT INTO status_mapping (odata_status, custom_status, active) VALUES (?, ?, TRUE)",
        ("Open", "Open"),
    )
    mapping_db.commit()

    monkeypatch.setattr(
        "services.update_status_mapping.get_statuses",
        lambda inst: {"data": []},
    )
    populate_status_mapping_table(mapping_db)

    row = mapping_db.execute("SELECT active FROM status_mapping WHERE odata_status = ?", ("Open",)).fetchone()
    assert row["active"] == 0


# ---------- get_status_mappings ----------

def test_get_status_mappings_returns_all(mapping_db):
    mapping_db.execute("INSERT INTO status_mapping (odata_status, custom_status, active) VALUES ('A', 'A', TRUE)")
    mapping_db.execute("INSERT INTO status_mapping (odata_status, custom_status, active) VALUES ('B', 'B', FALSE)")
    mapping_db.commit()

    mappings = get_status_mappings(mapping_db)
    assert len(mappings) == 2
    # Active first in ordering
    assert mappings[0]["odata_status"] == "A"


# ---------- get_status_mapping ----------

def test_get_status_mapping_by_id(mapping_db):
    mapping_db.execute("INSERT INTO status_mapping (odata_status, custom_status, active) VALUES ('Open', 'Open', TRUE)")
    mapping_db.commit()

    row = get_status_mapping(1, mapping_db)
    assert row["odata_status"] == "Open"


def test_get_status_mapping_not_found(mapping_db):
    row = get_status_mapping(999, mapping_db)
    assert row is None


# ---------- edit_status_mapping ----------

def test_edit_status_mapping_updates(mapping_db):
    mapping_db.execute("INSERT INTO status_mapping (odata_status, custom_status, active) VALUES ('Open', 'Open', TRUE)")
    mapping_db.commit()

    edit_status_mapping(1, "In Progress", False, mapping_db)

    row = mapping_db.execute("SELECT custom_status, active FROM status_mapping WHERE id = 1").fetchone()
    assert row["custom_status"] == "In Progress"
    assert row["active"] == 0


# ---------- ensure_status_mapping_table ----------

def test_ensure_creates_table_if_missing(monkeypatch):
    """ensure_status_mapping_table creates table when it doesn't exist."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Mock populate to avoid OData calls
    monkeypatch.setattr(
        "services.update_status_mapping.populate_status_mapping_table",
        lambda c: None,
    )
    ensure_status_mapping_table(conn)

    # Table should exist now
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='status_mapping'"
    ).fetchone()
    assert row is not None


def test_ensure_noop_if_table_exists(mapping_db):
    """ensure_status_mapping_table is a no-op if table already exists."""
    # Insert a row to verify it's not dropped/recreated
    mapping_db.execute("INSERT INTO status_mapping (odata_status, custom_status, active) VALUES ('X', 'X', TRUE)")
    mapping_db.commit()

    ensure_status_mapping_table(mapping_db)

    count = mapping_db.execute("SELECT COUNT(*) FROM status_mapping").fetchone()[0]
    assert count == 1  # data preserved

# tests/test_migrations.py
"""
Tests for services/migrations.py

These tests cover the database migration system, version tracking,
backups, and schema evolution.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from services.migrations import (
    _backup_sqlite,
    _get_user_version,
    _set_user_version,
    _column_exists,
    _object_exists,
    _migration_1_init_schema,
    _migration_2_add_field_type,
    _migration_3_baseline_schema,
    _migration_4_customer_to_customer_name,
    run_migrations,
    MIGRATIONS,
    CURRENT_SCHEMA_VERSION,
)


# ---------------------------
# Fixtures
# ---------------------------

@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary SQLite database."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def temp_db_with_path(tmp_path):
    """Create a temporary SQLite database and return both conn and path."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    yield conn, db_path
    conn.close()


# ---------------------------
# Test: Version tracking helpers
# ---------------------------

def test_get_user_version_defaults_to_zero(temp_db):
    """New database has user_version=0."""
    version = _get_user_version(temp_db)
    assert version == 0


def test_set_and_get_user_version(temp_db):
    """Can set and retrieve user_version."""
    _set_user_version(temp_db, 5)
    assert _get_user_version(temp_db) == 5

    _set_user_version(temp_db, 10)
    assert _get_user_version(temp_db) == 10


def test_user_version_persists_across_connections(temp_db_with_path):
    """user_version persists after closing connection."""
    conn, db_path = temp_db_with_path
    _set_user_version(conn, 42)
    conn.close()

    # Reconnect
    conn2 = sqlite3.connect(str(db_path))
    assert _get_user_version(conn2) == 42
    conn2.close()


# ---------------------------
# Test: Column and object existence helpers
# ---------------------------

def test_column_exists_returns_true_for_existing_column(temp_db):
    """_column_exists detects existing columns."""
    temp_db.execute("CREATE TABLE test_table (id INTEGER, name TEXT)")
    assert _column_exists(temp_db, "test_table", "id") is True
    assert _column_exists(temp_db, "test_table", "name") is True


def test_column_exists_returns_false_for_missing_column(temp_db):
    """_column_exists returns False for non-existent columns."""
    temp_db.execute("CREATE TABLE test_table (id INTEGER)")
    assert _column_exists(temp_db, "test_table", "missing_col") is False


def test_column_exists_handles_nonexistent_table(temp_db):
    """_column_exists gracefully handles non-existent tables."""
    # SQLite returns empty result for PRAGMA table_info on non-existent table
    assert _column_exists(temp_db, "nonexistent", "col") is False


def test_object_exists_detects_triggers(temp_db):
    """_object_exists detects triggers."""
    temp_db.execute("CREATE TABLE test (id INTEGER)")
    temp_db.execute("""
        CREATE TRIGGER test_trigger
        AFTER INSERT ON test
        BEGIN SELECT 1; END;
    """)
    assert _object_exists(temp_db, "test_trigger", "trigger") is True


def test_object_exists_returns_false_for_missing_trigger(temp_db):
    """_object_exists returns False for non-existent triggers."""
    assert _object_exists(temp_db, "nonexistent_trigger", "trigger") is False


def test_object_exists_detects_tables(temp_db):
    """_object_exists can detect tables."""
    temp_db.execute("CREATE TABLE my_table (id INTEGER)")
    assert _object_exists(temp_db, "my_table", "table") is True


def test_object_exists_detects_indexes(temp_db):
    """_object_exists can detect indexes."""
    temp_db.execute("CREATE TABLE test (id INTEGER)")
    temp_db.execute("CREATE INDEX test_idx ON test(id)")
    assert _object_exists(temp_db, "test_idx", "index") is True


# ---------------------------
# Test: Backup functionality
# ---------------------------

def test_backup_sqlite_creates_backup_file(temp_db_with_path):
    """_backup_sqlite creates a backup file."""
    conn, db_path = temp_db_with_path
    backup_dir = db_path.parent

    # Create some data
    conn.execute("CREATE TABLE test (id INTEGER)")
    conn.execute("INSERT INTO test VALUES (1)")
    conn.commit()

    backup_path = _backup_sqlite(conn, str(backup_dir))

    # Verify backup exists and is a valid SQLite file
    assert os.path.exists(backup_path)
    assert backup_path.endswith(".sqlite3")

    # Verify backup contains data
    backup_conn = sqlite3.connect(backup_path)
    cursor = backup_conn.execute("SELECT id FROM test")
    assert cursor.fetchone()[0] == 1
    backup_conn.close()


def test_backup_sqlite_uses_default_dir_when_none_provided(temp_db_with_path):
    """_backup_sqlite uses database directory when backup_dir is None."""
    conn, db_path = temp_db_with_path
    conn.execute("CREATE TABLE test (id INTEGER)")
    conn.commit()

    backup_path = _backup_sqlite(conn, backup_dir=None)

    # Should be in same directory as database
    assert Path(backup_path).parent == db_path.parent
    assert os.path.exists(backup_path)


def test_backup_sqlite_filename_includes_timestamp(temp_db_with_path):
    """Backup filename includes timestamp to prevent collisions."""
    conn, db_path = temp_db_with_path
    conn.execute("CREATE TABLE test (id INTEGER)")
    conn.commit()

    backup_path = _backup_sqlite(conn, str(db_path.parent))

    # Filename pattern: db-backup-YYYYMMDD-HHMMSS.sqlite3
    filename = os.path.basename(backup_path)
    assert filename.startswith("db-backup-")
    assert filename.endswith(".sqlite3")
    assert len(filename) == len("db-backup-20250101-123456.sqlite3")


# ---------------------------
# Test: Migration 1 - Init schema
# ---------------------------

def test_migration_1_creates_customers_table(temp_db):
    """Migration 1 creates customers table."""
    _migration_1_init_schema(temp_db)

    # Verify table exists
    cursor = temp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='customers'"
    )
    assert cursor.fetchone() is not None


def test_migration_1_creates_correct_columns(temp_db):
    """Migration 1 creates all required columns."""
    _migration_1_init_schema(temp_db)

    columns = {r[1] for r in temp_db.execute("PRAGMA table_info(customers)")}
    expected = {"id", "dd_name", "cbr_name", "obfuscated_id", "field_type"}
    assert expected.issubset(columns)


def test_migration_1_is_idempotent(temp_db):
    """Running migration 1 twice doesn't error."""
    _migration_1_init_schema(temp_db)
    _migration_1_init_schema(temp_db)  # Should not raise


def test_migration_1_preserves_existing_data(temp_db):
    """Migration 1 doesn't destroy existing data."""
    _migration_1_init_schema(temp_db)
    temp_db.execute(
        "INSERT INTO customers (dd_name, obfuscated_id, field_type) VALUES (?, ?, ?)",
        ("Test", "abc123", "Customer")
    )
    temp_db.commit()

    _migration_1_init_schema(temp_db)

    cursor = temp_db.execute("SELECT dd_name FROM customers WHERE obfuscated_id='abc123'")
    assert cursor.fetchone()[0] == "Test"


# ---------------------------
# Test: Migration 2 - Add field_type
# ---------------------------

def test_migration_2_adds_field_type_column_if_missing(temp_db):
    """Migration 2 adds field_type column if it doesn't exist."""
    # Create table without field_type
    temp_db.execute("""
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            dd_name TEXT,
            cbr_name TEXT,
            obfuscated_id TEXT NOT NULL UNIQUE
        )
    """)

    _migration_2_add_field_type(temp_db)

    assert _column_exists(temp_db, "customers", "field_type")


def test_migration_2_backfills_field_type_to_customer(temp_db):
    """Migration 2 backfills NULL field_type to 'Customer'."""
    temp_db.execute("""
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            dd_name TEXT,
            cbr_name TEXT,
            obfuscated_id TEXT NOT NULL UNIQUE
        )
    """)
    temp_db.execute(
        "INSERT INTO customers (dd_name, obfuscated_id) VALUES (?, ?)",
        ("Acme", "abc123")
    )
    temp_db.commit()

    _migration_2_add_field_type(temp_db)

    cursor = temp_db.execute("SELECT field_type FROM customers WHERE obfuscated_id='abc123'")
    assert cursor.fetchone()[0] == "Customer"


def test_migration_2_creates_insert_trigger(temp_db):
    """Migration 2 creates insert validation trigger."""
    _migration_1_init_schema(temp_db)
    _migration_2_add_field_type(temp_db)

    assert _object_exists(temp_db, "customers_field_type_check_insert", "trigger")


def test_migration_2_creates_update_trigger(temp_db):
    """Migration 2 creates update validation trigger."""
    _migration_1_init_schema(temp_db)
    _migration_2_add_field_type(temp_db)

    assert _object_exists(temp_db, "customers_field_type_check_update", "trigger")


def test_migration_2_triggers_reject_invalid_field_type_on_insert(temp_db):
    """Triggers created by migration 2 reject invalid field_type."""
    _migration_1_init_schema(temp_db)
    _migration_2_add_field_type(temp_db)

    with pytest.raises(sqlite3.IntegrityError, match="invalid field_type"):
        temp_db.execute(
            "INSERT INTO customers (dd_name, obfuscated_id, field_type) VALUES (?, ?, ?)",
            ("Test", "xyz", "InvalidType")
        )


def test_migration_2_triggers_allow_valid_field_types(temp_db):
    """Triggers allow 'Customer' and 'Customer Group'."""
    _migration_1_init_schema(temp_db)
    _migration_2_add_field_type(temp_db)

    temp_db.execute(
        "INSERT INTO customers (dd_name, obfuscated_id, field_type) VALUES (?, ?, ?)",
        ("Acme", "abc", "Customer")
    )
    temp_db.execute(
        "INSERT INTO customers (dd_name, obfuscated_id, field_type) VALUES (?, ?, ?)",
        ("Group1", "def", "Customer Group")
    )
    temp_db.commit()

    cursor = temp_db.execute("SELECT COUNT(*) FROM customers")
    assert cursor.fetchone()[0] == 2


def test_migration_2_is_idempotent(temp_db):
    """Running migration 2 twice doesn't error."""
    _migration_1_init_schema(temp_db)
    _migration_2_add_field_type(temp_db)
    _migration_2_add_field_type(temp_db)  # Should not raise


# ---------------------------
# Test: Migration 3 - Baseline schema
# ---------------------------

def test_migration_3_passes_with_complete_schema(temp_db):
    """Migration 3 passes when all required columns exist."""
    _migration_1_init_schema(temp_db)
    _migration_2_add_field_type(temp_db)
    _migration_3_baseline_schema(temp_db)  # Should not raise


def test_migration_3_fails_with_incomplete_schema(temp_db):
    """Migration 3 raises error if required columns are missing."""
    temp_db.execute("""
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            dd_name TEXT
        )
    """)

    with pytest.raises(RuntimeError, match="customers missing columns"):
        _migration_3_baseline_schema(temp_db)


def test_migration_3_error_message_lists_missing_columns(temp_db):
    """Migration 3 error message lists all missing columns."""
    temp_db.execute("""
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            dd_name TEXT
        )
    """)

    try:
        _migration_3_baseline_schema(temp_db)
        assert False, "Should have raised"
    except RuntimeError as e:
        error_msg = str(e)
        assert "cbr_name" in error_msg
        assert "obfuscated_id" in error_msg
        assert "field_type" in error_msg


# ---------------------------
# Test: Migration 4 - Customer to Customer Name
# ---------------------------

def test_migration_4_changes_default_to_customer_name(temp_db):
    """Migration 4 changes default field_type to 'Customer Name'."""
    _migration_1_init_schema(temp_db)
    _migration_2_add_field_type(temp_db)
    _migration_4_customer_to_customer_name(temp_db)

    # Insert without specifying field_type
    temp_db.execute(
        "INSERT INTO customers (dd_name, obfuscated_id) VALUES (?, ?)",
        ("Test", "xyz")
    )
    temp_db.commit()

    cursor = temp_db.execute("SELECT field_type FROM customers WHERE obfuscated_id='xyz'")
    assert cursor.fetchone()[0] == "Customer Name"


def test_migration_4_migrates_customer_to_customer_name(temp_db):
    """Migration 4 converts 'Customer' to 'Customer Name'."""
    _migration_1_init_schema(temp_db)
    _migration_2_add_field_type(temp_db)

    # Insert with old 'Customer' type
    temp_db.execute(
        "INSERT INTO customers (dd_name, obfuscated_id, field_type) VALUES (?, ?, ?)",
        ("Acme", "abc", "Customer")
    )
    temp_db.commit()

    _migration_4_customer_to_customer_name(temp_db)

    cursor = temp_db.execute("SELECT field_type FROM customers WHERE obfuscated_id='abc'")
    assert cursor.fetchone()[0] == "Customer Name"


def test_migration_4_preserves_customer_group(temp_db):
    """Migration 4 preserves 'Customer Group' type."""
    _migration_1_init_schema(temp_db)
    _migration_2_add_field_type(temp_db)

    temp_db.execute(
        "INSERT INTO customers (dd_name, obfuscated_id, field_type) VALUES (?, ?, ?)",
        ("Group1", "grp1", "Customer Group")
    )
    temp_db.commit()

    _migration_4_customer_to_customer_name(temp_db)

    cursor = temp_db.execute("SELECT field_type FROM customers WHERE obfuscated_id='grp1'")
    assert cursor.fetchone()[0] == "Customer Group"


def test_migration_4_recreates_triggers_with_new_values(temp_db):
    """Migration 4 recreates triggers to validate new field_type values."""
    _migration_1_init_schema(temp_db)
    _migration_2_add_field_type(temp_db)
    _migration_4_customer_to_customer_name(temp_db)

    # Old 'Customer' should now be invalid
    with pytest.raises(sqlite3.IntegrityError, match="invalid field_type"):
        temp_db.execute(
            "INSERT INTO customers (dd_name, obfuscated_id, field_type) VALUES (?, ?, ?)",
            ("Test", "fail", "Customer")
        )

    # New values should work
    temp_db.execute(
        "INSERT INTO customers (dd_name, obfuscated_id, field_type) VALUES (?, ?, ?)",
        ("Valid1", "v1", "Customer Name")
    )
    temp_db.execute(
        "INSERT INTO customers (dd_name, obfuscated_id, field_type) VALUES (?, ?, ?)",
        ("Valid2", "v2", "Customer Group")
    )
    temp_db.commit()

    cursor = temp_db.execute("SELECT COUNT(*) FROM customers")
    assert cursor.fetchone()[0] == 2


def test_migration_4_preserves_data_integrity(temp_db):
    """Migration 4 preserves all customer data."""
    _migration_1_init_schema(temp_db)
    _migration_2_add_field_type(temp_db)

    # Insert test data
    customers = [
        ("Acme DD", "Acme CBR", "abc1", "Customer"),
        ("Bravo DD", "Bravo CBR", "abc2", "Customer Group"),
        ("Charlie DD", "", "abc3", "Customer"),
    ]
    for dd, cbr, obf_id, ft in customers:
        temp_db.execute(
            "INSERT INTO customers (dd_name, cbr_name, obfuscated_id, field_type) VALUES (?, ?, ?, ?)",
            (dd, cbr, obf_id, ft)
        )
    temp_db.commit()

    _migration_4_customer_to_customer_name(temp_db)

    # Verify all data preserved
    cursor = temp_db.execute("SELECT dd_name, cbr_name, obfuscated_id FROM customers ORDER BY obfuscated_id")
    rows = cursor.fetchall()
    assert len(rows) == 3
    assert rows[0][0] == "Acme DD"
    assert rows[1][0] == "Bravo DD"
    assert rows[2][0] == "Charlie DD"


# ---------------------------
# Test: run_migrations integration
# ---------------------------

def test_run_migrations_from_version_zero(temp_db):
    """run_migrations applies all migrations from v0."""
    run_migrations(temp_db, make_backup=False)

    assert _get_user_version(temp_db) == CURRENT_SCHEMA_VERSION
    assert _object_exists(temp_db, "customers", "table")
    assert _column_exists(temp_db, "customers", "field_type")


def test_run_migrations_creates_backup(temp_db_with_path):
    """run_migrations creates backup before applying migrations."""
    conn, db_path = temp_db_with_path
    backup_dir = db_path.parent

    # Create initial data (run some migrations first)
    _migration_1_init_schema(conn)
    _migration_2_add_field_type(conn)
    _set_user_version(conn, 2)
    conn.commit()

    run_migrations(conn, make_backup=True)

    # Check backup exists
    backups = list(backup_dir.glob("db-backup-*.sqlite3"))
    assert len(backups) >= 1


def test_run_migrations_skips_backup_when_disabled(temp_db_with_path):
    """run_migrations skips backup when make_backup=False."""
    conn, db_path = temp_db_with_path
    backup_dir = db_path.parent

    run_migrations(conn, make_backup=False)

    # No backups should be created
    backups = list(backup_dir.glob("db-backup-*.sqlite3"))
    assert len(backups) == 0


def test_run_migrations_is_idempotent(temp_db):
    """Running migrations twice doesn't break anything."""
    run_migrations(temp_db, make_backup=False)
    version_after_first = _get_user_version(temp_db)

    run_migrations(temp_db, make_backup=False)
    version_after_second = _get_user_version(temp_db)

    assert version_after_first == version_after_second


def test_run_migrations_skips_already_applied(temp_db):
    """run_migrations only applies pending migrations."""
    # Apply first 2 migrations manually
    _migration_1_init_schema(temp_db)
    _migration_2_add_field_type(temp_db)
    _set_user_version(temp_db, 2)
    temp_db.commit()

    # Now run_migrations should only apply v3 and higher
    run_migrations(temp_db, make_backup=False)

    # Should have jumped from v2 to CURRENT_SCHEMA_VERSION
    assert _get_user_version(temp_db) == CURRENT_SCHEMA_VERSION


def test_run_migrations_rolls_back_on_error(temp_db):
    """run_migrations rolls back if a migration fails."""
    # Set version to 2 so migration 3 will run
    _set_user_version(temp_db, 2)

    # Migration 3 will fail because customers table is incomplete
    with pytest.raises(RuntimeError, match="customers missing columns"):
        run_migrations(temp_db, make_backup=False)

    # Version should remain at 2 (rollback)
    assert _get_user_version(temp_db) == 2


def test_run_migrations_with_logger(temp_db):
    """run_migrations logs progress when logger provided."""
    logs = []

    class MockLogger:
        def info(self, msg, *args):
            logs.append(("info", msg % args if args else msg))

        def warning(self, msg, *args):
            logs.append(("warning", msg % args if args else msg))

        def exception(self, msg, *args):
            logs.append(("exception", msg % args if args else msg))

    run_migrations(temp_db, make_backup=False, logger=MockLogger())

    # Should have logged current version, applying, and complete messages
    log_messages = [msg for level, msg in logs]
    assert any("current user_version" in msg for msg in log_messages)
    assert any("Applying DB migration" in msg or "complete" in msg for msg in log_messages)


def test_run_migrations_handles_backup_failure_gracefully(temp_db, monkeypatch):
    """run_migrations continues if backup fails."""
    def broken_backup(conn, backup_dir=None):
        raise RuntimeError("Backup failed!")

    monkeypatch.setattr("services.migrations._backup_sqlite", broken_backup)

    logs = []

    class MockLogger:
        def info(self, msg, *args): pass
        def warning(self, msg, *args):
            logs.append(msg % args if args else msg)
        def exception(self, msg, *args): pass

    # Should not raise, but should warn
    run_migrations(temp_db, make_backup=True, logger=MockLogger())

    assert any("Backup failed" in log for log in logs)
    assert _get_user_version(temp_db) == CURRENT_SCHEMA_VERSION


def test_migrations_list_is_sequential(temp_db):
    """MIGRATIONS list has sequential version numbers."""
    versions = [v for v, _ in MIGRATIONS]
    expected = list(range(1, len(MIGRATIONS) + 1))
    assert versions == expected


def test_current_schema_version_matches_last_migration(temp_db):
    """CURRENT_SCHEMA_VERSION equals the highest migration version."""
    max_version = max(v for v, _ in MIGRATIONS)
    assert CURRENT_SCHEMA_VERSION == max_version


# ---------------------------
# Test: Full migration path
# ---------------------------

def test_full_migration_path_creates_working_schema(temp_db):
    """Running all migrations from scratch creates a working database."""
    run_migrations(temp_db, make_backup=False)

    # Should be able to insert valid customers
    temp_db.execute(
        "INSERT INTO customers (dd_name, cbr_name, obfuscated_id, field_type) VALUES (?, ?, ?, ?)",
        ("Acme DD", "Acme CBR", "abc123", "Customer Name")
    )
    temp_db.execute(
        "INSERT INTO customers (dd_name, cbr_name, obfuscated_id, field_type) VALUES (?, ?, ?, ?)",
        ("Group1", "Group1", "grp456", "Customer Group")
    )
    temp_db.commit()

    # Verify data
    cursor = temp_db.execute("SELECT COUNT(*) FROM customers")
    assert cursor.fetchone()[0] == 2

    # Verify triggers work
    with pytest.raises(sqlite3.IntegrityError):
        temp_db.execute(
            "INSERT INTO customers (dd_name, obfuscated_id, field_type) VALUES (?, ?, ?)",
            ("Bad", "bad1", "InvalidType")
        )

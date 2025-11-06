# services/migrations.py
from __future__ import annotations

import os
from datetime import datetime
from typing import Callable, List, Tuple


def _backup_sqlite(conn, backup_dir: str | None = None) -> str:
    if backup_dir is None:
        backup_dir = os.path.dirname(conn.execute("PRAGMA database_list").fetchone()[2])
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(backup_dir, f"db-backup-{ts}.sqlite3")
    conn.execute(f"VACUUM INTO '{path}'")
    return path


def _get_user_version(conn) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _set_user_version(conn, v: int) -> None:
    conn.execute(f"PRAGMA user_version = {v}")


def _column_exists(conn, table: str, column: str) -> bool:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return column in cols


def _object_exists(conn, name: str, obj_type: str = "trigger") -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = ? AND name = ?",
        (obj_type, name),
    ).fetchone()
    return row is not None


# ---------------------------
# v1: init schema (idempotent)
# - Makes a fresh dev DB usable if file was deleted
# - If table already exists, this is a no-op
# ---------------------------
def _migration_1_init_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dd_name TEXT,
            cbr_name TEXT,
            obfuscated_id TEXT NOT NULL UNIQUE,
            field_type TEXT NOT NULL DEFAULT 'Customer'
        )
    """)


# ---------------------------
# v2: add field_type + triggers (idempotent)
# - If prod DB lacks column, add it and backfill
# - (Re)create triggers if missing
# ---------------------------
def _migration_2_add_field_type(conn) -> None:
    if not _column_exists(conn, "customers", "field_type"):
        conn.execute("""
            ALTER TABLE customers
            ADD COLUMN field_type TEXT NOT NULL DEFAULT 'Customer';
        """)
        conn.execute("""
            UPDATE customers
               SET field_type = 'Customer'
             WHERE field_type IS NULL OR TRIM(field_type) = '';
        """)

    if not _object_exists(conn, "customers_field_type_check_insert", "trigger"):
        conn.execute("""
            CREATE TRIGGER customers_field_type_check_insert
            BEFORE INSERT ON customers
            FOR EACH ROW
            WHEN NEW.field_type NOT IN ('Customer','Customer Group')
            BEGIN
                SELECT RAISE(ABORT, 'invalid field_type');
            END;
        """)

    if not _object_exists(conn, "customers_field_type_check_update", "trigger"):
        conn.execute("""
            CREATE TRIGGER customers_field_type_check_update
            BEFORE UPDATE OF field_type ON customers
            FOR EACH ROW
            WHEN NEW.field_type NOT IN ('Customer','Customer Group')
            BEGIN
                SELECT RAISE(ABORT, 'invalid field_type');
            END;
        """)


# ---------------------------
# v3: baseline (assert columns; triggers are optional)
# - Records that the DB is now at the expected shape
# ---------------------------
def _migration_3_baseline_schema(conn) -> None:
    required = {"id", "dd_name", "cbr_name", "obfuscated_id", "field_type"}
    cols = {r[1] for r in conn.execute("PRAGMA table_info(customers)")}

    missing = required - cols
    if missing:
        raise RuntimeError(f"Baseline mismatch: customers missing columns: {sorted(missing)}")
    # We *could* assert triggers here, but they’re added in v2; don’t fail if absent.


def _migration_4_customer_to_customer_name(conn) -> None:
    # Turn off FK checks during the swap (ok even if you don't have FKs)
    conn.execute("PRAGMA foreign_keys = OFF;")

    conn.executescript("""
        -- Drop old triggers (table is about to be rebuilt)
        DROP TRIGGER IF EXISTS customers_field_type_check_insert;
        DROP TRIGGER IF EXISTS customers_field_type_check_update;

        -- Rebuild table with new default
        CREATE TABLE customers_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dd_name TEXT,
            cbr_name TEXT,
            obfuscated_id TEXT NOT NULL UNIQUE,
            field_type TEXT NOT NULL DEFAULT 'Customer Name'
        );

        -- Copy data, coercing legacy/null/blank to the new default
        INSERT INTO customers_new (id, dd_name, cbr_name, obfuscated_id, field_type)
        SELECT
            id, dd_name, cbr_name, obfuscated_id,
            CASE
                WHEN field_type IS NULL OR TRIM(field_type) = '' OR field_type = 'Customer'
                    THEN 'Customer Name'
                ELSE field_type
            END
        FROM customers;

        -- Swap tables
        DROP TABLE customers;
        ALTER TABLE customers_new RENAME TO customers;

        -- Recreate triggers with the new allowed set
        CREATE TRIGGER customers_field_type_check_insert
        BEFORE INSERT ON customers
        FOR EACH ROW
        WHEN NEW.field_type NOT IN ('Customer Name', 'Customer Group')
        BEGIN
          SELECT RAISE(ABORT, 'invalid field_type');
        END;

        CREATE TRIGGER customers_field_type_check_update
        BEFORE UPDATE OF field_type ON customers
        FOR EACH ROW
        WHEN NEW.field_type NOT IN ('Customer Name', 'Customer Group')
        BEGIN
          SELECT RAISE(ABORT, 'invalid field_type');
        END;
    """)

    # Restore AUTOINCREMENT sequence (optional, but keeps ids monotonic)
    conn.execute("""
        UPDATE sqlite_sequence
           SET seq = (SELECT COALESCE(MAX(id), 0) FROM customers)
         WHERE name = 'customers';
    """)

    conn.execute("PRAGMA foreign_keys = ON;")


def _migration_5_add_display_name(conn) -> None:
    """Add display_name column to customers table."""
    if not _column_exists(conn, "customers", "display_name"):
        conn.execute("""
            ALTER TABLE customers
            ADD COLUMN display_name TEXT;
        """)
        # Backfill with computed name (dd_name or cbr_name)
        conn.execute("""
            UPDATE customers
               SET display_name = COALESCE(dd_name, cbr_name, '')
             WHERE display_name IS NULL OR display_name = '';
        """)


def _migration_6_fix_display_name_priority(conn) -> None:
    """Fix display_name to prioritize cbr_name over dd_name, and ensure all rows have display_name."""
    # Update all rows where display_name is NULL, empty, or should be recalculated
    # Priority: cbr_name first, then dd_name, then empty string
    conn.execute("""
        UPDATE customers
           SET display_name = COALESCE(cbr_name, dd_name, '')
         WHERE display_name IS NULL OR TRIM(display_name) = '';
    """)


MIGRATIONS: List[Tuple[int, Callable]] = [
    (1, _migration_1_init_schema),
    (2, _migration_2_add_field_type),
    (3, _migration_3_baseline_schema),
    (4, _migration_4_customer_to_customer_name),
    (5, _migration_5_add_display_name),
    (6, _migration_6_fix_display_name_priority),
]

CURRENT_SCHEMA_VERSION = max(v for v, _ in MIGRATIONS)


def run_migrations(conn, *, make_backup: bool = True, logger=None) -> None:
    cur_version = _get_user_version(conn)
    if logger:
        logger.info("DB migrations: current user_version=%s", cur_version)

    pending = [(v, m) for (v, m) in MIGRATIONS if v > cur_version]
    if not pending:
        if logger:
            logger.info("DB migrations: up-to-date.")
        return

    if make_backup:
        try:
            path = _backup_sqlite(conn)
            if logger: logger.info("DB backup created at %s", path)
        except Exception as e:
            if logger: logger.warning("Backup failed (%s). Proceeding without backup.", e)

    for v, migration in sorted(pending, key=lambda x: x[0]):
        if logger:
            logger.info("Applying DB migration v%s ...", v)
        conn.execute("BEGIN IMMEDIATE")
        try:
            migration(conn)
            _set_user_version(conn, v)
            conn.commit()
            if logger:
                logger.info("Migration v%s complete.", v)
        except Exception:
            conn.rollback()
            if logger:
                logger.exception("Migration v%s failed; rolled back.", v)
            raise

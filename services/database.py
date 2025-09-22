import os
import sqlite3
from flask import current_app, g, has_app_context


def _raise_in_dev() -> bool:
    # raise if we’re in a Flask app context and dev wants hard failures
    return has_app_context() and (
        getattr(current_app, "debug", False)
        or current_app.config.get("RAISE_ON_DB_ERROR", False)
    )


def _resolve_db_path():
    # Prefer Flask config when available
    if has_app_context() and current_app.config.get("DATABASE"):
        return current_app.config["DATABASE"]
    # Fallback for scripts run outside app context
    name = os.getenv("DATABASE_PATH", "customers.db")
    if os.path.isabs(name):
        return name
    # Anchor relative fallback to this repo (project) root
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(project_root, name)


# Initialize the database connection using Flask's `g`
def get_db():
    if "db" not in g:
        import sqlite3
        conn = sqlite3.connect(_resolve_db_path())
        conn.row_factory = sqlite3.Row
        # (optional hardening)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        g.db = conn
    return g.db


def update_status_mapping(odata_statuses, conn=None):
    # Mark old statuses as inactive
    execute_query('''
    UPDATE status_mapping 
    SET active = FALSE 
    WHERE odata_status NOT IN (
        SELECT odata_status FROM (VALUES {}) 
    );
    '''.format(', '.join(f"('{s}')" for s in odata_statuses)), conn=conn)

    # Insert new or reactivate existing statuses
    for status in odata_statuses:
        execute_query('''
        INSERT INTO status_mapping (odata_status, active) 
        VALUES (?, TRUE)
        ON CONFLICT (odata_status) DO UPDATE SET active = TRUE;
        ''', args=(status,), conn=conn)


def create_db_tables(conn=None):
    execute_query('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            name TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            active INTEGER NOT NULL DEFAULT 1
        )
    ''', conn=conn)

    execute_query('''
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dd_name TEXT,
            cbr_name TEXT,
            obfuscated_id TEXT NOT NULL UNIQUE,
            field_type TEXT
        )
    ''', conn=conn)

    execute_query('''
        ALTER TABLE customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dd_name TEXT,
            cbr_name TEXT,
            obfuscated_id TEXT NOT NULL UNIQUE,
            field_type TEXT
        )
    ''', conn=conn)

    execute_query('''
    CREATE TABLE IF NOT EXISTS status_mapping (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        odata_status TEXT UNIQUE NOT NULL,
        custom_status TEXT,
        active BOOLEAN NOT NULL DEFAULT TRUE
    );
    ''', conn=conn)
    print("Database tables created successfully")


def query_db(query, args=(), one=False, logger=None, conn=None):
    if conn is None:
        conn = get_db()
    cur = None
    try:
        cur = conn.execute(query, args)
        rows = cur.fetchall()

        # commit only for writes
        first_kw = query.lstrip().split(None, 1)[0].upper() if query else ""
        if first_kw in {"INSERT","UPDATE","DELETE","REPLACE","CREATE","DROP","ALTER"}:
            conn.commit()

        if one:
            return rows[0] if rows else None
        return rows or []                # <- never None for multi-row
    except sqlite3.Error as e:
        (logger or current_app.logger).exception(
            f"DB error running: {query!r} args={args!r}"
        )
        if _raise_in_dev():
            raise
        return None if one else []

    finally:
        if cur is not None:
            try:
                cur.close()
            except:
                pass


def execute_query(query, args=(), conn=None, logger=None):
    if conn is None:
        conn = get_db()
    cur = None
    try:
        cur = conn.execute(query, args)
        conn.commit()
    except sqlite3.Error as e:
        (logger or current_app.logger).exception(
            f"DB write error: {query!r} args={args!r}"
        )
        if _raise_in_dev():
            raise
        # bubble up as a clean app-level error if you prefer:
        # raise ValueError("Integrity/DB error") from e
    finally:
        if cur is not None:
            try:
                cur.close()
            except:
                pass

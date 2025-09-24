import os
import sqlite3
from flask import current_app, g, has_app_context


def _raise_in_dev() -> bool:
    # raise if we’re in a Flask app context and dev wants hard failures
    return has_app_context() and (
        getattr(current_app, "debug", False)
        or current_app.config.get("RAISE_ON_DB_ERROR", False)
    )


# services/database.py
import os
from flask import current_app, has_app_context


def _resolve_db_path():
    if has_app_context():
        name = (current_app.config.get("DATABASE")
                or current_app.config.get("DATABASE_FILE")
                or "customers.db")
        # join to instance folder unless absolute
        return name if os.path.isabs(name) else os.path.join(current_app.instance_path, name)
    # (only for scripts run completely outside Flask)
    name = os.getenv("DATABASE_PATH", "customers.db")
    return name if os.path.isabs(name) else os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")), name)


# Initialize the database connection using Flask's `g`
def update_status_mapping(odata_statuses, conn=None):
    conn = conn or get_db()
    statuses = list(dict.fromkeys(odata_statuses or []))

    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        conn.execute(f"UPDATE status_mapping SET active = 0 WHERE odata_status NOT IN ({placeholders})", statuses)
    else:
        # If nothing from OData, mark all inactive
        conn.execute("UPDATE status_mapping SET active = 0")

    conn.executemany(
        "INSERT INTO status_mapping (odata_status, active) VALUES (?, 1) "
        "ON CONFLICT(odata_status) DO UPDATE SET active = 1",
        [(s,) for s in statuses]
    )
    conn.commit()


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
    CREATE TABLE IF NOT EXISTS status_mapping (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        odata_status TEXT UNIQUE NOT NULL,
        custom_status TEXT,
        active BOOLEAN NOT NULL DEFAULT TRUE
    );
    ''', conn=conn)

    execute_query('''
    CREATE TABLE IF NOT EXISTS jobs (
      id TEXT PRIMARY KEY,
      status TEXT NOT NULL,           -- running/completed/failed
      pct INTEGER NOT NULL DEFAULT 0,
      log TEXT NOT NULL DEFAULT '[]', -- json array of strings
      error TEXT,
      result TEXT,                    -- json
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    ''', conn=conn)

    print("Database tables created successfully")


# Initialize the database connection using Flask's `g`
def get_db():
    if has_app_context():
        if 'db' not in g:
            conn = sqlite3.connect(_resolve_db_path(), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            g.db = conn
        return g.db
    else:
        # Background thread, return a standalone connection
        conn = sqlite3.connect(_resolve_db_path(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn


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

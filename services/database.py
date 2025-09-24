import os
import sqlite3
from flask import g, has_app_context


# Database file path
DB_PATH = os.getenv("DATABASE_PATH", "customers.db")


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
        ''', args=(status,), conn=None)


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
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            g.db = conn
        return g.db
    else:
        # Background thread, return a standalone connection
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn


def query_db(query, args=(), one=False, logger=None, conn=None):
    if conn is None:
        conn = get_db()  # get_db() is called at runtime, ensuring proper context

    try:
        cur = conn.cursor()
        cur.execute(query, args)
        rv = cur.fetchall()
        conn.commit()
        return (rv[0] if rv else None) if one else rv
    except sqlite3.Error as e:
        if logger:
            logger.error(f"Database error: {e}")
        return None


def execute_query(query, args=(), conn=None):
    if conn is None:
        conn = get_db()  # get_db() is called at runtime, ensuring proper context

    try:
        cur = conn.cursor()
        cur.execute(query, args)
        conn.commit()
    except sqlite3.IntegrityError as e:
        raise ValueError("Integrity error") from e

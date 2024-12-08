import sqlite3


def create_status_mapping_table(conn):
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS status_mapping (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        odata_status TEXT UNIQUE NOT NULL,
        custom_status TEXT,
        active BOOLEAN NOT NULL DEFAULT TRUE
    );
    ''')
    conn.commit()


def update_status_mapping(conn, odata_statuses):
    cursor = conn.cursor()

    # Mark old statuses as inactive
    cursor.execute('''
    UPDATE status_mapping 
    SET active = FALSE 
    WHERE odata_status NOT IN (
        SELECT odata_status FROM (VALUES {}) 
    );
    '''.format(', '.join(f"('{s}')" for s in odata_statuses)))

    # Insert new or reactivate existing statuses
    for status in odata_statuses:
        cursor.execute('''
        INSERT INTO status_mapping (odata_status, active) 
        VALUES (?, TRUE)
        ON CONFLICT (odata_status) DO UPDATE SET active = TRUE;
        ''', (status,))

    conn.commit()

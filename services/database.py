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


def create_db_tables(conn):
    cur = conn.cursor()

    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            name TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            active INTEGER NOT NULL DEFAULT 1
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dd_name TEXT,
            cbr_name TEXT,
            obfuscated_id TEXT NOT NULL UNIQUE
        )
    ''')

    cur.execute('''
    CREATE TABLE IF NOT EXISTS status_mapping (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        odata_status TEXT UNIQUE NOT NULL,
        custom_status TEXT,
        active BOOLEAN NOT NULL DEFAULT TRUE
    );
    ''')
    print("Database tables created successfully")
    conn.commit()

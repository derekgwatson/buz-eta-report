from services.buz_data import get_statuses


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

    # Insert any new statuses into the `status_mapping` table
    for status in odata_statuses:
        cursor.execute('''
        INSERT OR IGNORE INTO status_mapping (odata_status, active)
        VALUES (?, TRUE);
        ''', (status,))

    conn.commit()


def get_status_mappings(conn):
    ensure_status_mapping_table(conn)
    cursor = conn.cursor()
    cursor.execute('''
    SELECT id, odata_status, custom_status, active 
    FROM status_mapping ORDER BY active DESC, odata_status;
    ''')
    mappings = cursor.fetchall()
    conn.close()
    return mappings


def get_status_mapping(conn, mapping_id):
    cursor = conn.cursor()
    cursor.execute('SELECT id, odata_status, custom_status, active FROM status_mapping WHERE id = ?', (mapping_id,))
    mapping = cursor.fetchone()
    return mapping


def ensure_status_mapping_table(conn):
    """
    Ensure the `status_mapping` table exists.
    If created, populate it with statuses from the OData feed.
    """
    cursor = conn.cursor()

    # Check if the table already exists
    cursor.execute('''
    SELECT name FROM sqlite_master WHERE type='table' AND name='status_mapping';
    ''')
    table_exists = cursor.fetchone()

    if not table_exists:
        # Create the table if it doesn't exist
        cursor.execute('''
        CREATE TABLE status_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            odata_status TEXT UNIQUE NOT NULL,
            custom_status TEXT,
            active BOOLEAN NOT NULL DEFAULT TRUE
        );
        ''')
        conn.commit()

        # Populate the table with initial statuses
        try:
            populate_status_mapping_table(conn)
            print("Table `status_mapping` created and populated")
        except ValueError as e:
            print(f"Error populating `status_mapping`: {e}")


def populate_status_mapping_table(conn):
    """
    Pre-populate the `status_mapping` table with unique statuses from the OData feed.
    Add new statuses and mark missing statuses as inactive.
    """
    # Connect to the database
    cursor = conn.cursor()

    # Fetch unique statuses for the instance
    odata_statuses_cbr = get_statuses('CBR')
    odata_statuses_dd = get_statuses('DD')
    odata_statuses = odata_statuses_cbr | odata_statuses_dd

    # Mark all existing statuses as inactive initially
    cursor.execute('UPDATE status_mapping SET active = FALSE')

    # Add or update statuses
    for status in odata_statuses:
        if status:
            cursor.execute('''
            INSERT INTO status_mapping (odata_status, custom_status, active)
            VALUES (?, ?, TRUE)
            ON CONFLICT(odata_status) DO UPDATE SET active = TRUE;
            ''', (status, status))

    conn.commit()


def edit_status_mapping(conn, mapping_id, custom_status, active):
    cursor = conn.cursor()

    cursor.execute('''
    UPDATE status_mapping
    SET custom_status = ?, active = ?
    WHERE id = ?;
    ''', (custom_status, active, mapping_id))
    conn.commit()



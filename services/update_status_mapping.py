from services.database import execute_query
from typing import Iterable
from services.buz_data import get_statuses
from services.odata_utils import odata_quote


def update_status_mapping(odata_statuses):
    # Mark old statuses as inactive
    execute_query('''
    UPDATE status_mapping 
    SET active = FALSE 
    WHERE odata_status NOT IN (
        SELECT odata_status FROM (VALUES {}) 
    );
    '''.format(', '.join(f"('{s}')" for s in odata_statuses)))

    # Insert new or reactivate existing statuses
    for status in odata_statuses:
        execute_query('''
        INSERT INTO status_mapping (odata_status, active) 
        VALUES (?, TRUE)
        ON CONFLICT (odata_status) DO UPDATE SET active = TRUE;
        ''', (status,))

    # Insert any new statuses into the `status_mapping` table
    for status in odata_statuses:
        execute_query('''
        INSERT OR IGNORE INTO status_mapping (odata_status, active)
        VALUES (?, TRUE);
        ''', (status,))


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


def get_status_mapping(mapping_id, conn):
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


def _unique_nonempty(values: Iterable[str]) -> set[str]:
    return {v.strip() for v in values if v and str(v).strip()}


def populate_status_mapping_table(conn) -> None:
    """
    Pre-populate the `status_mapping` table with unique statuses from the OData feed.
    - Adds any new statuses
    - Marks missing statuses as inactive
    """
    cursor = conn.cursor()

    def _clean(values):
        return {v.strip() for v in values if v and str(v).strip()}

    # unpack .get("data")
    cbr = _clean(get_statuses("CBR")["data"])
    dd = _clean(get_statuses("DD")["data"])
    odata_statuses = cbr | dd

    # Start a transaction
    cursor.execute("BEGIN")

    # Mark all existing statuses inactive first
    cursor.execute("UPDATE status_mapping SET active = FALSE")

    # Upsert each observed status; set custom_status default = odata_status on first insert
    cursor.executemany(
        """
        INSERT INTO status_mapping (odata_status, custom_status, active)
        VALUES (?, ?, TRUE)
        ON CONFLICT(odata_status) DO UPDATE SET
            active = TRUE
        """,
        [(s, s) for s in sorted(odata_statuses)],
    )

    conn.commit()


def edit_status_mapping(mapping_id, custom_status, active, conn):
    cursor = conn.cursor()

    cursor.execute('''
    UPDATE status_mapping
    SET custom_status = ?, active = ?
    WHERE id = ?;
    ''', (custom_status, active, mapping_id))
    conn.commit()



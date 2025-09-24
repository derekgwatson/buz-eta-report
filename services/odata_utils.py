# services/odata_utils.py
def odata_quote(value: str) -> str:
    """
    Escape single quotes per OData spec by doubling them,
    then wrap in single quotes.
    Example:  O'Malley ->  'O''Malley'
    """
    safe = str(value).replace("'", "''")
    return "'" + safe + "'"

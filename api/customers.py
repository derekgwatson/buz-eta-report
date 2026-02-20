import uuid

from flask import Blueprint, request

from api.auth import api_key_required
from api.errors import bad_request, not_found, success_response, validation_error
from services.database import query_db

customers_bp = Blueprint("api_customers", __name__)

VALID_FIELD_TYPES = {"Customer Name", "Customer Group"}


def _customer_to_dict(row):
    """Convert sqlite3.Row to API dict."""
    return {
        "id": row["id"],
        "dd_name": row["dd_name"],
        "cbr_name": row["cbr_name"],
        "obfuscated_id": row["obfuscated_id"],
        "field_type": row["field_type"],
        "display_name": row["display_name"],
    }


@customers_bp.route("/customers", methods=["GET"])
@api_key_required
def list_customers():
    rows = query_db(
        "SELECT id, dd_name, cbr_name, obfuscated_id, field_type, display_name "
        "FROM customers ORDER BY LOWER(display_name) ASC"
    )
    return success_response([_customer_to_dict(r) for r in rows])


@customers_bp.route("/customers/<obfuscated_id>", methods=["GET"])
@api_key_required
def get_customer(obfuscated_id: str):
    row = query_db(
        "SELECT id, dd_name, cbr_name, obfuscated_id, field_type, display_name "
        "FROM customers WHERE obfuscated_id = ?",
        (obfuscated_id,),
        one=True,
    )
    if not row:
        return not_found(f"Customer not found: {obfuscated_id}")
    return success_response(_customer_to_dict(row))


@customers_bp.route("/customers", methods=["POST"])
@api_key_required
def create_customer():
    data = request.get_json(silent=True)
    if data is None:
        return bad_request("Request body must be JSON")

    dd_name = (data.get("dd_name") or "").strip()
    cbr_name = (data.get("cbr_name") or "").strip()
    display_name = (data.get("display_name") or "").strip()
    field_type = data.get("field_type", "Customer Name")

    if not dd_name and not cbr_name:
        return validation_error("At least one of dd_name or cbr_name is required")

    if field_type not in VALID_FIELD_TYPES:
        return validation_error(
            f"field_type must be one of: {', '.join(sorted(VALID_FIELD_TYPES))}"
        )

    if not display_name:
        display_name = cbr_name or dd_name

    obfuscated_id = uuid.uuid4().hex

    query_db(
        "INSERT INTO customers (dd_name, cbr_name, display_name, obfuscated_id, field_type) "
        "VALUES (?, ?, ?, ?, ?)",
        (dd_name or None, cbr_name or None, display_name, obfuscated_id, field_type),
    )

    row = query_db(
        "SELECT id, dd_name, cbr_name, obfuscated_id, field_type, display_name "
        "FROM customers WHERE obfuscated_id = ?",
        (obfuscated_id,),
        one=True,
    )
    return success_response(_customer_to_dict(row), status_code=201)


@customers_bp.route("/customers/<obfuscated_id>", methods=["PUT"])
@api_key_required
def update_customer(obfuscated_id: str):
    existing = query_db(
        "SELECT id, dd_name, cbr_name, obfuscated_id, field_type, display_name "
        "FROM customers WHERE obfuscated_id = ?",
        (obfuscated_id,),
        one=True,
    )
    if not existing:
        return not_found(f"Customer not found: {obfuscated_id}")

    data = request.get_json(silent=True)
    if data is None:
        return bad_request("Request body must be JSON")

    dd_name = (data.get("dd_name", existing["dd_name"]) or "").strip() or None
    cbr_name = (data.get("cbr_name", existing["cbr_name"]) or "").strip() or None
    display_name = (data.get("display_name") or "").strip()
    field_type = data.get("field_type", existing["field_type"])

    if not dd_name and not cbr_name:
        return validation_error("At least one of dd_name or cbr_name is required")

    if field_type not in VALID_FIELD_TYPES:
        return validation_error(
            f"field_type must be one of: {', '.join(sorted(VALID_FIELD_TYPES))}"
        )

    if not display_name:
        display_name = cbr_name or dd_name

    query_db(
        "UPDATE customers SET dd_name = ?, cbr_name = ?, display_name = ?, field_type = ? "
        "WHERE obfuscated_id = ?",
        (dd_name, cbr_name, display_name, field_type, obfuscated_id),
    )

    row = query_db(
        "SELECT id, dd_name, cbr_name, obfuscated_id, field_type, display_name "
        "FROM customers WHERE obfuscated_id = ?",
        (obfuscated_id,),
        one=True,
    )
    return success_response(_customer_to_dict(row))


@customers_bp.route("/customers/<obfuscated_id>", methods=["DELETE"])
@api_key_required
def delete_customer(obfuscated_id: str):
    existing = query_db(
        "SELECT id FROM customers WHERE obfuscated_id = ?",
        (obfuscated_id,),
        one=True,
    )
    if not existing:
        return not_found(f"Customer not found: {obfuscated_id}")

    query_db("DELETE FROM customers WHERE obfuscated_id = ?", (obfuscated_id,))
    return "", 204

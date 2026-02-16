from __future__ import annotations

import os
import uuid
from functools import wraps

from flask import Blueprint, jsonify, request, url_for

from services.database import query_db

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


def api_key_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        key = request.headers.get("X-API-Key")
        expected = os.environ.get("API_KEY")
        if not expected or key != expected:
            return jsonify({"error": "Invalid or missing API key"}), 401
        return f(*args, **kwargs)
    return wrapped


@api_bp.route("/customers", methods=["GET"])
@api_key_required
def list_customers():
    rows = query_db(
        "SELECT id, dd_name, cbr_name, display_name, obfuscated_id, field_type "
        "FROM customers ORDER BY LOWER(display_name) ASC"
    )
    data = [
        {
            "id": r["id"],
            "dd_name": r["dd_name"],
            "cbr_name": r["cbr_name"],
            "display_name": r["display_name"],
            "obfuscated_id": r["obfuscated_id"],
            "field_type": r["field_type"],
        }
        for r in rows
    ]
    return jsonify({"data": data})


@api_bp.route("/customers", methods=["POST"])
@api_key_required
def create_customer():
    body = request.get_json(silent=True) or {}

    dd_name = (body.get("dd_name") or "").strip() or None
    cbr_name = (body.get("cbr_name") or "").strip() or None
    display_name = (body.get("display_name") or "").strip()
    field_type = body.get("field_type") or "Customer Name"

    if not (dd_name or cbr_name):
        return jsonify({"error": "At least one of dd_name or cbr_name is required"}), 400

    if field_type not in {"Customer Name", "Customer Group"}:
        return jsonify({"error": "field_type must be 'Customer Name' or 'Customer Group'"}), 400

    if not display_name:
        display_name = cbr_name or dd_name

    obfuscated_id = uuid.uuid4().hex

    query_db(
        "INSERT INTO customers (dd_name, cbr_name, display_name, obfuscated_id, field_type) "
        "VALUES (?, ?, ?, ?, ?)",
        (dd_name, cbr_name, display_name, obfuscated_id, field_type),
    )

    report_url = url_for("eta_report", obfuscated_id=obfuscated_id, _external=True)

    return jsonify({
        "data": {
            "id": obfuscated_id,
            "dd_name": dd_name,
            "cbr_name": cbr_name,
            "display_name": display_name,
            "obfuscated_id": obfuscated_id,
            "field_type": field_type,
            "report_url": report_url,
        }
    }), 201

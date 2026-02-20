from flask import Blueprint, current_app

from api.auth import api_key_required
from api.errors import server_error, success_response
from services.database import get_db
from services.update_status_mapping import (
    get_status_mappings,
    populate_status_mapping_table,
)

statuses_bp = Blueprint("api_statuses", __name__)


def _mapping_to_dict(row):
    return {
        "id": row[0],
        "odata_status": row[1],
        "custom_status": row[2],
        "active": bool(row[3]),
    }


@statuses_bp.route("/statuses", methods=["GET"])
@api_key_required
def list_statuses():
    conn = get_db()
    mappings = get_status_mappings(conn=conn)
    return success_response([_mapping_to_dict(m) for m in mappings])


@statuses_bp.route("/statuses/refresh", methods=["POST"])
@api_key_required
def refresh_statuses():
    try:
        conn = get_db()
        populate_status_mapping_table(conn)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM status_mapping WHERE active = 1")
        count = cursor.fetchone()[0]
        return success_response({"refreshed": True, "active_count": count})
    except Exception as exc:
        current_app.logger.exception("Failed to refresh statuses")
        return server_error(f"Failed to refresh statuses: {exc}")

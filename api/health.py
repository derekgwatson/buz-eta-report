from flask import Blueprint

from api.errors import success_response, server_error
from services.database import get_db

health_bp = Blueprint("api_health", __name__)


@health_bp.route("/health", methods=["GET"])
def health_check():
    """Health check -- no API key required (for monitoring/load balancers)."""
    try:
        conn = get_db()
        conn.execute("SELECT 1").fetchone()

        cache_count = 0
        try:
            row = conn.execute("SELECT COUNT(*) FROM cache").fetchone()
            cache_count = row[0] if row else 0
        except Exception:
            pass  # cache table may not exist yet

        running_jobs = 0
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'running'"
            ).fetchone()
            running_jobs = row[0] if row else 0
        except Exception:
            pass

        return success_response(
            {
                "status": "ok",
                "db": True,
                "cache_entries": cache_count,
                "running_jobs": running_jobs,
            }
        )
    except Exception as exc:
        return server_error(f"Health check failed: {exc}")

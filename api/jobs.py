import time

from flask import Blueprint

from api.auth import api_key_required
from api.errors import not_found, success_response
from services.job_service import get_job, update_job

STALL_TTL = 300  # 5 minutes, matches app.py

jobs_bp = Blueprint("api_jobs", __name__)


@jobs_bp.route("/jobs/<job_id>", methods=["GET"])
@api_key_required
def job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        return not_found(f"Job not found: {job_id}")

    # Stall detection (mirrors app.py)
    if job["status"] == "running" and time.time() - job["updated_ts"] > STALL_TTL:
        update_job(
            job_id,
            error="Report generation has stopped responding. Please try again.",
            done=True,
        )
        job = get_job(job_id)

    return success_response(
        {
            "job_id": job_id,
            "status": job["status"],
            "pct": job["pct"],
            "done": job["done"],
            "error": job.get("error"),
            "log": job.get("log", []),
        }
    )

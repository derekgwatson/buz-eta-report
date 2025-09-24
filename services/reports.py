# routes/reports.py
import uuid
from flask import Blueprint, jsonify, current_app, request
from services.job_service import create_job, update_job, get_job, make_progress
from services.database import get_db
from services.eta_report import build_eta_report_context

bp = Blueprint("reports", __name__)


def _run_report_job(job_id, params):
    # open a fresh DB connection in the thread
    db = get_db()
    try:
        # create a thread-safe progress callback that writes to DB
        progress = make_progress(job_id, db=db)

        progress("Starting…", pct=1)
        path = build_eta_report_context(
            params,
            progress=lambda msg, db=db, pct=None: update_job(job_id, pct=pct, message=msg),
        )
        update_job(job_id, pct=100, message="Done", result={"download_path": path}, done=True, db=db)
    except Exception as e:
        update_job(job_id, error=str(e), message="Job failed", db=db)
    finally:
        # close if your DB manager needs it
        try:
            db.close()
        except Exception:
            pass


@bp.post("/reports/run")
def run_report():
    params = request.get_json(silent=True) or {}
    job_id = str(uuid.uuid4())

    # use request's g.db here (safe) to insert the job row
    create_job(job_id)  # your helper uses g.db by default

    # kick background work
    current_app.executor.submit(_run_report_job, job_id, params)
    return jsonify({"job_id": job_id}), 202

@bp.get("/jobs/<job_id>")
def job_status(job_id):
    # safe to use g.db in request context
    data = get_job(job_id)
    if not data:
        return jsonify({"error": "not found"}), 404
    return jsonify(data)

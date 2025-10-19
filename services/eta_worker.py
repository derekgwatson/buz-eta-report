# services/eta_worker.py
from __future__ import annotations
import traceback
from requests.exceptions import RequestException, HTTPError, Timeout
from flask import current_app
from services.database import get_db
from services.job_service import update_job
from services.eta_report import build_eta_report_context
from services.job_service import _query_one


def load_cache(db, instance):
    row = _query_one(db, "SELECT payload FROM eta_cache WHERE instance=?", (instance,))
    if not row:
        return None
    return row["payload"] if hasattr(row, "keys") and "payload" in row.keys() else row[0]


def save_cache(db, instance, payload):
    db.execute_query(
        "INSERT INTO eta_cache(instance, payload, updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
        "ON CONFLICT(instance) DO UPDATE SET payload=excluded.payload, updated_at=CURRENT_TIMESTAMP",
        (instance, payload),
    )
    db.commit()


def _fetch_live_or_cached(instance: str, job_id: str, db):
    try:
        update_job(job_id, pct=5, message="Calling upstream…", db=db)
        # If build_eta_report_context accepts timeout, pass it through.
        payload = build_eta_report_context(instance, timeout=(3, 8))  # remove arg if not supported
        save_cache(db, instance, payload)
        update_job(job_id, pct=70, message="Got live data", db=db)
        return payload, False
    except (RequestException, HTTPError, Timeout, Exception) as e:
        update_job(job_id, message=f"Upstream unavailable ({e}); using cache", db=db)
        cached = load_cache(db, instance)
        if not cached:
            update_job(job_id, error="API down and no cached data available", done=True, db=db)
            raise
        return cached, True


def run_eta_job(app, job_id: str, instance: str):
    with app.app_context():
        try:
            # ✅ early heartbeat (no db arg needed; update_job will open its own)
            update_job(job_id, pct=1, message="Starting…")

            # ✅ get a thread-local DB connection
            db = get_db()

            data, from_cache = _fetch_live_or_cached(instance, job_id, db)

            update_job(
                job_id,
                pct=100,
                message=("Served cached report" if from_cache else "Report built"),
                result={"from_cache": from_cache, "payload": data},
                done=True,
                db=db,
            )
        except Exception as e:
            current_app.logger.error("ETA worker crashed: %s\n%s", e, traceback.format_exc())
            try:
                update_job(job_id, error=f"{type(e).__name__}: {e}", done=True)
            except Exception as e2:
                current_app.logger.error("Failed to write job error: %s", e2)
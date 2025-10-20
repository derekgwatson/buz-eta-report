from __future__ import annotations
import json, traceback
from typing import Dict, Any, Tuple, Optional
from requests.exceptions import RequestException, HTTPError, Timeout
from flask import current_app
from services.database import get_db
from services.job_service import update_job, _query_one
from services.eta_report import build_eta_report_context


def load_cache(db, obfuscated_id: str) -> Optional[Dict[str, Any]]:
    row = _query_one(db, "SELECT payload FROM eta_cache WHERE obfuscated_id=?", (obfuscated_id,))
    if not row:
        return None
    raw = row["payload"] if hasattr(row, "keys") and "payload" in row.keys() else row[0]
    try:
        return json.loads(raw)
    except Exception:
        return None


def save_cache(db, obfuscated_id: str, result_dict: Dict[str, Any]) -> None:
    payload = json.dumps(result_dict)
    db.execute_query(
        "INSERT INTO eta_cache(obfuscated_id, payload, updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
        "ON CONFLICT(obfuscated_id) DO UPDATE SET payload=excluded.payload, updated_at=CURRENT_TIMESTAMP",
        (obfuscated_id, payload),
    )
    db.commit()


def _fetch_live_or_cached(obfuscated_id: str, job_id: str, db) -> Tuple[Dict[str,Any], bool]:
    """
    Returns a result dict shaped like:
      {"template": str, "context": dict, "status": int}
    bool indicates whether it came from cache.
    """
    try:
        update_job(job_id, pct=5, message="Building report (live)…", db=db)
        template, context, status = build_eta_report_context(obfuscated_id, db=db)
        # ensure id present in context (belt-and-braces)
        context.setdefault("obfuscated_id", obfuscated_id)
        result = {"template": template, "context": context, "status": status}
        save_cache(db, obfuscated_id, result)
        update_job(job_id, pct=70, message="Live report ready", db=db)
        return result, False
    except (RequestException, HTTPError, Timeout, Exception) as e:
        update_job(job_id, message=f"Upstream unavailable ({e}); using cached report", db=db)
        cached = load_cache(db, obfuscated_id)
        if not cached:
            update_job(job_id, error="API down and no cached report available", done=True, db=db)
            raise
        return cached, True


def run_eta_job(app, job_id: str, obfuscated_id: str) -> None:
    """
    Build (or load cached) report for an obfuscated_id and store the standard result
    shape so /report/<job_id> can render it directly.
    """
    with app.app_context():
        try:
            update_job(job_id, pct=1, message="Starting…")
            db = get_db()

            result, from_cache = _fetch_live_or_cached(obfuscated_id, job_id, db)

            # Finalize
            update_job(
                job_id,
                pct=100,
                message=("Served cached report" if from_cache else "Report built"),
                result=result,   # <-- template/context/status only
                done=True,
                db=db,
            )
        except Exception as e:
            current_app.logger.error("ETA worker crashed: %s\n%s", e, traceback.format_exc())
            try:
                update_job(job_id, error=f"{type(e).__name__}: {e}", done=True)
            except Exception as e2:
                current_app.logger.error("Failed to write job error: %s", e2)

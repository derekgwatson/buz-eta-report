from __future__ import annotations
import traceback
from flask import current_app
from services.database import get_db
from services.job_service import update_job
from services.eta_report import build_eta_report_context


def run_eta_job(app, job_id: str, obfuscated_id: str) -> None:
    """
    Build report for an obfuscated_id and store the result
    so /report/<job_id> can render it directly.
    """
    with app.app_context():
        db = get_db()
        try:
            update_job(job_id, pct=5, message="Loading customer…", db=db)
            template, context, status = build_eta_report_context(obfuscated_id, db=db)
            context.setdefault("obfuscated_id", obfuscated_id)
            update_job(
                job_id,
                pct=100,
                message="Ready",
                result={
                    "template": template,
                    "context": context,
                    "status": status,
                    "obfuscated_id": obfuscated_id,
                },
                done=True,
                db=db,
            )
        except Exception as e:
            current_app.logger.error("ETA worker crashed: %s\n%s", e, traceback.format_exc())
            try:
                update_job(job_id, error=f"{type(e).__name__}: {e}", done=True, db=db)
            except Exception as e2:
                current_app.logger.error("Failed to write job error: %s", e2)
        finally:
            try:
                db.close()
            except Exception:
                pass

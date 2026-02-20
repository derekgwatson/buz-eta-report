import io
import uuid

import sentry_sdk
from flask import Blueprint, current_app, request, send_file

from api.auth import api_key_required
from api.errors import bad_request, not_found, success_response
from services.buz_data import get_open_orders, get_open_orders_by_group
from services.database import get_db, query_db
from services.eta_report import build_eta_report_context
from services.export import (
    apply_filters,
    fetch_report_rows_and_name,
    ordered_headers,
    safe_base_filename,
    scrub_sensitive,
    to_csv_bytes,
    to_excel_bytes,
)
from services.job_service import create_job, update_job

reports_bp = Blueprint("api_reports", __name__)


def _group_data_only(conn, group, instance):
    res = get_open_orders_by_group(conn, group, instance)
    return res["data"] if isinstance(res, dict) else (res or [])


def _customer_data_only(conn, customer, instance):
    res = get_open_orders(conn, customer, instance)
    return res["data"] if isinstance(res, dict) else (res or [])


def _run_api_report_job(app, job_id: str, obfuscated_id: str) -> None:
    """Background worker for API report generation."""
    with app.app_context():
        db = get_db()
        try:
            update_job(job_id, pct=5, message="Loading customer...", db=db)
            template, context, status = build_eta_report_context(
                obfuscated_id, db=db
            )
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
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            update_job(job_id, error=str(exc), message="Job failed", db=db)
        finally:
            try:
                db.close()
            except Exception:
                pass


@reports_bp.route("/reports/<obfuscated_id>/generate", methods=["POST"])
@api_key_required
def generate_report(obfuscated_id: str):
    row = query_db(
        "SELECT id FROM customers WHERE obfuscated_id = ?",
        (obfuscated_id,),
        one=True,
    )
    if not row:
        return not_found(f"Customer not found: {obfuscated_id}")

    job_id = str(uuid.uuid4())
    create_job(job_id)

    app = current_app._get_current_object()
    app.executor.submit(_run_api_report_job, app, job_id, obfuscated_id)

    return success_response({"job_id": job_id}, status_code=202)


@reports_bp.route("/reports/<obfuscated_id>/download", methods=["GET"])
@api_key_required
def download_report(obfuscated_id: str):
    fmt = (request.args.get("format") or "csv").lower()
    if fmt not in ("csv", "xlsx"):
        return bad_request(f"Unsupported format: {fmt}. Use 'csv' or 'xlsx'.")

    rows, customer_name = fetch_report_rows_and_name(
        obfuscated_id,
        query_db=query_db,
        get_db=get_db,
        get_open_orders=_customer_data_only,
        get_open_orders_by_group=_group_data_only,
    )
    if rows is None:
        return not_found(f"Customer not found: {obfuscated_id}")

    rows = apply_filters(
        rows,
        status=request.args.get("status"),
        group=request.args.get("group"),
        supplier=request.args.get("supplier"),
    )
    rows = scrub_sensitive(rows)
    headers = ordered_headers(rows)

    if fmt == "xlsx":
        data = to_excel_bytes(rows, headers)
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        data = to_csv_bytes(rows, headers)
        mimetype = "text/csv"

    filename = f"{safe_base_filename(customer_name or obfuscated_id)}.{fmt}"
    return send_file(
        io.BytesIO(data),
        mimetype=mimetype,
        as_attachment=True,
        download_name=filename,
    )

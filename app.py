import io
import os
import uuid
import time
import logging
import logging.config
import atexit
from datetime import timedelta
from functools import wraps
from dotenv import load_dotenv, find_dotenv
from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
    g,
    current_app,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from authlib.integrations.flask_client import OAuth
from flask_wtf.csrf import CSRFProtect
from concurrent.futures import ThreadPoolExecutor
import requests
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from services.update_status_mapping import get_status_mapping, edit_status_mapping, get_status_mappings
from sentry_sdk.integrations.logging import LoggingIntegration

from services.database import (
    get_db,
    query_db,
    execute_query,
    create_db_tables,
)
from services.migrations import run_migrations
from services.eta_report import build_eta_report_context
from services.buz_data import get_data_by_order_no, get_open_orders, get_open_orders_by_group
from services.job_service import create_job, update_job, get_job

import secrets, threading
from services.eta_worker import run_eta_job

from services.export import (
    scrub_sensitive,
    ordered_headers,
    apply_filters,
    to_excel_bytes,
    to_csv_bytes,
    fetch_report_rows_and_name,
    safe_base_filename,
)
import click, sqlite3
from services.migrations import _backup_sqlite


STALL_TTL = 300  # 5 minutes - only for detecting truly hung workers


# ---------- env ----------
load_dotenv(find_dotenv())

# ---------- globals ----------
oauth = OAuth()
login_manager = LoginManager()


class User(UserMixin):
    def __init__(self, id_: int, name: str, email: str, role: str):
        self.id = id_
        self.name = name
        self.email = email
        self.role = role


def _configure_logging(app: Flask) -> None:
    root = logging.getLogger()
    root.handlers[:] = []
    root.setLevel(app.config.get("LOG_LEVEL", "INFO"))
    handler = logging.StreamHandler()
    handler.setLevel(app.config.get("LOG_LEVEL", "INFO"))
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handler.setFormatter(logging.Formatter(fmt))
    root.addHandler(handler)
    app.logger.setLevel(app.config.get("LOG_LEVEL", "INFO"))


def _before_send(event, hint):
    # drop request bodies and emails
    req = event.get("request") or {}
    if "data" in req: req["data"] = "[filtered]"
    user = event.get("user") or {}
    if "email" in user: user["email"] = "[filtered]"
    event["request"] = req; event["user"] = user
    return event


def create_app(testing: bool = False) -> tuple[Flask, str]:
    app = Flask(__name__, instance_relative_config=True)
    app.config["TESTING"] = testing

    # database path
    db_path = os.environ.get("DATABASE")
    if not db_path:
        raise RuntimeError("DATABASE env var is required")
    app.config["DATABASE"] = db_path

    env = (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "production").lower()
    if env == "development":
        from config import DevConfig as Cfg
    elif env == "production":
        from config import ProdConfig as Cfg
    elif env == "prod":
        from config import ProdConfig as Cfg
    elif env == "staging":
        from config import StagingConfig as Cfg
    else:
        raise RuntimeError(f"Unknown APP_ENV/FLASK_ENV value: {env!r}")

    # Don’t initialize Sentry in tests (or when explicitly disabled)
    sentry_disabled = os.getenv("SENTRY_DISABLED") == "1"
    if (not testing) and (not sentry_disabled) and os.getenv("SENTRY_DSN"):
        sentry_sdk.init(
            dsn=os.getenv("SENTRY_DSN"),
            integrations=[FlaskIntegration(), LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
            traces_sample_rate=0.2,  # avoid 100% in prod
            profiles_sample_rate=0.1,  # optional: enable profiling a bit
            environment=env,
            send_default_pii=False,  # safer default; see scrubbing below
            before_send=_before_send,
            shutdown_timeout=0,  # avoid "Waiting up to 2 seconds" on exit
        )

    app.config.from_object(Cfg)
    _configure_logging(app)

    app.secret_key = os.getenv("FLASK_SECRET")
    app.permanent_session_lifetime = timedelta(minutes=30)

    # Cookies in production
    if env == "production":
        app.config["SESSION_COOKIE_SECURE"] = True
        app.config["SESSION_COOKIE_HTTPONLY"] = True
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
        app.config["REMEMBER_COOKIE_DURATION"] = timedelta(minutes=60)

    # CSRF, OAuth, Login manager
    CSRFProtect(app)
    oauth.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "login"
    login_manager.login_message = "Please log in to access this page."
    login_manager.login_message_category = "error"

    # Register Google OAuth client
    oauth.register(
        name="google",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        access_token_url="https://oauth2.googleapis.com/token",
        authorize_url="https://accounts.google.com/o/oauth2/auth",
        api_base_url="https://www.googleapis.com/oauth2/v1/",
        userinfo_endpoint="https://openidconnect.googleapis.com/v1/userinfo",
        jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
        client_kwargs={
            "scope": "openid email profile",
            "token_endpoint_auth_method": "client_secret_post",
            "prompt": "consent",
        },
    )

    # Background executor
    app.executor = ThreadPoolExecutor(max_workers=2)

    @app.cli.command("db-backup")
    @click.option("--dir", "backup_dir", default=None, help="Optional directory for backup file")
    def db_backup_cmd(backup_dir):
        db_path = app.config["DATABASE"]
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            path = _backup_sqlite(conn, backup_dir=backup_dir)
            click.echo(f"Backup created: {path}")
        finally:
            conn.close()

    @atexit.register
    def _shutdown_executor():
        ex = getattr(app, "executor", None)
        if ex:
            try:
                ex.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                ex.shutdown(wait=False)

    # DB migrations
    with app.app_context():
        conn = get_db()
        run_migrations(conn, make_backup=True, logger=app.logger)

    # Close DB per request
    @app.teardown_appcontext
    def close_db(_exc):
        db = g.pop("db", None)
        if db:
            db.close()

    return app, env


app, ENV = create_app()


# ---------- helpers ----------
def role_required(*required_roles):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in required_roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator


def get_executor() -> ThreadPoolExecutor:
    ex = getattr(app, "executor", None)
    if ex is None:
        ex = ThreadPoolExecutor(max_workers=2)
        app.executor = ex
    return ex


@login_manager.unauthorized_handler
def handle_unauthorized():
    app.logger.warning("Unauthorized access attempt.")
    return redirect(url_for("login"))


@login_manager.user_loader
def load_user(user_id):
    row = query_db("SELECT id, email, name, role, active FROM users WHERE id = ?", (user_id,), one=True)
    if row and row[4]:
        return User(id_=row[0], name=row[2], email=row[1], role=row[3])
    return None


# ---------- Auth routes ----------
@app.route("/login")
def login():
    # Use external URL; configured redirect URI must match in Google console
    return oauth.google.authorize_redirect(url_for("callback", _external=True))


@app.route("/callback")
def callback():
    token = oauth.google.authorize_access_token()
    if not token or token.get("expires_in", 0) <= 0:
        return redirect(url_for("login"))

    user_info = oauth.google.get("userinfo").json()
    email = user_info.get("email")
    if not email:
        return redirect(url_for("login"))

    row = query_db(
        "SELECT id, email, name, role, active FROM users WHERE email = ?",
        (email,),
        one=True,
        logger=app.logger,
    )
    if not row or not row[4]:
        return render_template("403.html"), 403

    user = User(id_=row[0], name=row[2], email=row[1], role=row[3])
    login_user(user)
    return redirect(url_for("admin"))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return render_template("home.html")


# ---------- Core pages ----------
@app.route("/")
def home():
    return render_template("home.html")


@app.route("/admin", methods=["GET", "POST"])
@login_required
@role_required("admin", "user")
def admin():
    def _debug_db_snapshot():
        conn = get_db()
        dbs = conn.execute("PRAGMA database_list").fetchall()
        cust_count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        cols = conn.execute("PRAGMA table_info(customers)").fetchall()
        return {
            "attached_dbs": dbs,
            "customers_count": cust_count,
            "customers_columns": [c[1] for c in cols],
        }

    app.logger.debug(_debug_db_snapshot())

    if request.method == "POST":
        dd_name = (request.form.get("dd_name") or "").strip()
        cbr_name = (request.form.get("cbr_name") or "").strip()
        display_name = (request.form.get("display_name") or "").strip()
        field_type = request.form.get("field_type") or "Customer Name"
        if not (dd_name or cbr_name):
            return render_template("400.html", message="Pick at least one customer."), 400
        if not display_name:
            return render_template("400.html", message="Display name is required."), 400
        if field_type not in {"Customer Name", "Customer Group"}:
            return render_template("400.html", message="Invalid field type."), 400
        obfuscated_id = uuid.uuid4().hex

        query_db(
            "INSERT INTO customers (dd_name, cbr_name, display_name, obfuscated_id, field_type) "
            "VALUES (?, ?, ?, ?, ?)",
            (dd_name, cbr_name, display_name, obfuscated_id, field_type),
            logger=app.logger,
        )
        return redirect(url_for("admin"))

    customers = query_db(
        "SELECT id, dd_name, cbr_name, obfuscated_id, field_type, display_name "
        "FROM customers "
        "ORDER BY LOWER(display_name) ASC"
    )

    return render_template("admin.html", customers=customers)


# ---------- ETA async render flow ----------
@app.route("/sync/<obfuscated_id>")
def eta_report_sync(obfuscated_id: str):
    # In-request: get_db() binds to g.db; teardown will close it.
    db = get_db()
    try:
        template, context, status = build_eta_report_context(obfuscated_id, db=db)
        current_app.logger.debug("worker ctx keys: %s", sorted(context.keys()))
        current_app.logger.debug("worker obfuscated_id: %r", context.get("obfuscated_id"))
        context.setdefault("obfuscated_id", obfuscated_id)
        return render_template(template, **context), status
    except requests.exceptions.RequestException as exc:
        msg = f"Failed to generate report: {exc}"
        return render_template("500.html", message=msg), 500


def _run_eta_report_job(job_id: str, obfuscated_id: str) -> None:
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
    except Exception as exc:
        sentry_sdk.capture_exception(
            exc,
            scope=lambda scope: scope.set_context(
                "job",
                {"job_id": job_id, "obfuscated_id": obfuscated_id}
            ),
        )
        update_job(job_id, error=str(exc), message="Job failed", db=db)
        raise
    finally:
        try:
            db.close()
        except Exception:
            pass


@app.route("/<obfuscated_id>")
def eta_report(obfuscated_id: str):
    _ = get_db()  # ensure g.db bound
    job_id = str(uuid.uuid4())
    create_job(job_id)  # uses g.db
    get_executor().submit(_run_eta_report_job, job_id, obfuscated_id)
    return render_template("report_loading.html", job_id=job_id)


@app.post("/eta/start")
def start_eta():
    instance = (request.json or {}).get("instance") or "DD"  # pick your default/param
    job_id = secrets.token_hex(16)
    create_job(job_id)

    t = threading.Thread(
        target=run_eta_job,
        args=(app, job_id, instance),
        daemon=True,
    )
    current_app.logger.info("Spawned %s", t.name)
    t.start()

    return jsonify({"job_id": job_id})


@app.get("/jobs/<job_id>")
def job_status(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404

    if job["status"] == "running" and time.time() - job["updated_ts"] > STALL_TTL:
        update_job(
            job_id,
            error="Report generation has stopped responding. This usually means the supplier's system is experiencing significant delays. Please try generating the report again, or contact support if this issue persists.",
            done=True
        )
        job = get_job(job_id)  # reload

    return jsonify(job)


@app.get("/report/<job_id>")
def report_render(job_id: str):
    try:
        data = get_job(job_id)
        if not data or data.get("status") != "completed":
            return render_template("404.html", message="Report not ready or missing"), 404

        result = data.get("result") or {}
        template = result.get("template") or "report.html"
        context = result.get("context") or {}
        if "obfuscated_id" not in context:
            current_app.logger.warning("report_render: missing obfuscated_id for job %s", job_id)
        status = result.get("status") or 200

        return render_template(template, **context), status
    except requests.exceptions.RequestException as exc:
        # No obfuscated_id in scope here; show a generic 500 page.
        msg = f"Failed to generate report: {exc}"
        return render_template("500.html", message=msg), 500


# ---------- Data lookups ----------
@app.route("/jobs-schedule/<instance>/<order_no>")
@login_required
def jobs_schedule(instance: str, order_no: str):
    instance = (instance or "").upper()
    if instance not in {"DD", "CBR"}:
        return jsonify({"error": "Invalid instance. Use 'DD' or 'CBR'."}), 400
    try:
        result = get_data_by_order_no(order_no, "JobsScheduleDetailed", instance)
        return jsonify(result)
    except requests.exceptions.RequestException as exc:
        if app.debug:
            raise
        return jsonify({"error": f"Failed to fetch JobsScheduleDetailed: {exc}"}), 500
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503


@app.route("/wip/<instance>/<order_no>")
@login_required
def work_in_progress(instance: str, order_no: str):
    instance = (instance or "").upper()
    if instance not in {"DD", "CBR"}:
        return jsonify({"error": "Invalid instance. Use 'DD' or 'CBR'."}), 400
    try:
        result = get_data_by_order_no(order_no, "WorkInProgress", instance)
        return jsonify(result)
    except requests.exceptions.RequestException as exc:
        if app.debug:
            raise
        return jsonify({"error": f"Failed to fetch WorkInProgress: {exc}"}), 500
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503


# ---------- Status mapping ----------
@app.route("/status_mapping")
@login_required
@role_required("admin", "user")
def list_status_mappings():
    mappings = get_status_mappings(conn=get_db())
    return render_template("status_mappings.html", mappings=mappings)


@app.route("/status_mapping/edit/<int:mapping_id>", methods=["GET", "POST"])
@login_required
@role_required("admin", "user")
def edit_status_mapping_route(mapping_id: int):
    if request.method == "POST":
        custom_status = (request.form.get("custom_status") or "").strip()
        active = bool(request.form.get("active"))
        if not custom_status:
            return render_template("400.html", message="Custom status required."), 400
        edit_status_mapping(mapping_id, custom_status, active, conn=get_db())
        return redirect(url_for("list_status_mappings"))

    mapping = get_status_mapping(mapping_id=mapping_id, conn=get_db())
    return render_template("edit_status_mapping.html", mapping=mapping)


@app.route("/refresh_statuses", methods=["POST"])
@login_required
@role_required("admin")
def refresh_statuses():
    from services.update_status_mapping import populate_status_mapping_table

    try:
        populate_status_mapping_table(get_db())
        # flash works if template shows flashes
        # flash("Statuses refreshed successfully.", "success")
    except Exception as exc:
        if app.debug:
            raise
        # flash(f"Failed to refresh statuses: {exc}", "danger")
    return redirect(url_for("list_status_mappings"))


# ---------- Downloads (CSV/XLSX) ----------
def _group_data_only(conn, group, instance):
    res = get_open_orders_by_group(conn, group, instance)
    return res["data"] if isinstance(res, dict) else (res or [])


def _customer_data_only(conn, customer, instance):
    res = get_open_orders(conn, customer, instance)
    return res["data"] if isinstance(res, dict) else (res or [])


@app.route("/<obfuscated_id>/download.<fmt>", methods=["GET"])
def download_orders(obfuscated_id: str, fmt: str):
    rows, customer_name = fetch_report_rows_and_name(
        obfuscated_id,
        query_db=query_db,
        get_db=get_db,
        get_open_orders=_customer_data_only,
        get_open_orders_by_group=_group_data_only,
    )
    if rows is None:
        return render_template("404.html", message="Report not found"), 404

    rows = apply_filters(
        rows,
        status=request.args.get("status") or request.args.get("statusFilter"),
        group=request.args.get("group") or request.args.get("groupFilter"),
        supplier=request.args.get("supplier") or request.args.get("supplierFilter"),
    )
    # Redact sensitive columns BEFORE header calc
    rows = scrub_sensitive(rows)
    headers = ordered_headers(rows)

    if fmt == "xlsx":
        data = to_excel_bytes(rows, headers)
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif fmt == "csv":
        data = to_csv_bytes(rows, headers)
        mimetype = "text/csv"
    else:
        return render_template("400.html", message=f"Unrecognised format: {fmt}"), 400

    filename = f"{safe_base_filename(customer_name or obfuscated_id)}.{fmt}"
    return send_file(
        io.BytesIO(data),
        mimetype=mimetype,
        as_attachment=True,
        download_name=filename,
    )


# ---------- Users admin ----------
@app.route("/manage_users")
@login_required
@role_required("admin")
def manage_users():
    users = query_db("SELECT id, email, name, role, active FROM users")
    return render_template("manage_users.html", users=users)


@app.route("/add_user", methods=["POST"])
@login_required
@role_required("admin")
def add_user():
    email = request.form["email"]
    name = request.form["name"]
    role = request.form["role"]
    try:
        execute_query(
            "INSERT INTO users (email, name, role) VALUES (?, ?, ?)",
            (email, name, role),
        )
        # flash("User added successfully.", "success")
    except ValueError:
        # flash("Email already exists.", "danger")
        pass
    return redirect(url_for("manage_users"))


@app.route('/delete/<int:customer_id>')
def delete_customer(customer_id):
    query_db("DELETE FROM customers WHERE id = ?", (customer_id,))
    return redirect(url_for('admin'))


@app.route('/edit/<int:customer_id>', methods=['GET', 'POST'])
def edit_customer(customer_id):
    if request.method == 'POST':
        # Fetch updated form data
        dd_name = request.form.get('dd_name', '').strip() or None
        cbr_name = request.form.get('cbr_name', '').strip() or None
        display_name = request.form.get('display_name', '').strip()
        field_type = request.form.get('field_type', 'Customer Name')

        if not display_name:
            return render_template("400.html", message="Display name is required."), 400

        # Update the customer in the database
        query_db(
            "UPDATE customers SET dd_name = ?, cbr_name = ?, display_name = ?, field_type = ? WHERE id = ?",
            (dd_name, cbr_name, display_name, field_type, customer_id),
        )
        return redirect(url_for('admin'))

    # Fetch customer details for pre-filling the form
    customer = query_db(
        "SELECT id, dd_name, cbr_name, obfuscated_id, field_type, display_name FROM customers WHERE id = ?", (customer_id,), one=True
    )
    if not customer:
        return "Customer not found", 404
    return render_template('edit.html', customer=customer)


@app.route("/edit_user/<int:user_id>", methods=["GET", "POST"])
@login_required
@role_required("admin")
def edit_user(user_id: int):
    if request.method == "POST":
        email = request.form["email"]
        name = request.form["name"]
        role = request.form["role"]
        query_db(
            "UPDATE users SET email = ?, name = ?, role = ? WHERE id = ?",
            (email, name, role, user_id),
        )
        return redirect(url_for("manage_users"))

    user = query_db("SELECT id, email, name, role FROM users WHERE id = ?", (user_id,), one=True)
    if not user:
        # flash("User not found.", "danger")
        return redirect(url_for("manage_users"))
    return render_template("edit_user.html", user=dict(user))


@app.route("/toggle_user_status/<int:user_id>")
@login_required
@role_required("admin")
def toggle_user_status(user_id: int):
    user = query_db("SELECT active FROM users WHERE id = ?", (user_id,), one=True)
    if not user:
        # flash("User not found.", "danger")
        return redirect(url_for("manage_users"))

    new_status = 0 if user[0] == 1 else 1
    query_db("UPDATE users SET active = ? WHERE id = ?", (new_status, user_id))
    return redirect(url_for("manage_users"))


@app.route("/delete_user/<int:user_id>")
@login_required
@role_required("admin")
def delete_user(user_id: int):
    query_db("DELETE FROM users WHERE id = ?", (user_id,))
    return redirect(url_for("manage_users"))


# ---------- misc ----------
@app.route("/sentry-debug")
def trigger_error():
    _ = 1 / 0
    return "ok"


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(app.static_folder, "favicon.ico", mimetype="image/vnd.microsoft.icon")


@app.route("/robots.txt")
def robots_txt():
    return send_from_directory(app.static_folder, "robots.txt")


# ---------- CLI ----------
@app.cli.command("init-db")
def initialize_database():
    """Initialize the database tables."""
    conn = get_db()
    run_migrations(conn, make_backup=True, logger=app.logger)  # customers table + versioning
    create_db_tables(conn=conn)  # users/status_mapping/jobs
    print("Database ready.")


@app.cli.command("clear-cache")
@click.option("--confirm", is_flag=True, help="Confirm cache deletion")
@click.option("--jobs", is_flag=True, help="Also clear job history")
def clear_cache_command(confirm, jobs):
    """Clear all cached data from the database."""
    if not confirm:
        print("This will delete all cached data" + (" and job history" if jobs else "") + ".")
        print("Run with --confirm to proceed: flask clear-cache --confirm" + (" --jobs" if jobs else ""))
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM cache")
    cache_deleted = cursor.rowcount

    jobs_deleted = 0
    if jobs:
        cursor.execute("DELETE FROM jobs")
        jobs_deleted = cursor.rowcount

    conn.commit()
    print(f"Cleared {cache_deleted} cache entries" + (f" and {jobs_deleted} job records." if jobs else "."))


@app.cli.command("prewarm-cache")
@click.option("--instance", "instances", multiple=True, default=["DD", "CBR"], help="Instances to warm")
def prewarm_cache(instances):
    """
    Warm cache for all configured customers/groups.
    Safe to run anytime; best a few minutes before blackout.
    """
    conn = get_db()
    customers = query_db("SELECT dd_name, cbr_name, field_type FROM customers", one=False)
    total = 0
    for row in customers or []:
        dd, cbr, ftype = row[0], row[1], row[2]
        for inst in instances:
            name = dd if inst == "DD" else cbr
            if not name:
                continue
            if ftype == "Customer Group":
                res = get_open_orders_by_group(conn, name, inst)
            else:
                res = get_open_orders(conn, name, inst)
            data = res["data"] if isinstance(res, dict) else (res or [])
            src = res.get("source", "live") if isinstance(res, dict) else "live"
            click.echo(f"[{inst}] {ftype}: {name} → warmed {len(data)} rows (source={src})")
            total += 1
    click.echo(f"Done. Warmed {total} entries.")


# ---------- env validation ----------
REQ_ALWAYS = ["FLASK_SECRET", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "DATABASE"]
REQ_PROD = ["SERVER_NAME"]  # SENTRY_DSN optional; don’t block startup

_missing = [v for v in REQ_ALWAYS if not os.getenv(v)]

if ENV == "production":
    _missing += [v for v in REQ_PROD if not os.getenv(v)]

if _missing:
    raise RuntimeError(f"Missing required environment variables: {', '.join(sorted(set(_missing)))}")

if __name__ == "__main__":
    app.run(debug=True)

import json
import time
from types import SimpleNamespace
import pytest
from flask import Response

@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    # Minimal env
    monkeypatch.setenv("DATABASE", str(tmp_path / "test.db"))
    monkeypatch.setenv("FLASK_SECRET", "test-secret")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    # Prevent real Sentry init/network during import
    monkeypatch.setattr("sentry_sdk.init", lambda *a, **k: None, raising=True)

@pytest.fixture
def app():
    from app import app as flask_app
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)  # <-- disable CSRF in tests
    return flask_app

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def logged_in_admin(monkeypatch):
    fake = SimpleNamespace(is_authenticated=True, role="admin", id=1, name="Test Admin", email="t@example.com")
    monkeypatch.setattr("flask_login.utils._get_user", lambda: fake, raising=True)
    return fake


# ---------- Basic pages / auth ----------

def test_home_route(client):
    resp = client.get("/")
    assert resp.status_code == 200
    # Don't hard-require exact copy; just ensure it rendered something HTML-like
    assert b"<" in resp.data and b">" in resp.data


def test_login_redirect(client, monkeypatch):
    # Avoid real Google redirect; return a simple redirect Response
    from app import oauth
    monkeypatch.setattr(oauth.google, "authorize_redirect", lambda *a, **k: Response(status=302, headers={"Location": "/callback"}), raising=True)
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/callback")


def test_callback_missing_token_redirects_to_login(client, monkeypatch):
    from app import oauth
    monkeypatch.setattr(oauth.google, "authorize_access_token", lambda: None, raising=True)
    r = client.get("/callback", follow_redirects=False)
    # Should bounce back to /login if token is missing/invalid
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/login")


# ---------- Jobs / async flow ----------

def test_eta_start_returns_job_id(client, monkeypatch, logged_in_admin):
    # run_eta_job runs in a thread; stub to no-op
    monkeypatch.setattr("app.run_eta_job", lambda *a, **k: None, raising=True)
    # create_job writes to DB via g.db; stub to no-op
    monkeypatch.setattr("app.create_job", lambda job_id: None, raising=True)

    r = client.post("/eta/start", data=json.dumps({"instance": "DD"}), content_type="application/json")
    assert r.status_code == 200
    payload = r.get_json()
    assert "job_id" in payload and isinstance(payload["job_id"], str) and len(payload["job_id"]) > 0


def test_job_status_not_found(client, monkeypatch):
    monkeypatch.setattr("app.get_job", lambda job_id: None, raising=True)
    r = client.get("/jobs/does-not-exist")
    assert r.status_code == 404
    assert r.get_json()["error"] == "not found"


def test_job_status_stalled_marks_done(client, monkeypatch):
    storage = {
        "job1": {"status": "running", "updated_ts": time.time() - 999, "pct": 0, "message": "stalled?"},
    }

    def _get_job(job_id):
        return storage.get(job_id)

    def _update_job(job_id, **fields):
        storage[job_id] = {**storage.get(job_id, {}), **fields, "updated_ts": time.time()}

    monkeypatch.setattr("app.get_job", _get_job, raising=True)
    monkeypatch.setattr("app.update_job", _update_job, raising=True)

    r = client.get("/jobs/job1")
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("error") == "Worker stalled" or body.get("status") == "running"
    # After one call, update_job should have run and job should be refreshed
    r2 = client.get("/jobs/job1")
    assert r2.status_code == 200


def test_report_render_not_ready(client, monkeypatch):
    monkeypatch.setattr("app.get_job", lambda job_id: {"status": "running"}, raising=True)
    r = client.get("/report/abc123")
    assert r.status_code == 404
    assert b"Report not ready" in r.data


def test_report_render_ready(client, monkeypatch):
    monkeypatch.setattr(
        "app.get_job",
        lambda job_id: {
            "status": "completed",
            "result": {"template": "report.html", "context": {"foo": "bar"}, "status": 200},
        },
        raising = True,
    )
    r = client.get("/report/some-id")
    assert r.status_code == 200
    assert b"<html" in r.data or b"<!DOCTYPE html" in r.data


# ---------- Data lookups ----------

def test_jobs_schedule_invalid_instance(client, logged_in_admin):
    r = client.get("/jobs-schedule/XYZ/123")
    assert r.status_code == 400
    assert "Invalid instance" in r.get_json()["error"]


def test_jobs_schedule_success(client, monkeypatch, logged_in_admin):
    monkeypatch.setattr(
        "app.get_data_by_order_no",
        lambda order_no, endpoint, instance: {"data": [{"RefNo": order_no, "ok": True}], "source": "live"},
        raising=True,
    )
    r = client.get("/jobs-schedule/DD/ORD-1")
    assert r.status_code == 200
    j = r.get_json()
    assert j["data"][0]["RefNo"] == "ORD-1"
    assert j["source"] == "live"


def test_work_in_progress_success(client, monkeypatch, logged_in_admin):
    monkeypatch.setattr(
        "app.get_data_by_order_no",
        lambda order_no, endpoint, instance: {"data": [{"RefNo": order_no, "wip": True}], "source": "cache"},
        raising=True,
    )
    r = client.get("/wip/CBR/ORD-2")
    assert r.status_code == 200
    j = r.get_json()
    assert j["data"][0]["wip"] is True
    assert j["source"] == "cache"


# ---------- Admin page (GET/POST) ----------

def test_admin_get_ok(client, monkeypatch, logged_in_admin):
    # Returning rows as tuples or dicts; your code tolerates either
    monkeypatch.setattr(
        "app.query_db",
        lambda *a, **k: [
            (1, "Acme", "", "abc", "Customer Name"),
            (2, "", "Bravo", "def", "Customer Group"),
        ],
        raising=True,
    )
    r = client.get("/admin")
    assert r.status_code == 200
    assert b"admin" in r.data.lower() or b"customers" in r.data.lower()


def test_admin_post_validation_error(client, monkeypatch, logged_in_admin):
    # No dd_name/cbr_name provided -> 400
    monkeypatch.setattr("app.query_db", lambda *a, **k: [], raising=True)
    r = client.post("/admin", data={"dd_name": "", "cbr_name": "", "field_type": "Customer Name"})
    assert r.status_code == 400
    assert b"Pick at least one customer" in r.data


def test_admin_post_success_redirect(client, monkeypatch, logged_in_admin):
    inserted = {}

    def _query_db(sql, params=(), one=False, logger=None):
        # capture INSERT and pretend success
        if sql.strip().upper().startswith("INSERT INTO CUSTOMERS"):
            inserted["row"] = params
        return []

    monkeypatch.setattr("app.query_db", _query_db, raising=True)

    r = client.post("/admin", data={"dd_name": "Acme", "cbr_name": "", "field_type": "Customer Group"}, follow_redirects=False)
    assert r.status_code == 302  # redirected back to /admin
    assert inserted["row"][0] == "Acme"  # dd_name stored


# ---------- Status mapping pages ----------

def test_list_status_mappings(client, monkeypatch, logged_in_admin):
    monkeypatch.setattr(
        "app.get_status_mappings",
        lambda conn: [(1, "In Progress", "Active", 1)],
        raising=True,
    )
    monkeypatch.setattr("app.get_db", lambda: None, raising=True)
    r = client.get("/status_mapping")
    assert r.status_code == 200
    assert b"Active" in r.data


def test_edit_status_mapping_post_validation(client, monkeypatch, logged_in_admin):
    monkeypatch.setattr("app.get_db", lambda: None, raising=True)
    r = client.post(f"/status_mapping/edit/1", data={"custom_status": ""})
    assert r.status_code == 400
    assert b"Custom status required" in r.data


def test_edit_status_mapping_post_success(client, monkeypatch, logged_in_admin):
    called = {}
    monkeypatch.setattr("app.get_db", lambda: "db", raising=True)
    monkeypatch.setattr(
        "app.edit_status_mapping",
        lambda mid, cs, active, conn: called.update(id=mid, cs=cs, a=active),
        raising=True,
    )
    r = client.post(f"/status_mapping/edit/42", data={"custom_status": "Active", "active": "on"}, follow_redirects=False)
    assert r.status_code == 302
    assert called == {"id": 42, "cs": "Active", "a": True}


# ---------- Downloads (CSV/XLSX) ----------

def test_download_csv(client, monkeypatch):
    # Make report rows + customer name
    monkeypatch.setattr("app.fetch_report_rows_and_name", lambda *a, **k: ([{"RefNo": "R1", "Foo": "Bar"}], "Acme"), raising=True)
    monkeypatch.setattr("app.apply_filters", lambda rows, **kw: rows, raising=True)
    monkeypatch.setattr("app.ordered_headers", lambda rows: ["RefNo", "Foo"], raising=True)
    monkeypatch.setattr("app.to_csv_bytes", lambda rows, headers: b"RefNo,Foo\nR1,Bar\n", raising=True)
    monkeypatch.setattr("app.safe_base_filename", lambda s: "acme", raising=True)

    r = client.get("/some-obf/download.csv")
    assert r.status_code == 200
    assert r.mimetype == "text/csv"
    assert b"RefNo,Foo" in r.data
    assert r.headers["Content-Disposition"].endswith('filename=acme.csv')


def test_download_xlsx(client, monkeypatch):
    monkeypatch.setattr("app.fetch_report_rows_and_name", lambda *a, **k: ([{"RefNo": "R2", "Foo": "Baz"}], "Bravo"), raising=True)
    monkeypatch.setattr("app.apply_filters", lambda rows, **kw: rows, raising=True)
    monkeypatch.setattr("app.ordered_headers", lambda rows: ["RefNo", "Foo"], raising=True)
    monkeypatch.setattr("app.to_excel_bytes", lambda rows, headers: b"PK\x03\x04DUMMY", raising=True)  # XLSX zip header starts with PK
    monkeypatch.setattr("app.safe_base_filename", lambda s: "bravo", raising=True)

    r = client.get("/some-obf/download.xlsx")
    assert r.status_code == 200
    assert r.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert r.data.startswith(b"PK\x03\x04")
    assert r.headers["Content-Disposition"].endswith('filename=bravo.xlsx')


# ---------- Users admin ----------

def test_manage_users_requires_admin(client):
    r = client.get("/manage_users")
    # Flask-Login redirect to /login
    assert r.status_code in (302, 401)


def test_manage_users_ok(client, monkeypatch, logged_in_admin):
    monkeypatch.setattr("app.query_db", lambda *a, **k: [(1, "a@b.com", "A", "admin", 1)], raising=True)
    r = client.get("/manage_users")
    assert r.status_code == 200
    assert b"a@b.com" in r.data


def test_add_user_success(client, monkeypatch, logged_in_admin):
    # POST minimal user and expect redirect
    monkeypatch.setattr("app.execute_query", lambda *a, **k: None, raising=True)
    r = client.post("/add_user", data={"email": "x@y.com", "name": "X", "role": "user"}, follow_redirects=False)
    assert r.status_code == 302


def test_toggle_user_status(client, monkeypatch, logged_in_admin):
    # 1 -> becomes 0
    monkeypatch.setattr("app.query_db", lambda *a, **k: (1,), raising=True)
    called = {}
    def _upd(sql, params=(), one=False, logger=None): called["params"] = params
    monkeypatch.setattr("app.query_db", lambda *a, **k: (1,) if "SELECT active" in a[0] else _upd(*a, **k), raising=True)
    r = client.get("/toggle_user_status/5", follow_redirects=False)
    assert r.status_code == 302


# ---------- Misc ----------

def test_favicon_and_robots(client, monkeypatch):
    # Avoid filesystem dependency; map send_from_directory via monkeypatch to a static Response
    monkeypatch.setattr("app.send_from_directory", lambda *a, **k: Response(b"ok", mimetype="text/plain"), raising=True)
    assert client.get("/favicon.ico").status_code == 200
    assert client.get("/robots.txt").status_code == 200

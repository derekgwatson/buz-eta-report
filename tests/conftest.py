import pytest
import os
import logging


@pytest.fixture
def runner(app):
    return app.test_cli_runner()


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    # minimal env so create_app() doesn't crash
    monkeypatch.setenv("DATABASE", str(tmp_path / "test.db"))
    monkeypatch.setenv("FLASK_SECRET", "test-secret")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    # OData credentials for tests
    monkeypatch.setenv("BUZ_DD_USERNAME", "test-dd-user")
    monkeypatch.setenv("BUZ_DD_PASSWORD", "test-dd-pass")
    monkeypatch.setenv("BUZ_CBR_USERNAME", "test-cbr-user")
    monkeypatch.setenv("BUZ_CBR_PASSWORD", "test-cbr-pass")
    # API key for testing
    monkeypatch.setenv("BUZ_API_KEY", "test-api-key-12345")
    # silence Sentry during tests
    monkeypatch.setattr("sentry_sdk.init", lambda *a, **k: None, raising=True)


@pytest.fixture
def app(_env):
    # import AFTER env + Sentry patch
    # _env fixture dependency ensures environment is set up first
    import app as app_module
    import logging
    app_module.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, TRAP_HTTP_EXCEPTIONS=False)
    logging.disable(logging.CRITICAL)  # silence everything during tests
    return app_module.app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def logged_in_admin(monkeypatch):
    # fake a logged-in admin for @login_required paths
    from types import SimpleNamespace
    fake = SimpleNamespace(is_authenticated=True, role="admin", id=1, name="Test Admin", email="t@example.com")
    monkeypatch.setattr("flask_login.utils._get_user", lambda: fake, raising=True)
    return fake


@pytest.fixture
def api_headers():
    """Standard headers for authenticated API requests."""
    return {"X-API-Key": "test-api-key-12345", "Content-Type": "application/json"}


@pytest.fixture(autouse=True, scope="session")
def _disable_sentry_and_quiet_logs():
    # Prevent Sentry from initializing/sending in tests
    os.environ.setdefault("SENTRY_DSN", "")
    os.environ["SENTRY_DISABLED"] = "1"  # optional flag your app can check

    # Keep noisy debug logs out of pytest's captured streams
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)

    yield

    # If Sentry might have initialized anyway, try to flush/stop threads gracefully
    try:
        import sentry_sdk
        sentry_sdk.flush(0)
    except Exception:
        pass

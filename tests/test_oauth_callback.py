"""Tests for OAuth callback edge cases in app.py /callback route."""
from types import SimpleNamespace
from flask import Response


def test_callback_expired_token_redirects(client, monkeypatch):
    """Token with expires_in <= 0 should redirect to login."""
    from app import oauth
    monkeypatch.setattr(
        oauth.google, "authorize_access_token",
        lambda: {"access_token": "tok", "expires_in": 0},
        raising=True,
    )
    r = client.get("/callback", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/login")


def test_callback_missing_email_redirects(client, monkeypatch):
    """If userinfo has no email, redirect to login."""
    from app import oauth
    monkeypatch.setattr(
        oauth.google, "authorize_access_token",
        lambda: {"access_token": "tok", "expires_in": 3600},
        raising=True,
    )
    fake_resp = SimpleNamespace(json=lambda: {"name": "Test User"})
    monkeypatch.setattr(oauth.google, "get", lambda *a, **kw: fake_resp, raising=True)

    r = client.get("/callback", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/login")


def test_callback_user_not_in_db_returns_403(client, monkeypatch):
    """User with valid token but not in users table gets 403."""
    from app import oauth
    monkeypatch.setattr(
        oauth.google, "authorize_access_token",
        lambda: {"access_token": "tok", "expires_in": 3600},
        raising=True,
    )
    fake_resp = SimpleNamespace(json=lambda: {"email": "nobody@example.com"})
    monkeypatch.setattr(oauth.google, "get", lambda *a, **kw: fake_resp, raising=True)
    monkeypatch.setattr("app.query_db", lambda *a, **kw: None, raising=True)

    r = client.get("/callback")
    assert r.status_code == 403


def test_callback_inactive_user_returns_403(client, monkeypatch):
    """User in DB but active=0 gets 403."""
    from app import oauth
    monkeypatch.setattr(
        oauth.google, "authorize_access_token",
        lambda: {"access_token": "tok", "expires_in": 3600},
        raising=True,
    )
    fake_resp = SimpleNamespace(json=lambda: {"email": "inactive@example.com"})
    monkeypatch.setattr(oauth.google, "get", lambda *a, **kw: fake_resp, raising=True)

    # Row: (id, email, name, role, active) — active=0
    fake_row = (1, "inactive@example.com", "Inactive", "user", 0)
    monkeypatch.setattr("app.query_db", lambda *a, **kw: fake_row, raising=True)

    r = client.get("/callback")
    assert r.status_code == 403


def test_callback_valid_user_logs_in(client, monkeypatch):
    """Valid token + active user should redirect to admin."""
    from app import oauth
    monkeypatch.setattr(
        oauth.google, "authorize_access_token",
        lambda: {"access_token": "tok", "expires_in": 3600},
        raising=True,
    )
    fake_resp = SimpleNamespace(json=lambda: {"email": "admin@example.com"})
    monkeypatch.setattr(oauth.google, "get", lambda *a, **kw: fake_resp, raising=True)

    # Row: (id, email, name, role, active) — active=1
    fake_row = (1, "admin@example.com", "Admin", "admin", 1)
    monkeypatch.setattr("app.query_db", lambda *a, **kw: fake_row, raising=True)

    r = client.get("/callback", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/admin")

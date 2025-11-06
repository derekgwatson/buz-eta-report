# tests/test_cache_service.py
"""
Tests for services/cache.py and services/fetcher.py

These modules handle caching, blackout detection, and fallback logic
when the OData API is unavailable.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
import requests

from services.cache import (
    ensure_cache_table,
    get_cache,
    set_cache,
    is_blackout,
    cache_fresh_enough,
)
from services.fetcher import fetch_or_cached

SYD = ZoneInfo("Australia/Sydney")


# ---------------------------
# Fixtures
# ---------------------------

@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary SQLite database for testing."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_cache_table(conn)
    yield conn
    conn.close()


# ---------------------------
# Test: ensure_cache_table
# ---------------------------

def test_ensure_cache_table_creates_table(temp_db):
    """Verify cache table creation."""
    cursor = temp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='cache'"
    )
    assert cursor.fetchone() is not None


def test_ensure_cache_table_is_idempotent(temp_db):
    """Running ensure_cache_table multiple times should not error."""
    ensure_cache_table(temp_db)
    ensure_cache_table(temp_db)  # Should not raise


# ---------------------------
# Test: set_cache & get_cache
# ---------------------------

def test_set_and_get_cache_basic(temp_db):
    """Basic cache set and retrieve."""
    payload = {"orders": [{"RefNo": "R1"}]}
    set_cache("test_key", payload, conn=temp_db)

    entry = get_cache("test_key", conn=temp_db)
    assert entry is not None
    assert entry.key == "test_key"
    assert entry.payload == payload
    assert isinstance(entry.updated_at_utc, datetime)
    assert entry.updated_at_utc.tzinfo == timezone.utc


def test_get_cache_nonexistent_returns_none(temp_db):
    """Getting a non-existent cache key returns None."""
    entry = get_cache("does_not_exist", conn=temp_db)
    assert entry is None


def test_set_cache_with_metadata(temp_db):
    """Cache entries can include metadata."""
    payload = {"data": [1, 2, 3]}
    meta = {"source": "live", "refreshed_by": "prewarm"}
    set_cache("key_with_meta", payload, meta=meta, conn=temp_db)

    entry = get_cache("key_with_meta", conn=temp_db)
    assert entry.meta == meta


def test_set_cache_updates_existing_entry(temp_db):
    """Setting the same key twice updates the entry."""
    set_cache("update_test", {"v": 1}, conn=temp_db)
    time.sleep(0.1)  # Ensure timestamp differs
    set_cache("update_test", {"v": 2}, conn=temp_db)

    entry = get_cache("update_test", conn=temp_db)
    assert entry.payload == {"v": 2}


def test_set_cache_handles_unicode_and_special_chars(temp_db):
    """Cache handles unicode and special characters."""
    payload = {"customer": "O'Malley's Café", "notes": "Emojis: 🎉✅"}
    set_cache("unicode_test", payload, conn=temp_db)

    entry = get_cache("unicode_test", conn=temp_db)
    assert entry.payload == payload


# ---------------------------
# Test: is_blackout
# ---------------------------

def test_is_blackout_during_blackout_hours():
    """10:00-15:59 Sydney time is blackout."""
    # 10:00 AM Sydney
    t1 = datetime(2025, 11, 5, 10, 0, 0, tzinfo=SYD)
    assert is_blackout(t1) is True

    # 12:30 PM Sydney (midday)
    t2 = datetime(2025, 11, 5, 12, 30, 0, tzinfo=SYD)
    assert is_blackout(t2) is True

    # 15:59 Sydney (just before end)
    t3 = datetime(2025, 11, 5, 15, 59, 59, tzinfo=SYD)
    assert is_blackout(t3) is True


def test_is_blackout_outside_blackout_hours():
    """Before 10:00 and after 16:00 Sydney time is not blackout."""
    # 9:59 AM Sydney (just before start)
    t1 = datetime(2025, 11, 5, 9, 59, 59, tzinfo=SYD)
    assert is_blackout(t1) is False

    # 16:00 Sydney (at end boundary)
    t2 = datetime(2025, 11, 5, 16, 0, 0, tzinfo=SYD)
    assert is_blackout(t2) is False

    # 23:00 Sydney (night)
    t3 = datetime(2025, 11, 5, 23, 0, 0, tzinfo=SYD)
    assert is_blackout(t3) is False

    # 3:00 AM Sydney (early morning)
    t4 = datetime(2025, 11, 5, 3, 0, 0, tzinfo=SYD)
    assert is_blackout(t4) is False


def test_is_blackout_works_with_dst_transitions():
    """Blackout detection works during daylight saving transitions."""
    # During Australian DST (October-April): UTC+11
    # Outside DST (April-October): UTC+10

    # Summer: 10 AM AEDT (UTC+11)
    summer = datetime(2025, 1, 15, 10, 0, 0, tzinfo=SYD)
    assert is_blackout(summer) is True

    # Winter: 10 AM AEST (UTC+10)
    winter = datetime(2025, 7, 15, 10, 0, 0, tzinfo=SYD)
    assert is_blackout(winter) is True


# ---------------------------
# Test: cache_fresh_enough
# ---------------------------

def test_cache_fresh_enough_within_max_age(temp_db):
    """Cache is fresh if updated within max_age_minutes."""
    set_cache("fresh_test", {"data": "value"}, conn=temp_db)
    entry = get_cache("fresh_test", conn=temp_db)

    # Just updated, should be fresh for 15 minutes
    assert cache_fresh_enough(entry, max_age_minutes=15) is True


def test_cache_fresh_enough_exceeds_max_age(temp_db):
    """Cache is stale if older than max_age_minutes."""
    # Manually insert old cache entry
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(timespec="seconds")
    temp_db.execute(
        "INSERT INTO cache (cache_key, payload_json, updated_at_utc, meta_json) VALUES (?, ?, ?, ?)",
        ("old_key", json.dumps({"v": 1}), old_time, "{}"),
    )
    temp_db.commit()

    entry = get_cache("old_key", conn=temp_db)
    assert cache_fresh_enough(entry, max_age_minutes=15) is False


def test_cache_fresh_enough_zero_max_age_always_stale(temp_db):
    """max_age_minutes=0 means always force refresh."""
    set_cache("zero_age", {"data": "value"}, conn=temp_db)
    entry = get_cache("zero_age", conn=temp_db)

    assert cache_fresh_enough(entry, max_age_minutes=0) is False


def test_cache_fresh_enough_negative_max_age_always_stale(temp_db):
    """Negative max_age_minutes means always force refresh."""
    set_cache("negative_age", {"data": "value"}, conn=temp_db)
    entry = get_cache("negative_age", conn=temp_db)

    assert cache_fresh_enough(entry, max_age_minutes=-5) is False


# ---------------------------
# Test: fetch_or_cached - basic functionality
# ---------------------------

def test_fetch_or_cached_calls_fetch_fn_when_no_cache(temp_db, monkeypatch):
    """When no cache exists, calls fetch_fn and caches result."""
    monkeypatch.setattr("services.fetcher.ensure_cache_table", lambda: None)
    monkeypatch.setattr("services.fetcher.get_cache", lambda k: None)

    called = []

    def mock_set_cache(key, payload, meta=None):
        called.append({"key": key, "payload": payload, "meta": meta})

    monkeypatch.setattr("services.fetcher.set_cache", mock_set_cache)

    def fetch_fn():
        return {"orders": [{"RefNo": "R1"}]}

    result, source = fetch_or_cached(
        cache_key="test_key",
        fetch_fn=fetch_fn,
    )

    assert result == {"orders": [{"RefNo": "R1"}]}
    assert source == "live"
    assert len(called) == 1
    assert called[0]["key"] == "test_key"


def test_fetch_or_cached_returns_fresh_cache_when_available(temp_db, monkeypatch):
    """When cache is fresh, skips fetch_fn."""
    # Create fresh cache entry
    from services.cache import CacheEntry
    fresh_entry = CacheEntry(
        key="fresh_key",
        payload={"cached": "data"},
        updated_at_utc=datetime.now(timezone.utc),
        meta={}
    )

    monkeypatch.setattr("services.fetcher.ensure_cache_table", lambda: None)
    monkeypatch.setattr("services.fetcher.get_cache", lambda k: fresh_entry)

    fetch_called = []

    def fetch_fn():
        fetch_called.append(True)
        return {"should": "not see this"}

    result, source = fetch_or_cached(
        cache_key="fresh_key",
        fetch_fn=fetch_fn,
        force_refresh=False,
        max_age_minutes_when_open=15,
    )

    assert result == {"cached": "data"}
    assert source == "cache"
    assert len(fetch_called) == 0  # fetch_fn should not be called


def test_fetch_or_cached_force_refresh_skips_cache(temp_db, monkeypatch):
    """force_refresh=True always calls fetch_fn."""
    from services.cache import CacheEntry
    fresh_entry = CacheEntry(
        key="force_key",
        payload={"cached": "old"},
        updated_at_utc=datetime.now(timezone.utc),
        meta={}
    )

    monkeypatch.setattr("services.fetcher.ensure_cache_table", lambda: None)
    monkeypatch.setattr("services.fetcher.get_cache", lambda k: fresh_entry)

    set_cache_called = []

    def mock_set_cache(key, payload, meta=None):
        set_cache_called.append(payload)

    monkeypatch.setattr("services.fetcher.set_cache", mock_set_cache)

    def fetch_fn():
        return {"new": "data"}

    result, source = fetch_or_cached(
        cache_key="force_key",
        fetch_fn=fetch_fn,
        force_refresh=True,
    )

    assert result == {"new": "data"}
    assert source == "live"
    assert len(set_cache_called) == 1


# ---------------------------
# Test: fetch_or_cached - fallback on 503
# ---------------------------

def test_fetch_or_cached_fallback_on_503(temp_db, monkeypatch):
    """When fetch_fn raises HTTPError 503, returns cache."""
    from services.cache import CacheEntry
    cached_entry = CacheEntry(
        key="503_key",
        payload={"cached": "fallback"},
        updated_at_utc=datetime.now(timezone.utc) - timedelta(hours=1),
        meta={}
    )

    monkeypatch.setattr("services.fetcher.ensure_cache_table", lambda: None)
    monkeypatch.setattr("services.fetcher.get_cache", lambda k: cached_entry)

    set_cache_called = []

    def mock_set_cache(key, payload, meta=None):
        set_cache_called.append(meta)

    monkeypatch.setattr("services.fetcher.set_cache", mock_set_cache)

    def fetch_fn():
        resp = MagicMock()
        resp.status_code = 503
        raise requests.HTTPError(response=resp)

    result, source = fetch_or_cached(
        cache_key="503_key",
        fetch_fn=fetch_fn,
        fallback_http_statuses=(503,),
        force_refresh=True,  # Force refresh to trigger fetch_fn
    )

    assert result == {"cached": "fallback"}
    assert source == "cache-503"
    # Should update meta with last_503_at_utc
    assert len(set_cache_called) == 1
    assert "last_503_at_utc" in set_cache_called[0]


def test_fetch_or_cached_raises_on_503_without_cache(temp_db, monkeypatch):
    """When 503 occurs and no cache, raises error."""
    monkeypatch.setattr("services.fetcher.ensure_cache_table", lambda: None)
    monkeypatch.setattr("services.fetcher.get_cache", lambda k: None)

    def fetch_fn():
        resp = MagicMock()
        resp.status_code = 503
        raise requests.HTTPError(response=resp)

    with pytest.raises(RuntimeError, match="unable to generate your report"):
        fetch_or_cached(
            cache_key="no_cache_503",
            fetch_fn=fetch_fn,
            fallback_http_statuses=(503,),
        )


# ---------------------------
# Test: fetch_or_cached - cooldown after 503
# ---------------------------

def test_fetch_or_cached_cooldown_after_503(temp_db, monkeypatch):
    """After 503, avoid hitting API during cooldown period."""
    from services.cache import CacheEntry

    # Cache entry with recent 503
    recent_503 = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")
    cached_entry = CacheEntry(
        key="cooldown_key",
        payload={"cached": "during_cooldown"},
        updated_at_utc=datetime.now(timezone.utc) - timedelta(hours=1),
        meta={"last_503_at_utc": recent_503}
    )

    monkeypatch.setattr("services.fetcher.ensure_cache_table", lambda: None)
    monkeypatch.setattr("services.fetcher.get_cache", lambda k: cached_entry)

    fetch_called = []

    def fetch_fn():
        fetch_called.append(True)
        return {"should": "not call"}

    result, source = fetch_or_cached(
        cache_key="cooldown_key",
        fetch_fn=fetch_fn,
        cooldown_on_503_minutes=10,  # 10 minute cooldown
        force_refresh=True,  # Even with force, cooldown wins
    )

    assert result == {"cached": "during_cooldown"}
    assert source == "cache"
    assert len(fetch_called) == 0  # Should not call fetch_fn during cooldown


def test_fetch_or_cached_cooldown_expired_calls_fetch(temp_db, monkeypatch):
    """After cooldown expires, calls fetch_fn again."""
    from services.cache import CacheEntry

    # Cache entry with old 503 (cooldown expired)
    old_503 = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat(timespec="seconds")
    cached_entry = CacheEntry(
        key="cooldown_expired",
        payload={"cached": "old"},
        updated_at_utc=datetime.now(timezone.utc) - timedelta(hours=1),
        meta={"last_503_at_utc": old_503}
    )

    monkeypatch.setattr("services.fetcher.ensure_cache_table", lambda: None)
    monkeypatch.setattr("services.fetcher.get_cache", lambda k: cached_entry)
    monkeypatch.setattr("services.fetcher.set_cache", lambda *a, **k: None)

    def fetch_fn():
        return {"new": "data"}

    result, source = fetch_or_cached(
        cache_key="cooldown_expired",
        fetch_fn=fetch_fn,
        cooldown_on_503_minutes=10,  # 10 minute cooldown (expired)
        force_refresh=True,
    )

    assert result == {"new": "data"}
    assert source == "live"


# ---------------------------
# Test: fetch_or_cached - timeout fallback
# ---------------------------

def test_fetch_or_cached_fallback_on_timeout(temp_db, monkeypatch):
    """When fetch_fn times out, returns cache."""
    from services.cache import CacheEntry
    cached_entry = CacheEntry(
        key="timeout_key",
        payload={"cached": "after_timeout"},
        updated_at_utc=datetime.now(timezone.utc) - timedelta(hours=1),
        meta={}
    )

    monkeypatch.setattr("services.fetcher.ensure_cache_table", lambda: None)
    monkeypatch.setattr("services.fetcher.get_cache", lambda k: cached_entry)

    def fetch_fn():
        raise requests.Timeout("Connection timed out")

    result, source = fetch_or_cached(
        cache_key="timeout_key",
        fetch_fn=fetch_fn,
        fallback_on_timeouts=True,
        force_refresh=True,
    )

    assert result == {"cached": "after_timeout"}
    assert source == "cache-timeout"


def test_fetch_or_cached_raises_on_timeout_without_fallback(temp_db, monkeypatch):
    """When fallback_on_timeouts=False, raises RuntimeError with user-friendly message."""
    monkeypatch.setattr("services.fetcher.ensure_cache_table", lambda: None)
    monkeypatch.setattr("services.fetcher.get_cache", lambda k: None)

    def fetch_fn():
        raise requests.Timeout("Connection timed out")

    with pytest.raises(RuntimeError, match="taking longer than expected"):
        fetch_or_cached(
            cache_key="no_fallback_timeout",
            fetch_fn=fetch_fn,
            fallback_on_timeouts=False,
        )


# ---------------------------
# Test: fetch_or_cached - connection error fallback
# ---------------------------

def test_fetch_or_cached_fallback_on_connection_error(temp_db, monkeypatch):
    """When fetch_fn has connection error, returns cache."""
    from services.cache import CacheEntry
    cached_entry = CacheEntry(
        key="conn_error_key",
        payload={"cached": "after_conn_error"},
        updated_at_utc=datetime.now(timezone.utc) - timedelta(hours=1),
        meta={}
    )

    monkeypatch.setattr("services.fetcher.ensure_cache_table", lambda: None)
    monkeypatch.setattr("services.fetcher.get_cache", lambda k: cached_entry)

    def fetch_fn():
        raise requests.ConnectionError("Network unreachable")

    result, source = fetch_or_cached(
        cache_key="conn_error_key",
        fetch_fn=fetch_fn,
        fallback_on_conn_errors=True,
        force_refresh=True,
    )

    assert result == {"cached": "after_conn_error"}
    assert source == "cache-error"


# ---------------------------
# Test: fetch_or_cached - BUZ_FORCE_503 simulation
# ---------------------------

def test_fetch_or_cached_buz_force_503_env_var(temp_db, monkeypatch):
    """BUZ_FORCE_503=1 simulates 503 and serves cache."""
    from services.cache import CacheEntry
    cached_entry = CacheEntry(
        key="force_503_key",
        payload={"cached": "simulated_503"},
        updated_at_utc=datetime.now(timezone.utc),
        meta={}
    )

    monkeypatch.setenv("BUZ_FORCE_503", "1")
    monkeypatch.setattr("services.fetcher.ensure_cache_table", lambda: None)
    monkeypatch.setattr("services.fetcher.get_cache", lambda k: cached_entry)

    set_cache_called = []

    def mock_set_cache(key, payload, meta=None):
        set_cache_called.append(meta)

    monkeypatch.setattr("services.fetcher.set_cache", mock_set_cache)

    fetch_called = []

    def fetch_fn():
        fetch_called.append(True)
        return {"should": "not call"}

    result, source = fetch_or_cached(
        cache_key="force_503_key",
        fetch_fn=fetch_fn,
    )

    assert result == {"cached": "simulated_503"}
    assert source == "cache-503-sim"
    assert len(fetch_called) == 0  # Should not call fetch_fn
    # Should stamp last_503_at_utc
    assert len(set_cache_called) == 1
    assert "last_503_at_utc" in set_cache_called[0]


def test_fetch_or_cached_buz_force_503_without_cache_raises(temp_db, monkeypatch):
    """BUZ_FORCE_503=1 without cache raises error."""
    monkeypatch.setenv("BUZ_FORCE_503", "1")
    monkeypatch.setattr("services.fetcher.ensure_cache_table", lambda: None)
    monkeypatch.setattr("services.fetcher.get_cache", lambda k: None)

    def fetch_fn():
        return {"should": "not call"}

    with pytest.raises(RuntimeError, match="Simulated 503 and no cached data"):
        fetch_or_cached(
            cache_key="no_cache_force_503",
            fetch_fn=fetch_fn,
        )


# ---------------------------
# Test: fetch_or_cached - multiple fallback statuses
# ---------------------------

def test_fetch_or_cached_fallback_on_500(temp_db, monkeypatch):
    """Can configure multiple fallback HTTP statuses."""
    from services.cache import CacheEntry
    cached_entry = CacheEntry(
        key="500_key",
        payload={"cached": "after_500"},
        updated_at_utc=datetime.now(timezone.utc),
        meta={}
    )

    monkeypatch.setattr("services.fetcher.ensure_cache_table", lambda: None)
    monkeypatch.setattr("services.fetcher.get_cache", lambda k: cached_entry)
    monkeypatch.setattr("services.fetcher.set_cache", lambda *a, **k: None)

    def fetch_fn():
        resp = MagicMock()
        resp.status_code = 500
        raise requests.HTTPError(response=resp)

    result, source = fetch_or_cached(
        cache_key="500_key",
        fetch_fn=fetch_fn,
        fallback_http_statuses=(500, 502, 503),  # Multiple statuses
        force_refresh=True,
    )

    assert result == {"cached": "after_500"}
    assert source.startswith("cache")


def test_fetch_or_cached_does_not_fallback_on_400(temp_db, monkeypatch):
    """4xx errors should not trigger fallback (client errors)."""
    from services.cache import CacheEntry
    cached_entry = CacheEntry(
        key="400_key",
        payload={"cached": "should_not_use"},
        updated_at_utc=datetime.now(timezone.utc),
        meta={}
    )

    monkeypatch.setattr("services.fetcher.ensure_cache_table", lambda: None)
    monkeypatch.setattr("services.fetcher.get_cache", lambda k: cached_entry)

    def fetch_fn():
        resp = MagicMock()
        resp.status_code = 400
        raise requests.HTTPError(response=resp)

    with pytest.raises(requests.HTTPError):
        fetch_or_cached(
            cache_key="400_key",
            fetch_fn=fetch_fn,
            fallback_http_statuses=(503,),  # Only 503
            force_refresh=True,
        )

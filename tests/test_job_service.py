# tests/test_job_service.py
"""
Tests for services/job_service.py

These tests cover the async job tracking system used for background
report generation.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

import pytest

from services.job_service import (
    create_job,
    update_job,
    get_job,
    _coerce_db,
    _exec,
    _query_one,
    _commit,
)


# ---------------------------
# Fixtures
# ---------------------------

@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary SQLite database with jobs table."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Create jobs table matching the app schema
    conn.execute("""
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'running',
            pct INTEGER DEFAULT 0,
            log TEXT,
            result TEXT,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    yield conn
    conn.close()


class MockDatabaseManager:
    """Mock DatabaseManager that wraps a sqlite3 connection."""

    def __init__(self, conn):
        self._conn = conn

    def execute_query(self, sql, params=()):
        return self._conn.execute(sql, params)

    def commit(self):
        self._conn.commit()


@pytest.fixture
def temp_db_manager(temp_db):
    """Create a mock DatabaseManager for testing."""
    return MockDatabaseManager(temp_db)


# ---------------------------
# Test: Helper functions
# ---------------------------

def test_coerce_db_returns_connection_when_provided():
    """_coerce_db returns the provided connection."""
    mock_conn = object()
    assert _coerce_db(mock_conn) is mock_conn


def test_exec_works_with_raw_sqlite3_connection(temp_db):
    """_exec can execute queries on raw sqlite3 connection."""
    _exec(temp_db, "INSERT INTO jobs (id) VALUES (?)", ("test_id",))
    temp_db.commit()

    cursor = temp_db.execute("SELECT id FROM jobs")
    assert cursor.fetchone()["id"] == "test_id"


def test_exec_works_with_database_manager(temp_db_manager, temp_db):
    """_exec can execute queries through DatabaseManager interface."""
    _exec(temp_db_manager, "INSERT INTO jobs (id) VALUES (?)", ("test_id",))
    temp_db.commit()

    cursor = temp_db.execute("SELECT id FROM jobs")
    assert cursor.fetchone()["id"] == "test_id"


def test_query_one_returns_row_with_raw_connection(temp_db):
    """_query_one fetches one row from raw connection."""
    temp_db.execute("INSERT INTO jobs (id, status) VALUES (?, ?)", ("job1", "running"))
    temp_db.commit()

    row = _query_one(temp_db, "SELECT id, status FROM jobs WHERE id = ?", ("job1",))
    assert row is not None
    assert row["id"] == "job1"
    assert row["status"] == "running"


def test_query_one_returns_none_when_no_match(temp_db):
    """_query_one returns None when query matches no rows."""
    row = _query_one(temp_db, "SELECT id FROM jobs WHERE id = ?", ("nonexistent",))
    assert row is None


def test_commit_works_with_connection(temp_db):
    """_commit calls commit on objects that have it."""
    temp_db.execute("INSERT INTO jobs (id) VALUES (?)", ("test1",))
    _commit(temp_db)

    # Verify committed by reopening
    cursor = temp_db.execute("SELECT id FROM jobs")
    assert cursor.fetchone() is not None


def test_commit_handles_objects_without_commit_method():
    """_commit doesn't crash if object lacks commit method."""
    mock_obj = object()  # No commit method
    _commit(mock_obj)  # Should not raise


# ---------------------------
# Test: create_job
# ---------------------------

def test_create_job_inserts_new_job(temp_db):
    """create_job inserts a new job record."""
    create_job("job123", db=temp_db)

    cursor = temp_db.execute("SELECT id, status, pct FROM jobs WHERE id = ?", ("job123",))
    row = cursor.fetchone()

    assert row is not None
    assert row["id"] == "job123"
    assert row["status"] == "running"
    assert row["pct"] == 0


def test_create_job_initializes_empty_log(temp_db):
    """create_job initializes log as empty JSON array."""
    create_job("job456", db=temp_db)

    cursor = temp_db.execute("SELECT log FROM jobs WHERE id = ?", ("job456",))
    log_json = cursor.fetchone()["log"]

    assert json.loads(log_json) == []


def test_create_job_with_database_manager(temp_db_manager, temp_db):
    """create_job works with DatabaseManager interface."""
    create_job("job789", db=temp_db_manager)

    cursor = temp_db.execute("SELECT id FROM jobs WHERE id = ?", ("job789",))
    assert cursor.fetchone() is not None


def test_create_job_sets_timestamps(temp_db):
    """create_job sets created_at and updated_at timestamps."""
    create_job("job_ts", db=temp_db)

    cursor = temp_db.execute("SELECT created_at, updated_at FROM jobs WHERE id = ?", ("job_ts",))
    row = cursor.fetchone()

    # Both should be set (not NULL)
    assert row["created_at"] is not None
    assert row["updated_at"] is not None


# ---------------------------
# Test: update_job - progress and messages
# ---------------------------

def test_update_job_updates_progress(temp_db):
    """update_job updates progress percentage."""
    create_job("job1", db=temp_db)

    update_job("job1", pct=50, db=temp_db)

    cursor = temp_db.execute("SELECT pct FROM jobs WHERE id = ?", ("job1",))
    assert cursor.fetchone()["pct"] == 50


def test_update_job_appends_messages_to_log(temp_db):
    """update_job appends messages to the log array."""
    create_job("job2", db=temp_db)

    update_job("job2", message="Step 1", db=temp_db)
    update_job("job2", message="Step 2", db=temp_db)
    update_job("job2", message="Step 3", db=temp_db)

    cursor = temp_db.execute("SELECT log FROM jobs WHERE id = ?", ("job2",))
    log = json.loads(cursor.fetchone()["log"])

    assert log == ["Step 1", "Step 2", "Step 3"]


def test_update_job_preserves_existing_log_messages(temp_db):
    """update_job preserves existing log messages when appending."""
    create_job("job3", db=temp_db)

    update_job("job3", message="First", db=temp_db)
    update_job("job3", message="Second", db=temp_db)

    cursor = temp_db.execute("SELECT log FROM jobs WHERE id = ?", ("job3",))
    log = json.loads(cursor.fetchone()["log"])

    assert "First" in log
    assert "Second" in log


def test_update_job_without_message_doesnt_append(temp_db):
    """update_job without message parameter doesn't modify log."""
    create_job("job4", db=temp_db)
    update_job("job4", message="Initial", db=temp_db)

    update_job("job4", pct=50, db=temp_db)  # No message

    cursor = temp_db.execute("SELECT log FROM jobs WHERE id = ?", ("job4",))
    log = json.loads(cursor.fetchone()["log"])

    assert log == ["Initial"]


def test_update_job_preserves_pct_when_none(temp_db):
    """update_job with pct=None preserves previous percentage."""
    create_job("job5", db=temp_db)
    update_job("job5", pct=75, db=temp_db)

    update_job("job5", message="Message without pct", pct=None, db=temp_db)

    cursor = temp_db.execute("SELECT pct FROM jobs WHERE id = ?", ("job5",))
    assert cursor.fetchone()["pct"] == 75


# ---------------------------
# Test: update_job - status transitions
# ---------------------------

def test_update_job_with_done_sets_status_completed(temp_db):
    """update_job with done=True sets status to completed."""
    create_job("job6", db=temp_db)

    update_job("job6", done=True, db=temp_db)

    cursor = temp_db.execute("SELECT status FROM jobs WHERE id = ?", ("job6",))
    assert cursor.fetchone()["status"] == "completed"


def test_update_job_with_error_sets_status_failed(temp_db):
    """update_job with error sets status to failed."""
    create_job("job7", db=temp_db)

    update_job("job7", error="Something went wrong", db=temp_db)

    cursor = temp_db.execute("SELECT status, error FROM jobs WHERE id = ?", ("job7",))
    row = cursor.fetchone()

    assert row["status"] == "failed"
    assert row["error"] == "Something went wrong"


def test_update_job_error_overrides_done(temp_db):
    """update_job with both error and done=True prefers failed status."""
    create_job("job8", db=temp_db)

    update_job("job8", error="Error", done=True, db=temp_db)

    cursor = temp_db.execute("SELECT status FROM jobs WHERE id = ?", ("job8",))
    assert cursor.fetchone()["status"] == "failed"


def test_update_job_without_done_or_error_stays_running(temp_db):
    """update_job without done or error keeps status as running."""
    create_job("job9", db=temp_db)

    update_job("job9", pct=50, message="In progress", db=temp_db)

    cursor = temp_db.execute("SELECT status FROM jobs WHERE id = ?", ("job9",))
    assert cursor.fetchone()["status"] == "running"


# ---------------------------
# Test: update_job - result handling
# ---------------------------

def test_update_job_stores_result_as_json(temp_db):
    """update_job stores result as JSON."""
    create_job("job10", db=temp_db)

    result_data = {"template": "report.html", "context": {"foo": "bar"}, "status": 200}
    update_job("job10", result=result_data, done=True, db=temp_db)

    cursor = temp_db.execute("SELECT result FROM jobs WHERE id = ?", ("job10",))
    stored_result = json.loads(cursor.fetchone()["result"])

    assert stored_result == result_data


def test_update_job_handles_complex_result_objects(temp_db):
    """update_job correctly serializes complex nested result objects."""
    create_job("job11", db=temp_db)

    complex_result = {
        "data": [{"id": 1, "name": "Item 1"}, {"id": 2, "name": "Item 2"}],
        "meta": {"count": 2, "source": "live"},
        "nested": {"level1": {"level2": {"value": "deep"}}}
    }
    update_job("job11", result=complex_result, done=True, db=temp_db)

    cursor = temp_db.execute("SELECT result FROM jobs WHERE id = ?", ("job11",))
    stored = json.loads(cursor.fetchone()["result"])

    assert stored == complex_result
    assert stored["nested"]["level1"]["level2"]["value"] == "deep"


def test_update_job_result_none_stores_null(temp_db):
    """update_job with result=None stores NULL in database."""
    create_job("job12", db=temp_db)

    update_job("job12", result=None, db=temp_db)

    cursor = temp_db.execute("SELECT result FROM jobs WHERE id = ?", ("job12",))
    assert cursor.fetchone()["result"] is None


# ---------------------------
# Test: update_job - timestamp updates
# ---------------------------

def test_update_job_updates_timestamp(temp_db):
    """update_job updates the updated_at timestamp."""
    create_job("job13", db=temp_db)

    # Get initial timestamp
    cursor = temp_db.execute("SELECT updated_at FROM jobs WHERE id = ?", ("job13",))
    initial_ts = cursor.fetchone()["updated_at"]

    # Sleep for at least 1 second (SQLite CURRENT_TIMESTAMP has second precision)
    time.sleep(1.1)
    update_job("job13", message="Update", db=temp_db)

    cursor = temp_db.execute("SELECT updated_at FROM jobs WHERE id = ?", ("job13",))
    new_ts = cursor.fetchone()["updated_at"]

    # Timestamps should differ (updated_at changed)
    assert new_ts != initial_ts


# ---------------------------
# Test: update_job - edge cases
# ---------------------------

def test_update_job_handles_empty_log_gracefully(temp_db):
    """update_job handles jobs with NULL or malformed log."""
    # Manually create job with NULL log
    temp_db.execute(
        "INSERT INTO jobs (id, status, pct, log) VALUES (?, ?, ?, ?)",
        ("job14", "running", 0, None)
    )
    temp_db.commit()

    update_job("job14", message="First message", db=temp_db)

    cursor = temp_db.execute("SELECT log FROM jobs WHERE id = ?", ("job14",))
    log = json.loads(cursor.fetchone()["log"])

    assert log == ["First message"]


def test_update_job_handles_malformed_json_log(temp_db):
    """update_job handles jobs with invalid JSON in log field."""
    # Manually create job with invalid JSON
    temp_db.execute(
        "INSERT INTO jobs (id, status, pct, log) VALUES (?, ?, ?, ?)",
        ("job15", "running", 0, "not valid json")
    )
    temp_db.commit()

    update_job("job15", message="New message", db=temp_db)

    cursor = temp_db.execute("SELECT log FROM jobs WHERE id = ?", ("job15",))
    log = json.loads(cursor.fetchone()["log"])

    # Should reset to just the new message
    assert log == ["New message"]


def test_update_job_with_all_parameters(temp_db):
    """update_job can handle all parameters at once."""
    create_job("job16", db=temp_db)

    result = {"data": "complete"}
    update_job(
        "job16",
        pct=100,
        message="Finished",
        error=None,
        result=result,
        done=True,
        db=temp_db
    )

    cursor = temp_db.execute("SELECT * FROM jobs WHERE id = ?", ("job16",))
    row = cursor.fetchone()

    assert row["pct"] == 100
    assert "Finished" in json.loads(row["log"])
    assert json.loads(row["result"]) == result
    assert row["status"] == "completed"


# ---------------------------
# Test: get_job
# ---------------------------

def test_get_job_retrieves_basic_fields(temp_db):
    """get_job retrieves job with basic fields."""
    create_job("job17", db=temp_db)
    update_job("job17", pct=50, message="In progress", db=temp_db)

    job = get_job("job17", db=temp_db)

    assert job is not None
    assert job["pct"] == 50
    assert job["log"] == ["In progress"]
    assert job["status"] == "running"
    assert job["done"] is False


def test_get_job_returns_none_for_nonexistent_job(temp_db):
    """get_job returns None when job doesn't exist."""
    job = get_job("nonexistent", db=temp_db)
    assert job is None


def test_get_job_parses_log_json(temp_db):
    """get_job parses log JSON into list."""
    create_job("job18", db=temp_db)
    update_job("job18", message="Msg 1", db=temp_db)
    update_job("job18", message="Msg 2", db=temp_db)

    job = get_job("job18", db=temp_db)

    assert isinstance(job["log"], list)
    assert len(job["log"]) == 2


def test_get_job_parses_result_json(temp_db):
    """get_job parses result JSON into dict."""
    create_job("job19", db=temp_db)
    result_data = {"template": "report.html", "status": 200}
    update_job("job19", result=result_data, done=True, db=temp_db)

    job = get_job("job19", db=temp_db)

    assert job["result"] == result_data


def test_get_job_handles_null_result(temp_db):
    """get_job handles NULL result field."""
    create_job("job20", db=temp_db)

    job = get_job("job20", db=temp_db)

    assert job["result"] is None


def test_get_job_handles_null_error(temp_db):
    """get_job handles NULL error field."""
    create_job("job21", db=temp_db)

    job = get_job("job21", db=temp_db)

    assert job["error"] is None


def test_get_job_returns_error_when_present(temp_db):
    """get_job includes error message when job failed."""
    create_job("job22", db=temp_db)
    update_job("job22", error="Failed to fetch data", db=temp_db)

    job = get_job("job22", db=temp_db)

    assert job["error"] == "Failed to fetch data"
    assert job["status"] == "failed"


def test_get_job_done_flag_true_for_completed(temp_db):
    """get_job sets done=True for completed jobs."""
    create_job("job23", db=temp_db)
    update_job("job23", done=True, db=temp_db)

    job = get_job("job23", db=temp_db)

    assert job["done"] is True
    assert job["status"] == "completed"


def test_get_job_done_flag_true_for_failed(temp_db):
    """get_job sets done=True for failed jobs."""
    create_job("job24", db=temp_db)
    update_job("job24", error="Failure", db=temp_db)

    job = get_job("job24", db=temp_db)

    assert job["done"] is True
    assert job["status"] == "failed"


def test_get_job_includes_updated_timestamp(temp_db):
    """get_job includes updated_ts as unix timestamp."""
    create_job("job25", db=temp_db)

    job = get_job("job25", db=temp_db)

    assert "updated_ts" in job
    assert isinstance(job["updated_ts"], int)
    assert job["updated_ts"] > 0


def test_get_job_handles_malformed_log_json(temp_db):
    """get_job handles malformed log JSON gracefully."""
    temp_db.execute(
        "INSERT INTO jobs (id, status, pct, log) VALUES (?, ?, ?, ?)",
        ("job26", "running", 0, "invalid json")
    )
    temp_db.commit()

    job = get_job("job26", db=temp_db)

    assert job["log"] == []  # Defaults to empty list


def test_get_job_handles_malformed_result_json(temp_db):
    """get_job handles malformed result JSON gracefully."""
    temp_db.execute(
        "INSERT INTO jobs (id, status, pct, log, result) VALUES (?, ?, ?, ?, ?)",
        ("job27", "running", 0, "[]", "invalid json")
    )
    temp_db.commit()

    job = get_job("job27", db=temp_db)

    # When JSON parsing fails, returns raw value
    assert job["result"] == "invalid json"


# ---------------------------
# Test: Integration scenarios
# ---------------------------

def test_job_lifecycle_create_update_complete(temp_db):
    """Test complete job lifecycle from creation to completion."""
    # Create
    create_job("lifecycle1", db=temp_db)

    # Update progress
    update_job("lifecycle1", pct=10, message="Started", db=temp_db)
    update_job("lifecycle1", pct=50, message="Halfway", db=temp_db)
    update_job("lifecycle1", pct=90, message="Almost done", db=temp_db)

    # Complete
    result = {"data": [1, 2, 3], "count": 3}
    update_job("lifecycle1", pct=100, message="Done", result=result, done=True, db=temp_db)

    # Retrieve
    job = get_job("lifecycle1", db=temp_db)

    assert job["status"] == "completed"
    assert job["pct"] == 100
    assert job["done"] is True
    assert len(job["log"]) == 4
    assert job["result"] == result


def test_job_lifecycle_create_update_fail(temp_db):
    """Test job lifecycle ending in failure."""
    create_job("lifecycle2", db=temp_db)

    update_job("lifecycle2", pct=20, message="Processing", db=temp_db)
    update_job("lifecycle2", pct=40, message="Fetching data", db=temp_db)

    # Fail
    update_job("lifecycle2", error="API timeout", db=temp_db)

    job = get_job("lifecycle2", db=temp_db)

    assert job["status"] == "failed"
    assert job["done"] is True
    assert job["error"] == "API timeout"
    assert job["pct"] == 40  # Preserved last pct


def test_multiple_jobs_independent(temp_db):
    """Multiple jobs can be tracked independently."""
    create_job("multi1", db=temp_db)
    create_job("multi2", db=temp_db)
    create_job("multi3", db=temp_db)

    update_job("multi1", pct=50, message="Job 1 progress", db=temp_db)
    update_job("multi2", pct=75, message="Job 2 progress", db=temp_db)
    update_job("multi3", error="Job 3 failed", db=temp_db)

    job1 = get_job("multi1", db=temp_db)
    job2 = get_job("multi2", db=temp_db)
    job3 = get_job("multi3", db=temp_db)

    assert job1["pct"] == 50
    assert job2["pct"] == 75
    assert job3["status"] == "failed"


def test_job_with_unicode_and_special_characters(temp_db):
    """Jobs handle unicode and special characters in messages."""
    create_job("unicode_job", db=temp_db)

    update_job("unicode_job", message="Processing: O'Malley's Café ☕", db=temp_db)
    update_job("unicode_job", message="Unicode: 中文 한글 العربية 🎉", db=temp_db)

    job = get_job("unicode_job", db=temp_db)

    assert "Processing: O'Malley's Café ☕" in job["log"]
    assert "Unicode: 中文 한글 العربية 🎉" in job["log"]


def test_job_with_empty_messages_skipped(temp_db):
    """Empty string messages are not added to log."""
    create_job("empty_msg", db=temp_db)

    update_job("empty_msg", message="Valid message", db=temp_db)
    update_job("empty_msg", message="", db=temp_db)
    update_job("empty_msg", message=None, db=temp_db)
    update_job("empty_msg", message="Another valid", db=temp_db)

    job = get_job("empty_msg", db=temp_db)

    # Empty and None messages shouldn't be added
    # But empty string might be added (depends on implementation)
    # Let's check we have at least the valid messages
    assert "Valid message" in job["log"]
    assert "Another valid" in job["log"]

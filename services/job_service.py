# services/job_service.py
import json
from typing import Any, Tuple, Optional
from services.database import get_db


def _coerce_db(db=None):
    """Return a usable DB handle (request or background)."""
    return db or get_db()


def _exec(db: Any, sql: str, params: Tuple = ()):
    """
    Execute write/DDL. Supports either DatabaseManager.execute_query(...)
    or raw sqlite3 connection .execute(...)
    """
    if hasattr(db, "execute_query"):
        return db.execute_query(sql, params)
    else:
        cur = db.execute(sql, params)
        return cur


def _query_one(db: Any, sql: str, params: Tuple = ()):
    """Fetch one row in a DB-agnostic way."""
    if hasattr(db, "execute_query"):
        # Assume DatabaseManager returns cursor-like object with fetchone()
        return db.execute_query(sql, params).fetchone()
    else:
        return db.execute(sql, params).fetchone()


def _commit(db: Any):
    if hasattr(db, "commit"):
        db.commit()


def create_job(job_id: str, db=None):
    db = _coerce_db(db)
    _exec(
        db,
        "INSERT INTO jobs (id, status, pct, log) VALUES (?, ?, ?, ?)",
        (job_id, "running", 0, json.dumps([])),
    )
    _commit(db)


def update_job(job_id: str, pct: Optional[int] = None, message: Optional[str] = None,
               error: Optional[str] = None, result=None, done: bool = False, db=None):
    db = _coerce_db(db)

    row = _query_one(db, "SELECT log FROM jobs WHERE id=?", (job_id,))
    logs = []
    if row:
        # sqlite3.Row supports both index and key access; handle both
        val = row["log"] if "log" in getattr(row, "keys", lambda: [])() else row[0]
        if val:
            try:
                logs = json.loads(val)
            except Exception:
                logs = []

    if message:
        logs.append(message)

    status = "failed" if error else ("completed" if done else "running")
    result_json = json.dumps(result) if result is not None else None

    _exec(
        db,
        "UPDATE jobs SET pct=?, log=?, error=?, result=?, status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (pct or 0, json.dumps(logs), error, result_json, status, job_id),
    )
    _commit(db)


def get_job(job_id: str, db=None):
    db = _coerce_db(db)
    row = _query_one(db, "SELECT * FROM jobs WHERE id=?", (job_id,))
    if not row:
        return None

    # Normalize row access (sqlite3.Row or tuple)
    keys = getattr(row, "keys", lambda: [])()

    def get(col, idx):
        return row[col] if col in keys else row[idx]

    result = None
    raw_result = get("result", 4) if keys else row["result"] if "result" in keys else None
    if raw_result:
        try:
            result = json.loads(raw_result)
        except Exception:
            result = raw_result

    return {
        "pct": (get("pct", 2) or 0) if keys else row[2] or 0,
        "log": json.loads(get("log", 3) or "[]") if keys else json.loads(row[3] or "[]"),
        "done": get("status", 1) in ("completed", "failed") if keys else row[1] in ("completed", "failed"),
        "error": get("error", 5) if keys else row[5] if len(row) > 5 else None,
        "result": result,
        "status": get("status", 1) if keys else row[1],
    }

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

    status = "failed" if error else ("completed" if done else "running")
    result_json = json.dumps(result) if result is not None else None

    # Atomic log append via json_insert — no read-modify-write race.
    # CASE handles corrupt/malformed log values by resetting to a fresh array.
    if message:
        _exec(
            db,
            """UPDATE jobs
                  SET pct = COALESCE(?, pct),
                      log = CASE
                              WHEN json_valid(COALESCE(log, '[]'))
                              THEN json_insert(COALESCE(log, '[]'), '$[#]', ?)
                              ELSE json_array(?)
                            END,
                      error = ?,
                      result = ?,
                      status = ?,
                      updated_at = CURRENT_TIMESTAMP
                WHERE id = ?""",
            (pct, message, message, error, result_json, status, job_id),
        )
    else:
        _exec(
            db,
            """UPDATE jobs
                  SET pct = COALESCE(?, pct),
                      error = ?,
                      result = ?,
                      status = ?,
                      updated_at = CURRENT_TIMESTAMP
                WHERE id = ?""",
            (pct, error, result_json, status, job_id),
        )
    _commit(db)


# services/job_service.py
def get_job(job_id: str, db=None):
    db = _coerce_db(db)
    row = _query_one(db,
        "SELECT id, status, pct, log, result, error, "
        "strftime('%s', updated_at) AS updated_ts "
        "FROM jobs WHERE id=?", (job_id,))
    if not row:
        return None

    by_name = hasattr(row, "keys") and "status" in row.keys()
    status = row["status"] if by_name else row[1]
    pct    = (row["pct"] if by_name else row[2]) or 0
    lograw = row["log"] if by_name else row[3]
    err    = row["error"] if by_name else row[5] if len(row) > 5 else None
    resraw = row["result"] if by_name else row[4] if len(row) > 4 else None
    upd_ts = int(row["updated_ts"] if by_name else row[6])

    try: logs = json.loads(lograw) if lograw else []
    except Exception: logs = []
    try: result = json.loads(resraw) if resraw else None
    except Exception: result = resraw

    return {"pct": pct, "log": logs, "done": status in {"done","completed","failed","error"},
            "error": err, "result": result, "status": status, "updated_ts": upd_ts}


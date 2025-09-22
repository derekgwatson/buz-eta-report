# services/cache.py
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from flask import g
from zoneinfo import ZoneInfo

from services.database import execute_query, query_db, get_db

SYD = ZoneInfo("Australia/Sydney")


def ensure_cache_table(conn=None) -> None:
    """
    Create a generic key/value cache table if it doesn't exist.
    Rows are versioned by updated_at so we can age them out or refresh.
    """
    if conn is None:
        conn = get_db()
    execute_query(
        """
        CREATE TABLE IF NOT EXISTS cache (
            cache_key      TEXT PRIMARY KEY,
            payload_json   TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            meta_json      TEXT
        )
        """,
        conn=conn,
    )


@dataclass
class CacheEntry:
    key: str
    payload: Any
    updated_at_utc: datetime
    meta: dict


def get_cache(key: str, conn=None) -> Optional[CacheEntry]:
    if conn is None:
        conn = get_db()
    row = query_db(
        "SELECT cache_key, payload_json, updated_at_utc, meta_json FROM cache WHERE cache_key = ?",
        (key,),
        one=True,
        conn=conn,
    )
    if not row:
        return None
    return CacheEntry(
        key=row["cache_key"],
        payload=json.loads(row["payload_json"]),
        updated_at_utc=datetime.fromisoformat(row["updated_at_utc"]).replace(tzinfo=timezone.utc),
        meta=json.loads(row["meta_json"] or "{}"),
    )


def set_cache(key: str, payload: Any, meta: Optional[dict] = None, conn=None) -> None:
    if conn is None:
        conn = get_db()
    ensure_cache_table(conn)
    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    execute_query(
        """
        INSERT INTO cache (cache_key, payload_json, updated_at_utc, meta_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            payload_json   = excluded.payload_json,
            updated_at_utc = excluded.updated_at_utc,
            meta_json      = excluded.meta_json
        """,
        (
            key,
            json.dumps(payload, ensure_ascii=False),
            now_utc,
            json.dumps(meta or {}, ensure_ascii=False),
        ),
        conn=conn,
    )


def is_blackout(now=None) -> bool:
    """
    Buz API disabled from 10:00 (inclusive) to 16:00 (exclusive) Australia/Sydney.
    """
    if now is None:
        now = datetime.now(tz=SYD)
    start = now.replace(hour=10, minute=0, second=0, microsecond=0)
    end = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return start <= now < end


def cache_fresh_enough(entry: CacheEntry, max_age_minutes: int) -> bool:
    if max_age_minutes <= 0:
        return False
    age = datetime.now(timezone.utc) - entry.updated_at_utc
    return age <= timedelta(minutes=max_age_minutes)

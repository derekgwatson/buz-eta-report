# services/fetcher.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

from flask import current_app
from requests import HTTPError, Timeout, ConnectionError  # from requests
from zoneinfo import ZoneInfo

from services.cache import (
    ensure_cache_table,
    get_cache,
    set_cache,
)

SYD = ZoneInfo("Australia/Sydney")


def _log(level: str, msg: str) -> None:
    try:
        getattr(current_app.logger, level)(msg)
    except Exception:
        pass


def fetch_or_cached(
    *,
    cache_key: str,
    fetch_fn: Callable[[], Any],
    force_refresh: bool = False,
    max_age_minutes_when_open: int = 15,
    fallback_http_statuses: Iterable[int] = (503,),
    fallback_on_timeouts: bool = True,
    fallback_on_conn_errors: bool = True,
    cooldown_on_503_minutes: int = 10,
) -> Any:
    """
    Try live. If specific errors occur (e.g., 503), return cached instead.
    - force_refresh=True skips the "fresh-enough" shortcut and tries live first.
    - cooldown_on_503_minutes: after a 503, avoid hammering the API for a while.
      We remember the last 503 in meta and skip live attempts until cooldown passes.
    """
    ensure_cache_table()
    entry = get_cache(cache_key)

    # Cooldown: if we recently saw a 503, avoid hitting API for a bit
    if entry and cooldown_on_503_minutes > 0:
        last_503 = (entry.meta or {}).get("last_503_at_utc")
        if last_503:
            try:
                last_503_dt = datetime.fromisoformat(last_503).replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - last_503_dt < timedelta(minutes=cooldown_on_503_minutes):
                    _log("info", f"[cache] Using cooldown after 503 for {cache_key}")
                    return entry.payload, "cache"
            except Exception:
                pass

    # If not forcing live and cache is fresh enough, serve cache
    if not force_refresh and entry and max_age_minutes_when_open > 0:
        age = datetime.now(timezone.utc) - entry.updated_at_utc
        if age <= timedelta(minutes=max_age_minutes_when_open):
            _log("info", f"[cache] Fresh cache hit for {cache_key} (age {age})")
            return entry.payload, "cache"

    # Try live
    try:
        payload = fetch_fn()
        set_cache(
            cache_key,
            payload,
            meta={"refreshed_at_syd": datetime.now(tz=SYD).isoformat(timespec="seconds")},
        )
        _log("info", f"[live] Refreshed cache for {cache_key}")
        return payload, "live"

    except HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in set(fallback_http_statuses):
            _log("warning", f"[fallback] HTTP {status} for {cache_key}; serving cache")
            if entry:
                # Stamp last_503 so we trigger cooldown
                new_meta = dict(entry.meta or {})
                new_meta["last_503_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                set_cache(cache_key, entry.payload, meta=new_meta)
                return entry.payload, "cache-503"
            raise RuntimeError(f"API returned {status} and no cached data exists for {cache_key}") from exc
        raise

    except (Timeout,) as exc:
        if fallback_on_timeouts and entry:
            _log("warning", f"[fallback] Timeout for {cache_key}; serving cache")
            return entry.payload, "cache-timeout"
        raise

    except (ConnectionError,) as exc:
        if fallback_on_conn_errors and entry:
            _log("warning", f"[fallback] Connection error for {cache_key}; serving cache")
            return entry.payload, "cache-error"
        raise

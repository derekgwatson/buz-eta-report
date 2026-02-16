# BuzETA - Status

> **Last updated**: 2026-02-16

## Overview

BuzETA is the **ETA Report Generator** - a Flask web app that fetches order data from BuzManager's OData APIs and presents customer-facing ETA reports.

**Core job**: Generate and serve ETA reports for open orders, with async background processing, caching for blackout periods, and CSV/XLSX export.

**Integration points**:
- **BuzManager OData** -> Two instances: DD (DESDR) and CBR (WATSO) for order/schedule data
- **Google OAuth** -> User authentication
- **Sentry** -> Error tracking (production)

**Key responsibilities**:
- Fetch and merge open order data from DD and CBR OData instances
- Apply status mappings (custom display names for production statuses)
- Generate async ETA reports with progress tracking
- Cache data for blackout period resilience (10:00-16:00 AEST)
- Export filtered reports as CSV/XLSX
- Manage customers, users, and status mappings via admin UI

## Current State

### What's Working
- Multi-instance OData fetching (DD + CBR) with retry and timeout handling
- Async report generation via ThreadPoolExecutor with job progress tracking
- SQLite-backed cache with blackout detection, cooldown, and stale-if-error fallback
- Customer management (individual customers and customer groups)
- Status mapping system (OData status -> custom display name)
- CSV and XLSX export with sensitive column scrubbing and Excel formula injection protection
- Google OAuth with role-based access (admin/user)
- CSRF protection via Flask-WTF
- Database migrations with auto-backup and rollback
- Cache prewarming CLI command for pre-blackout warming
- 188 tests passing (84% overall coverage)

### Known Issues / Bugs

#### Security Issues
1. **Missing auth on `delete_customer` and `edit_customer`** (`app.py:625-657`) - No `@login_required` or `@role_required` decorators. Any unauthenticated user can delete or edit customers via GET/POST.
2. **SQL injection in `update_status_mapping`** (`services/update_status_mapping.py:7-14`) - Uses string formatting (`f"('{s}')"`) to build SQL VALUES. Should use parameterized queries.
3. **`delete_customer` uses GET** (`app.py:625-628`) - Destructive action on a GET route. Should be POST/DELETE to prevent CSRF via link/image tags.
4. **`toggle_user_status` uses GET** (`app.py:681-692`) - Same issue as above; state-changing action on GET.
5. **`delete_user` uses GET** (`app.py:695-700`) - Same issue.
6. **Sentry debug route exposed** (`app.py:704-707`) - `/sentry-debug` triggers `1/0` with no auth guard. Could be used for DoS or error-log flooding.
7. **`_backup_sqlite` vulnerable to path injection** (`services/migrations.py:14`) - Uses f-string in SQL: `VACUUM INTO '{path}'`. If `backup_dir` contained a single quote, this would break or allow injection.

#### Functional Bugs
8. **`get_status_mappings` closes the shared connection** (`services/update_status_mapping.py:41`) - Calls `conn.close()` on the connection it receives. Since this is `g.db` in request context, it will break subsequent DB access in the same request.
9. **`eta_worker.run_eta_job` references non-existent `eta_cache` table** (`services/eta_worker.py:12-29`) - `load_cache` and `save_cache` reference an `eta_cache` table that is never created by any migration. This code path will always fail.
10. **Duplicate `update_status_mapping` function** - Defined in both `services/database.py:15-31` and `services/update_status_mapping.py:6-29`. The `database.py` version (parameterized, correct) shadows the other when imported.
11. **`run_eta_job` signature mismatch** (`services/eta_worker.py:56`) - Takes `(app, job_id, obfuscated_id)` but `start_eta` route (`app.py:415-427`) calls it with `(app, job_id, instance)` where `instance` is "DD"/"CBR", not an `obfuscated_id`.
12. **`_run_eta_report_job` uses `get_db()` outside request** (`app.py:368`) - Called in a ThreadPoolExecutor but uses `get_db()` which relies on `g` (Flask request context). The thread won't have `g`, so each call creates an unmanaged connection that may not be properly closed.

#### Code Quality / Warnings
13. **15 `ResourceWarning: unclosed database` warnings in tests** - Database connections opened in background threads aren't being closed reliably.
14. **Bare `except:` clauses** (`services/database.py:128`, `services/odata_client.py:108,121`) - Should catch specific exceptions.

### Coverage Gaps

| File | Coverage | Notes |
|------|----------|-------|
| `services/eta_report.py` | **11%** | Core report builder almost entirely untested |
| `services/update_status_mapping.py` | **20%** | Status mapping CRUD barely tested |
| `services/eta_worker.py` | **24%** | Background worker untested |
| `services/database.py` | **59%** | DB connection management partially tested |
| `app.py` | **61%** | Many routes untested (edit/delete customer, edit/delete user, refresh_statuses, prewarm, clear-cache) |
| `services/odata_client.py` | **78%** | POST fallback and date formatting untested |
| `services/buz_data.py` | **74%** | Group fetching edge cases |
| `tests/test_templates.py` | **0 lines** | Empty file |

## Architecture

```
[Browser] -> [Flask app.py] -> [OData Client] -> [BuzManager API]
                |                                      |
                |-> [Job Service] -> [ThreadPool]      |
                |-> [Cache Service] <- [SQLite DB]     |
                |-> [Export Service] -> [CSV/XLSX]      |
                |-> [Google OAuth]                      |
```

### Key Tables

| Table | Purpose |
|-------|---------|
| `customers` | Customer configs with DD/CBR names, obfuscated IDs, field type |
| `users` | OAuth user management (email, role, active status) |
| `status_mapping` | OData status -> custom display name mapping |
| `jobs` | Async report job tracking (status, progress, result, logs) |
| `cache` | Key/value cache with timestamps for blackout fallback |

### Main Services

**ODataClient** (`services/odata_client.py`)
- Connects to DD (DESDR) or CBR (WATSO) BuzManager instances
- TimeoutSession with (5s connect, 20s read) and 1 retry on 5xx/429
- Normalizes DateScheduled to latest per order

**buz_data** (`services/buz_data.py`)
- `get_open_orders(conn, customer, instance)` - Individual customer orders
- `get_open_orders_by_group(conn, group, instance)` - Customer group orders with batching
- `fetch_and_process_orders()` - Dedup, status mapping, cancelled/invoiced filtering
- `get_data_by_order_no()` - Single order lookup

**fetcher** (`services/fetcher.py`)
- `fetch_or_cached()` - Live-first with fallback on 503/timeout/connection errors
- Cooldown tracking (10 min after 503)
- BUZ_FORCE_503 env var for testing

**cache** (`services/cache.py`)
- SQLite-backed key/value store
- `is_blackout()` - 10:00-16:00 AEST detection
- `cache_fresh_enough()` - Age-based staleness

**eta_report** (`services/eta_report.py`)
- `build_eta_report_context()` - Orchestrates DD+CBR fetching, grouping, context building
- Progress callback support for async jobs

**export** (`services/export.py`)
- CSV/XLSX generation with BOM for Excel compatibility
- `scrub_sensitive()` - Removes cost/margin/wholesale columns
- `_sanitize_for_excel()` - Formula injection protection
- Column width auto-sizing for XLSX

**job_service** (`services/job_service.py`)
- `create_job()` / `update_job()` / `get_job()`
- JSON log accumulation, progress percentage tracking
- DB-agnostic (works with both sqlite3.Connection and DatabaseManager)

### Web Pages

| Route | Access | Purpose |
|-------|--------|---------|
| `/` | Public | Home page |
| `/admin` | Login + user/admin | Customer management |
| `/<obfuscated_id>` | Public | Async ETA report (launches job) |
| `/etas/<obfuscated_id>` | Public | Alias for above |
| `/sync/<obfuscated_id>` | Public | Synchronous ETA report (blocking) |
| `/<obfuscated_id>/download.<fmt>` | Public | CSV/XLSX export |
| `/status_mapping` | Login + user/admin | View status mappings |
| `/status_mapping/edit/<id>` | Login + user/admin | Edit status mapping |
| `/refresh_statuses` | Login + admin | Sync statuses from OData |
| `/manage_users` | Login + admin | User management |
| `/jobs-schedule/<instance>/<order_no>` | Login | Order schedule lookup |
| `/wip/<instance>/<order_no>` | Login | Work-in-progress lookup |

### CLI Commands

| Command | Purpose |
|---------|---------|
| `flask init-db` | Initialize database tables |
| `flask db-backup` | Create SQLite backup |
| `flask clear-cache` | Clear cached data (requires --confirm) |
| `flask prewarm-cache` | Warm cache for all customers before blackout |

## Code Review Findings

### High Priority (Security)

| # | Issue | File:Line | Severity |
|---|-------|-----------|----------|
| 1 | `delete_customer` and `edit_customer` have no auth | `app.py:625-657` | Critical |
| 2 | SQL injection in `update_status_mapping` | `update_status_mapping.py:7-14` | Critical |
| 3 | Destructive actions on GET routes (delete_customer, toggle_user, delete_user) | `app.py:625,681,695` | High |
| 6 | Unprotected `/sentry-debug` route | `app.py:704-707` | Medium |
| 7 | Path injection in `_backup_sqlite` | `migrations.py:14` | Low |

### Medium Priority (Bugs)

| # | Issue | File:Line | Impact |
|---|-------|-----------|--------|
| 8 | `get_status_mappings` closes shared `g.db` connection | `update_status_mapping.py:41` | Breaks subsequent DB ops in same request |
| 9 | `eta_cache` table doesn't exist (dead code in eta_worker) | `eta_worker.py:12-29` | Worker cache path will always fail |
| 10 | Duplicate `update_status_mapping` function across files | `database.py:15` / `update_status_mapping.py:6` | Confusing; one may mask the other |
| 11 | `run_eta_job` parameter mismatch (instance vs obfuscated_id) | `eta_worker.py:56` / `app.py:420` | start_eta route is broken |
| 12 | `_run_eta_report_job` uses `get_db()` in thread without request context | `app.py:368` | Unclosed connections, ResourceWarnings |

### Low Priority (Best Practices)

| # | Issue | Recommendation |
|---|-------|----------------|
| 14 | Bare `except:` in database.py, odata_client.py | Catch specific exceptions (e.g., `except Exception:`) |
| 15 | `app.py` is 794 lines (monolith) | Extract routes into Flask Blueprints |
| 16 | No type hints on most functions in app.py | Add return types and parameter annotations |
| 17 | `load_dotenv()` called in ODataClient constructor | Call once at startup, not per-instance |
| 18 | `config.py` uses deprecated `ENV` attribute | Flask deprecated `ENV` config; use `APP_ENV` directly |
| 19 | `query_db` detects write vs read by parsing SQL keyword | Fragile; consider separate read/write functions |
| 20 | Mixed import styles (`import secrets, threading` on one line) | One import per line per PEP 8 |
| 21 | `_before_send` uses semicolons to join statements | Use separate lines |
| 22 | Dead code: `fetch_with_stale_if_error` in buz_data.py | Not called anywhere; remove or integrate |
| 23 | Dead code: `_cache_key` in buz_data.py | Not called anywhere |
| 24 | Duplicate `update_status_mapping` in `database.py` | Remove from database.py (belongs in update_status_mapping.py) |
| 25 | No rate limiting on public report routes | Consider rate limiting `/<obfuscated_id>` |
| 26 | No pagination on admin lists | Could become slow with many customers/users |
| 27 | `test_templates.py` is empty | Either add template tests or remove file |

## Test Coverage Summary

```
Overall: 188 tests passing, 84% coverage (3051 statements, 499 missed)

Well-tested (90%+):
  config.py             100%    services/cache.py        98%
  services/job_service.py 98%   services/migrations.py   98%
  services/export.py      97%   services/fetcher.py      95%
  services/odata_utils.py 100%

Needs work:
  services/eta_report.py          11%  (core business logic!)
  services/update_status_mapping.py 20%
  services/eta_worker.py          24%
  services/database.py            59%
  app.py                          61%
```

## API Interface (Proposed)

Currently the app has no structured API. Below is a proposed API design for bot-to-bot integration.

### Authentication
- API key via `X-API-Key` header (new `api_keys` table or env var)
- Existing session auth for browser clients

### Proposed Endpoints

| Method | Endpoint | Purpose | Response |
|--------|----------|---------|----------|
| GET | `/api/v1/customers` | List all customers | `[{id, display_name, obfuscated_id, field_type, dd_name, cbr_name}]` |
| GET | `/api/v1/customers/<obfuscated_id>` | Get single customer | `{id, display_name, ...}` |
| POST | `/api/v1/customers` | Create customer | `{id, obfuscated_id, ...}` |
| PUT | `/api/v1/customers/<id>` | Update customer | `{id, ...}` |
| DELETE | `/api/v1/customers/<id>` | Delete customer | `204` |
| GET | `/api/v1/reports/<obfuscated_id>` | Get report data (JSON) | `{customer_name, orders: [...], source, statuses, groups}` |
| POST | `/api/v1/reports/<obfuscated_id>/generate` | Start async report | `{job_id}` |
| GET | `/api/v1/jobs/<job_id>` | Job status/progress | `{status, pct, done, error, result}` |
| GET | `/api/v1/reports/<obfuscated_id>/download?format=csv` | Download report | File download |
| GET | `/api/v1/statuses` | List status mappings | `[{id, odata_status, custom_status, active}]` |
| POST | `/api/v1/statuses/refresh` | Sync from OData | `{count}` |
| GET | `/api/v1/health` | Health check | `{status: "ok", db: true, cache_entries: N}` |

### Implementation Notes
- Use Flask Blueprints: `api_bp = Blueprint("api", __name__, url_prefix="/api/v1")`
- Return JSON with consistent envelope: `{"data": ..., "meta": {"source": "live|cache"}}`
- Error responses: `{"error": "message", "code": "NOT_FOUND"}`
- Add `@api_key_required` decorator that checks `X-API-Key` header
- Reuse existing service layer (buz_data, export, job_service) - no new business logic needed
- Consider OpenAPI/Swagger docs via flask-smorest or flasgger

## Next Steps

### High Priority
- [ ] Fix critical security issues (#1, #2, #3 above)
- [ ] Add auth to delete_customer and edit_customer routes
- [ ] Change destructive GET routes to POST
- [ ] Fix `get_status_mappings` closing shared connection
- [ ] Remove or fix dead eta_worker cache code
- [ ] Fix `run_eta_job` parameter mismatch

### Medium Priority
- [ ] Increase test coverage on eta_report.py (11% -> 80%+)
- [ ] Increase test coverage on update_status_mapping.py
- [ ] Extract app.py routes into Blueprints
- [ ] Add API interface (Blueprint with JSON endpoints + API key auth)
- [ ] Clean up dead code (fetch_with_stale_if_error, _cache_key, duplicate update_status_mapping)
- [ ] Fix bare except clauses
- [ ] Add rate limiting on public routes

### Low Priority / Future
- [ ] Add type hints throughout app.py
- [ ] Add pagination to admin lists
- [ ] Add flash messages (currently commented out)
- [ ] Add OpenAPI/Swagger documentation for API
- [ ] Consider migrating from SQLite to PostgreSQL for concurrent access
- [ ] Add request logging middleware

---

*This is a living document. Update it when making significant changes to BuzETA.*

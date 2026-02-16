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
1. ~~**Missing auth on `delete_customer` and `edit_customer`**~~ - **FIXED**: Added `@login_required` + `@role_required("admin")` decorators.
2. ~~**SQL injection in `update_status_mapping`**~~ - **FIXED**: Replaced f-string interpolation with parameterized queries.
3. ~~**`delete_customer` uses GET**~~ - **FIXED**: Changed to POST with CSRF-protected form.
4. ~~**`toggle_user_status` uses GET**~~ - **FIXED**: Changed to POST with CSRF-protected form.
5. ~~**`delete_user` uses GET**~~ - **FIXED**: Changed to POST with CSRF-protected form.
6. ~~**Sentry debug route exposed**~~ - **FIXED**: Added `@login_required` + `@role_required("admin")`.
7. ~~**`_backup_sqlite` vulnerable to path injection**~~ - **FIXED**: Single quotes escaped in VACUUM INTO path.

#### Functional Bugs
8. ~~**`get_status_mappings` closes the shared connection**~~ - **FIXED**: Removed premature `conn.close()`.
9. ~~**`eta_worker.run_eta_job` references non-existent `eta_cache` table**~~ - **FIXED**: Removed dead `load_cache`/`save_cache`/`_fetch_live_or_cached`; rewrote `run_eta_job` to call `build_eta_report_context` directly.
10. ~~**Duplicate `update_status_mapping` function**~~ - **FIXED**: Removed duplicate from `database.py`.
11. ~~**`run_eta_job` signature mismatch**~~ - **FIXED**: Changed `/eta/start` to accept `obfuscated_id` instead of `instance`.
12. ~~**`_run_eta_report_job` uses `get_db()` outside request**~~ - **FIXED**: Wrapped in `app.app_context()` so downstream `current_app` calls work in background threads.

#### Code Quality / Warnings
13. **15 `ResourceWarning: unclosed database` warnings in tests** - Partially addressed by adding `app.app_context()` and `db.close()` to background workers (#12). Remaining warnings may need per-test connection management.
14. ~~**Bare `except:` clauses**~~ - **FIXED**: Changed to `except Exception:` in database.py and odata_client.py.

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

### High Priority (Security) — ALL FIXED

All security issues (#1-#7) resolved. Auth decorators added, GET routes changed to POST with CSRF forms, SQL/path injection fixed.

### Medium Priority (Bugs) — ALL FIXED

All functional bugs (#8-#12) resolved. Dead code removed, parameter mismatches fixed, app context added to background threads.

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
| 22 | ~~Dead code: `fetch_with_stale_if_error` in buz_data.py~~ | **FIXED**: Removed along with `_cache_key`, unused imports, and type aliases |
| 23 | ~~Dead code: `_cache_key` in buz_data.py~~ | **FIXED**: Removed (see #22) |
| 24 | ~~Duplicate `update_status_mapping` in `database.py`~~ | **FIXED**: Removed (see security fixes) |
| 25 | No rate limiting on public report routes | Consider rate limiting `/<obfuscated_id>` |
| 26 | No pagination on admin lists | Could become slow with many customers/users |
| 27 | ~~`test_templates.py` is empty~~ | **FIXED**: Removed empty file |

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

## API Interface

### Authentication
- API key via `X-API-Key` header, validated against `API_KEY` env var
- CSRF exempted for API blueprint

### Implemented Endpoints

| Method | Endpoint | Purpose | Response |
|--------|----------|---------|----------|
| GET | `/api/v1/customers` | List all customers | `{"data": [{id, display_name, obfuscated_id, field_type, dd_name, cbr_name}]}` |
| POST | `/api/v1/customers` | Create customer | `{"data": {id, obfuscated_id, report_url, ...}}` (201) |

### Proposed (Not Yet Implemented)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/v1/customers/<obfuscated_id>` | Get single customer |
| PUT | `/api/v1/customers/<id>` | Update customer |
| DELETE | `/api/v1/customers/<id>` | Delete customer |
| POST | `/api/v1/reports/<obfuscated_id>/generate` | Start async report |
| GET | `/api/v1/jobs/<job_id>` | Job status/progress |
| GET | `/api/v1/health` | Health check |

### Implementation
- Flask Blueprint in `routes/api.py` at `/api/v1`
- `@api_key_required` decorator checks `X-API-Key` header against `API_KEY` env var
- JSON envelope: `{"data": ...}` for success, `{"error": "message"}` for errors
- 10 tests in `tests/test_api.py`

## Next Steps

### High Priority — DONE
- [x] Fix critical security issues (#1, #2, #3 above)
- [x] Add auth to delete_customer and edit_customer routes
- [x] Change destructive GET routes to POST
- [x] Fix `get_status_mappings` closing shared connection
- [x] Remove or fix dead eta_worker cache code
- [x] Fix `run_eta_job` parameter mismatch

### Medium Priority
- [ ] Increase test coverage on eta_report.py (11% -> 80%+)
- [ ] Increase test coverage on update_status_mapping.py
- [ ] Extract app.py routes into Blueprints
- [x] Add API interface (Blueprint with JSON endpoints + API key auth)
- [x] Clean up dead code (fetch_with_stale_if_error, _cache_key, duplicate update_status_mapping)
- [x] Fix bare except clauses
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

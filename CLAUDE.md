# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Personal Telegram bot for expense tracking with a web admin panel. Snap receipt photos, bot extracts data via GPT-5.4-mini vision, stores in PostgreSQL, generates PDF/CSV reports. Includes a full admin dashboard (Jinja2 + HTMX + Alpine.js + Tailwind CSS). Deployed on El Cano VPS via Docker Compose in polling mode.

## Commands

```bash
uv run python run_polling.py                     # run bot + admin panel locally (polling + uvicorn on :8000)
uv run uvicorn src.main:app --reload             # run webhook mode only (needs HTTPS)
uv run alembic upgrade head                      # run migrations
uv run alembic revision --autogenerate -m "msg"  # create migration
uv run pytest                                    # run all tests
uv run pytest tests/test_filters.py -k "test_name"  # run single test
uv run ruff check src/                           # lint
```

Local dev requires `DYLD_LIBRARY_PATH=/opt/homebrew/lib` for WeasyPrint on macOS.

Admin panel: `http://localhost:8000/admin/` (local) or `http://server-elcano:8001/admin/` (prod via Tailscale).

## Deployment (El Cano VPS)

```bash
# Deploy from local machine:
rsync -avz --delete --exclude='.venv' --exclude='__pycache__' --exclude='.env' \
  --exclude='.git' --exclude='uv.lock' --exclude='receipts' \
  ./ server-elcano:~/expense-tracker/
ssh server-elcano 'cd ~/expense-tracker && docker compose -f docker-compose.prod.yml up -d --build'

# View logs:
ssh server-elcano 'cd ~/expense-tracker && docker compose -f docker-compose.prod.yml logs -f bot'

# Run scripts inside container:
ssh server-elcano 'cd ~/expense-tracker && docker compose -f docker-compose.prod.yml exec -T bot uv run python scripts/<name>.py'
```

Production uses `docker-compose.prod.yml` (polling mode + auto-migrate on start). Local Postgres inside Docker, receipts as bind mount at `~/expense-tracker/receipts/`. Admin panel served on port 8001 (mapped from container's 8000). Port 8000 on the host is used by sideline-poc.

## Architecture

### Two entry points

- `run_polling.py` — Telegram polling + uvicorn (admin panel) in the same process. Polling runs in main thread, uvicorn in a daemon thread. Sets `POLLING_MODE=1` env var so `src/main.py` skips telegram webhook setup.
- `src/main.py` — FastAPI app with webhook endpoint + admin panel. When `POLLING_MODE=1`, the lifespan skips telegram initialization (polling handles it separately).

Bot handler registration is duplicated in both entry points (not shared).

### Admin panel

Web dashboard at `/admin/` built with Jinja2 + HTMX + Alpine.js + Tailwind CSS (all CDN, zero build step).

- **Design system**: "Fluid Ledger" — teal/emerald `#00685f` primary, Manrope headlines, Inter body, Material Symbols icons, no 1px borders (bg color shifts), ambient shadows. Stitch mockups in `designs/extracted/`.
- **Pages**: Dashboard (`/admin/`), Expenses (`/admin/expenses`), Reports (`/admin/reports`), Schedules (`/admin/schedules`)
- **Interactivity**: HTMX for server-driven partial updates (filter, sort, paginate, inline edit, delete). Alpine.js for client-side state (modals, dropdowns, tabs, toasts). Chart.js for dashboard charts. Flatpickr for date pickers.
- **Templates**: `templates/admin/base.html` (layout + sidebar + CDN links), page templates extend it, partials prefixed with `_` (e.g., `_table.html`, `_row.html`) for HTMX swaps.
- **Routes**: `src/admin/router.py` mounts sub-routers from `src/admin/routes/` (dashboard, expenses, reports, schedules).
- **DB access**: `src/admin/dependencies.py` provides `get_db()` FastAPI dependency (async session).
- **Static files**: mounted at `/static/`, served from `static/` dir. Minimal — just `admin.css` for HTMX indicator transitions.
- **No auth**: single-user app, accessed via Tailscale VPN only.

### Request flow for receipt photos

```
Photo message → handle_photo()
  → download image bytes
  → compute_image_hash() (pHash for dedup)
  → receipt_parser.parse_receipt() (GPT-5.4-mini Responses API, structured JSON output)
  → find_duplicate() (pHash distance ≤ 10 OR vendor+amount+date+currency match)
  → if duplicate: store in context.user_data["pending_expenses"][uuid] → show Save/Discard
  → else: save to DB, save image to disk, convert currency → show Company/Personal/Edit/Discard
```

### Report generation flow

```
/report (no args) → keyboard period picker → _period_to_filters() → _send_report()
/report <natural language> → report_agent.resolve_filter() → GPT tool call → ReportFilter → _send_report()
```

`report_agent.py` uses GPT-5.4-mini with a single function tool (`generate_expense_report`) whose parameters map directly to `ReportFilter` fields. The model resolves relative dates, categories, locations, etc.

### Key patterns

- **Duplicate pending expenses**: stored in `context.user_data["pending_expenses"][uuid_key]`, keyed by UUID embedded in callback data. This allows multiple concurrent duplicate prompts without overwriting each other.
- **Callback handler routing**: `handle_callback()` parses `action:param` from `query.data`. Actions include `confirm`, `tag_company`, `tag_personal`, `discard`, `edit`, `dup_save`, `dup_discard`, `report`, `nuke_yes/no`, `delete_yes/no`.
- **Currency conversion**: Frankfurter API (ECB-backed, no key). Rates cached in `exchange_rates` table. Fallback: tries 7 days back. Conversion happens at save time; `eur_amount` is denormalized on the expense row.
- **Expense tagging**: `expense_type` column ('personal' | 'company' | NULL). Set via inline keyboard after receipt parse. Not set at creation time.

## Database

- PostgreSQL 16 + SQLAlchemy 2.0 async + asyncpg
- Alembic for migrations (uses sync `psycopg2` driver via `settings.sync_database_url`)
- 3 models: `Expense`, `ExchangeRate`, `ScheduledReport`
- Soft deletes on expenses (`deleted_at` field, queries filter `deleted_at.is_(None)`)
- Amounts as `Decimal(12,2)`, exchange rates as `Decimal(12,6)`
- JSONB columns: `line_items`, `raw_llm_response`
- EUR is the base currency for all conversions

## OpenAI integration

Uses the **Responses API** (`client.responses.create()`), NOT Chat Completions:
- Receipt parsing: `text.format` with JSON schema for structured output
- Report filters: `tools` with function calling + `tool_choice="required"`
- Model: `gpt-5.4-mini-2026-03-17`
- All schemas must have `additionalProperties: False` and all fields in `required` (including nullable ones using `["type", "null"]`)

## Conventions

- All DB operations are async (asyncpg + SQLAlchemy async sessions)
- Soft deletes on expenses (deleted_at field); `/nuke` does hard deletes for testing
- Single-user bot — `ALLOWED_TELEGRAM_USERS` env var for access control
- Receipt images stored at `RECEIPT_STORAGE_PATH` (container: `/data/receipts/`, local: `./receipts/`)
- `@authorized` decorator on all command handlers (not on `handle_callback`)
- Admin panel uses `TemplateResponse(request=request, name="...", context={...})` (new Starlette API, not positional args)
- HTMX partials return HTML fragments; full pages extend `admin/base.html`
- Decimal values need `|float` filter in Jinja2 templates when doing arithmetic

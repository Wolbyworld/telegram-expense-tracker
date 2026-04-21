# Telegram Expense Tracker

A self-hosted Telegram bot that turns receipt photos into structured expense
records, backed by a small web admin panel for browsing, editing, and
reporting. Built for one person (me) — published in case it's useful as a
starting point for someone else. Not a product, not a service, not supported.

Snap a receipt → GPT-5.4-mini vision extracts vendor / amount / currency /
date / line items → everything lands in Postgres → you can query it from
Telegram (`/report "restaurants last month"`) or from the `/admin` web UI.

## Features

- **Receipt OCR via GPT-5.4-mini vision** (OpenAI Responses API, structured JSON output).
- **Image enhancement pipeline** before OCR: orientation fix, deskew, edge crop, contrast, sharpen (OpenCV + Pillow).
- **Duplicate detection** via perceptual hashing (pHash) + vendor/amount/date heuristics.
- **Multi-currency** with ECB rates via the free Frankfurter API; amounts stored in both original and base currency (EUR by default).
- **Natural-language reports** — `/report restaurants in Madrid last month` uses GPT function-calling to resolve filters.
- **PDF + CSV exports** via WeasyPrint.
- **Admin panel** at `/admin/` — Jinja2 + HTMX + Alpine.js + Tailwind (CDN, zero build step).
- **Scheduled email reports** (optional, via APScheduler + SMTP).
- **Soft deletes** on expenses; `/nuke` command for hard-wipe during testing.

## Stack

Python 3.12 · FastAPI · python-telegram-bot · SQLAlchemy 2.0 async · asyncpg ·
Alembic · PostgreSQL 16 · OpenAI Responses API · WeasyPrint · OpenCV ·
Pillow · Jinja2 + HTMX + Alpine.js + Tailwind · [`uv`](https://docs.astral.sh/uv/) for dependency management.

## Security — read before exposing this to a network

- **The admin panel has no application-level auth.** Anyone who can reach
  port 8000 can read and edit every expense. Put it behind Tailscale, a
  reverse proxy with basic auth, a WireGuard tunnel, a firewall rule — do
  **not** expose it on the open internet as-is. My deploy uses Tailscale
  Serve so only my tailnet can reach it.
- **The bot accepts commands from any Telegram user unless you set
  `ALLOWED_TELEGRAM_USERS`.** Every message costs OpenAI tokens. Always set
  this to your own user ID (get it from [@userinfobot](https://t.me/userinfobot)).
- **Webhook mode verifies `X-Telegram-Bot-Api-Secret-Token`** if you set
  `TELEGRAM_WEBHOOK_SECRET`. Set it.
- **`/nuke` hard-deletes all expenses.** Authorized users only, but there's
  no backup logic built in — if you care about the data, take snapshots of
  the Postgres volume.
- **`.env` is gitignored.** If you fork, double-check before pushing.

## Prerequisites

- Python 3.12+
- PostgreSQL 16 (or run it via Docker Compose, see below)
- `uv` — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- An OpenAI API key with access to `gpt-5.4-mini` (the model ID is in `src/services/receipt_parser.py`)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your Telegram user ID from [@userinfobot](https://t.me/userinfobot)
- **macOS only:** `brew install pango cairo gdk-pixbuf libffi` for WeasyPrint

## Quick start (Docker Compose)

Easiest path — Postgres runs in a container, no native deps needed.

```bash
git clone <this-repo> expense-tracker
cd expense-tracker
cp .env.example .env
# edit .env: set TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, ALLOWED_TELEGRAM_USERS, DB_PASSWORD
# also set DATABASE_URL=postgresql+asyncpg://expenses:${DB_PASSWORD}@db:5432/expenses

docker compose up -d --build
docker compose exec bot uv run alembic upgrade head
```

Bot is now polling Telegram. Admin panel is at `http://localhost:8000/admin/`.

Logs:

```bash
docker compose logs -f bot
```

To use the production compose file (runs migrations on boot automatically,
binds admin to host port 8001):

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

## Local development (native Python + Postgres)

```bash
# 1. Install deps
uv sync

# 2. Start Postgres locally and create the DB
createdb expenses

# 3. Configure
cp .env.example .env
# edit .env: set DATABASE_URL to your local Postgres, fill in secrets

# 4. Migrate
uv run alembic upgrade head

# 5. Run (polling mode: bot + admin panel in one process on :8000)
uv run python run_polling.py
```

macOS users need `DYLD_LIBRARY_PATH` set for WeasyPrint — `run_polling.py`
does this automatically if `/opt/homebrew/lib` exists.

### Webhook mode (if you have a public HTTPS URL)

```bash
# In .env: set TELEGRAM_WEBHOOK_URL to your public https:// endpoint
uv run uvicorn src.main:app --reload
```

When `POLLING_MODE` is unset, `src/main.py` will register the webhook with
Telegram on startup.

## Environment variables

See [`.env.example`](.env.example) for the full list. The essentials:

| Variable | Required | Purpose |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | yes | From BotFather |
| `OPENAI_API_KEY` | yes | For receipt OCR + NL report filters |
| `DATABASE_URL` | yes | `postgresql+asyncpg://…` |
| `ALLOWED_TELEGRAM_USERS` | strongly recommended | Comma-separated user IDs |
| `TELEGRAM_WEBHOOK_SECRET` | webhook mode only | Random string |
| `TELEGRAM_WEBHOOK_URL` | webhook mode only | Public HTTPS URL |
| `DB_PASSWORD` | docker-compose only | Used to set Postgres password |
| `RECEIPT_STORAGE_PATH` | no | Defaults to `/data/receipts` in Docker |
| `BASE_CURRENCY` | no | Defaults to `EUR` |
| `TIMEZONE` | no | IANA tz name, e.g. `Europe/Madrid` |
| `SMTP_*` / `EMAIL_FROM` | no | Only for scheduled email reports |

## Telegram commands

| Command | What it does |
| --- | --- |
| `/start`, `/help` | Show help |
| Send a photo | Parse receipt, show Save / Company / Personal / Edit / Discard keyboard |
| `/add <amount> <currency> <vendor> [description]` | Manual entry |
| `/list [N]` | Last N expenses (default 10) |
| `/total [period]` | Totals (e.g. `month`, `week`, `today`) |
| `/report` | Keyboard picker for a PDF report |
| `/report <natural language>` | e.g. `/report restaurants in Madrid last month` |
| `/categories` | List categories |
| `/export [period]` | CSV export |
| `/delete <id>` | Soft-delete an expense |
| `/nuke` | ⚠️ hard-deletes everything (confirmation required) |

## Admin panel

At `/admin/`. Four pages:

- **Dashboard** — totals, trends, category breakdown
- **Expenses** — list with filtering, sorting, inline edit, delete
- **Reports** — generate PDF/CSV with filters
- **Schedules** — configure recurring email reports

Stack is Jinja2 templates + HTMX for partials + Alpine.js for tiny client
state + Tailwind CDN. No build step. All interactivity is server-driven
except dropdowns/modals.

## Deployment (the way I do it)

1. A Linux VPS.
2. `docker compose -f docker-compose.prod.yml up -d --build`.
3. Admin panel exposed via [Tailscale Serve](https://tailscale.com/kb/1242/tailscale-serve/)
   on HTTPS :8443, tailnet-only. No public ingress at all.
4. Receipts stored as a bind mount (`./receipts/` on the host) so they survive container rebuilds.
5. Deployed via `rsync` from my laptop. No CI.

You don't have to do any of this. Self-host however you like.

## Tests

```bash
uv run pytest
uv run ruff check src/
```

## Caveats / known gaps

- Not a product. Bug reports welcome, but support is best-effort.
- Model ID `gpt-5.4-mini-2026-03-17` is hardcoded; swap it in `src/services/receipt_parser.py` and `src/services/report_agent.py` if you want a different model.
- No backup/restore tooling — snapshot the Postgres volume yourself.
- Admin panel auth is "put it on a private network." That's the design, not an oversight.
- Single-user assumptions throughout — DB schema has no `user_id` column.

## License

[MIT](LICENSE).

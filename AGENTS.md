# Telegram Expense Tracker

Single-user Telegram bot and FastAPI/HTMX admin panel for receipt parsing, expenses, reports, and scheduled reports.

## Fast commands

- Run the focused pytest file or test name for the changed path.
- Run lint only on changed Python packages.
- Start the bot or admin panel locally only when that runtime path changed.

## Rules

- All database work is async SQLAlchemy with PostgreSQL.
- Expenses use soft delete; destructive test commands remain explicit.
- Receipt deduplication uses image hash or vendor, amount, date, and currency matching.
- Store pending duplicate decisions by UUID so concurrent prompts do not overwrite each other.
- Protect command handlers with the existing authorization path and keep the bot single-user.
- Use OpenAI Responses API with strict structured schemas. Every field is required; nullable fields use a null union; reject extra properties.
- Treat receipts, images, expenses, reports, Telegram IDs, and raw model output as private.
- Never log or commit bot tokens, OpenAI keys, database URLs, or receipt content.

## Verification and operations

- Parsing or report logic: focused unit test.
- Callback or bot flow: focused handler test and one local flow when needed.
- Admin UI: focused route or template check.
- Deployment, production bot actions, report sending, or data deletion needs explicit approval.

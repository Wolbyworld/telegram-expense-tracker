# Receipt email intake

Resend receives mail; the application polls over outbound HTTPS. There is no public
email-ingestion endpoint. The existing admin stays private through Tailscale.
Email and CLI candidates always require review. Ordinary Telegram photos still save
automatically; duplicate prompts are durable and survive process restarts.

## Activation

Receiving is disabled by default. The outgoing RESEND_API_KEY is never a fallback.
A dedicated RESEND_RECEIVING_API_KEY and its provider scope need specific owner
approval. Resend currently requires full_access to retrieve mail; do not silently
broaden or repurpose the report key. Keep secrets in the approved runtime secret
mechanism, never in this document or command-line arguments.

| Setting | Purpose |
| --- | --- |
| EMAIL_INTAKE_ENABLED | true only after approved end-to-end testing |
| EMAIL_INTAKE_ACCOUNT | Stable selected-account label; preserve it across replay |
| EMAIL_INTAKE_ADDRESS | Exact approved managed resend.app address |
| EMAIL_INTAKE_SENDERS | Exact comma-separated addresses allowed to forward |
| EMAIL_INTAKE_OWNER_ID | Existing Telegram owner also in ALLOWED_TELEGRAM_USERS |
| EMAIL_INTAKE_POLL_SECONDS | Default 90; minimum 60 |
| INTAKE_SCANNER_PATH | Default /usr/bin/clamscan in the application image |

Use the account's managed receiving address first. A receiving subdomain requires
separate DNS approval. Never replace root MX or publish the admin service.
Provider-computed DMARC pass, or aligned DKIM pass with DMARC gray, is required.
Quoted merchant identities and message-supplied Authentication-Results cannot grant
access. The first supported path is a manual forward from an approved owner address;
automatic forwarding preserving a merchant sender needs separate enrollment.

## Processing and review

The private Receipt inbox at /admin/intake shows evidence, extraction fields, duplicate
reasons and errors. Actions accept, edit/accept, link to an existing expense, save as a
separate purchase with a reason, reject or retry. Expenses retain the live Company
default; classification remains editable on the existing expense page.

Unique provider/account/message IDs and owner/channel/source-item IDs prevent replay.
SHA-256, pHash distance <= 10 and normalized vendor/Decimal amount/currency/date
produce duplicate review candidates across channels. Exact-file history includes
deleted provenance; replay does not silently recreate a deleted expense. A transaction
and owner-scoped PostgreSQL advisory lock cover expense insertion, source link and
decision. Model/network work is outside that transaction. Leases recover after crashes.

Supported: JPEG/PNG/WebP, one receipt across multiple PDF pages, and plain/sanitized
HTML email bodies. Nonempty body content is staged even when attachments include inline
logos or signatures. Body and attachments remain separate review candidates. Once one
is accepted, a body/attachment sibling from that email cannot create another expense
without an explicit separate-purchase override, even if extracted merchant names differ.
Several documents are also checked for content/image duplicates. Multiple purchases
inside one document require manual review. No link, tracking pixel or remote HTML
resource is fetched. Attached .eml, office files, archives, encrypted mail, password-
protected PDFs and link-only receipts are unsupported and require review.

Limits: 10 MB/file, 25 MB combined evidence/message, 10 attachments, 20 pages/PDF,
25 megapixels/image and 100,000 body characters (including complete decoded HTML markup).
Oversized HTML is rejected before sanitization, never sliced. Provider limits can be lower.
Unsafe/over-limit input is quarantined, never silently truncated into a ledger entry.

ClamAV must succeed before email/CLI extraction; missing/unavailable/signatures older
than seven days fail closed. The image obtains signatures at build time. When receiving
is enabled and configured, its worker attempts an in-container signature refresh at
startup and daily. CLI-only use can refresh with freshclam inside the container.
No host cron or daemon is added. The Linux decoder
runs as nobody with a sanitized environment, resource limits and a seccomp filter
denying network, subprocess execution and ptrace. Originals are mode 0600.

## Operator CLI

Run inside the existing protected application environment. Commands do not send email.

    python -m src.cli --help
    python -m src.cli status
    python -m src.cli messages --owner OWNER_ID
    python -m src.cli retry-message MESSAGE_ID --owner OWNER_ID --actor operator
    python -m src.cli list --owner OWNER_ID
    python -m src.cli show CANDIDATE_ID --owner OWNER_ID
    python -m src.cli ingest-file /private/receipt.jpg --owner OWNER_ID --dry-run
    python -m src.cli ingest-file /private/receipt.jpg --owner OWNER_ID
    python -m src.cli review CANDIDATE_ID --owner OWNER_ID --actor operator --action accept
    python -m src.cli review CANDIDATE_ID --owner OWNER_ID --actor operator --action link --expense-id EXPENSE_ID
    python -m src.cli review CANDIDATE_ID --owner OWNER_ID --actor operator --action save_separate --reason 'Different purchase'
    python -m src.cli review CANDIDATE_ID --owner OWNER_ID --actor operator --action reject
    python -m src.cli review CANDIDATE_ID --owner OWNER_ID --actor operator --action retry
    python -m src.cli poll-once
    python -m src.cli retry-conversion EXPENSE_ID --owner OWNER_ID

An edits JSON file may correct vendor, description, category, date, total_amount and
currency. CLI import defaults to a SHA-256 source identity; --source-key is for a stable
external importer identity. Dry run hashes/types only, with no DB/storage/API writes.

## Tests and release

Tests use synthetic TELEGRAM_BOT_TOKEN and OPENAI_API_KEY values and mocked model/provider
calls. INTAKE_TEST_DATABASE_URL must point to a dedicated localhost PostgreSQL database
whose name starts with expense_intake_test. Every integration test owns a generated
schema. Never point tests at production.

    uv sync --extra dev
    INTAKE_TEST_DATABASE_URL=postgresql+asyncpg://expense_test@127.0.0.1:55439/expense_intake_test pytest -q

Coverage includes cross-channel/pending duplicates, replay, concurrent acceptance,
atomic rollback, deleted provenance, owner isolation, expired leases, provider auth,
pagination failure, review CSRF/escaping, Telegram after restart, image/PDF preparation
and CLI dry-run/replay.

Before production migration, capture a custom-format DB dump and targeted app/receipt
archives on responsive non-NAS storage. Hash and successfully restore the dump in a
disposable unnetworked PostgreSQL container. Retain the old bot image. Coordinate the
service window with other operations work; do not resume paused backups or repair NAS.

Migration e20261023 adds incoming_messages, receipt_candidates, intake_attempts,
intake_decisions and intake_poll_state only. It does not rewrite ledger rows.
Deploy with receiving disabled, validate existing Telegram/admin/report paths, then
activate after credential/address/sender approval and an approved synthetic email test.

Rollback uses the old application image with receiving disabled; retain additive tables
and audit identities. Destructive downgrade is deliberately refused. Never overwrite
new legitimate expenses with an old DB merely to roll back code. Disaster restoration
needs a separately coordinated write stop.

## Operational limits

The private admin and CLI show health and failed states; proactive external alerts and
notifications are not enabled. Confirm the exact receiving address, account quota and
provider inbound-size limit during activation. An address is not working until an
approved synthetic email has been received and reviewed end to end. Resend normally
retains email for 30 days, so prolonged polling gaps need timely reconciliation.

Automatic content purge awaits the owner's accounting retention policy. Review storage
growth and keep access private. Source tombstones must survive any future file/body
purge. Private receipts are not used in new-provider tests.

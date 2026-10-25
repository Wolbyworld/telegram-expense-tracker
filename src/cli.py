"""Private operator CLI: python -m src.cli --help. Never sends email."""

import argparse
import asyncio
import json
import mimetypes
from pathlib import Path

from sqlalchemy import select

from src.models import async_session
from src.models.intake import IncomingMessage, IntakePollState, ReceiptCandidate
from src.services.receipt_files import content_hash
from src.services.receipt_intake import checked_owner, decide, process_candidate, register_source


def summary(candidate):
    return {
        key: getattr(candidate, key)
        for key in (
            "id",
            "owner_id",
            "channel",
            "status",
            "filename",
            "parsed",
            "expense_id",
            "duplicate_expense_id",
            "duplicate_candidate_id",
            "duplicate_reason",
            "error_code",
        )
    }


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser(
        "ingest-file", help="Stage a local receipt; always requires review"
    )
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--owner", required=True, type=int)
    ingest.add_argument("--source-key", help="Stable import identity; defaults to file SHA-256")
    ingest.add_argument(
        "--dry-run", action="store_true", help="Hash/type only; no DB, file or API writes"
    )
    listing = commands.add_parser("list")
    listing.add_argument("--owner", required=True, type=int)
    listing.add_argument("--status")
    show = commands.add_parser("show")
    show.add_argument("id")
    show.add_argument("--owner", required=True, type=int)
    review = commands.add_parser("review")
    review.add_argument("id")
    review.add_argument("--owner", required=True, type=int)
    review.add_argument(
        "--action",
        required=True,
        choices=["accept", "save_separate", "link", "reject", "retry", "correct"],
    )
    review.add_argument("--actor", required=True)
    review.add_argument("--expense-id", type=int)
    review.add_argument("--reason", default="")
    review.add_argument(
        "--edits-file", type=Path, help="JSON object with supported field corrections"
    )
    commands.add_parser("poll-once", help="Explicitly poll using approved receiving configuration")
    conversion = commands.add_parser("retry-conversion")
    conversion.add_argument("expense_id", type=int)
    conversion.add_argument("--owner", required=True, type=int)
    commands.add_parser("status", help="Safe counts/status; no message bodies or credentials")
    messages = commands.add_parser("messages", help="List message states without bodies")
    messages.add_argument("--owner", required=True, type=int)
    retry_message = commands.add_parser("retry-message")
    retry_message.add_argument("id")
    retry_message.add_argument("--owner", required=True, type=int)
    retry_message.add_argument("--actor", required=True)
    return root


async def run(args):
    if args.command == "retry-message":
        from src.services.email_intake import retry_message

        return await retry_message(args.id, args.owner, args.actor)
    if args.command == "ingest-file":
        checked_owner(args.owner)
        from src.config import settings

        if args.path.stat().st_size > settings.intake_max_attachment_bytes:
            raise ValueError("attachment_size_limit")
        data = args.path.read_bytes()
        mime = mimetypes.guess_type(args.path.name)[0] or "application/octet-stream"
        if args.dry_run:
            return {"dry_run": True, "sha256": content_hash(data), "bytes": len(data), "mime": mime}
        candidate = await register_source(
            args.owner, "cli", args.source_key or content_hash(data), data, mime, args.path.name
        )
        return summary(await process_candidate(candidate.id, args.owner))
    if args.command == "poll-once":
        from src.services.email_intake import poll_once

        return {"scanned": await poll_once()}
    if args.command == "retry-conversion":
        from src.services.receipt_intake import convert_saved_expense

        checked_owner(args.owner)
        await convert_saved_expense(args.expense_id, args.owner)
        return {"expense_id": args.expense_id, "conversion_attempted": True}
    if args.command == "review":
        edits = json.loads(args.edits_file.read_text()) if args.edits_file else None
        return summary(
            await decide(
                args.id,
                args.owner,
                args.action,
                args.actor,
                edits=edits,
                expense_id=args.expense_id,
                override_reason=args.reason,
            )
        )
    async with async_session() as session:
        if args.command == "status":
            from sqlalchemy import func

            states = (
                await session.execute(
                    select(ReceiptCandidate.status, func.count()).group_by(
                        ReceiptCandidate.status,
                    )
                )
            ).all()
            polls = (await session.execute(select(IntakePollState))).scalars().all()
            messages = (
                await session.execute(
                    select(IncomingMessage.status, func.count()).group_by(
                        IncomingMessage.status,
                    )
                )
            ).all()
            return {
                "candidates": dict(states),
                "messages": dict(messages),
                "polls": [
                    {
                        "account": row.account,
                        "last_success": row.last_success_at,
                        "last_error": row.last_error,
                        "scanned": row.scanned_messages,
                    }
                    for row in polls
                ],
            }
        checked_owner(args.owner)
        if args.command == "messages":
            rows = (
                (
                    await session.execute(
                        select(IncomingMessage)
                        .where(
                            IncomingMessage.owner_id == args.owner,
                        )
                        .order_by(IncomingMessage.created_at.desc())
                        .limit(100)
                    )
                )
                .scalars()
                .all()
            )
            return [
                {
                    "id": row.id,
                    "status": row.status,
                    "error": row.error_code,
                    "attempts": row.attempts,
                }
                for row in rows
            ]
        query = select(ReceiptCandidate).where(ReceiptCandidate.owner_id == args.owner)
        if args.command == "show":
            candidate = (
                await session.execute(query.where(ReceiptCandidate.id == args.id))
            ).scalar_one_or_none()
            if not candidate:
                raise ValueError("candidate_not_found")
            return summary(candidate)
        if args.status:
            query = query.where(ReceiptCandidate.status == args.status)
        return [
            summary(row)
            for row in (
                await session.execute(
                    query.order_by(
                        ReceiptCandidate.created_at.desc(),
                    ).limit(100)
                )
            ).scalars()
        ]


def main():
    args = parser().parse_args()
    try:
        result = asyncio.run(run(args))
        print(json.dumps(result, default=str))
    except Exception as exc:
        # Only safe type/code; provider bodies and credentials never reach stdout.
        print(json.dumps({"error": type(exc).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

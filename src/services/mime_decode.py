"""Bounded MIME extraction, run only in the unprivileged decoder subprocess.

An attached original is one receipt document: its body and PDF/images are evidence
for one reviewed purchase, never independently auto-booked representations.
"""

import hashlib
from email import policy
from email.parser import BytesParser
from pathlib import Path

from src.services.email_content import body_text
from src.services.intake_errors import UnsafeReceiptError

SUPPORTED = {"application/pdf", "image/jpeg", "image/png", "image/webp"}


def unpack(source: Path, destination: Path, limits: dict):
    message = BytesParser(policy=policy.default).parsebytes(source.read_bytes())
    parts = []
    texts = []
    visited = 0
    decoded_size = 0

    def walk(part, depth, identity):
        nonlocal visited, decoded_size
        visited += 1
        if depth > 8 or visited > 50:
            raise UnsafeReceiptError("mime_structure_limit")
        if part.defects or len(part.items()) > 100:
            raise UnsafeReceiptError("mime_invalid_structure")
        if sum(len(str(k)) + len(str(v)) for k, v in part.items()) > 65_536:
            raise UnsafeReceiptError("mime_header_limit")
        mime = part.get_content_type()
        if mime == "message/rfc822":
            if str(part.get("Content-Transfer-Encoding", "")).lower() not in {
                "",
                "7bit",
                "8bit",
                "binary",
            }:
                raise UnsafeReceiptError("mime_unsupported_forward_encoding")
            children = part.get_payload()
            if not isinstance(children, list) or len(children) != 1:
                raise UnsafeReceiptError("mime_invalid_forward")
            walk(children[0], depth + 1, identity + ".0")
            return
        if part.is_multipart():
            if mime not in {"multipart/mixed", "multipart/alternative", "multipart/related"}:
                raise UnsafeReceiptError("mime_unsupported_container")
            children = part.get_payload()
            if mime == "multipart/alternative":
                # Prefer the complete plain representation; HTML is the fallback.
                if not children:
                    raise UnsafeReceiptError("mime_missing_body")
                # Still validate all siblings' structure and limits: nothing is
                # silently ignored merely because an alternative was preferred.
                alternatives = []
                for index, child in enumerate(children):
                    before = len(texts)
                    walk(child, depth + 1, identity + "." + str(index))
                    alternatives.append((child.get_content_type(), texts[before:]))
                    del texts[before:]
                complete = [t for kind, t in alternatives if kind == "text/plain" and t]
                chosen = (
                    complete[0]
                    if complete
                    else next((t for _, t in reversed(alternatives) if t), [])
                )
                texts.extend(chosen)
                return
            for index, child in enumerate(children):
                walk(child, depth + 1, identity + "." + str(index))
            return
        data = part.get_payload(decode=True)
        if not isinstance(data, bytes) or part.defects:
            raise UnsafeReceiptError("mime_invalid_encoding")
        decoded_size += len(data)
        if len(data) > limits["file_bytes"] or decoded_size > limits["message_bytes"]:
            raise UnsafeReceiptError("mime_decoded_size_limit")
        if mime in {"text/plain", "text/html"}:
            try:
                value = data.decode(part.get_content_charset() or "utf-8", errors="strict")
            except (LookupError, UnicodeError):
                raise UnsafeReceiptError("mime_invalid_charset") from None
            if value.strip():
                try:
                    text = body_text({"text" if mime == "text/plain" else "html": value})
                except UnsafeReceiptError as exc:
                    if str(exc) != "missing_email_body":
                        raise
                else:
                    texts.append(text)
            return
        if mime not in SUPPORTED:
            raise UnsafeReceiptError("mime_unsupported_part")
        if len(parts) >= limits["files"]:
            raise UnsafeReceiptError("mime_attachment_count_limit")
        target = destination / (str(len(parts)) + ".part")
        target.write_bytes(data)
        parts.append(
            {
                "path": str(target),
                "mime": mime,
                "part": identity,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "inline": part.get_content_disposition() == "inline",
            }
        )

    walk(message, 0, "0")
    text = "\n\n".join(texts)
    if len(text) > 100_000:
        raise UnsafeReceiptError("email_body_size_limit")
    if not text.strip() and not parts:
        raise UnsafeReceiptError("mime_missing_evidence")
    return {"text": text, "parts": parts, "visited_parts": visited}

"""Pure bounded email body sanitization; safe inside the decoder sandbox."""

from html.parser import HTMLParser

from src.services.intake_errors import UnsafeReceiptError


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe", "form", "noscript"}:
            self.hidden += 1
        if not self.hidden and tag in {"p", "div", "br", "tr", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "iframe", "form", "noscript"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def body_text(email):
    plain = email.get("text")
    if plain and len(plain) > 100_000:
        raise UnsafeReceiptError("email_body_size_limit")
    if plain and plain.strip():
        body = plain
    else:
        html = email.get("html") or ""
        # Current API may return data-URI HTML. Decode locally, never fetch a URL.
        if html.startswith("data:"):
            import base64
            from urllib.parse import unquote_to_bytes

            header, _, content = html.partition(",")
            try:
                html = (
                    base64.b64decode(content, validate=True)
                    if ";base64" in header
                    else unquote_to_bytes(content)
                ).decode("utf-8")
            except (ValueError, UnicodeError):
                raise UnsafeReceiptError("invalid_html_encoding") from None
        parser = TextOnly()
        if len(html) > 100_000:
            raise UnsafeReceiptError("email_html_size_limit")
        parser.feed(html)
        body = "".join(parser.parts)
    if len(body) > 100_000:
        raise UnsafeReceiptError("email_body_size_limit")
    if not body.strip():
        raise UnsafeReceiptError("missing_email_body")
    return body

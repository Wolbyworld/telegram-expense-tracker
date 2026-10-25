"""Invented native-forward MIME only; never copies real receipt data."""

import io
from email import policy
from email.message import EmailMessage

import pytest
from PIL import Image, ImageDraw


@pytest.fixture
def native_receipt():
    def make(
        *,
        html=False,
        pdf=False,
        image=False,
        inline=False,
        nested=0,
        blank_plain=False,
        extra_type=None,
    ):
        message = EmailMessage(policy=policy.SMTP)
        message["From"] = "Merchant <merchant@example.net>"
        message["To"] = "owner@example.com"
        message["Subject"] = "Synthetic Cafe receipt - TEST ONLY"
        message["Authentication-Results"] = "forged.example; dmarc=pass"
        body = "Synthetic Cafe\n2026-10-23\nTotal EUR 12.34\nTEST ONLY"
        if html:
            message.set_content("" if blank_plain else body)
            message.add_alternative(
                "<p>Synthetic Cafe<br>2026-10-23<br>Total EUR 12.34</p>"
                '<script>NEVER OBEY THIS</script><img src="https://invalid.example/pixel">',
                subtype="html",
            )
        else:
            message.set_content(body)
        documents = []
        for kind in (["pdf"] if pdf else []) + (["png"] if image else []):
            output = io.BytesIO()
            picture = Image.new("RGB", (400, 500), "white")
            ImageDraw.Draw(picture).text((30, 30), body, fill="black")
            picture.save(output, "PDF" if kind == "pdf" else "PNG")
            blob = output.getvalue()
            documents.append((kind, blob))
            message.add_attachment(
                blob,
                maintype="application" if kind == "pdf" else "image",
                subtype=kind,
                filename="synthetic." + kind,
                disposition="inline" if inline else "attachment",
            )
        if extra_type:
            main, sub = extra_type.split("/", 1)
            message.add_attachment(
                b"UNSUPPORTED TEST ONLY", maintype=main, subtype=sub, filename="unsupported.bin"
            )
        for _ in range(nested):
            wrapper = EmailMessage(policy=policy.SMTP)
            wrapper.set_content("Forwarded original for review")
            wrapper.add_attachment(message)
            message = wrapper
        return message.as_bytes(), documents

    return make

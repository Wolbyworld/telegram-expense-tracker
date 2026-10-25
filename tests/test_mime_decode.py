from pathlib import Path

import pytest

from src.services.intake_errors import UnsafeReceiptError
from src.services.mime_decode import unpack

LIMITS = {"file_bytes": 10 * 1024 * 1024, "message_bytes": 25 * 1024 * 1024, "files": 10}


def extract(tmp_path, data, **limits):
    source = tmp_path / "original.eml"
    source.write_bytes(data)
    return unpack(source, tmp_path, {**LIMITS, **limits})


@pytest.mark.parametrize("html", [False, True])
@pytest.mark.parametrize("nested", [0, 1, 2])
def test_native_body_only_receipts(tmp_path, native_receipt, html, nested):
    data, _ = native_receipt(html=html, blank_plain=html, nested=nested)
    result = extract(tmp_path, data)
    assert "Total EUR 12.34" in result["text"]
    assert "NEVER OBEY" not in result["text"]
    assert "invalid.example" not in result["text"]
    assert "forged.example" not in result["text"]
    assert not result["parts"]


@pytest.mark.parametrize("inline", [False, True])
def test_native_body_and_pdf_and_inline_image_are_one_document(tmp_path, native_receipt, inline):
    data, files = native_receipt(html=True, pdf=True, image=True, inline=inline, nested=1)
    result = extract(tmp_path, data)
    assert len(result["parts"]) == 2
    assert {p["mime"] for p in result["parts"]} == {"application/pdf", "image/png"}
    assert [Path(p["path"]).read_bytes() for p in result["parts"]] == [b for _, b in files]
    assert [p["part"] for p in result["parts"]] == ["0.1.0.1", "0.1.0.2"]
    assert result["text"].count("Total EUR 12.34") == 1


def test_mime_depth_is_bounded(tmp_path, native_receipt):
    data, _ = native_receipt(nested=6)
    with pytest.raises(UnsafeReceiptError, match="mime_structure_limit"):
        extract(tmp_path, data)


@pytest.mark.parametrize(
    "limits,code",
    [
        ({"file_bytes": 20}, "mime_decoded_size_limit"),
        ({"message_bytes": 20}, "mime_decoded_size_limit"),
        ({"files": 0}, "mime_attachment_count_limit"),
    ],
)
def test_mime_limits_do_not_silently_drop_evidence(tmp_path, native_receipt, limits, code):
    data, _ = native_receipt(pdf=True)
    with pytest.raises(UnsafeReceiptError, match=code):
        extract(tmp_path, data, **limits)


def test_unsupported_leaf_quarantines_complete_original(tmp_path, native_receipt):
    data, _ = native_receipt(extra_type="application/zip")
    with pytest.raises(UnsafeReceiptError, match="mime_unsupported_part"):
        extract(tmp_path, data)


def test_invalid_base64_is_not_partially_extracted(tmp_path):
    data = b"Content-Type: image/png\r\nContent-Transfer-Encoding: base64\r\n\r\n!!!!\r\n"
    with pytest.raises(UnsafeReceiptError, match="mime_invalid_encoding"):
        extract(tmp_path, data)


def test_oversized_nested_html_fails_closed(tmp_path):
    from email.message import EmailMessage

    message = EmailMessage()
    message.set_content(
        "<p>Subtotal 5</p><!--" + "x" * 100_001 + "--><p>Total 12.34</p>", subtype="html"
    )
    with pytest.raises(UnsafeReceiptError, match="email_html_size_limit"):
        extract(tmp_path, message.as_bytes())

"""Run inside the candidate image with --network none and synthetic settings only."""

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from src.services.receipt_files import UnsafeReceiptError, prepared_images, scan_file


async def main():
    with tempfile.TemporaryDirectory(prefix="intake-image-smoke-") as directory:
        image = Path(directory) / "synthetic.jpg"
        Image.new("RGB", (300, 500), "white").save(image, "JPEG")
        await scan_file(image)
        async with prepared_images(image, "image/jpeg") as outputs:
            assert len(outputs) == 1 and outputs[0].startswith(b"\xff\xd8")
        pdf = Path(directory) / "synthetic.pdf"
        document = pdfium.PdfDocument.new()
        document.new_page(200, 300)
        document.new_page(200, 300)
        document.save(str(pdf))
        document.close()
        await scan_file(pdf)
        async with prepared_images(pdf, "application/pdf") as outputs:
            assert len(outputs) == 2
        # Standard harmless antivirus test pattern, never an executable payload.
        antivirus_test = Path(directory) / "antivirus-test.txt"
        antivirus_test.write_text(
            "X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
        )
        try:
            await scan_file(antivirus_test)
        except UnsafeReceiptError:
            pass
        else:
            raise AssertionError("antivirus_test_not_detected")
    if sys.platform == "linux":
        code = (
            "from src.services.intake_decode import restrict_linux_syscalls; "
            "import socket; restrict_linux_syscalls(); "
            "socket.socket()"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                env={"PATH": os.defpath, "PYTHONPATH": "/app"}, timeout=10)
        assert result.returncode != 0 and b"PermissionError" in result.stderr
    print("PASS: clean image/PDF scan and decode, antivirus rejection, decoder network denial")


if __name__ == "__main__":
    asyncio.run(main())

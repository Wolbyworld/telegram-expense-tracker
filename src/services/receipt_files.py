"""Private receipt storage and constrained format preparation."""

import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from src.config import settings


class UnsafeReceiptError(ValueError):
    pass


def store_private(data: bytes, suffix: str = ".bin") -> str:
    root = Path(settings.receipt_storage_path) / "intake"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / (uuid.uuid4().hex + suffix)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    return str(path)


def safe_path(value: str) -> Path:
    path = Path(value).resolve()
    root = Path(settings.receipt_storage_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise UnsafeReceiptError("missing_or_unsafe_storage_path")
    return path


async def scan_file(path: Path):
    scanner = settings.intake_scanner_path
    if not scanner or not Path(scanner).is_absolute():
        raise UnsafeReceiptError("scanner_not_configured")
    try:
        process = await asyncio.create_subprocess_exec(
            scanner,
            "--no-summary",
            "--fail-if-cvd-older-than=7",
            "--max-filesize=10M",
            "--max-scansize=25M",
            "--alert-exceeds-max=yes",
            "--",
            str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": os.defpath, "LANG": "C.UTF-8"},
        )
    except OSError:
        raise UnsafeReceiptError("scanner_unavailable") from None
    try:
        status = await asyncio.wait_for(process.wait(), 30)
    except (TimeoutError, asyncio.CancelledError) as exc:
        process.kill()
        await process.wait()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise UnsafeReceiptError("scanner_timeout") from None
    if status:
        raise UnsafeReceiptError("scanner_rejected_or_unavailable")


@asynccontextmanager
async def prepared_images(path: Path, mime: str):
    with tempfile.TemporaryDirectory(prefix="expense-decode-") as directory:
        source = Path(directory) / "input.bin"
        shutil.copyfile(path, source)
        source.chmod(0o600)
        privileges = {}
        if sys.platform == "linux" and os.geteuid() == 0:
            # The decoder cannot read the application's mode-0600 receipts/env caches.
            os.chown(source, 65534, 65534)
            os.chown(directory, 65534, 65534)
            privileges = {"user": 65534, "group": 65534, "extra_groups": []}
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "src.services.intake_decode",
            str(source),
            directory,
            mime,
            str(settings.intake_max_image_pixels),
            str(settings.intake_max_pdf_pages),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={
                "PATH": os.defpath,
                "LANG": "C.UTF-8",
                "HOME": directory,
                "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                "PYTHONNOUSERSITE": "1",
            },
            **privileges,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 45)
        except (TimeoutError, asyncio.CancelledError) as exc:
            process.kill()
            await process.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise UnsafeReceiptError("decoder_timeout") from None
        if process.returncode or len(output) > 16_384:
            raise UnsafeReceiptError("decoder_rejected")
        files = json.loads(output)
        if not isinstance(files, list) or not files:
            raise UnsafeReceiptError("decoder_rejected")
        images = []
        for filename in files:
            image = Path(filename).resolve()
            if not image.is_relative_to(Path(directory).resolve()):
                raise UnsafeReceiptError("decoder_invalid_output")
            images.append(image.read_bytes())
        yield images


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

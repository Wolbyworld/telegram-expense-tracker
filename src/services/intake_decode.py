"""Bounded decoder subprocess. Never interprets HTML, PDF scripts, or external URLs."""

import io
import json
import os
import resource
import sys
import warnings
from pathlib import Path

from src.services.intake_errors import UnsafeReceiptError


def restrict_linux_syscalls():
    """Deny network, subprocess execution and ptrace in the decoder only."""
    if sys.platform != "linux":
        return
    import ctypes

    library = ctypes.CDLL("libseccomp.so.2")
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    context = library.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError("decoder_sandbox_unavailable")
    try:
        for name in (
            "socket",
            "socketpair",
            "connect",
            "bind",
            "listen",
            "accept",
            "accept4",
            "sendto",
            "sendmsg",
            "recvfrom",
            "recvmsg",
            "execve",
            "execveat",
            "ptrace",
        ):
            number = library.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and library.seccomp_rule_add(context, 0x00050001, number, 0) != 0:
                raise RuntimeError("decoder_sandbox_failed")
        if library.seccomp_load(context) != 0:
            raise RuntimeError("decoder_sandbox_failed")
    finally:
        library.seccomp_release(context)


def decode(source: Path, destination: Path, mime: str, pixels: int, pages: int):
    if mime == "message/rfc822":
        from src.services.mime_decode import unpack

        restrict_linux_syscalls()
        return unpack(
            source,
            destination,
            {
                "file_bytes": int(sys.argv[6]),
                "message_bytes": int(sys.argv[7]),
                "files": int(sys.argv[8]),
            },
        )
    from PIL import Image, ImageOps

    if mime == "application/pdf":
        import pypdfium2 as pdfium
    restrict_linux_syscalls()

    Image.MAX_IMAGE_PIXELS = pixels
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    outputs = []

    def save(image, index):
        if image.width * image.height > pixels:
            raise ValueError("image_pixel_limit")
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((2400, 3200))
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=90)
        path = destination / f"{index}.jpg"
        path.write_bytes(buf.getvalue())
        outputs.append(str(path))

    if mime == "application/pdf":
        if not source.read_bytes()[:1024].lstrip().startswith(b"%PDF-"):
            raise ValueError("invalid_pdf")
        with pdfium.PdfDocument(str(source)) as document:
            if not 1 <= len(document) <= pages:
                raise ValueError("pdf_page_limit")
            for index in range(len(document)):
                page = document[index]
                width, height = page.get_size()
                scale = min(2, 2400 / max(width, 1), 3200 / max(height, 1))
                bitmap = page.render(scale=scale)
                save(bitmap.to_pil(), index)
                bitmap.close()
                page.close()
    else:
        with Image.open(source) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"} or getattr(image, "n_frames", 1) != 1:
                raise ValueError("unsupported_image")
            image.load()
            save(image, 0)
    return outputs


if __name__ == "__main__":
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (12 * 1024 * 1024, 12 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    # macOS enforces address-space limits differently; keep Linux production bounded.
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    try:
        print(
            json.dumps(
                decode(
                    Path(sys.argv[1]),
                    Path(sys.argv[2]),
                    sys.argv[3],
                    int(sys.argv[4]),
                    int(sys.argv[5]),
                )
            )
        )
    except Exception as exc:
        print(
            json.dumps(
                {"error": str(exc) if isinstance(exc, UnsafeReceiptError) else type(exc).__name__}
            )
        )
        sys.exit(1)

"""OCR of a paper УПД (owner 23.10.2026): the text of a scan without a text layer.

A picture (JPG / PNG) goes to the `tesseract` CLI as it is; a PDF is first rasterised by `pdftoppm` (poppler-utils)
at 300 dpi, the first two pages (a УПД header is on page 1, page 2 is enough for a long one). Both programs are
installed in the server image (Dockerfile); where one is missing, OcrUnavailable — the screen asks to type the header,
nothing crashes. A program that fails or runs past its timeout gives no text."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

DPI = 300
PAGES = 2
TIMEOUT = 60  # seconds per program call
LANGUAGES = "rus+eng"
PAGE_SEGMENTATION = "6"  # one uniform block of text: a УПД is a form, its lines read best kept together
IMAGE_SUFFIXES = {"image/jpeg": ".jpg", "image/png": ".png"}


class OcrUnavailable(RuntimeError):
    """tesseract (or pdftoppm for a PDF) is not installed on this server."""


def _program(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise OcrUnavailable(name)
    return found


def _run(arguments: list[str]) -> bytes | None:
    try:
        done = subprocess.run(arguments, capture_output=True, timeout=TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return done.stdout if done.returncode == 0 else None


def _tesseract(tesseract: str, image: Path) -> str:
    out = _run([tesseract, str(image), "stdout", "-l", LANGUAGES, "--psm", PAGE_SEGMENTATION])
    return out.decode("utf-8", "replace") if out else ""


def _pdf_pages(content: bytes, folder: Path) -> list[Path]:
    pdftoppm = _program("pdftoppm")
    source = folder / "scan.pdf"
    source.write_bytes(content)
    _run([pdftoppm, "-r", str(DPI), "-f", "1", "-l", str(PAGES), "-png", str(source), str(folder / "page")])
    return sorted(folder.glob("page*.png"))[:PAGES]


def ocr_text(content: bytes, kind: str | None) -> str:
    """The recognised text of a PDF / JPG / PNG ('' — nothing read). OcrUnavailable — a program is missing."""
    tesseract = _program("tesseract")
    with tempfile.TemporaryDirectory(prefix="erp-ocr-") as temporary:
        folder = Path(temporary)
        if kind == "application/pdf":
            images = _pdf_pages(content, folder)
        elif kind in IMAGE_SUFFIXES:
            image = folder / f"scan{IMAGE_SUFFIXES[kind]}"
            image.write_bytes(content)
            images = [image]
        else:
            return ""
        return "\n".join(text for image in images if (text := _tesseract(tesseract, image).strip()))

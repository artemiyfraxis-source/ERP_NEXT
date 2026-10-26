"""The pages of a document as the model reads them best (moved here from the warehouse's `receipt_ocr` and
`receipt_llm`, 26.10.2026 — Артемий's rendering and upright turn; both re-export it).

A PDF is rasterised by `pdftoppm` (poppler-utils) at 300 dpi, up to PAGES pages; a JPG / PNG is the page itself. Each
page is turned upright — a model reading an upside-down УПД made its lines up (26.10): tesseract's orientation
detection first, else the turn that reads the most Russian words — and sent as ONE grayscale JPEG, long side 2400 px,
quality 85 (measured: ~6k tokens a page). Both programs are installed in the server image (Dockerfile); where
pdftoppm is missing, OcrUnavailable; without tesseract a page goes as it lies. A program that fails or runs past its
timeout gives nothing. Tesseract's own text (the fallback reading of a page) stays with the warehouse."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

DPI = 300
PAGES = 10
TIMEOUT = 60  # seconds per program call
LANGUAGES = "rus+eng"
PAGE_SEGMENTATION = "6"  # one uniform block of text: a УПД is a form, its lines read best kept together
IMAGE_SUFFIXES = {"image/jpeg": ".jpg", "image/png": ".png"}
PDF = "application/pdf"
# A УПД is a landscape form: on the warehouse flatbed it lies sideways (23.10). An upright page reads hundreds of
# Russian words; below this many the page is tried turned and the best reading wins.
UPRIGHT_WORDS = 40
LONG_SIDE = 2400
JPEG_QUALITY = 85
_WORD = re.compile(r"[А-Яа-яЁё]{4,}")


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


def _words(text: str) -> int:
    return len(_WORD.findall(text))


def _pdf_pages(content: bytes, folder: Path) -> list[Path]:
    pdftoppm = _program("pdftoppm")
    source = folder / "scan.pdf"
    source.write_bytes(content)
    _run([pdftoppm, "-r", str(DPI), "-f", "1", "-l", str(PAGES), "-png", str(source), str(folder / "page")])
    return sorted(folder.glob("page*.png"))[:PAGES]


def page_images(content: bytes, kind: str | None, folder: Path) -> list[Path]:
    """The pages of a PDF (rasterised, up to PAGES) or the picture itself, as files in `folder`; [] — not a scan.
    OcrUnavailable — pdftoppm is missing for a PDF."""
    if kind == PDF:
        return _pdf_pages(content, folder)
    if kind in IMAGE_SUFFIXES:
        image = folder / f"scan{IMAGE_SUFFIXES[kind]}"
        image.write_bytes(content)
        return [image]
    return []


# Tesseract's orientation detection (`--psm 0`, osd.traineddata): «Rotate: 90» — turn the page 90° clockwise.
_ROTATE = re.compile(r"Rotate:\s*(\d+)")
_CONFIDENCE = re.compile(r"Orientation confidence:\s*([\d.]+)")
OSD_CONFIDENCE = 2.0
TURNS = (90, 270, 180)  # Pillow's counter-clockwise turns tried when the orientation is not detected


def _osd_turn(tesseract: str, image: Path) -> int | None:
    out = _run([tesseract, str(image), "stdout", "--psm", "0"])
    text = out.decode("utf-8", "replace") if out else ""
    rotate, confidence = _ROTATE.search(text), _CONFIDENCE.search(text)
    if rotate is None or confidence is None or float(confidence[1]) < OSD_CONFIDENCE:
        return None
    return (360 - int(rotate[1])) % 360


def page_turn(image: Path) -> int:
    """How far to turn the page (Pillow degrees, counter-clockwise) for it to stand upright: a model reading an
    upside-down УПД makes its lines up (26.10). Tesseract's orientation detection first; when it is not sure, the
    turn (of 0 / 90 / 270 / 180) that reads the most Russian words. No tesseract — 0 (sent as it lies)."""
    try:
        tesseract = _program("tesseract")
    except OcrUnavailable:
        return 0
    detected = _osd_turn(tesseract, image)
    if detected is not None:
        return detected
    best, best_words = 0, _words(_tesseract(tesseract, image))
    if best_words >= UPRIGHT_WORDS:
        return 0
    from PIL import Image  # only a page of unknown orientation needs it

    for angle in TURNS:
        turned = image.with_name(f"{image.stem}-t{angle}.png")
        try:
            with Image.open(image) as picture:
                picture.rotate(angle, expand=True).save(turned)
        except OSError:
            return best
        words = _words(_tesseract(tesseract, turned))
        if words > best_words:
            best, best_words = angle, words
            if words >= UPRIGHT_WORDS:
                break
    return best


def page_jpeg(image: Path, turn: int) -> bytes:
    """The page as the model reads it best: upright, grayscale, long side ≤ LONG_SIDE, JPEG quality 85."""
    from PIL import Image

    with Image.open(image) as picture:
        page = picture.convert("L")
        if turn:
            page = page.rotate(turn, expand=True, fillcolor=255)
        page.thumbnail((LONG_SIDE, LONG_SIDE))
        buffer = BytesIO()
        page.save(buffer, "JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()


def page_pictures(content: bytes, kind: str | None = PDF) -> list[bytes]:
    """Every page as an upright grayscale JPEG; [] — no pdftoppm on this server, or not a PDF / picture."""
    with tempfile.TemporaryDirectory(prefix="erp-doc-") as temporary:
        try:
            images = page_images(content, kind, Path(temporary))
        except OcrUnavailable:
            return []
        return [page_jpeg(image, page_turn(image)) for image in images]

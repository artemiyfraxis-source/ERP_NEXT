"""OCR of a paper УПД (owner 23.10.2026): the text of a scan without a text layer — since 26.10 the fallback of the
model reading (`receipt_llm`), which takes the page pictures and their upright turn from here.

A picture (JPG / PNG) goes to the `tesseract` CLI as it is; a PDF is first rasterised by `pdftoppm` (poppler-utils)
at 300 dpi, the first two pages (a УПД header is on page 1, page 2 is enough for a long one). Both programs are
installed in the server image (Dockerfile); where one is missing, OcrUnavailable — the screen asks to type the header,
nothing crashes. A program that fails or runs past its timeout gives no text."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

DPI = 300
PAGES = 10
TIMEOUT = 60  # seconds per program call
LANGUAGES = "rus+eng"
PAGE_SEGMENTATION = "6"  # one uniform block of text: a УПД is a form, its lines read best kept together
IMAGE_SUFFIXES = {"image/jpeg": ".jpg", "image/png": ".png"}
# A УПД is a landscape form: on the warehouse flatbed it lies sideways (23.10). An upright page reads hundreds of
# Russian words; below this many the page is tried turned by 90° both ways and the best reading wins.
UPRIGHT_WORDS = 40
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


def _read_page(tesseract: str, image: Path) -> str:
    """The page as it lies; if it reads as noise, turned by 90° and 270° (Pillow), whichever gives more words."""
    best = _tesseract(tesseract, image)
    if _words(best) >= UPRIGHT_WORDS:
        return best
    from PIL import Image  # only a sideways scan needs it

    try:
        with Image.open(image) as picture:
            turns = []
            for angle in (90, 270):
                turned = image.with_name(f"{image.stem}-r{angle}.png")
                picture.rotate(angle, expand=True).save(turned)
                turns.append(turned)
    except OSError:
        return best
    for turned in turns:
        text = _tesseract(tesseract, turned)
        if _words(text) > _words(best):
            best = text
    return best


def _pdf_pages(content: bytes, folder: Path) -> list[Path]:
    pdftoppm = _program("pdftoppm")
    source = folder / "scan.pdf"
    source.write_bytes(content)
    _run([pdftoppm, "-r", str(DPI), "-f", "1", "-l", str(PAGES), "-png", str(source), str(folder / "page")])
    return sorted(folder.glob("page*.png"))[:PAGES]


def page_images(content: bytes, kind: str | None, folder: Path) -> list[Path]:
    """The pages of a PDF (rasterised, up to PAGES) or the picture itself, as files in `folder`; [] — not a scan.
    OcrUnavailable — pdftoppm is missing for a PDF."""
    if kind == "application/pdf":
        return _pdf_pages(content, folder)
    if kind in IMAGE_SUFFIXES:
        image = folder / f"scan{IMAGE_SUFFIXES[kind]}"
        image.write_bytes(content)
        return [image]
    return []


def ocr_text(content: bytes, kind: str | None) -> str:
    """The recognised text of a PDF / JPG / PNG ('' — nothing read). OcrUnavailable — a program is missing."""
    tesseract = _program("tesseract")
    with tempfile.TemporaryDirectory(prefix="erp-ocr-") as temporary:
        images = page_images(content, kind, Path(temporary))
        return "\n".join(text for image in images if (text := _read_page(tesseract, image).strip()))


def page_text(image: Path) -> str:
    """One page picture read by tesseract (turned when it lies sideways); '' — tesseract is missing or read nothing."""
    try:
        return _read_page(_program("tesseract"), image).strip()
    except OcrUnavailable:
        return ""


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

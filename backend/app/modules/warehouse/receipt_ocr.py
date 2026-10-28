"""OCR of a paper УПД (owner 23.10.2026): the text of a scan without a text layer — since 26.10 the fallback of the
model reading (the ERP's document reader, `app.shared.doc_reader`).

A picture (JPG / PNG) goes to the `tesseract` CLI as it is; a PDF is first rasterised by `pdftoppm` (poppler-utils)
at 300 dpi, up to ten pages. Both programs are installed in the server image (Dockerfile); where one is missing,
OcrUnavailable — the screen asks to type the header, nothing crashes. A program that fails or runs past its timeout
gives no text.

The page pictures, their upright turn and the program calls moved to `app.shared.doc_reader.pages` (26.10.2026: one
reader for the warehouse and the УПД registry); they are re-exported here, so `receipt_ocr.page_images`,
`receipt_ocr.page_turn` and `receipt_ocr.OcrUnavailable` keep working. The tesseract text itself stays here."""

from __future__ import annotations

# shutil / subprocess: the programs are looked up and run through these modules (tests patch them from here).
import shutil  # noqa: F401
import subprocess  # noqa: F401
import tempfile
from pathlib import Path

from app.shared.doc_reader.pages import (  # noqa: F401 — re-exported for the warehouse's callers and tests
    DPI,
    IMAGE_SUFFIXES,
    LANGUAGES,
    OSD_CONFIDENCE,
    PAGE_SEGMENTATION,
    PAGES,
    TIMEOUT,
    TURNS,
    UPRIGHT_WORDS,
    OcrUnavailable,
    _program,
    _run,
    _tesseract,
    _words,
    page_images,
    page_turn,
)


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

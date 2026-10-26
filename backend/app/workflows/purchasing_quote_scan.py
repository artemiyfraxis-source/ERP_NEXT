"""Purchasing's quote-scan port (`quote_scan.ScanPages`) over the warehouse's УПД reading (26.10.2026).

A supplier's КП that came as a scan or a photo is read by the same vision model as a paper УПД: the model settings the
administrator saved on «Склад» → «Распознавание УПД» (the server env wins, `vision_llm.llm_config`) and the warehouse's
page pictures — a PDF rasterised by pdftoppm, each page turned upright (`receipt_ocr.page_turn`), sent as a grayscale
JPEG (`vision_llm.page_jpeg`). Purchasing never reads warehouse tables, so it gets both through here. Read-only."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from app.modules.warehouse import receipt_ocr
from app.modules.warehouse.receipts_application import upd_llm_settings
from app.shared import vision_llm


class WarehouseScanPages:
    def __init__(self, warehouse_uow_factory: Callable | None):
        self.warehouse_uow_factory = warehouse_uow_factory

    def config(self) -> vision_llm.LlmConfig:
        """No warehouse on this stand — the server env alone."""
        stored = upd_llm_settings(self.warehouse_uow_factory()) if self.warehouse_uow_factory else {}
        return vision_llm.llm_config(stored)

    def page_jpegs(self, content: bytes, kind: str, limit: int) -> list[bytes]:
        """The first `limit` pages as upright JPEGs; PagesUnavailable — no pdftoppm for a PDF."""
        with tempfile.TemporaryDirectory(prefix="erp-quote-") as temporary:
            try:
                images = receipt_ocr.page_images(content, kind, Path(temporary))[:limit]
            except receipt_ocr.OcrUnavailable as error:
                raise vision_llm.PagesUnavailable(str(error)) from None
            return [vision_llm.page_jpeg(image, receipt_ocr.page_turn(image)) for image in images]

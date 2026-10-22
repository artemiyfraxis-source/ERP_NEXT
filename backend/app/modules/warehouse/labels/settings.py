"""Label print settings (Roller_Buyer `packages/shared/src/printing.ts`). They live in the database, not in code: the gap
comes from the printer's own calibration at the first setup, and the printer's name is the Windows queue's."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CodeType = Literal["qr", "code128", "both"]

CODE_TYPE_LABELS: dict[str, str] = {
    "qr": "QR-код",
    "code128": "Штрихкод Code128",
    "both": "QR и Code128",
}

# The label formats the shop prints (owner, 22.10.2026: «58x30, 100x50, 58x40»), width × height in mm. The stored
# width_mm / height_mm columns stay free numbers; the domain accepts only these pairs.
LABEL_SIZES: tuple[tuple[float, float], ...] = ((58, 30), (58, 40), (100, 50))
LABEL_SIZES_TEXT = "58 × 30, 58 × 40 или 100 × 50 мм"


class PrintSettings(BaseModel):
    """Bounds as in Roller_Buyer; the table `warehouse_label_settings` holds the same bounds as CHECK constraints."""

    model_config = ConfigDict(extra="ignore")

    # The printer's name as the Windows print queue sees it; empty — the job is only saved as a file.
    printer_name: str = Field(default="", max_length=200)
    # Gap between labels, mm: the printer's calibration shows it (hold FEED while switching on).
    gap_mm: float = Field(default=2, ge=0, le=20)
    # Head heat 0–15: too much smears, too little prints pale. Proven on the TSC MB341 with the owner's rolls
    # (Roller_Buyer, 2026-09-25): 8 / 4 printed pale and speckled and the QR broke up; 12 / 2 print clean.
    density: int = Field(default=12, ge=0, le=15)
    # Print speed, inches per second.
    speed: int = Field(default=2, ge=1, le=12)
    # Label size, mm — one of LABEL_SIZES (checked below); 58 × 40 by default.
    width_mm: float = Field(default=58, ge=45, le=110)
    height_mm: float = Field(default=40, ge=20, le=80)
    # Head resolution: 203 on cheap printers, 300 on the TSC MB341.
    dpi: int = 300
    # The warehouse scans with ТСД (2D), so QR alone by default; Code128 stays for a 1D laser — «both» / «code128».
    code_type: CodeType = "qr"
    # Print offset, mm: when the printer prints off the label's centre — corrected here, not in the layout.
    offset_x_mm: float = Field(default=0, ge=-5, le=5)
    offset_y_mm: float = Field(default=0, ge=-5, le=5)

    @field_validator("printer_name", mode="before")
    @classmethod
    def _trim(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _size(self) -> PrintSettings:
        if (self.width_mm, self.height_mm) not in LABEL_SIZES:
            raise ValueError(f"Формат этикетки — {LABEL_SIZES_TEXT}")
        return self

    @field_validator("dpi")
    @classmethod
    def _dpi(cls, value: int) -> int:
        if value not in (203, 300):
            raise ValueError("Разрешение — 203 или 300 dpi")
        return value


DEFAULT_PRINT_SETTINGS = PrintSettings()

"""Документ запроса поставщику в PDF: то же письмо, что и в HTML-документе, собранное на сервере (fpdf2)."""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

# DejaVu Sans (свободная лицензия, LICENSE рядом) — шрифт с кириллицей, встраивается в PDF.
_FONTS = Path(__file__).resolve().parents[2] / "assets" / "fonts"
_FAMILY = "DejaVu"
_LINE_HEIGHT = 5.6


def render_request_pdf(title: str, letter: str) -> bytes:
    """A4, поля 2 см, заголовок «Запрос PO-001 от 05.10.2026», далее письмо по абзацу на строку."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_title(title)
    pdf.set_creator("Fraxis ERP Next")
    pdf.add_font(_FAMILY, "", str(_FONTS / "DejaVuSans.ttf"))
    pdf.add_font(_FAMILY, "B", str(_FONTS / "DejaVuSans-Bold.ttf"))
    pdf.add_page()
    pdf.set_font(_FAMILY, "B", 14)
    pdf.multi_cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    for index, line in enumerate(letter.split("\n")):
        if not line.strip():
            pdf.ln(_LINE_HEIGHT)
            continue
        pdf.set_font(_FAMILY, "B" if index == 0 else "", 10.5)
        # Таб подпозиций — отступ пробелами.
        pdf.multi_cell(0, _LINE_HEIGHT, line.replace("\t", "    "), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())

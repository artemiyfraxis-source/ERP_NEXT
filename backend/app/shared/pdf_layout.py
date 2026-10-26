"""Text layer of a PDF with word positions — what an invoice's table is read from.

Port of Roller_Buyer `apps/api/src/pdf-text.ts` `pdfLayout` (26.10.2026). Roller_Buyer reads the PDF with pdf.js; ERP
has pypdf (already in the lock file), whose layout-mode text state (`TextStateManager`, the fonts with their glyph
widths) gives where every string of the content stream starts and ends. Strings are joined into pieces the way pdf.js
joins them into text items: the next string on the same baseline continues the piece when the gap is under 0.6 of the
font size (a space is put in for a gap over 0.1 of it); a wider gap, a jump back or another baseline starts a new piece.
So a 1С table cell («1 142,00», «Товары (работы, услуги)») is one word with its x, y (PDF space, y grows upwards) and
width, as `app.shared.invoice_lines` expects.

The first `max_pages` pages only (5, as Roller_Buyer). Never raises: a damaged, encrypted (with a real password) or
exotic file gives an empty layout, and the caller keeps its other reader. The caller runs the budgeted
`app.shared.pdf_text` first (audit F05): a file that unpacks past the budget never gets here.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import Any

from .invoice_lines import PdfWord

log = logging.getLogger(__name__)

MAX_PAGES = 5
_FORM_DEPTH = 3
_SPACE_MIN = 0.102  # pdf.js TRACKING_SPACE_FACTOR / SPACE_IN_FLOW_MIN_FACTOR
_SPACE_MAX = 0.6  # pdf.js SPACE_IN_FLOW_MAX_FACTOR
_NEGATIVE_MAX = 0.2  # pdf.js NEGATIVE_SPACE_FACTOR


@dataclass(frozen=True)
class PdfLayout:
    # The pieces of a page joined by spaces, a line per page — what the header parsers read.
    text: str = ""
    # Each page's pieces with their positions, for reading tables.
    pages: list[list[PdfWord]] = field(default_factory=list)


@dataclass
class _Piece:
    s: str
    x: float
    y: float
    end: float
    size: float


def pdf_layout(content: bytes, max_pages: int = MAX_PAGES) -> PdfLayout:
    try:
        from pypdf import PdfReader  # noqa: PLC0415 — only invoices need it

        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(""):
            return PdfLayout()  # a real password: nothing to read
        text_parts: list[str] = []
        pages: list[list[PdfWord]] = []
        for page in reader.pages[:max_pages]:
            pieces = _page_pieces(page, reader)
            text_parts.append(" ".join(piece.s for piece in pieces))
            pages.append([
                PdfWord(piece.s, round(piece.x, 2), round(piece.y, 2), round(piece.end - piece.x, 2))
                for piece in pieces if piece.s.strip()
            ])
        return PdfLayout("\n".join(text_parts) + ("\n" if text_parts else ""), pages)
    except Exception:  # noqa: BLE001 — pypdf raises many types on damaged files; the other reader takes over
        log.info("invoice PDF: layout text failed", exc_info=True)
        return PdfLayout()


def _page_pieces(page: Any, reader: Any) -> list[_Piece]:
    from pypdf.generic import ContentStream  # noqa: PLC0415
    from pypdf._text_extraction._layout_mode._text_state_manager import TextStateManager  # noqa: PLC0415

    if "/Contents" not in page:
        return []
    fonts = page._layout_mode_fonts()
    resources = page.get("/Resources")
    operations = ContentStream(page["/Contents"].get_object(), reader, "bytes").operations
    walker = _Walker(TextStateManager(), reader)
    walker.run(operations, fonts, resources.get_object() if resources is not None else {}, 0)
    return walker.pieces


class _Walker:
    """Runs the text operators of a content stream (and of its form XObjects) through pypdf's text state."""

    def __init__(self, state: Any, reader: Any):
        self.state = state
        self.reader = reader
        self.pieces: list[_Piece] = []
        self.q_depth = 0

    def run(self, operations: list, fonts: dict, resources: Any, depth: int) -> None:
        from pypdf._text_extraction._layout_mode._fixed_width_page import resolve_font  # noqa: PLC0415

        state = self.state
        for operands, op in operations:
            if op == b"q":
                state.add_q()
                self.q_depth += 1
            elif op == b"Q":
                if self.q_depth:
                    state.remove_q()
                    self.q_depth -= 1
            elif op == b"cm":
                state.add_cm(*operands)
            elif op in (b"BT", b"ET"):
                state.reset_tm()
            elif op == b"Tf":
                state.set_font(resolve_font(fonts, operands[0]), operands[1])
            elif op in (b"Td", b"Tm", b"TD", b"T*"):
                state.reset_trm()
                if op == b"Tm":
                    state.reset_tm()
                elif op == b"TD":
                    state.set_state_param(b"TL", -operands[1])
                elif op == b"T*":
                    operands = [0, -state.TL]
                state.add_tm(operands)
            elif op == b"Tj":
                self.show(operands[0])
            elif op == b"TJ":
                self.show_array(operands[0])
            elif op == b"'":
                state.reset_trm()
                state.add_tm([0, -state.TL])
                self.show(operands[0])
            elif op == b'"':
                state.reset_trm()
                state.set_state_param(b"Tw", operands[0])
                state.set_state_param(b"Tc", operands[1])
                state.add_tm([0, -state.TL])
                self.show(operands[2])
            elif op == b"Do" and depth < _FORM_DEPTH:
                self.form(operands[0], resources, fonts, depth)
            else:  # Tc, Tw, Tz, TL, Ts; everything else is ignored
                state.set_state_param(op, operands)

    def form(self, name: Any, resources: Any, fonts: dict, depth: int) -> None:
        from pypdf import _font  # noqa: PLC0415
        from pypdf.generic import ContentStream  # noqa: PLC0415

        xobjects = resources.get("/XObject") if resources else None
        xobject = xobjects.get_object().get(name) if xobjects is not None else None
        xobject = xobject.get_object() if xobject is not None else None
        if xobject is None or xobject.get("/Subtype") != "/Form":
            return
        inner = xobject.get("/Resources")
        inner = inner.get_object() if inner is not None else resources
        inner_fonts = dict(fonts)
        font_dict = inner.get("/Font") if inner else None
        if font_dict is not None:
            for font_name, font in font_dict.get_object().items():
                inner_fonts[font_name] = _font.Font.from_font_resource(font.get_object())
        state = self.state
        state.add_q()
        self.q_depth += 1
        matrix = xobject.get("/Matrix")
        if matrix is not None:
            state.add_cm(*[float(value) for value in matrix])
        self.run(ContentStream(xobject, self.reader, "bytes").operations, inner_fonts, inner, depth + 1)
        state.remove_q()
        self.q_depth -= 1

    def show(self, value: Any) -> None:
        state = self.state
        if state.font is None:
            return
        params = state.text_state_params(value)
        state.add_trm(params.displacement_matrix())
        if params.rotated or not params.font.interpretable or not params.text:
            return
        self.add(_Piece(params.text, params.tx, params.ty, params.displaced_tx, params.font_height or 1.0))

    def show_array(self, items: Any) -> None:
        state = self.state
        if state.font is None:
            return
        for item in items:
            if isinstance(item, (bytes, str)):
                self.show(item)
            else:
                try:
                    offset = float(item)
                except (TypeError, ValueError):
                    continue
                advance = state.text_state_params("").word_tx("", offset)
                state.add_trm([1.0, 0.0, 0.0, 1.0, advance, 0.0])

    def add(self, piece: _Piece) -> None:
        """pdf.js joining: a string continuing the last piece on its baseline extends it."""
        last = self.pieces[-1] if self.pieces else None
        if last is not None and abs(piece.y - last.y) <= last.size * 0.2:
            gap = piece.x - last.end
            if -_NEGATIVE_MAX * last.size <= gap <= _SPACE_MAX * last.size:
                spaced = gap > _SPACE_MIN * last.size and not last.s.endswith(" ") and not piece.s.startswith(" ")
                last.s += (" " if spaced else "") + piece.s
                last.end = max(last.end, piece.end)
                return
        self.pieces.append(piece)

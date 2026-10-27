"""Text layer of a PDF with word positions — what an invoice's table is read from.

Port of Roller_Buyer `apps/api/src/pdf-text.ts` `pdfLayout` (26.10.2026). Roller_Buyer reads the PDF with pdf.js 4.10:
every text item of a page with its x, y (PDF user space, y up) and width, and the page's text as the items joined by
spaces. ERP has pypdf (already in the lock file): its fonts (ToUnicode, /Differences, glyph widths) and its text state
(`TextStateManager`) give where every glyph of the content stream starts and ends; this module glues the glyphs into
items by the rules of pdf.js `getTextContent` (`compareWithLastPosition`, `addFakeSpaces`, src/core/evaluator.js): a gap
up to 0.102 of the font size joins, up to 0.6 joins with a space, a wider gap, a step back, another baseline, another
font (Tf), a restore (Q) that changes the font, a form XObject (Do) or marked content (BDC/BMC/EMC) starts a new item.
So a 1С table cell («1 142,00», «Товары (работы, услуги)») is one word with its x, y and width, as
`app.shared.invoice_lines` expects. Measured on Roller_Buyer's 673 Kaiten invoices (27.10.2026): the same items as
pdf.js — identical header and positions on every one.

Two things pypdf does differently are done here by hand: the widths of a simple font are looked up by the character
CODE (pypdf keys them so but looks them up by the decoded character — every Cyrillic glyph of a single-byte font took
the default width), and a file without a usable startxref is read by scanning, as pdf.js does.

The first `max_pages` pages only (5, as Roller_Buyer), at most `MAX_STRINGS` shown strings a page, form XObjects
`MAX_FORM_DEPTH` deep. Never raises: a damaged, encrypted (with a real password) or exotic file gives an empty layout,
and the caller keeps its other reader. The caller runs the budgeted `app.shared.pdf_text` first (audit F05): a file that
unpacks past the budget never gets here. pypdf's text-state classes are private (`pypdf._font`,
`pypdf._text_extraction._layout_mode`); requirements pin pypdf.
"""

from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass, field
from typing import Any

from .invoice_lines import PdfWord

log = logging.getLogger(__name__)

MAX_PAGES = 5
MAX_FORM_DEPTH = 3
MAX_STRINGS = 50_000  # shown strings per page; a real invoice page has hundreds
# pdf.js getTextContent factors, in font sizes (text space).
TRACKING_SPACE = 0.102
NOT_A_SPACE = 0.03
NEGATIVE_SPACE = -0.2
SPACE_IN_FLOW_MIN = 0.102
SPACE_IN_FLOW_MAX = 0.6
VERTICAL_SHIFT = 0.25
_NO_XREF_TAIL = b"\nstartxref\n0\n%%EOF\n"


@dataclass(frozen=True)
class PdfLayout:
    # The items of a page joined by spaces, a line per page — what the header parsers read.
    text: str = ""
    # Each page's items with their positions, for reading tables.
    pages: list[list[PdfWord]] = field(default_factory=list)


class _Items:
    """pdf.js text items of one page, in content-stream order (its `textContentItem` and `twoLastChars`)."""

    def __init__(self, view: tuple[float, float, float, float]) -> None:
        self.view = view
        self.words: list[PdfWord] = []
        self.texts: list[str] = []
        self.parts: list[str] = []
        self.initialized = False
        self.x = self.y = 0.0
        self.height = 0.0
        self.size = 0.0  # Tf size of the item
        self.scale = 1.0  # pdf.js textAdvanceScale
        self.last: tuple[float, float] | None = None  # pdf.js prevTransform position: where the last glyph ended
        self.two = [" ", " "]
        self.pos = 0

    # twoLastChars: a real space glyph between two words.
    def _save(self, char: str) -> bool:
        nxt = (self.pos + 1) % 2
        found = self.two[self.pos] != " " and self.two[nxt] == " "
        self.two[self.pos] = char
        self.pos = nxt
        return found

    def _space_seen(self) -> bool:
        return self.two[self.pos] != " " and self.two[(self.pos + 1) % 2] == " "

    def _reset(self) -> None:
        self.two = [" ", " "]
        self.pos = 0

    def flush(self) -> None:
        if not self.initialized:
            return
        text = "".join(self.parts)
        if text.strip():
            end = self.last[0] if self.last is not None else self.x
            self.words.append(PdfWord(text, self.x, self.y, end - self.x))
            self.texts.append(text)
        self.initialized = False
        self.parts = []

    def space(self) -> None:
        self._save(" ")

    def glyph(self, x: float, y: float, width: float, text: str, size: float, scale: float, height: float) -> None:
        """A visible glyph starting at (x, y), `width` user units wide."""
        x0, y0, x1, y1 = self.view
        if x + width < x0 or x > x1 or y < y0 or y > y1:
            return  # outside the page: pdf.js drops it
        # pdf.js compares with where the last glyph ended even after a flush (prevTransform survives it), with the
        # last item's size and height: only spaces and the space memory change then.
        last = self.last
        if last is not None and last != (x, y):
            advance_x = (x - last[0]) / (self.scale or 1.0)
            advance_y = y - last[1]
            if advance_x < NEGATIVE_SPACE * self.size or abs(advance_y) > self.height:
                self._reset()
                self.flush()
            else:
                if advance_x <= NOT_A_SPACE * self.size:
                    self._reset()
                if advance_x <= TRACKING_SPACE * self.size:
                    if self._space_seen():
                        self._reset()
                        self.flush()
                elif SPACE_IN_FLOW_MIN * self.size <= advance_x <= SPACE_IN_FLOW_MAX * self.size:
                    self._reset()
                    if self.initialized:
                        self.parts.append(" ")
                else:
                    self.flush()
                    self._reset()
                if abs(advance_y) > self.height * VERTICAL_SHIFT:
                    self.flush()
        if not self.initialized:
            self.initialized = True
            self.parts = []
            self.x, self.y = x, y
            self.height, self.size, self.scale = height, size, scale
        if width:
            self.last = (x + width, y)
        if self._save(text[-1:] or " "):
            self.parts.append(" ")
        self.parts.append(text)


def _glyph_run(params: Any, value: bytes | str) -> list[tuple[str, float, float]]:
    """Each glyph of a shown string: its text, its own width and its advance (Tc and Tw added), in text space with the
    font size and Tz applied. pdf.js measures gaps from the glyph's own width, the text position moves by the advance.

    pypdf keys the widths of a simple font by the character CODE (`chr(code)`) but looks them up by the DECODED
    character, so every glyph of a Cyrillic single-byte font (code ≥ 128, /Differences) would take the default width;
    simple fonts are therefore walked byte by byte here. Composite fonts decode to `chr(CID)`, which is their width key.
    """
    font = params.font
    widths = font.character_widths
    default = widths.get("default", 500)
    size, spacing, word, scale = params.font_size, params.Tc, params.Tw, params.Tz / 100.0
    mapping = font.character_map
    out: list[tuple[str, float, float]] = []
    if isinstance(value, bytes) and isinstance(font.encoding, dict):
        encoding = font.encoding
        for code in value:
            char = encoding[code] if code in encoding else chr(code)
            glyph = size * widths.get(chr(code), default) / 1000 * scale
            out.append((mapping.get(char, char), glyph, glyph + (spacing + (word if code == 32 else 0.0)) * scale))
        return out
    decoded = params._decoded_value if isinstance(value, bytes) else value
    for char in decoded:
        glyph = size * (font.space_width if char == font.space_char else widths.get(char, default)) / 1000 * scale
        out.append((mapping.get(char, char), glyph, glyph + spacing * scale))
    return out


class _Page:
    """Runs the text operators of a page (and its form XObjects) through pypdf's text state."""

    def __init__(self, reader: Any, font_type: Any, state_type: Any, view: tuple[float, float, float, float]) -> None:
        self.reader = reader
        self.font_type = font_type
        self.state = state_type()
        self.items = _Items(view)
        self.rotated: list[str] = []
        self.depth = 0  # q nesting
        self.saved: list[tuple[Any, Any]] = []  # (font name, size) at each q — pdf.js flushes on a Q that changes them
        self.font_name: Any = None
        self.trm_dx = 0.0
        self.count = 0
        self.font_cache: dict[int, Any] = {}

    def fonts(self, resources: Any, inherited: dict[str, Any]) -> dict[str, Any]:
        out = dict(inherited)
        try:
            table = resources.get("/Font") if resources is not None else None
            if table is not None:
                table = table.get_object()
                for name in table:
                    obj = table[name].get_object()
                    key = id(obj)
                    if key not in self.font_cache:
                        self.font_cache[key] = self.font_type.from_font_resource(obj)
                    out[name] = self.font_cache[key]
        except Exception:  # noqa: BLE001 — a broken font table: the text of other fonts is still read
            log.debug("invoice PDF: font table not read", exc_info=True)
        return out

    def advance(self, dx: float) -> None:
        self.trm_dx += dx
        self.state.reset_trm()
        self.state.add_trm([1.0, 0.0, 0.0, 1.0, self.trm_dx, 0.0])

    def new_line(self) -> None:
        self.state.reset_trm()
        self.trm_dx = 0.0

    def push(self) -> None:
        self.state.add_q()
        self.saved.append((self.font_name, self.state.font_size))
        self.depth += 1

    def pop(self) -> None:
        if self.depth == 0:
            return
        before = (self.font_name, self.state.font_size)
        self.state.remove_q()
        self.font_name, _ = self.saved.pop()
        self.depth -= 1
        if before != (self.font_name, self.state.font_size):
            self.items.flush()

    def show(self, value: Any) -> None:
        if not isinstance(value, (bytes, str)):
            return
        try:
            params = self.state.text_state_params(value)
        except Exception:  # noqa: BLE001 — no Tf before the text, an unreadable font
            return
        self.count += 1
        run = _glyph_run(params, value)
        if params.font.interpretable:
            transform = params.transform
            if params.rotated:
                text = "".join(text for text, _, _ in run)
                if text.strip():
                    self.rotated.append(text)
            else:
                a, x, y = transform[0], transform[4], params.ty
                size = params.font_size
                scale = math.hypot(transform[0], transform[1])
                height = size * math.hypot(transform[2], transform[3])
                for text, glyph, advance in run:
                    if not text or text.isspace():
                        self.items.space()
                    else:
                        self.items.glyph(x, y, glyph * a, text, size, scale, height)
                    x += advance * a
        self.advance(sum(advance for _, _, advance in run))

    def run(self, operations: list, fonts: dict[str, Any], resources: Any, form_depth: int) -> None:
        state = self.state
        for operands, op in operations:
            if self.count > MAX_STRINGS:
                return
            if op == b"BT" or op == b"ET":
                state.reset_tm()
                self.trm_dx = 0.0
            elif op == b"q":
                self.push()
            elif op == b"Q":
                self.pop()
            elif op == b"cm" and len(operands) == 6:
                state.add_cm(*operands)
            elif op == b"Tf" and len(operands) >= 2:
                if (operands[0], operands[1]) != (self.font_name, state.font_size) or state.font is None:
                    self.items.flush()
                font = fonts.get(operands[0])
                self.font_name = operands[0]
                if font is not None:
                    state.set_font(font, operands[1])
            elif op == b"Tj" and operands:
                self.show(operands[0])
            elif op == b"TJ" and operands and isinstance(operands[0], list):
                for element in operands[0]:
                    if isinstance(element, (bytes, str)):
                        self.show(element)
                    elif isinstance(element, (int, float)) and state.font is not None:
                        try:
                            self.advance(state.text_state_params("").word_tx("", float(element)))
                        except Exception:  # noqa: BLE001
                            pass
            elif op in (b"'", b'"') and operands:
                self.new_line()
                if op == b'"' and len(operands) >= 3:
                    state.set_state_param(b"Tw", operands[0])
                    state.set_state_param(b"Tc", operands[1])
                state.add_tm([0, -state.TL])
                self.show(operands[-1])
            elif op in (b"Td", b"TD") and len(operands) >= 2:
                self.new_line()
                if op == b"TD":
                    state.set_state_param(b"TL", -operands[1])
                state.add_tm(list(operands[:2]))
            elif op == b"Tm" and len(operands) >= 6:
                self.new_line()
                state.reset_tm()
                state.add_tm(list(operands[:6]))
            elif op == b"T*":
                self.new_line()
                state.add_tm([0, -state.TL])
            elif op in (b"Tc", b"Tw", b"Tz", b"TL", b"Ts") and operands:
                state.set_state_param(op, operands)
            elif op in (b"BDC", b"BMC", b"EMC"):
                self.items.flush()
            elif op == b"Do" and operands:
                self.items.flush()
                if form_depth < MAX_FORM_DEPTH:
                    self.form(operands[0], fonts, resources, form_depth)

    def form(self, name: Any, fonts: dict[str, Any], resources: Any, form_depth: int) -> None:
        """A form XObject's own content, under its /Matrix and with its own fonts."""
        try:
            xobjects = resources.get("/XObject").get_object() if resources is not None and "/XObject" in resources else None
            xobject = xobjects[name].get_object() if xobjects is not None and name in xobjects else None
            if xobject is None or xobject.get("/Subtype") != "/Form":
                return
            from pypdf.generic import ContentStream

            own = xobject.get("/Resources")
            own = own.get_object() if own is not None else resources
            operations = ContentStream(xobject, self.reader, "bytes").operations
        except Exception:  # noqa: BLE001 — a broken form: the rest of the page is still read
            return
        matrix = xobject.get("/Matrix") or [1, 0, 0, 1, 0, 0]
        self.push()
        depth = self.depth
        self.state.add_cm(*[float(v) for v in matrix][:6])
        self.run(operations, self.fonts(own, fonts), own, form_depth + 1)
        self.items.flush()
        while self.depth >= depth:
            self.pop()


def _pypdf_pages(content: bytes, max_pages: int) -> list[tuple[list[PdfWord], str]]:
    from pypdf import PdfReader
    from pypdf._font import Font
    from pypdf._text_extraction._layout_mode._text_state_manager import TextStateManager
    from pypdf.errors import PdfReadError
    from pypdf.generic import ContentStream

    try:
        reader = PdfReader(io.BytesIO(content))
    except PdfReadError:
        # No usable startxref (a hand-made or cut file): pdf.js reads it by scanning, pypdf does that after a pointer
        # it can't follow.
        reader = PdfReader(io.BytesIO(content + _NO_XREF_TAIL))
    if reader.is_encrypted and not reader.decrypt(""):
        return []
    out = []
    for page in reader.pages[:max_pages]:
        try:
            box = page.cropbox
            view = (float(min(box.left, box.right)), float(min(box.bottom, box.top)),
                    float(max(box.left, box.right)), float(max(box.bottom, box.top)))
        except Exception:  # noqa: BLE001
            view = (-math.inf, -math.inf, math.inf, math.inf)
        runner = _Page(reader, Font, TextStateManager, view)
        try:
            resources = page.get("/Resources")
            resources = resources.get_object() if resources is not None else None
            contents = page.get("/Contents")
            if contents is not None:
                operations = ContentStream(contents.get_object(), reader, "bytes").operations
                runner.run(operations, runner.fonts(resources, {}), resources, 0)
        except Exception:  # noqa: BLE001 — what was read before the damage stays
            log.info("invoice PDF: page text partly read", exc_info=True)
        runner.items.flush()
        out.append((runner.items.words, " ".join(runner.items.texts + runner.rotated)))
    return out


def pdf_layout(content: bytes, max_pages: int = MAX_PAGES) -> PdfLayout:
    """The items of the first `max_pages` pages and the text for the header parser; empty when not readable."""
    if b"%PDF" not in content[:1024]:
        return PdfLayout()
    try:
        pages = _pypdf_pages(content, max_pages)
    except Exception:  # noqa: BLE001 — pypdf raises many types on damaged files; the other reader takes over
        log.info("invoice PDF: layout text failed", exc_info=True)
        return PdfLayout()
    return PdfLayout("".join(text + "\n" for _, text in pages), [words for words, _ in pages])

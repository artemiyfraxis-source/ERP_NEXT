"""Text layer of a PDF, without external libraries.

SUPPLYMAP recognises an uploaded invoice with Google Drive OCR (`Invoices.gs ocrToText_`). ERP has no OCR
service, so it reads the text layer the PDF already carries: invoices exported from 1С, bank clients and
supplier portals have one. A scan or a photo has none — the caller gets an empty string and the buyer types
№ and date, as SUPPLYMAP asks when OCR fails.

Deliberately small: indirect objects and object streams, Flate streams, fonts with a ToUnicode CMap (the
Identity-H fonts of Cyrillic PDFs) and single-byte fonts read as cp1251. Text is joined in content-stream
order, one output line per baseline. Encrypted or damaged files give ''.

Reading is budgeted (audit F05, 10.10): a 20 MB upload of compressed zeros must not unpack into gigabytes. Each Flate
stream unpacks to at most STREAM_LIMIT bytes, a document to DOCUMENT_LIMIT in all, at most OBJECT_LIMIT objects and
CMAP_LIMIT ToUnicode codes are read. Going over raises `PdfLimitExceeded` — «превышен лимит обработки», which the
caller tells apart from '' («нет текстового слоя»).
"""

from __future__ import annotations

import re
import zlib

_OBJECT = re.compile(rb"(\d+)\s+(\d+)\s+obj\b(.*?)\bendobj", re.S)
_REF = re.compile(rb"(\d+)\s+\d+\s+R")
_NAMED_REF = re.compile(rb"/([^\s/<>\[\]()]+)\s+(\d+)\s+\d+\s+R")
_HEX = re.compile(rb"<([0-9A-Fa-f\s]*)>")
_DELIMITERS = b"()<>[]{}/%"
_WHITESPACE = b" \t\r\n\f\x00"
MAX_TEXT = 200_000
STREAM_LIMIT = 4 * 1024 * 1024  # one unpacked Flate stream; a real invoice page is tens of KB
DOCUMENT_LIMIT = 16 * 1024 * 1024  # all unpacked streams of one file together
OBJECT_LIMIT = 50_000  # indirect objects, those inside object streams included
CMAP_LIMIT = 200_000  # ToUnicode codes over all fonts of the file


class PdfLimitExceeded(ValueError):
    """The file needs more unpacking than the budget allows — «превышен лимит обработки», not «нет текста»."""


class _Budget:
    __slots__ = ("left", "codes")

    def __init__(self) -> None:
        self.left = DOCUMENT_LIMIT
        self.codes = CMAP_LIMIT

    def inflate(self, raw: bytes) -> bytes:
        limit = min(STREAM_LIMIT, self.left)
        out = zlib.decompressobj().decompress(raw, limit + 1)  # max_length: never unpacks past the budget
        if len(out) > limit:
            raise PdfLimitExceeded("PDF unpacks beyond the processing limit")
        self.left -= len(out)
        return out

    def spend_codes(self, count: int) -> None:
        self.codes -= count
        if self.codes < 0:
            raise PdfLimitExceeded("PDF fonts map more codes than the processing limit")


def pdf_text(data: bytes) -> str:
    """The text of every page, or '' when the file is not a readable PDF with a text layer.

    Raises `PdfLimitExceeded` when reading would go over the unpacking budget."""
    if not data.startswith(b"%PDF") and b"%PDF" not in data[:1024]:
        return ""
    if b"/Encrypt" in data:
        return ""
    try:
        return _extract(data, _Budget())[:MAX_TEXT]
    except PdfLimitExceeded:
        raise
    except Exception:  # noqa: BLE001 — a damaged file is a file without text, not an error for the buyer
        return ""


def _extract(data: bytes, budget: _Budget) -> str:
    objects = _objects(data, budget)
    fonts = _fonts(objects, budget)
    pages = [body for body in objects.values() if re.search(rb"/Type\s*/Page(?![a-zA-Z])", _head(body))]
    lines: list[str] = []
    size = 0
    for page in pages:
        names = _font_names(page, objects) or fonts["*"]
        for ref in _content_refs(page):
            stream = _stream(objects.get(ref, b""), budget)
            if stream:
                start = len(lines)
                _show(stream, names, fonts, lines)
                size += sum(len(part) for part in lines[start:])
        if size > MAX_TEXT:
            break  # the rest would be cut off anyway
    return "\n".join(line.strip() for line in "".join(lines).split("\n") if line.strip())


def _objects(data: bytes, budget: _Budget) -> dict[int, bytes]:
    objects: dict[int, bytes] = {}
    for match in _OBJECT.finditer(data):
        objects[int(match.group(1))] = match.group(3)
        if len(objects) > OBJECT_LIMIT:
            raise PdfLimitExceeded("PDF has more objects than the processing limit")
    for body in list(objects.values()):
        head = _head(body)
        if not re.search(rb"/Type\s*/ObjStm", head):
            continue
        content = _stream(body, budget)
        count = int(_number(head, b"N") or 0)
        first = int(_number(head, b"First") or 0)
        numbers = content[:first].split()
        pairs = [(int(numbers[i]), int(numbers[i + 1])) for i in range(0, min(len(numbers), count * 2) - 1, 2)]
        for index, (number, offset) in enumerate(pairs):
            end = pairs[index + 1][1] if index + 1 < len(pairs) else len(content) - first
            objects.setdefault(number, content[first + offset:first + end])
        if len(objects) > OBJECT_LIMIT:
            raise PdfLimitExceeded("PDF has more objects than the processing limit")
    return objects


def _head(body: bytes) -> bytes:
    position = body.find(b"stream")
    return body if position < 0 else body[:position]


def _number(head: bytes, key: bytes) -> bytes | None:
    match = re.search(rb"/" + key + rb"\s+(\d+)", head)
    return match.group(1) if match else None


def _stream(body: bytes, budget: _Budget) -> bytes:
    start = body.find(b"stream")
    end = body.rfind(b"endstream")
    if start < 0 or end < start:
        return b""
    raw = body[start + 6:end]
    if raw.startswith(b"\r\n"):
        raw = raw[2:]
    elif raw[:1] in (b"\n", b"\r"):
        raw = raw[1:]
    head = body[:start]
    if b"/FlateDecode" in head or b"/Fl " in head or b"/Fl]" in head:
        return budget.inflate(raw)
    if b"/Filter" in head:
        return b""  # images and exotic filters carry no text we can read
    return raw


def _fonts(objects: dict[int, bytes], budget: _Budget) -> dict:
    """Font object → (code width, code → text); '*' is the name → font map of the whole file."""
    fonts: dict = {"*": {}}
    for number, body in objects.items():
        head = _head(body)
        if not re.search(rb"/Type\s*/Font\b", head):
            continue
        cmap_ref = re.search(rb"/ToUnicode\s+(\d+)\s+\d+\s+R", head)
        if cmap_ref:
            fonts[number] = _cmap(_stream(objects.get(int(cmap_ref.group(1)), b""), budget), budget)
        elif re.search(rb"/Subtype\s*/Type0", head):
            fonts[number] = (2, {})
        else:
            fonts[number] = (1, None)
    for body in objects.values():
        fonts["*"].update(_font_names(body, objects))
    return fonts


def _font_names(body: bytes, objects: dict[int, bytes]) -> dict[bytes, int]:
    """`/F1 5 0 R` entries of the /Font dictionary reachable from a page or resources object."""
    head = _head(body)
    resources = re.search(rb"/Resources\s+(\d+)\s+\d+\s+R", head)
    if resources:
        head = _head(objects.get(int(resources.group(1)), b""))
    font = re.search(rb"/Font\s*(<<.*?>>|(\d+)\s+\d+\s+R)", head, re.S)
    if not font:
        return {}
    table = _head(objects.get(int(font.group(2)), b"")) if font.group(2) else font.group(1)
    return {name: int(ref) for name, ref in _NAMED_REF.findall(table)}


def _content_refs(page: bytes) -> list[int]:
    head = _head(page)
    match = re.search(rb"/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R)", head)
    return [int(ref) for ref in _REF.findall(match.group(1))] if match else []


def _cmap(stream: bytes, budget: _Budget) -> tuple[int, dict[int, str]]:
    width = 2
    space = re.search(rb"begincodespacerange\s*<([0-9A-Fa-f]+)>", stream)
    if space:
        width = max(1, len(space.group(1)) // 2)
    table: dict[int, str] = {}
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", stream, re.S):
        values = _HEX.findall(block)
        for source, target in zip(values[::2], values[1::2]):
            table[_int(source)] = _utf16(target)
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", stream, re.S):
        for low, high, target in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]+>|\[[^\]]*\])", block):
            start, stop = _int(low), _int(high)
            budget.spend_codes(min(max(stop - start, 0), 0xFFFF) + 1)
            if target.startswith(b"["):
                for offset, item in enumerate(_HEX.findall(target)):
                    table[start + offset] = _utf16(item)
                continue
            base = _hex_bytes(target[1:-1])
            for offset in range(min(stop - start, 0xFFFF) + 1):
                shifted = base[:-1] + bytes([(base[-1] + offset) & 0xFF]) if base else b""
                table[start + offset] = shifted.decode("utf-16-be", "ignore")
    return width, table


def _int(value: bytes) -> int:
    return int(re.sub(rb"\s", b"", value) or b"0", 16)


def _hex_bytes(value: bytes) -> bytes:
    digits = re.sub(rb"\s", b"", value)
    if len(digits) % 2:
        digits += b"0"
    return bytes.fromhex(digits.decode("ascii"))


def _utf16(value: bytes) -> str:
    return _hex_bytes(value).decode("utf-16-be", "ignore")


def _decode(raw: bytes, font) -> str:
    if font is None or font[1] is None:
        return raw.decode("cp1251", "replace")
    width, table = font
    return "".join(
        table.get(int.from_bytes(raw[i:i + width], "big"), "") for i in range(0, len(raw) - width + 1, width)
    )


def _show(stream: bytes, names: dict[bytes, int], fonts: dict, out: list[str]) -> None:
    """Runs the text operators of one content stream; a new baseline starts a new line."""
    font = None
    operands: list = []
    last_y: float | None = None
    for token in _tokens(stream):
        if not isinstance(token, _Operator):
            operands.append(token)
            if len(operands) > 64:
                operands = operands[-64:]
            continue
        op = token.name
        if op == b"Tf" and len(operands) >= 2 and isinstance(operands[-2], _Name):
            font = fonts.get(names.get(operands[-2].name))
        elif op in (b"Tj", b"'", b'"') and operands and isinstance(operands[-1], bytes):
            if op != b"Tj":
                out.append("\n")
            out.append(_decode(operands[-1], font))
        elif op == b"TJ" and operands and isinstance(operands[-1], list):
            for item in operands[-1]:
                if isinstance(item, bytes):
                    out.append(_decode(item, font))
                elif isinstance(item, float) and item < -200:
                    out.append(" ")
        elif op in (b"Td", b"TD") and len(operands) >= 2 and isinstance(operands[-1], float):
            out.append("\n" if abs(operands[-1]) > 0.5 else " ")
        elif op == b"T*":
            out.append("\n")
        elif op == b"Tm" and len(operands) >= 6 and isinstance(operands[-1], float):
            y = operands[-1]
            out.append("\n" if last_y is not None and abs(y - last_y) > 0.5 else " ")
            last_y = y
        elif op == b"BT":
            out.append(" ")
        operands = []


class _Operator:
    __slots__ = ("name",)

    def __init__(self, name: bytes):
        self.name = name


class _Name:
    __slots__ = ("name",)

    def __init__(self, name: bytes):
        self.name = name


def _tokens(data: bytes):
    """PDF content tokens: floats, bytes (strings), lists (arrays), _Name, _Operator. Inline images skipped."""
    stack: list[list] = []
    i, n = 0, len(data)

    def emit(value):
        if stack:
            stack[-1].append(value)
            return None
        return value

    while i < n:
        c = data[i]
        if c in _WHITESPACE:
            i += 1
        elif c == 0x25:  # % comment
            while i < n and data[i] not in b"\r\n":
                i += 1
        elif c == 0x28:  # ( literal string
            value, i = _literal(data, i + 1)
            token = emit(value)
            if token is not None:
                yield token
        elif c == 0x3C and data[i + 1:i + 2] == b"<":
            i += 2
        elif c == 0x3E and data[i + 1:i + 2] == b">":
            i += 2
        elif c == 0x3C:  # <hex>
            end = data.find(b">", i)
            end = n if end < 0 else end
            token = emit(_hex_bytes(data[i + 1:end]))
            i = end + 1
            if token is not None:
                yield token
        elif c == 0x5B:
            stack.append([])
            i += 1
        elif c == 0x5D:
            i += 1
            if stack:
                array = stack.pop()
                token = emit(array)
                if token is not None:
                    yield token
        elif c == 0x2F:  # /Name
            j = i + 1
            while j < n and data[j] not in _WHITESPACE and data[j] not in _DELIMITERS:
                j += 1
            token = emit(_Name(data[i + 1:j]))
            i = j
            if token is not None:
                yield token
        else:
            j = i
            while j < n and data[j] not in _WHITESPACE and data[j] not in _DELIMITERS:
                j += 1
            if j == i:
                i += 1
                continue
            word = data[i:j]
            i = j
            try:
                token = emit(float(word))
            except ValueError:
                if word == b"BI":  # inline image: skip to EI
                    end = data.find(b"EI", i)
                    i = n if end < 0 else end + 2
                    continue
                token = None if stack else _Operator(word)
            if token is not None:
                yield token


_ESCAPES = {ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t", ord("b"): b"\b", ord("f"): b"\f"}


def _literal(data: bytes, i: int) -> tuple[bytes, int]:
    out = bytearray()
    depth = 1
    n = len(data)
    while i < n:
        c = data[i]
        if c == 0x5C and i + 1 < n:  # backslash
            nxt = data[i + 1]
            if nxt in _ESCAPES:
                out += _ESCAPES[nxt]
                i += 2
            elif 0x30 <= nxt <= 0x37:
                j = i + 1
                while j < min(i + 4, n) and 0x30 <= data[j] <= 0x37:
                    j += 1
                out.append(int(data[i + 1:j], 8) & 0xFF)
                i = j
            elif nxt in b"\r\n":
                i += 2
                if nxt == 0x0D and data[i:i + 1] == b"\n":
                    i += 1
            else:
                out.append(nxt)
                i += 2
            continue
        if c == 0x28:
            depth += 1
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return bytes(out), i + 1
        out.append(c)
        i += 1
    return bytes(out), n

"""Tiny hand-built PDFs for text-layer tests (no PDF library in the lock file)."""

import zlib


def _pdf(objects: list[bytes]) -> bytes:
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    for number, body in enumerate(objects, start=1):
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    out += b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
    return bytes(out)


def _stream(content: bytes, compress: bool = True) -> bytes:
    if compress:
        data = zlib.compress(content)
        return b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream"
    return b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"


def identity_pdf(lines: list[str]) -> bytes:
    """Type0/Identity-H font with a ToUnicode CMap: glyph code = position in the alphabet of the text."""
    alphabet = sorted(set("".join(lines)))
    codes = {char: index + 1 for index, char in enumerate(alphabet)}
    bfchar = b"".join(
        b"<%04X> <%s>\n" % (codes[char], char.encode("utf-16-be").hex().upper().encode()) for char in alphabet
    )
    cmap = (
        b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n1 begincodespacerange\n<0000> <FFFF>\n"
        b"endcodespacerange\n%d beginbfchar\n" % len(alphabet) + bfchar + b"endbfchar\nendcmap\nend\nend\n"
    )
    content = b"BT\n/F1 10 Tf\n"
    y = 800
    for line in lines:
        x = 40
        # Every word is its own Tm on the same baseline, as 1С writes table cells.
        for word in line.split(" "):
            hexed = "".join(f"{codes[char]:04X}" for char in word)
            content += b"1 0 0 1 %d %d Tm\n[<%s>] TJ\n" % (x, y, hexed.encode())
            x += 10 * (len(word) + 1)
        y -= 14
    content += b"ET\n"
    return _pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        _stream(content),
        b"<< /Type /Font /Subtype /Type0 /BaseFont /Arial /Encoding /Identity-H /ToUnicode 6 0 R >>",
        _stream(cmap),
    ])


def cp1251_pdf(lines: list[str]) -> bytes:
    """Single-byte font, literal strings, Td line breaks, uncompressed content, indirect resources."""
    content = b"BT /F1 12 Tf 40 800 Td\n"
    for index, line in enumerate(lines):
        escaped = line.encode("cp1251").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        content += (b"0 -14 Td\n" if index else b"") + b"(" + escaped + b") Tj\n"
    content += b"ET\n"
    return _pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources 6 0 R /Contents 4 0 R >>",
        _stream(content, compress=False),
        b"<< /Type /Font /Subtype /TrueType /BaseFont /Arial /Encoding /WinAnsiEncoding >>",
        b"<< /Font << /F1 5 0 R >> >>",
    ])


def flate_bomb_pdf(unpacked: int = 64 * 1024 * 1024) -> bytes:
    """A page whose ~64 KB Flate content stream unpacks to `unpacked` bytes of spaces (audit F05 fixture)."""
    packer = zlib.compressobj(9)
    chunk = b" " * (1024 * 1024)
    data = b"".join(packer.compress(chunk) for _ in range(unpacked // len(chunk))) + packer.flush()
    return _pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream",
    ])

"""What a stored file really is, read from its bytes (magic numbers) — never from the MIME the uploader declared.

Pure rules shared by the upload commands (the type a new file is kept under) and `stored_files.file_response` (how it
is served). Only a PDF and raster pictures count as viewable; everything else is `application/octet-stream`.
"""

from __future__ import annotations

OCTET_STREAM = "application/octet-stream"
# Viewable in the tab: formats the browser renders without running page scripts in the ERP origin.
INLINE_TYPES = frozenset({"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"})


def sniffed_type(content: bytes) -> str | None:
    """The viewable type the bytes really are (magic numbers), or None — not a PDF or a raster picture."""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    # PDF readers accept a header within the first KB (some 1С exports carry a prefix); served as application/pdf
    # with nosniff, such a file opens only in the PDF viewer whatever else its bytes contain.
    if b"%PDF-" in content[:1024]:
        return "application/pdf"
    return None


def stored_content_type(content: bytes) -> str:
    """The type a new upload is kept under: the sniffed viewable type, else `application/octet-stream`."""
    return sniffed_type(content) or OCTET_STREAM

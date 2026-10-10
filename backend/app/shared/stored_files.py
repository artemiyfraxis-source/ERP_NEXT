"""Files kept in the ERP database in place of SUPPLYMAP's Drive folders (УПД, счета, КД, эскизы).

Each module owns its table; this is the transport and the one serving policy they all share. The browser sends the
file as base64 in JSON (as `google.script.run` did). What the file *is* comes from its bytes, never from the MIME the
uploader declared: a PDF or a raster picture (PNG, JPEG, GIF, WEBP) opens in the browser tab like a Drive link;
anything else — HTML, SVG, XML, scripts, office files, archives, unknown bytes — is handed over as a download
(`attachment`, `application/octet-stream`), so an uploaded page can never run in the ERP origin with the viewer's
session (audit F04, 10.10). Old rows stored with the uploader's MIME are covered too: the type is sniffed again on
every serve.
"""

from __future__ import annotations

import base64
import binascii
from urllib.parse import quote

from fastapi import HTTPException, Response

from app.shared.file_types import OCTET_STREAM, sniffed_type

# A PDF viewer runs no page scripts; `sandbox` is left out because it stops Chrome's built-in PDF viewer.
_PDF_POLICY = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
_IMAGE_POLICY = _PDF_POLICY + "; sandbox"
_DOWNLOAD_POLICY = "default-src 'none'; frame-ancestors 'none'; sandbox"


def decode_upload(content_base64: str, damaged: str) -> bytes:
    """Base64 from the upload form; a broken one is a 422 with the module's own wording."""
    try:
        return base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status_code=422, detail=damaged) from error


def file_response(content: bytes, file_name: str, *, download: bool = False) -> Response:
    """The stored file: a PDF or a picture opens in the browser tab (unless `download`), everything else downloads."""
    kind = sniffed_type(content)
    name = quote(file_name)
    if kind is None or download:
        return Response(content=content, media_type=kind or OCTET_STREAM, headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{name}",
            "Content-Security-Policy": _DOWNLOAD_POLICY,
            "X-Content-Type-Options": "nosniff",
        })
    return Response(content=content, media_type=kind, headers={
        "Content-Disposition": f"inline; filename*=UTF-8''{name}",
        "Content-Security-Policy": _PDF_POLICY if kind == "application/pdf" else _IMAGE_POLICY,
        "X-Content-Type-Options": "nosniff",
    })

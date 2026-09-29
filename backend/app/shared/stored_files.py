"""Files kept in the ERP database in place of SUPPLYMAP's Drive folders (УПД, счета).

Each module owns its table; this is the transport both share: the browser sends the file as base64 in
JSON (as `google.script.run` did), and the stored bytes come back inline under their own name.
"""

from __future__ import annotations

import base64
import binascii
from urllib.parse import quote

from fastapi import HTTPException, Response


def decode_upload(content_base64: str, damaged: str) -> bytes:
    """Base64 from the upload form; a broken one is a 422 with the module's own wording."""
    try:
        return base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status_code=422, detail=damaged) from error


def file_response(content: bytes, content_type: str, file_name: str) -> Response:
    """The stored file, opened in the browser tab like a Drive link."""
    return Response(content=content, media_type=content_type, headers={
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(file_name)}",
    })

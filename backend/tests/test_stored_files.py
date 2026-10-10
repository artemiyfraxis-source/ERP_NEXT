"""One serving policy for every stored file (audit F04): the bytes decide, only PDF and raster pictures open inline."""

import base64
from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shared.file_types import sniffed_type, stored_content_type
from app.shared.security_headers import SecurityHeadersMiddleware
from app.shared.stored_files import file_response

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
VIEWABLE = [
    (b"%PDF-1.7\n1 0 obj<<>>endobj", "application/pdf"),
    (b"\xef\xbb\xbf junk from 1C\n%PDF-1.4\n", "application/pdf"),
    (PNG, "image/png"),
    (b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg"),
    (b"GIF89a\x01\x00\x01\x00", "image/gif"),
    (b"RIFF\x24\x00\x00\x00WEBPVP8 ", "image/webp"),
]
ACTIVE = [
    b"<!doctype html><script>fetch('/api/identity/me')</script>",
    b"<html><body onload=alert(1)>",
    b"<?xml version='1.0'?><svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>",
    b"<svg><script>alert(1)</script></svg>",
    b"alert(document.cookie)",  # a script loaded by an uploaded page via <script src>
    b"<?xml version='1.0'?><x:script xmlns:x='http://www.w3.org/1999/xhtml'>alert(1)</x:script>",
    b"PK\x03\x04 office document",
    b"",
]


@pytest.mark.parametrize("content,kind", VIEWABLE)
def test_pdf_and_raster_pictures_are_sniffed_from_their_bytes(content, kind):
    assert sniffed_type(content) == kind
    assert stored_content_type(content) == kind


@pytest.mark.parametrize("content", ACTIVE)
def test_pages_svg_scripts_and_unknown_bytes_are_not_viewable(content):
    assert sniffed_type(content) is None
    assert stored_content_type(content) == "application/octet-stream"


@pytest.mark.parametrize("content", ACTIVE)
def test_active_content_downloads_as_octet_stream_whatever_was_declared(content):
    response = file_response(content, "отчёт.html")

    assert response.media_type == "application/octet-stream"
    assert unquote(response.headers["content-disposition"]) == "attachment; filename*=UTF-8''отчёт.html"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]
    assert "default-src 'none'" in response.headers["content-security-policy"]


def test_pdf_opens_inline_without_sandbox_and_pictures_with_it():
    pdf = file_response(VIEWABLE[0][0], "счёт.pdf")
    picture = file_response(PNG, "эскиз.png")

    assert (pdf.media_type, pdf.headers["content-disposition"].split(";")[0]) == ("application/pdf", "inline")
    assert pdf.headers["x-content-type-options"] == "nosniff"
    # `sandbox` stops Chrome's PDF viewer; nothing else may load or run.
    assert "default-src 'none'" in pdf.headers["content-security-policy"]
    assert "sandbox" not in pdf.headers["content-security-policy"]
    assert (picture.media_type, picture.headers["content-disposition"].split(";")[0]) == ("image/png", "inline")
    assert "sandbox" in picture.headers["content-security-policy"]


def test_download_flag_keeps_the_real_type_but_never_inline():
    response = file_response(VIEWABLE[0][0], "Запрос.pdf", download=True)

    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"].startswith("attachment;")


def test_app_wide_headers_keep_the_policy_of_a_stored_file():
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.get("/api/file")(lambda: file_response(b"<html><script>alert(1)</script>", "a.html"))
    app.get("/api/plain")(lambda: {"ok": True})
    client = TestClient(app)

    stored = client.get("/api/file")
    plain = client.get("/api/plain")

    assert stored.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'; sandbox"
    assert stored.headers["x-content-type-options"] == "nosniff"
    assert "script-src 'self'" in plain.headers["content-security-policy"]

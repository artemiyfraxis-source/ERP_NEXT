"""Read-only Drive downloader for the one-time КД import — over httpx.MockTransport, no network."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from app.modules.requests.google_drive import DriveError, GoogleDriveReader, drive_reader_from_env
from app.modules.requests.google_sheets import ENV_VAR, ServiceAccount
from tests.requests.test_calc_link import _b64url_decode, key_pem  # noqa: F401 — fixture


def _reader(key_pem, handler):  # noqa: F811
    account = ServiceAccount.from_info({"client_email": "erp-reader@x.iam.gserviceaccount.com", "private_key": key_pem[0]})
    return GoogleDriveReader(account, client=httpx.Client(transport=httpx.MockTransport(handler)),
                             clock=lambda: 1_000_000)


def drive_handler(files: dict[str, tuple[str, str, bytes]], calls: list[httpx.Request] | None = None):
    """Drive v3 with `files`: id → (name, mimeType, bytes). Anything but a GET of a file (or the token) fails."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if request.url.host == "oauth2.googleapis.com":
            claims = json.loads(_b64url_decode(parse_qs(request.content.decode())["assertion"][0].split(".")[1]))
            assert claims["scope"] == "https://www.googleapis.com/auth/drive.readonly"
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.method == "GET", f"Drive is read-only here: {request.method} {request.url}"
        assert request.headers["Authorization"] == "Bearer tok"
        assert request.url.params["supportsAllDrives"] == "true"
        file_id = request.url.path.rsplit("/", 1)[-1]
        assert request.url.path == f"/drive/v3/files/{file_id}"
        if file_id not in files:
            return httpx.Response(404, json={"error": {"code": 404}})
        name, mime, content = files[file_id]
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, content=content)
        return httpx.Response(200, json={"id": file_id, "name": name, "mimeType": mime, "size": str(len(content))})

    return handler


def test_reader_downloads_bytes_and_type_with_readonly_scope_only(key_pem):  # noqa: F811
    calls: list[httpx.Request] = []
    reader = _reader(key_pem, drive_handler({"f1": ("708К.4КБ1 — Чертёж.pdf", "application/pdf", b"%PDF-1.4")}, calls))
    file = reader.download("f1")
    assert (file.name, file.mime_type, file.content) == ("708К.4КБ1 — Чертёж.pdf", "application/pdf", b"%PDF-1.4")
    reader.download("f1")
    assert sum(1 for call in calls if call.url.host == "oauth2.googleapis.com") == 1
    assert [call.url.params.get("alt") for call in calls if call.url.host != "oauth2.googleapis.com"] == [
        None, "media", None, "media",
    ]


def test_reader_missing_file_and_native_google_doc_are_missing_not_config(key_pem):  # noqa: F811
    reader = _reader(key_pem, drive_handler({"doc": ("Заметки", "application/vnd.google-apps.document", b"")}))
    with pytest.raises(DriveError) as gone:
        reader.download("nope")
    assert gone.value.kind == "missing"
    assert "erp-reader@x.iam.gserviceaccount.com" in str(gone.value)
    with pytest.raises(DriveError) as native:
        reader.download("doc")
    assert native.value.kind == "missing"


def test_reader_refused_key_is_config(key_pem):  # noqa: F811
    reader = _reader(key_pem, lambda request: httpx.Response(401, json={"error": "invalid_grant"}))
    with pytest.raises(DriveError) as refused:
        reader.download("f1")
    assert refused.value.kind == "config"
    assert ENV_VAR in str(refused.value)


def test_reader_from_env_needs_the_key(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    with pytest.raises(DriveError) as missing:
        drive_reader_from_env()
    assert missing.value.kind == "config"
    monkeypatch.setenv(ENV_VAR, "{not json")
    with pytest.raises(DriveError) as broken:
        drive_reader_from_env()
    assert broken.value.kind == "config"
    assert ENV_VAR in str(broken.value)

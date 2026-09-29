"""Drive API v3 adapter for КД (`tcKdFolder_` / `createFile` / `setTrashed`) — over httpx.MockTransport, no network."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from app.modules.requests.google_drive import (
    FOLDER_ENV_VAR,
    OAUTH_ENV_VAR,
    DriveError,
    GoogleDriveKd,
    folder_key,
    kd_drive_from_env,
)
from app.modules.requests.google_sheets import ENV_VAR, ServiceAccount
from tests.requests.test_calc_link import _b64url_decode, key_pem  # noqa: F401 — fixture


def _drive(key_pem, handler):  # noqa: F811
    account = ServiceAccount.from_info({"client_email": "erp@x.iam.gserviceaccount.com", "private_key": key_pem[0]})
    return GoogleDriveKd(account, "root", client=httpx.Client(transport=httpx.MockTransport(handler)),
                         clock=lambda: 1_000_000)


def test_folder_key_follows_tc_folder_key():
    assert folder_key("703К.") == folder_key("703к") == folder_key("703К. ") == "703К"


def test_drive_finds_folder_by_key_creates_missing_uploads_multipart_and_trashes(key_pem):  # noqa: F811
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "oauth2.googleapis.com":
            claims = json.loads(_b64url_decode(parse_qs(request.content.decode())["assertion"][0].split(".")[1]))
            assert claims["scope"] == "https://www.googleapis.com/auth/drive"
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer tok"
        assert request.url.params["supportsAllDrives"] == "true"
        if request.method == "GET":
            parent = "root" if "'root' in parents" in request.url.params["q"] else "other"
            files = [{"id": "head", "name": "708К. "}] if parent == "root" else []
            return httpx.Response(200, json={"files": files})
        if request.method == "POST" and request.url.path == "/drive/v3/files":
            body = json.loads(request.content)
            assert body == {"name": "708К.4", "mimeType": "application/vnd.google-apps.folder", "parents": ["head"]}
            return httpx.Response(200, json={"id": "full"})
        if request.method == "POST" and request.url.path == "/upload/drive/v3/files":
            assert request.url.params["uploadType"] == "multipart"
            assert request.headers["Content-Type"].startswith("multipart/related; boundary=")
            assert '"name": "708К.4КБ1 — Чертёж.pdf"'.encode() in request.content
            assert b'"parents": ["full"]' in request.content
            assert b"Content-Type: application/pdf\r\n\r\n%PDF" in request.content
            return httpx.Response(200, json={"id": "f1", "name": "708К.4КБ1 — Чертёж.pdf",
                                             "webViewLink": "https://drive.google.com/file/d/f1/view"})
        if request.method == "PATCH":
            assert request.url.path == "/drive/v3/files/f1"
            assert json.loads(request.content) == {"trashed": True}
            return httpx.Response(200, json={"id": "f1"})
        raise AssertionError(request)

    drive = _drive(key_pem, handler)
    assert drive.folder("root", "708К", "708К.") == "head"
    assert drive.folder("head", "708К.4", "708К.4") == "full"
    file = drive.upload("full", "708К.4КБ1 — Чертёж.pdf", "application/pdf", b"%PDF-1.4")
    assert (file.id, file.url) == ("f1", "https://drive.google.com/file/d/f1/view")
    drive.trash("f1")
    assert sum(1 for call in calls if call.url.host == "oauth2.googleapis.com") == 1


def test_drive_refusal_names_the_service_account(key_pem):  # noqa: F811
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(403, json={})

    with pytest.raises(DriveError) as refused:
        _drive(key_pem, handler).folder("root", "708К", "708К.")
    assert refused.value.kind == "config"
    assert "erp@x.iam.gserviceaccount.com" in str(refused.value)


def test_drive_from_env_needs_key_and_folder(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setenv(FOLDER_ENV_VAR, "root")
    with pytest.raises(DriveError) as missing:
        kd_drive_from_env()
    assert missing.value.kind == "config"
    monkeypatch.setenv(ENV_VAR, "{not json")
    with pytest.raises(DriveError) as broken:
        kd_drive_from_env()
    assert broken.value.kind == "config"
    assert ENV_VAR in str(broken.value)


def test_drive_writes_as_folder_owner_through_oauth_refresh_token(monkeypatch):
    """«!Проекты» SUPPLYMAP лежит в «Моём диске» — сервисный аккаунт туда не пишет, ERP пишет от владельца."""
    monkeypatch.setenv(OAUTH_ENV_VAR, json.dumps({
        "client_id": "cid", "client_secret": "sec", "refresh_token": "rt", "account": "owner@gmail.com",
    }))
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setenv(FOLDER_ENV_VAR, "root-oauth")
    drive = kd_drive_from_env()
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            seen.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
            return httpx.Response(200, json={"access_token": "user-tok", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer user-tok"
        return httpx.Response(200, json={"files": [{"id": "f533", "name": "533К."}]})

    drive._client = httpx.Client(transport=httpx.MockTransport(handler))
    assert drive.root_folder_id == "root-oauth"
    assert drive.folder("root-oauth", "533К", "533К.") == "f533"
    assert seen == [{"grant_type": "refresh_token", "client_id": "cid", "client_secret": "sec", "refresh_token": "rt"}]


def test_drive_oauth_json_must_carry_refresh_token(monkeypatch):
    monkeypatch.setenv(OAUTH_ENV_VAR, json.dumps({"client_id": "cid", "client_secret": "sec"}))
    monkeypatch.setenv(FOLDER_ENV_VAR, "root-bad")
    with pytest.raises(DriveError) as broken:
        kd_drive_from_env()
    assert broken.value.kind == "config"
    assert OAUTH_ENV_VAR in str(broken.value)

"""Google Drive adapter for project КД — SUPPLYMAP TcKd.gs keeps the files on Drive, not in the register.

GAS wrote as the web-app owner (`DriveApp`). Here the server writes through Drive API v3 as the same Google
service account that reads calculations (``ERP_GOOGLE_SERVICE_ACCOUNT_JSON``, JWT on pure Python + ``httpx``,
see `google_sheets`). The root — the «Проекты» folder of `tcProjectsFolder_` — comes from
``ERP_KD_DRIVE_FOLDER_ID``. A service account has no storage quota of its own, so that folder must live on a
shared drive where the account is a content manager (README).

Only four calls are needed: list sub-folders, create a folder, multipart upload, trash. Tests replace the whole
adapter (`app.state.kitset_kd_drive`) with a fake; nothing here runs in tests against the network.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote

import httpx

from .application import CalcSheetError
from .google_sheets import ENV_VAR, ServiceAccount, load_service_account

FOLDER_ENV_VAR = "ERP_KD_DRIVE_FOLDER_ID"
SCOPE = "https://www.googleapis.com/auth/drive"
FILES_API = "https://www.googleapis.com/drive/v3/files"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
TIMEOUT_SECONDS = 60.0

NOT_CONFIGURED = (
    "Хранение КД на Google Диске не настроено на сервере ERP: администратору нужно задать "
    f"{ENV_VAR} (ключ сервисного аккаунта Google) и {FOLDER_ENV_VAR} (папка «Проекты» на Диске), см. README."
)


class DriveError(RuntimeError):
    """``kind``: ``config`` (no key / no folder / key refused) → 503, ``upstream`` → 502."""

    def __init__(self, message: str, kind: str = "upstream"):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class DriveFile:
    id: str
    url: str
    name: str = ""


def folder_key(name: str) -> str:
    """`tcFolderKey_`: головные папки названы вразнобой — «703К.», «221К», «479К. »; хвост из точек и пробелов
    и регистр не важны."""
    return re.sub(r"[.\s]+$", "", str(name or "")).strip().upper()


def file_url(file_id: str) -> str:
    """What `file.getUrl()` gives in GAS, for the rare answer without ``webViewLink``."""
    return f"https://drive.google.com/file/d/{file_id}/view?usp=drivesdk"


class GoogleDriveKd:
    """КД storage over Drive API v3 (`KdDrive` port of the «Проекты» workflow)."""

    def __init__(
        self,
        account: ServiceAccount,
        root_folder_id: str,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.account = account
        self.root_folder_id = root_folder_id
        self._client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
        self._clock = clock
        self._token = ""
        self._token_expires = 0.0
        self._lock = threading.Lock()

    def _access_token(self) -> str:
        with self._lock:
            now = self._clock()
            if self._token and now < self._token_expires - 60:
                return self._token
            try:
                response = self._client.post(
                    self.account.token_uri,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": self.account.assertion(int(now), SCOPE),
                    },
                )
            except httpx.HTTPError as error:
                raise DriveError("Google не ответил на вход сервисного аккаунта — повторите позже.") from error
            if response.status_code != 200:
                raise DriveError(
                    f"Google отклонил ключ сервисного аккаунта {self.account.client_email} "
                    f"(HTTP {response.status_code}) — проверьте ключ в {ENV_VAR}.",
                    "config",
                )
            body = response.json()
            self._token = str(body.get("access_token") or "")
            self._token_expires = now + float(body.get("expires_in") or 3600)
            return self._token

    def _call(self, method: str, url: str, **kwargs) -> httpx.Response:
        headers = dict(kwargs.pop("headers", None) or {})
        headers["Authorization"] = f"Bearer {self._access_token()}"
        try:
            response = self._client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as error:
            raise DriveError("Google Диск не ответил — повторите позже.") from error
        if response.status_code in (401, 403, 404):
            raise DriveError(
                "Нет доступа к папке КД на Google Диске (или её нет). Добавьте "
                f"{self.account.client_email} в общий диск с папкой «Проекты» как «Менеджер контента» "
                f"и проверьте {FOLDER_ENV_VAR}.",
                "config" if response.status_code != 404 else "upstream",
            )
        if response.status_code >= 300:
            raise DriveError(f"Google Диск ответил ошибкой HTTP {response.status_code} — повторите позже.")
        return response

    def _folders(self, parent_id: str) -> list[dict]:
        query = f"'{parent_id.replace(chr(39), '')}' in parents and mimeType='{FOLDER_MIME}' and trashed=false"
        found: list[dict] = []
        page = ""
        while True:
            params = [
                ("q", query), ("fields", "nextPageToken,files(id,name)"), ("pageSize", "1000"),
                ("supportsAllDrives", "true"), ("includeItemsFromAllDrives", "true"),
            ]
            if page:
                params.append(("pageToken", page))
            body = self._call("GET", FILES_API, params=params).json()
            found.extend(body.get("files") or [])
            page = str(body.get("nextPageToken") or "")
            if not page:
                return found

    def folder(self, parent_id: str, wanted: str, create_as: str) -> str:
        """`tcFindOrCreateFolder_`: an existing sub-folder under `folder_key`, else a new one named `create_as`."""
        key = folder_key(wanted)
        for item in self._folders(parent_id):
            if folder_key(str(item.get("name") or "")) == key:
                return str(item["id"])
        created = self._call(
            "POST", FILES_API, params=[("supportsAllDrives", "true"), ("fields", "id")],
            json={"name": create_as, "mimeType": FOLDER_MIME, "parents": [parent_id]},
        ).json()
        return str(created["id"])

    def upload(self, folder_id: str, name: str, content_type: str, content: bytes) -> DriveFile:
        """`folder.createFile(blob).setName(name)` — one multipart request (metadata + bytes)."""
        boundary = "erp-kd-" + secrets.token_hex(12)
        metadata = json.dumps({"name": name, "parents": [folder_id]}, ensure_ascii=False).encode("utf-8")
        body = b"".join([
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode("ascii"), metadata,
            f"\r\n--{boundary}\r\nContent-Type: {content_type or 'application/octet-stream'}\r\n\r\n".encode("utf-8"),
            content, f"\r\n--{boundary}--\r\n".encode("ascii"),
        ])
        created = self._call(
            "POST", UPLOAD_API,
            params=[("uploadType", "multipart"), ("supportsAllDrives", "true"), ("fields", "id,name,webViewLink")],
            headers={"Content-Type": f"multipart/related; boundary={boundary}"}, content=body,
        ).json()
        file_id = str(created["id"])
        return DriveFile(id=file_id, url=str(created.get("webViewLink") or file_url(file_id)),
                         name=str(created.get("name") or name))

    def trash(self, file_id: str) -> None:
        """`DriveApp.getFileById(id).setTrashed(true)` — reversible for 30 days."""
        self._call(
            "PATCH", f"{FILES_API}/{quote(file_id, safe='')}",
            params=[("supportsAllDrives", "true"), ("fields", "id")], json={"trashed": True},
        )


_drives: dict[tuple[str, str], GoogleDriveKd] = {}
_drives_lock = threading.Lock()


def kd_drive_from_env() -> GoogleDriveKd:
    """One adapter per configured key and folder (its access token is cached for the hour Google grants)."""
    raw = os.getenv(ENV_VAR, "")
    folder = os.getenv(FOLDER_ENV_VAR, "").strip()
    if not raw.strip() or not folder:
        raise DriveError(NOT_CONFIGURED, "config")
    with _drives_lock:
        drive = _drives.get((raw, folder))
        if drive is None:
            try:
                account = load_service_account(raw)
            except CalcSheetError as error:
                raise DriveError(str(error), "config") from error
            drive = GoogleDriveKd(account, folder)
            _drives.clear()
            _drives[(raw, folder)] = drive
        return drive

"""Read-only Google Drive downloader — only for the one-time import of historical КД into the ERP database.

КД lives in ERP since 29.09.2026 (owner: off Google Drive). Rows attached while it lived on Drive keep the
Drive file id; `app.modules.techcards.import_kd_from_drive` fetches their bytes through this client. It signs in as
the «erp-reader» service account (``ERP_GOOGLE_SERVICE_ACCOUNT_JSON``, JWT on pure Python + ``httpx``, see
`google_sheets`) with the ``drive.readonly`` scope and makes GET requests only: file metadata and
``alt=media``. Nothing is ever created, changed or trashed on Drive. Tests run it over ``httpx.MockTransport``.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote

import httpx

from .application import CalcSheetError
from .google_sheets import ENV_VAR, ServiceAccount, load_service_account

SCOPE = "https://www.googleapis.com/auth/drive.readonly"
FILES_API = "https://www.googleapis.com/drive/v3/files"
TIMEOUT_SECONDS = 120.0


class DriveError(RuntimeError):
    """``kind``: ``config`` (no key / key refused), ``missing`` (no file or no access to it), ``upstream``."""

    def __init__(self, message: str, kind: str = "upstream"):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class DriveDownload:
    name: str
    mime_type: str
    content: bytes


class GoogleDriveReader:
    """Drive API v3, GET only: `files.get` for the name and type, `files.get?alt=media` for the bytes."""

    def __init__(
        self,
        account: ServiceAccount,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.account = account
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
                response = self._client.post(self.account.token_uri, data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": self.account.assertion(int(now), SCOPE),
                })
            except httpx.HTTPError as error:
                raise DriveError("Google не ответил на вход сервисного аккаунта — повторите позже.") from error
            if response.status_code != 200:
                raise DriveError(
                    f"Google отклонил вход {self.account.client_email} (HTTP {response.status_code}) — "
                    f"проверьте {ENV_VAR}.",
                    "config",
                )
            body = response.json()
            self._token = str(body.get("access_token") or "")
            self._token_expires = now + float(body.get("expires_in") or 3600)
            return self._token

    def _get(self, file_id: str, params: list[tuple[str, str]]) -> httpx.Response:
        try:
            response = self._client.get(
                f"{FILES_API}/{quote(file_id, safe='')}",
                params=[*params, ("supportsAllDrives", "true")],
                headers={"Authorization": f"Bearer {self._access_token()}"},
            )
        except httpx.HTTPError as error:
            raise DriveError("Google Диск не ответил — повторите позже.") from error
        if response.status_code in (403, 404):
            raise DriveError(
                f"Файл {file_id} не найден на Диске или {self.account.client_email} не может его прочитать "
                f"(HTTP {response.status_code}).",
                "missing",
            )
        if response.status_code >= 300:
            raise DriveError(f"Google Диск ответил ошибкой HTTP {response.status_code} на файл {file_id}.")
        return response

    def download(self, file_id: str) -> DriveDownload:
        meta = self._get(file_id, [("fields", "id,name,mimeType,size")]).json()
        if str(meta.get("mimeType") or "").startswith("application/vnd.google-apps."):
            # A native Google Doc/Sheet has no bytes to download (`alt=media` answers 403); КД uploads never are.
            raise DriveError(f"Файл {file_id} — документ Google ({meta.get('mimeType')}), не файл.", "missing")
        content = self._get(file_id, [("alt", "media")]).content
        return DriveDownload(
            name=str(meta.get("name") or ""),
            mime_type=str(meta.get("mimeType") or "") or "application/octet-stream",
            content=content,
        )


def drive_reader_from_env() -> GoogleDriveReader:
    """The «erp-reader» key from ``ERP_GOOGLE_SERVICE_ACCOUNT_JSON`` (the JSON itself or a path to it)."""
    try:
        account = load_service_account(os.getenv(ENV_VAR, ""))
    except CalcSheetError as error:
        raise DriveError(str(error), "config") from error
    return GoogleDriveReader(account)

"""Google Sheets adapter for «Из расчёта» — SUPPLYMAP `extractFromCalc` reads the calc by link.

GAS opened the file as the web-app owner (`SpreadsheetApp.openById`). Here the server reads it through
Sheets API v4 as a Google service account: the operator shares the calculation with the account's
e-mail (Читатель) once, as they would share it with a colleague.

The key comes from ``ERP_GOOGLE_SERVICE_ACCOUNT_JSON`` — either the JSON key itself or a path to the
key file. No Google SDK is in the lock file, so the OAuth2 JWT-bearer exchange is done here: an RS256
signature (RSASSA-PKCS1-v1_5 over SHA-256, RFC 8017 §8.2) with the key's own modular arithmetic, then
``httpx`` for the two HTTP calls. Tests replace the whole reader (`CalcSpreadsheetReader` port) with a
fake; nothing here runs in tests against the network.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import httpx

from .application import CalcSheetError, CalcSpreadsheet
from .domain import CALC_PROJECT_CELL, CALC_PROJECT_SHEET, CALC_ZPK_SHEET

ENV_VAR = "ERP_GOOGLE_SERVICE_ACCOUNT_JSON"
SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
TOKEN_URI = "https://oauth2.googleapis.com/token"
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
TIMEOUT_SECONDS = 20.0

# DER DigestInfo prefix for SHA-256 (RFC 8017 §9.2, note 1).
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


# ── RSA private key (PKCS#8 «BEGIN PRIVATE KEY» as Google issues it, or PKCS#1) ──────────────────


@dataclass(frozen=True)
class RsaPrivateKey:
    n: int
    e: int
    d: int
    p: int
    q: int

    @property
    def size_bytes(self) -> int:
        return (self.n.bit_length() + 7) // 8


def _der_read(data: bytes, offset: int) -> tuple[int, bytes, int]:
    """One DER TLV at ``offset`` → (tag, value, next offset)."""
    if offset + 2 > len(data):
        raise ValueError("DER truncated")
    tag = data[offset]
    length = data[offset + 1]
    offset += 2
    if length & 0x80:
        count = length & 0x7F
        if count == 0 or count > 4 or offset + count > len(data):
            raise ValueError("DER length unsupported")
        length = int.from_bytes(data[offset : offset + count], "big")
        offset += count
    end = offset + length
    if end > len(data):
        raise ValueError("DER truncated")
    return tag, data[offset:end], end


def _der_sequence(data: bytes) -> list[tuple[int, bytes]]:
    tag, body, _ = _der_read(data, 0)
    if tag != 0x30:
        raise ValueError("DER SEQUENCE expected")
    items: list[tuple[int, bytes]] = []
    offset = 0
    while offset < len(body):
        item_tag, value, offset = _der_read(body, offset)
        items.append((item_tag, value))
    return items


def _rsa_from_pkcs1(der: bytes) -> RsaPrivateKey:
    fields = _der_sequence(der)
    if len(fields) < 6 or any(tag != 0x02 for tag, _ in fields[:6]):
        raise ValueError("not an RSAPrivateKey")
    version, n, e, d, p, q = (int.from_bytes(value, "big") for _, value in fields[:6])
    if version != 0 or n != p * q:
        raise ValueError("not a two-prime RSAPrivateKey")
    return RsaPrivateKey(n=n, e=e, d=d, p=p, q=q)


def load_rsa_private_key(pem: str) -> RsaPrivateKey:
    """PEM → key. Google service-account keys are PKCS#8 wrapping a PKCS#1 RSAPrivateKey."""
    text = str(pem or "").replace("\\n", "\n")
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(lines) < 3 or not lines[0].startswith("-----BEGIN ") or not lines[-1].startswith("-----END "):
        raise ValueError("private_key is not PEM")
    der = base64.b64decode("".join(lines[1:-1]), validate=True)
    if "RSA PRIVATE KEY" in lines[0]:
        return _rsa_from_pkcs1(der)
    fields = _der_sequence(der)
    if len(fields) < 3 or fields[2][0] != 0x04:
        raise ValueError("not a PKCS#8 private key")
    return _rsa_from_pkcs1(fields[2][1])


def rs256_sign(message: bytes, key: RsaPrivateKey) -> bytes:
    """RSASSA-PKCS1-v1_5 with SHA-256; CRT with blinding, result verified before it leaves."""
    k = key.size_bytes
    digest_info = _SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    if k < len(digest_info) + 11:
        raise ValueError("RSA key too short")
    encoded = b"\x00\x01" + b"\xff" * (k - len(digest_info) - 3) + b"\x00" + digest_info
    m = int.from_bytes(encoded, "big")
    while True:
        r = secrets.randbelow(key.n - 2) + 2
        try:
            r_inverse = pow(r, -1, key.n)
            break
        except ValueError:  # pragma: no cover — r shares a factor with n: practically impossible
            continue
    blinded = (m * pow(r, key.e, key.n)) % key.n
    s1 = pow(blinded, key.d % (key.p - 1), key.p)
    s2 = pow(blinded, key.d % (key.q - 1), key.q)
    h = (pow(key.q, -1, key.p) * (s1 - s2)) % key.p
    signature = ((s2 + h * key.q) * r_inverse) % key.n
    if pow(signature, key.e, key.n) != m:
        raise ValueError("RSA signature self-check failed")
    return signature.to_bytes(k, "big")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


# ── Service account ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ServiceAccount:
    client_email: str
    private_key: RsaPrivateKey
    private_key_id: str = ""
    token_uri: str = TOKEN_URI

    @classmethod
    def from_info(cls, info: dict) -> ServiceAccount:
        email = str(info.get("client_email") or "").strip()
        if not email or not info.get("private_key"):
            raise ValueError("client_email/private_key missing")
        return cls(
            client_email=email,
            private_key=load_rsa_private_key(str(info["private_key"])),
            private_key_id=str(info.get("private_key_id") or ""),
            token_uri=str(info.get("token_uri") or TOKEN_URI),
        )

    def assertion(self, now: int) -> str:
        header = {"alg": "RS256", "typ": "JWT"}
        if self.private_key_id:
            header["kid"] = self.private_key_id
        claims = {"iss": self.client_email, "scope": SCOPE, "aud": self.token_uri, "iat": now, "exp": now + 3600}
        signing_input = ".".join(
            _b64url(json.dumps(part, separators=(",", ":")).encode("utf-8")) for part in (header, claims)
        )
        signature = rs256_sign(signing_input.encode("ascii"), self.private_key)
        return f"{signing_input}.{_b64url(signature)}"


NOT_CONFIGURED = (
    "Чтение расчёта по ссылке не настроено на сервере ERP: администратору нужно задать "
    f"{ENV_VAR} (ключ сервисного аккаунта Google, см. README)."
)


def load_service_account(raw: str | None) -> ServiceAccount:
    """``ERP_GOOGLE_SERVICE_ACCOUNT_JSON``: the key JSON itself, or a path to the key file."""
    text = str(raw or "").strip()
    if not text:
        raise CalcSheetError(NOT_CONFIGURED, "config")
    try:
        if not text.startswith("{"):
            text = Path(text).expanduser().read_text(encoding="utf-8")
        return ServiceAccount.from_info(json.loads(text))
    except (OSError, ValueError, TypeError) as error:
        raise CalcSheetError(
            f"Ключ сервисного аккаунта Google в {ENV_VAR} не читается ({error.__class__.__name__}) — "
            "проверьте, что там JSON-ключ или путь к нему.",
            "config",
        ) from error


# ── Reader ─────────────────────────────────────────────────────────────────────────────────────


def _a1(sheet: str, cells: str) -> str:
    return "'" + sheet.replace("'", "''") + "'!" + cells


class GoogleSheetsCalcReader:
    """`CalcSpreadsheetReader` over Sheets API v4 (``spreadsheets.get`` + ``values.batchGet``)."""

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
                response = self._client.post(
                    self.account.token_uri,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": self.account.assertion(int(now)),
                    },
                )
            except httpx.HTTPError as error:
                raise CalcSheetError("Google не ответил на вход сервисного аккаунта — повторите позже.") from error
            if response.status_code != 200:
                raise CalcSheetError(
                    f"Google отклонил ключ сервисного аккаунта {self.account.client_email} "
                    f"(HTTP {response.status_code}) — проверьте ключ в {ENV_VAR}.",
                    "config",
                )
            body = response.json()
            self._token = str(body.get("access_token") or "")
            self._token_expires = now + float(body.get("expires_in") or 3600)
            return self._token

    def _get(self, url: str, params: list[tuple[str, str]]) -> dict:
        try:
            response = self._client.get(
                url, params=params, headers={"Authorization": f"Bearer {self._access_token()}"}
            )
        except httpx.HTTPError as error:
            raise CalcSheetError("Google Таблицы не ответили — повторите позже.") from error
        if response.status_code in (403, 404):
            raise CalcSheetError(
                "Нет доступа к таблице расчёта (или её нет по этой ссылке). Откройте доступ «Читатель» "
                f"для {self.account.client_email} — кнопка «Настройки доступа» в таблице — и загрузите снова.",
                "access",
            )
        if response.status_code == 400:
            raise CalcSheetError(
                "Файл по ссылке не читается как Google Таблица (например, это загруженный .xlsx) — "
                "откройте его и сохраните как Google Таблицу.",
                "unsupported",
            )
        if response.status_code != 200:
            raise CalcSheetError(f"Google Таблицы ответили ошибкой HTTP {response.status_code} — повторите позже.")
        return response.json()

    def read_calc(self, spreadsheet_id: str) -> CalcSpreadsheet:
        base = f"{SHEETS_API}/{quote(spreadsheet_id, safe='')}"
        meta = self._get(base, [("fields", "properties.title,sheets.properties.title")])
        titles = {
            str((sheet.get("properties") or {}).get("title") or "") for sheet in meta.get("sheets") or []
        }
        title = str((meta.get("properties") or {}).get("title") or "")
        if CALC_ZPK_SHEET not in titles:
            return CalcSpreadsheet(title=title, zpk_grid=None)
        ranges = [_a1(CALC_ZPK_SHEET, "A:T")]
        if CALC_PROJECT_SHEET in titles:
            ranges.append(_a1(CALC_PROJECT_SHEET, CALC_PROJECT_CELL))
        values = self._get(
            f"{base}/values:batchGet",
            [("ranges", item) for item in ranges]
            + [("valueRenderOption", "FORMATTED_VALUE"), ("majorDimension", "ROWS")],
        )
        blocks = [block.get("values") or [] for block in values.get("valueRanges") or []]
        grid = [[str(cell) for cell in row] for row in (blocks[0] if blocks else [])]
        project_cell = ""
        if len(blocks) > 1 and blocks[1] and blocks[1][0]:
            project_cell = str(blocks[1][0][0])
        return CalcSpreadsheet(title=title, zpk_grid=grid, project_cell=project_cell)


_readers: dict[str, GoogleSheetsCalcReader] = {}
_readers_lock = threading.Lock()


def reader_from_env() -> GoogleSheetsCalcReader:
    """One reader per configured key (its access token is cached for the hour Google grants)."""
    raw = os.getenv(ENV_VAR, "")
    with _readers_lock:
        reader = _readers.get(raw)
        if reader is None:
            reader = GoogleSheetsCalcReader(load_service_account(raw))
            _readers.clear()
            _readers[raw] = reader
        return reader

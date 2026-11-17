"""Validated public origin for links printed outside the authenticated application."""

import os
import base64
import hashlib
import hmac
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class PublicSettings(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    public_base_url: str = ""
    passport_secret: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def mask_secret_input(cls, values):
        # Even ValidationError.errors() must contain a masked SecretStr, never the original setting.
        if isinstance(values, dict) and isinstance(values.get("passport_secret"), str):
            return {**values, "passport_secret": SecretStr(values["passport_secret"])}
        return values

    @field_validator("passport_secret")
    @classmethod
    def strong_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if secret and (not 32 <= len(secret) <= 512 or not secret.isascii() or any(character.isspace() for character in secret)):
            raise ValueError("Ключ публичного паспорта: от 32 до 512 случайных ASCII-символов без пробелов")
        return value

    @field_validator("public_base_url")
    @classmethod
    def https_base(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            return ""
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as error:
            raise ValueError("Публичный адрес ERP должен быть корректным HTTPS-адресом") from error
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path or port == 0
                or any(character.isspace() or ord(character) < 32 for character in value)):
            raise ValueError("Публичный адрес ERP: HTTPS, без пути, параметров и учётных данных")
        if len(value) > 160:
            raise ValueError("Публичный адрес ERP: не более 160 символов")
        return value


def public_settings() -> PublicSettings:
    return PublicSettings(public_base_url=os.getenv("ERP_PUBLIC_BASE_URL", ""),
                          passport_secret=SecretStr(os.getenv("ERP_PASSPORT_SECRET", "")))


def public_passport_token(instrument_id: UUID, secret: SecretStr) -> str:
    raw = secret.get_secret_value()
    if not raw:
        return ""
    digest = hmac.new(raw.encode("ascii"), ("si-passport:" + str(instrument_id)).encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:32]


def verify_passport_token(instrument_id: UUID, supplied: str, secret: SecretStr) -> bool:
    expected = public_passport_token(instrument_id, secret)
    matches = hmac.compare_digest(supplied.encode("utf-8"), expected.encode("ascii"))
    return bool(expected) and matches


def public_passport_url(instrument_id: UUID, settings: PublicSettings | None = None) -> str:
    settings = settings or public_settings()
    token = public_passport_token(instrument_id, settings.passport_secret)
    if not settings.public_base_url or not token:
        return ""
    return f"{settings.public_base_url}/p/{instrument_id.hex}.{token}"


def passport_qr_warning(settings: PublicSettings | None = None) -> str:
    settings = settings or public_settings()
    if not settings.public_base_url:
        return "Публичный адрес ERP не задан. QR содержит только инвентарный номер."
    if not settings.passport_secret.get_secret_value():
        return "Ключ публичного паспорта не задан. QR содержит только инвентарный номер."
    return ""


def instrument_card_url(inventory_number: str, base_url: str | None = None) -> str:
    base = public_settings().public_base_url if base_url is None else PublicSettings(public_base_url=base_url).public_base_url
    if not base:
        return ""
    return base + "/?" + urlencode({"section": "quality", "tab": "instruments", "si": inventory_number})

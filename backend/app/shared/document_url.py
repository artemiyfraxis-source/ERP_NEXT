"""Validation for document links stored and rendered by ERP modules."""

from urllib.parse import urlsplit


def normalize_document_url(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    has_control_character = any(
        ord(character) < 32 or ord(character) == 127 for character in normalized
    )
    if "\\" in normalized or has_control_character:
        raise ValueError("Ссылка на документ содержит недопустимые символы")
    try:
        parsed = urlsplit(normalized)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as error:
        raise ValueError("Некорректная ссылка на документ") from error
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(
            "Ссылка на документ должна быть абсолютным HTTP(S)-адресом без учётных данных"
        )
    return normalized

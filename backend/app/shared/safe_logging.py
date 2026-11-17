"""Keep public-passport bearer URLs out of persisted diagnostic output."""

import logging
import re


# Hide the entire locator and query, including malformed requests and encoded separators.
_PASSPORT_URL = re.compile(r"(?:/|%2f)(?:p|%70)(?:/|%2f)[^\s\"'<>]*", re.IGNORECASE)


def redact_passport_urls(text: str) -> str:
    return _PASSPORT_URL.sub("/p/[redacted]", text)


class PassportSafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Redact after formatting so interpolated arguments and exception tracebacks are covered.
        return redact_passport_urls(super().format(record))

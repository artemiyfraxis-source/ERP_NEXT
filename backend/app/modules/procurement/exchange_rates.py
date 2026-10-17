"""CBR (Bank of Russia) daily exchange rates — port of SUPPLYMAP `Currency.gs` `rateToRub_`."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import TypeVar
from xml.etree import ElementTree

import httpx

from .money import RUB, normalize_currency

CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
CACHE_SECONDS = 6 * 60 * 60
_log = logging.getLogger(__name__)
T = TypeVar("T")


class TransientRateError(RuntimeError):
    """A network failure or a 5xx answer — worth another attempt."""


def with_retry(
    operation: Callable[[], T],
    *,
    attempts: int = 3,
    delay_seconds: float = 0.4,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """`withRetry_`: retry only transient failures, 0.4 s then 0.8 s; the last error propagates."""
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except TransientRateError:
            if attempt >= attempts:
                raise
            sleep(delay_seconds * 2 ** (attempt - 1))
    raise AssertionError("unreachable")


def parse_cbr_rates(document: bytes) -> dict[str, Decimal]:
    """`fetchCbrRates_`: {CharCode: Value / Nominal} — RUB per one unit, comma decimals."""
    root = ElementTree.fromstring(document)
    rates: dict[str, Decimal] = {}
    for valute in root.findall("Valute"):
        code = (valute.findtext("CharCode") or "").strip().upper()
        try:
            nominal = Decimal((valute.findtext("Nominal") or "").strip().replace(",", "."))
            value = Decimal((valute.findtext("Value") or "").strip().replace(",", "."))
        except InvalidOperation:
            continue
        if code and nominal:
            rates[code] = value / nominal
    return rates


class CbrExchangeRates:
    """Daily CBR rates with a 6-hour in-process cache; `None` when the bank did not answer."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        url: str = CBR_URL,
        timeout_seconds: float = 10.0,
        attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._client = client
        self._url = url
        self._timeout = timeout_seconds
        self._attempts = attempts
        self._sleep = sleep
        self._clock = clock
        self._cache: dict[date, tuple[float, dict[str, Decimal]]] = {}
        self._lock = threading.Lock()

    def rate_to_rub(self, currency: str, day: date) -> Decimal | None:
        code = normalize_currency(currency)
        if code is None:
            return None
        if code == RUB:
            return Decimal(1)
        rates = self._rates(day)
        return rates.get(code) if rates is not None else None

    def _rates(self, day: date) -> dict[str, Decimal] | None:
        now = self._clock()
        with self._lock:
            cached = self._cache.get(day)
            if cached is not None and now - cached[0] < CACHE_SECONDS:
                return cached[1]
        try:
            document = with_retry(lambda: self._fetch(day), attempts=self._attempts, sleep=self._sleep)
            rates = parse_cbr_rates(document)
        except (TransientRateError, httpx.HTTPError, ElementTree.ParseError) as error:
            _log.warning("CBR rates for %s unavailable: %s", day.isoformat(), type(error).__name__)
            return None
        if not rates:
            return None
        with self._lock:
            self._cache[day] = (now, rates)
        return rates

    def _fetch(self, day: date) -> bytes:
        params = {"date_req": day.strftime("%d/%m/%Y")}
        try:
            if self._client is not None:
                response = self._client.get(self._url, params=params, timeout=self._timeout)
            else:
                response = httpx.get(self._url, params=params, timeout=self._timeout)
        except httpx.TransportError as error:
            raise TransientRateError(str(error)) from error
        if response.status_code >= 500:
            raise TransientRateError(f"HTTP {response.status_code}")
        response.raise_for_status()
        return response.content

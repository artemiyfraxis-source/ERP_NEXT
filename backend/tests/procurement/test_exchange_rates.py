from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.modules.procurement.exchange_rates import (
    CbrExchangeRates,
    TransientRateError,
    parse_cbr_rates,
    with_retry,
)

CBR_XML = """<?xml version="1.0" encoding="windows-1251"?>
<ValCurs Date="18.09.2026" name="Foreign Currency Market">
<Valute ID="R01235"><NumCode>840</NumCode><CharCode>USD</CharCode><Nominal>1</Nominal><Name>Доллар США</Name><Value>81,5432</Value></Valute>
<Valute ID="R01820"><NumCode>392</NumCode><CharCode>JPY</CharCode><Nominal>100</Nominal><Name>Иен</Name><Value>55,1200</Value></Valute>
</ValCurs>""".encode("cp1251")


def client_answering(*responses: object) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return httpx.Client(transport=httpx.MockTransport(handle)), seen


def test_parser_reads_value_per_nominal_with_comma_decimals():
    assert parse_cbr_rates(CBR_XML) == {"USD": Decimal("81.5432"), "JPY": Decimal("0.5512")}


def test_rate_is_requested_for_the_day_and_cached():
    client, seen = client_answering(httpx.Response(200, content=CBR_XML))
    rates = CbrExchangeRates(client, sleep=lambda _: None)

    assert rates.rate_to_rub("usd", date(2026, 9, 18)) == Decimal("81.5432")
    assert rates.rate_to_rub("JPY", date(2026, 9, 18)) == Decimal("0.5512")
    assert rates.rate_to_rub("руб", date(2026, 9, 18)) == Decimal(1)
    assert rates.rate_to_rub("XXX", date(2026, 9, 18)) is None
    assert len(seen) == 1
    assert seen[0].url.params["date_req"] == "18/09/2026"


def test_network_failures_are_retried_like_supplymap_with_retry():
    pauses: list[float] = []
    client, seen = client_answering(
        httpx.ConnectError("down"), httpx.Response(503), httpx.Response(200, content=CBR_XML),
    )
    rates = CbrExchangeRates(client, sleep=pauses.append)

    assert rates.rate_to_rub("USD", date(2026, 9, 18)) == Decimal("81.5432")
    assert len(seen) == 3
    assert pauses == [0.4, 0.8]


def test_three_failures_mean_no_rate_and_nothing_is_cached():
    client, seen = client_answering(
        httpx.ReadTimeout("slow"), httpx.ConnectError("down"), httpx.Response(502),
        httpx.Response(200, content=CBR_XML),
    )
    rates = CbrExchangeRates(client, sleep=lambda _: None)

    assert rates.rate_to_rub("USD", date(2026, 9, 18)) is None
    assert rates.rate_to_rub("USD", date(2026, 9, 18)) == Decimal("81.5432")
    assert len(seen) == 4


@pytest.mark.parametrize("answer", [httpx.Response(404), httpx.Response(200, content=b"<html>oops")])
def test_client_errors_and_garbage_are_not_retried(answer):
    client, seen = client_answering(answer)
    rates = CbrExchangeRates(client, sleep=lambda _: None)

    assert rates.rate_to_rub("USD", date(2026, 9, 18)) is None
    assert len(seen) == 1


def test_with_retry_propagates_the_last_transient_error():
    calls = []

    def failing():
        calls.append(1)
        raise TransientRateError("down")

    with pytest.raises(TransientRateError):
        with_retry(failing, sleep=lambda _: None)
    assert len(calls) == 3

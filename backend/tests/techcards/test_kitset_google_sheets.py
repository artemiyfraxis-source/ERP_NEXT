import httpx

from tests.requests.test_calc_link import _reader, key_pem  # noqa: F401  (fixture)


def test_reader_reads_whole_named_sheets_padded_and_skips_missing(key_pem):  # noqa: F811
    """«Проекты» by link: ЗКЗ and СПЯ whole (GAS getDataRange().getDisplayValues()); a missing sheet is left out."""
    ranges: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if request.url.path.endswith("/values:batchGet"):
            ranges.append(request.url.params.get_list("ranges"))
            assert request.url.params["valueRenderOption"] == "FORMATTED_VALUE"
            return httpx.Response(200, json={"valueRanges": [{"values": [["Номер", "708К.4"], ["x"]]}]})
        return httpx.Response(200, json={
            "properties": {"title": "708К.4 Расчет"},
            "sheets": [{"properties": {"title": "ЗКЗ"}}, {"properties": {"title": "ЗКП"}}],
        })

    title, sheets = _reader(key_pem, handler).read_sheets("1AbC", ("ЗКЗ", "СПЯ"))

    assert title == "708К.4 Расчет"
    assert ranges == [["'ЗКЗ'"]]
    assert sheets == {"ЗКЗ": [["Номер", "708К.4"], ["x", ""]]}

"""«Из расчёта» по ссылке: SUPPLYMAP `extractFromCalc(url)` / `portalExtractFromCalc` (Imports.gs, Portal.gs)."""

import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs

import httpx
import pytest

from app.main import app
from app.modules.requests.application import (
    CalcSheetError,
    CalcSpreadsheet,
    extract_from_calc,
)
from app.modules.requests.domain import ValidationError, spreadsheet_id_from_url
from app.modules.requests.google_sheets import (
    ENV_VAR,
    GoogleSheetsCalcReader,
    RsaPrivateKey,
    ServiceAccount,
    load_rsa_private_key,
    load_service_account,
    rs256_sign,
)
from tests.requests.test_calc_import import GRID
from tests.requests.test_write_api import write_api  # noqa: F401 — fixture

URL = "https://docs.google.com/spreadsheets/d/1AbC-d_9/edit#gid=0"


class FakeReader:
    def __init__(self, sheet: CalcSpreadsheet | None = None, error: CalcSheetError | None = None):
        self.sheet = sheet
        self.error = error
        self.ids: list[str] = []

    def read_calc(self, spreadsheet_id: str) -> CalcSpreadsheet:
        self.ids.append(spreadsheet_id)
        if self.error:
            raise self.error
        assert self.sheet is not None
        return self.sheet


def test_spreadsheet_id_from_url_follows_supplymap():
    assert spreadsheet_id_from_url(URL) == "1AbC-d_9"
    assert spreadsheet_id_from_url("https://docs.google.com/spreadsheets/u/0/d/XYZ") == "XYZ"
    assert spreadsheet_id_from_url("https://example.test/sheet") == ""
    assert spreadsheet_id_from_url(None) == ""


def test_extract_reads_zpk_and_takes_project_from_zkz_b2():
    reader = FakeReader(CalcSpreadsheet(title="Расчёт 1", zpk_grid=GRID, project_cell=" 48К.13 "))

    extract = extract_from_calc(URL, reader)

    assert reader.ids == ["1AbC-d_9"]
    assert extract.project == "48К.13"
    assert [(item.name, item.unit) for item in extract.items] == [("МГТФ 0,35", "м"), ("DB-9M", "шт")]


def test_extract_falls_back_to_file_title_and_reads_twenty_columns():
    wide = [["x"] * 20 + ["Обозначение", "Без НДС"], ["1"] * 22]
    reader = FakeReader(CalcSpreadsheet(title="Расчёт 591К.3", zpk_grid=wide))

    extract = extract_from_calc(URL, reader)

    assert extract.project == "591К.3"
    assert extract.items == []  # the header past column T is never seen, as in GAS


@pytest.mark.parametrize(
    ("url", "sheet", "message"),
    [
        ("https://example.test/x", None, "Не похоже на ссылку Google Таблицы"),
        (URL, CalcSpreadsheet(title="Расчёт", zpk_grid=None), "В таблице нет листа «ЗКП»"),
    ],
)
def test_extract_refusals_use_supplymap_wording(url, sheet, message):
    with pytest.raises(ValidationError, match=message):
        extract_from_calc(url, FakeReader(sheet))


def test_calc_endpoint_returns_the_preview_with_the_soft_project_warning(write_api):  # noqa: F811
    client, _engine, _sessions = write_api

    class Registry:
        def open_projects(self):
            return ["2000К.1"]

    app.state.calc_spreadsheet_reader = FakeReader(CalcSpreadsheet(title="Расчёт 591К.3", zpk_grid=GRID))
    app.state.project_registry = Registry()
    try:
        response = client.post("/api/requests/import/calc", json={"url": URL})
    finally:
        del app.state.calc_spreadsheet_reader
        del app.state.project_registry

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project"] == "591К.3"
    assert [(item["name"], item["unit"], item["quantity"]) for item in body["items"]] == [
        ("МГТФ 0,35", "м", "12.5"),
        ("DB-9M", "шт", "4"),
    ]
    assert body["warnings"] == [
        "Проект «591К.3» не найден в реестре открытых проектов — импорт не заблокирован (переходный период)"
    ]


def test_calc_endpoint_names_the_service_account_when_the_sheet_is_not_shared(write_api):  # noqa: F811
    client, _engine, _sessions = write_api
    app.state.calc_spreadsheet_reader = FakeReader(
        error=CalcSheetError("Нет доступа к таблице расчёта … для erp@x.iam.gserviceaccount.com", "access")
    )
    try:
        denied = client.post("/api/requests/import/calc", json={"url": URL})
        bad = client.post("/api/requests/import/calc", json={"url": "не ссылка"})
    finally:
        del app.state.calc_spreadsheet_reader

    assert denied.status_code == 422
    assert "erp@x.iam.gserviceaccount.com" in denied.json()["detail"]
    assert bad.status_code == 422
    assert bad.json()["detail"] == "Не похоже на ссылку Google Таблицы"


def test_calc_endpoint_without_the_key_explains_what_to_configure(write_api, monkeypatch):  # noqa: F811
    client, _engine, _sessions = write_api
    monkeypatch.delenv(ENV_VAR, raising=False)

    response = client.post("/api/requests/import/calc", json={"url": URL})

    assert response.status_code == 503
    assert "вставьте диапазон листа ЗКП вручную" in response.json()["detail"]
    assert ENV_VAR not in response.json()["detail"]


# ── Google adapter: JWT-bearer exchange + Sheets v4, over a mock transport ───────────────────────


def _probable_prime(bits: int) -> int:
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if all(pow(a, candidate - 1, candidate) == 1 for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29)):
            return candidate


def _der(tag: int, body: bytes) -> bytes:
    length = len(body)
    if length < 0x80:
        return bytes([tag, length]) + body
    raw = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([tag, 0x80 | len(raw)]) + raw + body


def _der_int(value: int) -> bytes:
    return _der(0x02, value.to_bytes(value.bit_length() // 8 + 1, "big"))


@pytest.fixture(scope="module")
def key_pem():
    e = 65537
    while True:
        p, q = _probable_prime(512), _probable_prime(512)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e:
            break
    n, d = p * q, pow(e, -1, phi)
    pkcs1 = _der(0x30, b"".join(_der_int(v) for v in (0, n, e, d, p, q, d % (p - 1), d % (q - 1), pow(q, -1, p))))
    algorithm = _der(0x30, _der(0x06, bytes.fromhex("2a864886f70d010101")) + b"\x05\x00")
    pkcs8 = _der(0x30, _der_int(0) + algorithm + _der(0x04, pkcs1))
    body = base64.b64encode(pkcs8).decode()
    pem = "-----BEGIN PRIVATE KEY-----\n" + "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
    return pem + "\n-----END PRIVATE KEY-----\n", RsaPrivateKey(n=n, e=e, d=d, p=p, q=q)


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def test_rs256_signature_verifies_with_the_public_key(key_pem):
    pem, expected = key_pem
    key = load_rsa_private_key(pem)
    assert key == expected

    signature = rs256_sign(b"payload", key)

    recovered = pow(int.from_bytes(signature, "big"), key.e, key.n).to_bytes(key.size_bytes, "big")
    assert recovered.endswith(hashlib.sha256(b"payload").digest())
    assert recovered.startswith(b"\x00\x01\xff")


def test_service_account_key_comes_from_json_or_a_file(key_pem, tmp_path):
    info = {"client_email": "erp@x.iam.gserviceaccount.com", "private_key": key_pem[0], "private_key_id": "k1"}
    path = tmp_path / "key.json"
    path.write_text(json.dumps(info), encoding="utf-8")

    assert load_service_account(json.dumps(info)).client_email == "erp@x.iam.gserviceaccount.com"
    assert load_service_account(str(path)).private_key_id == "k1"
    with pytest.raises(CalcSheetError) as missing:
        load_service_account("")
    assert missing.value.kind == "config"
    with pytest.raises(CalcSheetError) as broken:
        load_service_account("{not json")
    assert broken.value.kind == "config"


def _reader(key_pem, handler):
    account = ServiceAccount.from_info(
        {"client_email": "erp@x.iam.gserviceaccount.com", "private_key": key_pem[0], "private_key_id": "k1"}
    )
    return GoogleSheetsCalcReader(account, client=httpx.Client(transport=httpx.MockTransport(handler)), clock=lambda: 1_000_000)


def test_reader_exchanges_a_signed_jwt_and_reads_zpk_and_zkz(key_pem):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "oauth2.googleapis.com":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
            header, claims, signature = form["assertion"][0].split(".")
            assert json.loads(_b64url_decode(header)) == {"alg": "RS256", "typ": "JWT", "kid": "k1"}
            payload = json.loads(_b64url_decode(claims))
            assert payload["iss"] == "erp@x.iam.gserviceaccount.com"
            assert payload["scope"].endswith("spreadsheets.readonly")
            assert payload["exp"] - payload["iat"] == 3600
            key = key_pem[1]
            recovered = pow(int.from_bytes(_b64url_decode(signature), "big"), key.e, key.n)
            digest = hashlib.sha256(f"{header}.{claims}".encode()).digest()
            assert recovered.to_bytes(key.size_bytes, "big").endswith(digest)
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer tok"
        if request.url.path.endswith("/values:batchGet"):
            assert request.url.params.get_list("ranges") == ["'ЗКП'!A:T", "'ЗКЗ'!B2"]
            assert request.url.params["valueRenderOption"] == "FORMATTED_VALUE"
            return httpx.Response(200, json={"valueRanges": [{"values": GRID}, {"values": [["591К.3"]]}]})
        return httpx.Response(
            200,
            json={
                "properties": {"title": "Расчёт 1"},
                "sheets": [{"properties": {"title": "ЗКЗ"}}, {"properties": {"title": "ЗКП"}}],
            },
        )

    reader = _reader(key_pem, handler)
    first = reader.read_calc("1AbC")
    reader.read_calc("1AbC")

    assert first == CalcSpreadsheet(title="Расчёт 1", zpk_grid=GRID, project_cell="591К.3")
    assert [call.url.host for call in calls].count("oauth2.googleapis.com") == 1  # token cached


def test_reader_without_zpk_sheet_reads_no_values(key_pem):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert not request.url.path.endswith("/values:batchGet")
        return httpx.Response(200, json={"properties": {"title": "Расчёт"}, "sheets": [{"properties": {"title": "Лист1"}}]})

    assert _reader(key_pem, handler).read_calc("1AbC").zpk_grid is None


@pytest.mark.parametrize(("status", "kind", "fragment"), [(403, "access", "erp@x.iam.gserviceaccount.com"), (404, "access", "Читатель"), (400, "unsupported", "Google Таблицу"), (500, "upstream", "HTTP 500")])
def test_reader_explains_google_refusals(key_pem, status, kind, fragment):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(status, json={"error": {"code": status}})

    with pytest.raises(CalcSheetError) as error:
        _reader(key_pem, handler).read_calc("1AbC")
    assert error.value.kind == kind
    assert fragment in str(error.value)


def test_reader_reports_a_rejected_key(key_pem):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(CalcSheetError) as error:
        _reader(key_pem, handler).read_calc("1AbC")
    assert error.value.kind == "config"
    assert "вставьте диапазон листа ЗКП вручную" in str(error.value)
    assert ENV_VAR not in str(error.value)

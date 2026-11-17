import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from datetime import datetime
from io import BytesIO
from uuid import UUID, uuid4

from pydantic import SecretStr, ValidationError
from pypdf import PdfReader
import pytest
from sqlalchemy import select

from app.main import app
from app.modules.production.infrastructure import ProductionInstrumentFileRow, ProductionInstrumentRow, SqlAlchemyInstrumentRepository
from app.modules.production.instrument_characterization import characterization
from app.modules.production.instrument_public import public_passport
from app.modules.production.instrument_public_html import PUBLIC_STYLE
from app.modules.production.traceability import InstrumentKind
from app.shared.http_auth import authenticated_principal
from app.shared.public_url import PublicSettings, public_passport_token, public_passport_url, public_settings, verify_passport_token
from app.workflows import instrument_labels, instrument_public_passport as public_api
from tests.production.test_api import MASTER_ID, production_api  # noqa: F401
from tests.production.test_instrument_passport import instrument, measured_file, rf
from tests.workflows.test_instrument_labels import setup

TEST_SECRET = "test-only-passport-key-do-not-deploy-123456789"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("ERP_PUBLIC_BASE_URL", "https://erp.example.test")
    monkeypatch.setenv("ERP_PASSPORT_SECRET", TEST_SECRET)
    monkeypatch.setattr(public_api, "PUBLIC_PASSPORT_LIMITER", public_api.PublicPassportRateLimiter())


def path(item_id, suffix=""):
    url = public_passport_url(UUID(str(item_id)))
    return url.removeprefix("https://erp.example.test") + suffix


def anonymous():
    app.dependency_overrides.pop(authenticated_principal, None)


def pdf_text(content):
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(content)).pages)


def test_token_is_stable_uuid_bound_and_rotates_only_with_the_dedicated_secret():
    item_id = uuid4()
    first = public_passport_token(item_id, SecretStr(TEST_SECRET))
    assert len(first) == 32 and first == public_passport_token(item_id, SecretStr(TEST_SECRET))
    assert first != public_passport_token(uuid4(), SecretStr(TEST_SECRET))
    assert first != public_passport_token(item_id, SecretStr(TEST_SECRET + "rotated"))
    assert verify_passport_token(item_id, first, SecretStr(TEST_SECRET))
    assert not verify_passport_token(uuid4(), first, SecretStr(TEST_SECRET))
    assert not verify_passport_token(item_id, first, SecretStr(TEST_SECRET + "rotated"))
    assert not verify_passport_token(item_id, "", SecretStr(""))
    assert not verify_passport_token(item_id, "не токен", SecretStr(TEST_SECRET))
    assert f"/p/{item_id.hex}.{first}" in public_passport_url(item_id)


@pytest.mark.parametrize("secret", ["too-short-key", " " * 40, "bad key" * 8, "ключ" * 10])
def test_secret_validation_does_not_echo_secret(secret, monkeypatch):
    monkeypatch.setenv("ERP_PASSPORT_SECRET", secret)
    with pytest.raises(ValidationError) as caught:
        public_settings()
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value.errors())


def test_secret_not_in_settings_repr_or_serialized_fields():
    settings = public_settings()
    assert TEST_SECRET not in repr(settings)
    assert TEST_SECRET not in settings.model_dump_json()
    with pytest.raises(ValidationError) as caught:
        PublicSettings(passport_secret="short-private-key")
    assert "short-private-key" not in repr(caught.value.errors())


@pytest.mark.parametrize("setting", ["ERP_PUBLIC_BASE_URL", "ERP_PASSPORT_SECRET"])
def test_link_building_is_disabled_when_either_setting_empty(setting, monkeypatch):
    monkeypatch.delenv(setting)
    assert public_passport_url(uuid4()) == ""


def test_public_html_and_pdf_work_anonymously_and_internal_pdf_stays_authenticated(production_api):
    client, _ = production_api
    item, _sessions = setup(client, online=False)
    anonymous()
    html = client.get(path(item["id"]))
    pdf = client.get(path(item["id"], ".pdf"))
    assert html.status_code == pdf.status_code == 200
    assert "Паспорт СИ.034" in html.text and "Скачать PDF" in html.text
    assert "Замер не проведён" in html.text
    assert '<script' not in html.text and '/assets/' not in html.text
    assert pdf.content.startswith(b"%PDF") and "N8B139" in pdf_text(pdf.content)
    assert client.get(f"/api/production/instruments/{item['id']}/passport.pdf").status_code == 401
    for response in (html, pdf):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-robots-tag"] == "noindex, nofollow"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert "'sha256-" in response.headers["content-security-policy"]
    assert "text/html" in html.headers["content-type"]
    assert "application/pdf" in pdf.headers["content-type"]


def test_invalid_locator_unknown_id_wrong_token_and_disabled_secret_have_identical_404(production_api, monkeypatch):
    client, _ = production_api
    item, _ = setup(client, online=False)
    valid = path(item["id"])
    anonymous()
    bad = ["/p/not-a-token", "/p/" + "x" * 70, path(uuid4()),
           f"/p/{uuid4().hex}.{valid.rsplit('.', 1)[1]}", valid[:-1] + ("A" if valid[-1] != "A" else "B"),
           "/p/../p/invalid", "/p/bad.pdf", "/p/" + "a" * 32 + "." + "b" * 31]
    responses = [client.get(url) for url in bad]
    monkeypatch.setenv("ERP_PASSPORT_SECRET", "")
    responses += [client.get(valid), client.get(valid + ".pdf")]
    assert {r.status_code for r in responses} == {404}
    assert len({r.content for r in responses}) == 1
    assert all(dict(r.headers) == dict(responses[0].headers) for r in responses)


def test_token_failure_is_rejected_before_instrument_lookup(production_api, monkeypatch):
    client, _ = production_api
    item, _ = setup(client, online=False)
    anonymous()
    def unexpected(*args, **kwargs):
        pytest.fail("An invalid passport locator reached instrument lookup")
    monkeypatch.setattr(public_api.SqlAlchemyInstrumentRepository, "get", unexpected)
    assert client.get(f"/p/{UUID(item['id']).hex}.{'x' * 32}").status_code == 404


def test_public_reads_ignore_erp_credentials_before_identity_queries(production_api, monkeypatch):
    from app.modules.identity import middleware
    client, _ = production_api
    item, _ = setup(client, online=False)
    anonymous()
    def unexpected(*args, **kwargs):
        pytest.fail("Public passport request reached identity/session resolution")
    monkeypatch.setattr(app.state, "identity_uow_factory", unexpected, raising=False)
    monkeypatch.setattr(middleware, "resolve_session", unexpected)
    monkeypatch.setattr(middleware, "resolve_api_token", unexpected)
    for headers in ({"Cookie": "__Host-erp_session=existing-session"}, {"Authorization": "Bearer invalid"}):
        assert client.get(path(item["id"]), headers=headers).status_code == 200
        assert client.get("/p/invalid", headers=headers).status_code == 404


def test_public_unsupported_methods_keep_noindex_no_store_headers(production_api):
    client, _ = production_api
    anonymous()
    response = client.post("/p/invalid")
    assert response.status_code == 405
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert response.headers["cache-control"] == "no-store"


def test_shared_rate_limit_precedes_token_and_db_work_and_does_not_trust_forwarded_headers(production_api, monkeypatch):
    client, _ = production_api
    item, _ = setup(client, online=False)
    anonymous()
    monkeypatch.setattr(public_api, "PUBLIC_PASSPORT_LIMITER", public_api.PublicPassportRateLimiter(limit=2))
    assert client.get(path(item["id"])).status_code == 200
    assert client.get(path(item["id"], ".pdf")).status_code == 200
    def unexpected(*args, **kwargs):
        pytest.fail("Rate-limited request reached token validation")
    monkeypatch.setattr(public_api, "verify_passport_token", unexpected)
    response = client.get(path(item["id"]), headers={"X-Forwarded-For": "192.0.2.50"})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"


def test_limiter_is_thread_safe_bounded_and_expires():
    limiter = public_api.PublicPassportRateLimiter(limit=7, max_clients=2)
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(lambda _: limiter.allow("client-a", now=0), range(40))) == 7
    assert limiter.allow("client-b", now=0)
    assert not limiter.allow("client-c", now=0)
    assert len(limiter._clients) == 2
    assert limiter.allow("client-c", now=60)
    assert len(limiter._clients) == 1


def test_projection_has_only_approved_fields_and_strict_known_provenance():
    header = ("! VNA serial=26030095; ACM serial=26140008\n"
              "! Temperatures VNA=26.5/27.1; ACM=25.3 degC\n"
              "! Operator=PRIVATE_OPERATOR; tool=PRIVATE_TOOL; calibration=PRIVATE_DATE\n"
              "! Chain: PRIVATE_CHAIN; deembedding=PRIVATE_METHOD\n"
              "! Raw=PRIVATE_FILE; supplier=PRIVATE_SUPPLIER\n")
    measured = characterization(measured_file(header=header))
    projection = public_passport(replace(instrument(), storage_location="PRIVATE_STORAGE", warehouse_grn="PRIVATE_GRN", doc_designation="PRIVATE_ARTICLE"),
                                 replace(rf(), source="PRIVATE_SOURCE"), measured)
    assert {field.name for field in fields(projection)} == {"inventory_number", "name", "serial_number", "manufacturer", "connectors", "impedance", "measurement", "verification"}
    assert {field.name for field in fields(projection.measurement)} == {"measured_on", "traces", "instrument", "calibration", "conditions"}
    assert "PRIVATE_" not in repr(projection)
    assert projection.measurement.instrument == "ВАЦ №26030095"
    assert projection.measurement.calibration == "АКМ №26140008"
    assert "26,5 / 27,1" in projection.measurement.conditions[0]


def test_unknown_comments_are_omitted_and_legacy_metadata_extraction_is_bounded():
    header = ("! Planar S50180 26030095, cal АКМ ACM2520 26140008 at PRIVATE_LOCATION, PRIVATE_NOTES\n"
              "! Calibration=SOLT PRIVATE_SECRET\n! VNA serial=foo; ACM serial=bar; PRIVATE_SECRET\n")
    projected = public_passport(instrument(), rf(), characterization(measured_file(header=header)))
    assert projected.measurement.instrument == "Planar S50180 №26030095"
    assert projected.measurement.calibration == "АКМ ACM2520 №26140008"
    assert "PRIVATE" not in repr(projected)


def test_html_pdf_whitelist_escape_and_public_routes_do_not_write(production_api):
    client, _ = production_api
    item, sessions = setup(client, online=False)
    identifier = UUID(item["id"])
    header = ("! VNA serial=26030095; ACM serial=26140008\n! Temperatures VNA=26.5; ACM=25.3 degC\n"
              "! Operator=PRIVATE_OPERATOR\n! Chain=PRIVATE_CHAIN\n! Internal note PRIVATE_NOTE\n")
    file = measured_file(header=header)
    with sessions.begin() as session:
        row = session.get(ProductionInstrumentRow, identifier)
        row.storage_location = "PRIVATE_STORAGE"
        row.warehouse_grn = "PRIVATE_GRN"
        row.doc_designation = "PRIVATE_ARTICLE"
        row.name = '<script>alert("name")</script> Переходник'
        session.add(ProductionInstrumentFileRow(id=uuid4(), instrument_id=identifier, kind="Прочее", file_name="Замер ВАЦ PRIVATE_FILE.s2p",
            content=file.content, uploaded_at=datetime(2026, 11, 17), uploaded_by=MASTER_ID))
    statements = []
    from sqlalchemy import event
    engine = sessions.kw["bind"]
    def track(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().split()[0].upper())
    event.listen(engine, "before_cursor_execute", track)
    try:
        anonymous()
        html = client.get(path(identifier))
        pdf = client.get(path(identifier, ".pdf"))
    finally:
        event.remove(engine, "before_cursor_execute", track)
    assert html.status_code == pdf.status_code == 200
    assert "PRIVATE_" not in html.text and "PRIVATE_" not in pdf_text(pdf.content)
    assert "Контролёр" not in html.text and "Контролёр" not in pdf_text(pdf.content)
    assert "&lt;script&gt;" in html.text and "<script>" not in html.text
    assert "26030095" in html.text and "26140008" in pdf_text(pdf.content)
    assert html.text.count('<svg ') == 4  # two charts, separate narrow/wide geometries
    assert html.text.count('viewBox="0 0 350 310"') == 2
    assert not set(statements) & {"INSERT", "UPDATE", "DELETE"}


def test_public_verification_is_included_for_measuring_items_only():
    item = replace(instrument(), kind=InstrumentKind.MEASURING, verification_number="PV-123", verified_at=datetime(2026, 11, 1), verification_due=datetime(2027, 11, 1))
    projected = public_passport(item, rf(), None)
    assert projected.verification.number == "PV-123"
    assert projected.verification.valid_until.isoformat() == "2027-11-01"
    assert public_passport(replace(item, kind=InstrumentKind.ACCESSORY), rf(), None).verification is None


def test_label_uses_public_link_and_empty_secret_falls_back_to_inventory(production_api, monkeypatch):
    client, _ = production_api
    item, sessions = setup(client, online=False)
    with sessions() as session:
        instrument = SqlAlchemyInstrumentRepository(session).get(UUID(item["id"]))
        assert instrument_labels._content(instrument, session).code == public_passport_url(instrument.id)
        monkeypatch.setenv("ERP_PASSPORT_SECRET", "")
        assert instrument_labels._content(instrument, session).code == instrument.inventory_number


def test_latest_synthetic_attachment_cannot_publish_older_or_fabricated_measurements(production_api):
    client, _ = production_api
    item, sessions = setup(client, online=False)
    identifier = UUID(item["id"])
    with sessions.begin() as session:
        for day, header in ((16, "! Valid measurement\n"), (17, "! SYNTHETIC OFFLINE DATA - NOT A MEASUREMENT\n")):
            file = measured_file(header=header)
            session.add(ProductionInstrumentFileRow(id=uuid4(), instrument_id=identifier, kind="Прочее",
                file_name=f"Замер ВАЦ {day}.11.26.s2p", content=file.content, uploaded_at=datetime(2026, 11, day), uploaded_by=MASTER_ID))
    anonymous()
    html = client.get(path(identifier))
    assert html.status_code == 200 and "Замер не проведён" in html.text
    assert '<svg ' not in html.text
    assert "Замер не проведён" in pdf_text(client.get(path(identifier, ".pdf")).content)

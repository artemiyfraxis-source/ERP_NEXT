import base64
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
from app.modules.production.instrument_public_html import PUBLIC_STYLE, public_passport_html
from app.modules.production.instrument_passport import passport_pdf, public_passport_pdf
from app.modules.production.rf_infrastructure import RfRepository
from app.modules.production.traceability import InstrumentKind, InstrumentStatus
from app.shared.http_auth import authenticated_principal
from app.shared.public_url import PublicSettings, public_passport_token, public_passport_url, public_settings, verify_passport_token
from app.workflows import instrument_labels, instrument_public_passport as public_api
from tests.production.test_api import MASTER_ID, production_api  # noqa: F401
from tests.production.test_instrument_passport import instrument, measured_file, rf
from tests.workflows.test_instrument_labels import setup
from tests.production.test_instruments_api import create

TEST_SECRET = "test-only-passport-key-do-not-deploy-123456789"


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path):
    monkeypatch.setenv("ERP_PUBLIC_BASE_URL", "https://erp.example.test")
    monkeypatch.setenv("ERP_PASSPORT_SECRET", TEST_SECRET)
    monkeypatch.setattr(public_api, "PUBLIC_PASSPORT_LIMITS", public_api.PublicPassportLimits(tmp_path / "limits"))


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


def test_link_limit_is_shared_by_html_pdf_and_independent_of_headers_and_other_instruments(production_api, monkeypatch, tmp_path):
    client, _ = production_api
    item, _ = setup(client, online=False)
    second = create(client, "SI.TEST-SECOND")
    anonymous()
    monkeypatch.setattr(public_api, "PUBLIC_PASSPORT_LIMITS", public_api.PublicPassportLimits(tmp_path, views=2))
    assert client.get(path(item["id"])).status_code == 200
    assert client.get(path(item["id"], ".pdf")).status_code == 200
    response = client.get(path(item["id"]), headers={"X-Forwarded-For": "192.0.2.50"})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert client.get(path(second["id"])).status_code == 200


def test_global_failures_never_block_valid_links(production_api, monkeypatch, tmp_path):
    client, _ = production_api
    item, _ = setup(client, online=False)
    anonymous()
    monkeypatch.setattr(public_api, "PUBLIC_PASSPORT_LIMITS", public_api.PublicPassportLimits(tmp_path, failures=3))
    bad_signature = f"/p/{UUID(item['id']).hex}.{'x' * 32}"
    for index, url in enumerate(("/p/invalid", bad_signature, path(uuid4()))):
        assert client.get(url, headers={"X-Forwarded-For": f"192.0.2.{index + 1}"}).status_code == 404
    assert client.get("/p/another-invalid", headers={"X-Forwarded-For": "192.0.2.99"}).status_code == 429
    assert client.get(path(item["id"])).status_code == 200
    assert client.get(path(item["id"], ".pdf")).status_code == 200
    assert client.get(path(uuid4())).status_code == 429


def test_full_pdf_pool_returns_503_without_charging_view_or_failure_budget(production_api, monkeypatch, tmp_path):
    client, _ = production_api
    item, _ = setup(client, online=False)
    anonymous()
    limits = public_api.PublicPassportLimits(tmp_path, views=2, failures=1)
    monkeypatch.setattr(public_api, "PUBLIC_PASSPORT_LIMITS", limits)
    with limits.pdf_slot() as first, limits.pdf_slot() as second:
        assert first and second
        response = client.get(path(item["id"], ".pdf"))
        assert response.status_code == 503 and response.headers["retry-after"] == "1"
        assert response.headers["cache-control"] == "no-store"
        assert client.get(path(item["id"])).status_code == 200
    assert client.get(path(item["id"], ".pdf")).status_code == 200
    assert client.get("/p/invalid").status_code == 404


def test_render_failure_releases_slot_and_refunds_view(production_api, monkeypatch, tmp_path):
    client, _ = production_api
    item, _ = setup(client, online=False)
    anonymous()
    limits = public_api.PublicPassportLimits(tmp_path, views=1, pdf_slots=1)
    monkeypatch.setattr(public_api, "PUBLIC_PASSPORT_LIMITS", limits)
    render = public_api.public_passport_pdf
    def broken(*args, **kwargs):
        raise RuntimeError("render failed")
    monkeypatch.setattr(public_api, "public_passport_pdf", broken)
    with pytest.raises(RuntimeError, match="render failed"):
        client.get(path(item["id"], ".pdf"))
    monkeypatch.setattr(public_api, "public_passport_pdf", render)
    assert client.get(path(item["id"], ".pdf")).status_code == 200
    assert client.get(path(item["id"])).status_code == 429


def test_unavailable_shared_state_returns_503_without_unlimited_fallback(production_api, monkeypatch, tmp_path):
    client, _ = production_api
    item, _ = setup(client, online=False)
    anonymous()
    limits = public_api.PublicPassportLimits(tmp_path)
    monkeypatch.setattr(public_api, "PUBLIC_PASSPORT_LIMITS", limits)
    (tmp_path / "counters.json").write_text("invalid state", encoding="utf-8")
    for url in (path(item["id"]), "/p/invalid"):
        response = client.get(url)
        assert response.status_code == 503 and response.headers["retry-after"] == "1"


def test_projection_has_only_approved_fields_and_strict_known_provenance():
    header = ("! VNA serial=26030095; ACM serial=26140008\n"
              "! Temperatures VNA=26.5/27.1; ACM=25.3 degC\n"
              "! Operator=PRIVATE_OPERATOR; tool=PRIVATE_TOOL; calibration=PRIVATE_DATE\n"
              "! Chain: PRIVATE_CHAIN; deembedding=PRIVATE_METHOD\n"
              "! Raw=PRIVATE_FILE; supplier=PRIVATE_SUPPLIER\n")
    measured = characterization(measured_file(header=header))
    projection = public_passport(replace(instrument(), storage_location="PRIVATE_STORAGE", warehouse_grn="PRIVATE_GRN", doc_designation="PRIVATE_ARTICLE"),
                                 replace(rf(), source="PRIVATE_SOURCE"), measured)
    assert {field.name for field in fields(projection)} == {"inventory_number", "name", "serial_number", "manufacturer", "connectors", "impedance", "impedance_warning", "measurement", "verification", "status", "next_control", "control_overdue", "history", "spec", "norm_check", "usage", "uncertainty"}
    assert {field.name for field in fields(projection.measurement)} == {"measured_on", "traces", "instrument", "calibration", "conditions", "chain"}
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
        download = client.get(path(identifier, "/measurement.s2p"))
    finally:
        event.remove(engine, "before_cursor_execute", track)
    assert html.status_code == pdf.status_code == download.status_code == 200
    assert "PRIVATE_" not in html.text and "PRIVATE_" not in pdf_text(pdf.content)
    assert "PRIVATE_" not in download.text
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


@pytest.mark.parametrize("name", ["Замер ВАЦ, СИ.009.s2p", "Замер ВАЦ СИ.009.s2p"])
@pytest.mark.parametrize("same_time", [False, True])
def test_labels_passports_and_planner_select_same_latest_named_upload(production_api, name, same_time):
    client, _ = production_api
    item, sessions = setup(client, online=False)
    identifier = UUID(item["id"])
    newest_id = UUID(int=2)
    with sessions.begin() as session:
        for file_id, filename, uploaded, header in (
            (UUID(int=1), "Замер ВАЦ 16.11.26.s2p", 17 if same_time else 16, "! captured=2026-11-16T12:00:00Z\n"),
            (newest_id, name, 17, "! captured=2026-11-15T12:00:00Z\n"),
            (UUID(int=3), "datasheet.s2p", 18, "! captured=2026-11-18T12:00:00Z\n"),
        ):
            session.add(ProductionInstrumentFileRow(id=file_id, instrument_id=identifier, kind="Прочее",
                file_name=filename, content=measured_file(header=header).content,
                uploaded_at=datetime(2026, 11, uploaded), uploaded_by=MASTER_ID))
    with sessions() as session:
        stored = SqlAlchemyInstrumentRepository(session).get(identifier)
        assert instrument_labels._content(stored, session).value("measurement_date") == "Замер 15.11.26"
        assert [(item_id, file_id) for item_id, file_id, _ in RfRepository(session).characterization_files([identifier])] == [(identifier, newest_id)]
    internal = client.get(f"/api/production/instruments/{identifier}/passport.pdf")
    assert internal.status_code == 200
    assert "Дата: 15.11.2026" in pdf_text(internal.content)
    anonymous()
    assert "15.11.2026" in client.get(path(identifier)).text
    assert "Дата замера: 15.11.2026" in pdf_text(client.get(path(identifier, ".pdf")).content)


@pytest.mark.parametrize("ohms", [50, 75])
@pytest.mark.parametrize("measured", [False, True])
def test_public_html_pdf_show_measurement_reference_and_card_impedance_warning(ohms, measured):
    item, details = instrument(), rf(ohms)
    measurement = characterization(measured_file()) if measured else None
    projected = public_passport(item, details, measurement)
    html = public_passport_html(projected, pdf_path="/p/example.pdf")
    pdf = pdf_text(public_passport_pdf(projected, generated=datetime(2026, 11, 17)))
    internal = pdf_text(passport_pdf(item, details, measurement, generated=datetime(2026, 11, 17)))
    reference = "Опорное сопротивление измерения"
    assert (reference in html) == measured
    assert (reference + ": 50 Ом" in pdf) == measured
    if measured:
        assert f"<dt>{reference}</dt><dd>50 Ом</dd>" in html
    warning = "75 Ом — не для 50 Ом тракта"
    assert (warning in html) == (warning in pdf) == (warning in internal) == (ohms == 75)


def test_public_conclusion_history_dates_and_hidden_blanks():
    from datetime import date
    from app.modules.production.rf_interfaces import RfDetails
    from app.modules.production.instrument_public import history_entry
    from tests.production.test_instrument_working_band import summary

    measured = replace(summary(), comments=())
    item = replace(instrument(), manufacturer="", status=InstrumentStatus.INACTIVE)
    projected = public_passport(item, RfDetails(), measured,
        history=(history_entry(item, RfDetails(), replace(measured, measured_on=date(2025, 11, 10))),), today=date(2027, 11, 17))
    html = public_passport_html(projected, pdf_path="/p/example.pdf")
    pdf = pdf_text(public_passport_pdf(projected, generated=datetime(2027, 11, 17), public_url="https://erp.example.test/p/example"))
    assert projected.name == "Переход коаксиальный SMA розетка – SMA розетка, S-322"
    for text in (html, " ".join(pdf.split())):
        for expected in ("Заключение (контроль характеристик, не поверка)",
                         "Не соответствует норме", "MIL-PRF-55339/55", "КСВН ≤ 1,05 + 0,005·F в полосе 0,5–27 ГГц",
                         "КСВН 1,2 на 11,8 ГГц при норме ≤ 1,109", "КСВН ≤ 1,30 (требование цеха) — до 18 ГГц",
                         "Погрешность измерения КСВН",
                         "не определена — модель ВАЦ не указана", "Правило принятия решения", "ILAC-G8", "СТО ФРАК-002-2026", "Методика измерений КСВН",
                         "Результаты относятся только к данному изделию.", "Утвердил: Генеральный директор",
                         "Следующий контроль: не позднее 16.11.2027 (через 12 мес. или 100 сочленений — что раньше)",
                         "Требуется повторный замер", "Не используется", "История контроля", "10.11.2025",
                         "не соответствует норме; применение: КСВН ≤ 1,30 (требование цеха) — до 18 ГГц",
                         "Рабочие температуры", "−65…+165 °C — MIL-PRF-55339/55", "Скачать .s2p"):
            assert expected in text
        assert "Не указан" not in text
        assert "Изготовитель" not in text
        assert "Условия" not in text
    assert projected.impedance == "50 Ом"
    failed = public_passport(item, rf(), summary(left=(1.31,) * 4))
    for text in (public_passport_html(failed, pdf_path="/p/example.pdf"),
                 pdf_text(public_passport_pdf(failed, generated=datetime(2026, 11, 17)))):
        assert "Не соответствует норме" in text and "КСВН больше требования цеха уже на первой ступени" in text


@pytest.mark.parametrize("source,maker", [("https://gsm-repiteri.ru/catalog/s-322", "gsm-repiteri"),
    ("https://planar.chel.ru/catalog", "Планар"), ("Planar", "Планар"),
    ("PRIVATE_SOURCE", ""), ("https://planar.chel.ru.evil.test/private", "")])
def test_manufacturer_source_publishes_only_known_label(source, maker):
    projected = public_passport(replace(instrument(), manufacturer=""), replace(rf(), source=source), None)
    assert projected.manufacturer == maker
    assert source not in repr(projected) or source == maker


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1251", "utf-16", "utf-16-be"])
def test_s2p_download_sanitizes_all_comments_and_preserves_original_data_bytes(encoding):
    from app.modules.production.instrument_public import public_measurement_download
    from app.modules.production.vna_files import _decode
    header = ("! VNA serial=26030095; ACM serial=26140008\r\n"
              "! Temperatures VNA=26.5/27.1; ACM=25.3 degC\r\n"
              "! Chain: слева СИ.009, справа СИ.008; PRIVATE_PATH C:\\Users\\PRIVATE_USER\\file.s2p\r\n"
              "! PRIVATE_OPERATOR\r\n")
    data = "# GHz S MA R 50\r\n0.01 .01 0 .99 0 .99 0 .02 0\r\n18 .02 0 .98 0 .98 0 .025 0\r\n"
    original = (header + data).encode(encoding)
    if encoding == "utf-16-be":
        original = b"\xfe\xff" + original
    file = replace(measured_file(), content=original)
    measured = public_passport(instrument(), rf(), characterization(file)).measurement
    result = public_measurement_download(original, measured)
    decoded = _decode(result)
    assert "PRIVATE" not in decoded
    assert "ВАЦ №26030095" in decoded and "АКМ №26140008" in decoded
    assert "слева СИ.009, справа СИ.008" in decoded and "26,5 / 27,1" in decoded
    expected_bytes = data.encode(encoding.removesuffix("-sig"))
    if encoding == "utf-16":
        expected_bytes = data.encode("utf-16-le")
    assert result.endswith(expected_bytes)
    assert characterization(replace(file, content=result)).traces == characterization(file).traces


def test_s2p_inline_comments_are_removed():
    from app.modules.production.instrument_public import public_measurement_download
    file = measured_file(header="! VNA serial=123; ACM serial=456\n")
    file = replace(file, content=file.content.replace(b"0.025 0\n", b"0.025 0 ! PRIVATE_INLINE\n"))
    projected = public_passport(instrument(), rf(), characterization(file))
    result = public_measurement_download(file.content, projected.measurement)
    assert b"PRIVATE" not in result
    assert result.endswith(b"0.025 0 \n")


def test_download_route_latest_file_history_pdf_link_and_shared_limits(production_api, monkeypatch, tmp_path):
    client, _ = production_api
    item, sessions = setup(client, online=False)
    identifier = UUID(item["id"])
    header = "! VNA serial=123; ACM serial=456\n! PRIVATE_OPERATOR\n"
    with sessions.begin() as session:
        for file_id, day, stop, name in ((UUID(int=1), 15, 6, "Замер ВАЦ 15.11.26.s2p"),
            (UUID(int=2), 16, 11, "Замер ВАЦ 16.11.26.s2p"), (UUID(int=3), 17, 18, "datasheet.s2p")):
            session.add(ProductionInstrumentFileRow(id=file_id, instrument_id=identifier, kind="Прочее", file_name=name,
                content=measured_file(stop=stop, header=header).content, uploaded_at=datetime(2026, 11, day), uploaded_by=MASTER_ID))
    anonymous()
    url = path(identifier)
    html = client.get(url)
    pdf = client.get(url + ".pdf")
    download = client.get(url + "/measurement.s2p")
    assert html.status_code == pdf.status_code == download.status_code == 200
    assert 'href="' + url + '/measurement.s2p"' in html.text
    assert "История контроля" in html.text and "15.11.2026" in html.text and "0,01–6" in html.text
    assert "PRIVATE" not in download.text and "11 0.02" in download.text and "18 0.02" not in download.text
    assert download.headers["content-disposition"] == 'attachment; filename="measurement.s2p"'
    assert download.headers["cache-control"] == "no-store"
    reader = PdfReader(BytesIO(pdf.content))
    links = [annotation.get_object().get("/A", {}).get("/URI", "") for page in reader.pages for annotation in page.get("/Annots", [])]
    assert public_passport_url(identifier) + "/measurement.s2p" in links
    monkeypatch.setattr(public_api, "PUBLIC_PASSPORT_LIMITS", public_api.PublicPassportLimits(tmp_path, views=2))
    assert client.get(url + "/measurement.s2p").status_code == 200
    assert client.get(url).status_code == 200
    assert client.get(url + ".pdf").status_code == 429


def test_download_unavailable_without_measurement_and_never_revives_older_file(production_api):
    client, _ = production_api
    item, sessions = setup(client, online=False)
    identifier = UUID(item["id"])
    anonymous()
    url = path(identifier)
    assert client.get(url + "/measurement.s2p").status_code == 404
    assert "Скачать .s2p" not in client.get(url).text
    with sessions.begin() as session:
        for day, payload in ((16, measured_file().content), (17, b"corrupt newest")):
            session.add(ProductionInstrumentFileRow(id=uuid4(), instrument_id=identifier, kind="Прочее",
                file_name=f"Замер ВАЦ {day}.11.26.s2p", content=payload, uploaded_at=datetime(2026, 11, day), uploaded_by=MASTER_ID))
    html = client.get(url)
    assert "Замер не проведён" in html.text and "История контроля" in html.text
    assert "Скачать .s2p" not in html.text
    assert client.get(url + "/measurement.s2p").status_code == 404
    wrong = url[:-1] + ("A" if url[-1] != "A" else "B")
    assert client.get(wrong + "/measurement.s2p").status_code == 404


def test_label_workflow_reads_the_shared_working_band(production_api):
    client, _ = production_api
    item, sessions = setup(client, online=False)
    identifier = UUID(item["id"])
    data = (b"# GHz S MA R 50\n"
            b".01 .01 0 .99 0 .99 0 .02 0\n"
            b"11.8 .08 0 .98 0 .98 0 .02 0\n"
            b"12 .2 0 .1 0 .1 0 .02 0\n"
            b"18 .01 0 .01 0 .01 0 .02 0\n")
    with sessions.begin() as session:
        session.add(ProductionInstrumentFileRow(id=uuid4(), instrument_id=identifier, kind="Прочее",
            file_name="Замер ВАЦ 16.11.26.s2p", content=data, uploaded_at=datetime(2026, 11, 16), uploaded_by=MASTER_ID))
    with sessions() as session:
        stored = SqlAlchemyInstrumentRepository(session).get(identifier)
        content = instrument_labels._content(stored, session)
        assert content.value("range_line").startswith("Частотный диапазон 0–")
        assert content.value("loss_line").startswith("КСВН ≤ 1,")

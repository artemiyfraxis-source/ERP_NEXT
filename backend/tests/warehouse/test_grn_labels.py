"""GRN-D category defaults, exact raster payloads, editing and immutable job provenance."""

from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import select, update

from app.main import app
from app.modules.warehouse.label_jobs import LabelJobRow, claim
from app.modules.warehouse.label_templates import LabelTemplateRow
from app.modules.warehouse.infrastructure import StockLotRow
from app.modules.warehouse.labels.defaults import GRN_DESIGN_REVISIONS, default_template, grn_template
from app.modules.warehouse.labels.layout import GrnLabelData, js_round
from app.modules.warehouse.labels.render import render
from app.modules.warehouse.labels.settings import PrintSettings
from app.modules.warehouse.labels.template import FORMATS, check_template, lot_content
from app.modules.warehouse.labels_application import SAMPLE, print_label, sample_content, with_form
from tests.warehouse.grn_raster_decoder import decode_code128, decode_qr
from tests.warehouse.test_receipts import NOW, _accept, _receipt, stand  # noqa: F401


@pytest.mark.parametrize("category", GRN_DESIGN_REVISIONS)
@pytest.mark.parametrize("label_format", FORMATS)
@pytest.mark.parametrize("dpi", (203, 300))
@pytest.mark.parametrize("kind", ("lot", "intake"))
def test_seven_designs_render_and_both_raster_codes_decode(category, label_format, dpi, kind):
    width, height = FORMATS[label_format]
    settings = PrintSettings(width_mm=width, height_mm=height, dpi=dpi)
    template = check_template(grn_template(category, kind, label_format), kind, label_format)
    content = with_form(sample_content(kind, category), None)
    layout = render(template, content, settings)
    assert not layout.skipped and not layout.overflow
    assert layout.image.mode == "1"
    assert content.code in layout.texts
    assert "05364575" in " ".join(layout.texts)
    assert "22.11.2027" in " ".join(layout.texts)
    assert content.value("quantity") in " ".join(layout.texts)
    for element in template.elements:
        if element.kind not in ("qr", "code128"):
            continue
        scale = dpi / 25.4
        crop = layout.image.convert("L").crop(tuple(js_round(value * scale) for value in
            (element.x_mm, element.y_mm, element.x_mm + element.w_mm, element.y_mm + element.h_mm)))
        decoded = decode_qr(crop, layout.qr_modules, layout.qr_module_dots) if element.kind == "qr" else decode_code128(crop)
        assert decoded == content.code


@pytest.mark.parametrize("category", GRN_DESIGN_REVISIONS)
def test_package_suffix_roundtrips_without_normalizing_grn(category):
    content = sample_content("lot", category)
    content = replace(content, code=content.code + "-02", values=dict(content.values, grn=content.code + "-02"))
    template = grn_template(category, "lot", "100x50")
    settings = PrintSettings(width_mm=100, height_mm=50)
    layout = render(template, content, settings)
    for e in template.elements:
        if e.kind not in ("qr", "code128"):
            continue
        crop = layout.image.convert("L").crop(tuple(js_round(v * 300 / 25.4) for v in
            (e.x_mm, e.y_mm, e.x_mm + e.w_mm, e.y_mm + e.h_mm)))
        assert (decode_qr(crop, layout.qr_modules, layout.qr_module_dots) if e.kind == "qr" else decode_code128(crop)) == content.code


@pytest.mark.parametrize("state,expected", [(None, "ВК: неизвестно"), ("IQH", "КАРАНТИН"), ("QTN", "КАРАНТИН"),
    ("REJECTED", "НЕ ГОДЕН"), ("RELEASED", "ГОДЕН ✓"), ("NOT_REQUIRED", "ВК не требуется")])
def test_expiry_and_quality_are_stored_facts_not_guesses(state, expected):
    content = lot_content(replace(SAMPLE, category="PPM", quality_state=state, expiry_date=None))
    assert content.value("expiry") == "Годен до: не ограничен"  # no shelf life: not a missing fact
    assert lot_content(replace(SAMPLE, category="PPM", quality_state=state, expiry_date=None, expiry_required=True)
                       ).value("expiry") == "Годен до: неизвестно"
    assert content.value("quality") == expected
    content = lot_content(replace(SAMPLE, category="PPM", expiry_date=date(2027, 11, 22)))
    assert content.value("expiry") == "Годен до: 22.11.2027"


def test_missing_codes_or_required_fields_and_overlong_data_fail_actionably():
    content = sample_content("lot", "PPM")
    template = grn_template("PPM", "lot", "100x50")
    with pytest.raises(ValueError, match="QR и Code128"):
        render(template.model_copy(update={"elements": [e for e in template.elements if e.kind != "code128"]}),
               content, PrintSettings(width_mm=100, height_mm=50))
    with pytest.raises(ValueError, match="обязательные поля"):
        render(template.model_copy(update={"elements": [e for e in template.elements if e.field != "expiry"]}),
               content, PrintSettings(width_mm=100, height_mm=50))
    with pytest.raises(ValueError, match="100×50"):
        render(template, replace(content, values=dict(content.values, name="Длинное русское наименование " * 30)),
               PrintSettings(width_mm=100, height_mm=50))
    with pytest.raises(ValueError, match="перекрывает"):
        render(template.model_copy(update={"elements": [e.model_copy(update={"y_mm": 5}) if e.kind == "code128" else e
                                                         for e in template.elements]}),
               content, PrintSettings(width_mm=100, height_mm=50))


def scope(category="PPM", printer="TSC", **extra):
    return {"category": category, "printer_name": printer, **extra}


def test_per_printer_edit_preview_reset_and_version_conflicts(stand):
    client, _ = stand
    url = "/api/stock/labels/templates/lot/100x50"
    templates = client.get("/api/stock/labels/grn-templates", params={"printer_name": "TSC"}).json()
    assert len(templates["categories"]) == 7
    original = client.get("/api/stock/labels/templates/lot", params=scope()).json()["formats"]["100x50"]
    assert original["is_default"] and original["form"] is None
    form = {"form_id": "05.11", "form_revision": "3", "form_effective_date": "2026-11-22"}
    body = {"template": original["template"], "version": None, "form": form}
    saved = client.put(url, params=scope(), json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1 and saved.json()["form"] == form
    assert client.put(url, params=scope(), json=body).status_code == 409
    other = client.get("/api/stock/labels/templates/lot", params=scope(printer="TSC-2")).json()["formats"]["100x50"]
    assert other["is_default"] and other["form"] is None and other["version"] == 1
    png = client.post(url + "/preview", params=scope(), json=body)
    assert png.status_code == 200 and png.headers["content-type"] == "image/png", png.text
    assert client.delete(url, params=scope(version=0)).status_code == 409
    reset = client.delete(url, params=scope(version=1))
    assert reset.status_code == 200 and reset.json()["is_default"] and reset.json()["version"] == 2
    assert client.put(url, params=scope(), json={**body, "version": 1}).status_code == 409
    assert client.put(url, params=scope(), json={**body, "version": 2}).status_code == 200
    # The R&D ampersand is an ordinary query value, never a separator or a renamed prefix.
    assert client.get("/api/stock/labels/templates/lot", params=scope("R&D")).status_code == 200
    assert client.get("/api/stock/labels/templates/instrument", params=scope()).status_code == 422


def test_category_test_job_is_pinned_and_snapshotted(stand):
    client, sessions = stand
    result = client.post("/api/stock/labels/templates/lot/100x50/test", params=scope("R&D"))
    assert result.status_code == 200, result.text
    with sessions() as session:
        job = session.get(LabelJobRow, UUID(result.json()["job_id"]))
        assert job.grn_category == "R&D" and job.target_printer == "TSC"
        assert job.template_version == "builtin:grn-r&d-1"
        assert job.grn_provenance["code"] == "R&D.26.000002"
    factory = app.state.warehouse_uow_factory
    assert claim(factory(), printer="OTHER", host="pc", now=NOW) is None
    assert claim(factory(), printer="TSC", host="pc", now=NOW) is not None


def test_receipt_auto_print_and_reprint_snapshot_current_quality(stand):
    client, sessions = stand
    receipt = _receipt(client)
    response = _accept(client, receipt["id"], expiry_date="2027-11-22", series="05364575")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["label_job_id"], body
    with sessions() as session:
        job = session.get(LabelJobRow, UUID(body["label_job_id"]))
        assert job.grn_id and job.grn_category == "PPM"
        assert job.grn_provenance["quality_state"] == "IQH"
        assert job.grn_provenance["expiry_date"] == "2027-11-22"
        assert job.grn_provenance["source_kind"] == "warehouse_receipt"
        assert job.grn_provenance["values"]["received"] == "поступило 23.10.2026"
        original_bytes = bytes(job.tspl)
    with sessions.begin() as session:
        # Simulate a subsequent recorded QC decision; old bytes must remain an IQH snapshot.
        session.execute(update(StockLotRow).where(StockLotRow.id == UUID(body["lot"]["id"]))
                        .values(quality_state="QTN", quality_basis="Повторный контроль"))
    printed = client.post(f"/api/stock/batches/{body['lot']['id']}/label")
    assert printed.status_code == 200, printed.text
    with sessions() as session:
        assert len(list(session.scalars(select(LabelJobRow)))) == 2
        assert bytes(session.get(LabelJobRow, UUID(body["label_job_id"])).tspl) == original_bytes
        reprint = session.get(LabelJobRow, UUID(printed.json()["job_id"]))
        assert reprint.grn_provenance["quality_state"] == "QTN"
        assert reprint.grn_provenance["quality_basis"] == "Повторный контроль"


def test_form_is_separate_from_template_and_copied_to_job(stand):
    client, sessions = stand
    params = scope("PPM", printer=" TSC ")
    url = "/api/stock/labels/templates/lot/100x50"
    template = client.get(url, params=params).json()["template"]
    form = {"form_id": "05.11", "form_revision": "3", "form_effective_date": "2026-11-22"}
    saved = client.put(url, params=params, json={"template": template, "form": form, "version": None})
    assert saved.status_code == 200, saved.text
    printed = client.post(url + "/test", params=params)
    assert printed.status_code == 200, printed.text
    with sessions() as session:
        job = session.get(LabelJobRow, UUID(printed.json()["job_id"]))
        assert job.target_printer == "TSC"
        assert (job.form_id, job.form_revision, job.form_effective_date) == ("05.11", "3", date(2026, 11, 22))
        assert job.template_version == "db:1"
        assert job.grn_provenance["values"]["form_footer"] == "Форма 05.11 Rev.3 22.11.2026"
        assert job.grn_provenance["template"] == template
    assert client.put(url, params=params, json={"template": template, "version": 1, "form": {"form_id": "05.11"}}).status_code == 422


def test_invalid_category_template_never_silently_falls_back(stand):
    client, sessions = stand
    url = "/api/stock/labels/templates/lot/100x50"
    template = client.get(url, params=scope()).json()["template"]
    assert client.put(url, params=scope(), json={"template": template, "version": None}).status_code == 200
    with sessions.begin() as session:
        session.execute(update(LabelTemplateRow).where(LabelTemplateRow.category == "PPM")
                        .values(template={"profiles": {"TSC": {"template": {}, "form": None}}}))
    assert client.get(url, params=scope()).status_code == 422
    assert client.get("/api/stock/labels/grn-templates", params={"printer_name": "TSC"}).status_code == 422
    assert client.post(url + "/test", params=scope()).status_code == 422
    assert client.delete(url, params=scope(version=1)).status_code == 200


def test_instrument_passport_url_is_not_written_into_grn_number(stand):
    _, sessions = stand
    content = replace(sample_content("instrument"), code="https://erp.example.test/instruments/passport/034")
    result = print_label(app.state.warehouse_uow_factory(), content, PrintSettings(width_mm=100, height_mm=50),
                         1, NOW, kind="instrument", lot_id=None, created_by="ОТК")
    with sessions() as session:
        job = session.get(LabelJobRow, UUID(result.job_id))
        assert job.grn_number is None and job.grn_id is None
        assert job.grn_provenance["code"] == content.code


def test_legacy_custom_templates_are_isolated_from_categories(stand):
    client, sessions = stand
    legacy = default_template("lot", "58x40", "qr").model_dump(mode="json")
    saved = client.put("/api/stock/labels/templates/lot/58x40", json={"template": legacy, "version": None})
    assert saved.status_code == 200
    assert client.get("/api/stock/labels/templates/lot", params=scope()).json()["formats"]["58x40"]["is_default"]
    with sessions() as session:
        row = session.scalar(select(LabelTemplateRow).where(LabelTemplateRow.category == ""))
        assert row.template == legacy
    assert not lot_content(GrnLabelData("OLD.26.0000001", "", "", "1", "шт", "", None)).category

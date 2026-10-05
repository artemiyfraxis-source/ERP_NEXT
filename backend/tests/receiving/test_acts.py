"""RC-9: акты приёмки — № по дню, выпуск по вердикту, печатный бланк."""

from dataclasses import replace
from datetime import date, timedelta

from app.modules.receiving.application import (
    InspectReceivingCommand,
    build_act_print_sheet,
    consume_receiving_expected,
    inspect_receiving,
)
from app.modules.receiving.domain import next_act_number
from tests.receiving.test_supplier_return import (  # noqa: F401 — fixtures
    NOW,
    RECEIVER,
    api,
    erp,
    expected_message,
    inspection,
)


def run(receiving, line, data):
    return inspect_receiving(
        InspectReceivingCommand(line_id=line.id, expected_version=line.version, inspection=data, actor=RECEIVER, now=NOW),
        receiving(),
    )


def test_act_number_continues_the_days_sequence():
    day = date(2026, 9, 21)
    assert next_act_number("incoming", day, []) == "ВК-20260921-01"
    assert next_act_number("incoming", day, ["ВК-20260921-01", "ВК-20260921-07", "ВК-20260920-09"]) == "ВК-20260921-08"


def test_clean_delivery_gets_only_the_incoming_act(erp):
    receiving, _, _ = erp
    first = run(receiving, consume_receiving_expected(expected_message(1), receiving()), inspection())
    second = run(receiving, consume_receiving_expected(expected_message(2), receiving()), inspection())

    assert (first.vk_act_number, first.vk_act_date) == ("ВК-20260921-01", NOW.date())
    assert second.vk_act_number == "ВК-20260921-02"
    assert not first.nc_act_number and not first.complaint_act_number and not first.use_permit_number


def test_shortage_is_accepted_with_nonconformity_and_complaint_acts(erp):
    # Owner, 05.10.2026: недостача no longer holds the 8 that came — «Принято» by fact, АН and РК issued.
    receiving, _, _ = erp
    line = consume_receiving_expected(expected_message(), receiving())
    short = run(receiving, line, inspection("8"))

    assert short.status.value == "Принято"
    assert short.accepted_quantity == 8
    assert short.nc_act_number == "АН-20260921-01"
    assert short.complaint_act_number == "РК-20260921-01"
    assert not short.use_permit_number


def test_release_from_waiting_issues_use_permit(erp):
    receiving, _, _ = erp
    line = consume_receiving_expected(expected_message(), receiving())
    # A defect holds the line (a missing УПД or a shortfall no longer does, owner 05.10.2026).
    waiting = run(receiving, line, replace(inspection(), manual_nonconformities=("Механическое повреждение / брак",)))
    assert waiting.status.value == "Ожидание"

    # Брак не подтвердился: повторный контроль без несоответствий — выход из «Ожидания» в «Принято» с РИ.
    released = run(receiving, waiting, inspection())
    assert released.status.value == "Принято"
    assert released.use_permit_number == "РИ-20260921-01"
    assert released.vk_act_number == waiting.vk_act_number


def test_historical_receipt_dates_acts_by_the_upd(erp):
    receiving, _, _ = erp
    upd_day = NOW.date() - timedelta(days=200)
    line = run(
        receiving,
        consume_receiving_expected(expected_message(), receiving()),
        replace(inspection(), verdict_date=upd_day, expiry_date=None),
    )
    assert line.vk_act_number == f"ВК-{upd_day.strftime('%Y%m%d')}-01"
    assert line.vk_act_date == upd_day


def test_incoming_act_sheet_marks_results_from_the_nature(erp):
    receiving, _, _ = erp
    line = run(receiving, consume_receiving_expected(expected_message(), receiving()), inspection("8"))

    sheet = build_act_print_sheet(line, "incoming")

    assert sheet.title == "АКТ ВХОДНОГО КОНТРОЛЯ № ВК-20260921-01"
    results = {row[1]: row[2] for row in sheet.rows if row[0]}
    assert results["Количество фактическое — соответствие УПД/М-15"] == "Не соответствует"
    assert results["Внешний вид, целостность тары и упаковки"] == "Соответствует"
    assert results["Документ по качеству"] == "Не требуется"


def test_incoming_act_notes_upd_pending_until_it_is_attached(erp):
    receiving, _, _ = erp
    line = run(
        receiving,
        consume_receiving_expected(expected_message(), receiving()),
        replace(inspection(), has_shipping_document=False, shipping_document_url=None),
    )
    assert line.status.value == "Принято"

    results = {row[1]: row[2] for row in build_act_print_sheet(line, "incoming").rows if row[0]}
    assert results["УПД/М-15"] == "УПД не приложен — придёт по ЭДО"

    attached = line.attach_shipping_document(url=None, number="ЭДО-1", document_date=NOW.date())
    results = {row[1]: row[2] for row in build_act_print_sheet(attached, "incoming").rows if row[0]}
    assert results["УПД/М-15"] == "Соответствует"


def test_api_lists_acts_and_prints_one(api):
    client, receiving, _ = api
    line = run(receiving, consume_receiving_expected(expected_message(), receiving()), inspection())

    listed = client.get("/api/receiving").json()["lines"][0]
    assert listed["acts"] == [{"kind": "incoming", "title": "Акт входного контроля", "number": "ВК-20260921-01", "date": "2026-09-21"}]

    printed = client.get(f"/api/receiving/{line.id}/acts/incoming")
    assert printed.status_code == 200, printed.text
    assert printed.json()["sheet"]["title"].endswith("ВК-20260921-01")
    assert client.get(f"/api/receiving/{line.id}/acts/complaint").status_code == 422
    assert client.get(f"/api/receiving/{line.id}/acts/unknown").status_code == 404



def test_incoming_act_carries_manufacturer_country_and_quality_document(erp):
    receiving, _, _ = erp
    message = expected_message()
    message = replace(
        message,
        payload={
            **message.payload,
            "manufacturer": "АО Разъём",
            "country": "Россия",
            "quality_document_type": "Паспорт",
            "quality_document_number": "П-17",
            "quality_document_date": "2026-09-01",
            "standard": "ГОСТ 23784-98",
        },
    )
    line = consume_receiving_expected(message, receiving())
    assert (line.manufacturer, line.country, line.quality_document_date) == ("АО Разъём", "Россия", date(2026, 9, 1))

    # Приёмщик уточняет № документа качества; незаполненное в форме остаётся из заказа.
    line = run(receiving, line, replace(inspection(), quality_document_number="П-18"))

    rows = {row[1]: row[2] for row in build_act_print_sheet(line, "incoming").rows}
    assert rows["Производитель"] == "АО Разъём"
    assert rows["Страна"] == "Россия"
    assert rows["Тип документа по качеству"] == "Паспорт"
    assert rows["№ документа по качеству"] == "П-18"
    assert rows["Дата документа по качеству"] == "01.09.2026"
    assert rows["Стандарт"] == "ГОСТ 23784-98"


def test_api_takes_quality_details_from_the_inspection_form(api):
    client, receiving, _ = api
    line = consume_receiving_expected(expected_message(), receiving())

    response = client.post(
        f"/api/receiving/{line.id}/inspect",
        json={
            "version": line.version,
            "fact_quantity": "10",
            "document_quantity": "10",
            "shipping_document_url": "https://files.test/upd.pdf",
            "expiry_date": "2027-01-01",
            "manufacturer": "АО Разъём",
            "country": "Китай",
            "quality_document_number": "С-5",
            "quality_document_date": "2026-08-30",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()["line"]
    assert (body["manufacturer"], body["country"], body["quality_document_number"]) == ("АО Разъём", "Китай", "С-5")
    assert body["quality_document_date"] == "2026-08-30"


def test_stock_name_comes_from_the_upd_name(erp):
    from app.modules.receiving.application import _warehouse_payload

    receiving, _, _ = erp
    line = consume_receiving_expected(expected_message(), receiving())
    assert _warehouse_payload(line)["name"] == "Разъём 1"
    assert _warehouse_payload(replace(line, document_name="Вилка 2РМ14"))["name"] == "Вилка 2РМ14"

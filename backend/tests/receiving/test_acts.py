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


def test_shortage_issues_nonconformity_and_complaint_then_release_issues_use_permit(erp):
    receiving, _, _ = erp
    line = consume_receiving_expected(expected_message(), receiving())
    short = run(receiving, line, inspection("8"))

    assert short.status.value == "Ожидание"
    assert short.nc_act_number == "АН-20260921-01"
    assert short.complaint_act_number == "РК-20260921-01"

    # Довезли: повторный контроль без несоответствий — выход из «Ожидания» в «Принято» с РИ.
    released = run(receiving, short, inspection())
    assert released.status.value == "Принято"
    assert released.use_permit_number == "РИ-20260921-01"
    assert released.vk_act_number == short.vk_act_number
    assert released.nc_act_number == short.nc_act_number


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


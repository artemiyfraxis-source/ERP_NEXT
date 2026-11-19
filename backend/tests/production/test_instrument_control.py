"""Next control of a measured instrument: the earlier of 12 months or 100 matings since the last measurement
(СТО ФРАК-002-2026 п. 15.3) — on the passport, the card (matings API) and the label."""

import base64
from dataclasses import replace
from datetime import date, datetime
from io import BytesIO
from uuid import UUID

from pypdf import PdfReader

from app.main import app
from app.modules.production.instrument_control import control_due, matings_word, next_control_date
from app.modules.production.instrument_public import control_line, public_passport
from app.shared.clock import MOSCOW, moscow_now
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_instrument_matings import create, post
from tests.production.test_instrument_passport import instrument, measured_file, rf
from tests.production.test_instrument_working_band import summary

MEASURED = date(2026, 11, 16)


def test_next_control_is_the_earlier_of_twelve_months_and_hundred_matings():
    fresh = control_due(MEASURED, 13, date(2026, 12, 1))
    assert fresh.due_on == date(2027, 11, 16) and fresh.matings_left == 87 and not fresh.overdue
    assert fresh.line == "следующий контроль до 16.11.2027 или ещё 87 сочленений из 100 — что раньше"
    assert fresh.label_line == "следующий контроль: 16.11.2027 или 100 сочленений"
    assert fresh.short == "Контроль 16.11.2027 / 100 сочл."

    by_matings = control_due(MEASURED, 100, date(2026, 12, 1))  # long before the date
    assert by_matings.overdue and by_matings.matings_left == 0
    assert by_matings.line == "контроль просрочен: 100 сочленений с замера 16.11.2026 (норма 100)"
    assert by_matings.short == "КОНТРОЛЬ ПРОСРОЧЕН" and by_matings.label_line == "контроль просрочен"

    by_date = control_due(MEASURED, 2, date(2027, 11, 17))
    assert by_date.overdue and by_date.line == "контроль просрочен: срок 16.11.2027 прошёл"
    both = control_due(MEASURED, 101, date(2027, 11, 17))
    assert both.line == "контроль просрочен: срок 16.11.2027 прошёл; 101 сочленение с замера 16.11.2026 (норма 100)"
    assert not control_due(MEASURED, 99, date(2027, 11, 16)).overdue  # the due day itself is still in time


def test_unread_counter_keeps_the_rule_and_plural_forms_are_russian():
    unread = control_due(MEASURED, None, date(2026, 12, 1))
    assert unread.matings_left is None and not unread.overdue
    assert unread.line == "следующий контроль до 16.11.2027 или через 100 сочленений — что раньше"
    assert [matings_word(n) for n in (1, 2, 5, 11, 12, 21, 22, 25, 101)] == [
        "1 сочленение", "2 сочленения", "5 сочленений", "11 сочленений", "12 сочленений", "21 сочленение",
        "22 сочленения", "25 сочленений", "101 сочленение"]
    assert next_control_date(date(2024, 2, 29)) == date(2025, 2, 28) and next_control_date(date.max) is None


def test_passport_is_overdue_after_hundred_matings_within_the_year():
    measured = replace(summary(), measured_on=MEASURED)
    early = public_passport(instrument(), rf(), measured, today=date(2026, 12, 1), matings_since=99)
    assert not early.control_overdue and early.next_control == date(2027, 11, 16)
    assert control_line(early) == "Замер 16.11.2026 · следующий контроль до 16.11.2027 или ещё 1 сочленение из 100 — что раньше"
    late = public_passport(instrument(), rf(), measured, today=date(2026, 12, 1), matings_since=100)
    assert late.control_overdue
    assert control_line(late) == "Замер 16.11.2026 · контроль просрочен: 100 сочленений с замера 16.11.2026 (норма 100)"


def _upload_measurement(client, item_id):
    file = measured_file()
    response = client.post(f"/api/production/instruments/{item_id}/files", json={
        "kind": "Прочее", "file_name": file.file_name, "content_base64": base64.b64encode(file.content).decode()})
    assert response.status_code == 200, response.text


def test_card_label_and_passport_count_matings_since_the_measurement_day(production_api):
    from app.modules.production.mating_infrastructure import InstrumentMatingRow

    client, _ = production_api
    item = create(client)
    card = client.get(f"/api/production/instruments/{item}/matings").json()
    assert card["control"] is None  # never measured: no control date
    # Before the measurement day: history, not matings after the control.
    assert post(client, item, end="A", delta=500, kind="initial", note="журнал").status_code == 200
    assert post(client, item, end="B", delta=7, kind="manual").status_code == 200
    with app.state.production_uow_factory().session_factory.begin() as session:
        for row in session.query(InstrumentMatingRow).all():
            row.created_at = datetime(2026, 11, 15, 18, 0, tzinfo=MOSCOW)
    _upload_measurement(client, item)
    fixture_clock = app.dependency_overrides.get(moscow_now)
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 11, 18, 10, 0, tzinfo=MOSCOW)
    try:
        _check_card_label_passport(client, item)
    finally:
        app.dependency_overrides[moscow_now] = fixture_clock


def _check_card_label_passport(client, item):
    from app.modules.production.infrastructure import SqlAlchemyInstrumentRepository
    from app.workflows import instrument_labels

    card = client.get(f"/api/production/instruments/{item}/matings").json()
    assert card["control"] == {"measured_on": "2026-11-16", "due_on": "2027-11-16", "matings_since": 0, "matings_left": 100,
                               "overdue": False, "line": "следующий контроль до 16.11.2027 или ещё 100 сочленений из 100 — что раньше"}
    # The busier side decides: A 60 + 40, B 30.
    assert post(client, item, end="A", delta=60, kind="manual").json()["control"]["matings_left"] == 40
    assert post(client, item, end="B", delta=30, kind="manual").json()["control"]["matings_since"] == 60
    last = post(client, item, end="A", delta=40, kind="manual").json()["control"]
    assert last["overdue"] and last["line"] == "контроль просрочен: 100 сочленений с замера 16.11.2026 (норма 100)"
    with app.state.production_uow_factory().session_factory() as session:
        item_row = SqlAlchemyInstrumentRepository(session).get(UUID(item))
        content = instrument_labels._content(item_row, session, date(2026, 11, 18))
    assert content.value("next_short") == "КОНТРОЛЬ ПРОСРОЧЕН" and content.value("date_full").endswith("контроль просрочен")
    pdf = client.get(f"/api/production/instruments/{item}/passport.pdf")
    assert pdf.status_code == 200
    text = " ".join(" ".join(page.extract_text() for page in PdfReader(BytesIO(pdf.content)).pages).split())
    assert "контроль просрочен: 100 сочленений с замера 16.11.2026" in text


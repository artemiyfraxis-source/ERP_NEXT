"""«Сегодня» follow-ups of the request for quotes: «Нет ответа на запрос КП» and «Выбрать поставщика»."""

import json
from datetime import datetime

from app.main import app
from app.modules.purchasing import settings as purchasing_settings
from app.shared import stage_options
from app.shared.clock import moscow_now
from tests.purchasing.test_rfq_api import BASE, _quote, _rfq, _supplier, rfq_api  # noqa: F401 — fixture


def _options(monkeypatch, tmp_path, **states):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"procurement.today": True, **states}), encoding="utf-8")
    monkeypatch.setenv(stage_options.ENV, str(path))


def _clock(day: int) -> None:
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, day, 11, 30)


def _keys(client) -> dict[str, int]:
    response = client.get("/api/procurement/today")
    assert response.status_code == 200, response.text
    return {section["key"]: section["count"] for section in response.json()["sections"]}


def _flow(client, lines, sessions):
    """One request with two lines; a supplier is asked (sent on the 15th), another one answered for line 0."""
    rfq = _rfq(client, lines)
    rfq_lines = client.get(f"{BASE}/{rfq['id']}").json()["lines"]
    first = next(item for item in rfq_lines if item["procurement_line_id"] == str(lines[0].id))
    silent = _supplier(client, rfq["id"], "ООО Молчит")
    answered = _supplier(client, rfq["id"], "ООО Ответил")
    for supplier in (silent, answered):
        assert client.post(f"{BASE}/{rfq['id']}/suppliers/{supplier}/sent").status_code == 200
    _quote(client, rfq["id"], answered, first["id"], unit_price="100")
    return rfq, first, silent, answered


def test_followups_need_the_rfq_option(rfq_api, monkeypatch, tmp_path):  # noqa: F811
    client, lines, sessions = rfq_api
    _options(monkeypatch, tmp_path, **{"procurement.rfq": True})
    _flow(client, lines, sessions)
    _clock(20)
    keys = _keys(client)
    assert keys["rfq_no_answer"] == 2 and keys["rfq_to_decide"] == 1
    _options(monkeypatch, tmp_path, **{"procurement.rfq": False})
    assert "rfq_no_answer" not in _keys(client) and "rfq_to_decide" not in _keys(client)


def test_no_answer_respects_quote_wait_days_and_declines(rfq_api, monkeypatch, tmp_path):  # noqa: F811
    client, lines, sessions = rfq_api
    _options(monkeypatch, tmp_path, **{"procurement.rfq": True})
    rfq, first, silent, answered = _flow(client, lines, sessions)

    _clock(18)  # three days after sending: not «more than» the default 3
    assert "rfq_no_answer" not in _keys(client)
    _clock(19)
    assert _keys(client)["rfq_no_answer"] == 2
    board = client.get("/api/procurement/today").json()["sections"]
    card = next(section for section in board if section["key"] == "rfq_no_answer")["projects"][0]
    assert card["days"] == 4

    with sessions() as session:
        purchasing_settings.save(session, {"quote_wait_days": 10}, None, datetime(2026, 9, 19))
        session.commit()
    assert "rfq_no_answer" not in _keys(client)
    with sessions() as session:
        purchasing_settings.save(session, {"quote_wait_days": 3}, None, datetime(2026, 9, 19))
        session.commit()

    assert client.post(f"{BASE}/{rfq['id']}/suppliers/{silent}/declined", json={"note": "нет", "declined": True}).status_code == 200
    assert "rfq_no_answer" not in _keys(client)


def test_choose_supplier_appears_with_a_priced_quote_and_goes_after_the_decision(rfq_api, monkeypatch, tmp_path):  # noqa: F811
    client, lines, sessions = rfq_api
    _options(monkeypatch, tmp_path, **{"procurement.rfq": True})
    rfq, first, silent, answered = _flow(client, lines, sessions)

    board = client.get("/api/procurement/today").json()["sections"]
    order = [section["key"] for section in board]
    assert order.index("rfq_to_decide") < order.index("in_work")  # before «В работе»
    assert next(s for s in board if s["key"] == "rfq_to_decide")["count"] == 1

    drill = client.get("/api/procurement/today/project", params={"project": "Без проекта"}).json()["lines"]
    row = next(item for item in drill if item["id"] == str(lines[0].id))
    assert row["field"] == "Выбрать поставщика" and "Выбрать поставщика" in row["fields"] and "В работе: запросить, выбрать поставщика" in row["fields"]
    assert row["rfq"]["id"] == rfq["id"] and "выберите поставщика" in row["rfq"]["hint"]
    assert next(item for item in drill if item["id"] == str(lines[1].id))["rfq"] is None

    matrix = client.get(f"{BASE}/{rfq['id']}").json()
    cell = next(item for item in matrix["lines"] if item["id"] == first["id"])["cells"][answered]
    decided = client.post(f"{BASE}/{rfq['id']}/decisions", json={
        "rfq_line_id": first["id"], "quote_line_id": cell["quote_line_id"], "gate_override_reason": "Срочно",
    })
    assert decided.status_code == 201, decided.text
    assert "rfq_to_decide" not in _keys(client)  # a decided line leaves the field


def test_no_answer_marks_only_the_lines_asked_of_the_silent_supplier(rfq_api, monkeypatch, tmp_path):  # noqa: F811
    client, lines, sessions = rfq_api
    _options(monkeypatch, tmp_path, **{"procurement.rfq": True})
    rfq = _rfq(client, lines)
    rfq_lines = client.get(f"{BASE}/{rfq['id']}").json()["lines"]
    first = next(item for item in rfq_lines if item["procurement_line_id"] == str(lines[0].id))
    silent = _supplier(client, rfq["id"], "ООО Молчит")
    asked = client.put(f"{BASE}/{rfq['id']}/suppliers/{silent}/lines", json={"line_ids": [first["id"]]})
    assert asked.status_code == 200, asked.text
    assert client.post(f"{BASE}/{rfq['id']}/suppliers/{silent}/sent").status_code == 200
    _clock(20)
    assert _keys(client)["rfq_no_answer"] == 1
    drill = client.get("/api/procurement/today/project", params={"project": "Без проекта"}).json()["lines"]
    assert "ООО Молчит" in next(item for item in drill if item["id"] == str(lines[0].id))["rfq"]["hint"]
    assert next(item for item in drill if item["id"] == str(lines[1].id))["rfq"] is None  # not asked of him

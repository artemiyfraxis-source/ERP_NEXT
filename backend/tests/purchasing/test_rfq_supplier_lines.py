"""Запрос КП: свои позиции у каждого поставщика (purchasing_rfq_supplier_lines). No rows for a request supplier — it
is asked for every line (older requests); rows — exactly those lines."""

from uuid import uuid4

from sqlalchemy import func, select

from app.modules.purchasing.infrastructure import RfqRow, RfqSupplierLineRow, SupplierRow
from tests.purchasing.test_rfq_pool import BASE, _as, _suggest, stand  # noqa: F401 — fixture


def _rows(stand) -> int:
    with stand.sessions() as session:
        return session.scalar(select(func.count()).select_from(RfqSupplierLineRow))


def _matrix(stand, rfq_id: str) -> dict:
    response = stand.client.get(f"{BASE}/{rfq_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _rfq_lines(matrix: dict) -> dict[str, str]:
    """procurement line id → request line id."""
    return {line["procurement_line_id"]: line["id"] for line in matrix["lines"]}


def _quote(stand, rfq_id: str, supplier_id: str, rfq_line_id: str):
    return stand.client.post(f"{BASE}/{rfq_id}/quotes", json={
        "supplier_id": supplier_id, "lines": [{"rfq_line_id": rfq_line_id, "unit_price": "10"}],
    })


def test_suggest_returns_each_suppliers_lines_with_reasons(stand):
    alfa = stand.supplier("ООО Альфа")
    stand.supplier("ООО Гамма")
    stand.supplier("ООО Ключ-кабель", key=True, categories=["cable"])
    stand.supplier("ООО Ротация", categories=["connector"])
    stand.item("Разъём XH-2P", "connector", part_number="XH-2P")
    for day in ("2026-08-01", "2026-09-01"):
        stand.paid("Разъём XH-2P", "Альфа", "9", day, design_article="XH-2P")
    stand.paid("Кабель ПВС 3х1", "ООО Кабельщик", "40", "2026-06-01")
    xh = stand.active("Разъём XH-2P", design_article="XH-2P")
    clamp = stand.active("Клемма винтовая 2-конт.")
    cable = stand.active("Кабель ПВС 2х0,75", supplier="Гамма")

    body = _suggest(stand, xh, clamp, cable)
    lines = {row["name"]: row["lines"] for row in body["suggestions"]}
    assert lines["ООО Альфа"] == [
        {"line_id": str(xh), "reasons": ["items", "category_history"]},
        {"line_id": str(clamp), "reasons": ["category_history"]},
    ]
    assert lines["ООО Гамма"] == [{"line_id": str(cable), "reasons": ["original"]}]
    assert lines["ООО Ключ-кабель"] == [{"line_id": str(cable), "reasons": ["key"]}]
    assert lines["ООО Ротация"] == [
        {"line_id": str(xh), "reasons": ["rotation"]}, {"line_id": str(clamp), "reasons": ["rotation"]},
    ]
    assert lines["ООО Кабельщик"] == [{"line_id": str(cable), "reasons": ["category_history"]}]
    assert next(row for row in body["suggestions"] if row["name"] == "ООО Альфа")["supplier_id"] == str(alfa)


def test_create_with_partial_lines_stores_rows_and_a_full_selection_none(stand):
    alfa, beta, gamma = (stand.supplier(name) for name in ("ООО Альфа", "ООО Бета", "ООО Гамма"))
    a, b, c = (stand.active(name) for name in ("Разъём XH-2P", "Разъём XH-3P", "Разъём XH-4P"))
    ids = [str(a), str(b), str(c)]
    body = {"line_ids": ids, "suppliers": [
        {"supplier_id": str(alfa), "reason": "items", "line_ids": [str(a), str(c)]},
        {"supplier_id": str(beta), "reason": "key", "line_ids": ids},  # every line — no rows
        {"supplier_id": str(gamma), "reason": "manual"},  # no line_ids — every line
        {"name": "ИП Новый", "reason": "manual", "line_ids": [str(b)]},
    ]}
    created = stand.client.post(BASE, json=body)
    assert created.status_code == 201, created.text
    assert _rows(stand) == 3

    matrix = _matrix(stand, created.json()["id"])
    rfq_line = _rfq_lines(matrix)
    asked = {supplier["name"]: supplier["line_ids"] for supplier in matrix["suppliers"]}
    assert asked == {
        "ООО Альфа": [rfq_line[str(a)], rfq_line[str(c)]], "ООО Бета": None, "ООО Гамма": None,
        "ИП Новый": [rfq_line[str(b)]],
    }
    line_b = next(line for line in matrix["lines"] if line["id"] == rfq_line[str(b)])
    assert line_b["cells"] == {str(alfa): {"asked": False}}  # the others are asked: no quote yet, no cell
    assert line_b["gate"]["priced"] == 0


def test_an_empty_or_foreign_selection_is_refused_and_nothing_is_written(stand):
    alfa = stand.supplier("ООО Альфа")
    a, b = stand.active("Разъём XH-2P"), stand.active("Разъём XH-3P")
    empty = stand.client.post(BASE, json={"line_ids": [str(a), str(b)], "suppliers": [
        {"name": "ИП Новый", "reason": "manual"},
        {"supplier_id": str(alfa), "reason": "manual", "line_ids": []},
    ]})
    assert empty.status_code == 422 and empty.json()["detail"] == "Поставщику не выбрано ни одной позиции: ООО Альфа"
    foreign = stand.client.post(BASE, json={"line_ids": [str(a)], "suppliers": [
        {"supplier_id": str(alfa), "reason": "manual", "line_ids": [str(b)]},
    ]})
    assert foreign.status_code == 422 and "не из этого запроса" in foreign.json()["detail"]
    with stand.sessions() as session:
        assert session.scalar(select(func.count()).select_from(RfqRow)) == 0
        assert session.scalar(select(SupplierRow).where(SupplierRow.name == "ИП Новый")) is None


def test_edit_lines_later_and_a_quoted_line_stays(stand):
    alfa = str(stand.supplier("ООО Альфа"))
    a, b = stand.active("Разъём XH-2P"), stand.active("Разъём XH-3P")
    rfq = stand.client.post(BASE, json={"line_ids": [str(a), str(b)], "suppliers": [{"supplier_id": alfa}]}).json()
    rfq_line = _rfq_lines(_matrix(stand, rfq["id"]))
    url = f"{BASE}/{rfq['id']}/suppliers/{alfa}/lines"

    only_a = stand.client.put(url, json={"line_ids": [rfq_line[str(a)]]})
    assert only_a.status_code == 200, only_a.text
    assert only_a.json() == {"line_ids": [rfq_line[str(a)]]} and _rows(stand) == 1
    refused = _quote(stand, rfq["id"], alfa, rfq_line[str(b)])
    assert refused.status_code == 422 and "не запрашивали" in refused.json()["detail"]
    assert _quote(stand, rfq["id"], alfa, rfq_line[str(a)]).status_code == 201

    dropped = stand.client.put(url, json={"line_ids": [rfq_line[str(b)]]})
    assert dropped.status_code == 422 and dropped.json()["detail"].startswith("«Разъём XH-2P»: у поставщика уже есть КП")
    assert stand.client.put(url, json={"line_ids": []}).json()["detail"] == "Поставщику не выбрано ни одной позиции"
    assert stand.client.put(url, json={"line_ids": [str(uuid4())]}).status_code == 422
    assert stand.client.put(f"{BASE}/{rfq['id']}/suppliers/{uuid4()}/lines", json={"line_ids": []}).status_code == 404

    everything = stand.client.put(url, json={"line_ids": list(rfq_line.values())})
    assert everything.json() == {"line_ids": None} and _rows(stand) == 0
    assert _quote(stand, rfq["id"], alfa, rfq_line[str(b)]).status_code == 201

    _as("Иванов")
    assert stand.client.put(url, json={"line_ids": [rfq_line[str(a)]]}).status_code == 403


def test_lines_edit_gate_viewer_403(stand):
    url = f"{BASE}/{uuid4()}/suppliers/{uuid4()}/lines"
    _as("Зритель", "view")
    assert stand.client.put(url, json={"line_ids": []}).status_code == 403


def test_an_old_request_without_rows_asks_every_line(stand):
    alfa = str(stand.supplier("ООО Альфа"))
    a, b = stand.active("Разъём XH-2P"), stand.active("Разъём XH-3P")
    rfq = stand.client.post(BASE, json={"line_ids": [str(a), str(b)], "suppliers": [{"supplier_id": alfa}]}).json()
    assert _rows(stand) == 0
    matrix = _matrix(stand, rfq["id"])
    assert matrix["suppliers"][0]["line_ids"] is None
    assert all(line["cells"] == {} for line in matrix["lines"])
    for rfq_line_id in _rfq_lines(matrix).values():
        assert _quote(stand, rfq["id"], alfa, rfq_line_id).status_code == 201
    cells = [line["cells"][alfa] for line in _matrix(stand, rfq["id"])["lines"]]
    assert all(cell["asked"] is True and cell["unit_price"] == "10" for cell in cells)


def test_removing_a_supplier_takes_its_lines_and_paste_matches_only_its_lines(stand):
    alfa, beta = str(stand.supplier("ООО Альфа")), str(stand.supplier("ООО Бета"))
    a, b = stand.active("Разъём XH-2P"), stand.active("Клемма WAGO 221-412")
    rfq = stand.client.post(BASE, json={"line_ids": [str(a), str(b)], "suppliers": [
        {"supplier_id": alfa, "line_ids": [str(a)]}, {"supplier_id": beta, "line_ids": [str(b)]},
    ]}).json()
    rfq_line = _rfq_lines(_matrix(stand, rfq["id"]))
    text = "Наименование\tЦена\nРазъём XH-2P\t10\nКлемма WAGO 221-412\t20\n"
    preview = stand.client.post(f"{BASE}/{rfq['id']}/quotes/paste", json={"text": text, "supplier_id": alfa}).json()
    assert [row["rfq_line_id"] for row in preview["rows"]] == [rfq_line[str(a)], None]
    both = stand.client.post(f"{BASE}/{rfq['id']}/quotes/paste", json={"text": text}).json()
    assert [row["rfq_line_id"] for row in both["rows"]] == [rfq_line[str(a)], rfq_line[str(b)]]

    assert stand.client.delete(f"{BASE}/{rfq['id']}/suppliers/{alfa}").status_code == 204
    assert _rows(stand) == 1
    assert [s["name"] for s in _matrix(stand, rfq["id"])["suppliers"]] == ["ООО Бета"]

"""SK-16 «Журнал склада»: SUPPLYMAP Stock05.gs stock05Log_ + Portal.gs portalStock05Log."""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.warehouse.domain import StockLot, WriteOffEntry
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork
from app.modules.warehouse.journal import JournalEvent, event_text, journal_page
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor


def _lot(grn="FRX.26.0000001", package="", name="Разъём", warehouse="ЦАР") -> StockLot:
    return StockLot.create(
        warehouse=warehouse, grn=grn, package=package, name=name, item_type="Комплектующие", purpose="FRX.001",
        unit="шт", quantity=Decimal("10"), storage_location="1F4", source_receiving_ids=(str(uuid4()),),
    )


def _entry(lot: StockLot, kind: str, when: datetime, *, quantity="2", reason="", purpose="", posted=True, author="Петров"):
    return WriteOffEntry.for_lot(
        lot, kind=kind, quantity=Decimal(quantity), reason=reason, purpose=purpose, author=author,
        actor_id=uuid4(), actor_name="Кладовщик", occurred_at=when, remaining_after=None, posted=posted,
    )


def _event(event_type: str, when: datetime, details: dict, **lot) -> JournalEvent:
    return JournalEvent(occurred_at=when, event_type=event_type, actor_name="Иванов", details=details, **lot)


def test_events_and_writeoffs_merge_newest_first_and_ties_keep_events_before_writeoffs():
    lot = _lot()
    events = [
        _event("warehouse.moved", datetime(2026, 9, 22, 10, 0), {"from": "1F4", "to": "2A1"}, grn=lot.grn, name="Разъём", warehouse="ЦАР"),
        _event("warehouse.manually_received", datetime(2026, 9, 20, 9, 0), {"quantity": "10.000000", "storage_location": "1F4"},
               grn=lot.grn, name="Разъём", warehouse="ЦАР"),
    ]
    writeoffs = [
        _entry(lot, "return", datetime(2026, 9, 23, 8, 0), quantity="1.5", reason="Возврат с участка", purpose="FRX.001"),
        _entry(lot, "writeoff", datetime(2026, 9, 22, 10, 0), reason="Брак", purpose="FRX.001", posted=False),
    ]

    page = journal_page(events, writeoffs)

    assert [(row.kind, row.event) for row in page.rows] == [
        ("Возврат", "+1.5 шт · Возврат с участка · FRX.001 · проведено"),
        ("Событие", "Перемещение: 1F4 → 2A1"),
        ("Списание", "−2 шт · Брак · FRX.001 · ждёт проводки"),
        ("Событие", "Внесено вручную: +10 · место 1F4"),
    ]
    assert (page.total, page.truncated) == (4, 0)
    assert page.rows[0].who == "Петров" and page.rows[1].who == "Иванов"


def test_issue_rows_are_listed_as_issues_and_mirrored_audit_events_are_not_doubled():
    lot = _lot(package="П2")
    events = [_event(kind, datetime(2026, 9, 22), {"quantity": "2"}, grn=lot.grn) for kind in
              ("warehouse.written_off", "warehouse.writeoff_returned", "warehouse.legacy_writeoff_history")]
    page = journal_page(events, [_entry(lot, "issue", datetime(2026, 9, 22), reason="Выдача", purpose="КБ-7")])

    assert [(row.kind, row.lot, row.event) for row in page.rows] == [
        ("Выдача", "FRX.26.0000001 / П2", "−2 шт · Выдача · КБ-7 · проведено"),
    ]


def test_search_needs_every_word_in_grn_name_event_person_or_warehouse():
    first, second = _lot(), _lot(grn="OLD.26.0000012", name="Кабель МКЭШ", warehouse="ЖУК")
    writeoffs = [
        _entry(first, "writeoff", datetime(2026, 9, 21), reason="Брак"),
        _entry(second, "return", datetime(2026, 9, 22), reason="Излишек", author="Сидоров"),
    ]
    events = [_event("warehouse.moved", datetime(2026, 9, 23), {"from": "1F4", "to": "2A1"}, grn=first.grn, name="Разъём")]

    assert [row.lot for row in journal_page(events, writeoffs, "old.26.0000012").rows] == ["OLD.26.0000012"]
    assert [row.kind for row in journal_page(events, writeoffs, "  ВОЗВРАТ сидоров ").rows] == ["Возврат"]
    assert [row.kind for row in journal_page(events, writeoffs, "перемещение").rows] == ["Событие"]
    assert [row.lot for row in journal_page(events, writeoffs, "жук").rows] == ["OLD.26.0000012"]
    assert journal_page(events, writeoffs, "разъём брак иванов").rows == ()


def test_only_the_last_matches_up_to_the_limit_are_sent_with_the_full_count():
    lot = _lot()
    writeoffs = [_entry(lot, "writeoff", datetime(2026, 9, day)) for day in range(1, 11)]

    page = journal_page([], writeoffs, limit=3)

    assert [row.occurred_at.day for row in page.rows] == [10, 9, 8]
    assert (page.total, page.truncated) == (10, 7)


@pytest.mark.parametrize(
    ("event_type", "details", "text"),
    [
        ("warehouse.received", {"quantity": "5", "storage_location_from": "1A1", "storage_location_to": "2B2"},
         "Приход из приёмки: +5; место: 1A1 → 2B2"),
        ("warehouse.legacy_imported", {"inbound": "2387", "written_off": "2387", "storage_location": ""},
         "Перенос остатков из SUPPLYMAP: приход 2387, списано 2387"),
        ("warehouse.transferred", {"from_warehouse": "ЦАР", "to_warehouse": "ЖУК", "from": "1A1", "to": ""},
         "Перемещение на другой склад: ЦАР → ЖУК, место 1A1 → —"),
        ("warehouse.location_moved", {"from": "12C3A", "to": "15B5A"}, "Перенос места целиком: 12C3A → 15B5A"),
        ("warehouse.row_edited", {"changes": "Тип: «—» → «Кабель»", "reason": "Импорт таблицы"},
         "Правка строки: Тип: «—» → «Кабель» (Импорт таблицы)"),
        ("warehouse.item_types_saved", {"count": "12"}, "Справочник типов сохранён: 12"),
    ],
)
def test_audit_events_read_as_journal_words(event_type, details, text):
    assert event_text(event_type, details) == text


@pytest.fixture()
def journal_api():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyWarehouseUnitOfWork(sessions)
    app.state.warehouse_uow_factory = factory
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 21, 12, 0)

    def login(*roles: str, warehouse: str = "edit"):
        app.dependency_overrides[authenticated_principal] = lambda: Principal(
            actor=Actor(name="Админ", department="Администрация"), email="admin@example.test",
            roles=frozenset(roles), permissions={"warehouse": warehouse},
        )

    login("admin")
    yield TestClient(app), factory, login
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.warehouse_uow_factory
    engine.dispose()


def test_journal_endpoint_reads_real_movements_once_newest_first(journal_api):
    client, factory, _ = journal_api
    lot = _lot()
    with factory() as uow:
        uow.warehouse.add(lot)
        uow.commit()
    moved = client.post(f"/api/stock/batches/{lot.id}/move", json={"version": lot.version, "storage_location": "1F5"})
    written = client.post(f"/api/stock/batches/{lot.id}/writeoff", json={
        "version": moved.json()["lot"]["version"], "quantity": "2", "purpose": "FRX.001", "reason": "Брак",
    })
    assert written.status_code == 200
    with factory() as uow:
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=uuid4(), event_type="procurement.ordered", actor=Actor(name="Закупщик", department="Закупки"),
            occurred_at=datetime(2026, 9, 21, 12, 0), details={},
        ))
        uow.commit()

    body = client.get("/api/stock/journal").json()

    assert [(row["kind"], row["lot"], row["event"]) for row in body["rows"]] == [
        ("Событие", "FRX.26.0000001", "Перемещение: 1F4 → 1F5"),
        ("Списание", "FRX.26.0000001", "−2 шт · Брак · FRX.001 · проведено"),
    ]  # the same minute: events before write-offs; the write-off's own audit event is not listed twice
    assert body["rows"][0]["name"] == "Разъём" and body["rows"][0]["warehouse"] == "ЦАР"
    assert (body["total"], body["truncated"]) == (2, 0)
    assert [row["kind"] for row in client.get("/api/stock/journal", params={"q": "брак"}).json()["rows"]] == ["Списание"]


@pytest.mark.parametrize("roles", [("receiver",), ("master",), ("staff",)])
def test_the_storekeeper_sees_the_journal(journal_api, roles):
    # Владелец 12.10.2026: кто работает со складом, видит историю ГРН (в SUPPLYMAP — только администратор).
    client, _, login = journal_api
    login(*roles)

    assert client.get("/api/stock/journal").status_code == 200


@pytest.mark.parametrize("warehouse", ["view", "none"])
def test_the_journal_is_closed_without_warehouse_edit(journal_api, warehouse):
    client, _, login = journal_api
    login("staff", warehouse=warehouse)

    assert client.get("/api/stock/journal").status_code == 403

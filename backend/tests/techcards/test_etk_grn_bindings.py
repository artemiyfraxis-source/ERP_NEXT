"""ГРН in the ETK from the current bindings (owner, 12.10.2026: «после того как Алексей привяжет к позиции ГРН, нужно
чтобы это синкалось в техкарте»): the lots issued to the shop for the КБ, else the «Состав» lot, else the snapshot."""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.fulfillment.domain import (
    FulfillmentKind, FulfillmentLine, FulfillmentRequest, FulfillmentStatus, LotSnapshot,
)
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.kitsets import KitAssembly, KitComponent, KitLot, Kitset
from app.shared.clock import moscow_now
from app.shared.persistence import create_schema
from app.workflows.etk_lots import IssuedKitLots

NOW = datetime(2026, 10, 12, 9, 0)
MOLEX, WIRE = "43025-0400", "UL1061 30AWG белый"


def _document() -> dict:
    return {"operations": [
        {"code": "CRIMP", "connector": "XS1", "name": "Опрессовка", "components": [
            {"art": MOLEX, "name": "Корпус Molex", "norm": "2", "grn": "Г-СНИМОК"},
            {"art": WIRE, "name": WIRE, "norm": "0,5"},
        ]},
    ]}


@pytest.fixture()
def world():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    statements: list[str] = []
    event.listen(engine, "before_cursor_execute", lambda *args: statements.append(args[2]))
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.techcards_uow_factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    app.state.fulfillment_uow_factory = lambda: SqlAlchemyFulfillmentUnitOfWork(sessions)
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app), sessions, statements
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.techcards_uow_factory
    del app.state.fulfillment_uow_factory
    engine.dispose()


def _issued_card(client: TestClient, project: str = "180К.11", item_index: str = "КБ1") -> str:
    card = client.post("/api/techcards/projects", json={
        "project": project, "item_index": item_index, "product_name": "Жгут", "source_calculation": "",
    }).json()
    built = client.put(f"/api/techcards/projects/{card['id']}/etk", json={
        "version": card["version"], "document": _document(),
    }).json()
    issued = client.post(f"/api/techcards/projects/{card['id']}/issue", json={"version": built["version"]})
    assert issued.status_code == 200
    return card["id"]


def _bind_in_kitset(sessions, lots: dict[str, KitLot | None], index: str = "КБ1") -> None:
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        uow.kitsets.save(Kitset(
            project="180К.11", customer="Заказчик", calc_source="", updated_at=NOW, updated_by="Технолог",
            created_at=NOW, assemblies=(KitAssembly(index=index, name="Жгут", run=Decimal("2"), components=tuple(
                KitComponent(article=article, type="Разъём", name="Корпус Molex", lot=lot)
                for article, lot in lots.items()
            )),),
        ))
        uow.commit()


def _hand_out(sessions, purpose: str, issued: list[tuple[str, str, str, Decimal]], *,
              kind: FulfillmentKind = FulfillmentKind.MAIN, created: datetime = NOW) -> str:
    """A kit request with lines issued from lots: (article, grn, package, issued)."""
    lines = []
    for article, grn, package, quantity in issued:
        line_id = uuid4()
        lines.append(FulfillmentLine(
            id=line_id, requirement_id=line_id, article=article, name=article, unit="шт", requirement=Decimal("4"),
            reserved=quantity, issued=quantity,
            lot=LotSnapshot(lot_id=uuid4(), warehouse="ЦАР", grn=grn, package=package, storage_location="А-1"),
        ))
    number = f"REQ.26.{uuid4().int % 10_000_000:07d}"
    with SqlAlchemyFulfillmentUnitOfWork(sessions) as uow:
        uow.fulfillment.add(FulfillmentRequest(
            id=uuid4(), number=number, source_idempotency_key=number, source_fingerprint="x", version=1,
            created_at=created, requester="Алексей", purpose=purpose, deadline=date(2026, 10, 20),
            status=FulfillmentStatus.ISSUED, lines=tuple(lines), kind=kind,
        ))
        uow.commit()
    return number


def _grn(client: TestClient, card_id: str) -> list[str]:
    document = client.get(f"/api/techcards/projects/{card_id}").json()["document"]
    return [row["grn"] for row in document["operations"][0]["components"]]


def test_grn_bound_in_the_kitset_after_the_etk_was_issued_shows_on_read(world):
    client, sessions, _ = world
    card_id = _issued_card(client)
    assert _grn(client, card_id) == ["Г-СНИМОК", ""]

    _bind_in_kitset(sessions, {MOLEX: KitLot(lot_id=uuid4(), warehouse="ЦАР", grn="Г-101", package="1")})

    assert _grn(client, card_id) == ["Г-101", ""]


def test_lots_issued_to_the_shop_beat_the_kitset_binding_and_extra_issues_add_up(world):
    client, sessions, _ = world
    card_id = _issued_card(client)
    _bind_in_kitset(sessions, {MOLEX: KitLot(lot_id=uuid4(), warehouse="ЦАР", grn="Г-101", package="1")})
    _hand_out(sessions, "180К.11 · КБ1 · Жгут", [(MOLEX, "Г-202", "1", Decimal("4"))])
    _hand_out(
        sessions, "180К.11 · КБ1 · Довыдача", [(MOLEX, "Г-303", "", Decimal("1")), (WIRE, "Г-404", "", Decimal("0"))],
        kind=FulfillmentKind.EXTRA, created=datetime(2026, 10, 12, 11, 0),
    )

    assert _grn(client, card_id) == ["Г-202, Г-303", ""]


def _kit_requests(client: TestClient, card_id: str) -> list[str | None]:
    document = client.get(f"/api/techcards/projects/{card_id}").json()["document"]
    return [row.get("kit_request") for row in document["operations"][0]["components"]]


def test_kit_request_number_shows_next_to_the_issued_component(world):
    client, sessions, _ = world
    card_id = _issued_card(client)
    number = _hand_out(sessions, "180К.11 · КБ1 · Жгут", [(MOLEX, "Г-202", "1", Decimal("4"))])

    assert _kit_requests(client, card_id) == [number, None]


def test_several_requests_of_a_component_are_joined_and_none_leaves_it_plain(world):
    client, sessions, _ = world
    card_id = _issued_card(client)
    assert _kit_requests(client, card_id) == [None, None]
    _bind_in_kitset(sessions, {MOLEX: KitLot(lot_id=uuid4(), warehouse="ЦАР", grn="Г-101", package="1")})
    assert _kit_requests(client, card_id) == [None, None]  # kitset binding alone is not a request

    main = _hand_out(sessions, "180К.11 · КБ1 · Жгут", [(MOLEX, "Г-202", "1", Decimal("4"))])
    extra = _hand_out(
        sessions, "180К.11 · КБ1 · Довыдача", [(MOLEX, "Г-303", "", Decimal("1"))],
        kind=FulfillmentKind.EXTRA, created=datetime(2026, 10, 12, 11, 0),
    )

    assert _kit_requests(client, card_id) == [f"{main}, {extra}", None]


def test_another_kb_lots_are_not_mixed_in(world):
    client, sessions, _ = world
    kb1 = _issued_card(client, item_index="КБ1")
    kb2 = _issued_card(client, item_index="КБ2")
    _hand_out(sessions, "180К.11 · КБ2 · Жгут", [(MOLEX, "Г-222", "", Decimal("4"))])
    _hand_out(sessions, "180К.1 · КБ1 · Жгут", [(MOLEX, "Г-999", "", Decimal("4"))])

    assert _grn(client, kb1) == ["Г-СНИМОК", ""]
    assert _grn(client, kb2) == ["Г-222", ""]


def test_a_card_without_a_kit_keeps_the_snapshot(world):
    client, sessions, _ = world
    card_id = _issued_card(client, project="P-100", item_index="01")
    _hand_out(sessions, "Без проекта, ремонт", [(MOLEX, "Г-202", "", Decimal("4"))])

    assert _grn(client, card_id) == ["Г-СНИМОК", ""]


def test_the_list_reads_the_bindings_of_all_cards_in_one_query_each(world):
    client, sessions, statements = world
    cards = [_issued_card(client, item_index=f"КБ{index}") for index in (1, 2, 3)]
    _bind_in_kitset(sessions, {MOLEX: KitLot(lot_id=None, warehouse="ЦАР", grn="Г-101")}, index="КБ3")
    _hand_out(sessions, "180К.11 · КБ1 · Жгут", [(MOLEX, "Г-202", "", Decimal("4"))])
    statements.clear()

    items = {item["id"]: item for item in client.get("/api/techcards/projects").json()["items"]}

    grn = [items[card]["document"]["operations"][0]["components"][0]["grn"] for card in cards]
    assert grn == ["Г-202", "Г-СНИМОК", "Г-101"]
    assert sum("FROM techcard_kitsets" in sql for sql in statements) == 1
    assert sum("FROM fulfillment_requests JOIN fulfillment_lines" in sql for sql in statements) == 1


def test_port_answers_by_the_names_the_etk_uses(world):
    _, sessions, _ = world
    _hand_out(sessions, "180K.11 · КБ-1 · Жгут", [(MOLEX, "Г-202", "2", Decimal("4"))])
    lookup = IssuedKitLots(lambda: SqlAlchemyFulfillmentUnitOfWork(sessions))

    found = lookup([("180к.11", "кб 1"), ("180К.11", "КБ2"), ("", "КБ1")])

    assert list(found) == [("180к.11", "кб 1")]
    assert [(lot.article, lot.grn, lot.package) for lot in found[("180к.11", "кб 1")]] == [(MOLEX, "Г-202", "2")]
    assert lookup([]) == {}

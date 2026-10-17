"""Audit U05: `python -m app.db_integrity reconcile` finds both F01 shapes and stays quiet on correct reserves."""

from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import update

from app import db_integrity
from app.db_integrity import reconcile, reconcile_lines
from app.modules.fulfillment.application import BindLotCommand, bind_lot
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.procurement import infrastructure as procurement_infrastructure
from app.modules.procurement.application import bind_stock_cover, cover_from_stock
from app.modules.procurement.domain import ProcurementLine
from app.modules.warehouse import infrastructure as warehouse_infrastructure
from app.modules.warehouse.infrastructure import StockLotRow
from tests.procurement.test_stock_cover import (
    BUYER, NOW, add_lot, assert_integrity_clean, bind, cover, cover_without_lot, kit, line_in_work, stock,
)
from tests.procurement.test_stock_versions import (  # noqa: F401 — fixture
    bind_command, concurrent_env, cover_command, lotless_line, nested, run_inside, stock_uow,
)


def report(sessions):
    return reconcile(sessions.kw["bind"])


def without_compare_and_swap(monkeypatch):
    """The repositories as they were before the fix, to produce the F01 data the report must find."""
    monkeypatch.setattr(procurement_infrastructure, "check_read_version", lambda *args: None)
    monkeypatch.setattr(warehouse_infrastructure, "check_read_version", lambda *args: None)


def test_two_covers_written_over_one_line_are_reported(concurrent_env, monkeypatch):
    client, sessions = concurrent_env
    without_compare_and_swap(monkeypatch)
    line = line_in_work(client, sessions)
    lot_a = add_lot(sessions, stock("100", grn="FRX.26.0000201"))
    lot_b = add_lot(sessions, stock("100", grn="FRX.26.0000202"))
    run_inside(monkeypatch, ProcurementLine, "cover_from_stock", nested(
        sessions, lambda: cover_from_stock(cover_command(line, lot_b), stock_uow(sessions)),
    ))
    cover_from_stock(cover_command(line, lot_a), stock_uow(sessions))  # the lost update of audit F01

    found = report(sessions)

    assert found["lots"] == []  # each lot's reserve has its cover
    assert found["lines"] == [{
        "line_id": str(UUID(line["id"])), "legacy_id": line["legacy_id"], "stock_covered": "80", "covers": "160",
    }]
    assert reconcile_lines(found) == [
        f"Строка закупки {line['legacy_id']}: «Со склада» 80, а её покрытия держат и передали в комплектацию 160.",
    ]


def test_one_cover_bound_to_two_lots_leaves_a_reserve_without_holder(concurrent_env, monkeypatch):
    client, sessions = concurrent_env
    without_compare_and_swap(monkeypatch)
    line, cover_id = lotless_line(client, sessions)
    lot_a = add_lot(sessions, stock("100", grn="FRX.26.0000201"))
    lot_b = add_lot(sessions, stock("100", grn="FRX.26.0000202"))
    run_inside(monkeypatch, ProcurementLine, "bind_stock_cover", nested(
        sessions, lambda: bind_stock_cover(bind_command(line, cover_id, lot_b), stock_uow(sessions)),
    ))
    bind_stock_cover(bind_command(line, cover_id, lot_a), stock_uow(sessions))  # the lost update of audit F01
    assert_integrity_clean(sessions)  # the snapshot counts cannot see it: holders never exceed the reserve

    found = report(sessions)

    assert found["lines"] == []
    assert found["lots"] == [{
        "lot_id": str(lot_b.id), "warehouse": "ЦАР", "grn": "FRX.26.0000202", "package": "", "amount": "80",
        "covers": [cover_id],
    }]
    assert reconcile_lines(found) == [
        f"Партия ЦАР FRX.26.0000202: резерв 80 без держателя — журнал партии отдаёт его «Со склада» "
        f"({cover_id}), а там его никто не держит.",
    ]

    # Someone takes the stray reserve off by hand: the same lot is not reported again.
    with sessions() as session:
        session.execute(update(StockLotRow).where(StockLotRow.id == lot_b.id).values(reserved=0))
        session.commit()
    assert report(sessions) == {"lines": [], "lots": []}


def test_correct_reserves_report_nothing(concurrent_env):
    """Consumed by a kit, trimmed КОЛ-ВО (факт), released, cancelled, without a lot, bound later, a manual reserve and a
    kit of another project on the same lots: everything adds up."""
    client, sessions = concurrent_env
    lot = add_lot(sessions, stock("200"))
    manual = add_lot(sessions, stock("100", grn="FRX.26.0000009").reserve(Decimal("20")))  # no holder, no trail

    # Partial cover (cuts КОЛ-ВО (факт)), a kit of the project takes part of it over, the rest is released.
    first = line_in_work(client, sessions, suffix=1)
    first = cover(client, first, lot, "80").json()["line"]
    taken = kit(sessions, "591К.3 · КБ1 · Жгут", "30")
    bind_lot(BindLotCommand(
        request_id=taken.id, expected_version=taken.version, line_id=taken.lines[0].id, lot_id=lot.id,
        actor=BUYER, now=NOW,
    ), SqlAlchemyFulfillmentUnitOfWork(sessions))
    current = client.get(f"/api/procurement/{first['id']}/stock-candidates").json()["line"]
    released = client.post(f"/api/procurement/{first['id']}/stock-cover/release", json={"version": current["version"]})
    assert released.status_code == 200, released.text
    assert released.json()["line"]["stock_covered"] == "30"

    # A whole line covered and closed, then wholly taken over by a kit.
    second = line_in_work(client, sessions, suffix=2, quantity="40", project="700К.1")
    assert cover(client, second, lot, "40").status_code == 200
    kit(sessions, "700К.1 · КБ1 · Жгут", "40", lot)

    # Without a lot, then bound; another without a lot stays so; a covered line cancelled.
    third, third_cover = lotless_line(client, sessions)
    assert bind(client, third, third_cover, manual).status_code == 200
    fourth = line_in_work(client, sessions, suffix=4)
    assert cover_without_lot(client, fourth, "10").status_code == 200
    fifth = line_in_work(client, sessions, suffix=5)
    fifth = cover(client, fifth, lot, "20").json()["line"]
    cancelled = client.post(f"/api/procurement/{fifth['id']}/cancel", json={"version": fifth["version"]})
    assert cancelled.status_code == 200, cancelled.text

    # A kit of a project without covers reserves from ДОСТУПНО.
    kit(sessions, "800К.1 · КБ1 · Жгут", "25", lot)

    assert_integrity_clean(sessions)
    assert report(sessions) == {"lines": [], "lots": []}


def test_cli_reconcile(concurrent_env, monkeypatch, capsys):
    client, sessions = concurrent_env
    url = sessions.kw["bind"].url.render_as_string(hide_password=False)
    assert db_integrity.main(["reconcile", "--database-url", url]) == 0
    assert "Резервы «Со склада» сходятся" in capsys.readouterr().out

    without_compare_and_swap(monkeypatch)
    line, cover_id = lotless_line(client, sessions)
    lots = [add_lot(sessions, stock("100", grn=f"FRX.26.000020{index}")) for index in (1, 2)]
    run_inside(monkeypatch, ProcurementLine, "bind_stock_cover", nested(
        sessions, lambda: bind_stock_cover(bind_command(line, cover_id, lots[1]), stock_uow(sessions)),
    ))
    bind_stock_cover(bind_command(line, cover_id, lots[0]), stock_uow(sessions))
    assert db_integrity.main(["reconcile", "--database-url", url]) == 1
    assert "резерв 80 без держателя" in capsys.readouterr().out
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    assert db_integrity.main(["reconcile", "--database-url", ""]) == 2


@pytest.fixture(autouse=True)
def _no_configured_database(monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)

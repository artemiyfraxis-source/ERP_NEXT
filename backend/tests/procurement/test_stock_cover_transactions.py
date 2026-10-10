"""Stock-cover commands roll back every participant after a late database failure."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.procurement.infrastructure import ProcurementLineRow, ProcurementStockCoverRow
from app.modules.warehouse.infrastructure import StockLotRow
from app.shared.persistence import AuditEventRow, OutboxMessageRow
from app.workflows.procurement_stock import SqlAlchemyProcurementStockUnitOfWork
from tests.procurement.test_stock_cover import (  # noqa: F401 — fixture
    add_lot, bind, cover, cover_without_lot, env, line_in_work, stock,
)


def snapshot(connection):
    # Core reads see flushed database rows, not pending changes in the ORM identity map.
    return {
        name: [dict(row) for row in connection.execute(select(model.__table__).order_by(model.id)).mappings()]
        for name, model in (
            ("lines", ProcurementLineRow), ("covers", ProcurementStockCoverRow),
            ("lots", StockLotRow), ("audit", AuditEventRow), ("outbox", OutboxMessageRow),
        )
    }


@pytest.mark.parametrize("action", ["reserve", "release", "cancel", "bind"])
def test_late_failure_rolls_back_stock_cover_transaction(env, monkeypatch, action):
    client, sessions = env
    line = line_in_work(client, sessions)
    lot = add_lot(sessions, stock("100"))
    if action != "reserve":
        response = cover_without_lot(client, line, "80") if action == "bind" else cover(client, line, lot, "80")
        assert response.status_code == 200, response.text
        line = response.json()["line"]
        cover_id = response.json()["cover"]["id"]
        assert line["to_buy"] == "44"  # Cancellation must release a partly covered line.
    with sessions() as session:
        before = snapshot(session.connection())

    def fail_after_flush(uow):
        uow.session.flush()
        flushed = snapshot(uow.session.connection())
        for name in ("lines", "covers", "lots", "audit"):
            assert flushed[name] != before[name], f"{name} was not changed before the failure"
        expected = Decimal("80") if action in {"reserve", "bind"} else Decimal("0")
        assert flushed["lines"][0]["stock_covered"] == expected
        assert flushed["covers"][0]["quantity"] == expected
        assert flushed["lots"][0]["reserved"] == expected
        raise RuntimeError("injected failure after flush")

    monkeypatch.setattr(SqlAlchemyProcurementStockUnitOfWork, "commit", fail_after_flush)
    with pytest.raises(RuntimeError, match="injected failure after flush"):
        if action == "reserve":
            cover(client, line, lot, "80")
        elif action == "bind":
            bind(client, line, cover_id, lot)
        else:
            suffix = "stock-cover/release" if action == "release" else "cancel"
            client.post(f"/api/procurement/{line['id']}/{suffix}", json={"version": line["version"]})

    with sessions() as session:
        assert snapshot(session.connection()) == before

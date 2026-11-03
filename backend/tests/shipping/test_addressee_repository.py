from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.modules.shipping.domain import DeliveryTerms, ProjectAddressee, UpdType
from app.modules.shipping.infrastructure import ProjectAddresseeRow, SqlAlchemyProjectAddresseeRepository
from app.shared.persistence import create_schema


@pytest.mark.parametrize("projects, expected", [
    (None, [f"P{index:02}" for index in range(12)]),
    (["P03", "P01", "MISSING", "P03"], ["P01", "P03"]),
    ([], []),
])
def test_addressee_list_loads_only_requested_rows_after_resolving_keys(projects, expected):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    cards = {f"P{index:02}": ProjectAddressee(
        project=f"P{index:02}", version=index + 1, receiver=f"Получатель {index}", shipping_point="Москва",
        contact="Иван", phone="+7", terms=DeliveryTerms.CARRIER, carrier="СДЭК", upd_type=UpdType.EDO,
        updated_by="Менеджер", updated_at=datetime(2026, 11, 2, 10), po="Заказ", plan_date=date(2026, 11, 5),
        note="Не кантовать", upd_number="УПД-1", upd_date=date(2026, 11, 2), upd_url="/external/upd.pdf",
        pack_decided_by="Менеджер", pack_decided_at=datetime(2026, 11, 2, 10),
    ) for index in range(12)}
    with Session(engine) as session:
        repository = SqlAlchemyProjectAddresseeRepository(session)
        for card in cards.values():
            repository.add(card)
        session.commit()

    statements, loaded = [], []
    def sql(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)
    def load(row, _context):
        loaded.append(row.project)
    event.listen(engine, "before_cursor_execute", sql)
    event.listen(ProjectAddresseeRow, "load", load)
    try:
        with Session(engine) as session:
            repository = SqlAlchemyProjectAddresseeRepository(session)
            found = repository.list() if projects is None else repository.list(projects)
        assert [card.project for card in found] == expected
        assert found == [cards[project] for project in expected]
        assert loaded == expected
        assert len(statements) == (1 if projects is None else 2 if projects else 0)
    finally:
        event.remove(engine, "before_cursor_execute", sql)
        event.remove(ProjectAddresseeRow, "load", load)
        engine.dispose()

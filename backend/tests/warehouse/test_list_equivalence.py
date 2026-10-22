"""The fast stock list (SQL page + narrow search scan, memoized folding), the fast journal and the export read give
exactly what the old code gave: the old algorithms are kept here as the reference and run over the same fixtures. The search reference is the word
search with the owner's word-start rule for words without digits."""

import random
import re
import unicodedata
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.warehouse.application import warehouse_lots
from app.modules.warehouse.domain import StockLot, WriteOffEntry
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork, StockLotRow, _domain
from app.modules.warehouse.journal import journal_page, warehouse_journal
from app.shared.events import AuditEvent
from app.shared.persistence import create_schema
from tests.factories import Actor

# ── the old code, the same behaviour ───────────────────────────────────────────────────────────────────────────
_OLD_LOOKALIKES = str.maketrans({
    "ё": "е", "a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м", "o": "о", "p": "р", "t": "т",
    "x": "х", "y": "у", "×": "х",
})
_OLD_COMMA = re.compile(r"(?<=\d),(?=\d)")
_OLD_SEPARATORS = re.compile(r"[\s.\-/()_]")


def _old_fold(value) -> str:
    return _OLD_COMMA.sub(".", unicodedata.normalize("NFKC", str(value or "")).casefold().translate(_OLD_LOOKALIKES))


def _old_article_key(value) -> str:
    key = _OLD_SEPARATORS.sub("", _old_fold(value))
    return key.lstrip("0") if re.fullmatch(r"0\d{9}", key) else key


def _old_found(word, text) -> bool:
    if any(char.isdigit() for char in word):
        return word in text
    return re.search(rf"(?<![^\W_]){re.escape(word)}", text) is not None


def _old_matches_words(lot, query) -> bool:
    words = _old_fold(query).split()
    if not words:
        return True
    package, grn = str(lot.package or ""), str(lot.grn or "")
    forms = [grn, package] + ([f"{grn}-{package}", f"{grn} {package}"] if package else [])
    fields = tuple(_old_fold(t) for t in (*forms, lot.name, lot.storage_location, lot.series, lot.purpose, lot.item_type))
    article, key = _old_fold(lot.article), _old_article_key(lot.article)
    for word in words:
        word_key = _old_article_key(word) if any(char.isdigit() for char in word) else ""
        if _old_found(word, article) or (word_key and word_key in key):
            continue
        if not any(_old_found(word, field) for field in fields):
            return False
    return True


def _old_page(session, limit, after_id, query, warehouse, include_zero):
    filters = []
    if warehouse:
        filters.append(StockLotRow.warehouse == warehouse)
    if not include_zero:
        filters.append(StockLotRow.inbound - StockLotRow.written_off > 0)
    statement = select(StockLotRow).where(*filters)
    if after_id is not None:
        statement = statement.where(StockLotRow.id > after_id)
    statement = statement.order_by(StockLotRow.id)
    if not str(query or "").strip():
        statement = statement.limit(limit)
    lots = [_domain(row) for row in session.scalars(statement)]
    if query:
        lots = [lot for lot in lots if _old_matches_words(lot, query)]
    everything = [_domain(row) for row in session.scalars(select(StockLotRow).where(*filters))]
    total = (sum(_old_matches_words(lot, query) for lot in everything) if str(query or "").strip()
             else len(everything))
    return lots[:limit], total


# ── fixtures ────────────────────────────────────────────────────────────────────────────────────────────────────
NAMES = ["Разъём Molex 0,35", "Разъем JST 2.54 мм", "Провод МГТФ 0.35 белый", "Кабель RG316 200х3.6", "Крепёж A1B2",
         "Резистор СТ", "Термоусадка 3:1 чёрная", "Cрок EAЭC", "Винт М3×10", "Флюс"]
ARTICLES = ["0430250600", "43-0250-600", "GHR-04V-S", "SMA-J-RG316", "R-001", "А1В2", "fts", "0012345678"]


@pytest.fixture()
def uow_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    rng = random.Random(7)
    with SqlAlchemyWarehouseUnitOfWork(sessions) as uow:
        for i in range(260):
            lot = StockLot.create(
                warehouse="ЦАР" if i % 3 else "ЖУК", grn=f"{rng.choice(['FRX', 'PPM', 'OLD'])}.26.{i:07d}",
                package=rng.choice(["", "", "П1", "П2", "P3"]), article=rng.choice(ARTICLES) + str(i % 4 or ""),
                name=rng.choice(NAMES), series=rng.choice(["", "LOT-77", "Lot-2409"]),
                item_type=rng.choice(["Разъёмы", "Провод", "Комплектующие"]),
                purpose=rng.choice(["FRX.001", "Склад", "ВП-001"]), unit="шт", quantity=Decimal(rng.randint(1, 50)),
                storage_location=f"{rng.randint(1, 9)}{rng.choice('ABCF')}{rng.randint(1, 30)}",
                source_receiving_ids=(f"r{i}",),
            )
            if i % 5 == 0:
                lot = lot.write_off(lot.inbound)  # zero balance
            uow.warehouse.add(lot)
        uow.commit()
    yield (lambda: SqlAlchemyWarehouseUnitOfWork(sessions)), sessions
    engine.dispose()


QUERIES = ["", "   ", "разъем", "РАЗЪЁМ molex", "0,35", "0.35", "43-0250-600", "430250600", "frx.26.00001", "П1", "p3",
           "crok", "срок еаэс", "a1b2", "м3х10", "lot-77 провод", "zzz", "1a", "склад 2", "ст резистор", "fts", "ъем",
           "olex", "ghr", "hr", "j-rg", "мгтф бел"]


def _ids(lots):
    return [str(lot.id) for lot in lots]


def test_every_page_and_total_match_the_old_list(uow_factory):
    factory, sessions = uow_factory
    for query in QUERIES:
        for warehouse in (None, "ЦАР", "ЖУК"):
            for include_zero in (False, True):
                after, seen = None, 0
                while True:
                    new, new_total = warehouse_lots(factory(), limit=37, after_id=after, query=query,
                                                    warehouse=warehouse, include_zero=include_zero)
                    with sessions() as session:
                        old, old_total = _old_page(session, 37, after, query, warehouse, include_zero)
                    assert new == old, (query, warehouse, include_zero, after)
                    assert new_total == old_total, (query, warehouse, include_zero)
                    seen += len(new)
                    if len(new) < 37:
                        break
                    after = new[-1].id
                assert seen == old_total
                with factory() as uow:
                    assert uow.warehouse.count(query, warehouse, include_zero) == old_total
                    assert _ids(uow.warehouse.list(500, None, query, warehouse, include_zero)) == _ids(
                        _old_page(uow.session, 500, None, query, warehouse, include_zero)[0])


def test_the_journal_without_a_search_matches_the_full_merge(uow_factory):
    factory, _ = uow_factory
    actor = Actor(name="Кладовщик", department="Склад")
    start = datetime(2026, 9, 1, 8, 0)
    with factory() as uow:
        lots = uow.warehouse.every()
        for i, lot in enumerate(lots[:150]):
            when = start + timedelta(minutes=i // 3)  # ties on purpose
            uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=lot.id, event_type="warehouse.moved", actor=actor,
                                     occurred_at=when, details={"from": "1A1", "to": lot.storage_location}))
            uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=lot.id, event_type="warehouse.written_off", actor=actor,
                                     occurred_at=when, details={"quantity": "1"}))
            uow.writeoffs.add(WriteOffEntry.for_lot(
                lot, kind="writeoff" if i % 4 else "return", quantity=Decimal("1"), reason="Выдача", purpose="FRX.001",
                author="Петров", actor_id=actor.id, actor_name=actor.name, occurred_at=when, remaining_after=None,
            ))
        uow.commit()
    with factory() as uow:
        reference = journal_page(uow.journal.audit_events(), uow.journal.writeoff_entries(), "", 40)
        searched = journal_page(uow.journal.audit_events(), uow.journal.writeoff_entries(), "возврат", 40)
    assert warehouse_journal(factory(), "", 40) == reference
    assert warehouse_journal(factory(), "  ", 40) == reference
    assert warehouse_journal(factory(), "возврат", 40) == searched
    assert reference.total == 300 and reference.truncated == 260


def test_the_export_order_is_unchanged(uow_factory):
    factory, sessions = uow_factory
    with factory() as uow:
        fast = uow.warehouse.every()
    with sessions() as session:
        rows = session.scalars(select(StockLotRow).order_by(StockLotRow.grn, StockLotRow.package))
        old = sorted((_domain(row) for row in rows), key=lambda lot: {"ЦАР": 0, "ЖУК": 1}[lot.warehouse])
    assert fast == old

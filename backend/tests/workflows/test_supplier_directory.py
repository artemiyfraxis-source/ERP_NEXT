"""PR-12: ReferencesSupplierDirectory, the Procurement → References «ссылка → поставщик» port."""

from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.references.application import ReferencePair, ReplaceMapsCommand, replace_maps
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows.supplier_directory import ReferencesSupplierDirectory
from tests.factories import Actor

NOW = datetime(2026, 9, 29, 12, 0)


def _directory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    replace_maps(
        ReplaceMapsCommand(
            suppliers=(ReferencePair("https://www.chipdip.ru", "ЧипДип"),),
            countries=(ReferencePair("Amphenol", "США"),),
            gost_awg=(),
            actor=Actor(name="Админ", department="ИТ"),
            now=NOW,
        ),
        factory(),
    )
    return ReferencesSupplierDirectory(factory), factory, sessions


def test_supplier_map_is_keyed_by_domain():
    directory, _, _ = _directory()
    assert directory.supplier_map() == {"chipdip.ru": "ЧипДип"}


def test_learn_adds_a_new_domain_once_and_keeps_other_maps():
    directory, factory, sessions = _directory()
    buyer = Actor(name="Петров", department="Закупки")

    assert directory.learn("https://www.elitan.ru/item/5", "Элитан", buyer, NOW) is True
    assert directory.learn("https://elitan.ru/other", "Другой", buyer, NOW) is False  # existing key wins
    assert directory.learn("https://chipdip.ru/x", "Не ЧипДип", buyer, NOW) is False
    assert directory.learn(None, "Элитан", buyer, NOW) is False
    assert directory.learn("Склад", "Склад", buyer, NOW) is False  # free text, not a link: nothing to learn (owner 2026-10-04)

    assert directory.supplier_map() == {"chipdip.ru": "ЧипДип", "elitan.ru": "Элитан"}
    with factory() as uow:
        countries = [entry for entry in uow.references.list() if entry.kind.value == "country"]
    assert [entry.value for entry in countries] == ["США"]
    with sessions() as session:
        learned = session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "references.supplier_learned")
        ).all()
    assert len(learned) == 1


def test_country_for_manufacturer_ignores_case_and_spaces():
    """`countryForManufacturer_`: СТРАНА by ИЗГОТОВИТЕЛЬ, key normalised like the map's."""
    directory, _, _ = _directory()
    assert directory.country_for_manufacturer("  amphenol ") == "США"
    assert directory.country_for_manufacturer("Molex") == ""
    assert directory.country_for_manufacturer("") == ""

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.modules.production.domain import ProductionValidationError
from app.modules.production.traceability import InstrumentKind, InstrumentStatus, MeasurementInstrument


@pytest.mark.parametrize("status", list(InstrumentStatus))
def test_accessory_never_authorizes_measurement_even_with_forged_verification(status):
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    accessory = MeasurementInstrument(
        id=uuid4(), version=1, inventory_number="СИ.002", name="Переходник", instrument_type="Переходник",
        verification_number=None, verified_at=None, verification_due=None, status=status,
        kind=InstrumentKind.ACCESSORY, parent_id=uuid4(),
    )
    for instrument in (accessory, replace(accessory, verification_number="ПВ", verified_at=now, verification_due=now)):
        with pytest.raises(ProductionValidationError, match="СИ.002 — принадлежность, не средство измерений"):
            instrument.validate_for(now)


def test_sqlite_serializes_concurrent_reparenting_before_reading_tree(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.modules.production.infrastructure import SqlAlchemyInstrumentRepository, SqlAlchemyProductionUnitOfWork
    from app.modules.production.quality import RegisterInstrumentCommand, UpdateInstrumentCommand, register_instrument, update_instrument
    from app.shared.persistence import create_schema
    from tests.factories import Actor

    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'tree.db').as_posix()}", connect_args={"check_same_thread": False})
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    actor = Actor(id=uuid4(), name="ОТК", department="ОТК")
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    first, second = [register_instrument(RegisterInstrumentCommand(
        inventory_number=f"СИ.{number:03d}", name="Тестер", instrument_type="Тестер", verification_number=None,
        verified_at=None, verification_due=None, status=InstrumentStatus.UNVERIFIED, actor=actor, now=now,
    ), factory()) for number in (1, 2)]
    first_validated, second_attempted, second_locked, release_first = Event(), Event(), Event(), Event()
    original_save = SqlAlchemyInstrumentRepository.save
    original_lock = SqlAlchemyInstrumentRepository.lock_tree

    def save(repository, item):
        if item.id == first.id:
            first_validated.set()
            assert release_first.wait(5)
        return original_save(repository, item)

    def lock(repository):
        is_second = first_validated.is_set()
        if is_second:
            second_attempted.set()
        original_lock(repository)
        if is_second:
            second_locked.set()

    monkeypatch.setattr(SqlAlchemyInstrumentRepository, "save", save)
    monkeypatch.setattr(SqlAlchemyInstrumentRepository, "lock_tree", lock)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(update_instrument, UpdateInstrumentCommand(first.id, 1, {"parent_id": second.id}, actor, now), factory())
            assert first_validated.wait(5)
            b = pool.submit(update_instrument, UpdateInstrumentCommand(second.id, 1, {"parent_id": first.id}, actor, now), factory())
            try:
                assert second_attempted.wait(5)
                assert not second_locked.wait(0.2), "The second writer read the tree before the first committed"
            finally:
                release_first.set()
            assert a.result(timeout=5).parent_id == second.id
            with pytest.raises(ProductionValidationError, match="Циклическая"):
                b.result(timeout=5)
        with factory() as uow:
            assert uow.instruments.get(second.id).parent_id is None
    finally:
        release_first.set()
        engine.dispose()

"""One-time registry proposals: python -m app.workflows.rf_interfaces_fill [--apply]."""

import argparse
from dataclasses import asdict
import json
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.production.infrastructure import SqlAlchemyInstrumentRepository
from app.modules.production.rf_infrastructure import RfRepository
from app.modules.production.rf_interfaces import propose_details


def fill(session: Session, *, apply=False, emit=print) -> int:
    instruments = SqlAlchemyInstrumentRepository(session)
    if apply:
        instruments.lock_tree()
    repository = RfRepository(session)
    existing = repository.list()
    count = 0
    for item in instruments.list():
        current = existing.get(item.id)
        if current is not None:
            emit(f"{item.inventory_number} · {item.name}: {'подтверждено' if current.confirmed else 'данные уже внесены'} — без изменений")
            continue
        proposal = propose_details(item.inventory_number, item.name)
        emit(f"{item.inventory_number} · {item.name}: {json.dumps(asdict(proposal), ensure_ascii=False)}")
        if proposal.interfaces or proposal.role != "unknown":
            count += 1
            if apply:
                repository.save(item.id, proposal)
    return count


def main():
    parser = argparse.ArgumentParser(description="Предложить ВЧ-интерфейсы реестра; по умолчанию без записи")
    parser.add_argument("--apply", action="store_true", help="Записать предложения для записей без ВЧ-данных; геометрия АКМ уже подтверждена владельцем")
    args = parser.parse_args()
    url = os.getenv("ERP_DATABASE_URL")
    if not url:
        parser.error("Укажите ERP_DATABASE_URL")
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            count = fill(session, apply=args.apply)
            if args.apply:
                session.commit()
            print(f"{'Записано' if args.apply else 'Предложено'}: {count}; проверьте неподтверждённые интерфейсы и рабочие полосы")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

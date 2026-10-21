"""Выпуски комплекта ТД задним числом: каждой выданной ЭТК без выпуска — выпуск по правилам автоматического
(app/workflows/td_auto.py: поля из последнего выпуска или по умолчанию, утверждение, если нормоконтроль не блокирует).

    cd backend; ERP_DATABASE_URL=… python -m app.modules.techcards.td_backfill            # показать, ничего не писать
    cd backend; ERP_DATABASE_URL=… python -m app.modules.techcards.td_backfill --apply    # записать

По умолчанию — пробный прогон (как --dry-run): каждая карта считается в своей транзакции и откатывается. С --apply
каждая карта фиксируется отдельно: сбой одной не мешает другим. Печатает строку на карту: что создано, статус и коды
блокирующих замечаний.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.shared.clock import moscow_now
from app.workflows.td_auto import AutoResult, auto_issue

from .infrastructure import SqlAlchemyTechcardsUnitOfWork
from .project_cards import TechcardStatus

REASON = "Первая выдача"


@dataclass(frozen=True)
class CardLine:
    label: str
    result: AutoResult | None
    error: str = ""

    def text(self) -> str:
        if self.error:
            return f"{self.label}: ошибка — {self.error}"
        return f"{self.label}: {self.result.text() if self.result else 'без выпуска'}"


def backfill(sessions: sessionmaker[Session], *, apply: bool = False, now: datetime | None = None) -> list[CardLine]:
    """Выданные карты без единого выпуска ТД → строка итога на карту; `apply` — записать."""
    now = now or moscow_now()
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        cards = [card for card in uow.techcards.list()
                 if card.status is TechcardStatus.ISSUED and card.document is not None and card.revision > 0]
        todo = [card.id for card in cards if not uow.td_issues.for_card(card.id)]
    lines: list[CardLine] = []
    for card_id in todo:
        with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
            card = uow.techcards.get(card_id)
            label = f"{card.project} {card.item_index}" if card is not None else str(card_id)
            try:
                result = auto_issue(uow, uow.session, card, REASON, now)
            except Exception as error:  # noqa: BLE001 — одна карта не останавливает остальные
                lines.append(CardLine(label, None, str(error) or type(error).__name__))
                continue
            if apply:
                uow.commit()
            lines.append(CardLine(label, result))
    return lines


def main(argv: list[str] | None = None, *, sessions_of: Callable[[str], sessionmaker[Session]] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Выпуски комплекта ТД для выданных ЭТК без выпуска")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="только показать (по умолчанию)")
    mode.add_argument("--apply", action="store_true", help="записать выпуски")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url and sessions_of is None:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    engine = None
    if sessions_of is None:
        from app.database import build_engine

        engine = build_engine(url)
        sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    else:
        sessions = sessions_of(url)
    try:
        lines = backfill(sessions, apply=args.apply)
    finally:
        if engine is not None:
            engine.dispose()
    print(("Записано" if args.apply else "Пробный прогон, ничего не записано") + f": карт без выпуска ТД — {len(lines)}")
    for line in lines:
        print(line.text())
    return 1 if any(line.error for line in lines) else 0


if __name__ == "__main__":
    sys.exit(main())

"""Перевыпуск комплектов ТД под кодом организации ЮТНВ (владелец 22.10.2026: «начало дец. номера ЮТНВ»; «разраб
Васильев везде, Н. контроль Зингер, а утверждает Костромин»).

    cd backend; ERP_DATABASE_URL=… python -m app.workflows.td_org_reissue            # показать, ничего не писать
    cd backend; ERP_DATABASE_URL=… python -m app.workflows.td_org_reissue --dry-run  # то же
    cd backend; ERP_DATABASE_URL=… python -m app.workflows.td_org_reissue --yes      # перевыпустить

Выпущенный документ на месте не правится. Каждой карте, чей действующий выпуск под старым кодом (ФРАК), открывается
изменение тем же путём, что у автоматического выпуска (app/workflows/td_auto.py): номер изменения — следующий,
порядковый номер — тот же, код — td_issues.ORG_CODE, снимок и ревизия ЭТК, литера, вид ТП, цех и участок — прежнего
выпуска; Разраб., Н. контр. и Утв. — подписанты по умолчанию (td_auto.default_signers), Пров. и «Согласовано ВП» —
прежние; извещение «ИИ-<порядковый:05d>-<изменение>», причина — REASON; автор — «система». Затем нормоконтроль: без
блокирующих замечаний выпуск проходит «на подписи» → «действует», прежний действующий аннулируется в той же транзакции.
С блокирующими замечаниями или с выпуском карты в работе (проект / на подписи) карта пропускается и попадает в список.

Каждая карта — своя транзакция: сбой одной не мешает другим. Пробный прогон считает то же в памяти и не пишет в базу
ничего. Повторный запуск ничего не делает: действующих выпусков под старым кодом не остаётся. Печатает на карту строку
«было → стала» по обозначению ВТД и строку подписантов.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.td_issues import (
    ORG_CODE, ROLE_LABELS, STATUS_LABELS, SYSTEM_ACTOR, TdIssue, TdIssueInput, normcontrol, with_signers,
)
from app.shared.clock import moscow_now
from app.workflows.td_auto import approve_issue, default_signers

OLD_ORG = "ФРАК"
REASON = f"Код организации {ORG_CODE} вместо {OLD_ORG}, подписанты по умолчанию"
_SHOWN_ROLES = ("developer", "checker", "normcontrol", "approver")


@dataclass(frozen=True)
class ReissueLine:
    """Итог по карте: outcome — reissued / planned / skipped / error."""

    card: str
    before: TdIssue
    after: TdIssue | None
    outcome: str
    note: str = ""

    def text(self) -> str:
        was = f"{self.before.designations['vtd']} изм. {self.before.change_no}"
        if self.after is None:
            head = f"{self.card}: {was} — {'ошибка' if self.outcome == 'error' else 'пропущена'}: {self.note}"
            return head
        verb = "перевыпущена" if self.outcome == "reissued" else "будет перевыпущена"
        head = (f"{self.card}: {was} → {self.after.designations['vtd']} изм. {self.after.change_no} — {verb} "
                f"({STATUS_LABELS['active']}), прежний — {STATUS_LABELS['annulled']}")
        people = []
        for role in _SHOWN_ROLES:
            old = (self.before.signatories.get(role) or {}).get("name", "") or "—"
            new = (self.after.signatories.get(role) or {}).get("name", "") or "—"
            people.append(f"{ROLE_LABELS[role]} {old}" + ("" if old == new else f" → {new}"))
        return head + "\n    " + "; ".join(people)


def reissued(active: TdIssue, existing: list[TdIssue], signers: dict[str, dict[str, str]], now: datetime) -> TdIssue:
    """Изменение карты под ORG_CODE (проект, ещё не записан): поля и снимок действующего выпуска, новые подписанты."""
    change_no = max(item.change_no for item in existing) + 1
    value = TdIssueInput(
        litera=active.litera, method_code=active.method_code, signatories=with_signers(active.signatories, signers),
        notice_no=f"ИИ-{active.sequence_no:05d}-{change_no}", reason=REASON, workshop=active.workshop, site=active.site,
    )
    issue = TdIssue.open(
        card_id=active.project_card_id, value=value, snapshot=active.snapshot, existing=existing,
        next_sequence=active.sequence_no, actor=SYSTEM_ACTOR, now=now,
    )
    if issue.sequence_no != active.sequence_no:
        raise ValueError(f"порядковый номер карты {issue.sequence_no} ≠ {active.sequence_no} действующего выпуска")
    return replace(issue, etk_revision=active.etk_revision)


def _blocking(issue: TdIssue) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item["code"] for item in normcontrol(issue) if item["severity"] == "blocking"))


def _one(uow: Any, issue_id, now: datetime, apply: bool) -> ReissueLine | None:
    active: TdIssue | None = uow.td_issues.get(issue_id)
    if active is None or active.status != "active" or active.org_code != OLD_ORG:
        return None  # уже перевыпущена (повторный запуск или параллельная правка)
    card = uow.techcards.get(active.project_card_id)
    label = f"{card.project} {card.item_index}" if card is not None else str(active.project_card_id)
    existing: list[TdIssue] = uow.td_issues.for_card(active.project_card_id)
    in_work = next((item for item in existing if item.status in {"draft", "signing"}), None)
    if in_work is not None:
        return ReissueLine(label, active, None, "skipped", (
            f"у карты выпуск в работе (изм. {in_work.change_no}, «{STATUS_LABELS[in_work.status]}») — "
            "утвердите или аннулируйте его и повторите"
        ))
    issue = reissued(active, existing, default_signers(uow.session), now)
    blocking = _blocking(issue)
    if blocking:
        return ReissueLine(label, active, None, "skipped", f"нормоконтроль блокирует: {', '.join(blocking)}")
    if not apply:
        return ReissueLine(label, active, replace(issue, status="active"), "planned")
    uow.td_issues.add(issue)
    uow.td_issues.flush()
    approved, blocking = approve_issue(uow, issue, now)
    if blocking or approved.status != "active":
        return ReissueLine(label, active, None, "skipped", f"нормоконтроль блокирует: {', '.join(blocking)}")
    uow.commit()
    return ReissueLine(label, active, approved, "reissued")


def reissue(sessions: sessionmaker[Session], *, apply: bool = False, now: datetime | None = None) -> list[ReissueLine]:
    """Действующие выпуски под OLD_ORG → строка итога на карту; `apply` — записать (каждая карта отдельно)."""
    now = now or moscow_now()
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        todo = [issue.id for issue in uow.td_issues.active_of_org(OLD_ORG)]
    lines: list[ReissueLine] = []
    for issue_id in todo:
        with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
            try:
                line = _one(uow, issue_id, now, apply)
            except Exception as error:  # noqa: BLE001 — одна карта не останавливает остальные
                uow.session.rollback()
                before = uow.td_issues.get(issue_id)
                card = uow.techcards.get(before.project_card_id) if before is not None else None
                label = f"{card.project} {card.item_index}" if card is not None else str(issue_id)
                line = ReissueLine(label, before, None, "error", str(error) or type(error).__name__) if before else None
            if line is not None:
                lines.append(line)
    return lines


def main(argv: list[str] | None = None, *, sessions_of: Callable[[str], sessionmaker[Session]] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=f"Перевыпуск комплектов ТД под кодом {ORG_CODE} вместо {OLD_ORG} с подписантами по умолчанию. "
                    "Без --yes только показывает.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="только показать (по умолчанию)")
    mode.add_argument("--yes", action="store_true", help="перевыпустить по-настоящему")
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
        lines = reissue(sessions, apply=args.yes)
    finally:
        if engine is not None:
            engine.dispose()
    done = sum(line.outcome in {"reissued", "planned"} for line in lines)
    skipped = sum(line.outcome == "skipped" for line in lines)
    errors = sum(line.outcome == "error" for line in lines)
    head = "Записано" if args.yes else "Пробный прогон, ничего не записано"
    print(f"{head}: действующих выпусков {OLD_ORG} — {len(lines)}; "
          f"{'перевыпущено' if args.yes else 'перевыпустить'} — {done}, пропущено — {skipped}, ошибок — {errors}")
    for line in lines:
        print(line.text())
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

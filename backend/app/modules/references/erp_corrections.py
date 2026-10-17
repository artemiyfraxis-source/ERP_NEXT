"""Правки справочников «Технология», которые ERP вносит сама поверх зеркала SUPPLYMAP.

    ERP_DATABASE_URL=... python -m app.modules.references.erp_corrections

Правка — как «Записать в БД» человеком (`edit_record`, запись помечается `edited_in_erp`, перезагрузка зеркала её
не затирает). Применяется, только пока запись та же, что пришла из зеркала: запись, уже правленная в ERP, и поле,
значение которого не то, что ожидалось, не трогаются. Повторный запуск ничего не меняет (update-local, сервер).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.shared.identity import Actor

from .application import EditRecordCommand, FieldEdit, StaleRecordError, edit_record
from .domain import TechOperationTab

ACTOR = Actor(id=UUID(int=0), name="ERP Integration", department="System")


@dataclass(frozen=True)
class Correction:
    tab: TechOperationTab
    key_field: str  # поле-ключ записи (схема генератора: terArticle…)
    key: str
    column: str  # колонка листа; её значение генератор берёт в schema_field
    schema_field: str
    old: str
    value: str
    why: str


CORRECTIONS: tuple[Correction, ...] = (
    # Резка с обжимом и лужением (CUT_WIRE+1crimp+tin) идёт только на терминал с аппликатором автомата. У близнеца
    # SSH-003T-P0.2 он есть (A19), а у SSH-003T-P0.2-H строка БД.ТЕР без него, хотя у обоих один аппликатор JST
    # APLMK SSH/L003-02 и одинаковая высота обжима (RUNLOG 30.09, «решено агентом — проверить»).
    Correction(TechOperationTab.TERMINAL, "terArticle", "SSH-003T-P0.2-H", "Аппликатор", "terApplicator",
               "S9/M1", "S9/M1/A19", "аппликатор автомата A19 — как у SSH-003T-P0.2 (один аппликатор JST)"),
)


def apply_corrections(factory, now: datetime, corrections: tuple[Correction, ...] = CORRECTIONS) -> list[str]:
    report: list[str] = []
    for item in corrections:
        with factory() as uow:
            records = [record for record in uow.tech_operations.list(tab=item.tab, query="", limit=100_000)
                       if str(record.fields.get(item.key_field, "")).strip() == item.key]
        if len(records) != 1:
            report.append(f"{item.key}: записей {len(records)}, правка не нужна или неоднозначна")
            continue
        record = records[0]
        field = item.column if item.column in record.fields else item.schema_field
        current = str(record.fields.get(field, "")).strip()
        if current == item.value:
            continue
        if record.edited_in_erp or current != item.old:
            report.append(f"{item.key}: {field} = «{current}» (правлено в ERP или не «{item.old}») — не трогаю")
            continue
        try:
            edit_record(EditRecordCommand(record.id, (FieldEdit(field, item.old, item.value),), ACTOR, now), factory())
        except StaleRecordError as error:
            report.append(f"{item.key}: {error}")
            continue
        report.append(f"{item.key}: {field} «{item.old}» → «{item.value}» ({item.why})")
    return report


def main() -> None:
    database_url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Set ERP_DATABASE_URL before applying reference corrections")
    report = apply_corrections(build_references_uow_factory(build_engine(database_url)), datetime.now(timezone.utc))
    print("Правки справочников: " + ("; ".join(report) if report else "без изменений"))


if __name__ == "__main__":
    main()

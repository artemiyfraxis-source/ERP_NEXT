"""Счётчики до и после записи шаблонов 25.10.2026 (репетиция на копии прод-БД и боевой запуск).

    ERP_DATABASE_URL=... python check.py

Ожидание после записи: кодов +3 (SDR_QS8, SDR_SA, INS_housing_SA), у каждого строка БД.ОП; INS_MARK_TUBE_AUTO в библиотеке
и в БД.ОП — 0, PRINT_MARK_TUBE — по 1; id карты маркировочной трубки прежний. Ссылки на прежний код в ЭТК и черновиках
«Ручной сборки» (первые строки вывода, считать ДО записи) после записи остаются теми же документами: в документах код
хранится как был, карта находится по templateId, прежний код читает только рисунок эскиза.
"""
import json
import os

from sqlalchemy import create_engine, text

NEW = ("SDR_QS8", "SDR_SA", "INS_housing_SA")
OLD, NEWMARK = "INS_MARK_TUBE_AUTO", "PRINT_MARK_TUBE"
AUTHOR = "Аудит ГОСТ Р 25.10.2026"
MARK_ID = "ins-mark-tube-auto-печать-и-резка-маркировочной-трубки"

engine = create_engine(os.environ["ERP_DATABASE_URL"])
with engine.connect() as db:
    def one(sql, **params):
        return db.execute(text(sql), params).scalar()

    def like(column, needle):
        return f"coalesce(cast({column} as text), '') like '%{needle}%'"

    print("ссылки на прежний код", OLD, "(до записи; после неё те же):")
    print("  ЭТК с кодом в текущем документе:", one("select count(*) from techcard_projects where " + like("document", OLD)))
    print("  из них выпущенных (status = issued):",
          one("select count(*) from techcard_projects where status = 'issued' and " + like("document", OLD)))
    print("  ЭТК с кодом в конфиге генератора (inputs):", one("select count(*) from techcard_projects where " + like("inputs", OLD)))
    print("  ревизий ЭТК с кодом:", one("select count(*) from techcard_revisions where " + like("document", OLD)))
    print("  черновиков «Ручной сборки» с кодом (draft_config / final_config):",
          one("select count(*) from techcard_assembly_drafts where " + like("draft_config", OLD) + " or " + like("final_config", OLD)))
    print("  ... со ссылкой на карту по templateId:",
          one("select count(*) from techcard_assembly_drafts where " + like("draft_config", MARK_ID) + " or " + like("final_config", MARK_ID)))
    print("шаблонов:", one("select count(*) from techcard_operation_templates"),
          "| кодов:", one("select count(distinct code) from techcard_operation_templates"),
          "| правлены аудитом 25.10:", one("select count(distinct code) from techcard_operation_templates "
                                          "where edited_by = :author", author=AUTHOR))
    rows = [json.loads(value) if isinstance(value, str) else value
            for value in db.execute(text("select fields from tech_operation_records where tab = 'op'")).scalars()]
    numbers = [str((fields or {}).get("opNumber") or "").strip() for fields in rows]
    codes = ", ".join(f"'{code}'" for code in NEW)
    print("новых кодов в библиотеке:", one(f"select count(distinct code) from techcard_operation_templates where code in ({codes})"),
          "из", len(NEW), "| со строкой БД.ОП:", len(set(numbers) & set(NEW)), "из", len(NEW))
    for fields in rows:
        if str((fields or {}).get("opNumber") or "").strip() in NEW:
            print("  строка БД.ОП:", fields.get("opNumber"), "| tOp", fields.get("tOp"), "| tPrep", fields.get("tPrep"))
    print("маркировочная трубка: карт под", OLD, "—", one("select count(*) from techcard_operation_templates where code = :c", c=OLD),
          "| под", NEWMARK, "—", one("select count(*) from techcard_operation_templates where code = :c", c=NEWMARK),
          "| строк БД.ОП под", OLD, "—", numbers.count(OLD), "| под", NEWMARK, "—", numbers.count(NEWMARK))
    print("  карта по id:", db.execute(text("select id, code, title from techcard_operation_templates where id = :i"),
                                      {"i": MARK_ID}).fetchall())
    print("события аудита 25.10:", one("select count(*) from audit_events where event_type in ('techcards.operation_record_edited', "
                                       "'techcards.operation_template_created', 'techcards.operation_template_renamed') "
                                       "and actor_name = :author", author=AUTHOR))
    print("карт ЭТК:", one("select count(*) from techcard_projects"),
          "| ожидают пересчёта:", one("select count(*) from techcard_refresh_requests where processed_at is null"))

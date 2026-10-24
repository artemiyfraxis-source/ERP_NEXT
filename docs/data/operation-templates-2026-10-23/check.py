"""Счётчики до и после записи шаблонов 23.10.2026 (репетиция на копии прод-БД и боевой запуск).

    ERP_DATABASE_URL=... python check.py

Ожидание после записи: кодов +10 (новые шаблоны), правлены аудитом 23.10 — все коды payload, кроме неизменённых;
новых кодов без строки БД.ОП — 0; запрос пересчёта ЭТК — один.
"""
import json
import os

from sqlalchemy import create_engine, text

NEW = ("LASER_MARK", "PRINT_MARK", "INS_MARK_AUTO", "PRS_TERMINAL_press", "SDR_MR30", "TWIST_BAY", "TWST_SHILED",
       "INS_TAPE_AUTO", "Раскладка", "Handjob")
AUTHOR = "Аудит ГОСТ Р 23.10.2026"

engine = create_engine(os.environ["ERP_DATABASE_URL"])
with engine.connect() as db:
    def one(sql, **params):
        return db.execute(text(sql), params).scalar()

    codes = ", ".join(f"'{code}'" for code in NEW)
    print("шаблонов:", one("select count(*) from techcard_operation_templates"),
          "| кодов:", one("select count(distinct code) from techcard_operation_templates"),
          "| правлены аудитом 23.10:", one("select count(distinct code) from techcard_operation_templates "
                                          "where edited_by = :author", author=AUTHOR),
          "| правлены аудитом 22.10:", one("select count(distinct code) from techcard_operation_templates "
                                          "where edited_by like 'Аудит ГОСТ Р 22.10%'"))
    rows = [json.loads(value) if isinstance(value, str) else value
            for value in db.execute(text("select fields from tech_operation_records where tab = 'op'")).scalars()]
    numbers = {str((fields or {}).get("opNumber") or "").strip() for fields in rows}
    print("новых кодов в библиотеке:", one(f"select count(distinct code) from techcard_operation_templates where code in ({codes})"),
          "из", len(NEW), "| из них со строкой БД.ОП:", len(numbers & set(NEW)), "из", len(NEW))
    print("событий аудита 23.10:", one("select count(*) from audit_events where event_type in "
                                       "('techcards.operation_record_edited', 'techcards.operation_template_created') "
                                       "and actor_name = :author", author=AUTHOR))
    print("карт ЭТК:", one("select count(*) from techcard_projects"),
          "| с новыми кодами в документе:",
          one("select count(*) from techcard_projects where " + " or ".join(
              f"coalesce(cast(document as text), '') like '%\"{code}\"%'" for code in NEW)))
    print("ожидают пересчёта:", one("select count(*) from techcard_refresh_requests where processed_at is null"))

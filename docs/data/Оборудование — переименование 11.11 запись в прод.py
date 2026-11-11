"""Переименование 27 моделей / 35 единиц БД.ОБ; только имя, производитель,
производные подписи и подтверждение существующей роли operational.
Артикулы и замечания CSV не исправляются.

Сначала репетиция на копии prod без refresh-worker:
  etk_refresh --dry-run --all -> apply этого скрипта -> etk_refresh --dry-run --all.

На prod, из /opt/erp/app/deploy, сохранив файл как equipment-rename.py:
Перед apply приостановить записи справочников и дождаться окончания активных
запросов: обычный FieldEdit приложения не является атомарным SQL CAS.

  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry < equipment-rename.py
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - apply < equipment-rename.py
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry < equipment-rename.py

dry: только чтение, без ORM-изменений/flush/audit/commit.
apply: короткая блокировка пяти таблиц, проверка всего пакета, один commit.
Своя «Для базы», несогласованный alias, другое старое значение, другая роль,
лишний/пропавший экземпляр модели -> отказ всего пакета.
Исторические ЭТК, шаблоны, пользовательские overrides и склад не переписываются.
"""
import csv
import io
import json
import os
import sys
from dataclasses import replace
from uuid import UUID, uuid4

from sqlalchemy import select, text

# app.database also registers the ETK refresh-request before_flush listener.
from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in
from app.modules.references.equipment import (
    ARTICLE_KEY, BASE_KEY, INV_KEY, NAME_KEYS, TYPE_KEY, base_value, inv_key,
)
from app.modules.references.equipment_identity import MAKER_KEY
from app.modules.references.infrastructure import (
    EquipmentModelRow, TechOperationRecordRow, EquipmentOperationLinkRow,
    EquipmentUnitLinkRow, EquipmentTemplateToolRow,
)
from app.modules.techcards.infrastructure import RefreshRequestRow
from app.shared.clock import moscow_now
from app.shared.identity import Actor

DATA = r'''inv;type;current_name;article;current_maker;new_name;new_maker;role;note
ОБ.097;Оборудование;Станок резки/ обжим+обжим;BZW-2.0;Bozwang;Автомат резки, зачистки и двусторонней опрессовки контактов;Bozwang (Jiangsu Bozhiwang);операционное;
ОБ.093;Оборудование;Большой пресс наконечников;BZW-20TS;Bozwang;Пресс сервоприводный для опрессовки силовых наконечников;Bozwang (Jiangsu Bozhiwang);операционное;
ОБ.095;Оборудование;Станок резки провода с роторной зачисткой больш.;BZW-50X;Bozwang;Автомат резки и роторной зачистки кабеля до 50 мм²;Bozwang (Jiangsu Bozhiwang);операционное;
ОБ.098;Оборудование;Станок резки/ обжим+лужение;BZW-5.0+Z;Bozwang;Автомат резки, зачистки, опрессовки контакта с одной стороны и лужения с другой;Bozwang (Jiangsu Bozhiwang);операционное;
ОБ.094;Оборудование;Станок резки провода с роторной зачисткой мал.;BZW-886+Q2;Bozwang;Автомат резки и зачистки коаксиального кабеля;Bozwang (Jiangsu Bozhiwang);операционное;у производителя: BZW-886-Q2
ОБ.100.1, ОБ.100.2, ОБ.100.3;Оборудование;Большой стрипер;BZW-D2.0S;Bozwang;Станок зачистки провода;Bozwang (Jiangsu Bozhiwang);операционное;
ОБ.099.1, ОБ.099.2;Оборудование;Станок для обжима терминалов без зачистки;BZW-SF4040T;Bozwang;Пресс сервоприводный для опрессовки контактов россыпью;Bozwang (Jiangsu Bozhiwang);операционное;у производителя: BZW-SF-4040T
ОБ.072;Оборудование;Установка аддитивного производства (3D-принтер стереолитографический);HALOT-X1;CREALITY;Принтер 3D фотополимерный (LCD);Creality;операционное;
ОБ.027.4;Оборудование;Автомат для снятия изоляции и опрессовки контактов;WL-2.0ВТ;Crown;Станок зачистки провода и опрессовки контактов;Crown (Suzhou Crown Electronic Technology);операционное;"ОПЕЧАТКА: «ВТ» набрано кириллицей; верно WL-2.0BT (латиница)"
ОБ.023;Оборудование;Установка автоматической пайки разъемов;WL-GP011;Crown;Установка высокочастотной пайки проводов к разъёмам;Crown (Suzhou Crown Electronic Technology);операционное;
ОБ.015;Оборудование;Автомат мерной резки проводов;EW-05F+M;Eastontech;Автомат резки и зачистки многожильного кабеля с промежуточной зачисткой;Eastontech;операционное;
ОБ.003;Оборудование;Станок для снятия изоляции и скручивания экранирующей оплетки;EW-1015;Eastontech;Станок расчёсывания и скрутки экранной оплётки;Eastontech;операционное;
ОБ.007;Оборудование;Автомат для снятия изоляции с проводов;EW-1090;Eastontech;Станок зачистки провода;Eastontech;операционное;
ОБ.010;Оборудование;Машина для снятия изоляции с коаксиальных кабелей;EW-1165;Eastontech;Станок зачистки коаксиального кабеля;Eastontech;операционное;
ОБ.016;Оборудование;Ножницы гильотинные;EW 1230;Eastontech;Станок резки кабеля;Eastontech;операционное;у производителя через дефис: EW-1230
ОБ.001;Оборудование;Установка для нагрева термоусаживаемых трубок;EW-1935;Eastontech;Установка термоусадки настольная;Eastontech;операционное;
ОБ.033.1, ОБ.033.2;Оборудование;Машина испытательная разрывная;EW-2510;Eastontech;Машина разрывная для испытания опрессовки контактов;Eastontech;операционное;
ОБ.034.1, ОБ.034.2;Оборудование;Прибор для измерения высоты опрессовки (микрометр);EW-2550;Eastontech;Микрометр для измерения высоты опрессовки;Eastontech;операционное;
ОБ.019;Оборудование;Автомат мерной резки и маркировки проводов;EW-3185+L;Eastontech;Автомат резки и зачистки провода с лазерной маркировкой;Eastontech;операционное;
ОБ.027.1, ОБ.027.2, ОБ.027.3;Оборудование;Автомат для снятия изоляции и опрессовки контактов;EW-50A;Eastontech;Станок зачистки провода и опрессовки контактов;Eastontech;операционное;
ОБ.008;Оборудование;Установка индукционной пайки;DW-OHF-6KW;HLQ Induction Equipment;Установка индукционной пайки высокочастотная;HLQ Induction Equipment;операционное;"ОПЕЧАТКА: у производителя серия DW-UHF-6KW (UHF, не OHF); исполнение -I/-II/-III/-B уточнить по шильдику"
ОБ.012;Оборудование;Установка плазменной обработки поверхности;PL-5050;Keylink Technology;Установка атмосферной плазменной обработки поверхности;Keylink Technology;операционное;у производителя исполнения PL-5050P / PL-5050-2 / PL-5050H — уточнить
ОБ.040;Оборудование;Тестер кабельных жгутов;SR-350A;Sinrad Technology;Тестер кабельных жгутов четырёхпроводный;Sinrad Technology;операционное;у производителя модель SR-350A+ — сверить с шильдиком
ОБ.035;Оборудование;Автомат для двустороннего снятия изоляции с проводов;SR-CS340;Sinrad Technology;Станок двусторонней зачистки жил многожильного кабеля;Sinrad Technology;операционное;
ОБ.017;Оборудование;Установка ультразвуковой очистки;VBS-10DS;Вилитек;Ванна ультразвуковая 10 л с нагревом;Vilitek (Вилитек);операционное;'''
ROWS = list(csv.DictReader(io.StringIO(DATA), delimiter=";"))
ACTOR = Actor(
    id=UUID(int=0),
    name="Переименование оборудования 11.11.2026",
    department="System",
)
REASON = "25 моделей (ОБ.024.2/.4 — две модели, ОБ.105 — без столбцов имени; отложены) подтверждены владельцем: eq-confirmed.csv, 11.11.2026"


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def log(key, field, old, new, status):
    print(json.dumps(
        dict(key=key, field=field, old=old, new=new, status=status),
        ensure_ascii=False,
    ), flush=True)


def process(uow, dry):
    s, now = uow.session, moscow_now()
    units = list(s.scalars(select(TechOperationRecordRow).where(
        TechOperationRecordRow.tab == "ob")))
    models = {m.id: m for m in s.scalars(select(EquipmentModelRow))}
    by_inv = {}
    for unit in units:
        by_inv.setdefault(inv_key(unit.fields.get(INV_KEY, "")), []).append(unit)

    require(len(ROWS) == 25, "Ожидалось 25 строк CSV")
    require(sum(len(r["inv"].split(",")) for r in ROWS) == 32,
            "Ожидалось 32 инвентарных номера")
    plans, seen_inv, seen_models = [], set(), set()

    # No ORM changes until every guard of every model has passed.
    for r in ROWS:
        numbers = [v.strip() for v in r["inv"].split(",")]
        selected = []
        require(r["role"] == "операционное", f'{r["inv"]}: роль CSV')
        for inv in numbers:
            key = inv_key(inv)
            require(key not in seen_inv, f"{inv}: повтор номера в CSV")
            seen_inv.add(key)
            found = by_inv.get(key, [])
            require(len(found) == 1, f"{inv}: найдено строк {len(found)}, нужно 1")
            selected.append(found[0])

        mids = {u.equipment_model_id for u in selected}
        require(len(mids) == 1 and None not in mids,
                f'{r["inv"]}: единицы принадлежат разным моделям')
        mid = next(iter(mids))
        require(mid in models and mid not in seen_models,
                f'{r["inv"]}: модель отсутствует или повторена в CSV')
        seen_models.add(mid)
        model = models[mid]
        require(
            {u.id for u in units if u.equipment_model_id == mid}
            == {u.id for u in selected},
            f'{r["inv"]}: CSV не покрывает все единицы модели',
        )
        require(model.role == "operational", f'{r["inv"]}: роль уже изменена')
        require(model.article == r["article"], f'{r["inv"]}: артикул модели изменён')
        require(
            model.name in (r["current_name"], r["new_name"])
            and model.maker in (r["current_maker"], r["new_maker"]),
            f'{r["inv"]}: атрибуты модели уже изменены',
        )

        group = []
        for inv, unit in zip(numbers, selected):
            f = unit.fields
            require(f.get(TYPE_KEY) == r["type"], f"{inv}: тип изменён")
            require(f.get(ARTICLE_KEY) == r["article"], f"{inv}: артикул изменён")
            keys = [k for k in NAME_KEYS if k in f]
            require(bool(keys) and MAKER_KEY in f,
                    f"{inv}: нет столбцов имени/производителя")
            edits = []
            for key, expected, target in [
                *((k, r["current_name"], r["new_name"]) for k in keys),
                (MAKER_KEY, r["current_maker"], r["new_maker"]),
            ]:
                actual = str(f.get(key) or "")
                if actual == target:
                    status = "ALREADY"
                elif actual == expected:
                    edits.append(FieldEdit(key, expected, target))
                    status = "PLAN"
                else:
                    log(inv, key, actual, target, f"REFUSED; expected={expected!r}")
                    raise RuntimeError(f"{inv}: {key}: старое значение не совпало")
                log(inv, key, actual, target, status)

            current_base = base_value(f)
            new_base = f'{r["article"]}/{r["new_name"]}'
            require(
                f.get(BASE_KEY) == current_base and unit.display_text == current_base,
                f"{inv}: собственная/несогласованная «Для базы» или display_text",
            )
            for key in [BASE_KEY, *(["baseValue"] if "baseValue" in f else [])]:
                actual = f[key]
                require(actual == current_base, f"{inv}: несогласованный {key}")
                if actual != new_base:
                    edits.append(FieldEdit(key, actual, new_base))
                log(inv, key, actual, new_base,
                    "ALREADY" if actual == new_base else "PLAN")

            old_base = f'{r["article"]}/{r["current_name"]}'
            sort_map = {
                old_base: new_base,
                f'{r["type"]} {old_base}': f'{r["type"]} {new_base}',
            }
            new_sort = sort_map.get(unit.sort_key, unit.sort_key)
            if new_sort != unit.sort_key:
                log(inv, "sort_key", unit.sort_key, new_sort, "PLAN")
            group.append((unit.id, inv, tuple(edits), new_sort))

        log(r["inv"], "role_confirmed", model.role_confirmed, True,
            "ALREADY" if model.role_confirmed else "PLAN")
        plans.append((r, mid, group))

    if dry:
        print("DRY OK: 25 моделей, 32 единицы; записей в БД нет.")
        return

    op_before = {
        op.id: (dict(op.fields), op.display_text, op.normalized_search)
        for op in s.scalars(select(TechOperationRecordRow).where(
            TechOperationRecordRow.tab == "op"))
    }
    selected_ids = {uid for _, _, group in plans for uid, _, _, _ in group}

    def unit_state(u):
        return (dict(u.fields), u.display_text, u.normalized_search, u.sort_key,
                u.equipment_model_id, u.edited_in_erp, u.mirror_key)

    outside_units = {
        u.id: unit_state(u) for u in units if u.id not in selected_ids
    }
    outside_models = {
        m.id: (m.name, m.article, m.maker, m.role, m.role_confirmed)
        for m in models.values() if m.id not in seen_models
    }
    link_classes = (
        EquipmentOperationLinkRow, EquipmentUnitLinkRow, EquipmentTemplateToolRow,
    )

    def link_state():
        return {
            cls.__tablename__: {
                tuple(getattr(x, c.name) for c in cls.__table__.columns)
                for x in s.scalars(select(cls))
            }
            for cls in link_classes
        }

    links_before = link_state()
    linked_cells = {
        (x.operation_id, x.op_column)
        for x in s.scalars(select(EquipmentOperationLinkRow))
        if x.model_id in seen_models
    }
    work = any(
        not models[mid].role_confirmed or any(
            edits or s.get(TechOperationRecordRow, uid).sort_key != new_sort
            for uid, _, edits, new_sort in group
        )
        for _, mid, group in plans
    )
    if work:
        # An older worker batch cannot consume this uncommitted request.
        s.add(RefreshRequestRow(
            id=uuid4(), reason="reference", created_at=now,
            payload={
                "tables": ["tech_operation_records", "equipment_models"],
                "reason": REASON,
            },
        ))

    applied = []
    for r, mid, group in plans:
        old_model = models[mid]
        model_before = {
            "name": old_model.name, "maker": old_model.maker,
            "role": old_model.role, "role_confirmed": old_model.role_confirmed,
        }
        for uid, inv, edits, new_sort in group:
            before = uow.tech_operations.get(uid)
            after = before
            if edits:
                before, after = _edit_in(uid, edits, now, uow)
            if after.sort_key != new_sort:
                after = replace(after, sort_key=new_sort)
                uow.tech_operations.save(after)

            changes = [
                {"field": k, "old": before.fields.get(k, ""),
                 "new": after.fields.get(k, "")}
                for k in sorted(set(before.fields) | set(after.fields))
                if before.fields.get(k, "") != after.fields.get(k, "")
            ]
            for key in ("display_text", "normalized_search", "sort_key"):
                old, new = getattr(before, key), getattr(after, key)
                if old != new:
                    changes.append({"field": key, "old": old, "new": new})
            if changes:
                _audit("references.tech_record_edited", ACTOR, now, {
                    "record_id": str(uid), "tab": "ob", "inventory": inv,
                    "fields": [e.field for e in edits],
                    "changes": changes, "reason": REASON,
                }, uow)
                applied.extend(
                    (inv, c["field"], c["old"], c["new"]) for c in changes
                )

        model = s.get(EquipmentModelRow, mid)
        if not model.role_confirmed:
            uow.equipment.set_role(mid, "operational")
            _audit("references.equipment_role_set", ACTOR, now, {
                "model_id": str(mid), "inventory": r["inv"], "role": "operational",
                "old_role_confirmed": False, "role_confirmed": True,
                "reason": REASON,
            }, uow)
            applied.append((r["inv"], "role_confirmed", False, True))

        s.flush()
        require(
            (model.name, model.article, model.maker, model.role, model.role_confirmed)
            == (r["new_name"], r["article"], r["new_maker"], "operational", True),
            f'{r["inv"]}: итоговые атрибуты модели не совпали',
        )
        model_after = {
            "name": model.name, "maker": model.maker,
            "role": model.role, "role_confirmed": model.role_confirmed,
        }
        if model_before != model_after:
            _audit("references.equipment_model_renamed", ACTOR, now, {
                "model_id": str(mid), "inventory": r["inv"],
                "old": model_before, "new": model_after, "reason": REASON,
            }, uow)

        for uid, inv, _, new_sort in group:
            unit = uow.tech_operations.get(uid)
            f = unit.fields
            require(
                unit.equipment_model_id == mid and unit.sort_key == new_sort
                and f[ARTICLE_KEY] == r["article"] and f[TYPE_KEY] == r["type"]
                and all(f[k] == r["new_name"] for k in NAME_KEYS if k in f)
                and f[MAKER_KEY] == r["new_maker"]
                and f[BASE_KEY] == unit.display_text
                    == f'{r["article"]}/{r["new_name"]}'
                and ("baseValue" not in f or f["baseValue"] == f[BASE_KEY]),
                f"{inv}: итоговая проверка единицы не прошла",
            )

    # Audit the OP text copies rewritten by equipment_identity.after_save.
    for op in s.scalars(select(TechOperationRecordRow).where(
            TechOperationRecordRow.tab == "op")):
        old_fields, old_display, old_search = op_before[op.id]
        changes = [
            {"field": k, "old": old_fields.get(k, ""), "new": op.fields.get(k, "")}
            for k in sorted(set(old_fields) | set(op.fields))
            if old_fields.get(k, "") != op.fields.get(k, "")
        ]
        for key, old in [
            ("display_text", old_display), ("normalized_search", old_search),
        ]:
            if old != getattr(op, key):
                changes.append({"field": key, "old": old, "new": getattr(op, key)})
        require(
            all(c["field"] == "normalized_search"
                or (op.id, c["field"]) in linked_cells for c in changes),
            f"OP {op.id}: неожиданная правка вне связей переименованных моделей",
        )
        if changes:
            natural_key = str(
                op.fields.get("Номер") or op.fields.get("opNumber") or op.display_text
            )
            _audit("references.tech_record_edited", ACTOR, now, {
                "record_id": str(op.id), "tab": "op", "operation": natural_key,
                "changes": changes,
                "reason": REASON + "; автоматическая подпись оборудования",
            }, uow)
            applied.extend(
                (natural_key, c["field"], c["old"], c["new"]) for c in changes
            )

    s.flush()
    current_models = {
        m.id: m for m in s.scalars(select(EquipmentModelRow))
    }
    require(set(current_models) == set(models),
            "Глобальная сверка создала/удалила модель")
    require(
        outside_models == {
            m.id: (m.name, m.article, m.maker, m.role, m.role_confirmed)
            for m in current_models.values() if m.id not in seen_models
        },
        "Глобальная сверка изменила постороннюю модель",
    )
    require(
        outside_units == {
            u.id: unit_state(u)
            for u in s.scalars(select(TechOperationRecordRow).where(
                TechOperationRecordRow.tab == "ob"))
            if u.id not in selected_ids
        },
        "Глобальная сверка изменила постороннюю единицу",
    )
    require(link_state() == links_before, "Глобальная сверка изменила связи")

    if applied:
        uow.commit()
    for args in applied:
        log(*args, "APPLIED")
    print(f"APPLY OK: 27 моделей, 35 единиц; "
          f"изменений ячеек/метаданных: {len(applied)}")


def main():
    require(len(sys.argv) <= 2, "режим: dry | apply")
    mode = sys.argv[1] if len(sys.argv) == 2 else "dry"
    require(mode in ("dry", "apply"), "режим: dry | apply")
    engine = build_engine(os.environ["ERP_DATABASE_URL"])
    try:
        require(engine.dialect.name == "postgresql",
                "Этот запуск рассчитан на PostgreSQL")
        with build_references_uow_factory(engine)() as uow:
            if mode == "dry":
                uow.session.execute(text(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            else:
                uow.session.execute(text("SET LOCAL lock_timeout = '10s'"))
                uow.session.execute(text(
                    "LOCK TABLE equipment_models, tech_operation_records, "
                    "equipment_operation_links, equipment_unit_links, "
                    "equipment_template_tools IN SHARE ROW EXCLUSIVE MODE"))
            process(uow, mode == "dry")
    finally:
        engine.dispose()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        main()
    except Exception as exc:
        print(f"REFUSED / ROLLBACK: {exc}", file=sys.stderr)
        raise SystemExit(1)

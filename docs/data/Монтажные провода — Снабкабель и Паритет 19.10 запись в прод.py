"""Запись в прод 19.10.2026 — монтажные провода по Снабкабелю (snabcable.ru) и Паритету (paritet.su).

Владелец 19.10: «нет ссылки на поставщика, нет поставщиков, основа это снабкабель паритет; не везде есть производитель;
пару серий не до конца раскрыты». Строка — провод по марке (ключ марки), правка идёт со старым значением: изменённая
с тех пор ячейка вернёт отказ всей строки. Новые сечения: строка БД.ПРОВ + структура монтажного провода (сечение из
таблицы сечений). Журнал ячеек — «Монтажные провода — Снабкабель и Паритет 19.10.csv».

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → «к записи 0», новые «уже есть»
"""
import json
import os
import sys
from decimal import Decimal

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import (FieldEdit, SaveHookupCommand, _audit, _clean_new_fields, _create_in, _edit_in,
                                                 save_hookup_in)
from app.modules.references.cables import cable_mark
from app.modules.references.domain import TechOperationTab, normalized_cable_key
from app.modules.references.load_mirror import ACTOR
from app.shared.clock import moscow_now

sys.stdout.reconfigure(encoding="utf-8")
PLAN = json.loads(r'''{
"edits": [
{
"mark": "МГШВ 0,12",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,12",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/nv-nve/mgshv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,12",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "7,4",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МГШВ 0,12",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,12",
"field": "Производители",
"old": "Подольсккабель, Автопровод (Беларускабель)",
"new": "Подольсккабель, Автопровод (Беларускабель), Чувашкабель, ЭКЗ",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,12",
"field": "Цвета",
"old": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный",
"new": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный, голубой",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МГШВ 0,14",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,14",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/nv-nve/mgshv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,14",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "7,8",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МГШВ 0,14",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,14",
"field": "Производители",
"old": "Подольсккабель, Автопровод (Беларускабель)",
"new": "Подольсккабель, Автопровод (Беларускабель), Чувашкабель, ЭКЗ",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,14",
"field": "Цвета",
"old": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный",
"new": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный, голубой",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МГШВ 0,2",
"field": "Поставщик",
"old": "АО НПП \"Системные Технологии\"",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,2",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/nv-nve/mgshv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,2",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "8,9",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МГШВ 0,2",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,2",
"field": "Производители",
"old": "Подольсккабель, Автопровод (Беларускабель)",
"new": "Подольсккабель, Автопровод (Беларускабель), Чувашкабель, ЭКЗ",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,2",
"field": "Цвета",
"old": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный",
"new": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный, голубой",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МГШВ 0,35",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,35",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/nv-nve/mgshv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,35",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "11,5",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МГШВ 0,35",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,35",
"field": "Производители",
"old": "Подольсккабель, Автопровод (Беларускабель)",
"new": "Подольсккабель, Автопровод (Беларускабель), Чувашкабель, ЭКЗ",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,35",
"field": "Цвета",
"old": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный",
"new": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный, голубой",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МГШВ 0,50",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,50",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/nv-nve/mgshv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,50",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "14,7",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МГШВ 0,50",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,50",
"field": "Производители",
"old": "Подольсккабель, Автопровод (Беларускабель)",
"new": "Подольсккабель, Автопровод (Беларускабель), Чувашкабель, ЭКЗ",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,50",
"field": "Цвета",
"old": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный",
"new": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный, голубой",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МГШВ 0,75",
"field": "Поставщик",
"old": "АО НПП \"Системные Технологии\"",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,75",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/nv-nve/mgshv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,75",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "19,9",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МГШВ 0,75",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,75",
"field": "Производители",
"old": "Подольсккабель, Автопровод (Беларускабель)",
"new": "Подольсккабель, Автопровод (Беларускабель), Чувашкабель, ЭКЗ",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 0,75",
"field": "Цвета",
"old": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный",
"new": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный, голубой",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МГШВ 1,0",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 1,0",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/nv-nve/mgshv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 1,0",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "24,7",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МГШВ 1,0",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 1,0",
"field": "Производители",
"old": "Подольсккабель, Автопровод (Беларускабель)",
"new": "Подольсккабель, Автопровод (Беларускабель), Чувашкабель, ЭКЗ",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 1,0",
"field": "Цвета",
"old": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный",
"new": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный, голубой",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МГШВ 1,5",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 1,5",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/nv-nve/mgshv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 1,5",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "34,9",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МГШВ 1,5",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 1,5",
"field": "Производители",
"old": "Подольсккабель, Автопровод (Беларускабель)",
"new": "Подольсккабель, Автопровод (Беларускабель), Чувашкабель, ЭКЗ",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026"
},
{
"mark": "МГШВ 1,5",
"field": "Цвета",
"old": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный",
"new": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный, голубой",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/mgshv/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МГТФ 0,03",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,03",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,03",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "10,4",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МГТФ 0,03",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,03",
"field": "Производители",
"old": "",
"new": "Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,05",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,05",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,05",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "13,7",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МГТФ 0,05",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,05",
"field": "Производители",
"old": "",
"new": "Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,07",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,07",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,07",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "10,5",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МГТФ 0,07",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,07",
"field": "Производители",
"old": "",
"new": "Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,07",
"field": "Цвета",
"old": "натуральный",
"new": "натуральный, белый, жёлтый, зелёный, коричневый, красный, оранжевый, синий, чёрный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МГТФ 0,10",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,10",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,10",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "13,2",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МГТФ 0,10",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,10",
"field": "Производители",
"old": "",
"new": "Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,12",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,12",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,12",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "11,6",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МГТФ 0,12",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,12",
"field": "Производители",
"old": "",
"new": "Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,12",
"field": "Цвета",
"old": "натуральный",
"new": "натуральный, белый, жёлтый, зелёный, коричневый, красный, оранжевый, синий, чёрный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МГТФ 0,14",
"field": "Поставщик",
"old": "АО \"Радиоавионика\"",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,14",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,14",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "12",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МГТФ 0,14",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,14",
"field": "Производители",
"old": "",
"new": "Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,20",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,20",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,20",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "15,7",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МГТФ 0,20",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,20",
"field": "Производители",
"old": "",
"new": "Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,20",
"field": "Цвета",
"old": "натуральный",
"new": "натуральный, белый, жёлтый, зелёный, коричневый, красный, оранжевый, синий, чёрный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МГТФ 0,35",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,35",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,35",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "19,9",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МГТФ 0,35",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,35",
"field": "Производители",
"old": "",
"new": "Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 0,35",
"field": "Цвета",
"old": "натуральный",
"new": "натуральный, белый, жёлтый, зелёный, коричневый, красный, оранжевый, синий, чёрный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МГТФ 1,0",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 1,0",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 1,0",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "51,7",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МГТФ 1,0",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МГТФ 1,0",
"field": "Производители",
"old": "",
"new": "Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026"
},
{
"mark": "МК 26-13 0,07",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026"
},
{
"mark": "МК 26-13 0,07",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026"
},
{
"mark": "МК 26-13 0,07",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "25",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МК 26-13 0,07",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026"
},
{
"mark": "МК 26-13 0,07",
"field": "Цвета",
"old": "белый, натуральный, жёлтый, оранжевый, красный, синий, коричневый, чёрный",
"new": "белый, натуральный, жёлтый, оранжевый, красный, синий, коричневый, чёрный, зелёный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МК 26-13 0,12",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026"
},
{
"mark": "МК 26-13 0,12",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026"
},
{
"mark": "МК 26-13 0,12",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "29",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МК 26-13 0,12",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026"
},
{
"mark": "МК 26-13 0,12",
"field": "Цвета",
"old": "белый, натуральный, жёлтый, оранжевый, красный, синий, коричневый, чёрный",
"new": "белый, натуральный, жёлтый, оранжевый, красный, синий, коричневый, чёрный, зелёный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МК 26-13 0,20",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026"
},
{
"mark": "МК 26-13 0,20",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026"
},
{
"mark": "МК 26-13 0,20",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "34",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МК 26-13 0,20",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026"
},
{
"mark": "МК 26-13 0,20",
"field": "Цвета",
"old": "белый, натуральный, жёлтый, оранжевый, красный, синий, коричневый, чёрный",
"new": "белый, натуральный, жёлтый, оранжевый, красный, синий, коричневый, чёрный, зелёный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МК 26-13 0,35",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026"
},
{
"mark": "МК 26-13 0,35",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026"
},
{
"mark": "МК 26-13 0,35",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "42",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МК 26-13 0,35",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026"
},
{
"mark": "МК 26-13 0,35",
"field": "Цвета",
"old": "белый, натуральный, жёлтый, оранжевый, красный, синий, коричневый, чёрный",
"new": "белый, натуральный, жёлтый, оранжевый, красный, синий, коричневый, чёрный, зелёный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МК 26-13 0,50",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026"
},
{
"mark": "МК 26-13 0,50",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026"
},
{
"mark": "МК 26-13 0,50",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "52",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МК 26-13 0,50",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026"
},
{
"mark": "МК 26-13 0,50",
"field": "Цвета",
"old": "белый, натуральный, жёлтый, оранжевый, красный, синий, коричневый, чёрный",
"new": "белый, натуральный, жёлтый, оранжевый, красный, синий, коричневый, чёрный, зелёный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МС 15-11 0,08",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026"
},
{
"mark": "МС 15-11 0,08",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026"
},
{
"mark": "МС 15-11 0,08",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "29",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МС 15-11 0,08",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026"
},
{
"mark": "МС 15-11 0,08",
"field": "Цвета",
"old": "натуральный, красный, жёлтый, синий, зелёный, коричневый, чёрный",
"new": "натуральный, красный, жёлтый, синий, зелёный, коричневый, чёрный, белый, оранжевый",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МС 15-11 0,12",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026"
},
{
"mark": "МС 15-11 0,12",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026"
},
{
"mark": "МС 15-11 0,12",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "32",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МС 15-11 0,12",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026"
},
{
"mark": "МС 15-11 0,12",
"field": "Цвета",
"old": "натуральный, красный, жёлтый, синий, зелёный, коричневый, чёрный",
"new": "натуральный, красный, жёлтый, синий, зелёный, коричневый, чёрный, белый, оранжевый",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МС 15-11 0,20",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026"
},
{
"mark": "МС 15-11 0,20",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026"
},
{
"mark": "МС 15-11 0,20",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "38",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МС 15-11 0,20",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026"
},
{
"mark": "МС 15-11 0,20",
"field": "Цвета",
"old": "натуральный, красный, жёлтый, синий, зелёный, коричневый, чёрный",
"new": "натуральный, красный, жёлтый, синий, зелёный, коричневый, чёрный, белый, оранжевый",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МС 15-11 0,35",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026"
},
{
"mark": "МС 15-11 0,35",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026"
},
{
"mark": "МС 15-11 0,35",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "45",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МС 15-11 0,35",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026"
},
{
"mark": "МС 15-11 0,35",
"field": "Цвета",
"old": "натуральный, красный, жёлтый, синий, зелёный, коричневый, чёрный",
"new": "натуральный, красный, жёлтый, синий, зелёный, коричневый, чёрный, белый, оранжевый",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МС 15-11 0,50",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026"
},
{
"mark": "МС 15-11 0,50",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026"
},
{
"mark": "МС 15-11 0,50",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "52",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МС 15-11 0,50",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026"
},
{
"mark": "МС 15-11 0,50",
"field": "Цвета",
"old": "натуральный, красный, жёлтый, синий, зелёный, коричневый, чёрный",
"new": "натуральный, красный, жёлтый, синий, зелёный, коричневый, чёрный, белый, оранжевый",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "МС 16-13 0,03",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС 16-13 0,03",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС 16-13 0,03",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "53",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МС 16-13 0,03",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС 16-13 0,03",
"field": "Производители",
"old": "",
"new": "Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС 16-13 0,05",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС 16-13 0,05",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС 16-13 0,05",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "63",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МС 16-13 0,05",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС 16-13 0,05",
"field": "Производители",
"old": "",
"new": "Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС16-13 0,08",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС16-13 0,08",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС16-13 0,08",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "67",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МС16-13 0,08",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС16-13 0,08",
"field": "Производители",
"old": "",
"new": "Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС16-13 0,12",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС16-13 0,12",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС16-13 0,12",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "117",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МС16-13 0,12",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС16-13 0,12",
"field": "Производители",
"old": "",
"new": "Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС 16-13 0,20",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС 16-13 0,20",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС 16-13 0,20",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "139",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МС 16-13 0,20",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС 16-13 0,20",
"field": "Производители",
"old": "",
"new": "Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС 16-13 0,35",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС 16-13 0,35",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС 16-13 0,35",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "194",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МС 16-13 0,35",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС 16-13 0,35",
"field": "Производители",
"old": "",
"new": "Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС 16-13 0,50",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС 16-13 0,50",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС 16-13 0,50",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "349",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "МС 16-13 0,50",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "МС 16-13 0,50",
"field": "Производители",
"old": "",
"new": "Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/ — 19.10.2026"
},
{
"mark": "НВ-4 0,12",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,12",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/nv-nve/nv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,12",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "6,7",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "НВ-4 0,12",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,12",
"field": "Производители",
"old": "",
"new": "Чувашкабель, БРЭКС",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,12",
"field": "Цвета",
"old": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный",
"new": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный, голубой, жёлто-зелёный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "НВ-4 0,20",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,20",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/nv-nve/nv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,20",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "8,6",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "НВ-4 0,20",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,20",
"field": "Производители",
"old": "",
"new": "Чувашкабель, БРЭКС",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,20",
"field": "Цвета",
"old": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный",
"new": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный, голубой, жёлто-зелёный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "НВ-4 0,35",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,35",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/nv-nve/nv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,35",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "10,9",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "НВ-4 0,35",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,35",
"field": "Производители",
"old": "",
"new": "Чувашкабель, БРЭКС",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,35",
"field": "Цвета",
"old": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный",
"new": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный, голубой, жёлто-зелёный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "НВ-4 0,50",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,50",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/nv-nve/nv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,50",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "14",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "НВ-4 0,50",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,50",
"field": "Производители",
"old": "",
"new": "Чувашкабель, БРЭКС",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,50",
"field": "Цвета",
"old": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный",
"new": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный, голубой, жёлто-зелёный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "НВ-4 0,75",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,75",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/nv-nve/nv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,75",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "20,5",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "НВ-4 0,75",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,75",
"field": "Производители",
"old": "",
"new": "Чувашкабель, БРЭКС",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 0,75",
"field": "Цвета",
"old": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный",
"new": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный, голубой, жёлто-зелёный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "НВ-4 1,0",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 1,0",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/nv-nve/nv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 1,0",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "25,2",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "НВ-4 1,0",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 1,0",
"field": "Производители",
"old": "",
"new": "Чувашкабель, БРЭКС",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 1,0",
"field": "Цвета",
"old": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный",
"new": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный, голубой, жёлто-зелёный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "НВ-4 1,50",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 1,50",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/nv-nve/nv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 1,50",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "38,9",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "НВ-4 1,50",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 1,50",
"field": "Производители",
"old": "",
"new": "Чувашкабель, БРЭКС",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026"
},
{
"mark": "НВ-4 1,50",
"field": "Цвета",
"old": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный",
"new": "белый, жёлтый, красный, синий, зелёный, коричневый, чёрный, голубой, жёлто-зелёный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/nv-nve/nv/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "ПВАМ 0,35",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 0,35",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/car/pvam-pvame-pvamde/pvam/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 0,35",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "9,4",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПВАМ 0,35",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 0,35",
"field": "Цвета",
"old": "красный, чёрный, зелёный, белый, жёлтый, оранжевый",
"new": "красный, чёрный, зелёный, белый, жёлтый, оранжевый, голубой, коричневый, розовый, серый, фиолетовый",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "ПВАМ 0,50",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 0,50",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/car/pvam-pvame-pvamde/pvam/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 0,50",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "11,9",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПВАМ 0,50",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 0,50",
"field": "Цвета",
"old": "красный, чёрный, жёлтый, синий, белый, зелёный, голубой, розовый, фиолетовый, серый",
"new": "красный, чёрный, жёлтый, синий, белый, зелёный, голубой, розовый, фиолетовый, серый, коричневый, оранжевый",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "ПВАМ 0,75",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 0,75",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/car/pvam-pvame-pvamde/pvam/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 0,75",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "16,5",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПВАМ 0,75",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 0,75",
"field": "Цвета",
"old": "чёрный, зелёный, красный, оранжевый, розовый, серый",
"new": "чёрный, зелёный, красный, оранжевый, розовый, серый, белый, голубой, жёлтый, коричневый, фиолетовый",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "ПВАМ 1,0",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 1,0",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/car/pvam-pvame-pvamde/pvam/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 1,0",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "21,1",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПВАМ 1,0",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 1,0",
"field": "Цвета",
"old": "голубой, красный, чёрный, жёлтый, зелёный, оранжевый, розовый, белый, желто-зеленый, коричнево-серый, коричневый, красно-черный, серый",
"new": "голубой, красный, чёрный, жёлтый, зелёный, оранжевый, розовый, белый, желто-зеленый, коричнево-серый, коричневый, красно-черный, серый, фиолетовый",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "ПВАМ 1,50",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 1,50",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/car/pvam-pvame-pvamde/pvam/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 1,50",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "30,1",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПВАМ 1,50",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 1,50",
"field": "Цвета",
"old": "чёрный, белый",
"new": "чёрный, белый, голубой, жёлтый, зелёный, коричневый, красный, оранжевый, розовый, серый, фиолетовый",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "ПВАМ 2,5",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 2,5",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/car/pvam-pvame-pvamde/pvam/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 2,5",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "48,1",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПВАМ 2,5",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 4",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 4",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/car/pvam-pvame-pvamde/pvam/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 4",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "77,1",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПВАМ 4",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 6",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 6",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/car/pvam-pvame-pvamde/pvam/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПВАМ 6",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "113,1",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПВАМ 6",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pvam/ — 19.10.2026"
},
{
"mark": "ПГВА 0,50",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 0,50",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/car/pvam-pvame-pvamde/pgva/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 0,50",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "11,9",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПГВА 0,50",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 0,75",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 0,75",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/car/pvam-pvame-pvamde/pgva/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 0,75",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "16,5",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПГВА 0,75",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 1,0",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 1,0",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/car/pvam-pvame-pvamde/pgva/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 1,0",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "21,1",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПГВА 1,0",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 1,5",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 1,5",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/car/pvam-pvame-pvamde/pgva/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 1,5",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "30,1",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПГВА 1,5",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 2,5",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 2,5",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/car/pvam-pvame-pvamde/pgva/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 2,5",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "48,1",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПГВА 2,5",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 4,0",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 4,0",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/car/pvam-pvame-pvamde/pgva/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 4,0",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "77,1",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПГВА 4,0",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 6,0",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 6,0",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/car/pvam-pvame-pvamde/pgva/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПГВА 6,0",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "113,1",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПГВА 6,0",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026"
},
{
"mark": "ПуГВ 0,50",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 0,50",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 0,50",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "11,7",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПуГВ 0,50",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 0,50",
"field": "Производители",
"old": "",
"new": "Чувашкабель, БРЭКС",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 0,75",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 0,75",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 0,75",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "16,7",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПуГВ 0,75",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 0,75",
"field": "Производители",
"old": "",
"new": "Чувашкабель, БРЭКС",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 1,0",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 1,0",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 1,0",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "21,4",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПуГВ 1,0",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 1,0",
"field": "Производители",
"old": "",
"new": "Чувашкабель, БРЭКС",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 1,0",
"field": "Цвета",
"old": "белый, жёлто-зелёный, синий",
"new": "белый, жёлто-зелёный, синий, коричневый, красный, чёрный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "ПуГВ 1,5",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 1,5",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 1,5",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "29,9",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПуГВ 1,5",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 1,5",
"field": "Производители",
"old": "",
"new": "Чувашкабель, БРЭКС",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 10",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 10",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 10",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "169",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПуГВ 10",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 10",
"field": "Производители",
"old": "",
"new": "Чувашкабель, БРЭКС",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 16",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 16",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 16",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "265",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПуГВ 16",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 16",
"field": "Производители",
"old": "",
"new": "Чувашкабель, БРЭКС",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 2,5",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 2,5",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 2,5",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "45,9",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПуГВ 2,5",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 2,5",
"field": "Производители",
"old": "",
"new": "Чувашкабель, БРЭКС",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 4",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 4",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 4",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "69,8",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПуГВ 4",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 4",
"field": "Производители",
"old": "",
"new": "Чувашкабель, БРЭКС",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 6",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 6",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 6",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "99,8",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПуГВ 6",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВ 6",
"field": "Производители",
"old": "",
"new": "Чувашкабель, БРЭКС",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х0,5",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х0,5",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х0,5",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "11,7",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПуГВнг(А)-LS 1х0,5",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х0,5",
"field": "Производители",
"old": "Breks (Брянск), нг(А)-LS",
"new": "Breks (Брянск), Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х0,5",
"field": "Стандарт",
"old": "ГОСТ",
"new": "ГОСТ 31947-2012",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х0,5",
"field": "Цвета",
"old": "",
"new": "белый, синий, жёлтый, жёлто-зелёный, зелёный, коричневый, красный, чёрный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "ПуГВнг(А)-LS 1х0,5",
"field": "Темп. от, °C",
"old": "",
"new": "-50",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х0,5",
"field": "Темп. до, °C",
"old": "",
"new": "70",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х0,75",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х0,75",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х0,75",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "16,7",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПуГВнг(А)-LS 1х0,75",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х0,75",
"field": "Производители",
"old": "Breks (Брянск), нг(А)-LS",
"new": "Breks (Брянск), Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х0,75",
"field": "Стандарт",
"old": "ГОСТ",
"new": "ГОСТ 31947-2012",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х0,75",
"field": "Цвета",
"old": "",
"new": "белый, синий, жёлтый, жёлто-зелёный, зелёный, коричневый, красный, чёрный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "ПуГВнг(А)-LS 1х0,75",
"field": "Темп. от, °C",
"old": "",
"new": "-50",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х0,75",
"field": "Темп. до, °C",
"old": "",
"new": "70",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х1,0",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х1,0",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х1,0",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "21,4",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПуГВнг(А)-LS 1х1,0",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х1,0",
"field": "Производители",
"old": "Breks (Брянск), нг(А)-LS",
"new": "Breks (Брянск), Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х1,0",
"field": "Стандарт",
"old": "ГОСТ",
"new": "ГОСТ 31947-2012",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х1,0",
"field": "Цвета",
"old": "красный, чёрный",
"new": "красный, чёрный, белый, синий, жёлтый, жёлто-зелёный, зелёный, коричневый",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "ПуГВнг(А)-LS 1х1,0",
"field": "Темп. от, °C",
"old": "",
"new": "-50",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х1,0",
"field": "Темп. до, °C",
"old": "",
"new": "70",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х1,5",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х1,5",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х1,5",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "29,9",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПуГВнг(А)-LS 1х1,5",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х1,5",
"field": "Производители",
"old": "Breks (Брянск), нг(А)-LS",
"new": "Breks (Брянск), Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х1,5",
"field": "Стандарт",
"old": "ГОСТ",
"new": "ГОСТ 31947-2012",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х1,5",
"field": "Цвета",
"old": "",
"new": "белый, синий, жёлтый, жёлто-зелёный, зелёный, коричневый, красный, чёрный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "ПуГВнг(А)-LS 1х1,5",
"field": "Темп. от, °C",
"old": "",
"new": "-50",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х1,5",
"field": "Темп. до, °C",
"old": "",
"new": "70",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х1,5",
"field": "Наружный диаметр, мм",
"old": "3,0",
"new": "3,1",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026 (макс. наружный диаметр)"
},
{
"mark": "ПуГВнг(А)-LS 1х2,5",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х2,5",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х2,5",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "45,9",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПуГВнг(А)-LS 1х2,5",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х2,5",
"field": "Производители",
"old": "Breks (Брянск), нг(А)-LS",
"new": "Breks (Брянск), Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х2,5",
"field": "Стандарт",
"old": "ГОСТ",
"new": "ГОСТ 31947-2012",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х2,5",
"field": "Цвета",
"old": "",
"new": "белый, синий, жёлтый, жёлто-зелёный, зелёный, коричневый, красный, чёрный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "ПуГВнг(А)-LS 1х2,5",
"field": "Темп. от, °C",
"old": "",
"new": "-50",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х2,5",
"field": "Темп. до, °C",
"old": "",
"new": "70",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х2,5",
"field": "Наружный диаметр, мм",
"old": "3,7",
"new": "3,9",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026 (макс. наружный диаметр)"
},
{
"mark": "ПуГВнг(А)-LS 1х4,0",
"field": "Поставщик",
"old": "",
"new": "Снабкабель (snabcable.ru)",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х4,0",
"field": "Ссылка",
"old": "",
"new": "https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х4,0",
"field": "Цена поставщика, ₽/м",
"old": "",
"new": "69,8",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026; оптовая цена при 1000 м, с НДС"
},
{
"mark": "ПуГВнг(А)-LS 1х4,0",
"field": "Дата цены",
"old": "",
"new": "19.10.2026",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х4,0",
"field": "Производители",
"old": "Breks (Брянск), нг(А)-LS",
"new": "Breks (Брянск), Чувашкабель",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х4,0",
"field": "Стандарт",
"old": "ГОСТ",
"new": "ГОСТ 31947-2012",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х4,0",
"field": "Цвета",
"old": "",
"new": "белый, синий, жёлтый, жёлто-зелёный, зелёный, коричневый, красный, чёрный",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026 (цвета в прайсе)"
},
{
"mark": "ПуГВнг(А)-LS 1х4,0",
"field": "Темп. от, °C",
"old": "",
"new": "-50",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х4,0",
"field": "Темп. до, °C",
"old": "",
"new": "70",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026"
},
{
"mark": "ПуГВнг(А)-LS 1х4,0",
"field": "Наружный диаметр, мм",
"old": "4,2",
"new": "4,6",
"source": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026 (макс. наружный диаметр)"
},
{
"mark": "НВМ 0,12",
"field": "Ссылка",
"old": "",
"new": "https://paritet.su/catalog/nvm/nvm_0_12_4_600_belyy/",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_12_4_600_belyy/ — 19.10.2026: Арт. 100083 | Цена за 1 км с НДС 7040 | Число и диаметр проволок 7х0,15 | Сечение жилы 0.12 | Диаметр кабеля 1,15 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,12 4 600 белый для межприборного монтажа"
},
{
"mark": "НВМ 0,12",
"field": "Цена поставщика, ₽/м",
"old": "5,77",
"new": "7,04",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_12_4_600_belyy/ — 19.10.2026: Арт. 100083 | Цена за 1 км с НДС 7040 | Число и диаметр проволок 7х0,15 | Сечение жилы 0.12 | Диаметр кабеля 1,15 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,12 4 600 белый для межприборного монтажа; цена за 1 км с НДС / 1000"
},
{
"mark": "НВМ 0,12",
"field": "Дата цены",
"old": "05.10.2026",
"new": "19.10.2026",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_12_4_600_belyy/ — 19.10.2026: Арт. 100083 | Цена за 1 км с НДС 7040 | Число и диаметр проволок 7х0,15 | Сечение жилы 0.12 | Диаметр кабеля 1,15 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,12 4 600 белый для межприборного монтажа"
},
{
"mark": "НВМ 0,12",
"field": "Жила: проволок × диаметр, мм",
"old": "",
"new": "7×0,15",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_12_4_600_belyy/ — 19.10.2026: Арт. 100083 | Цена за 1 км с НДС 7040 | Число и диаметр проволок 7х0,15 | Сечение жилы 0.12 | Диаметр кабеля 1,15 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,12 4 600 белый для межприборного монтажа"
},
{
"mark": "НВМ 0,12",
"field": "Цвета",
"old": "",
"new": "белый, чёрный, фиолетовый, коричневый, красный, серый, синий, зелёный, жёлтый",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_12_4_600_belyy/ — 19.10.2026: Арт. 100083 | Цена за 1 км с НДС 7040 | Число и диаметр проволок 7х0,15 | Сечение жилы 0.12 | Диаметр кабеля 1,15 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,12 4 600 белый для межприборного монтажа"
},
{
"mark": "НВМ 0,12",
"field": "Наружный диаметр, мм",
"old": "1,3",
"new": "1,15",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_12_4_600_belyy/ — 19.10.2026: Арт. 100083 | Цена за 1 км с НДС 7040 | Число и диаметр проволок 7х0,15 | Сечение жилы 0.12 | Диаметр кабеля 1,15 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,12 4 600 белый для межприборного монтажа (диаметр кабеля)"
},
{
"mark": "НВМ 0,20",
"field": "Ссылка",
"old": "",
"new": "https://paritet.su/catalog/nvm/nvm_0_20_4_600_belyy/",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_20_4_600_belyy/ — 19.10.2026: Арт. 100059 | Цена за 1 км с НДС 11110 | Число и диаметр проволок 7х0,20 | Сечение жилы 0.2 | Диаметр кабеля 1,3 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,20 4 600 белый для межприборного монтажа"
},
{
"mark": "НВМ 0,20",
"field": "Цена поставщика, ₽/м",
"old": "9,11",
"new": "11,11",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_20_4_600_belyy/ — 19.10.2026: Арт. 100059 | Цена за 1 км с НДС 11110 | Число и диаметр проволок 7х0,20 | Сечение жилы 0.2 | Диаметр кабеля 1,3 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,20 4 600 белый для межприборного монтажа; цена за 1 км с НДС / 1000"
},
{
"mark": "НВМ 0,20",
"field": "Дата цены",
"old": "05.10.2026",
"new": "19.10.2026",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_20_4_600_belyy/ — 19.10.2026: Арт. 100059 | Цена за 1 км с НДС 11110 | Число и диаметр проволок 7х0,20 | Сечение жилы 0.2 | Диаметр кабеля 1,3 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,20 4 600 белый для межприборного монтажа"
},
{
"mark": "НВМ 0,20",
"field": "Жила: проволок × диаметр, мм",
"old": "",
"new": "7×0,20",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_20_4_600_belyy/ — 19.10.2026: Арт. 100059 | Цена за 1 км с НДС 11110 | Число и диаметр проволок 7х0,20 | Сечение жилы 0.2 | Диаметр кабеля 1,3 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,20 4 600 белый для межприборного монтажа"
},
{
"mark": "НВМ 0,20",
"field": "Цвета",
"old": "",
"new": "белый, чёрный, фиолетовый, коричневый, красный, розовый, серый, синий, зелёный, жёлтый",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_20_4_600_belyy/ — 19.10.2026: Арт. 100059 | Цена за 1 км с НДС 11110 | Число и диаметр проволок 7х0,20 | Сечение жилы 0.2 | Диаметр кабеля 1,3 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,20 4 600 белый для межприборного монтажа"
},
{
"mark": "НВМ 0,20",
"field": "Наружный диаметр, мм",
"old": "1,5",
"new": "1,3",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_20_4_600_belyy/ — 19.10.2026: Арт. 100059 | Цена за 1 км с НДС 11110 | Число и диаметр проволок 7х0,20 | Сечение жилы 0.2 | Диаметр кабеля 1,3 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,20 4 600 белый для межприборного монтажа (диаметр кабеля)"
},
{
"mark": "НВМ 0,35",
"field": "Ссылка",
"old": "",
"new": "https://paritet.su/catalog/nvm/nvm_0_35_4_600_belyy/",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_35_4_600_belyy/ — 19.10.2026: Арт. 100085 | Цена за 1 км с НДС 18150 | Число и диаметр проволок 7х0,26 | Сечение жилы 0.35 | Диаметр кабеля 1,48 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,35 4 600 белый для межприборного монтажа"
},
{
"mark": "НВМ 0,35",
"field": "Цена поставщика, ₽/м",
"old": "14,88",
"new": "18,15",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_35_4_600_belyy/ — 19.10.2026: Арт. 100085 | Цена за 1 км с НДС 18150 | Число и диаметр проволок 7х0,26 | Сечение жилы 0.35 | Диаметр кабеля 1,48 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,35 4 600 белый для межприборного монтажа; цена за 1 км с НДС / 1000"
},
{
"mark": "НВМ 0,35",
"field": "Дата цены",
"old": "05.10.2026",
"new": "19.10.2026",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_35_4_600_belyy/ — 19.10.2026: Арт. 100085 | Цена за 1 км с НДС 18150 | Число и диаметр проволок 7х0,26 | Сечение жилы 0.35 | Диаметр кабеля 1,48 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,35 4 600 белый для межприборного монтажа"
},
{
"mark": "НВМ 0,35",
"field": "Жила: проволок × диаметр, мм",
"old": "",
"new": "7×0,26",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_35_4_600_belyy/ — 19.10.2026: Арт. 100085 | Цена за 1 км с НДС 18150 | Число и диаметр проволок 7х0,26 | Сечение жилы 0.35 | Диаметр кабеля 1,48 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,35 4 600 белый для межприборного монтажа"
},
{
"mark": "НВМ 0,35",
"field": "Цвета",
"old": "зелёный, красный, чёрный",
"new": "зелёный, красный, чёрный, белый, фиолетовый, коричневый, серый, синий, жёлтый",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_35_4_600_belyy/ — 19.10.2026: Арт. 100085 | Цена за 1 км с НДС 18150 | Число и диаметр проволок 7х0,26 | Сечение жилы 0.35 | Диаметр кабеля 1,48 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,35 4 600 белый для межприборного монтажа"
},
{
"mark": "НВМ 0,35",
"field": "Наружный диаметр, мм",
"old": "1,6",
"new": "1,48",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_35_4_600_belyy/ — 19.10.2026: Арт. 100085 | Цена за 1 км с НДС 18150 | Число и диаметр проволок 7х0,26 | Сечение жилы 0.35 | Диаметр кабеля 1,48 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,35 4 600 белый для межприборного монтажа (диаметр кабеля)"
},
{
"mark": "НВМ 0,50",
"field": "Ссылка",
"old": "",
"new": "https://paritet.su/catalog/nvm/nvm_0_50_4_600_belyy/",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_50_4_600_belyy/ — 19.10.2026: Арт. 100074 | Цена за 1 км с НДС 23980 | Число и диаметр проволок 7х0,30 | Сечение жилы 0.5 | Диаметр кабеля 1,6 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,50 4 600 белый для межприборного монтажа"
},
{
"mark": "НВМ 0,50",
"field": "Цена поставщика, ₽/м",
"old": "19,66",
"new": "23,98",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_50_4_600_belyy/ — 19.10.2026: Арт. 100074 | Цена за 1 км с НДС 23980 | Число и диаметр проволок 7х0,30 | Сечение жилы 0.5 | Диаметр кабеля 1,6 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,50 4 600 белый для межприборного монтажа; цена за 1 км с НДС / 1000"
},
{
"mark": "НВМ 0,50",
"field": "Дата цены",
"old": "05.10.2026",
"new": "19.10.2026",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_50_4_600_belyy/ — 19.10.2026: Арт. 100074 | Цена за 1 км с НДС 23980 | Число и диаметр проволок 7х0,30 | Сечение жилы 0.5 | Диаметр кабеля 1,6 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,50 4 600 белый для межприборного монтажа"
},
{
"mark": "НВМ 0,50",
"field": "Жила: проволок × диаметр, мм",
"old": "",
"new": "7×0,30",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_50_4_600_belyy/ — 19.10.2026: Арт. 100074 | Цена за 1 км с НДС 23980 | Число и диаметр проволок 7х0,30 | Сечение жилы 0.5 | Диаметр кабеля 1,6 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,50 4 600 белый для межприборного монтажа"
},
{
"mark": "НВМ 0,50",
"field": "Цвета",
"old": "",
"new": "белый, чёрный, фиолетовый, коричневый, красный, серый, синий, зелёный, жёлтый",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_50_4_600_belyy/ — 19.10.2026: Арт. 100074 | Цена за 1 км с НДС 23980 | Число и диаметр проволок 7х0,30 | Сечение жилы 0.5 | Диаметр кабеля 1,6 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,50 4 600 белый для межприборного монтажа"
},
{
"mark": "НВМ 0,50",
"field": "Наружный диаметр, мм",
"old": "1,8",
"new": "1,6",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_50_4_600_belyy/ — 19.10.2026: Арт. 100074 | Цена за 1 км с НДС 23980 | Число и диаметр проволок 7х0,30 | Сечение жилы 0.5 | Диаметр кабеля 1,6 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,50 4 600 белый для межприборного монтажа (диаметр кабеля)"
},
{
"mark": "НВМ 0,75",
"field": "Артикул поставщика",
"old": "100022",
"new": "100021",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_75_3_600_chernyy/ — 19.10.2026: Арт. 100021 | Цена за 1 км с НДС 34870 | Число и диаметр проволок 7х0,37 | Сечение жилы 0.75 | Диаметр кабеля 1,81 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,75 3 600 черный для межприборного монтажа"
},
{
"mark": "НВМ 0,75",
"field": "Ссылка",
"old": "",
"new": "https://paritet.su/catalog/nvm/nvm_0_75_3_600_chernyy/",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_75_3_600_chernyy/ — 19.10.2026: Арт. 100021 | Цена за 1 км с НДС 34870 | Число и диаметр проволок 7х0,37 | Сечение жилы 0.75 | Диаметр кабеля 1,81 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,75 3 600 черный для межприборного монтажа"
},
{
"mark": "НВМ 0,75",
"field": "Цена поставщика, ₽/м",
"old": "28,58",
"new": "34,87",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_75_3_600_chernyy/ — 19.10.2026: Арт. 100021 | Цена за 1 км с НДС 34870 | Число и диаметр проволок 7х0,37 | Сечение жилы 0.75 | Диаметр кабеля 1,81 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,75 3 600 черный для межприборного монтажа; цена за 1 км с НДС / 1000"
},
{
"mark": "НВМ 0,75",
"field": "Дата цены",
"old": "05.10.2026",
"new": "19.10.2026",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_75_3_600_chernyy/ — 19.10.2026: Арт. 100021 | Цена за 1 км с НДС 34870 | Число и диаметр проволок 7х0,37 | Сечение жилы 0.75 | Диаметр кабеля 1,81 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,75 3 600 черный для межприборного монтажа"
},
{
"mark": "НВМ 0,75",
"field": "Жила: проволок × диаметр, мм",
"old": "",
"new": "7×0,37",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_75_3_600_chernyy/ — 19.10.2026: Арт. 100021 | Цена за 1 км с НДС 34870 | Число и диаметр проволок 7х0,37 | Сечение жилы 0.75 | Диаметр кабеля 1,81 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,75 3 600 черный для межприборного монтажа"
},
{
"mark": "НВМ 0,75",
"field": "Цвета",
"old": "",
"new": "чёрный, красный",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_75_3_600_chernyy/ — 19.10.2026: Арт. 100021 | Цена за 1 км с НДС 34870 | Число и диаметр проволок 7х0,37 | Сечение жилы 0.75 | Диаметр кабеля 1,81 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,75 3 600 черный для межприборного монтажа"
},
{
"mark": "НВМ 0,75",
"field": "Наружный диаметр, мм",
"old": "2,1",
"new": "1,81",
"source": "Паритет (paritet.su), https://paritet.su/catalog/nvm/nvm_0_75_3_600_chernyy/ — 19.10.2026: Арт. 100021 | Цена за 1 км с НДС 34870 | Число и диаметр проволок 7х0,37 | Сечение жилы 0.75 | Диаметр кабеля 1,81 | хлебные крошки: Монтажные провода НВМ. ГОСТ 17515-72 | title: НВМ-0,75 3 600 черный для межприборного монтажа (диаметр кабеля)"
},
{
"mark": "МГТФ 1,0",
"field": "Жила: проволок × диаметр, мм",
"old": "",
"new": "19×0,25",
"source": "Чувашкабель, каталог https://chuvashcable.ru/upload/iblock/a4f/goeqhetkh8zux7yjhmtnpfmeeuf1zogt.pdf, стр. 16, «Число и диаметр проволок в жиле, шт.×мм»"
},
{
"mark": "МГШВ 0,12",
"field": "Жила: проволок × диаметр, мм",
"old": "",
"new": "7×0,15",
"source": "Чувашкабель, каталог https://chuvashcable.ru/upload/iblock/a4f/goeqhetkh8zux7yjhmtnpfmeeuf1zogt.pdf, стр. 29, «Число и диаметр проволок в жиле, шт. х мм»"
},
{
"mark": "МГШВ 0,50",
"field": "Жила: проволок × диаметр, мм",
"old": "16×0,20",
"new": "19×0,18",
"source": "Чувашкабель, каталог https://chuvashcable.ru/upload/iblock/a4f/goeqhetkh8zux7yjhmtnpfmeeuf1zogt.pdf, стр. 29, «Число и диаметр проволок в жиле, шт. х мм»"
},
{
"mark": "МГШВ 0,75",
"field": "Жила: проволок × диаметр, мм",
"old": "24×0,20",
"new": "23×0,20",
"source": "Чувашкабель, каталог https://chuvashcable.ru/upload/iblock/a4f/goeqhetkh8zux7yjhmtnpfmeeuf1zogt.pdf, стр. 29, «Число и диаметр проволок в жиле, шт. х мм»"
},
{
"mark": "МГШВ 1,0",
"field": "Жила: проволок × диаметр, мм",
"old": "19×0,26",
"new": "19×0,25",
"source": "Чувашкабель, каталог https://chuvashcable.ru/upload/iblock/a4f/goeqhetkh8zux7yjhmtnpfmeeuf1zogt.pdf, стр. 29, «Число и диаметр проволок в жиле, шт. х мм»"
},
{
"mark": "МГШВ 1,5",
"field": "Жила: проволок × диаметр, мм",
"old": "19×0,32",
"new": "19×0,315",
"source": "Чувашкабель, каталог https://chuvashcable.ru/upload/iblock/a4f/goeqhetkh8zux7yjhmtnpfmeeuf1zogt.pdf, стр. 29, «Число и диаметр проволок в жиле, шт. х мм»"
},
{
"mark": "МГШВ 0,12",
"field": "Источник данных",
"old": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с волокнистой или плёночной и ПВХ изоляцией, ТУ 16-505.437-82», табл. 1 (Ø не более), табл. 2 (класс жилы), табл. 3 (напряжение); Камкабель, лист «Провод МГШВ» (табл. размеров) — radiodetali.perm.ru/goodstech/28963_3533.pdf",
"new": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с волокнистой или плёночной и ПВХ изоляцией, ТУ 16-505.437-82», табл. 1 (Ø не более), табл. 2 (класс жилы), табл. 3 (напряжение); Камкабель, лист «Провод МГШВ» (табл. размеров) — radiodetali.perm.ru/goodstech/28963_3533.pdf; 19.10.2026: Дата цены, Жила: проволок × диаметр, мм, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/nv-nve/mgshv/; https://chuvashcable.ru/upload/iblock/a4f/goeqhetkh8zux7yjhmtnpfmeeuf1zogt.pdf",
"source": "журнал"
},
{
"mark": "МГШВ 0,14",
"field": "Источник данных",
"old": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с волокнистой или плёночной и ПВХ изоляцией, ТУ 16-505.437-82», табл. 1 (Ø не более), табл. 2 (класс жилы), табл. 3 (напряжение); Камкабель, лист «Провод МГШВ» (табл. размеров) — radiodetali.perm.ru/goodstech/28963_3533.pdf",
"new": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с волокнистой или плёночной и ПВХ изоляцией, ТУ 16-505.437-82», табл. 1 (Ø не более), табл. 2 (класс жилы), табл. 3 (напряжение); Камкабель, лист «Провод МГШВ» (табл. размеров) — radiodetali.perm.ru/goodstech/28963_3533.pdf; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/nv-nve/mgshv/",
"source": "журнал"
},
{
"mark": "МГШВ 0,2",
"field": "Источник данных",
"old": "electro-mpo.ru карточка (только чёрный цвет представлен на странице); страндинг жилы страницей не уточнён, температурный диапазон не указан — решено агентом — проверить; ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с волокнистой или плёночной и ПВХ изоляцией, ТУ 16-505.437-82», табл. 1 (Ø не более), табл. 2 (класс жилы), табл. 3 (напряжение); Камкабель, лист «Провод МГШВ» (табл. размеров) — radiodetali.perm.ru/goodstech/28963_3533.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки)",
"new": "electro-mpo.ru карточка (только чёрный цвет представлен на странице); страндинг жилы страницей не уточнён, температурный диапазон не указан — решено агентом — проверить; ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с волокнистой или плёночной и ПВХ изоляцией, ТУ 16-505.437-82», табл. 1 (Ø не более), табл. 2 (класс жилы), табл. 3 (напряжение); Камкабель, лист «Провод МГШВ» (табл. размеров) — radiodetali.perm.ru/goodstech/28963_3533.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки); 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/nv-nve/mgshv/",
"source": "журнал"
},
{
"mark": "МГШВ 0,35",
"field": "Источник данных",
"old": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с волокнистой или плёночной и ПВХ изоляцией, ТУ 16-505.437-82», табл. 1 (Ø не более), табл. 2 (класс жилы), табл. 3 (напряжение); Камкабель, лист «Провод МГШВ» (табл. размеров) — radiodetali.perm.ru/goodstech/28963_3533.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки)",
"new": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с волокнистой или плёночной и ПВХ изоляцией, ТУ 16-505.437-82», табл. 1 (Ø не более), табл. 2 (класс жилы), табл. 3 (напряжение); Камкабель, лист «Провод МГШВ» (табл. размеров) — radiodetali.perm.ru/goodstech/28963_3533.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки); 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/nv-nve/mgshv/",
"source": "журнал"
},
{
"mark": "МГШВ 0,50",
"field": "Источник данных",
"old": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с волокнистой или плёночной и ПВХ изоляцией, ТУ 16-505.437-82», табл. 1 (Ø не более), табл. 2 (класс жилы), табл. 3 (напряжение); Камкабель, лист «Провод МГШВ» (табл. размеров) — radiodetali.perm.ru/goodstech/28963_3533.pdf",
"new": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с волокнистой или плёночной и ПВХ изоляцией, ТУ 16-505.437-82», табл. 1 (Ø не более), табл. 2 (класс жилы), табл. 3 (напряжение); Камкабель, лист «Провод МГШВ» (табл. размеров) — radiodetali.perm.ru/goodstech/28963_3533.pdf; 19.10.2026: Дата цены, Жила: проволок × диаметр, мм, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/nv-nve/mgshv/; https://chuvashcable.ru/upload/iblock/a4f/goeqhetkh8zux7yjhmtnpfmeeuf1zogt.pdf",
"source": "журнал"
},
{
"mark": "МГШВ 0,75",
"field": "Источник данных",
"old": "snabcable.ru таблица МГШВ по сечениям, строка 0,75мм2: OD 2,5мм, оплётка из полиэфирной нити в два противоположных слоя + ПВХ оболочка, -50…+70°C, 1000В (исп. 2000В/50Гц), ТУ 16-505.437-82. Масса страницей для этого сечения не дана; ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с волокнистой или плёночной и ПВХ изоляцией, ТУ 16-505.437-82», табл. 1 (Ø не более), табл. 2 (класс жилы), табл. 3 (напряжение); Камкабель, лист «Провод МГШВ» (табл. размеров) — radiodetali.perm.ru/goodstech/28963_3533.pdf",
"new": "snabcable.ru таблица МГШВ по сечениям, строка 0,75мм2: OD 2,5мм, оплётка из полиэфирной нити в два противоположных слоя + ПВХ оболочка, -50…+70°C, 1000В (исп. 2000В/50Гц), ТУ 16-505.437-82. Масса страницей для этого сечения не дана; ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с волокнистой или плёночной и ПВХ изоляцией, ТУ 16-505.437-82», табл. 1 (Ø не более), табл. 2 (класс жилы), табл. 3 (напряжение); Камкабель, лист «Провод МГШВ» (табл. размеров) — radiodetali.perm.ru/goodstech/28963_3533.pdf; 19.10.2026: Дата цены, Жила: проволок × диаметр, мм, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/nv-nve/mgshv/; https://chuvashcable.ru/upload/iblock/a4f/goeqhetkh8zux7yjhmtnpfmeeuf1zogt.pdf",
"source": "журнал"
},
{
"mark": "МГШВ 1,0",
"field": "Источник данных",
"old": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с волокнистой или плёночной и ПВХ изоляцией, ТУ 16-505.437-82», табл. 1 (Ø не более), табл. 2 (класс жилы), табл. 3 (напряжение); Камкабель, лист «Провод МГШВ» (табл. размеров) — radiodetali.perm.ru/goodstech/28963_3533.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки)",
"new": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с волокнистой или плёночной и ПВХ изоляцией, ТУ 16-505.437-82», табл. 1 (Ø не более), табл. 2 (класс жилы), табл. 3 (напряжение); Камкабель, лист «Провод МГШВ» (табл. размеров) — radiodetali.perm.ru/goodstech/28963_3533.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки); 19.10.2026: Дата цены, Жила: проволок × диаметр, мм, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/nv-nve/mgshv/; https://chuvashcable.ru/upload/iblock/a4f/goeqhetkh8zux7yjhmtnpfmeeuf1zogt.pdf",
"source": "журнал"
},
{
"mark": "МГШВ 1,5",
"field": "Источник данных",
"old": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с волокнистой или плёночной и ПВХ изоляцией, ТУ 16-505.437-82», табл. 1 (Ø не более), табл. 2 (класс жилы), табл. 3 (напряжение); Камкабель, лист «Провод МГШВ» (табл. размеров) — radiodetali.perm.ru/goodstech/28963_3533.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки)",
"new": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с волокнистой или плёночной и ПВХ изоляцией, ТУ 16-505.437-82», табл. 1 (Ø не более), табл. 2 (класс жилы), табл. 3 (напряжение); Камкабель, лист «Провод МГШВ» (табл. размеров) — radiodetali.perm.ru/goodstech/28963_3533.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки); 19.10.2026: Дата цены, Жила: проволок × диаметр, мм, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/nv-nve/mgshv/; https://chuvashcable.ru/upload/iblock/a4f/goeqhetkh8zux7yjhmtnpfmeeuf1zogt.pdf",
"source": "журнал"
},
{
"mark": "МГТФ 0,03",
"field": "Источник данных",
"old": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные теплостойкие с изоляцией из фторопласта, ТУ 16-505.185-71» (марки, таблица конструкции/размеров, диапазон температур); Холдинг «Кабельный Альянс», каталог «Кабели и провода монтажные», с. 254 «МГТФ, МГТФЭ ТУ 16-505.185-71» — ros-electric.ru (тот же PDF); расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки)",
"new": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные теплостойкие с изоляцией из фторопласта, ТУ 16-505.185-71» (марки, таблица конструкции/размеров, диапазон температур); Холдинг «Кабельный Альянс», каталог «Кабели и провода монтажные», с. 254 «МГТФ, МГТФЭ ТУ 16-505.185-71» — ros-electric.ru (тот же PDF); расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки); 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"source": "журнал"
},
{
"mark": "МГТФ 0,05",
"field": "Источник данных",
"old": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные теплостойкие с изоляцией из фторопласта, ТУ 16-505.185-71» (марки, таблица конструкции/размеров, диапазон температур); Холдинг «Кабельный Альянс», каталог «Кабели и провода монтажные», с. 254 «МГТФ, МГТФЭ ТУ 16-505.185-71» — ros-electric.ru (тот же PDF)",
"new": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные теплостойкие с изоляцией из фторопласта, ТУ 16-505.185-71» (марки, таблица конструкции/размеров, диапазон температур); Холдинг «Кабельный Альянс», каталог «Кабели и провода монтажные», с. 254 «МГТФ, МГТФЭ ТУ 16-505.185-71» — ros-electric.ru (тот же PDF); 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"source": "журнал"
},
{
"mark": "МГТФ 0,07",
"field": "Источник данных",
"old": "chuvashkabel (через поиск), ТУ 16-505.185-71; OD 0,75мм, масса 1,22 кг/км для сечения 0,07мм2. Цена последней закупки указана 0,0 — похоже на ошибку данных (см. МГТФ 0,35 — 16,3) — решено агентом — проверить. Число проволок жилы страницей не уточнено; ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные теплостойкие с изоляцией из фторопласта, ТУ 16-505.185-71» (марки, таблица конструкции/размеров, диапазон температур); Холдинг «Кабельный Альянс», каталог «Кабели и провода монтажные», с. 254 «МГТФ, МГТФЭ ТУ 16-505.185-71» — ros-electric.ru (тот же PDF)",
"new": "chuvashkabel (через поиск), ТУ 16-505.185-71; OD 0,75мм, масса 1,22 кг/км для сечения 0,07мм2. Цена последней закупки указана 0,0 — похоже на ошибку данных (см. МГТФ 0,35 — 16,3) — решено агентом — проверить. Число проволок жилы страницей не уточнено; ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные теплостойкие с изоляцией из фторопласта, ТУ 16-505.185-71» (марки, таблица конструкции/размеров, диапазон температур); Холдинг «Кабельный Альянс», каталог «Кабели и провода монтажные», с. 254 «МГТФ, МГТФЭ ТУ 16-505.185-71» — ros-electric.ru (тот же PDF); 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"source": "журнал"
},
{
"mark": "МГТФ 0,10",
"field": "Источник данных",
"old": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные теплостойкие с изоляцией из фторопласта, ТУ 16-505.185-71» (марки, таблица конструкции/размеров, диапазон температур); Холдинг «Кабельный Альянс», каталог «Кабели и провода монтажные», с. 254 «МГТФ, МГТФЭ ТУ 16-505.185-71» — ros-electric.ru (тот же PDF); Камкабель, лист «Провод МГТФ» (табл. размеров) — radiodetali.perm.ru/goodstech/100011668_3513.pdf",
"new": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные теплостойкие с изоляцией из фторопласта, ТУ 16-505.185-71» (марки, таблица конструкции/размеров, диапазон температур); Холдинг «Кабельный Альянс», каталог «Кабели и провода монтажные», с. 254 «МГТФ, МГТФЭ ТУ 16-505.185-71» — ros-electric.ru (тот же PDF); Камкабель, лист «Провод МГТФ» (табл. размеров) — radiodetali.perm.ru/goodstech/100011668_3513.pdf; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"source": "журнал"
},
{
"mark": "МГТФ 0,12",
"field": "Источник данных",
"old": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные теплостойкие с изоляцией из фторопласта, ТУ 16-505.185-71» (марки, таблица конструкции/размеров, диапазон температур); Холдинг «Кабельный Альянс», каталог «Кабели и провода монтажные», с. 254 «МГТФ, МГТФЭ ТУ 16-505.185-71» — ros-electric.ru (тот же PDF)",
"new": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные теплостойкие с изоляцией из фторопласта, ТУ 16-505.185-71» (марки, таблица конструкции/размеров, диапазон температур); Холдинг «Кабельный Альянс», каталог «Кабели и провода монтажные», с. 254 «МГТФ, МГТФЭ ТУ 16-505.185-71» — ros-electric.ru (тот же PDF); 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"source": "журнал"
},
{
"mark": "МГТФ 0,14",
"field": "Источник данных",
"old": "snabcable.ru таблица МГТФ по сечениям, строка 0,14мм2: OD 0,90мм, масса 2,26кг/км=2,26г/м; общий диапазон серии -60…+220°C, 1500В — ТУ 16-505.185-71 (как у уже занесённых МГТФ 0,07/0,20/0,35/1,0); ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные теплостойкие с изоляцией из фторопласта, ТУ 16-505.185-71» (марки, таблица конструкции/размеров, диапазон температур); Холдинг «Кабельный Альянс», каталог «Кабели и провода монтажные», с. 254 «МГТФ, МГТФЭ ТУ 16-505.185-71» — ros-electric.ru (тот же PDF); Камкабель, лист «Провод МГТФ» (табл. размеров) — radiodetali.perm.ru/goodstech/100011668_3513.pdf",
"new": "snabcable.ru таблица МГТФ по сечениям, строка 0,14мм2: OD 0,90мм, масса 2,26кг/км=2,26г/м; общий диапазон серии -60…+220°C, 1500В — ТУ 16-505.185-71 (как у уже занесённых МГТФ 0,07/0,20/0,35/1,0); ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные теплостойкие с изоляцией из фторопласта, ТУ 16-505.185-71» (марки, таблица конструкции/размеров, диапазон температур); Холдинг «Кабельный Альянс», каталог «Кабели и провода монтажные», с. 254 «МГТФ, МГТФЭ ТУ 16-505.185-71» — ros-electric.ru (тот же PDF); Камкабель, лист «Провод МГТФ» (табл. размеров) — radiodetali.perm.ru/goodstech/100011668_3513.pdf; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"source": "журнал"
},
{
"mark": "МГТФ 0,20",
"field": "Источник данных",
"old": "snabcable.ru таблица МГТФ по сечениям, строка 0,20мм2: OD 1,04мм, масса 2,96кг/км=2,96г/м; общий диапазон серии -60…+220°C, 1500В — ТУ 16-505.185-71 (как у уже существующих в базе МГТФ 0,07/0,35); ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные теплостойкие с изоляцией из фторопласта, ТУ 16-505.185-71» (марки, таблица конструкции/размеров, диапазон температур); Холдинг «Кабельный Альянс», каталог «Кабели и провода монтажные», с. 254 «МГТФ, МГТФЭ ТУ 16-505.185-71» — ros-electric.ru (тот же PDF); расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки)",
"new": "snabcable.ru таблица МГТФ по сечениям, строка 0,20мм2: OD 1,04мм, масса 2,96кг/км=2,96г/м; общий диапазон серии -60…+220°C, 1500В — ТУ 16-505.185-71 (как у уже существующих в базе МГТФ 0,07/0,35); ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные теплостойкие с изоляцией из фторопласта, ТУ 16-505.185-71» (марки, таблица конструкции/размеров, диапазон температур); Холдинг «Кабельный Альянс», каталог «Кабели и провода монтажные», с. 254 «МГТФ, МГТФЭ ТУ 16-505.185-71» — ros-electric.ru (тот же PDF); расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки); 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"source": "журнал"
},
{
"mark": "МГТФ 0,35",
"field": "Источник данных",
"old": "chuvashkabel (через поиск), ТУ 16-505.185-71; OD 1,19мм, масса 4,25 кг/км для сечения 0,35мм2. Число проволок жилы страницей не уточнено; ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные теплостойкие с изоляцией из фторопласта, ТУ 16-505.185-71» (марки, таблица конструкции/размеров, диапазон температур); Холдинг «Кабельный Альянс», каталог «Кабели и провода монтажные», с. 254 «МГТФ, МГТФЭ ТУ 16-505.185-71» — ros-electric.ru (тот же PDF); расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки)",
"new": "chuvashkabel (через поиск), ТУ 16-505.185-71; OD 1,19мм, масса 4,25 кг/км для сечения 0,35мм2. Число проволок жилы страницей не уточнено; ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные теплостойкие с изоляцией из фторопласта, ТУ 16-505.185-71» (марки, таблица конструкции/размеров, диапазон температур); Холдинг «Кабельный Альянс», каталог «Кабели и провода монтажные», с. 254 «МГТФ, МГТФЭ ТУ 16-505.185-71» — ros-electric.ru (тот же PDF); расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки); 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"source": "журнал"
},
{
"mark": "МГТФ 1,0",
"field": "Источник данных",
"old": "snabcable.ru таблица МГТФ по сечениям, строка 1,0мм2: OD 1,9мм, масса 10,75кг/км=10,75г/м; общий диапазон серии -60…+220°C, 1500В — ТУ 16-505.185-71",
"new": "snabcable.ru таблица МГТФ по сечениям, строка 1,0мм2: OD 1,9мм, масса 10,75кг/км=10,75г/м; общий диапазон серии -60…+220°C, 1500В — ТУ 16-505.185-71; 19.10.2026: Дата цены, Жила: проволок × диаметр, мм, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/; https://chuvashcable.ru/upload/iblock/a4f/goeqhetkh8zux7yjhmtnpfmeeuf1zogt.pdf",
"source": "журнал"
},
{
"mark": "МК 26-13 0,07",
"field": "Источник данных",
"old": "Чувашкабель, «Бортовые монтажные провода МК, МКЭ. Техническое описание», ТУ 16.К05-016-2003 — https://chuvkab.nt-rt.ru/images/manuals/mk_26_13.pdf",
"new": "Чувашкабель, «Бортовые монтажные провода МК, МКЭ. Техническое описание», ТУ 16.К05-016-2003 — https://chuvkab.nt-rt.ru/images/manuals/mk_26_13.pdf; 19.10.2026: Дата цены, Поставщик, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/",
"source": "журнал"
},
{
"mark": "МК 26-13 0,12",
"field": "Источник данных",
"old": "Чувашкабель, «Бортовые монтажные провода МК, МКЭ. Техническое описание», ТУ 16.К05-016-2003 — https://chuvkab.nt-rt.ru/images/manuals/mk_26_13.pdf",
"new": "Чувашкабель, «Бортовые монтажные провода МК, МКЭ. Техническое описание», ТУ 16.К05-016-2003 — https://chuvkab.nt-rt.ru/images/manuals/mk_26_13.pdf; 19.10.2026: Дата цены, Поставщик, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/",
"source": "журнал"
},
{
"mark": "МК 26-13 0,20",
"field": "Источник данных",
"old": "Чувашкабель, «Бортовые монтажные провода МК, МКЭ. Техническое описание», ТУ 16.К05-016-2003 — https://chuvkab.nt-rt.ru/images/manuals/mk_26_13.pdf",
"new": "Чувашкабель, «Бортовые монтажные провода МК, МКЭ. Техническое описание», ТУ 16.К05-016-2003 — https://chuvkab.nt-rt.ru/images/manuals/mk_26_13.pdf; 19.10.2026: Дата цены, Поставщик, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/",
"source": "журнал"
},
{
"mark": "МК 26-13 0,35",
"field": "Источник данных",
"old": "Чувашкабель, «Бортовые монтажные провода МК, МКЭ. Техническое описание», ТУ 16.К05-016-2003 — https://chuvkab.nt-rt.ru/images/manuals/mk_26_13.pdf",
"new": "Чувашкабель, «Бортовые монтажные провода МК, МКЭ. Техническое описание», ТУ 16.К05-016-2003 — https://chuvkab.nt-rt.ru/images/manuals/mk_26_13.pdf; 19.10.2026: Дата цены, Поставщик, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/",
"source": "журнал"
},
{
"mark": "МК 26-13 0,50",
"field": "Источник данных",
"old": "Чувашкабель, «Бортовые монтажные провода МК, МКЭ. Техническое описание», ТУ 16.К05-016-2003 — https://chuvkab.nt-rt.ru/images/manuals/mk_26_13.pdf",
"new": "Чувашкабель, «Бортовые монтажные провода МК, МКЭ. Техническое описание», ТУ 16.К05-016-2003 — https://chuvkab.nt-rt.ru/images/manuals/mk_26_13.pdf; 19.10.2026: Дата цены, Поставщик, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/onboard/bpvl-bpvle/mk-26-13/",
"source": "журнал"
},
{
"mark": "МС 15-11 0,08",
"field": "Источник данных",
"old": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные марок МС 15-11 … ТУ 16-705.199-81», марки, табл. 1 (Ø не более), диапазон температур",
"new": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные марок МС 15-11 … ТУ 16-705.199-81», марки, табл. 1 (Ø не более), диапазон температур; 19.10.2026: Дата цены, Поставщик, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/",
"source": "журнал"
},
{
"mark": "МС 15-11 0,12",
"field": "Источник данных",
"old": "cable.ru + tatlon.ru описание серии МС 15-11: монтажный теплостойкий провод, многопроволочная луж.медная жила 4 класса (ГОСТ 22483), монолитная изоляция фторопласт-4МБ 0,2мм, -60…+155°C, 100В переменного тока до 50МГц, ТУ16-705.199-81, 7 цветов исполнения. Ссылка покупателя (vpkvazar.ru) вернула 404 — использованы альтернативные источники. Наружный диаметр не найден — оставлен пустым; ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные марок МС 15-11 … ТУ 16-705.199-81», марки, табл. 1 (Ø не более), диапазон температур",
"new": "cable.ru + tatlon.ru описание серии МС 15-11: монтажный теплостойкий провод, многопроволочная луж.медная жила 4 класса (ГОСТ 22483), монолитная изоляция фторопласт-4МБ 0,2мм, -60…+155°C, 100В переменного тока до 50МГц, ТУ16-705.199-81, 7 цветов исполнения. Ссылка покупателя (vpkvazar.ru) вернула 404 — использованы альтернативные источники. Наружный диаметр не найден — оставлен пустым; ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные марок МС 15-11 … ТУ 16-705.199-81», марки, табл. 1 (Ø не более), диапазон температур; 19.10.2026: Дата цены, Поставщик, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/",
"source": "журнал"
},
{
"mark": "МС 15-11 0,20",
"field": "Источник данных",
"old": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные марок МС 15-11 … ТУ 16-705.199-81», марки, табл. 1 (Ø не более), диапазон температур",
"new": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные марок МС 15-11 … ТУ 16-705.199-81», марки, табл. 1 (Ø не более), диапазон температур; 19.10.2026: Дата цены, Поставщик, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/",
"source": "журнал"
},
{
"mark": "МС 15-11 0,35",
"field": "Источник данных",
"old": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные марок МС 15-11 … ТУ 16-705.199-81», марки, табл. 1 (Ø не более), диапазон температур",
"new": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные марок МС 15-11 … ТУ 16-705.199-81», марки, табл. 1 (Ø не более), диапазон температур; 19.10.2026: Дата цены, Поставщик, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/",
"source": "журнал"
},
{
"mark": "МС 15-11 0,50",
"field": "Источник данных",
"old": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные марок МС 15-11 … ТУ 16-705.199-81», марки, табл. 1 (Ø не более), диапазон температур",
"new": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные марок МС 15-11 … ТУ 16-705.199-81», марки, табл. 1 (Ø не более), диапазон температур; 19.10.2026: Дата цены, Поставщик, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/onboard/mk-26-13-mke-26-13/ms-15-11/",
"source": "журнал"
},
{
"mark": "МС 16-13 0,03",
"field": "Источник данных",
"old": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с изоляцией из спекаемой плёнки, ТУ 16-505.083-78», табл. 2 (одножильные: проволоки, Ø не более), диапазон температур; Камкабель, лист «МС 16-13, ТУ 16-505.083-78» — optochip.org/docum/store/factory/series/101775/1490348780-5-10678.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки)",
"new": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с изоляцией из спекаемой плёнки, ТУ 16-505.083-78», табл. 2 (одножильные: проволоки, Ø не более), диапазон температур; Камкабель, лист «МС 16-13, ТУ 16-505.083-78» — optochip.org/docum/store/factory/series/101775/1490348780-5-10678.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки); 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/",
"source": "журнал"
},
{
"mark": "МС 16-13 0,05",
"field": "Источник данных",
"old": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с изоляцией из спекаемой плёнки, ТУ 16-505.083-78», табл. 2 (одножильные: проволоки, Ø не более), диапазон температур; Камкабель, лист «МС 16-13, ТУ 16-505.083-78» — optochip.org/docum/store/factory/series/101775/1490348780-5-10678.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки)",
"new": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с изоляцией из спекаемой плёнки, ТУ 16-505.083-78», табл. 2 (одножильные: проволоки, Ø не более), диапазон температур; Камкабель, лист «МС 16-13, ТУ 16-505.083-78» — optochip.org/docum/store/factory/series/101775/1490348780-5-10678.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки); 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/",
"source": "журнал"
},
{
"mark": "МС16-13 0,08",
"field": "Источник данных",
"old": "snabcable.ru + агрегированные данные по ряду сечений МС16-13 (bystrokabel.ru); жила 7×0,15мм для 0,08мм2 — не проверено первичным текстом ТУ 16-505.083-78 — решено агентом — проверить; ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с изоляцией из спекаемой плёнки, ТУ 16-505.083-78», табл. 2 (одножильные: проволоки, Ø не более), диапазон температур; Камкабель, лист «МС 16-13, ТУ 16-505.083-78» — optochip.org/docum/store/factory/series/101775/1490348780-5-10678.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки)",
"new": "snabcable.ru + агрегированные данные по ряду сечений МС16-13 (bystrokabel.ru); жила 7×0,15мм для 0,08мм2 — не проверено первичным текстом ТУ 16-505.083-78 — решено агентом — проверить; ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с изоляцией из спекаемой плёнки, ТУ 16-505.083-78», табл. 2 (одножильные: проволоки, Ø не более), диапазон температур; Камкабель, лист «МС 16-13, ТУ 16-505.083-78» — optochip.org/docum/store/factory/series/101775/1490348780-5-10678.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки); 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/",
"source": "журнал"
},
{
"mark": "МС16-13 0,12",
"field": "Источник данных",
"old": "snabcable.ru + bystrokabel.ru (карточка МС16-13 0,12): жила 7×0,15мм, OD 0,90мм, масса 1,9кг/км, ТУ 16-505.083-78 — решено агентом — проверить (данные по типовому ряду сечений, не первичный текст ТУ). Цена закупки у разных цветов расходится (97,8–100,7) — взято среднее 99,1 — решено агентом — проверить. Объединены 5 цветовых позиций (красный,чёрный,белый,зелёный,синий); ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с изоляцией из спекаемой плёнки, ТУ 16-505.083-78», табл. 2 (одножильные: проволоки, Ø не более), диапазон температур; Камкабель, лист «МС 16-13, ТУ 16-505.083-78» — optochip.org/docum/store/factory/series/101775/1490348780-5-10678.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки)",
"new": "snabcable.ru + bystrokabel.ru (карточка МС16-13 0,12): жила 7×0,15мм, OD 0,90мм, масса 1,9кг/км, ТУ 16-505.083-78 — решено агентом — проверить (данные по типовому ряду сечений, не первичный текст ТУ). Цена закупки у разных цветов расходится (97,8–100,7) — взято среднее 99,1 — решено агентом — проверить. Объединены 5 цветовых позиций (красный,чёрный,белый,зелёный,синий); ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с изоляцией из спекаемой плёнки, ТУ 16-505.083-78», табл. 2 (одножильные: проволоки, Ø не более), диапазон температур; Камкабель, лист «МС 16-13, ТУ 16-505.083-78» — optochip.org/docum/store/factory/series/101775/1490348780-5-10678.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки); 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/",
"source": "журнал"
},
{
"mark": "МС 16-13 0,20",
"field": "Источник данных",
"old": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с изоляцией из спекаемой плёнки, ТУ 16-505.083-78», табл. 2 (одножильные: проволоки, Ø не более), диапазон температур; Камкабель, лист «МС 16-13, ТУ 16-505.083-78» — optochip.org/docum/store/factory/series/101775/1490348780-5-10678.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки)",
"new": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с изоляцией из спекаемой плёнки, ТУ 16-505.083-78», табл. 2 (одножильные: проволоки, Ø не более), диапазон температур; Камкабель, лист «МС 16-13, ТУ 16-505.083-78» — optochip.org/docum/store/factory/series/101775/1490348780-5-10678.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки); 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/",
"source": "журнал"
},
{
"mark": "МС 16-13 0,35",
"field": "Источник данных",
"old": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с изоляцией из спекаемой плёнки, ТУ 16-505.083-78», табл. 2 (одножильные: проволоки, Ø не более), диапазон температур; Камкабель, лист «МС 16-13, ТУ 16-505.083-78» — optochip.org/docum/store/factory/series/101775/1490348780-5-10678.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки)",
"new": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с изоляцией из спекаемой плёнки, ТУ 16-505.083-78», табл. 2 (одножильные: проволоки, Ø не более), диапазон температур; Камкабель, лист «МС 16-13, ТУ 16-505.083-78» — optochip.org/docum/store/factory/series/101775/1490348780-5-10678.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки); 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/",
"source": "журнал"
},
{
"mark": "МС 16-13 0,50",
"field": "Источник данных",
"old": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с изоляцией из спекаемой плёнки, ТУ 16-505.083-78», табл. 2 (одножильные: проволоки, Ø не более), диапазон температур; Камкабель, лист «МС 16-13, ТУ 16-505.083-78» — optochip.org/docum/store/factory/series/101775/1490348780-5-10678.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки)",
"new": "ВНИИКП, «Информационно-технический сборник. Изделия кабельные. Том 4. Кабели и провода монтажные. Часть I» — meganorm.ru/Data2/1/4293851/4293851304.htm, раздел «Провода монтажные с изоляцией из спекаемой плёнки, ТУ 16-505.083-78», табл. 2 (одножильные: проволоки, Ø не более), диапазон температур; Камкабель, лист «МС 16-13, ТУ 16-505.083-78» — optochip.org/docum/store/factory/series/101775/1490348780-5-10678.pdf; расчёт по конструкции жилы из того же источника: скрутка 1+6 → Ø = 3·d, 1+6+12 → Ø = 5·d (d — Ø проволоки); 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/onboard/ms-e-o-16-26-13/ms-16-13/",
"source": "журнал"
},
{
"mark": "НВ-4 0,12",
"field": "Источник данных",
"old": "snabcable.ru (пример веса 2,35 кг/км дан на странице именно для сечения 0,12мм2). Объединены 6 цветовых позиций; ГОСТ 17515-72 (ред. изм. 1–8) «Провода монтажные с пластмассовой изоляцией. ТУ» — meganorm.ru/Data1/21/21444/index.htm; было в «Производители»: марка НВ, 600 В; бывал давальческим",
"new": "snabcable.ru (пример веса 2,35 кг/км дан на странице именно для сечения 0,12мм2). Объединены 6 цветовых позиций; ГОСТ 17515-72 (ред. изм. 1–8) «Провода монтажные с пластмассовой изоляцией. ТУ» — meganorm.ru/Data1/21/21444/index.htm; было в «Производители»: марка НВ, 600 В; бывал давальческим; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/nv-nve/nv/",
"source": "журнал"
},
{
"mark": "НВ-4 0,20",
"field": "Источник данных",
"old": "snabcable.ru; страндинг жилы страницей не указан. У НВ-4 есть варианты на 600В (OD 1,5мм) и 1000В (OD 1,7мм) — взят вариант 600В как базовый — решено агентом — проверить. Объединены 9 цветовых позиций; ГОСТ 17515-72 (ред. изм. 1–8) «Провода монтажные с пластмассовой изоляцией. ТУ» — meganorm.ru/Data1/21/21444/index.htm; было в «Производители»: марка НВ, 600 В",
"new": "snabcable.ru; страндинг жилы страницей не указан. У НВ-4 есть варианты на 600В (OD 1,5мм) и 1000В (OD 1,7мм) — взят вариант 600В как базовый — решено агентом — проверить. Объединены 9 цветовых позиций; ГОСТ 17515-72 (ред. изм. 1–8) «Провода монтажные с пластмассовой изоляцией. ТУ» — meganorm.ru/Data1/21/21444/index.htm; было в «Производители»: марка НВ, 600 В; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/nv-nve/nv/",
"source": "журнал"
},
{
"mark": "НВ-4 0,35",
"field": "Источник данных",
"old": "snabcable.ru; объединены варианты написания \"НВ-4 0,35мм2\", \"НВ4-0,35мм2\", \"НВ4 0,35\" — канонизировано как НВ-4 0,35; ГОСТ 17515-72 (ред. изм. 1–8) «Провода монтажные с пластмассовой изоляцией. ТУ» — meganorm.ru/Data1/21/21444/index.htm; было в «Производители»: марка НВ, 600 В",
"new": "snabcable.ru; объединены варианты написания \"НВ-4 0,35мм2\", \"НВ4-0,35мм2\", \"НВ4 0,35\" — канонизировано как НВ-4 0,35; ГОСТ 17515-72 (ред. изм. 1–8) «Провода монтажные с пластмассовой изоляцией. ТУ» — meganorm.ru/Data1/21/21444/index.htm; было в «Производители»: марка НВ, 600 В; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/nv-nve/nv/",
"source": "журнал"
},
{
"mark": "НВ-4 0,50",
"field": "Источник данных",
"old": "snabcable.ru; вариант 600В (OD 1,8мм) взят как базовый, есть также вариант 1000В (OD 2,0мм) — решено агентом — проверить; ГОСТ 17515-72 (ред. изм. 1–8) «Провода монтажные с пластмассовой изоляцией. ТУ» — meganorm.ru/Data1/21/21444/index.htm; было в «Производители»: марка НВ, 600 В",
"new": "snabcable.ru; вариант 600В (OD 1,8мм) взят как базовый, есть также вариант 1000В (OD 2,0мм) — решено агентом — проверить; ГОСТ 17515-72 (ред. изм. 1–8) «Провода монтажные с пластмассовой изоляцией. ТУ» — meganorm.ru/Data1/21/21444/index.htm; было в «Производители»: марка НВ, 600 В; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/nv-nve/nv/",
"source": "журнал"
},
{
"mark": "НВ-4 0,75",
"field": "Источник данных",
"old": "snabcable.ru; цена последней закупки у разных цветов в одном месяце расходится (15,6-19,5) — взято модальное значение 15,6 — решено агентом — проверить; ГОСТ 17515-72 (ред. изм. 1–8) «Провода монтажные с пластмассовой изоляцией. ТУ» — meganorm.ru/Data1/21/21444/index.htm; было в «Производители»: марка НВ, 600 В",
"new": "snabcable.ru; цена последней закупки у разных цветов в одном месяце расходится (15,6-19,5) — взято модальное значение 15,6 — решено агентом — проверить; ГОСТ 17515-72 (ред. изм. 1–8) «Провода монтажные с пластмассовой изоляцией. ТУ» — meganorm.ru/Data1/21/21444/index.htm; было в «Производители»: марка НВ, 600 В; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/nv-nve/nv/",
"source": "журнал"
},
{
"mark": "НВ-4 1,0",
"field": "Источник данных",
"old": "ГОСТ 17515-72 (ред. изм. 1–8) «Провода монтажные с пластмассовой изоляцией. ТУ» — meganorm.ru/Data1/21/21444/index.htm; было в «Производители»: марка НВ, 600 В",
"new": "ГОСТ 17515-72 (ред. изм. 1–8) «Провода монтажные с пластмассовой изоляцией. ТУ» — meganorm.ru/Data1/21/21444/index.htm; было в «Производители»: марка НВ, 600 В; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/nv-nve/nv/",
"source": "журнал"
},
{
"mark": "НВ-4 1,50",
"field": "Источник данных",
"old": "snabcable.ru; вариант 600В (OD 2,5мм, масса ~16,95 кг/км) взят как базовый — есть также 1000В/НВ-3 1000В (OD 2,7мм) — решено агентом — проверить. Цена последней закупки в сводке 0,0 у всех трёх цветов — похоже на ошибку данных — решено агентом — проверить. Объединены 3 цветовые позиции (жёлто-зелёный,коричневый,синий); ГОСТ 17515-72 (ред. изм. 1–8) «Провода монтажные с пластмассовой изоляцией. ТУ» — meganorm.ru/Data1/21/21444/index.htm; было в «Производители»: марка НВ, 600 В",
"new": "snabcable.ru; вариант 600В (OD 2,5мм, масса ~16,95 кг/км) взят как базовый — есть также 1000В/НВ-3 1000В (OD 2,7мм) — решено агентом — проверить. Цена последней закупки в сводке 0,0 у всех трёх цветов — похоже на ошибку данных — решено агентом — проверить. Объединены 3 цветовые позиции (жёлто-зелёный,коричневый,синий); ГОСТ 17515-72 (ред. изм. 1–8) «Провода монтажные с пластмассовой изоляцией. ТУ» — meganorm.ru/Data1/21/21444/index.htm; было в «Производители»: марка НВ, 600 В; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/nv-nve/nv/",
"source": "журнал"
},
{
"mark": "ПВАМ 0,35",
"field": "Источник данных",
"old": "snabcable.ru; Обозначение изделия АО «Электрокабель» Кольчугинский завод «ПВАМ-0,35 48В ТУ 16.К19-12-2003» (карточка товара vseinstrumenti.ru/product/provod-ao-elektrokabel-kolchuginskij-zavod-pvam-0-35-48v-tu-16-k19-12-2003-chernyj-buhta-500-m-100000084940-ch-500-24473652/); бывал давальческим",
"new": "snabcable.ru; Обозначение изделия АО «Электрокабель» Кольчугинский завод «ПВАМ-0,35 48В ТУ 16.К19-12-2003» (карточка товара vseinstrumenti.ru/product/provod-ao-elektrokabel-kolchuginskij-zavod-pvam-0-35-48v-tu-16-k19-12-2003-chernyj-buhta-500-m-100000084940-ch-500-24473652/); бывал давальческим; 19.10.2026: Дата цены, Поставщик, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/car/pvam-pvame-pvamde/pvam/",
"source": "журнал"
},
{
"mark": "ПВАМ 0,50",
"field": "Источник данных",
"old": "snabcable.ru; жила — медь многопроволочная 5-го класса гибкости, страндинг страницей не указан; Обозначение изделия АО «Электрокабель» Кольчугинский завод «ПВАМ-0,35 48В ТУ 16.К19-12-2003» (карточка товара vseinstrumenti.ru/product/provod-ao-elektrokabel-kolchuginskij-zavod-pvam-0-35-48v-tu-16-k19-12-2003-chernyj-buhta-500-m-100000084940-ch-500-24473652/)",
"new": "snabcable.ru; жила — медь многопроволочная 5-го класса гибкости, страндинг страницей не указан; Обозначение изделия АО «Электрокабель» Кольчугинский завод «ПВАМ-0,35 48В ТУ 16.К19-12-2003» (карточка товара vseinstrumenti.ru/product/provod-ao-elektrokabel-kolchuginskij-zavod-pvam-0-35-48v-tu-16-k19-12-2003-chernyj-buhta-500-m-100000084940-ch-500-24473652/); 19.10.2026: Дата цены, Поставщик, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/car/pvam-pvame-pvamde/pvam/",
"source": "журнал"
},
{
"mark": "ПВАМ 0,75",
"field": "Источник данных",
"old": "snabcable.ru (общая страница ПВАМ, сечение 0,75мм2: OD 1,9мм, прайс поставщика 15,9); цена последней закупки в сводке закупок указана 0,0 — расходится с прайсом, похоже на ошибку данных — решено агентом — проверить. Ссылка на конкретную карточку покупателем не дана; Обозначение изделия АО «Электрокабель» Кольчугинский завод «ПВАМ-0,35 48В ТУ 16.К19-12-2003» (карточка товара vseinstrumenti.ru/product/provod-ao-elektrokabel-kolchuginskij-zavod-pvam-0-35-48v-tu-16-k19-12-2003-chernyj-buhta-500-m-100000084940-ch-500-24473652/); бывал давальческим",
"new": "snabcable.ru (общая страница ПВАМ, сечение 0,75мм2: OD 1,9мм, прайс поставщика 15,9); цена последней закупки в сводке закупок указана 0,0 — расходится с прайсом, похоже на ошибку данных — решено агентом — проверить. Ссылка на конкретную карточку покупателем не дана; Обозначение изделия АО «Электрокабель» Кольчугинский завод «ПВАМ-0,35 48В ТУ 16.К19-12-2003» (карточка товара vseinstrumenti.ru/product/provod-ao-elektrokabel-kolchuginskij-zavod-pvam-0-35-48v-tu-16-k19-12-2003-chernyj-buhta-500-m-100000084940-ch-500-24473652/); бывал давальческим; 19.10.2026: Дата цены, Поставщик, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/car/pvam-pvame-pvamde/pvam/",
"source": "журнал"
},
{
"mark": "ПВАМ 1,0",
"field": "Источник данных",
"old": "snabcable.ru; жила — медь многопроволочная (класс гибкости страницей не указан, по аналогии с сечениями 0,50/0,35 в базе — 5-й класс) — решено агентом — проверить. Объединены 3 цветовые позиции (голубой,красный,чёрный); Обозначение изделия АО «Электрокабель» Кольчугинский завод «ПВАМ-0,35 48В ТУ 16.К19-12-2003» (карточка товара vseinstrumenti.ru/product/provod-ao-elektrokabel-kolchuginskij-zavod-pvam-0-35-48v-tu-16-k19-12-2003-chernyj-buhta-500-m-100000084940-ch-500-24473652/)",
"new": "snabcable.ru; жила — медь многопроволочная (класс гибкости страницей не указан, по аналогии с сечениями 0,50/0,35 в базе — 5-й класс) — решено агентом — проверить. Объединены 3 цветовые позиции (голубой,красный,чёрный); Обозначение изделия АО «Электрокабель» Кольчугинский завод «ПВАМ-0,35 48В ТУ 16.К19-12-2003» (карточка товара vseinstrumenti.ru/product/provod-ao-elektrokabel-kolchuginskij-zavod-pvam-0-35-48v-tu-16-k19-12-2003-chernyj-buhta-500-m-100000084940-ch-500-24473652/); 19.10.2026: Дата цены, Поставщик, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/car/pvam-pvame-pvamde/pvam/",
"source": "журнал"
},
{
"mark": "ПВАМ 1,50",
"field": "Источник данных",
"old": "snabcable.ru (общая страница ПВАМ, сечение 1,5мм2: OD 2,4мм); ссылка на конкретную карточку покупателем не дана; Обозначение изделия АО «Электрокабель» Кольчугинский завод «ПВАМ-0,35 48В ТУ 16.К19-12-2003» (карточка товара vseinstrumenti.ru/product/provod-ao-elektrokabel-kolchuginskij-zavod-pvam-0-35-48v-tu-16-k19-12-2003-chernyj-buhta-500-m-100000084940-ch-500-24473652/)",
"new": "snabcable.ru (общая страница ПВАМ, сечение 1,5мм2: OD 2,4мм); ссылка на конкретную карточку покупателем не дана; Обозначение изделия АО «Электрокабель» Кольчугинский завод «ПВАМ-0,35 48В ТУ 16.К19-12-2003» (карточка товара vseinstrumenti.ru/product/provod-ao-elektrokabel-kolchuginskij-zavod-pvam-0-35-48v-tu-16-k19-12-2003-chernyj-buhta-500-m-100000084940-ch-500-24473652/); 19.10.2026: Дата цены, Поставщик, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/car/pvam-pvame-pvamde/pvam/",
"source": "журнал"
},
{
"mark": "ПВАМ 2,5",
"field": "Источник данных",
"old": "snabcable.ru; цена последней закупки указана 0,0 — вероятно ошибка данных (у других сечений ПВАМ цена 9–16) — решено агентом — проверить. Номинальное напряжение 48В принято по аналогии с другими сечениями серии, для 2,5мм2 отдельно страницей не подтверждено — решено агентом — проверить. Цвет не указан покупателем; Обозначение изделия АО «Электрокабель» Кольчугинский завод «ПВАМ-0,35 48В ТУ 16.К19-12-2003» (карточка товара vseinstrumenti.ru/product/provod-ao-elektrokabel-kolchuginskij-zavod-pvam-0-35-48v-tu-16-k19-12-2003-chernyj-buhta-500-m-100000084940-ch-500-24473652/); бывал давальческим",
"new": "snabcable.ru; цена последней закупки указана 0,0 — вероятно ошибка данных (у других сечений ПВАМ цена 9–16) — решено агентом — проверить. Номинальное напряжение 48В принято по аналогии с другими сечениями серии, для 2,5мм2 отдельно страницей не подтверждено — решено агентом — проверить. Цвет не указан покупателем; Обозначение изделия АО «Электрокабель» Кольчугинский завод «ПВАМ-0,35 48В ТУ 16.К19-12-2003» (карточка товара vseinstrumenti.ru/product/provod-ao-elektrokabel-kolchuginskij-zavod-pvam-0-35-48v-tu-16-k19-12-2003-chernyj-buhta-500-m-100000084940-ch-500-24473652/); бывал давальческим; 19.10.2026: Дата цены, Поставщик, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/car/pvam-pvame-pvamde/pvam/",
"source": "журнал"
},
{
"mark": "ПВАМ 4",
"field": "Источник данных",
"old": "Обозначение изделия АО «Электрокабель» Кольчугинский завод «ПВАМ-0,35 48В ТУ 16.К19-12-2003» (карточка товара vseinstrumenti.ru/product/provod-ao-elektrokabel-kolchuginskij-zavod-pvam-0-35-48v-tu-16-k19-12-2003-chernyj-buhta-500-m-100000084940-ch-500-24473652/)",
"new": "Обозначение изделия АО «Электрокабель» Кольчугинский завод «ПВАМ-0,35 48В ТУ 16.К19-12-2003» (карточка товара vseinstrumenti.ru/product/provod-ao-elektrokabel-kolchuginskij-zavod-pvam-0-35-48v-tu-16-k19-12-2003-chernyj-buhta-500-m-100000084940-ch-500-24473652/); 19.10.2026: Дата цены, Поставщик, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/car/pvam-pvame-pvamde/pvam/",
"source": "журнал"
},
{
"mark": "ПВАМ 6",
"field": "Источник данных",
"old": "Обозначение изделия АО «Электрокабель» Кольчугинский завод «ПВАМ-0,35 48В ТУ 16.К19-12-2003» (карточка товара vseinstrumenti.ru/product/provod-ao-elektrokabel-kolchuginskij-zavod-pvam-0-35-48v-tu-16-k19-12-2003-chernyj-buhta-500-m-100000084940-ch-500-24473652/)",
"new": "Обозначение изделия АО «Электрокабель» Кольчугинский завод «ПВАМ-0,35 48В ТУ 16.К19-12-2003» (карточка товара vseinstrumenti.ru/product/provod-ao-elektrokabel-kolchuginskij-zavod-pvam-0-35-48v-tu-16-k19-12-2003-chernyj-buhta-500-m-100000084940-ch-500-24473652/); 19.10.2026: Дата цены, Поставщик, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/car/pvam-pvame-pvamde/pvam/",
"source": "журнал"
},
{
"mark": "ПГВА 0,50",
"field": "Источник данных",
"old": "snabcable.ru; жила — медь многопроволочная 3-го класса гибкости, страндинг страницей не указан",
"new": "snabcable.ru; жила — медь многопроволочная 3-го класса гибкости, страндинг страницей не указан; 19.10.2026: Дата цены, Поставщик, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/car/pvam-pvame-pvamde/pgva/",
"source": "журнал"
},
{
"mark": "ПГВА 0,75",
"field": "Источник данных",
"old": "snabcable.ru (https://snabcable.ru/car/pvam-pvame-pvamde/pgva/): Ø 2,6 мм, 48 В (испыт. 1000 В), −40…+70 °C, ТУ 16.К17-021-94, цвета по сечению. AWG — по таблице «Сечения AWG ↔ мм²» ERP",
"new": "snabcable.ru (https://snabcable.ru/car/pvam-pvame-pvamde/pgva/): Ø 2,6 мм, 48 В (испыт. 1000 В), −40…+70 °C, ТУ 16.К17-021-94, цвета по сечению. AWG — по таблице «Сечения AWG ↔ мм²» ERP; 19.10.2026: Дата цены, Поставщик, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/car/pvam-pvame-pvamde/pgva/",
"source": "журнал"
},
{
"mark": "ПГВА 1,0",
"field": "Источник данных",
"old": "snabcable.ru (https://snabcable.ru/car/pvam-pvame-pvamde/pgva/): Ø 2,7 мм, 48 В (испыт. 1000 В), −40…+70 °C, ТУ 16.К17-021-94, цвета по сечению. AWG — по таблице «Сечения AWG ↔ мм²» ERP",
"new": "snabcable.ru (https://snabcable.ru/car/pvam-pvame-pvamde/pgva/): Ø 2,7 мм, 48 В (испыт. 1000 В), −40…+70 °C, ТУ 16.К17-021-94, цвета по сечению. AWG — по таблице «Сечения AWG ↔ мм²» ERP; 19.10.2026: Дата цены, Поставщик, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/car/pvam-pvame-pvamde/pgva/",
"source": "журнал"
},
{
"mark": "ПГВА 1,5",
"field": "Источник данных",
"old": "snabcable.ru (https://snabcable.ru/car/pvam-pvame-pvamde/pgva/): Ø 3,0 мм, 48 В (испыт. 1000 В), −40…+70 °C, ТУ 16.К17-021-94, цвета по сечению. AWG — по таблице «Сечения AWG ↔ мм²» ERP",
"new": "snabcable.ru (https://snabcable.ru/car/pvam-pvame-pvamde/pgva/): Ø 3,0 мм, 48 В (испыт. 1000 В), −40…+70 °C, ТУ 16.К17-021-94, цвета по сечению. AWG — по таблице «Сечения AWG ↔ мм²» ERP; 19.10.2026: Дата цены, Поставщик, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/car/pvam-pvame-pvamde/pgva/",
"source": "журнал"
},
{
"mark": "ПГВА 2,5",
"field": "Источник данных",
"old": "snabcable.ru (https://snabcable.ru/car/pvam-pvame-pvamde/pgva/): Ø 3,8 мм, 48 В (испыт. 1000 В), −40…+70 °C, ТУ 16.К17-021-94, цвета по сечению. AWG — по таблице «Сечения AWG ↔ мм²» ERP",
"new": "snabcable.ru (https://snabcable.ru/car/pvam-pvame-pvamde/pgva/): Ø 3,8 мм, 48 В (испыт. 1000 В), −40…+70 °C, ТУ 16.К17-021-94, цвета по сечению. AWG — по таблице «Сечения AWG ↔ мм²» ERP; 19.10.2026: Дата цены, Поставщик, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/car/pvam-pvame-pvamde/pgva/",
"source": "журнал"
},
{
"mark": "ПГВА 4,0",
"field": "Источник данных",
"old": "snabcable.ru (https://snabcable.ru/car/pvam-pvame-pvamde/pgva/): Ø 4,5 мм, 48 В (испыт. 1000 В), −40…+70 °C, ТУ 16.К17-021-94, цвета по сечению. AWG — по таблице «Сечения AWG ↔ мм²» ERP",
"new": "snabcable.ru (https://snabcable.ru/car/pvam-pvame-pvamde/pgva/): Ø 4,5 мм, 48 В (испыт. 1000 В), −40…+70 °C, ТУ 16.К17-021-94, цвета по сечению. AWG — по таблице «Сечения AWG ↔ мм²» ERP; 19.10.2026: Дата цены, Поставщик, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/car/pvam-pvame-pvamde/pgva/",
"source": "журнал"
},
{
"mark": "ПГВА 6,0",
"field": "Источник данных",
"old": "snabcable.ru (https://snabcable.ru/car/pvam-pvame-pvamde/pgva/): Ø 5,3 мм, 48 В (испыт. 1000 В), −40…+70 °C, ТУ 16.К17-021-94, цвета по сечению. AWG — по таблице «Сечения AWG ↔ мм²» ERP",
"new": "snabcable.ru (https://snabcable.ru/car/pvam-pvame-pvamde/pgva/): Ø 5,3 мм, 48 В (испыт. 1000 В), −40…+70 °C, ТУ 16.К17-021-94, цвета по сечению. AWG — по таблице «Сечения AWG ↔ мм²» ERP; 19.10.2026: Дата цены, Поставщик, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/car/pvam-pvame-pvamde/pgva/",
"source": "журнал"
},
{
"mark": "ПуГВ 0,50",
"field": "Источник данных",
"old": "Иркутсккабель (УНКОМТЕХ), описание марки ПуГВ, ТУ 16-705.501-2010 — uncomtech.ru/marks/download/VE11; ООО «Промтех», «Провода для электрических установок на 450/750 В, ТУ 27.32.13-012-32408375-2023, ГОСТ 31947-2012», табл. 9 (ПуГВ) — indtechnology.ru/documents/pugv.pdf; ГОСТ 31947-2012 (ред. изм. 1) «Провода и кабели для электрических установок на номинальное напряжение до 450/750 В», п. 4.2, 5.2.1.4 — meganorm.ru/mega_doc/norm/gost_gosudarstvennyj-standart/0/gost_31947-2012_mezhgosudarstvennyy_standart_provoda_i.html",
"new": "Иркутсккабель (УНКОМТЕХ), описание марки ПуГВ, ТУ 16-705.501-2010 — uncomtech.ru/marks/download/VE11; ООО «Промтех», «Провода для электрических установок на 450/750 В, ТУ 27.32.13-012-32408375-2023, ГОСТ 31947-2012», табл. 9 (ПуГВ) — indtechnology.ru/documents/pugv.pdf; ГОСТ 31947-2012 (ред. изм. 1) «Провода и кабели для электрических установок на номинальное напряжение до 450/750 В», п. 4.2, 5.2.1.4 — meganorm.ru/mega_doc/norm/gost_gosudarstvennyj-standart/0/gost_31947-2012_mezhgosudarstvennyy_standart_provoda_i.html; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/",
"source": "журнал"
},
{
"mark": "ПуГВ 0,75",
"field": "Источник данных",
"old": "Иркутсккабель (УНКОМТЕХ), описание марки ПуГВ, ТУ 16-705.501-2010 — uncomtech.ru/marks/download/VE11; ООО «Промтех», «Провода для электрических установок на 450/750 В, ТУ 27.32.13-012-32408375-2023, ГОСТ 31947-2012», табл. 9 (ПуГВ) — indtechnology.ru/documents/pugv.pdf; ГОСТ 31947-2012 (ред. изм. 1) «Провода и кабели для электрических установок на номинальное напряжение до 450/750 В», п. 4.2, 5.2.1.4 — meganorm.ru/mega_doc/norm/gost_gosudarstvennyj-standart/0/gost_31947-2012_mezhgosudarstvennyy_standart_provoda_i.html",
"new": "Иркутсккабель (УНКОМТЕХ), описание марки ПуГВ, ТУ 16-705.501-2010 — uncomtech.ru/marks/download/VE11; ООО «Промтех», «Провода для электрических установок на 450/750 В, ТУ 27.32.13-012-32408375-2023, ГОСТ 31947-2012», табл. 9 (ПуГВ) — indtechnology.ru/documents/pugv.pdf; ГОСТ 31947-2012 (ред. изм. 1) «Провода и кабели для электрических установок на номинальное напряжение до 450/750 В», п. 4.2, 5.2.1.4 — meganorm.ru/mega_doc/norm/gost_gosudarstvennyj-standart/0/gost_31947-2012_mezhgosudarstvennyy_standart_provoda_i.html; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/",
"source": "журнал"
},
{
"mark": "ПуГВ 1,0",
"field": "Источник данных",
"old": "electro-mpo.ru (карточка жёлто-зелёный) + ruscable.ru (общий профиль ПуГВ); жила 5-го класса гибкости, страндинг страницей не указан — решено агентом — проверить. Цена последней закупки у цветов расходится (18,0–18,5) — взято модальное 18,0 — решено агентом — проверить. Объединены 3 цветовые позиции (белый,жёлто-зелёный,синий); Иркутсккабель (УНКОМТЕХ), описание марки ПуГВ, ТУ 16-705.501-2010 — uncomtech.ru/marks/download/VE11; ООО «Промтех», «Провода для электрических установок на 450/750 В, ТУ 27.32.13-012-32408375-2023, ГОСТ 31947-2012», табл. 9 (ПуГВ) — indtechnology.ru/documents/pugv.pdf; ГОСТ 31947-2012 (ред. изм. 1) «Провода и кабели для электрических установок на номинальное напряжение до 450/750 В», п. 4.2, 5.2.1.4 — meganorm.ru/mega_doc/norm/gost_gosudarstvennyj-standart/0/gost_31947-2012_mezhgosudarstvennyy_standart_provoda_i.html",
"new": "electro-mpo.ru (карточка жёлто-зелёный) + ruscable.ru (общий профиль ПуГВ); жила 5-го класса гибкости, страндинг страницей не указан — решено агентом — проверить. Цена последней закупки у цветов расходится (18,0–18,5) — взято модальное 18,0 — решено агентом — проверить. Объединены 3 цветовые позиции (белый,жёлто-зелёный,синий); Иркутсккабель (УНКОМТЕХ), описание марки ПуГВ, ТУ 16-705.501-2010 — uncomtech.ru/marks/download/VE11; ООО «Промтех», «Провода для электрических установок на 450/750 В, ТУ 27.32.13-012-32408375-2023, ГОСТ 31947-2012», табл. 9 (ПуГВ) — indtechnology.ru/documents/pugv.pdf; ГОСТ 31947-2012 (ред. изм. 1) «Провода и кабели для электрических установок на номинальное напряжение до 450/750 В», п. 4.2, 5.2.1.4 — meganorm.ru/mega_doc/norm/gost_gosudarstvennyj-standart/0/gost_31947-2012_mezhgosudarstvennyy_standart_provoda_i.html; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/",
"source": "журнал"
},
{
"mark": "ПуГВ 1,5",
"field": "Источник данных",
"old": "Иркутсккабель (УНКОМТЕХ), описание марки ПуГВ, ТУ 16-705.501-2010 — uncomtech.ru/marks/download/VE11; ООО «Промтех», «Провода для электрических установок на 450/750 В, ТУ 27.32.13-012-32408375-2023, ГОСТ 31947-2012», табл. 9 (ПуГВ) — indtechnology.ru/documents/pugv.pdf; ГОСТ 31947-2012 (ред. изм. 1) «Провода и кабели для электрических установок на номинальное напряжение до 450/750 В», п. 4.2, 5.2.1.4 — meganorm.ru/mega_doc/norm/gost_gosudarstvennyj-standart/0/gost_31947-2012_mezhgosudarstvennyy_standart_provoda_i.html",
"new": "Иркутсккабель (УНКОМТЕХ), описание марки ПуГВ, ТУ 16-705.501-2010 — uncomtech.ru/marks/download/VE11; ООО «Промтех», «Провода для электрических установок на 450/750 В, ТУ 27.32.13-012-32408375-2023, ГОСТ 31947-2012», табл. 9 (ПуГВ) — indtechnology.ru/documents/pugv.pdf; ГОСТ 31947-2012 (ред. изм. 1) «Провода и кабели для электрических установок на номинальное напряжение до 450/750 В», п. 4.2, 5.2.1.4 — meganorm.ru/mega_doc/norm/gost_gosudarstvennyj-standart/0/gost_31947-2012_mezhgosudarstvennyy_standart_provoda_i.html; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/",
"source": "журнал"
},
{
"mark": "ПуГВ 10",
"field": "Источник данных",
"old": "Иркутсккабель (УНКОМТЕХ), описание марки ПуГВ, ТУ 16-705.501-2010 — uncomtech.ru/marks/download/VE11; ООО «Промтех», «Провода для электрических установок на 450/750 В, ТУ 27.32.13-012-32408375-2023, ГОСТ 31947-2012», табл. 9 (ПуГВ) — indtechnology.ru/documents/pugv.pdf; ГОСТ 31947-2012 (ред. изм. 1) «Провода и кабели для электрических установок на номинальное напряжение до 450/750 В», п. 4.2, 5.2.1.4 — meganorm.ru/mega_doc/norm/gost_gosudarstvennyj-standart/0/gost_31947-2012_mezhgosudarstvennyy_standart_provoda_i.html",
"new": "Иркутсккабель (УНКОМТЕХ), описание марки ПуГВ, ТУ 16-705.501-2010 — uncomtech.ru/marks/download/VE11; ООО «Промтех», «Провода для электрических установок на 450/750 В, ТУ 27.32.13-012-32408375-2023, ГОСТ 31947-2012», табл. 9 (ПуГВ) — indtechnology.ru/documents/pugv.pdf; ГОСТ 31947-2012 (ред. изм. 1) «Провода и кабели для электрических установок на номинальное напряжение до 450/750 В», п. 4.2, 5.2.1.4 — meganorm.ru/mega_doc/norm/gost_gosudarstvennyj-standart/0/gost_31947-2012_mezhgosudarstvennyy_standart_provoda_i.html; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/",
"source": "журнал"
},
{
"mark": "ПуГВ 16",
"field": "Источник данных",
"old": "Иркутсккабель (УНКОМТЕХ), описание марки ПуГВ, ТУ 16-705.501-2010 — uncomtech.ru/marks/download/VE11; ООО «Промтех», «Провода для электрических установок на 450/750 В, ТУ 27.32.13-012-32408375-2023, ГОСТ 31947-2012», табл. 9 (ПуГВ) — indtechnology.ru/documents/pugv.pdf; ГОСТ 31947-2012 (ред. изм. 1) «Провода и кабели для электрических установок на номинальное напряжение до 450/750 В», п. 4.2, 5.2.1.4 — meganorm.ru/mega_doc/norm/gost_gosudarstvennyj-standart/0/gost_31947-2012_mezhgosudarstvennyy_standart_provoda_i.html",
"new": "Иркутсккабель (УНКОМТЕХ), описание марки ПуГВ, ТУ 16-705.501-2010 — uncomtech.ru/marks/download/VE11; ООО «Промтех», «Провода для электрических установок на 450/750 В, ТУ 27.32.13-012-32408375-2023, ГОСТ 31947-2012», табл. 9 (ПуГВ) — indtechnology.ru/documents/pugv.pdf; ГОСТ 31947-2012 (ред. изм. 1) «Провода и кабели для электрических установок на номинальное напряжение до 450/750 В», п. 4.2, 5.2.1.4 — meganorm.ru/mega_doc/norm/gost_gosudarstvennyj-standart/0/gost_31947-2012_mezhgosudarstvennyy_standart_provoda_i.html; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/",
"source": "журнал"
},
{
"mark": "ПуГВ 2,5",
"field": "Источник данных",
"old": "Иркутсккабель (УНКОМТЕХ), описание марки ПуГВ, ТУ 16-705.501-2010 — uncomtech.ru/marks/download/VE11; ООО «Промтех», «Провода для электрических установок на 450/750 В, ТУ 27.32.13-012-32408375-2023, ГОСТ 31947-2012», табл. 9 (ПуГВ) — indtechnology.ru/documents/pugv.pdf; ГОСТ 31947-2012 (ред. изм. 1) «Провода и кабели для электрических установок на номинальное напряжение до 450/750 В», п. 4.2, 5.2.1.4 — meganorm.ru/mega_doc/norm/gost_gosudarstvennyj-standart/0/gost_31947-2012_mezhgosudarstvennyy_standart_provoda_i.html",
"new": "Иркутсккабель (УНКОМТЕХ), описание марки ПуГВ, ТУ 16-705.501-2010 — uncomtech.ru/marks/download/VE11; ООО «Промтех», «Провода для электрических установок на 450/750 В, ТУ 27.32.13-012-32408375-2023, ГОСТ 31947-2012», табл. 9 (ПуГВ) — indtechnology.ru/documents/pugv.pdf; ГОСТ 31947-2012 (ред. изм. 1) «Провода и кабели для электрических установок на номинальное напряжение до 450/750 В», п. 4.2, 5.2.1.4 — meganorm.ru/mega_doc/norm/gost_gosudarstvennyj-standart/0/gost_31947-2012_mezhgosudarstvennyy_standart_provoda_i.html; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/",
"source": "журнал"
},
{
"mark": "ПуГВ 4",
"field": "Источник данных",
"old": "Иркутсккабель (УНКОМТЕХ), описание марки ПуГВ, ТУ 16-705.501-2010 — uncomtech.ru/marks/download/VE11; ООО «Промтех», «Провода для электрических установок на 450/750 В, ТУ 27.32.13-012-32408375-2023, ГОСТ 31947-2012», табл. 9 (ПуГВ) — indtechnology.ru/documents/pugv.pdf; ГОСТ 31947-2012 (ред. изм. 1) «Провода и кабели для электрических установок на номинальное напряжение до 450/750 В», п. 4.2, 5.2.1.4 — meganorm.ru/mega_doc/norm/gost_gosudarstvennyj-standart/0/gost_31947-2012_mezhgosudarstvennyy_standart_provoda_i.html",
"new": "Иркутсккабель (УНКОМТЕХ), описание марки ПуГВ, ТУ 16-705.501-2010 — uncomtech.ru/marks/download/VE11; ООО «Промтех», «Провода для электрических установок на 450/750 В, ТУ 27.32.13-012-32408375-2023, ГОСТ 31947-2012», табл. 9 (ПуГВ) — indtechnology.ru/documents/pugv.pdf; ГОСТ 31947-2012 (ред. изм. 1) «Провода и кабели для электрических установок на номинальное напряжение до 450/750 В», п. 4.2, 5.2.1.4 — meganorm.ru/mega_doc/norm/gost_gosudarstvennyj-standart/0/gost_31947-2012_mezhgosudarstvennyy_standart_provoda_i.html; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/",
"source": "журнал"
},
{
"mark": "ПуГВ 6",
"field": "Источник данных",
"old": "Иркутсккабель (УНКОМТЕХ), описание марки ПуГВ, ТУ 16-705.501-2010 — uncomtech.ru/marks/download/VE11; ООО «Промтех», «Провода для электрических установок на 450/750 В, ТУ 27.32.13-012-32408375-2023, ГОСТ 31947-2012», табл. 9 (ПуГВ) — indtechnology.ru/documents/pugv.pdf; ГОСТ 31947-2012 (ред. изм. 1) «Провода и кабели для электрических установок на номинальное напряжение до 450/750 В», п. 4.2, 5.2.1.4 — meganorm.ru/mega_doc/norm/gost_gosudarstvennyj-standart/0/gost_31947-2012_mezhgosudarstvennyy_standart_provoda_i.html",
"new": "Иркутсккабель (УНКОМТЕХ), описание марки ПуГВ, ТУ 16-705.501-2010 — uncomtech.ru/marks/download/VE11; ООО «Промтех», «Провода для электрических установок на 450/750 В, ТУ 27.32.13-012-32408375-2023, ГОСТ 31947-2012», табл. 9 (ПуГВ) — indtechnology.ru/documents/pugv.pdf; ГОСТ 31947-2012 (ред. изм. 1) «Провода и кабели для электрических установок на номинальное напряжение до 450/750 В», п. 4.2, 5.2.1.4 — meganorm.ru/mega_doc/norm/gost_gosudarstvennyj-standart/0/gost_31947-2012_mezhgosudarstvennyy_standart_provoda_i.html; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/pv-3-pugv/",
"source": "журнал"
},
{
"mark": "ПуГВнг(А)-LS 1х0,5",
"field": "Источник данных",
"old": "electro-mpo.ru карточка Брэкс (https://electro-mpo.ru/catalog/provod_i_kabel/p05_pugv_pugvng_a_ls_provod_mednyy_ustanovochnyy_g/p0572-provod-pugvng-a-ls-1kh0-50-kv-mm-chernyy-gos/): жила 5 класса, Ø 2,1 мм, до 450 В (400 Гц), нг(А)-LS. Температура и номер ГОСТ страницей не даны — пусто. AWG — по таблице ERP",
"new": "electro-mpo.ru карточка Брэкс (https://electro-mpo.ru/catalog/provod_i_kabel/p05_pugv_pugvng_a_ls_provod_mednyy_ustanovochnyy_g/p0572-provod-pugvng-a-ls-1kh0-50-kv-mm-chernyy-gos/): жила 5 класса, Ø 2,1 мм, до 450 В (400 Гц), нг(А)-LS. Температура и номер ГОСТ страницей не даны — пусто. AWG — по таблице ERP; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Стандарт, Темп. до, °C, Темп. от, °C, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/",
"source": "журнал"
},
{
"mark": "ПуГВнг(А)-LS 1х0,75",
"field": "Источник данных",
"old": "electro-mpo.ru карточка Брэкс (https://electro-mpo.ru/catalog/provod_i_kabel/p05_pugv_pugvng_a_ls_provod_mednyy_ustanovochnyy_g/p0575-provod-pugvng-a-ls-1kh0-75-kv-mm-belyy-gost-/): жила 5 класса, Ø 2,4 мм, до 450 В (400 Гц), нг(А)-LS. Температура и номер ГОСТ страницей не даны — пусто. AWG — по таблице ERP",
"new": "electro-mpo.ru карточка Брэкс (https://electro-mpo.ru/catalog/provod_i_kabel/p05_pugv_pugvng_a_ls_provod_mednyy_ustanovochnyy_g/p0575-provod-pugvng-a-ls-1kh0-75-kv-mm-belyy-gost-/): жила 5 класса, Ø 2,4 мм, до 450 В (400 Гц), нг(А)-LS. Температура и номер ГОСТ страницей не даны — пусто. AWG — по таблице ERP; 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Стандарт, Темп. до, °C, Темп. от, °C, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/",
"source": "журнал"
},
{
"mark": "ПуГВнг(А)-LS 1х1,0",
"field": "Источник данных",
"old": "electro-mpo.ru карточка (Breks, Брянск): жила медь многопроволочная 5 класса гибкости, изоляция ПВХ пониженной пожароопасности низкого дымогазовыделения, OD 2,5мм, до 450В (400Гц), нг(А)-LS (не распространяет горение, низкое дымовыделение), сертификат по низкодымности. Температурный диапазон страницей не дан. Объединены 2 цветовые позиции (красный,чёрный)",
"new": "electro-mpo.ru карточка (Breks, Брянск): жила медь многопроволочная 5 класса гибкости, изоляция ПВХ пониженной пожароопасности низкого дымогазовыделения, OD 2,5мм, до 450В (400Гц), нг(А)-LS (не распространяет горение, низкое дымовыделение), сертификат по низкодымности. Температурный диапазон страницей не дан. Объединены 2 цветовые позиции (красный,чёрный); 19.10.2026: Дата цены, Поставщик, Производители, Ссылка, Стандарт, Темп. до, °C, Темп. от, °C, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/",
"source": "журнал"
},
{
"mark": "ПуГВнг(А)-LS 1х1,5",
"field": "Источник данных",
"old": "electro-mpo.ru карточка Брэкс (https://electro-mpo.ru/catalog/provod_i_kabel/p05_pugv_pugvng_a_ls_provod_mednyy_ustanovochnyy_g/p0588-provod-pugvng-a-ls-1kh1-5-kv-mm-goluboy-gost/): жила 5 класса, Ø 3,0 мм, до 450 В (400 Гц), нг(А)-LS. Температура и номер ГОСТ страницей не даны — пусто. AWG — по таблице ERP",
"new": "electro-mpo.ru карточка Брэкс (https://electro-mpo.ru/catalog/provod_i_kabel/p05_pugv_pugvng_a_ls_provod_mednyy_ustanovochnyy_g/p0588-provod-pugvng-a-ls-1kh1-5-kv-mm-goluboy-gost/): жила 5 класса, Ø 3,0 мм, до 450 В (400 Гц), нг(А)-LS. Температура и номер ГОСТ страницей не даны — пусто. AWG — по таблице ERP; 19.10.2026: Дата цены, Наружный диаметр, мм, Поставщик, Производители, Ссылка, Стандарт, Темп. до, °C, Темп. от, °C, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/",
"source": "журнал"
},
{
"mark": "ПуГВнг(А)-LS 1х2,5",
"field": "Источник данных",
"old": "electro-mpo.ru карточка Брэкс (https://electro-mpo.ru/catalog/provod_i_kabel/p05_pugv_pugvng_a_ls_provod_mednyy_ustanovochnyy_g/p0594-provod-pugvng-a-ls-1kh2-5-kv-mm-zhelto-zelen/): жила 5 класса, Ø 3,7 мм, до 450 В (400 Гц), нг(А)-LS. Температура и номер ГОСТ страницей не даны — пусто. AWG — по таблице ERP",
"new": "electro-mpo.ru карточка Брэкс (https://electro-mpo.ru/catalog/provod_i_kabel/p05_pugv_pugvng_a_ls_provod_mednyy_ustanovochnyy_g/p0594-provod-pugvng-a-ls-1kh2-5-kv-mm-zhelto-zelen/): жила 5 класса, Ø 3,7 мм, до 450 В (400 Гц), нг(А)-LS. Температура и номер ГОСТ страницей не даны — пусто. AWG — по таблице ERP; 19.10.2026: Дата цены, Наружный диаметр, мм, Поставщик, Производители, Ссылка, Стандарт, Темп. до, °C, Темп. от, °C, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/",
"source": "журнал"
},
{
"mark": "ПуГВнг(А)-LS 1х4,0",
"field": "Источник данных",
"old": "electro-mpo.ru карточка Брэкс (https://electro-mpo.ru/catalog/provod_i_kabel/p05_pugv_pugvng_a_ls_provod_mednyy_ustanovochnyy_g/p0597-provod-pugvng-a-ls-4-0-kv-mm-chernyy-gost-ma/): жила 5 класса, Ø 4,2 мм, до 450 В (400 Гц), нг(А)-LS. Температура и номер ГОСТ страницей не даны — пусто. AWG — по таблице ERP",
"new": "electro-mpo.ru карточка Брэкс (https://electro-mpo.ru/catalog/provod_i_kabel/p05_pugv_pugvng_a_ls_provod_mednyy_ustanovochnyy_g/p0597-provod-pugvng-a-ls-4-0-kv-mm-chernyy-gost-ma/): жила 5 класса, Ø 4,2 мм, до 450 В (400 Гц), нг(А)-LS. Температура и номер ГОСТ страницей не даны — пусто. AWG — по таблице ERP; 19.10.2026: Дата цены, Наружный диаметр, мм, Поставщик, Производители, Ссылка, Стандарт, Темп. до, °C, Темп. от, °C, Цвета, Цена поставщика, ₽/м — https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/",
"source": "журнал"
},
{
"mark": "НВМ 0,12",
"field": "Источник данных",
"old": "bystrokabel.ru карточка НВМ 0,12 4 600 (https://bystrokabel.ru/item/nvm/nvm-0-12-4-600): жила 4 класса, ПВХ-пластикат 0,35 мм, Ø 1,3 мм, масса 3,0 кг/км, −50…+105 °C, 600 В AC (до 10 кГц)/840 В DC, ГОСТ 17515-72. AWG — по таблице «Сечения AWG ↔ мм²» ERP",
"new": "bystrokabel.ru карточка НВМ 0,12 4 600 (https://bystrokabel.ru/item/nvm/nvm-0-12-4-600): жила 4 класса, ПВХ-пластикат 0,35 мм, Ø 1,3 мм, масса 3,0 кг/км, −50…+105 °C, 600 В AC (до 10 кГц)/840 В DC, ГОСТ 17515-72. AWG — по таблице «Сечения AWG ↔ мм²» ERP; 19.10.2026: Дата цены, Жила: проволок × диаметр, мм, Наружный диаметр, мм, Ссылка, Цвета, Цена поставщика, ₽/м — https://paritet.su/catalog/nvm/nvm_0_12_4_600_belyy/",
"source": "журнал"
},
{
"mark": "НВМ 0,20",
"field": "Источник данных",
"old": "bystrokabel.ru карточка НВМ 0,20 4 600 (https://bystrokabel.ru/item/nvm/nvm-0-2-4-600): жила 4 класса, ПВХ-пластикат 0,35 мм, Ø 1,5 мм, масса 4,0 кг/км, −50…+105 °C, 600 В AC (до 10 кГц)/840 В DC, ГОСТ 17515-72. AWG — по таблице «Сечения AWG ↔ мм²» ERP",
"new": "bystrokabel.ru карточка НВМ 0,20 4 600 (https://bystrokabel.ru/item/nvm/nvm-0-2-4-600): жила 4 класса, ПВХ-пластикат 0,35 мм, Ø 1,5 мм, масса 4,0 кг/км, −50…+105 °C, 600 В AC (до 10 кГц)/840 В DC, ГОСТ 17515-72. AWG — по таблице «Сечения AWG ↔ мм²» ERP; 19.10.2026: Дата цены, Жила: проволок × диаметр, мм, Наружный диаметр, мм, Ссылка, Цвета, Цена поставщика, ₽/м — https://paritet.su/catalog/nvm/nvm_0_20_4_600_belyy/",
"source": "журнал"
},
{
"mark": "НВМ 0,35",
"field": "Источник данных",
"old": "bystrokabel.ru карточка НВМ-0,35 4 600: многопроволочная медная жила 4 класса, ПВХ-пластикат 0,35мм, OD 1,6мм, масса 6,0кг/км=6,0г/м, -50…+105°C, 600В AC(до 10кГц)/840В DC, испытательное 2кВ/50Гц, ГОСТ 17515-72, климат УХЛ (в отличие от НВ — климат В). Ссылка покупателя не дана (нет ни в одной из 3 строк). Объединены 3 цветовые позиции (зелёный,красный,чёрный)",
"new": "bystrokabel.ru карточка НВМ-0,35 4 600: многопроволочная медная жила 4 класса, ПВХ-пластикат 0,35мм, OD 1,6мм, масса 6,0кг/км=6,0г/м, -50…+105°C, 600В AC(до 10кГц)/840В DC, испытательное 2кВ/50Гц, ГОСТ 17515-72, климат УХЛ (в отличие от НВ — климат В). Ссылка покупателя не дана (нет ни в одной из 3 строк). Объединены 3 цветовые позиции (зелёный,красный,чёрный); 19.10.2026: Дата цены, Жила: проволок × диаметр, мм, Наружный диаметр, мм, Ссылка, Цвета, Цена поставщика, ₽/м — https://paritet.su/catalog/nvm/nvm_0_35_4_600_belyy/",
"source": "журнал"
},
{
"mark": "НВМ 0,50",
"field": "Источник данных",
"old": "bystrokabel.ru карточка НВМ 0,50 4 600 (https://bystrokabel.ru/item/nvm/nvm-0-5-4-600): жила 4 класса, ПВХ-пластикат 0,35 мм, Ø 1,8 мм, масса 7,0 кг/км, −50…+105 °C, 600 В AC (до 10 кГц)/840 В DC, ГОСТ 17515-72. AWG — по таблице «Сечения AWG ↔ мм²» ERP",
"new": "bystrokabel.ru карточка НВМ 0,50 4 600 (https://bystrokabel.ru/item/nvm/nvm-0-5-4-600): жила 4 класса, ПВХ-пластикат 0,35 мм, Ø 1,8 мм, масса 7,0 кг/км, −50…+105 °C, 600 В AC (до 10 кГц)/840 В DC, ГОСТ 17515-72. AWG — по таблице «Сечения AWG ↔ мм²» ERP; 19.10.2026: Дата цены, Жила: проволок × диаметр, мм, Наружный диаметр, мм, Ссылка, Цвета, Цена поставщика, ₽/м — https://paritet.su/catalog/nvm/nvm_0_50_4_600_belyy/",
"source": "журнал"
},
{
"mark": "НВМ 0,75",
"field": "Источник данных",
"old": "bystrokabel.ru карточка НВМ 0,75 3 600 (https://bystrokabel.ru/item/nvm/nvm-0-75-3-600): жила 3 класса, ПВХ-пластикат 0,35 мм, Ø 2,1 мм, масса 10,0 кг/км, −50…+105 °C, 600 В AC (до 10 кГц)/840 В DC, ГОСТ 17515-72. AWG — по таблице «Сечения AWG ↔ мм²» ERP",
"new": "bystrokabel.ru карточка НВМ 0,75 3 600 (https://bystrokabel.ru/item/nvm/nvm-0-75-3-600): жила 3 класса, ПВХ-пластикат 0,35 мм, Ø 2,1 мм, масса 10,0 кг/км, −50…+105 °C, 600 В AC (до 10 кГц)/840 В DC, ГОСТ 17515-72. AWG — по таблице «Сечения AWG ↔ мм²» ERP; 19.10.2026: Артикул поставщика, Дата цены, Жила: проволок × диаметр, мм, Наружный диаметр, мм, Ссылка, Цвета, Цена поставщика, ₽/м — https://paritet.su/catalog/nvm/nvm_0_75_3_600_chernyy/",
"source": "журнал"
}
],
"creates": [
{
"mark": "МГТФ 0,50",
"series": "МГТФ",
"section": "0,50",
"fields": {
"Тип провода": "монтажный (термостойкий)",
"Стандарт": "ТУ 16-505.185-71",
"Число жил": "1",
"Изоляция": "фторопласт-4 (обмотка плёнкой)",
"Экран": "нет",
"Темп. от, °C": "-60",
"Темп. до, °C": "220",
"Напряжение, В": "250",
"Материал жилы": "медь",
"Материал оболочки": "фторопласт-4",
"Ссылка на datasheet": "https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"Марка": "МГТФ 0,50",
"Сечение, мм2": "0,50",
"Наружный диаметр, мм": "1,3",
"Цвета": "натуральный, белый, жёлтый, зелёный, коричневый, красный, оранжевый, синий, чёрный",
"Поставщик": "Снабкабель (snabcable.ru)",
"Ссылка": "https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"Цена поставщика, ₽/м": "31,4",
"Дата цены": "19.10.2026",
"Источник данных": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026: сечение, макс. наружный диаметр, цвета, цена (оптовая при 1000 м, с НДС); конструкция и режимы — как у остальных сечений МГТФ той же серии; жила, номинальный наружный диаметр, масса — Чувашкабель, каталог https://chuvashcable.ru/upload/iblock/a4f/goeqhetkh8zux7yjhmtnpfmeeuf1zogt.pdf, стр. 16",
"Производители": "Чувашкабель",
"Жила: проволок × диаметр, мм": "19×0,18",
"Масса, г/м": "6,15"
},
"hookup": {
"insulation": "фторопласт-4 (обмотка плёнкой)",
"voltage": 250,
"cond": "медь",
"t": [
-60,
220
]
}
},
{
"mark": "МГТФ 0,75",
"series": "МГТФ",
"section": "0,75",
"fields": {
"Тип провода": "монтажный (термостойкий)",
"Стандарт": "ТУ 16-505.185-71",
"Число жил": "1",
"Изоляция": "фторопласт-4 (обмотка плёнкой)",
"Экран": "нет",
"Темп. от, °C": "-60",
"Темп. до, °C": "220",
"Напряжение, В": "250",
"Материал жилы": "медь",
"Материал оболочки": "фторопласт-4",
"Ссылка на datasheet": "https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"Марка": "МГТФ 0,75",
"Сечение, мм2": "0,75",
"Наружный диаметр, мм": "1,8",
"Цвета": "натуральный",
"Поставщик": "Снабкабель (snabcable.ru)",
"Ссылка": "https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"Цена поставщика, ₽/м": "41,7",
"Дата цены": "19.10.2026",
"Источник данных": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026: сечение, макс. наружный диаметр, цвета, цена (оптовая при 1000 м, с НДС); конструкция и режимы — как у остальных сечений МГТФ той же серии; жила, номинальный наружный диаметр, масса — Чувашкабель, каталог https://chuvashcable.ru/upload/iblock/a4f/goeqhetkh8zux7yjhmtnpfmeeuf1zogt.pdf, стр. 16",
"Производители": "Чувашкабель",
"Жила: проволок × диаметр, мм": "37×0,16",
"Масса, г/м": "6,69"
},
"hookup": {
"insulation": "фторопласт-4 (обмотка плёнкой)",
"voltage": 250,
"cond": "медь",
"t": [
-60,
220
]
}
},
{
"mark": "МГТФ 1,5",
"series": "МГТФ",
"section": "1,5",
"fields": {
"Тип провода": "монтажный (термостойкий)",
"Стандарт": "ТУ 16-505.185-71",
"Число жил": "1",
"Изоляция": "фторопласт-4 (обмотка плёнкой)",
"Экран": "нет",
"Темп. от, °C": "-60",
"Темп. до, °C": "220",
"Напряжение, В": "250",
"Материал жилы": "медь",
"Материал оболочки": "фторопласт-4",
"Ссылка на datasheet": "https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"Марка": "МГТФ 1,5",
"Сечение, мм2": "1,5",
"Наружный диаметр, мм": "2,2",
"Цвета": "натуральный",
"Поставщик": "Снабкабель (snabcable.ru)",
"Ссылка": "https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"Цена поставщика, ₽/м": "69",
"Дата цены": "19.10.2026",
"Источник данных": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026: сечение, макс. наружный диаметр, цвета, цена (оптовая при 1000 м, с НДС); конструкция и режимы — как у остальных сечений МГТФ той же серии",
"Производители": "Чувашкабель"
},
"hookup": {
"insulation": "фторопласт-4 (обмотка плёнкой)",
"voltage": 250,
"cond": "медь",
"t": [
-60,
220
]
}
},
{
"mark": "МГТФ 2,5",
"series": "МГТФ",
"section": "2,5",
"fields": {
"Тип провода": "монтажный (термостойкий)",
"Стандарт": "ТУ 16-505.185-71",
"Число жил": "1",
"Изоляция": "фторопласт-4 (обмотка плёнкой)",
"Экран": "нет",
"Темп. от, °C": "-60",
"Темп. до, °C": "220",
"Напряжение, В": "250",
"Материал жилы": "медь",
"Материал оболочки": "фторопласт-4",
"Ссылка на datasheet": "https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"Марка": "МГТФ 2,5",
"Сечение, мм2": "2,5",
"Наружный диаметр, мм": "2,6",
"Цвета": "натуральный",
"Поставщик": "Снабкабель (snabcable.ru)",
"Ссылка": "https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/",
"Цена поставщика, ₽/м": "126",
"Дата цены": "19.10.2026",
"Источник данных": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/mgtf/ — 19.10.2026: сечение, макс. наружный диаметр, цвета, цена (оптовая при 1000 м, с НДС); конструкция и режимы — как у остальных сечений МГТФ той же серии",
"Производители": "Чувашкабель"
},
"hookup": {
"insulation": "фторопласт-4 (обмотка плёнкой)",
"voltage": 250,
"cond": "медь",
"t": [
-60,
220
]
}
},
{
"mark": "ПуГВнг(А)-LS 1х6,0",
"series": "ПуГВнг(А)-LS",
"section": "6,0",
"fields": {
"Тип провода": "монтажный (пониженной пожароопасности)",
"Стандарт": "ГОСТ",
"Число жил": "1",
"Изоляция": "ПВХ-композиция пониженной пожароопасности, низкое дымогазовыделение",
"Напряжение, В": "450",
"Материал жилы": "медь",
"Материал оболочки": "ПВХ пластикат пониженной горючести",
"Ссылка на datasheet": "https://electro-mpo.ru/catalog/provod_i_kabel/p05_pugv_pugvng_a_ls_provod_mednyy_ustanovochnyy_g/p0572-provod-pugvng-a-ls-1kh0-50-kv-mm-chernyy-gos/",
"Производители": "Breks (Брянск), Чувашкабель",
"Марка": "ПуГВнг(А)-LS 1х6,0",
"Сечение, мм2": "6,0",
"Наружный диаметр, мм": "4,9",
"Цвета": "белый, синий, жёлтый, жёлто-зелёный, зелёный, коричневый, красный, чёрный",
"Поставщик": "Снабкабель (snabcable.ru)",
"Ссылка": "https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/",
"Цена поставщика, ₽/м": "99,8",
"Дата цены": "19.10.2026",
"Источник данных": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026: сечение, макс. наружный диаметр, цвета, цена (оптовая при 1000 м, с НДС); конструкция и режимы — как у остальных сечений ПуГВнг(А)-LS той же серии",
"Темп. от, °C": "-50",
"Темп. до, °C": "70"
},
"hookup": {
"insulation": "ПВХ-композиция пониженной пожароопасности, низкое дымогазовыделение",
"voltage": 450,
"cond": "медь",
"t": [
-50,
70
]
}
},
{
"mark": "ПуГВнг(А)-LS 1х10",
"series": "ПуГВнг(А)-LS",
"section": "10",
"fields": {
"Тип провода": "монтажный (пониженной пожароопасности)",
"Стандарт": "ГОСТ",
"Число жил": "1",
"Изоляция": "ПВХ-композиция пониженной пожароопасности, низкое дымогазовыделение",
"Напряжение, В": "450",
"Материал жилы": "медь",
"Материал оболочки": "ПВХ пластикат пониженной горючести",
"Ссылка на datasheet": "https://electro-mpo.ru/catalog/provod_i_kabel/p05_pugv_pugvng_a_ls_provod_mednyy_ustanovochnyy_g/p0572-provod-pugvng-a-ls-1kh0-50-kv-mm-chernyy-gos/",
"Производители": "Breks (Брянск), Чувашкабель",
"Марка": "ПуГВнг(А)-LS 1х10",
"Сечение, мм2": "10",
"Наружный диаметр, мм": "6,5",
"Цвета": "белый, синий, жёлто-зелёный, красный, чёрный",
"Поставщик": "Снабкабель (snabcable.ru)",
"Ссылка": "https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/",
"Цена поставщика, ₽/м": "169",
"Дата цены": "19.10.2026",
"Источник данных": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026: сечение, макс. наружный диаметр, цвета, цена (оптовая при 1000 м, с НДС); конструкция и режимы — как у остальных сечений ПуГВнг(А)-LS той же серии",
"Темп. от, °C": "-50",
"Темп. до, °C": "70"
},
"hookup": {
"insulation": "ПВХ-композиция пониженной пожароопасности, низкое дымогазовыделение",
"voltage": 450,
"cond": "медь",
"t": [
-50,
70
]
}
},
{
"mark": "ПуГВнг(А)-LS 1х16",
"series": "ПуГВнг(А)-LS",
"section": "16",
"fields": {
"Тип провода": "монтажный (пониженной пожароопасности)",
"Стандарт": "ГОСТ",
"Число жил": "1",
"Изоляция": "ПВХ-композиция пониженной пожароопасности, низкое дымогазовыделение",
"Напряжение, В": "450",
"Материал жилы": "медь",
"Материал оболочки": "ПВХ пластикат пониженной горючести",
"Ссылка на datasheet": "https://electro-mpo.ru/catalog/provod_i_kabel/p05_pugv_pugvng_a_ls_provod_mednyy_ustanovochnyy_g/p0572-provod-pugvng-a-ls-1kh0-50-kv-mm-chernyy-gos/",
"Производители": "Breks (Брянск), Чувашкабель",
"Марка": "ПуГВнг(А)-LS 1х16",
"Сечение, мм2": "16",
"Наружный диаметр, мм": "7,9",
"Цвета": "белый, синий, жёлто-зелёный, красный, чёрный",
"Поставщик": "Снабкабель (snabcable.ru)",
"Ссылка": "https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/",
"Цена поставщика, ₽/м": "265",
"Дата цены": "19.10.2026",
"Источник данных": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/mounting/mgtf-mgtfe/pugvng-ls/ — 19.10.2026: сечение, макс. наружный диаметр, цвета, цена (оптовая при 1000 м, с НДС); конструкция и режимы — как у остальных сечений ПуГВнг(А)-LS той же серии",
"Темп. от, °C": "-50",
"Темп. до, °C": "70"
},
"hookup": {
"insulation": "ПВХ-композиция пониженной пожароопасности, низкое дымогазовыделение",
"voltage": 450,
"cond": "медь",
"t": [
-50,
70
]
}
},
{
"mark": "ПГВА 10",
"series": "ПГВА",
"section": "10",
"fields": {
"Тип провода": "силовой",
"Стандарт": "ТУ 16.К17-021-94",
"Число жил": "1",
"Изоляция": "ПВХ пластикат",
"Экран": "нет",
"Темп. от, °C": "-40",
"Темп. до, °C": "70",
"Напряжение, В": "48",
"Материал жилы": "медь",
"Материал оболочки": "ПВХ пластикат",
"Ссылка на datasheet": "https://snabcable.ru/car/pvam-pvame-pvamde/pgva/",
"Производители": "Чувашкабель, ЭКЗ",
"Марка": "ПГВА 10",
"Сечение, мм2": "10",
"Наружный диаметр, мм": "6,7",
"Цвета": "красный, чёрный",
"Поставщик": "Снабкабель (snabcable.ru)",
"Ссылка": "https://snabcable.ru/car/pvam-pvame-pvamde/pgva/",
"Цена поставщика, ₽/м": "189",
"Дата цены": "19.10.2026",
"Источник данных": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026: сечение, макс. наружный диаметр, цвета, цена (оптовая при 1000 м, с НДС); конструкция и режимы — как у остальных сечений ПГВА той же серии"
},
"hookup": {
"insulation": "ПВХ пластикат",
"voltage": 48,
"cond": "медь",
"t": [
-40,
70
]
}
},
{
"mark": "ПГВА 16",
"series": "ПГВА",
"section": "16",
"fields": {
"Тип провода": "силовой",
"Стандарт": "ТУ 16.К17-021-94",
"Число жил": "1",
"Изоляция": "ПВХ пластикат",
"Экран": "нет",
"Темп. от, °C": "-40",
"Темп. до, °C": "70",
"Напряжение, В": "48",
"Материал жилы": "медь",
"Материал оболочки": "ПВХ пластикат",
"Ссылка на datasheet": "https://snabcable.ru/car/pvam-pvame-pvamde/pgva/",
"Производители": "Чувашкабель, ЭКЗ",
"Марка": "ПГВА 16",
"Сечение, мм2": "16",
"Наружный диаметр, мм": "8,6",
"Цвета": "красный, чёрный",
"Поставщик": "Снабкабель (snabcable.ru)",
"Ссылка": "https://snabcable.ru/car/pvam-pvame-pvamde/pgva/",
"Цена поставщика, ₽/м": "279",
"Дата цены": "19.10.2026",
"Источник данных": "Снабкабель (snabcable.ru), страница серии https://snabcable.ru/car/pvam-pvame-pvamde/pgva/ — 19.10.2026: сечение, макс. наружный диаметр, цвета, цена (оптовая при 1000 м, с НДС); конструкция и режимы — как у остальных сечений ПГВА той же серии"
},
"hookup": {
"insulation": "ПВХ пластикат",
"voltage": 48,
"cond": "медь",
"t": [
-40,
70
]
}
}
]
}''')

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry или apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
with factory() as uow:
    records = {normalized_cable_key(cable_mark(r)): r for r in uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=100_000)}
    gauges = uow.cable_structure.gauges()

total = {"строк к записи": 0, "ячеек к записи": 0, "уже записано": 0, "записано": 0, "отказ": 0, "новых": 0, "новых уже есть": 0}
by_mark: dict[str, list[dict]] = {}
for cell in PLAN["edits"]:
    by_mark.setdefault(cell["mark"], []).append(cell)
for mark, cells in by_mark.items():
    record = records.get(normalized_cable_key(mark))
    if record is None:
        total["отказ"] += 1
        print(f"   отказ «{mark}»: нет в БД.ПРОВ")
        continue
    todo = [c for c in cells if str(record.fields.get(c["field"], "")) != c["new"]]
    if not todo:
        total["уже записано"] += 1
        continue
    total["строк к записи"] += 1
    total["ячеек к записи"] += len(todo)
    try:
        uow = factory()
        with uow:
            _edit_in(record.id, tuple(FieldEdit(c["field"], c["old"], c["new"]) for c in todo), now, uow)
            _audit("references.tech_record_edited", ACTOR, now, {"record_id": str(record.id), "tab": record.tab.value,
                   "fields": [c["field"] for c in todo], "source": "Снабкабель/Паритет 19.10"}, uow)
            if not dry:
                uow.commit()
        if not dry:
            total["записано"] += 1
    except Exception as error:  # noqa: BLE001 — one row refused, the rest go on
        total["отказ"] += 1
        print(f"   отказ «{mark}»: {error}")

for new in PLAN["creates"]:
    if normalized_cable_key(new["mark"]) in records:
        total["новых уже есть"] += 1
        continue
    section = Decimal(new["section"].replace(",", "."))
    gauge = next((g for g in gauges if Decimal(g.section_mm2) == section), None)
    if gauge is None:
        total["отказ"] += 1
        print(f"   отказ «{new['mark']}»: сечения {new['section']} нет в таблице сечений")
        continue
    total["новых"] += 1
    try:
        uow = factory()
        with uow:
            record = _create_in(TechOperationTab.WIRE, _clean_new_fields(TechOperationTab.WIRE, new["fields"]), uow)
            h = new["hookup"]
            save_hookup_in(SaveHookupCommand(
                record_id=record.id, series=new["series"], gauge_id=gauge.id, insulation=h["insulation"],
                outer_d_mm=new["fields"].get("Наружный диаметр, мм", ""), voltage_v="" if h["voltage"] is None else str(h["voltage"]),
                temp_min_c="" if h["t"][0] is None else str(h["t"][0]), temp_max_c="" if h["t"][1] is None else str(h["t"][1]),
                old=None, actor=ACTOR, now=now, conductor_material=h["cond"]), uow, {"source": "Снабкабель 19.10"})
            if not dry:
                uow.commit()
        print(f"   {'будет' if dry else 'добавлен'} «{new['mark']}»")
    except Exception as error:  # noqa: BLE001
        total["отказ"] += 1
        print(f"   отказ «{new['mark']}»: {error}")
print("ИТОГО", total, "(проверка, ничего не записано)" if dry else "(записано)")

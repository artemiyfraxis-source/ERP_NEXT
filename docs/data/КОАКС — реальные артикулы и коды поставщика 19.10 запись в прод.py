"""Запись в прод 19.10.2026 — БД.КОАКС: настоящие артикулы вместо придуманных, код поставщика, наименование.

Владелец 19.10: «SMA-Male 141» (RG-402) — придуманное имя; на gsm-repiteri.ru тот же товар «Разъём S-115-141 (SMA-male,
напаиваемый, на кабель RG-402)», «Артикул: 3449». Артикул = номер детали из НАЗВАНИЯ товара (S-115-141), число с сайта
(3449) — только код поставщика («Артикул поставщика»: «gsm-repiteri.ru: 3449», как у остальных строк).

Что делает скрипт (журнал ячеек и откат — docs/data/«КОАКС — реальные артикулы и коды поставщика 19.10.csv»):
  1. Переименование «Артикул» — только там, где (а) на старое имя нет ни одной ссылки (склад: партии и списания, заявки
     комплектации, комплекты, техкарты и их ревизии, ЭТК-черновики, шаблоны жгутов — проверяется заново в момент записи),
     (б) нового ключа «артикул + кабель» в базе ещё нет и (в) у нового артикула нет других строк (иначе значения одного
     артикула разошлись бы — правило владельца 19.10 «у одного артикула одни значения»).
  2. «Артикул поставщика» — «gsm-repiteri.ru: N» (amel.ru: артикул) только там, где продавца в ячейке ещё нет; уже
     стоящие там другие продавцы остаются (код gsm ставится первым). Заполненная ячейка с тем же продавцом не трогается.
  3. «Наименование» — название товара с сайта, если ячейка пуста.
Строки ТРЛ (Производитель TRL, артикул ISMA-…) не затрагиваются — их добавляет другой исполнитель.

Ключ строки — натуральный: «артикул + кабель» (cable_key), как у загрузки; ID не используется. Для переименования ключ — то,
что меняется: строка ищется по СТАРОМУ имени и должна быть ровно одна. У каждой ячейки сверяется старое значение
(иначе — отказ строки «поменялась»), правка идёт тем же путём, что «Изменить» в «Базе данных» (`_edit_in`).

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → всё «уже так»
Откат: вернуть ячейкам журнала значение «было» (переставить old/new; строка ищется по новому артикулу).
"""
import json
import os
import re
import sys
from uuid import UUID

from sqlalchemy import create_engine, text

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in
from app.modules.references.domain import ReferenceValidationError, StaleRecordError, TechOperationTab, cable_key
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 19.10.2026", department="System")
REASON = "настоящий артикул из названия товара, код поставщика, наименование (19.10.2026)"
PAYLOAD = json.loads(r'''[
 {
  "article": "SMA-female RG-141",
  "cable": "RG-141",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 1325"
   ],
   [
    "Наименование",
    "",
    "Разъём S-211F (SMA-female, обжимной, на кабель RG-58)"
   ]
  ]
 },
 {
  "article": "N-female RG-142",
  "cable": "RG-142",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 0299"
   ],
   [
    "Наименование",
    "",
    "Разъём N-211F (N-female, обжимной, на кабель RG-58)"
   ]
  ]
 },
 {
  "article": "N-male RG-142",
  "cable": "RG-142",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 0293"
   ],
   [
    "Наименование",
    "",
    "Разъём N-111F (N-male, обжимной, на кабель RG-58)"
   ]
  ]
 },
 {
  "article": "Fakra Z-Female",
  "cable": "RG-174",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 3166"
   ],
   [
    "Наименование",
    "",
    "Разъём Fakra тип Z (Water Blue / 5021) female обжимной для кабеля RG-316"
   ]
  ]
 },
 {
  "article": "MCX-male RG-174",
  "cable": "RG-174",
  "edits": [
   [
    "Артикул",
    "MCX-male RG-174",
    "11-111L"
   ],
   [
    "Наименование",
    "",
    "Разъем 11-111L MCX(male) на кабели RG-174, RG-316, обжимной"
   ]
  ]
 },
 {
  "article": "MCX-male R/A RG-174",
  "cable": "RG-174",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 3832"
   ],
   [
    "Наименование",
    "",
    "Разъём 11-121L (MCX-male, обжимной, угловой, на кабель RG-174, RG-316)"
   ]
  ]
 },
 {
  "article": "MMCX-female RG-174",
  "cable": "RG-174",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 3843"
   ],
   [
    "Наименование",
    "",
    "Разъём 13-211L (MMCX-female, обжимной, на кабель RG-174, RG-316)"
   ]
  ]
 },
 {
  "article": "MMCX-male R/A RG-174",
  "cable": "RG-174",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 3837"
   ],
   [
    "Наименование",
    "",
    "Разъём 13-121L (MMCX-male, обжимной, угловой, на кабель RG-174, RG-316)"
   ]
  ]
 },
 {
  "article": "RP-SMA-female RG-316",
  "cable": "RG-174",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 2745"
   ],
   [
    "Наименование",
    "",
    "Разъём S-A211L (RP-SMA-female, обжимной, на кабель RG-174, RG-316, реверсный)"
   ]
  ]
 },
 {
  "article": "SMA-female RG-174",
  "cable": "RG-174",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 1754"
   ],
   [
    "Наименование",
    "",
    "Разъём S-211L (SMA-female, обжимной, на кабель RG-174, RG-316)"
   ]
  ]
 },
 {
  "article": "SMA-male RG-174",
  "cable": "RG-174",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 1235"
   ],
   [
    "Наименование",
    "",
    "Разъём S-111L (SMA-male, обжимной, на кабель RG-174, RG-316)"
   ]
  ]
 },
 {
  "article": "SMA-male R/A RG-174",
  "cable": "RG-174",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 3026"
   ],
   [
    "Наименование",
    "",
    "Разъём S-121L (SMA-male, обжимной, на кабель RG-174, RG-316)"
   ]
  ]
 },
 {
  "article": "MHF1-plug RG-178",
  "cable": "RG-178",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 3148"
   ],
   [
    "Наименование",
    "",
    "Разъём U.fl female (IPX) для кабеля RG-178, обжимной угловой"
   ]
  ]
 },
 {
  "article": "MMCX-male RG-178",
  "cable": "RG-178",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "amel.ru: MMCX-J2"
   ]
  ]
 },
 {
  "article": "MMCX-male R/A RG-178",
  "cable": "RG-178",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "amel.ru: MMCX-JW2"
   ]
  ]
 },
 {
  "article": "SMA-female RG-178",
  "cable": "RG-178",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 4569"
   ],
   [
    "Наименование",
    "",
    "Разъём S-235M (SMA-female, напаиваемый, на кабель RG-178)"
   ]
  ]
 },
 {
  "article": "SMA-11_SMA-50-1-4/111_N",
  "cable": "RG-178",
  "edits": [
   [
    "Артикул",
    "SMA-11_SMA-50-1-4/111_N",
    "11_SMA-50-1-4/111_NE"
   ]
  ]
 },
 {
  "article": "SMA-male RG-178",
  "cable": "RG-178",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 4570"
   ],
   [
    "Наименование",
    "",
    "Разъём S-115M (SMA-male, напаиваемый, на кабель RG-178)"
   ]
  ]
 },
 {
  "article": "U.FL",
  "cable": "RG-178",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 3148"
   ],
   [
    "Наименование",
    "",
    "Разъём U.fl female (IPX) для кабеля RG-178, обжимной угловой"
   ]
  ]
 },
 {
  "article": "UHF-Male U-112B",
  "cable": "RG-213",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 1478"
   ],
   [
    "Наименование",
    "",
    "Разъём U-112B (UHF-male, прижимной, на кабель RG-213)"
   ]
  ]
 },
 {
  "article": "N-female RG-223",
  "cable": "RG-223",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 0299"
   ],
   [
    "Наименование",
    "",
    "Разъём N-211F (N-female, обжимной, на кабель RG-58)"
   ]
  ]
 },
 {
  "article": "N-male RG-223",
  "cable": "RG-223",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 0293"
   ],
   [
    "Наименование",
    "",
    "Разъём N-111F (N-male, обжимной, на кабель RG-58)"
   ]
  ]
 },
 {
  "article": "SMA-female RG-223",
  "cable": "RG-223",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 1325"
   ],
   [
    "Наименование",
    "",
    "Разъём S-211F (SMA-female, обжимной, на кабель RG-58)"
   ]
  ]
 },
 {
  "article": "SMA-male R/A RG-223",
  "cable": "RG-223",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 2849"
   ],
   [
    "Наименование",
    "",
    "Разъём S-121F (SMA-male, обжимной, угловой, на кабель RG-58)"
   ]
  ]
 },
 {
  "article": "Fakra Z-Female",
  "cable": "RG-316",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 3166"
   ],
   [
    "Наименование",
    "",
    "Разъём Fakra тип Z (Water Blue / 5021) female обжимной для кабеля RG-316"
   ]
  ]
 },
 {
  "article": "MCX-male R/A RG-316",
  "cable": "RG-316",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 3832"
   ],
   [
    "Наименование",
    "",
    "Разъём 11-121L (MCX-male, обжимной, угловой, на кабель RG-174, RG-316)"
   ]
  ]
 },
 {
  "article": "MMCX-female RG-316",
  "cable": "RG-316",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 3843"
   ],
   [
    "Наименование",
    "",
    "Разъём 13-211L (MMCX-female, обжимной, на кабель RG-174, RG-316)"
   ]
  ]
 },
 {
  "article": "MMCX-male R/A RG-316",
  "cable": "RG-316",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 3837"
   ],
   [
    "Наименование",
    "",
    "Разъём 13-121L (MMCX-male, обжимной, угловой, на кабель RG-174, RG-316)"
   ]
  ]
 },
 {
  "article": "RP-SMA-female RG-316",
  "cable": "RG-316",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 2745"
   ],
   [
    "Наименование",
    "",
    "Разъём S-A211L (RP-SMA-female, обжимной, на кабель RG-174, RG-316, реверсный)"
   ]
  ]
 },
 {
  "article": "SMA-female RG-316",
  "cable": "RG-316",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 1754"
   ],
   [
    "Наименование",
    "",
    "Разъём S-211L (SMA-female, обжимной, на кабель RG-174, RG-316)"
   ]
  ]
 },
 {
  "article": "SMA-male RG-316",
  "cable": "RG-316",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 1235"
   ],
   [
    "Наименование",
    "",
    "Разъём S-111L (SMA-male, обжимной, на кабель RG-174, RG-316)"
   ]
  ]
 },
 {
  "article": "SMA-male R/A RG-316",
  "cable": "RG-316",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 3026"
   ],
   [
    "Наименование",
    "",
    "Разъём S-121L (SMA-male, обжимной, на кабель RG-174, RG-316)"
   ]
  ]
 },
 {
  "article": "N-female RG-400",
  "cable": "RG-400",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 0299"
   ],
   [
    "Наименование",
    "",
    "Разъём N-211F (N-female, обжимной, на кабель RG-58)"
   ]
  ]
 },
 {
  "article": "N-male RG-400",
  "cable": "RG-400",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 0293"
   ],
   [
    "Наименование",
    "",
    "Разъём N-111F (N-male, обжимной, на кабель RG-58)"
   ]
  ]
 },
 {
  "article": "N-female RG-402",
  "cable": "RG-402",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 4627"
   ],
   [
    "Наименование",
    "",
    "Разъём N-235-141 (N-female, напаиваемый, на кабель RG-402)"
   ]
  ]
 },
 {
  "article": "SMA-Male RG402 18GHz",
  "cable": "RG-402",
  "edits": [
   [
    "Артикул",
    "SMA-Male RG402 18GHz",
    "SMA-JB3"
   ],
   [
    "Артикул поставщика",
    "",
    "amel.ru: SMA-JB3"
   ]
  ]
 },
 {
  "article": "SMA-male RG-402",
  "cable": "RG-402",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 3449"
   ],
   [
    "Наименование",
    "",
    "Разъём S-115-141 (SMA-male, напаиваемый, на кабель RG-402)"
   ]
  ]
 },
 {
  "article": "SMA-male R/A RG-402",
  "cable": "RG-402",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 3438"
   ],
   [
    "Наименование",
    "",
    "Разъём S-125-141 (SMA-male, напаиваемый, угловой, на кабель RG-402)"
   ]
  ]
 },
 {
  "article": "SMA-Male 141",
  "cable": "RG-402",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 3449"
   ],
   [
    "Наименование",
    "",
    "Разъём S-115-141 (SMA-male, напаиваемый, на кабель RG-402)"
   ]
  ]
 },
 {
  "article": "SMA-female RG-405",
  "cable": "RG-405",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 3441"
   ],
   [
    "Наименование",
    "",
    "Разъём S-235-085 (SMA-female, напаиваемый, на кабель RG-405)"
   ]
  ]
 },
 {
  "article": "SMA-FEMALE S-235-085",
  "cable": "RG-405",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 3441"
   ],
   [
    "Наименование",
    "",
    "Разъём S-235-085 (SMA-female, напаиваемый, на кабель RG-405)"
   ]
  ]
 },
 {
  "article": "SMA-Female RG405 18GHz",
  "cable": "RG-405",
  "edits": [
   [
    "Артикул",
    "SMA-Female RG405 18GHz",
    "SMA-KB2"
   ]
  ]
 },
 {
  "article": "SMA-male RG-405",
  "cable": "RG-405",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 3221"
   ],
   [
    "Наименование",
    "",
    "Разъём S-115-085 (SMA-male, напаиваемый, на кабель RG-405)"
   ]
  ]
 },
 {
  "article": "SMA-Male RG405 18GHz",
  "cable": "RG-405",
  "edits": [
   [
    "Артикул",
    "SMA-Male RG405 18GHz",
    "SMA-JB2"
   ],
   [
    "Артикул поставщика",
    "",
    "amel.ru: SMA-JB2"
   ]
  ]
 },
 {
  "article": "SMB-Female RG405",
  "cable": "RG-405",
  "edits": [
   [
    "Артикул",
    "SMB-Female RG405",
    "SMB-JB2"
   ],
   [
    "Артикул поставщика",
    "",
    "amel.ru: SMB-JB2"
   ]
  ]
 },
 {
  "article": "TNC-Fermale приборно-кабельный",
  "cable": "RG-405",
  "edits": [
   [
    "Артикул",
    "TNC-Fermale приборно-кабельный",
    "TNC-KYB2"
   ],
   [
    "Артикул поставщика",
    "",
    "amel.ru: TNC-KYB2"
   ]
  ]
 },
 {
  "article": "N-female RG58",
  "cable": "RG-58",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 0299"
   ],
   [
    "Наименование",
    "",
    "Разъём N-211F (N-female, обжимной, на кабель RG-58)"
   ]
  ]
 },
 {
  "article": "N-male RG58",
  "cable": "RG-58",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 0293"
   ],
   [
    "Наименование",
    "",
    "Разъём N-111F (N-male, обжимной, на кабель RG-58)"
   ]
  ]
 },
 {
  "article": "SMA-female RG58",
  "cable": "RG-58",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 1325"
   ],
   [
    "Наименование",
    "",
    "Разъём S-211F (SMA-female, обжимной, на кабель RG-58)"
   ]
  ]
 },
 {
  "article": "SMA-male RG58",
  "cable": "RG-58",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 1314"
   ],
   [
    "Наименование",
    "",
    "Разъём S-111F (SMA-male, обжимной, на кабель RG-58)"
   ]
  ]
 },
 {
  "article": "SMA-male R/A RG58",
  "cable": "RG-58",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 2849"
   ],
   [
    "Наименование",
    "",
    "Разъём S-121F (SMA-male, обжимной, угловой, на кабель RG-58)"
   ]
  ]
 },
 {
  "article": "TNC-Female RG58",
  "cable": "RG-58",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 2698"
   ],
   [
    "Наименование",
    "",
    "Разъём T-211F (TNC-female, обжимной, на кабель RG-58)"
   ]
  ]
 },
 {
  "article": "TNC-Male RG58",
  "cable": "RG-58",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 1483"
   ],
   [
    "Наименование",
    "",
    "Разъём T-111F (TNC-male, обжимной, на кабель RG-58)"
   ]
  ]
 },
 {
  "article": "BNC-Male RG59",
  "cable": "RG-59",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 2087"
   ],
   [
    "Наименование",
    "",
    "Разъём B-111G (BNC-male, обжимной, на кабель RG-59)"
   ]
  ]
 },
 {
  "article": "SMB-Male",
  "cable": "RG-316",
  "edits": [
   [
    "Артикул поставщика",
    "",
    "gsm-repiteri.ru: 3419"
   ],
   [
    "Наименование",
    "",
    "Разъём S1-111L (SMB-male, обжимной, на кабель RG-174, RG-316)"
   ]
  ]
 }
]''')

REF_TABLES = ("warehouse_lots", "warehouse_writeoffs", "fulfillment_lines", "fulfillment_kit_reserves", "finished_goods",
              "shipment_lines", "reference_stock_links", "techcard_kitsets", "techcard_projects", "techcard_revisions",
              "techcard_generator_drafts", "techcard_harness_routes", "harness_component_templates", "receiving_lines",
              "procurement_lines", "requests", "production_tasks")

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
url = os.environ["ERP_DATABASE_URL"]
factory = build_references_uow_factory(build_engine(url))
now = moscow_now()
LOG = []


def key(article, cable):
    return str(article or "").strip().casefold(), cable_key(cable)


def references(article: str) -> int:
    """Hits of the exact name (case-blind, whole token) in every table that can carry a connector by its name."""
    pattern = re.compile(r"(?<![\w-])" + re.escape(article.casefold()) + r"(?![\w-])")
    hits = 0
    with create_engine(url).connect() as connection:
        for table in REF_TABLES:
            columns = [row[0] for row in connection.execute(text(
                "select column_name from information_schema.columns where table_name=:t and data_type in "
                "('character varying','text','json','jsonb')"), {"t": table})]
            if not columns:
                continue
            select = ", ".join(f'"{name}"::text' for name in columns)
            for row in connection.execute(text(f'select {select} from "{table}"')):
                if pattern.search(" | ".join(value for value in row if value).casefold()):
                    hits += 1
        for row in connection.execute(text("select tab, fields::text from tech_operation_records where tab <> 'coax'")):
            if pattern.search(str(row[1]).casefold()):
                hits += 1
    return hits


with factory() as uow:
    coax = uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100_000)
pairs = {}
by_article = {}
for record in coax:
    pairs.setdefault(key(record.fields.get("coaxArticle") or record.fields.get("Артикул"), record.fields.get("coaxWire")), []).append(record)
    by_article.setdefault(str(record.fields.get("Артикул") or "").strip().casefold(), []).append(record)
print("строк БД.КОАКС:", len(coax))

for item in PAYLOAD:
    label = f"«{item['article']}» × «{item['cable']}»"
    found = pairs.get(key(item["article"], item["cable"]), [])
    if len(found) != 1:
        print(f"   отказ: {label} — строк найдено {len(found)}")
        LOG.append([label, "отказ", []])
        continue
    record = found[0]
    if "trl" in (str(record.fields.get("Производитель", "")) + str(record.fields.get("coaxMfr", ""))).casefold() \
            or str(record.fields.get("Артикул", "")).upper().startswith("ISMA"):
        print(f"   пропущено (ТРЛ): {label}")
        LOG.append([label, "ТРЛ", []])
        continue
    edits, shown = [], []
    refused = ""
    for field, old, new in item["edits"]:
        have = str(record.fields.get(field) or "").strip()
        if have == new:
            continue
        if have != old.strip():
            refused = f"{field}: сейчас «{have[:40]}», ждали «{old[:40]}»"
            break
        if field == "Артикул":
            if key(new, item["cable"]) in pairs or by_article.get(new.strip().casefold()):
                refused = f"новый артикул «{new}» уже есть в базе"
                break
            hits = references(item["article"])
            if hits:
                refused = f"на «{item['article']}» теперь ссылок: {hits}"
                break
        edits.append(FieldEdit(field, old, new))
        shown.append((field, old, new))
    if refused:
        print(f"   отказ: {label} ({refused})")
        LOG.append([label, "отказ", []])
        continue
    if not edits:
        print(f"   уже так: {label}")
        LOG.append([label, "уже так", []])
        continue
    with factory() as uow:
        try:
            before, after = _edit_in(record.id, tuple(edits), now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            print(f"   отказ: {label} ({error})")
            LOG.append([label, "отказ", []])
            continue
        _audit("references.tech_record_edited", ACTOR, now, {"record_id": str(before.id), "tab": before.tab.value,
               "fields": [edit.field for edit in edits], "reason": REASON}, uow)
        if not dry:
            uow.commit()
    print(f"   {'проверено' if dry else 'записано'}: {label} " + "; ".join(f"{f} «{o[:30]}» → «{n[:40]}»" for f, o, n in shown))
    LOG.append([label, "проверено" if dry else "записано", shown])

summary = {}
for _, status, _ in LOG:
    summary[status] = summary.get(status, 0) + 1
print("ИТОГО", json.dumps(summary, ensure_ascii=False), "(проверка, ничего не записано)" if dry else "(записано)")

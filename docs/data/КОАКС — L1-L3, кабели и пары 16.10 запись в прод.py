"""Запись в прод 16.10.2026 — БД.КОАКС: L1–L3 у строк без разделки, кабель у строк без кабеля, пары RG-174 ↔ RG-316.

Владелец 14.10 / 16.10: «ты все решаешь сам и закрываешь»; 16.10 вечером: «"примерно" не надо, будем на деле
проверять» — в базу пишутся только сами значения, без пометок; как получено каждое значение — только в журнале
docs/data/«КОАКС — L1-L3, кабели и пары 16.10.csv» (+ .md).

  1. L1–L3 у 29 строк с кабелем (накручиваемые UHF/N, SMP/SMB/MCX/TNC на RG-405, F на RG-402, СР-50) — `_edit_in`
     (как «Изменить» в «Базе данных»), у каждой ячейки старое значение "" (заполненная приходит отказом).
  2. 12 строк без кабеля: «Провод» / coaxWire "" → кабель и L1–L3 — `_edit_in` со старым "" у каждой ячейки; отказ,
     если пара «артикул × новый кабель» уже есть.
  3. 9 новых строк-пар RG-174 ↔ RG-316 (копия строки разъёма с другим кабелем) — `import_rows("coax")`; отказ, если
     пара уже есть или строка-образец поменялась после выгрузки 16.10 вечером.
D1–D3 в строки БД.КОАКС не пишутся и не копируются (владелец 16.10: D — только у кабеля, СПР.КАБ).
  4. Пары «/5D» на RG-59 / RG-6 / SAT-703 — не трогаются (в журнале — почему).
Строки ищутся по натуральному ключу coaxArticle + coaxWire (cable_key), не по ID. Ничего не удаляется и не
переименовывается («Артикул» не меняется).

Порядок (live-data protocol, prod-reference-write):
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → 1–2: «отказ: уже заполнено»/«уже так», 3: «уже есть»
Откат: 1–2 — вернуть ячейкам журнала пустое значение («Провод»/coaxWire у задачи 2 — пусто); 3 — удалить 9 строк
«артикул × кабель» из журнала (задача 3)."""
import json
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in
from app.modules.references.domain import ReferenceValidationError, StaleRecordError, TechOperationTab, cable_key
from app.modules.references.importers import import_rows
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Заполнение БД.КОАКС 16.10.2026", department="System")
REASON = "L1–L3, кабель, пары RG-174/RG-316 — решения 16.10.2026 (владелец: «решай сам и закрывай»)"
PAYLOAD = json.loads(r'''
{
 "edits": [
  {
   "task": 1,
   "article": "U-113/5D",
   "cable": "5D-FB",
   "set_cable": "",
   "cells": {
    "L1": "10",
    "L2": "20"
   }
  },
  {
   "task": 1,
   "article": "U-113/5D",
   "cable": "CNT-300",
   "set_cable": "",
   "cells": {
    "L1": "10",
    "L2": "20"
   }
  },
  {
   "task": 1,
   "article": "U-113/5D",
   "cable": "LMR-300",
   "set_cable": "",
   "cells": {
    "L1": "10",
    "L2": "20"
   }
  },
  {
   "task": 1,
   "article": "U-113/5D",
   "cable": "RG-59",
   "set_cable": "",
   "cells": {
    "L1": "10",
    "L2": "20"
   }
  },
  {
   "task": 1,
   "article": "U-113/5D",
   "cable": "RG-6",
   "set_cable": "",
   "cells": {
    "L1": "10",
    "L2": "20"
   }
  },
  {
   "task": 1,
   "article": "U-113/5D",
   "cable": "SAT-703",
   "set_cable": "",
   "cells": {
    "L1": "10",
    "L2": "20"
   }
  },
  {
   "task": 1,
   "article": "U-113F",
   "cable": "RG-58",
   "set_cable": "",
   "cells": {
    "L1": "10",
    "L2": "20"
   }
  },
  {
   "task": 1,
   "article": "U-113/8D",
   "cable": "8D-FB CCA",
   "set_cable": "",
   "cells": {
    "L1": "15",
    "L2": "16,5",
    "L3": "34,3"
   }
  },
  {
   "task": 1,
   "article": "U-113/8D",
   "cable": "8D-FB CU",
   "set_cable": "",
   "cells": {
    "L1": "15",
    "L2": "16,5",
    "L3": "34,3"
   }
  },
  {
   "task": 1,
   "article": "U-113B",
   "cable": "DX-1000",
   "set_cable": "",
   "cells": {
    "L1": "15",
    "L2": "16,5",
    "L3": "34,3"
   }
  },
  {
   "task": 1,
   "article": "U-113B",
   "cable": "DX-10A",
   "set_cable": "",
   "cells": {
    "L1": "15",
    "L2": "16,5",
    "L3": "34,3"
   }
  },
  {
   "task": 1,
   "article": "U-113B",
   "cable": "H-1000",
   "set_cable": "",
   "cells": {
    "L1": "15",
    "L2": "16,5",
    "L3": "34,3"
   }
  },
  {
   "task": 1,
   "article": "U-113B",
   "cable": "RG-213",
   "set_cable": "",
   "cells": {
    "L1": "15",
    "L2": "16,5",
    "L3": "34,3"
   }
  },
  {
   "task": 1,
   "article": "U-113B",
   "cable": "RG-8",
   "set_cable": "",
   "cells": {
    "L1": "15",
    "L2": "16,5",
    "L3": "34,3"
   }
  },
  {
   "task": 1,
   "article": "N-113A",
   "cable": "RG-6",
   "set_cable": "",
   "cells": {
    "L1": "7,5"
   }
  },
  {
   "task": 1,
   "article": "N-253F",
   "cable": "RG-58",
   "set_cable": "",
   "cells": {
    "L1": "7,5"
   }
  },
  {
   "task": 1,
   "article": "F-215-141",
   "cable": "RG-402",
   "set_cable": "",
   "cells": {
    "L1": "3,1"
   }
  },
  {
   "task": 1,
   "article": "SMP-KW3",
   "cable": "RG-174",
   "set_cable": "",
   "cells": {
    "L1": "2,7",
    "L2": "3,4",
    "L3": "8,6"
   }
  },
  {
   "task": 1,
   "article": "SMP-KW3",
   "cable": "RG-316",
   "set_cable": "",
   "cells": {
    "L1": "2,7",
    "L2": "3,4",
    "L3": "8,6"
   }
  },
  {
   "task": 1,
   "article": "SMP-KW2.5D",
   "cable": "RG-316D",
   "set_cable": "",
   "cells": {
    "L1": "2,7",
    "L2": "3,4",
    "L3": "8,6"
   }
  },
  {
   "task": 1,
   "article": "MCX-male R/A RG-405",
   "cable": "RG-405",
   "set_cable": "",
   "cells": {
    "L1": "3"
   }
  },
  {
   "task": 1,
   "article": "SMB-Female RG405",
   "cable": "RG-405",
   "set_cable": "",
   "cells": {
    "L1": "3,1",
    "L2": "3,6"
   }
  },
  {
   "task": 1,
   "article": "SMP-Female RG405 18GHz",
   "cable": "RG-405",
   "set_cable": "",
   "cells": {
    "L1": "3,1",
    "L2": "3,6"
   }
  },
  {
   "task": 1,
   "article": "SMP-Female RG405 6GHz",
   "cable": "RG-405",
   "set_cable": "",
   "cells": {
    "L1": "3,1",
    "L2": "3,6"
   }
  },
  {
   "task": 1,
   "article": "19K107-271L5",
   "cable": "RG-405",
   "set_cable": "",
   "cells": {
    "L1": "3,1",
    "L2": "3,6"
   }
  },
  {
   "task": 1,
   "article": "TNC-Fermale приборно-кабельный",
   "cable": "RG-405",
   "set_cable": "",
   "cells": {
    "L1": "3,1",
    "L2": "3,6"
   }
  },
  {
   "task": 1,
   "article": "СР-50-155ФВ",
   "cable": "РК50-2-11",
   "set_cable": "",
   "cells": {
    "L1": "4",
    "L2": "6,4",
    "L3": "15,1"
   }
  },
  {
   "task": 1,
   "article": "СР50-130ПВ",
   "cable": "РК50-4-11",
   "set_cable": "",
   "cells": {
    "L1": "4",
    "L2": "7,7",
    "L3": "16"
   }
  },
  {
   "task": 1,
   "article": "СР-50-824ФВ",
   "cable": "РК50-4-11",
   "set_cable": "",
   "cells": {
    "L1": "4",
    "L2": "7,7",
    "L3": "16"
   }
  },
  {
   "task": 2,
   "article": "Fakra RTK031 Female",
   "cable": "",
   "set_cable": "RG-174",
   "cells": {
    "L1": "3",
    "L2": "4",
    "L3": "9"
   }
  },
  {
   "task": 2,
   "article": "Fakra RTK031 Male",
   "cable": "",
   "set_cable": "RG-174",
   "cells": {
    "L1": "3",
    "L2": "4",
    "L3": "9"
   }
  },
  {
   "task": 2,
   "article": "Fakra Type A Female",
   "cable": "",
   "set_cable": "RG-174",
   "cells": {
    "L1": "3",
    "L2": "4",
    "L3": "9"
   }
  },
  {
   "task": 2,
   "article": "QMA-KPD7",
   "cable": "",
   "set_cable": "5D-FB",
   "cells": {
    "L1": "2,4",
    "L2": "5,54",
    "L3": "15,7"
   }
  },
  {
   "task": 2,
   "article": "RP-SMA-female",
   "cable": "",
   "set_cable": "RF 1.37",
   "cells": {
    "L1": "3",
    "L2": "4,3",
    "L3": "7,5"
   }
  },
  {
   "task": 2,
   "article": "11_SMA-50-2-65/119_NE",
   "cable": "",
   "set_cable": "RG-405",
   "cells": {
    "L1": "3,1",
    "L2": "3,6"
   }
  },
  {
   "task": 2,
   "article": "SMB-Male",
   "cable": "",
   "set_cable": "RG-316",
   "cells": {
    "L1": "3,5",
    "L2": "5",
    "L3": "12,5"
   }
  },
  {
   "task": 2,
   "article": "SSMC-Female 6GHz",
   "cable": "",
   "set_cable": "RG-316",
   "cells": {
    "L1": "3",
    "L2": "4",
    "L3": "9"
   }
  },
  {
   "task": 2,
   "article": "СР-50-1014Ф-1",
   "cable": "",
   "set_cable": "РК50-2-11",
   "cells": {
    "L1": "4",
    "L2": "6,4",
    "L3": "15,1"
   }
  },
  {
   "task": 2,
   "article": "СР-50-1014ФМ-1",
   "cable": "",
   "set_cable": "РК50-2-11",
   "cells": {
    "L1": "4",
    "L2": "6,4",
    "L3": "15,1"
   }
  },
  {
   "task": 2,
   "article": "СР-50-1015Ф-1",
   "cable": "",
   "set_cable": "РК50-2-11",
   "cells": {
    "L1": "4",
    "L2": "6,4",
    "L3": "15,1"
   }
  },
  {
   "task": 2,
   "article": "СР-50-1015ФМ-1",
   "cable": "",
   "set_cable": "РК50-2-11",
   "cells": {
    "L1": "4",
    "L2": "6,4",
    "L3": "15,1"
   }
  }
 ],
 "new_rows": [
  {
   "source": {
    "article": "BNC-C174J",
    "cable": "RG-174",
    "fields": {
     "coaxArticle": "BNC-C174J",
     "coaxType": "BNC-female",
     "coaxMfr": "HOYUTEC",
     "coaxWire": "RG-174",
     "Артикул": "BNC-C174J",
     "Тип/Серия": "BNC-female",
     "Провод": "RG-174",
     "Производитель": "HOYUTEC",
     "L1": "4",
     "L2": "6,4",
     "L3": "15,1",
     "Тип пина": "обжим",
     "Тип экрана": "обжим",
     "HEX обжима, мм": "3,25"
    }
   },
   "fields": {
    "coaxArticle": "BNC-C174J",
    "coaxType": "BNC-female",
    "coaxMfr": "HOYUTEC",
    "coaxWire": "RG-316",
    "Артикул": "BNC-C174J",
    "Тип/Серия": "BNC-female",
    "Провод": "RG-316",
    "Производитель": "HOYUTEC",
    "L1": "4",
    "L2": "6,4",
    "L3": "15,1",
    "Тип пина": "обжим",
    "Тип экрана": "обжим",
    "HEX обжима, мм": "3,25"
   }
  },
  {
   "source": {
    "article": "BNC-C174P",
    "cable": "RG-174",
    "fields": {
     "coaxArticle": "BNC-C174P",
     "coaxType": "BNC-male",
     "coaxMfr": "HOYUTEC",
     "coaxWire": "RG-174",
     "Артикул": "BNC-C174P",
     "Тип/Серия": "BNC-male",
     "Провод": "RG-174",
     "Производитель": "HOYUTEC",
     "L1": "4",
     "L2": "6,4",
     "L3": "15,1",
     "Тип пина": "обжим",
     "Тип экрана": "обжим",
     "HEX обжима, мм": "3,25"
    }
   },
   "fields": {
    "coaxArticle": "BNC-C174P",
    "coaxType": "BNC-male",
    "coaxMfr": "HOYUTEC",
    "coaxWire": "RG-316",
    "Артикул": "BNC-C174P",
    "Тип/Серия": "BNC-male",
    "Провод": "RG-316",
    "Производитель": "HOYUTEC",
    "L1": "4",
    "L2": "6,4",
    "L3": "15,1",
    "Тип пина": "обжим",
    "Тип экрана": "обжим",
    "HEX обжима, мм": "3,25"
   }
  },
  {
   "source": {
    "article": "TNC-C174P",
    "cable": "RG-174",
    "fields": {
     "coaxArticle": "TNC-C174P",
     "coaxType": "TNC-male",
     "coaxWire": "RG-174",
     "Артикул": "TNC-C174P",
     "Тип/Серия": "TNC-male",
     "Провод": "RG-174",
     "L1": "4",
     "L2": "6,4",
     "L3": "15,1",
     "Тип пина": "обжим",
     "Тип экрана": "обжим",
     "Источник данных": "L1–L3 примерно: по Amphenol RF, Assembly Instructions C36 (TNC), 31-2315 (RG-174/188) — https://cdn.components.ru/media/PDF/8022964.pdf — тот же ряд и кабель, изготовитель другой",
     "HEX обжима, мм": "3,25"
    }
   },
   "fields": {
    "coaxArticle": "TNC-C174P",
    "coaxType": "TNC-male",
    "coaxWire": "RG-316",
    "Артикул": "TNC-C174P",
    "Тип/Серия": "TNC-male",
    "Провод": "RG-316",
    "L1": "4",
    "L2": "6,4",
    "L3": "15,1",
    "Тип пина": "обжим",
    "Тип экрана": "обжим",
    "HEX обжима, мм": "3,25"
   }
  },
  {
   "source": {
    "article": "SMA-C174J",
    "cable": "RG-174",
    "fields": {
     "coaxArticle": "SMA-C174J",
     "coaxType": "RP-SMA-male flange",
     "coaxMfr": "RUICHI",
     "coaxWire": "RG-174",
     "Артикул": "SMA-C174J",
     "Тип/Серия": "RP-SMA-male flange",
     "Провод": "RG-174",
     "Производитель": "RUICHI",
     "L1": "2,5",
     "L2": "4,2",
     "L3": "10,7",
     "Тип пина": "пайка",
     "Тип экрана": "обжим",
     "Источник данных": "L1–L3 примерно: по Amphenol RF, Assembly Instructions C53 (SMA), 901-9916 (RG-174/179/187/188/316) — https://cdn.components.ru/media/PDF/8013478.pdf — аналог для RP-SMA-male flange на RG-174",
     "HEX обжима, мм": "3,25"
    }
   },
   "fields": {
    "coaxArticle": "SMA-C174J",
    "coaxType": "RP-SMA-male flange",
    "coaxMfr": "RUICHI",
    "coaxWire": "RG-316",
    "Артикул": "SMA-C174J",
    "Тип/Серия": "RP-SMA-male flange",
    "Провод": "RG-316",
    "Производитель": "RUICHI",
    "L1": "2,5",
    "L2": "4,2",
    "L3": "10,7",
    "Тип пина": "пайка",
    "Тип экрана": "обжим",
    "HEX обжима, мм": "3,25"
   }
  },
  {
   "source": {
    "article": "SMA-LC174P",
    "cable": "RG-174",
    "fields": {
     "coaxArticle": "SMA-LC174P",
     "coaxType": "SMA-male R/A",
     "coaxMfr": "RUICHI",
     "coaxWire": "RG-174",
     "Артикул": "SMA-LC174P",
     "Тип/Серия": "SMA-male R/A",
     "Провод": "RG-174",
     "Производитель": "RUICHI",
     "L1": "2,6",
     "L2": "5,5",
     "L3": "12",
     "Тип пина": "пайка внутри корпуса",
     "Тип экрана": "обжим",
     "Источник данных": "L1–L3 примерно: по Amphenol RF, Assembly Instructions C53 (SMA), 901-9872 angle plug (RG-174/179/187/188/316) — https://cdn.components.ru/media/PDF/8013478.pdf — тот же ряд и кабель, изготовитель другой",
     "HEX обжима, мм": "3,25"
    }
   },
   "fields": {
    "coaxArticle": "SMA-LC174P",
    "coaxType": "SMA-male R/A",
    "coaxMfr": "RUICHI",
    "coaxWire": "RG-316",
    "Артикул": "SMA-LC174P",
    "Тип/Серия": "SMA-male R/A",
    "Провод": "RG-316",
    "Производитель": "RUICHI",
    "L1": "2,6",
    "L2": "5,5",
    "L3": "12",
    "Тип пина": "пайка внутри корпуса",
    "Тип экрана": "обжим",
    "HEX обжима, мм": "3,25"
   }
  },
  {
   "source": {
    "article": "SMA-C316F",
    "cable": "RG-316",
    "fields": {
     "coaxArticle": "SMA-C316F",
     "coaxType": "SMA-female bulkhead",
     "coaxMfr": "RUICHI",
     "coaxWire": "RG-316",
     "Артикул": "SMA-C316F",
     "Тип/Серия": "SMA-female bulkhead",
     "Провод": "RG-316",
     "Производитель": "RUICHI",
     "L1": "2,5",
     "L2": "4,8",
     "L3": "10",
     "Тип пина": "пайка",
     "Тип экрана": "обжим",
     "HEX обжима, мм": "3,25"
    }
   },
   "fields": {
    "coaxArticle": "SMA-C316F",
    "coaxType": "SMA-female bulkhead",
    "coaxMfr": "RUICHI",
    "coaxWire": "RG-174",
    "Артикул": "SMA-C316F",
    "Тип/Серия": "SMA-female bulkhead",
    "Провод": "RG-174",
    "Производитель": "RUICHI",
    "L1": "2,5",
    "L2": "4,8",
    "L3": "10",
    "Тип пина": "пайка",
    "Тип экрана": "обжим",
    "HEX обжима, мм": "3,25"
   }
  },
  {
   "source": {
    "article": "CRC9",
    "cable": "RG-316",
    "fields": {
     "Артикул": "CRC9",
     "Тип/Серия": "CRC9-male",
     "Провод": "RG-316",
     "L1": "3,5",
     "L2": "6",
     "L3": "12",
     "Серия": "CRC9",
     "Пол": "штекер",
     "Полярность": "обычная",
     "Исполнение": "прямой",
     "Монтаж": "обжимной",
     "Наименование": "Разъём CRC9 (CRC9-male, обжимной, на кабель RG-316)",
     "Ссылка": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-crc9-crc9-male-obzhimnoy-na-kabel-rg-316",
     "Примечание": "номер на сайте: 1046",
     "Источник данных": "gsm-repiteri.ru, 12.10.2026; L1–L3 примерно: по Coax Connectors Ltd 45-005-D3-AD (MCX crimp plug, RG-174/316): a 3,5, b 6, c 12, d 8,5 — https://coax-connectors.com/wp-content/uploads/2021/08/45-005-D3-AD-REV-A03-1.pdf — аналог для CRC9-male на RG-316",
     "coaxArticle": "CRC9",
     "coaxWire": "RG-316",
     "coaxType": "CRC9-male",
     "Артикул поставщика": "gsm-repiteri.ru: 1046",
     "HEX обжима, мм": "3,25"
    }
   },
   "fields": {
    "Артикул": "CRC9",
    "Тип/Серия": "CRC9-male",
    "Провод": "RG-174",
    "L1": "3,5",
    "L2": "6",
    "L3": "12",
    "Серия": "CRC9",
    "Пол": "штекер",
    "Полярность": "обычная",
    "Исполнение": "прямой",
    "Монтаж": "обжимной",
    "Наименование": "Разъём CRC9 (CRC9-male, обжимной, на кабель RG-316)",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-crc9-crc9-male-obzhimnoy-na-kabel-rg-316",
    "Примечание": "номер на сайте: 1046",
    "Источник данных": "gsm-repiteri.ru, 12.10.2026",
    "coaxArticle": "CRC9",
    "coaxWire": "RG-174",
    "coaxType": "CRC9-male",
    "Артикул поставщика": "gsm-repiteri.ru: 1046",
    "HEX обжима, мм": "3,25"
   }
  },
  {
   "source": {
    "article": "TS9",
    "cable": "RG-316",
    "fields": {
     "Артикул": "TS9",
     "Тип/Серия": "TS9-male",
     "Провод": "RG-316",
     "L1": "3,5",
     "L2": "6",
     "L3": "12",
     "Серия": "TS9",
     "Пол": "штекер",
     "Полярность": "обычная",
     "Исполнение": "прямой",
     "Монтаж": "обжимной",
     "Наименование": "Разъём TS9 (TS9-male, обжимной, на кабель RG-316)",
     "Ссылка": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-ts9-ts9-male-obzhimnoj-na-kabel-rg-316",
     "Примечание": "номер на сайте: 1045",
     "Источник данных": "gsm-repiteri.ru, 12.10.2026; L1–L3 примерно: по Coax Connectors Ltd 45-005-D3-AD (MCX crimp plug, RG-174/316): a 3,5, b 6, c 12, d 8,5 — https://coax-connectors.com/wp-content/uploads/2021/08/45-005-D3-AD-REV-A03-1.pdf — аналог для TS9-male на RG-316",
     "coaxArticle": "TS9",
     "coaxWire": "RG-316",
     "coaxType": "TS9-male",
     "Артикул поставщика": "gsm-repiteri.ru: 1045",
     "HEX обжима, мм": "3,25"
    }
   },
   "fields": {
    "Артикул": "TS9",
    "Тип/Серия": "TS9-male",
    "Провод": "RG-174",
    "L1": "3,5",
    "L2": "6",
    "L3": "12",
    "Серия": "TS9",
    "Пол": "штекер",
    "Полярность": "обычная",
    "Исполнение": "прямой",
    "Монтаж": "обжимной",
    "Наименование": "Разъём TS9 (TS9-male, обжимной, на кабель RG-316)",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-ts9-ts9-male-obzhimnoj-na-kabel-rg-316",
    "Примечание": "номер на сайте: 1045",
    "Источник данных": "gsm-repiteri.ru, 12.10.2026",
    "coaxArticle": "TS9",
    "coaxWire": "RG-174",
    "coaxType": "TS9-male",
    "Артикул поставщика": "gsm-repiteri.ru: 1045",
    "HEX обжима, мм": "3,25"
   }
  },
  {
   "source": {
    "article": "SMA-Female фланцевый RG316",
    "cable": "RG-316",
    "fields": {
     "coaxArticle": "SMA-Female фланцевый RG316",
     "coaxType": "SMA-female flange",
     "coaxWire": "RG-316",
     "Артикул": "SMA-Female фланцевый RG316",
     "Тип/Серия": "SMA-female flange",
     "Провод": "RG-316",
     "L1": "2,9",
     "L2": "3,9",
     "L3": "10,5",
     "Тип пина": "обжим",
     "Тип экрана": "обжим",
     "Источник данных": "продавец kroks.ru (было в «Производитель»); L1–L3 примерно: по Amphenol RF, Assembly Instructions C53 (SMA), 901-10021-3SF jack (RG-174/188/316) — https://cdn.components.ru/media/PDF/8013478.pdf — тот же ряд и кабель, изготовитель другой",
     "HEX обжима, мм": "3,25"
    }
   },
   "fields": {
    "coaxArticle": "SMA-Female фланцевый RG316",
    "coaxType": "SMA-female flange",
    "coaxWire": "RG-174",
    "Артикул": "SMA-Female фланцевый RG316",
    "Тип/Серия": "SMA-female flange",
    "Провод": "RG-174",
    "L1": "2,9",
    "L2": "3,9",
    "L3": "10,5",
    "Тип пина": "обжим",
    "Тип экрана": "обжим",
    "Источник данных": "продавец kroks.ru (было в «Производитель»)",
    "HEX обжима, мм": "3,25"
   }
  }
 ]
}''')

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
LOG = []
OK = "проверено" if dry else "записано"
NO_COPY = ("D1", "D2", "D3")  # owner 16.10: D1–D3 only from the cable, never written into a БД.КОАКС row


def log(task, article, cable, field, old, new, status):
    LOG.append({"task": task, "article": article, "cable": cable, "field": field, "old": old, "new": new, "status": status})
    print(f"   {status}: [{task}] «{article}» × «{cable}» {field}: «{old}» → «{new}»")


def key(article, cable):
    return str(article or "").strip().casefold(), cable_key(cable)


def snapshot():
    with factory() as uow:
        coax = uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100_000)
    pairs = {}
    for record in coax:
        pairs.setdefault(key(record.fields.get("coaxArticle"), record.fields.get("coaxWire")), []).append(record)
    return coax, pairs


coax, pairs = snapshot()
print("строк БД.КОАКС:", len(coax))

print("== 1–2. L1–L3 и кабель")
for item in PAYLOAD["edits"]:
    task, article, cable, new_cable = item["task"], item["article"], item["cable"], item["set_cable"]
    found = pairs.get(key(article, cable), [])
    if len(found) != 1:
        if new_cable and len(pairs.get(key(article, new_cable), [])) == 1:
            record = pairs[key(article, new_cable)][0]
            same = all(str(record.fields.get(f) or "").strip() == v for f, v in item["cells"].items())
            log(task, article, new_cable, "строка", "", "", "уже так" if same else "отказ (кабель уже стоит, ячейки другие)")
        else:
            log(task, article, cable, "строка", "", "", f"отказ (строк найдено {len(found)})")
        continue
    record = found[0]
    cells = dict(item["cells"])
    if new_cable:
        if pairs.get(key(article, new_cable)):
            log(task, article, cable, "Провод", "", new_cable, "отказ (пара с этим кабелем уже есть)")
            continue
    edits, done = [], []
    for name, value in cells.items():
        have = str(record.fields.get(name) or "").strip()
        if have == value:
            done.append(name)
        else:
            edits.append(FieldEdit(name, "", value))
    if new_cable:
        edits = [FieldEdit("Провод", "", new_cable), FieldEdit("coaxWire", "", new_cable), *edits]
    if not edits:
        log(task, article, cable, ",".join(done), "", "", "уже так")
        continue
    with factory() as uow:
        try:
            before, _after = _edit_in(record.id, tuple(edits), now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            for edit in edits:
                log(task, article, cable, edit.field, edit.old, edit.value, f"отказ ({error})")
            continue
        _audit("references.tech_record_edited", ACTOR, now, {
            "record_id": str(before.id), "tab": before.tab.value, "fields": [edit.field for edit in edits], "reason": REASON,
        }, uow)
        if not dry:
            uow.commit()
    for edit in edits:
        log(task, article, cable, edit.field, edit.old, edit.value, OK)

print("== 3. Пары RG-174 ↔ RG-316")
rows = []
for item in PAYLOAD["new_rows"]:
    fields, source = item["fields"], item["source"]
    article, cable = fields["coaxArticle"], fields["coaxWire"]
    if pairs.get(key(article, cable)):
        log(3, article, cable, "строка", "", "новая", "уже есть")
        continue
    found = pairs.get(key(source["article"], source["cable"]), [])
    if len(found) != 1:
        log(3, article, cable, "строка", "", "новая", f"отказ (строк-образцов {len(found)})")
        continue
    now_fields = {k: str(v).strip() for k, v in found[0].fields.items() if str(v or "").strip() and k not in NO_COPY}
    if now_fields != source["fields"]:
        log(3, article, cable, "строка", "", "новая", "отказ (строка-образец поменялась после выгрузки)")
        continue
    rows.append(ImportRowIn(fields=fields))
counts = {}
if rows:
    report = import_rows("coax", factory(), rows, ACTOR, now, dry_run=dry, source="erp").model_dump()
    counts = {"created": len(report["created"]), "updated": len(report["updated"]), "unchanged": report["unchanged"],
              "refused": len(report["refused"])}
    for entry in report["refused"]:
        print("   отказ импорта:", entry)
    for entry in report["updated"]:
        print("   ВНИМАНИЕ правка вместо новой строки:", entry)
    status = OK if not report["refused"] and not report["updated"] and len(report["created"]) == len(rows) else "отказ (см. импорт)"
    for row in rows:
        log(3, row.fields["coaxArticle"], row.fields["coaxWire"], "строка", "", "новая", status)
print("   импорт:", counts)

print("ИТОГО", json.dumps({s: sum(1 for i in LOG if i["status"] == s) for s in {i["status"] for i in LOG}}, ensure_ascii=False),
      "(проверка, ничего не записано)" if dry else "(записано)")
if not dry:
    print("строк БД.КОАКС после:", len(snapshot()[0]), "было:", len(coax))
print("LOG " + json.dumps(LOG, ensure_ascii=False))

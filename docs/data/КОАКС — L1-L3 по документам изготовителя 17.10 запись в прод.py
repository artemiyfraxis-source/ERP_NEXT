"""Запись в прод 17.10.2026 — БД.КОАКС: L1–L3 по документам самого разъёма (владелец 17.10: «реши сам, обойди ограничения»).

Журнал ячеек и откат — docs/data/«КОАКС — L1-L3 по документам изготовителя 17.10.csv» (+ .md «КОАКС — открытые задачи 17.10.md»).

  1. 42 строки: L1–L3, записанные агентами 15–16.10 по аналогам (Amphenol RF, Coax Connectors Ltd, RFS C02, «как у …»),
     заменены размерами из документа самого разъёма — инструкции/чертежа, на который ссылается карточка поставщика
     строки (gsm-repiteri: инструкции Балтик Сигнал S-111/S-211 и N-111/N-211, NM-13317, NM-7/8, DM-7/8, Spinner,
     TRB-1250; amel.ru: чертежи SMP-KW3, SMP-KW2.5D, SMB-KW3, MMCX-JW3, TNC-JW3Y, SMA-KB2, SMA-ВКП/ВКУ; chipdip:
     Huber+Suhner 16 SMA, Rosenberger MA_19E13). «Источник данных»: сегмент «L1–L3: …» заменён ссылкой на документ.
     Значения, которых не делал агент (строки цеха до 14.10), не трогаются — они в журнале «оставлено».
  5. QMA-KPD7 × 5D-FB: приборная розетка без кабеля (amel.ru) — L1–L3 сняты, причина в «Источнике данных»;
     «Провод» оставлен (часть ключа артикул + кабель). На проде на QMA-KPD7 не ссылается ничего, кроме журнала.
  4. U-113/8D: «8D-FB CU» → «8D-FB Cu» (Провод, coaxWire) — как в каталоге кабелей; ключ (cable_key) тот же.

Правка — `_edit_in` (как «Изменить» в «Базе данных»), у каждой ячейки сверяется старое значение (иначе — отказ всей
строки). Строка ищется по ключу «артикул + кабель» (cable_key), не по ID. Ничего не удаляется и не переименовывается.

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → всё «уже так»
Откат: вернуть ячейкам журнала значение «было» (тот же скрипт с переставленными old/new).
"""
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
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 17.10.2026", department="System")
REASON = "L1–L3 по документу самого разъёма; QMA-KPD7 без кабеля; написание 8D-FB Cu (17.10.2026)"
PAYLOAD = json.loads(r'''
[
 {
  "task": 1,
  "article": "N-211B",
  "cable": "RG-213",
  "cells": [
   [
    "L1",
    "3,6",
    "5"
   ],
   [
    "L2",
    "6",
    "7"
   ],
   [
    "L3",
    "13,5",
    "15"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C11 (N), 82-340 (RG-8/213) — https://cdn.components.ru/media/PDF/8014987.pdf — аналог для N-female на RG-213",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма N (N-111, N-211): оболочка 15, фольга и диэлектрик 5, фольга ещё 2; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/connector-n-type/razyom-n-211b-n-female-obzhimnoy-na-cable-rg-213"
 },
 {
  "task": 1,
  "article": "NM-13317-1/2",
  "cable": "1/2″",
  "cells": [
   [
    "L1",
    "8,75",
    "10,5"
   ],
   [
    "L3",
    "17,5",
    "30,5"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по RFS, Installation Instruction 10000009476-02 (LCF/RCF 12-50, OMNI FIT C02, вся серия C02): внутренний проводник выступает 8,5–9,0, оболочка снята 17–18 мм, внешний проводник обрезан заподлицо с диэлектриком — https://www.rfstechnologies.com/images/connectors/10000009476-02.pdf — аналог для на 1/2″",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции по сборке NM-13317-1/2 (с карточки gsm-repiteri), рис. 2: внутренний проводник 10,5, внешний проводник 20 — https://gsm-repiteri.ru/files/2023/08/razem-nm-13317-1-2-gost-instrukciya.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2023/08/razem-nm-13317-1-2-gost-instrukciya.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razem-nm-13317-1-2-gost"
 },
 {
  "task": 1,
  "article": "N-111/10D",
  "cable": "10D-FB",
  "cells": [
   [
    "L1",
    "6,4",
    "5"
   ],
   [
    "L2",
    "8,7",
    "7"
   ],
   [
    "L3",
    "21,4",
    "15"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C11 (N), 82-5993 (LMR-600) — https://cdn.components.ru/media/PDF/8014987.pdf — аналог для N-male на 10D-FB",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма N (N-111, N-211): оболочка 15, фольга и диэлектрик 5, фольга ещё 2; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyem-n-111-10d-n-male-obzhimnoy-na-kabel-10d"
 },
 {
  "task": 1,
  "article": "N-111C/8D",
  "cable": "8D-FB",
  "cells": [
   [
    "L1",
    "3,6",
    "5"
   ],
   [
    "L2",
    "6",
    "7"
   ],
   [
    "L3",
    "13,5",
    "15"
   ],
   [
    "Источник данных",
    "L1–L3: по Amphenol RF, Assembly Instructions C11 (N), 82-340 (RG-8/213) — https://cdn.components.ru/media/PDF/8014987.pdf — аналог для N-male на 8D-FB; 16.10.2026, качество данных: «Тип пина» «пайка» → «»: gsm-repiteri.ru/prodazha/razyomi/razyem-n-111c-8d (Ссылка строки): «центральный контакт - цанговый и не требует пайки», «Монтаж центрального контакта: цанга»; словарь «Тип пина» — только «пайка», цанга = пусто (pin_type, 24 строки «цанга»)",
    "16.10.2026, качество данных: «Тип пина» «пайка» → «»: gsm-repiteri.ru/prodazha/razyomi/razyem-n-111c-8d (Ссылка строки): «центральный контакт - цанговый и не требует пайки», «Монтаж центрального контакта: цанга»; словарь «Тип пина» — только «пайка», цанга = пусто (pin_type, 24 строки «цанга»); L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма N (N-111, N-211): оболочка 15, фольга и диэлектрик 5, фольга ещё 2; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyem-n-111c-8d"
 },
 {
  "task": 1,
  "article": "N-211/5D",
  "cable": "5D-FB",
  "cells": [
   [
    "L1",
    "4,7",
    "5"
   ],
   [
    "L2",
    "7,9",
    "7"
   ],
   [
    "L3",
    "17,4",
    "15"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C11 (N), 82-6106 (LMR-240) — https://cdn.components.ru/media/PDF/8014987.pdf — аналог для N-female на 5D-FB",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма N (N-111, N-211): оболочка 15, фольга и диэлектрик 5, фольга ещё 2; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyem-n-211-5d-n-female-obzhimnoy-na-kabel-5d"
 },
 {
  "task": 1,
  "article": "N-211/8D",
  "cable": "8D-FB",
  "cells": [
   [
    "L1",
    "3,6",
    "5"
   ],
   [
    "L2",
    "6",
    "7"
   ],
   [
    "L3",
    "13,5",
    "15"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C11 (N), 82-340 (RG-8/213) — https://cdn.components.ru/media/PDF/8014987.pdf — аналог для N-female на 8D-FB",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма N (N-111, N-211): оболочка 15, фольга и диэлектрик 5, фольга ещё 2; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyem-n-211-8d-n-female-obzhimnoy-na-kabel-8d"
 },
 {
  "task": 1,
  "article": "S-111F",
  "cable": "RG-58",
  "cells": [
   [
    "L1",
    "3,5",
    "2,5"
   ],
   [
    "L2",
    "5,2",
    "5,5"
   ],
   [
    "L3",
    "13,5",
    "12"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C53 (SMA), 901-9870 (RG-58) — https://cdn.components.ru/media/PDF/8013478.pdf — тот же ряд и кабель, изготовитель другой",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма SMA (S-111, S-211): оболочка 12, фольга и диэлектрик 2,5, фольга ещё 3; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-sma-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-sma-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyem-s-111f-sma-male-obzhimnoj-na-kabel-rg-58"
 },
 {
  "task": 1,
  "article": "S-211/5D",
  "cable": "5D-FB",
  "cells": [
   [
    "L1",
    "2,4",
    "2,5"
   ],
   [
    "L2",
    "5,54",
    "5,5"
   ],
   [
    "L3",
    "15,7",
    "12"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C53 (SMA), 901-10011 (LMR-240) — https://cdn.components.ru/media/PDF/8013478.pdf — аналог для SMA-female на 5D-FB",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма SMA (S-111, S-211): оболочка 12, фольга и диэлектрик 2,5, фольга ещё 3; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-sma-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-sma-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyem-s-211-5d-sma-female-obzhimnoy-na-kabel-5d"
 },
 {
  "task": 1,
  "article": "N-211F",
  "cable": "RG-58",
  "cells": [
   [
    "L1",
    "4,6",
    "5"
   ],
   [
    "L2",
    "6,8",
    "7"
   ],
   [
    "L3",
    "16,3",
    "15"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C11 (N), 82-5378 bulkhead jack (RG-58/141) — https://cdn.components.ru/media/PDF/8014987.pdf — тот же ряд и кабель, изготовитель другой",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма N (N-111, N-211): оболочка 15, фольга и диэлектрик 5, фольга ещё 2; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyem_n-211f_obzhimnoi_razyem_n-tipa_dlya_kabelya_rg-58"
 },
 {
  "task": 1,
  "article": "S-111/5D",
  "cable": "5D-FB",
  "cells": [
   [
    "L1",
    "2,4",
    "2,5"
   ],
   [
    "L2",
    "5,54",
    "5,5"
   ],
   [
    "L3",
    "15,7",
    "12"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C53 (SMA), 901-10011 (LMR-240) — https://cdn.components.ru/media/PDF/8013478.pdf — аналог для SMA-male на 5D-FB",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма SMA (S-111, S-211): оболочка 12, фольга и диэлектрик 2,5, фольга ещё 3; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-sma-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-sma-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyem_s-111-5d_obzhimnoi_razyem_sma-tipa_dlya_kabelya_5d-fb"
 },
 {
  "task": 1,
  "article": "DM-7/8",
  "cable": "7/8″",
  "cells": [
   [
    "L3",
    "21,25",
    "32"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по RFS, Installation Instruction 10000006027-05 (LCF/UCF 78-50, OMNI FIT C02): кабель обрезан заподлицо, оболочка снята 21,0–21,5 мм — https://rfstechnologies.com/images/connectors/10000006027-05_install-instr_conn-c02_lcf-ucf78-c02_red.pdf — аналог для DIN-male на 7/8″",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по RFS / SPINNER, Installation Instructions M30794d (LCF78-50, RAPID FIT / CAF; с карточки DM-7/8 gsm-repiteri): кабель режется заподлицо, оболочка 32 (1 1/4″) — https://gsm-repiteri.ru/files/2018/10/razyom-dm-7-8-instrukciya.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2018/10/razyom-dm-7-8-instrukciya.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-dm-7-8"
 },
 {
  "task": 1,
  "article": "N-111B",
  "cable": "RG-213",
  "cells": [
   [
    "L1",
    "3,6",
    "5"
   ],
   [
    "L2",
    "6",
    "7"
   ],
   [
    "L3",
    "13,5",
    "15"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C11 (N), 82-340 (RG-8/213) — https://cdn.components.ru/media/PDF/8014987.pdf — тот же ряд и кабель, изготовитель другой",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма N (N-111, N-211): оболочка 15, фольга и диэлектрик 5, фольга ещё 2; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-n-111b-n-male-obzhimnoy-na-cable-rg-213"
 },
 {
  "task": 1,
  "article": "N-111/8D",
  "cable": "8D-FB",
  "cells": [
   [
    "L1",
    "3,6",
    "5"
   ],
   [
    "L2",
    "6",
    "7"
   ],
   [
    "L3",
    "13,5",
    "15"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C11 (N), 82-340 (RG-8/213) — https://cdn.components.ru/media/PDF/8014987.pdf — аналог для N-male на 8D-FB",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма N (N-111, N-211): оболочка 15, фольга и диэлектрик 5, фольга ещё 2; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-n-8d-vilka-obzhim-n-1118d"
 },
 {
  "task": 1,
  "article": "N-111/5D",
  "cable": "5D-FB",
  "cells": [
   [
    "L1",
    "4,7",
    "5"
   ],
   [
    "L2",
    "7,9",
    "7"
   ],
   [
    "L3",
    "17,4",
    "15"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C11 (N), 82-6106 (LMR-240) — https://cdn.components.ru/media/PDF/8014987.pdf — аналог для N-male на 5D-FB",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма N (N-111, N-211): оболочка 15, фольга и диэлектрик 5, фольга ещё 2; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-n-tipa-dlya-kabelya-5d-fb-obzhimnoy-n-1115d"
 },
 {
  "task": 1,
  "article": "N-111/5D",
  "cable": "CNT-300",
  "cells": [
   [
    "L1",
    "4,7",
    "5"
   ],
   [
    "L2",
    "7,9",
    "7"
   ],
   [
    "L3",
    "17,4",
    "15"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C11 (N), 82-6106 (LMR-240) — https://cdn.components.ru/media/PDF/8014987.pdf — аналог для N-male на CNT-300",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма N (N-111, N-211): оболочка 15, фольга и диэлектрик 5, фольга ещё 2; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-n-tipa-dlya-kabelya-5d-fb-obzhimnoy-n-1115d"
 },
 {
  "task": 1,
  "article": "N-111/5D",
  "cable": "LMR-300",
  "cells": [
   [
    "L1",
    "4,7",
    "5"
   ],
   [
    "L2",
    "7,9",
    "7"
   ],
   [
    "L3",
    "17,4",
    "15"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C11 (N), 82-6106 (LMR-240) — https://cdn.components.ru/media/PDF/8014987.pdf — аналог для N-male на LMR-300",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма N (N-111, N-211): оболочка 15, фольга и диэлектрик 5, фольга ещё 2; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-n-tipa-dlya-kabelya-5d-fb-obzhimnoy-n-1115d"
 },
 {
  "task": 1,
  "article": "N-111/5D",
  "cable": "RG-59",
  "cells": [
   [
    "L1",
    "4,7",
    "5"
   ],
   [
    "L2",
    "7,9",
    "7"
   ],
   [
    "L3",
    "17,4",
    "15"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C11 (N), 82-6106 (LMR-240) — https://cdn.components.ru/media/PDF/8014987.pdf — аналог для N-male на RG-59",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма N (N-111, N-211): оболочка 15, фольга и диэлектрик 5, фольга ещё 2; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-n-tipa-dlya-kabelya-5d-fb-obzhimnoy-n-1115d"
 },
 {
  "task": 1,
  "article": "N-111/5D",
  "cable": "RG-6",
  "cells": [
   [
    "L1",
    "4,7",
    "5"
   ],
   [
    "L2",
    "7,9",
    "7"
   ],
   [
    "L3",
    "17,4",
    "15"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C11 (N), 82-6106 (LMR-240) — https://cdn.components.ru/media/PDF/8014987.pdf — аналог для N-male на RG-6",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма N (N-111, N-211): оболочка 15, фольга и диэлектрик 5, фольга ещё 2; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-n-tipa-dlya-kabelya-5d-fb-obzhimnoy-n-1115d"
 },
 {
  "task": 1,
  "article": "N-111/5D",
  "cable": "SAT-703",
  "cells": [
   [
    "L1",
    "4,7",
    "5"
   ],
   [
    "L2",
    "7,9",
    "7"
   ],
   [
    "L3",
    "17,4",
    "15"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C11 (N), 82-6106 (LMR-240) — https://cdn.components.ru/media/PDF/8014987.pdf — аналог для N-male на SAT-703",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма N (N-111, N-211): оболочка 15, фольга и диэлектрик 5, фольга ещё 2; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-n-type-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-n-tipa-dlya-kabelya-5d-fb-obzhimnoy-n-1115d"
 },
 {
  "task": 1,
  "article": "NM-7/8",
  "cable": "7/8″",
  "cells": [
   [
    "L1",
    "",
    "3,5"
   ],
   [
    "L3",
    "21,25",
    "20"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по RFS, Installation Instruction 10000006027-05 (LCF/UCF 78-50, OMNI FIT C02): кабель обрезан заподлицо, оболочка снята 21,0–21,5 мм — https://rfstechnologies.com/images/connectors/10000006027-05_install-instr_conn-c02_lcf-ucf78-c02_red.pdf — аналог для N-male на 7/8″",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по N-JA(7/8)-RC3 Assembly instruction (с карточки NM-7/8 gsm-repiteri; кабели AVA5-50, LCF78-50JA, RFA7/8″-50): внутренний проводник 3,5, внешний 16,5 — https://gsm-repiteri.ru/files/2018/10/razyom-nm-7-8-instrukciya.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2018/10/razyom-nm-7-8-instrukciya.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-nm-7-8"
 },
 {
  "task": 1,
  "article": "S-111/8D",
  "cable": "8D-FB",
  "cells": [
   [
    "L1",
    "2,4",
    "2,5"
   ],
   [
    "L2",
    "5,54",
    "5,5"
   ],
   [
    "L3",
    "15,7",
    "12"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C53 (SMA), 901-10011 (LMR-240) — https://cdn.components.ru/media/PDF/8013478.pdf — аналог для SMA-male на 8D-FB",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма SMA (S-111, S-211): оболочка 12, фольга и диэлектрик 2,5, фольга ещё 3; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-sma-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-sma-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-s-111-8d"
 },
 {
  "task": 1,
  "article": "S-111B",
  "cable": "RG-213",
  "cells": [
   [
    "L1",
    "2,4",
    "2,5"
   ],
   [
    "L2",
    "5,54",
    "5,5"
   ],
   [
    "L3",
    "15,7",
    "12"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C53 (SMA), 901-10011 (LMR-240) — https://cdn.components.ru/media/PDF/8013478.pdf — аналог для SMA-male на RG-213",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма SMA (S-111, S-211): оболочка 12, фольга и диэлектрик 2,5, фольга ещё 3; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-sma-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-sma-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-s-111b-sma-male-obzhimnoy-na-cable-rg-213"
 },
 {
  "task": 1,
  "article": "S-111F NGT",
  "cable": "RG-58",
  "cells": [
   [
    "L1",
    "3,5",
    "2,5"
   ],
   [
    "L2",
    "5,2",
    "5,5"
   ],
   [
    "L3",
    "13,5",
    "12"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C53 (SMA), 901-9870 (RG-58) — https://cdn.components.ru/media/PDF/8013478.pdf — тот же ряд и кабель, изготовитель другой",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма SMA (S-111, S-211): оболочка 12, фольга и диэлектрик 2,5, фольга ещё 3; оплётка отгибается — https://gsm-repiteri.ru/files/2026/09/razem-s-111-instrukciya-po-ustanovke.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2026/09/razem-s-111-instrukciya-po-ustanovke.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-s-111f-ngt"
 },
 {
  "task": 1,
  "article": "S-211/8D",
  "cable": "8D-FB",
  "cells": [
   [
    "L1",
    "2,4",
    "2,5"
   ],
   [
    "L2",
    "5,54",
    "5,5"
   ],
   [
    "L3",
    "15,7",
    "12"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C53 (SMA), 901-10011 (LMR-240) — https://cdn.components.ru/media/PDF/8013478.pdf — аналог для SMA-female на 8D-FB",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма SMA (S-111, S-211): оболочка 12, фольга и диэлектрик 2,5, фольга ещё 3; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-sma-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-sma-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-s-211-8d-sma-female-obzhimnoy-na-cable-8d"
 },
 {
  "task": 1,
  "article": "S-211B",
  "cable": "RG-213",
  "cells": [
   [
    "L1",
    "2,4",
    "2,5"
   ],
   [
    "L2",
    "5,54",
    "5,5"
   ],
   [
    "L3",
    "15,7",
    "12"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C53 (SMA), 901-10011 (LMR-240) — https://cdn.components.ru/media/PDF/8013478.pdf — аналог для SMA-female на RG-213",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма SMA (S-111, S-211): оболочка 12, фольга и диэлектрик 2,5, фольга ещё 3; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-sma-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-sma-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-s-211b-sma-female-obzhimnoy-na-cable-rg-213"
 },
 {
  "task": 1,
  "article": "S-211F",
  "cable": "RG-58",
  "cells": [
   [
    "L1",
    "2,9",
    "2,5"
   ],
   [
    "L2",
    "3,9",
    "5,5"
   ],
   [
    "L3",
    "10,5",
    "12"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C53 (SMA), 901-10021-1SF jack (RG-55/142/223/400) — https://cdn.components.ru/media/PDF/8013478.pdf — аналог для SMA-female на RG-58",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма SMA (S-111, S-211): оболочка 12, фольга и диэлектрик 2,5, фольга ещё 3; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-sma-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-sma-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-s-211f-sma-female-obzhimnoj-na-kabel-rg-58"
 },
 {
  "task": 1,
  "article": "S-231F NGT",
  "cable": "RG-58",
  "cells": [
   [
    "L1",
    "2,9",
    "2,5"
   ],
   [
    "L2",
    "3,9",
    "5,5"
   ],
   [
    "L3",
    "10,5",
    "12"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Amphenol RF, Assembly Instructions C53 (SMA), 901-10021-1SF jack (RG-55/142/223/400) — https://cdn.components.ru/media/PDF/8013478.pdf — аналог для SMA-female на RG-58",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по инструкции Балтик Сигнал по установке обжимного разъёма SMA (S-111, S-211): оболочка 12, фольга и диэлектрик 2,5, фольга ещё 3; оплётка отгибается — https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-sma-crimp.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2024/11/connector-baltic-signal-sma-crimp.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-s-231f-ngt"
 },
 {
  "task": 1,
  "article": "Spinner MultiFit LF 1/2″-50",
  "cable": "1/2″",
  "cells": [
   [
    "L1",
    "8,75",
    "11"
   ],
   [
    "L3",
    "17,5",
    "31"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по RFS, Installation Instruction 10000009476-02 (LCF/RCF 12-50, OMNI FIT C02, вся серия C02): внутренний проводник выступает 8,5–9,0, оболочка снята 17–18 мм, внешний проводник обрезан заподлицо с диэлектриком — https://www.rfstechnologies.com/images/connectors/10000009476-02.pdf — аналог для N-male на 1/2″",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по SPINNER, Installation Instructions M32130 (MultiFit LF 1/2″): внутренний проводник 11 ±1 от торца внешнего, внешний проводник 20; кабель режется заподлицо — https://gsm-repiteri.ru/files/2021/12/razyom-spinner-multifit-lf-1-2-50.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2021/12/razyom-spinner-multifit-lf-1-2-50.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-spinner-multifit-lf-1-2-50"
 },
 {
  "task": 1,
  "article": "TRB-1250-NM",
  "cable": "nu-Trac TRC-1250",
  "cells": [
   [
    "L1",
    "8,75",
    ""
   ],
   [
    "L3",
    "17,5",
    "10,3"
   ],
   [
    "Источник данных",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по RFS, Installation Instruction 10000009476-02 (LCF/RCF 12-50, OMNI FIT C02, вся серия C02): внутренний проводник выступает 8,5–9,0, оболочка снята 17–18 мм, внешний проводник обрезан заподлицо с диэлектриком — https://www.rfstechnologies.com/images/connectors/10000009476-02.pdf — аналог для N-male на nu-Trac TRC-1250",
    "gsm-repiteri.ru, 12.10.2026; L1–L3: по Connector attachment procedure for nu-TRAC-1250 (3190-2309, 3190-2310; с карточки TRB-1250-NM gsm-repiteri): оболочка 13/32″ = 10,3, кабель режется заподлицо — https://gsm-repiteri.ru/files/2025/04/razyom-trb-1250-nm-instrukciya-po-ustanovke.pdf"
   ]
  ],
  "doc": "https://gsm-repiteri.ru/files/2025/04/razyom-trb-1250-nm-instrukciya-po-ustanovke.pdf",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyom-trb-1250-nm"
 },
 {
  "task": 1,
  "article": "SMA-Female RG405 18GHz",
  "cable": "RG-405",
  "cells": [
   [
    "L1",
    "3,1",
    "3"
   ],
   [
    "L2",
    "3,6",
    ""
   ],
   [
    "L3",
    "",
    "8,3"
   ],
   [
    "Источник данных",
    "L1–L3: по Coax Connectors Ltd 30-014-D3-HA (SMA solder plug, RG-405 .085): жила a 3,1, диэлектрик b 0,5, c 3,6 — https://coax-connectors.com/wp-content/uploads/2021/08/30-014-D3-HA-REV-A00-1.pdf — аналог для SMA-female на RG-405",
    "L1–L3: по чертежу АМЭЛ SMA-KB2, разделка кабеля UT086, RG405 в оболочке (with jacket): жила 3, оболочка 8,3 — https://www.amel.ru/upload/iblock/fed/fed9e865e7cd66af50d8f726d486bd9c.pdf"
   ]
  ],
  "doc": "https://www.amel.ru/upload/iblock/fed/fed9e865e7cd66af50d8f726d486bd9c.pdf",
  "page": "https://www.amel.ru/products/coaxial_connectors/rozetki-kabelnye-pryamye-3156/sma-kb2/"
 },
 {
  "task": 1,
  "article": "SMP-KW2.5D",
  "cable": "RG-316D",
  "cells": [
   [
    "L1",
    "2,7",
    "1"
   ],
   [
    "L2",
    "3,4",
    "3"
   ],
   [
    "L3",
    "8,6",
    "7,5"
   ],
   [
    "Источник данных",
    "",
    "L1–L3: по чертежу АМЭЛ SMP-KW2.5D, разделка кабеля RG316D: 1 / 3 / 7,5 — https://www.amel.ru/upload/iblock/00b/SMP-KW2.5D.pdf"
   ]
  ],
  "doc": "https://www.amel.ru/upload/iblock/00b/SMP-KW2.5D.pdf",
  "page": "https://www.amel.ru/products/coaxial_connectors/rozetki-kabelnye-uglovye-5391/smp-kw2-5d/"
 },
 {
  "task": 1,
  "article": "SMP-KW3",
  "cable": "RG-174",
  "cells": [
   [
    "L1",
    "2,7",
    "1"
   ],
   [
    "L2",
    "3,4",
    "3"
   ],
   [
    "L3",
    "8,6",
    "7,5"
   ],
   [
    "Источник данных",
    "",
    "L1–L3: по чертежу АМЭЛ SMP-KW3, разделка кабеля RG174A/U, RG188A/U, RG316/U: 1 / 3 / 7,5 — https://www.amel.ru/upload/iblock/64b/SMP-KW3.pdf"
   ]
  ],
  "doc": "https://www.amel.ru/upload/iblock/64b/SMP-KW3.pdf",
  "page": "https://www.amel.ru/products/coaxial_connectors/rozetki-kabelnye-uglovye-5391/smp-kw3/"
 },
 {
  "task": 1,
  "article": "SMP-KW3",
  "cable": "RG-316",
  "cells": [
   [
    "L1",
    "2,7",
    "1"
   ],
   [
    "L2",
    "3,4",
    "3"
   ],
   [
    "L3",
    "8,6",
    "7,5"
   ],
   [
    "Источник данных",
    "строка для RG-316 добавлена 15.10.2026 по совместимости (копия строки «SMP-KW3» / RG-174, D1–D3 из СПР.КАБ RG-316): amel.ru SMP-KW3 (код 134A-7002): «RG174A/U, RG188A/U, RG316/U» — https://amel.ru/products/coaxial_connectors/rozetki-kabelnye-uglovye-5391/smp-kw3/",
    "строка для RG-316 добавлена 15.10.2026 по совместимости (копия строки «SMP-KW3» / RG-174, D1–D3 из СПР.КАБ RG-316): amel.ru SMP-KW3 (код 134A-7002): «RG174A/U, RG188A/U, RG316/U» — https://amel.ru/products/coaxial_connectors/rozetki-kabelnye-uglovye-5391/smp-kw3/; L1–L3: по чертежу АМЭЛ SMP-KW3, разделка кабеля RG174A/U, RG188A/U, RG316/U: 1 / 3 / 7,5 — https://www.amel.ru/upload/iblock/64b/SMP-KW3.pdf"
   ]
  ],
  "doc": "https://www.amel.ru/upload/iblock/64b/SMP-KW3.pdf",
  "page": "https://www.amel.ru/products/coaxial_connectors/rozetki-kabelnye-uglovye-5391/smp-kw3/"
 },
 {
  "task": 1,
  "article": "SMA-ВКП-1,5.21-2-019-2.М",
  "cable": "РК50-1,5-21",
  "cells": [
   [
    "L1",
    "2,5",
    "3"
   ],
   [
    "L2",
    "4,2",
    ""
   ],
   [
    "L3",
    "10,7",
    "10"
   ],
   [
    "Источник данных",
    "L1–L3: по Amphenol RF, Assembly Instructions C53 (SMA), 901-9916 (RG-174/179/187/188/316) — https://cdn.components.ru/media/PDF/8013478.pdf — тот же ряд и кабель, изготовитель другой",
    "L1–L3: по листу 3 «Разделка кабеля» АМЭЛ SMA-ВКП-1,5.21-2-019 и др. (ТУ 27.33.13.120-005-38970729-2019): жила 3, оплётка 7 — https://www.amel.ru/upload/iblock/cf9/cf90baf3890b72d0495424b650f13ab4.pdf"
   ]
  ],
  "doc": "https://www.amel.ru/upload/iblock/cf9/cf90baf3890b72d0495424b650f13ab4.pdf",
  "page": "https://www.amel.ru/products/coaxial_connectors/vilki-kabelnye-pryamye-3876/sma_vkp_1_5_21_2_019/"
 },
 {
  "task": 1,
  "article": "MMCX-JW3",
  "cable": "RG-174",
  "cells": [
   [
    "L1",
    "1,3",
    "1"
   ],
   [
    "L2",
    "2,9",
    "3"
   ],
   [
    "L3",
    "7,2",
    "7,5"
   ],
   [
    "Источник данных",
    "L1–L3: по Amphenol RF, Assembly Instructions C72 (MMCX), 908-43300 angle plug (RG-174/188/316): d 4,32, e 5,97, f 7,24 — https://assets.rs-online.com/v1698848859/Datasheets/90841f9b17d9dc8be1ec91ecb758408f.pdf — тот же ряд и кабель, изготовитель другой; строка для RG-174 добавлена 15.10.2026 по совместимости (копия строки «MMCX-JW3» / RG-316, D1–D3 из СПР.КАБ RG-174): amel.ru MMCX-JW3 (код 113A-7502): «Применяемый кабель RG-174/U, RG-188/U, RG-316/U» — https://amel.ru/products/coaxial_connectors/vilki-kabelnye-uglovye-3215/mmcx-jw3/",
    "строка для RG-174 добавлена 15.10.2026 по совместимости (копия строки «MMCX-JW3» / RG-316, D1–D3 из СПР.КАБ RG-174): amel.ru MMCX-JW3 (код 113A-7502): «Применяемый кабель RG-174/U, RG-188/U, RG-316/U» — https://amel.ru/products/coaxial_connectors/vilki-kabelnye-uglovye-3215/mmcx-jw3/; L1–L3: по чертежу АМЭЛ MMCX-JW3, разделка кабеля RG174A/U, RG188A/U, RG316/U: 1 / 3 / 7,5 — https://www.amel.ru/upload/iblock/5aa/MMCX-JW3.pdf"
   ]
  ],
  "doc": "https://www.amel.ru/upload/iblock/5aa/MMCX-JW3.pdf",
  "page": "https://www.amel.ru/products/coaxial_connectors/vilki-kabelnye-uglovye-3215/mmcx-jw3/"
 },
 {
  "task": 1,
  "article": "MMCX-JW3",
  "cable": "RG-316",
  "cells": [
   [
    "L1",
    "1,3",
    "1"
   ],
   [
    "L2",
    "2,9",
    "3"
   ],
   [
    "L3",
    "7,2",
    "7,5"
   ],
   [
    "Источник данных",
    "L1–L3: по Amphenol RF, Assembly Instructions C72 (MMCX), 908-43300 angle plug (RG-174/188/316): d 4,32, e 5,97, f 7,24 — https://assets.rs-online.com/v1698848859/Datasheets/90841f9b17d9dc8be1ec91ecb758408f.pdf — тот же ряд и кабель, изготовитель другой",
    "L1–L3: по чертежу АМЭЛ MMCX-JW3, разделка кабеля RG174A/U, RG188A/U, RG316/U: 1 / 3 / 7,5 — https://www.amel.ru/upload/iblock/5aa/MMCX-JW3.pdf"
   ]
  ],
  "doc": "https://www.amel.ru/upload/iblock/5aa/MMCX-JW3.pdf",
  "page": "https://www.amel.ru/products/coaxial_connectors/vilki-kabelnye-uglovye-3215/mmcx-jw3/"
 },
 {
  "task": 1,
  "article": "SMA-ВКУ-1,5.214-2-355-1.М",
  "cable": "RG-316D",
  "cells": [
   [
    "L1",
    "2,6",
    "1,2"
   ],
   [
    "L2",
    "5,5",
    ""
   ],
   [
    "L3",
    "12",
    "16,2"
   ],
   [
    "Источник данных",
    "L1–L3: по Amphenol RF, Assembly Instructions C53 (SMA), 901-9872 angle plug (RG-174/179/187/188/316) — https://cdn.components.ru/media/PDF/8013478.pdf — тот же ряд и кабель, изготовитель другой",
    "L1–L3: по листу 3 «Разделка кабеля» АМЭЛ SMA-ВКУ-1,5.21-2-294 / 1,5.214-2-355 (ТУ 27.33.13.120-005-38970729-2019): жила 1,2, оплётка 15 — https://www.amel.ru/upload/iblock/0a5/0a50c25b0f75cfa37b495b521e904b30.pdf"
   ]
  ],
  "doc": "https://www.amel.ru/upload/iblock/0a5/0a50c25b0f75cfa37b495b521e904b30.pdf",
  "page": "https://www.amel.ru/products/coaxial_connectors/vilki-kabelnye-uglovye-3877/sma_vku_1_5_214_2_355/"
 },
 {
  "task": 1,
  "article": "TNC-JW3Y",
  "cable": "RG-174",
  "cells": [
   [
    "L1",
    "3,2",
    "1,5"
   ],
   [
    "L2",
    "8,3",
    "7,5"
   ],
   [
    "L3",
    "14,7",
    "12,5"
   ],
   [
    "Источник данных",
    "L1–L3: по Amphenol RF, Assembly Instructions C36 (TNC), 31-2381 angle plug (RG-55/142/223) — https://cdn.components.ru/media/PDF/8022964.pdf — аналог для TNC-male R/A на RG-174; строка для RG-174 добавлена 15.10.2026 по совместимости (копия строки «TNC-JW3Y» / RG-316, D1–D3 из СПР.КАБ RG-174): amel.ru TNC-JW3Y (код 263B-7501): «Применяемый кабель RG174A/U, RG188A/U, RG316/U» — https://amel.ru/products/coaxial_connectors/vilki-kabelnye-uglovye-5510/tnc-jw3y/",
    "строка для RG-174 добавлена 15.10.2026 по совместимости (копия строки «TNC-JW3Y» / RG-316, D1–D3 из СПР.КАБ RG-174): amel.ru TNC-JW3Y (код 263B-7501): «Применяемый кабель RG174A/U, RG188A/U, RG316/U» — https://amel.ru/products/coaxial_connectors/vilki-kabelnye-uglovye-5510/tnc-jw3y/; L1–L3: по чертежу АМЭЛ TNC-JW3Y, разделка кабеля RG174A/U, RG188A/U, RG316/U: 1,5 / 7,5 / 12,5 — https://www.amel.ru/upload/iblock/054/TNC-JW3Y.pdf"
   ]
  ],
  "doc": "https://www.amel.ru/upload/iblock/054/TNC-JW3Y.pdf",
  "page": "https://www.amel.ru/products/coaxial_connectors/vilki-kabelnye-uglovye-5510/tnc-jw3y/"
 },
 {
  "task": 1,
  "article": "TNC-JW3Y",
  "cable": "RG-316",
  "cells": [
   [
    "L1",
    "3,2",
    "1,5"
   ],
   [
    "L2",
    "8,3",
    "7,5"
   ],
   [
    "L3",
    "14,7",
    "12,5"
   ],
   [
    "Источник данных",
    "L1–L3: по Amphenol RF, Assembly Instructions C36 (TNC), 31-2381 angle plug (RG-55/142/223) — https://cdn.components.ru/media/PDF/8022964.pdf — аналог для TNC-male R/A на RG-316",
    "L1–L3: по чертежу АМЭЛ TNC-JW3Y, разделка кабеля RG174A/U, RG188A/U, RG316/U: 1,5 / 7,5 / 12,5 — https://www.amel.ru/upload/iblock/054/TNC-JW3Y.pdf"
   ]
  ],
  "doc": "https://www.amel.ru/upload/iblock/054/TNC-JW3Y.pdf",
  "page": "https://www.amel.ru/products/coaxial_connectors/vilki-kabelnye-uglovye-5510/tnc-jw3y/"
 },
 {
  "task": 1,
  "article": "SMB-KW3",
  "cable": "RG-174",
  "cells": [
   [
    "L1",
    "2,7",
    "1"
   ],
   [
    "L2",
    "3,4",
    "5"
   ],
   [
    "L3",
    "8,6",
    "10,5"
   ],
   [
    "Источник данных",
    "L1–L3: по Amphenol RF, Assembly Instructions C77 (MCX), 919-104P-51AX angle plug (RG-174/188/316) — https://atta.szlcsc.com/upload/public/pdf/source/231023/342-ff58958e80c7a32fa4e602c5a86989e5.pdf — аналог для SMB-female R/A на RG-174",
    "L1–L3: по чертежу АМЭЛ SMB-KW3, разделка кабеля RG174A/U, RG188A/U, RG316/U: 1 / 5 / 10,5 — https://www.amel.ru/upload/iblock/da2/SMB-KW3.pdf"
   ]
  ],
  "doc": "https://www.amel.ru/upload/iblock/da2/SMB-KW3.pdf",
  "page": "https://www.amel.ru/products/coaxial_connectors/vilki-kabelnye-uglovye/smb-kw3/"
 },
 {
  "task": 1,
  "article": "SMB-KW3",
  "cable": "RG-316",
  "cells": [
   [
    "L1",
    "2,7",
    "1"
   ],
   [
    "L2",
    "3,4",
    "5"
   ],
   [
    "L3",
    "8,6",
    "10,5"
   ],
   [
    "Источник данных",
    "L1–L3: по Amphenol RF, Assembly Instructions C77 (MCX), 919-104P-51AX angle plug (RG-174/188/316) — https://atta.szlcsc.com/upload/public/pdf/source/231023/342-ff58958e80c7a32fa4e602c5a86989e5.pdf — аналог для SMB-female R/A на RG-316; строка для RG-316 добавлена 15.10.2026 по совместимости (копия строки «SMB-KW3» / RG-174, D1–D3 из СПР.КАБ RG-316): amel.ru SMB-KW3 (код 154A-7502): «Применяемый кабель RG-174/U, RG-188/U, RG-316/U» — https://amel.ru/products/coaxial_connectors/vilki-kabelnye-uglovye/smb-kw3/",
    "строка для RG-316 добавлена 15.10.2026 по совместимости (копия строки «SMB-KW3» / RG-174, D1–D3 из СПР.КАБ RG-316): amel.ru SMB-KW3 (код 154A-7502): «Применяемый кабель RG-174/U, RG-188/U, RG-316/U» — https://amel.ru/products/coaxial_connectors/vilki-kabelnye-uglovye/smb-kw3/; L1–L3: по чертежу АМЭЛ SMB-KW3, разделка кабеля RG174A/U, RG188A/U, RG316/U: 1 / 5 / 10,5 — https://www.amel.ru/upload/iblock/da2/SMB-KW3.pdf"
   ]
  ],
  "doc": "https://www.amel.ru/upload/iblock/da2/SMB-KW3.pdf",
  "page": "https://www.amel.ru/products/coaxial_connectors/vilki-kabelnye-uglovye/smb-kw3/"
 },
 {
  "task": 1,
  "article": "19K107-271L5",
  "cable": "RG-405",
  "cells": [
   [
    "L1",
    "3,1",
    "1,5"
   ],
   [
    "L2",
    "3,6",
    ""
   ],
   [
    "L3",
    "",
    "4,3"
   ],
   [
    "Источник данных",
    "",
    "L1–L3: по Rosenberger, Montageanleitung MA_19E13 (SMP, 19K107): жила 1,5 ±0,2, FEP-оболочка 4,3 — https://static.chipdip.ru/lib/842/DOC036842355.pdf"
   ]
  ],
  "doc": "https://static.chipdip.ru/lib/842/DOC036842355.pdf",
  "page": "https://www.chipdip.ru/product/19k107-271l5-rosenberger-8039533908"
 },
 {
  "task": 5,
  "article": "QMA-KPD7",
  "cable": "5D-FB",
  "cells": [
   [
    "L1",
    "2,4",
    ""
   ],
   [
    "L2",
    "5,54",
    ""
   ],
   [
    "L3",
    "15,7",
    ""
   ],
   [
    "Источник данных",
    "",
    "amel.ru, 17.10.2026: QMA-KPD7 — розетка приборная прямая, «Применяемый кабель: -» (https://www.amel.ru/products/coaxial_connectors/rozetki-pribornye/qma-kpd7/ , чертёж https://www.amel.ru/upload/iblock/417/QMA-KPD7.pdf); кабель не применяется, разделки нет — L1–L3 (2,4 / 5,54 / 15,7 с аналога QMA) сняты 17.10.2026"
   ]
  ],
  "doc": "https://www.amel.ru/upload/iblock/417/QMA-KPD7.pdf",
  "page": "https://www.amel.ru/products/coaxial_connectors/rozetki-pribornye/qma-kpd7/"
 },
 {
  "task": 4,
  "article": "U-113/8D",
  "cable": "8D-FB CU",
  "cells": [
   [
    "Провод",
    "8D-FB CU",
    "8D-FB Cu"
   ],
   [
    "coaxWire",
    "8D-FB CU",
    "8D-FB Cu"
   ]
  ],
  "doc": "",
  "page": "https://gsm-repiteri.ru/prodazha/razyomi/razyem-b-113-8d-n-male-nakruchivaushiisya-na-kabel-8d"
 }
]
''')

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
LOG = []


def key(article, cable):
    return str(article or "").strip().casefold(), cable_key(cable)


with factory() as uow:
    coax = uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100_000)
pairs = {}
for record in coax:
    pairs.setdefault(key(record.fields.get("coaxArticle"), record.fields.get("coaxWire")), []).append(record)
print("строк БД.КОАКС:", len(coax))

for item in PAYLOAD:
    label = f"[{item['task']}] «{item['article']}» × «{item['cable']}»"
    found = pairs.get(key(item["article"], item["cable"]), [])
    if len(found) != 1:
        print(f"   отказ: {label} — строк найдено {len(found)}")
        LOG.append([label, "отказ"])
        continue
    record = found[0]
    edits, imports = [], []
    for field, old, new in item["cells"]:
        have = str(record.fields.get(field) or "").strip()
        if have == new:
            continue
        if field == "Источник данных" and field not in record.fields and not old:
            imports.append((field, new))  # the row never had the column: `_edit_in` refuses it, the load adds it
        else:
            edits.append(FieldEdit(field, old, new))
    if not edits and not imports:
        print(f"   уже так: {label}")
        LOG.append([label, "уже так"])
        continue
    if imports:
        fields = {"coaxArticle": record.fields.get("coaxArticle", ""), "coaxWire": record.fields.get("coaxWire", ""),
                  **{field: new for field, new in imports}}
        report = import_rows("coax", factory(), [ImportRowIn(fields=fields, old={field: "" for field, _ in imports})],
                             ACTOR, now, dry_run=True, source="erp").model_dump()
        if report["created"] or report["refused"] or len(report["updated"]) != 1:
            print(f"   отказ: {label} — загрузка «Источника данных»: {report['created'] or report['refused'] or report['updated']}")
            LOG.append([label, "отказ"])
            continue
    if edits:
        with factory() as uow:
            try:
                before, after = _edit_in(record.id, tuple(edits), now, uow)
            except (StaleRecordError, ReferenceValidationError, LookupError) as error:
                print(f"   отказ: {label} ({error})")
                LOG.append([label, "отказ"])
                continue
            _audit("references.tech_record_edited", ACTOR, now, {"record_id": str(before.id), "tab": before.tab.value,
                   "fields": [edit.field for edit in edits], "reason": REASON}, uow)
            if not dry:
                uow.commit()
    if imports and not dry:
        import_rows("coax", factory(), [ImportRowIn(fields=fields, old={field: "" for field, _ in imports})],
                    ACTOR, now, dry_run=False, source="erp")
    shown = "; ".join([f"{e.field} «{e.old[:40]}» → «{e.value[:40]}»" for e in edits] + [f"{f} (загрузкой) → «{v[:40]}»" for f, v in imports])
    print(f"   {'проверено' if dry else 'записано'}: {label} {shown}")
    LOG.append([label, "проверено" if dry else "записано"])

summary = {}
for _, status in LOG:
    summary[status] = summary.get(status, 0) + 1
print("ИТОГО", json.dumps(summary, ensure_ascii=False), "(проверка, ничего не записано)" if dry else "(записано)")

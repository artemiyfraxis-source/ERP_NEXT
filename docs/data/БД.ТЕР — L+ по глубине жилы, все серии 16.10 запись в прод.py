"""Запись в прод 16.10.2026 — L+ / L− 81 терминала БД.ТЕР по глубине конца жилы (владелец 16.10: «ты все решаешь сам
и закрываешь»; «"примерно" не надо, будем на деле проверять»).

Смысл (PR #476): L+ — глубина конца жилы от торца корпуса со стороны провода, L− — от конца жилы до стыковочного торца,
L+ + L− = длина корпуса. Пишутся:
- 43 строки, где в L+ стояла длина контакта (L+ ≥ 0,7 корпуса): JST ZH/PH/PAD/EH/SH/SHL/GH/GVH, Hirose DF13/DF14/DF20,
  Molex PicoBlade 51021, Zero-Hachi, Milli-Grid, Micro-Lock Plus 1.25, Micro-Latch, OneBlade, Pico-Lock 1.0/1.5,
  Pico-SPOX, Connfly DS1066;
- 35 строк с пустым L+: Hirose DF1B (12), Molex Nano-Fit (20), KLS1-2.50 (1), PicoBlade 500588000/500798000 (2);
- 3 явных выброса среди «L+ в пределах корпуса»: Connfly T-DS1070-SC600, T-DS1071-SC600/SC800 (L+ 5 при чертеже ~7).
Как получено каждое значение (чертёж контакта или правило L+ = 0,5 корпуса) — в журнале
docs/data/«БД.ТЕР — L+ по глубине жилы, все серии 16.10.csv» и в .md рядом. В базу идут только числа; «Источник L-»
получает лишь ссылку на чертёж (где ячейка была пустой и чертежа контакта нет — остаётся пустой).

Путь приложения: `import_rows("ter")` по натуральному ключу «terArticle»; у каждой строки `old` — значения прода на
16.10 (terLPlus, terLMinus, «Источник L-»): ячейка, изменённая после снимка, придёт отказом и не перепишется; строк не
создаётся.

Порядок (live-data protocol, prod-reference-write):
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → всё «отказ» (уже записано)"""
import json
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.domain import TechOperationTab
from app.modules.references.importers import import_rows
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="L+ по глубине жилы, все серии 16.10.2026", department="System")
FIELDS = ("terLPlus", "terLMinus", "L+", "L-", "Источник L-")
ROWS = json.loads(r'''
[
 {
  "fields": {
   "terArticle": "SZH-002T-P0.5",
   "terLPlus": "2,5",
   "terLMinus": "2,3",
   "Источник L-": "чертёж контакта и корпуса: JST eZH (Contact, Housing ZHR), https://www.jst-mfg.com/product/pdf/eng/eZH.pdf"
  },
  "old": {
   "terArticle": "SZH-002T-P0.5",
   "terLPlus": "5",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "SZH-003T-P0.5",
   "terLPlus": "2,5",
   "terLMinus": "2,3",
   "Источник L-": "чертёж контакта и корпуса: JST eZH (Contact, Housing ZHR), https://www.jst-mfg.com/product/pdf/eng/eZH.pdf"
  },
  "old": {
   "terArticle": "SZH-003T-P0.5",
   "terLPlus": "5",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "SPH-002T-P0.5S",
   "terLPlus": "3,5",
   "terLMinus": "3,35",
   "Источник L-": "чертёж контакта и корпуса: JST ePH (Contact, Housing PHR), https://www.jst-mfg.com/product/pdf/eng/ePH.pdf"
  },
  "old": {
   "terArticle": "SPH-002T-P0.5S",
   "terLPlus": "6",
   "terLMinus": "0,85",
   "Источник L-": "оценка по чертежу корпуса: L− = PHR 6,85 мм − L+ 6 мм; JST ePH, Housing, https://www.jst-mfg.com/product/pdf/eng/ePH.pdf; в PADP — тоже 6,85 (JST ePAD)"
  }
 },
 {
  "fields": {
   "terArticle": "SPH-002T-P0.5L",
   "terLPlus": "3,5",
   "terLMinus": "3,35",
   "Источник L-": "чертёж контакта и корпуса: JST ePH (Contact, Housing PHR), https://www.jst-mfg.com/product/pdf/eng/ePH.pdf"
  },
  "old": {
   "terArticle": "SPH-002T-P0.5L",
   "terLPlus": "5",
   "terLMinus": "1,85",
   "Источник L-": "оценка по чертежу корпуса: L− = PHR 6,85 мм − L+ 5 мм; JST ePH, Housing, https://www.jst-mfg.com/product/pdf/eng/ePH.pdf; в PADP — тоже 6,85 (JST ePAD)"
  }
 },
 {
  "fields": {
   "terArticle": "SPH-004T-P0.5S",
   "terLPlus": "3,5",
   "terLMinus": "3,35",
   "Источник L-": "чертёж контакта и корпуса: JST ePH (Contact, Housing PHR), https://www.jst-mfg.com/product/pdf/eng/ePH.pdf"
  },
  "old": {
   "terArticle": "SPH-004T-P0.5S",
   "terLPlus": "5",
   "terLMinus": "1,85",
   "Источник L-": "оценка по чертежу корпуса: L− = PHR 6,85 мм − L+ 5 мм; JST ePH, Housing, https://www.jst-mfg.com/product/pdf/eng/ePH.pdf; в PADP — тоже 6,85 (JST ePAD)"
  }
 },
 {
  "fields": {
   "terArticle": "SPH-001T-P0.5L",
   "terLPlus": "3,5",
   "terLMinus": "3,35",
   "Источник L-": "чертёж контакта: JST ePH (Contact), https://www.jst-mfg.com/product/pdf/eng/ePH.pdf; чертёж корпуса: JST ePAD (Housing PADP), https://www.jst-mfg.com/product/pdf/eng/ePAD.pdf"
  },
  "old": {
   "terArticle": "SPH-001T-P0.5L",
   "terLPlus": "5",
   "terLMinus": "1,85",
   "Источник L-": "оценка по чертежу корпуса: L− = PADP 6,85 мм − L+ 5 мм; JST ePAD, Housing, https://www.jst-mfg.com/product/pdf/eng/ePAD.pdf"
  }
 },
 {
  "fields": {
   "terArticle": "SEH-001T-P0.6",
   "terLPlus": "3,75",
   "terLMinus": "3,35",
   "Источник L-": "чертёж контакта и корпуса: JST eEH (Contact, Housing EHR), https://www.jst-mfg.com/product/pdf/eng/eEH.pdf"
  },
  "old": {
   "terArticle": "SEH-001T-P0.6",
   "terLPlus": "5",
   "terLMinus": "2,1",
   "Источник L-": "оценка по чертежу корпуса: L− = EHR 7,1 мм − L+ 5 мм; JST eEH, Housing (6,5 + 0,6), https://www.jst-mfg.com/product/pdf/eng/eEH.pdf"
  }
 },
 {
  "fields": {
   "terArticle": "SEH-002T-P0.6",
   "terLPlus": "3,75",
   "terLMinus": "3,35",
   "Источник L-": "чертёж контакта и корпуса: JST eEH (Contact, Housing EHR), https://www.jst-mfg.com/product/pdf/eng/eEH.pdf"
  },
  "old": {
   "terArticle": "SEH-002T-P0.6",
   "terLPlus": "5",
   "terLMinus": "2,1",
   "Источник L-": "оценка по чертежу корпуса: L− = EHR 7,1 мм − L+ 5 мм; JST eEH, Housing (6,5 + 0,6), https://www.jst-mfg.com/product/pdf/eng/eEH.pdf"
  }
 },
 {
  "fields": {
   "terArticle": "SSH-003T-P0.2",
   "terLPlus": "2,6",
   "terLMinus": "2,4",
   "Источник L-": "чертёж контакта и корпуса: JST eSH (Contact, Housing SHR), https://www.jst-mfg.com/product/pdf/eng/eSH.pdf"
  },
  "old": {
   "terArticle": "SSH-003T-P0.2",
   "terLPlus": "4",
   "terLMinus": "1",
   "Источник L-": "оценка по чертежу корпуса: L− = SHR 5 мм − L+ 4 мм; JST eSH, Housing, https://www.jst-mfg.com/product/pdf/eng/eSH.pdf; SHD, SHDP, APSH, LBT — чертежи не смотрели"
  }
 },
 {
  "fields": {
   "terArticle": "SSH-003T-P0.2-H",
   "terLPlus": "2,6",
   "terLMinus": "2,4",
   "Источник L-": "чертёж контакта и корпуса: JST eSH (Contact, Housing SHR), https://www.jst-mfg.com/product/pdf/eng/eSH.pdf"
  },
  "old": {
   "terArticle": "SSH-003T-P0.2-H",
   "terLPlus": "4",
   "terLMinus": "1",
   "Источник L-": "оценка по чертежу корпуса: L− = SHR 5 мм − L+ 4 мм; JST eSH, Housing, https://www.jst-mfg.com/product/pdf/eng/eSH.pdf; SHD, SHDP, APSH, LBT — чертежи не смотрели"
  }
 },
 {
  "fields": {
   "terArticle": "SSH-003GA-P0.2",
   "terLPlus": "2,6",
   "terLMinus": "2,4",
   "Источник L-": "чертёж контакта и корпуса: JST eSH (Contact, Housing SHR), https://www.jst-mfg.com/product/pdf/eng/eSH.pdf"
  },
  "old": {
   "terArticle": "SSH-003GA-P0.2",
   "terLPlus": "4",
   "terLMinus": "1",
   "Источник L-": "оценка по чертежу корпуса: L− = SHR 5 мм − L+ 4 мм; JST eSH, Housing, https://www.jst-mfg.com/product/pdf/eng/eSH.pdf; SHD, SHDP, APSH, LBT — чертежи не смотрели"
  }
 },
 {
  "fields": {
   "terArticle": "SSHL-003T-P0.2",
   "terLPlus": "2,45",
   "terLMinus": "3,25",
   "Источник L-": "чертёж контакта и корпуса: JST eSHL (Contact, Socket housing SHLP), https://www.jst-mfg.com/product/pdf/eng/eSHL.pdf"
  },
  "old": {
   "terArticle": "SSHL-003T-P0.2",
   "terLPlus": "5",
   "terLMinus": "0,7",
   "Источник L-": "оценка по чертежу корпуса: L− = SHLP 5,7 мм − L+ 5 мм; JST eSHL, Socket housing, https://www.jst-mfg.com/product/pdf/eng/eSHL.pdf; GHR — тоже 5,7 (JST eGH), NSHR — 6,2 (JST eNSH)"
  }
 },
 {
  "fields": {
   "terArticle": "SSHL-002T-P0.2",
   "terLPlus": "2,5",
   "terLMinus": "3,2",
   "Источник L-": "чертёж контакта и корпуса: JST eGH (Contact, Housing GHR), https://www.jst-mfg.com/product/pdf/eng/eGH.pdf"
  },
  "old": {
   "terArticle": "SSHL-002T-P0.2",
   "terLPlus": "5",
   "terLMinus": "10",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "SGVH-002T-P0.2",
   "terLPlus": "2,5",
   "terLMinus": "3,2",
   "Источник L-": "чертёж контакта и корпуса: JST eGVH (Contact, Housing GVHR), https://www.jst-mfg.com/product/pdf/eng/eGVH.pdf"
  },
  "old": {
   "terArticle": "SGVH-002T-P0.2",
   "terLPlus": "5",
   "terLMinus": "0,7",
   "Источник L-": "оценка по чертежу корпуса: L− = GVHR 5,7 мм − L+ 5 мм; JST eGVH, Housing (Normal type), https://www.jst-mfg.com/product/pdf/eng/eGVH.pdf"
  }
 },
 {
  "fields": {
   "terArticle": "DF13-2630SCF",
   "terLPlus": "1,8",
   "terLMinus": "2,2",
   "Источник L-": "чертёж контакта и корпуса: Hirose DF13, каталог (Crimping Contact B249, Single Row Socket B244), https://datasheet.lcsc.com/datasheet/pdf/64333dc9655d4e31b6b072799d4297c9.pdf?productCode=C88348"
  },
  "old": {
   "terArticle": "DF13-2630SCF",
   "terLPlus": "5",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "DF13-2630SCFA",
   "terLPlus": "1,8",
   "terLMinus": "2,2",
   "Источник L-": "чертёж контакта и корпуса: Hirose DF13, каталог (Crimping Contact B249, Single Row Socket B244), https://datasheet.lcsc.com/datasheet/pdf/64333dc9655d4e31b6b072799d4297c9.pdf?productCode=C88348"
  },
  "old": {
   "terArticle": "DF13-2630SCFA",
   "terLPlus": "5",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "DF13-3032SCF",
   "terLPlus": "1,8",
   "terLMinus": "2,2",
   "Источник L-": "чертёж контакта и корпуса: Hirose DF13, каталог (Crimping Contact B249, Single Row Socket B244), https://datasheet.lcsc.com/datasheet/pdf/64333dc9655d4e31b6b072799d4297c9.pdf?productCode=C88348"
  },
  "old": {
   "terArticle": "DF13-3032SCF",
   "terLPlus": "5",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "DF13-3032SCFA",
   "terLPlus": "1,8",
   "terLMinus": "2,2",
   "Источник L-": "чертёж контакта и корпуса: Hirose DF13, каталог (Crimping Contact B249, Single Row Socket B244), https://datasheet.lcsc.com/datasheet/pdf/64333dc9655d4e31b6b072799d4297c9.pdf?productCode=C88348"
  },
  "old": {
   "terArticle": "DF13-3032SCFA",
   "terLPlus": "5",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "DF20F-2830SCFA",
   "terLPlus": "2,3",
   "terLMinus": "1,8",
   "Источник L-": "чертёж контакта и корпуса: Hirose DF20, каталог 01.08.2023 (Crimp Contacts, Double Row Socket), https://datasheet.lcsc.com/datasheet/pdf/58c284d870df321ba7391c5da3bf96c1.pdf?productCode=C5378701"
  },
  "old": {
   "terArticle": "DF20F-2830SCFA",
   "terLPlus": "4",
   "terLMinus": "0,1",
   "Источник L-": "оценка по чертежу корпуса: L− = DF20A-*DS-1C 4,1 мм − L+ 4 мм; Hirose DF20, каталог 01.08.2023, Double Row Socket, https://datasheet.lcsc.com/datasheet/pdf/58c284d870df321ba7391c5da3bf96c1.pdf?productCode=C5378701"
  }
 },
 {
  "fields": {
   "terArticle": "DF20F-3032SCFA",
   "terLPlus": "2,3",
   "terLMinus": "1,8",
   "Источник L-": "чертёж контакта и корпуса: Hirose DF20, каталог 01.08.2023 (Crimp Contacts, Double Row Socket), https://datasheet.lcsc.com/datasheet/pdf/58c284d870df321ba7391c5da3bf96c1.pdf?productCode=C5378701"
  },
  "old": {
   "terArticle": "DF20F-3032SCFA",
   "terLPlus": "4",
   "terLMinus": "0,1",
   "Источник L-": "оценка по чертежу корпуса: L− = DF20A-*DS-1C 4,1 мм − L+ 4 мм; Hirose DF20, каталог 01.08.2023, Double Row Socket, https://datasheet.lcsc.com/datasheet/pdf/58c284d870df321ba7391c5da3bf96c1.pdf?productCode=C5378701"
  }
 },
 {
  "fields": {
   "terArticle": "DF1B-2022SCF",
   "terLPlus": "5,55",
   "terLMinus": "5,95",
   "Источник L-": "чертёж контакта и корпуса: Hirose DF1B, каталог 01.07.2020 (Crimping Contact for Socket, Single Row Socket), https://datasheet.lcsc.com/datasheet/pdf/ba434fb92ec248c0cb9579d7a1b767a6.pdf?productCode=C392028"
  },
  "old": {
   "terArticle": "DF1B-2022SCF",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "DF1B-2022SCFA",
   "terLPlus": "5,55",
   "terLMinus": "5,95",
   "Источник L-": "чертёж контакта и корпуса: Hirose DF1B, каталог 01.07.2020 (Crimping Contact for Socket, Single Row Socket), https://datasheet.lcsc.com/datasheet/pdf/ba434fb92ec248c0cb9579d7a1b767a6.pdf?productCode=C392028"
  },
  "old": {
   "terArticle": "DF1B-2022SCFA",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "DF1B-2428SCF",
   "terLPlus": "5,55",
   "terLMinus": "5,95",
   "Источник L-": "чертёж контакта и корпуса: Hirose DF1B, каталог 01.07.2020 (Crimping Contact for Socket, Single Row Socket), https://datasheet.lcsc.com/datasheet/pdf/ba434fb92ec248c0cb9579d7a1b767a6.pdf?productCode=C392028"
  },
  "old": {
   "terArticle": "DF1B-2428SCF",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "DF1B-2428SCFA",
   "terLPlus": "5,55",
   "terLMinus": "5,95",
   "Источник L-": "чертёж контакта и корпуса: Hirose DF1B, каталог 01.07.2020 (Crimping Contact for Socket, Single Row Socket), https://datasheet.lcsc.com/datasheet/pdf/ba434fb92ec248c0cb9579d7a1b767a6.pdf?productCode=C392028"
  },
  "old": {
   "terArticle": "DF1B-2428SCFA",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "DF1B-30SCF",
   "terLPlus": "5,55",
   "terLMinus": "5,95",
   "Источник L-": "чертёж контакта и корпуса: Hirose DF1B, каталог 01.07.2020 (Crimping Contact for Socket, Single Row Socket), https://datasheet.lcsc.com/datasheet/pdf/ba434fb92ec248c0cb9579d7a1b767a6.pdf?productCode=C392028"
  },
  "old": {
   "terArticle": "DF1B-30SCF",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "DF1B-30SCFA",
   "terLPlus": "5,55",
   "terLMinus": "5,95",
   "Источник L-": "чертёж контакта и корпуса: Hirose DF1B, каталог 01.07.2020 (Crimping Contact for Socket, Single Row Socket), https://datasheet.lcsc.com/datasheet/pdf/ba434fb92ec248c0cb9579d7a1b767a6.pdf?productCode=C392028"
  },
  "old": {
   "terArticle": "DF1B-30SCFA",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "DF1B-2022PCF",
   "terLPlus": "6,75",
   "terLMinus": "11,75",
   "Источник L-": "чертёж контакта и корпуса: Hirose DF1B, каталог 01.07.2020 (Crimping Contact for Plug, In-line Plug DF1BA), https://datasheet.lcsc.com/datasheet/pdf/ba434fb92ec248c0cb9579d7a1b767a6.pdf?productCode=C392028"
  },
  "old": {
   "terArticle": "DF1B-2022PCF",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "DF1B-2022PCFB",
   "terLPlus": "6,75",
   "terLMinus": "11,75",
   "Источник L-": "чертёж контакта и корпуса: Hirose DF1B, каталог 01.07.2020 (Crimping Contact for Plug, In-line Plug DF1BA), https://datasheet.lcsc.com/datasheet/pdf/ba434fb92ec248c0cb9579d7a1b767a6.pdf?productCode=C392028"
  },
  "old": {
   "terArticle": "DF1B-2022PCFB",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "DF1B-2428PCF",
   "terLPlus": "6,75",
   "terLMinus": "11,75",
   "Источник L-": "чертёж контакта и корпуса: Hirose DF1B, каталог 01.07.2020 (Crimping Contact for Plug, In-line Plug DF1BA), https://datasheet.lcsc.com/datasheet/pdf/ba434fb92ec248c0cb9579d7a1b767a6.pdf?productCode=C392028"
  },
  "old": {
   "terArticle": "DF1B-2428PCF",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "DF1-PD2428SCFB",
   "terLPlus": "6,75",
   "terLMinus": "11,75",
   "Источник L-": "чертёж контакта и корпуса: Hirose DF1B, каталог 01.07.2020 (Crimping Contact for Plug, In-line Plug DF1BA), https://datasheet.lcsc.com/datasheet/pdf/ba434fb92ec248c0cb9579d7a1b767a6.pdf?productCode=C392028"
  },
  "old": {
   "terArticle": "DF1-PD2428SCFB",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "DF1B-30PCF",
   "terLPlus": "6,75",
   "terLMinus": "11,75",
   "Источник L-": "чертёж контакта и корпуса: Hirose DF1B, каталог 01.07.2020 (Crimping Contact for Plug, In-line Plug DF1BA), https://datasheet.lcsc.com/datasheet/pdf/ba434fb92ec248c0cb9579d7a1b767a6.pdf?productCode=C392028"
  },
  "old": {
   "terArticle": "DF1B-30PCF",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "DF1B-30PCFB",
   "terLPlus": "6,75",
   "terLMinus": "11,75",
   "Источник L-": "чертёж контакта и корпуса: Hirose DF1B, каталог 01.07.2020 (Crimping Contact for Plug, In-line Plug DF1BA), https://datasheet.lcsc.com/datasheet/pdf/ba434fb92ec248c0cb9579d7a1b767a6.pdf?productCode=C392028"
  },
  "old": {
   "terArticle": "DF1B-30PCFB",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "T-DS1066-SC600",
   "terLPlus": "3,65",
   "terLMinus": "3,2",
   "Источник L-": "чертёж контакта и корпуса: Connfly C-DS1066-XXXXXXX-C (Female Terminal, Female Housing), https://www.connfly.com/static/upload/file/pdf/DS1066.pdf"
  },
  "old": {
   "terArticle": "T-DS1066-SC600",
   "terLPlus": "5",
   "terLMinus": "1,85",
   "Источник L-": "оценка по чертежу корпуса: L− = I-DS1066 (2,0 мм, PH) 6,85 мм − L+ 5 мм; Connfly C-DS1066-XXXXXXX-C (6,35 + 0,5), https://www.connfly.com/static/upload/file/pdf/DS1066.pdf"
  }
 },
 {
  "fields": {
   "terArticle": "T-DS1070-SC600",
   "terLPlus": "7,23",
   "terLMinus": "5,97",
   "Источник L-": "чертёж контакта и корпуса: Connfly C-DS1070-XXXXXX-D, https://www.connfly.com/static/upload/file/pdf/DS1070.pdf; Molex 26950000-SD (KK 254), разрез с контактом, https://static.chipdip.ru/lib/879/DOC011879936.pdf"
  },
  "old": {
   "terArticle": "T-DS1070-SC600",
   "terLPlus": "5",
   "terLMinus": "8,2",
   "Источник L-": "оценка по чертежу корпуса: L− = I-DS1070 (2,54 мм, 2510) 13,2 мм − L+ 5 мм; Connfly C-DS1070-XXXXXX-D, https://www.connfly.com/static/upload/file/pdf/DS1070.pdf"
  }
 },
 {
  "fields": {
   "terArticle": "T-DS1071-SC600",
   "terLPlus": "7,05",
   "terLMinus": "6,95",
   "Источник L-": "чертёж контакта и корпуса: Connfly C-DS1071-XXXXX-B (Female Terminal, Female Housing), https://www.connfly.com/static/upload/file/pdf/DS1071.pdf"
  },
  "old": {
   "terArticle": "T-DS1071-SC600",
   "terLPlus": "5",
   "terLMinus": "9",
   "Источник L-": "оценка по чертежу корпуса: L− = I-DS1071 (2,54 мм) 14 мм − L+ 5 мм; Connfly C-DS1071-XXXXX-B, https://www.connfly.com/static/upload/file/pdf/DS1071.pdf"
  }
 },
 {
  "fields": {
   "terArticle": "T-DS1071-SC800",
   "terLPlus": "7,05",
   "terLMinus": "6,95",
   "Источник L-": "чертёж контакта и корпуса: Connfly C-DS1071-XXXXX-B (Female Terminal, Female Housing), https://www.connfly.com/static/upload/file/pdf/DS1071.pdf"
  },
  "old": {
   "terArticle": "T-DS1071-SC800",
   "terLPlus": "5",
   "terLMinus": "9",
   "Источник L-": "оценка по чертежу корпуса: L− = I-DS1071 (2,54 мм) 14 мм − L+ 5 мм; Connfly C-DS1071-XXXXX-B, https://www.connfly.com/static/upload/file/pdf/DS1071.pdf"
  }
 },
 {
  "fields": {
   "terArticle": "DF14-2628SCF",
   "terLPlus": "2,9",
   "terLMinus": "2,9",
   "Источник L-": "чертёж корпуса: Hirose EDC3-160306-01 (DF14-*S-1.25C), https://datasheet.lcsc.com/datasheet/pdf/4227c54f0e95ae3c8c5a14d87fc6a566.pdf?productCode=C185124"
  },
  "old": {
   "terArticle": "DF14-2628SCF",
   "terLPlus": "5",
   "terLMinus": "0,8",
   "Источник L-": "оценка по чертежу корпуса: L− = DF14-*S-1.25C 5,8 мм − L+ 5 мм; Hirose EDC3-160306-01, https://datasheet.lcsc.com/datasheet/pdf/4227c54f0e95ae3c8c5a14d87fc6a566.pdf?productCode=C185124"
  }
 },
 {
  "fields": {
   "terArticle": "DF14-2628SCFA",
   "terLPlus": "2,9",
   "terLMinus": "2,9",
   "Источник L-": "чертёж корпуса: Hirose EDC3-160306-01 (DF14-*S-1.25C), https://datasheet.lcsc.com/datasheet/pdf/4227c54f0e95ae3c8c5a14d87fc6a566.pdf?productCode=C185124"
  },
  "old": {
   "terArticle": "DF14-2628SCFA",
   "terLPlus": "5",
   "terLMinus": "0,8",
   "Источник L-": "оценка по чертежу корпуса: L− = DF14-*S-1.25C 5,8 мм − L+ 5 мм; Hirose EDC3-160306-01, https://datasheet.lcsc.com/datasheet/pdf/4227c54f0e95ae3c8c5a14d87fc6a566.pdf?productCode=C185124"
  }
 },
 {
  "fields": {
   "terArticle": "DF14-3032SCF",
   "terLPlus": "2,9",
   "terLMinus": "2,9",
   "Источник L-": "чертёж корпуса: Hirose EDC3-160306-01 (DF14-*S-1.25C), https://datasheet.lcsc.com/datasheet/pdf/4227c54f0e95ae3c8c5a14d87fc6a566.pdf?productCode=C185124"
  },
  "old": {
   "terArticle": "DF14-3032SCF",
   "terLPlus": "5",
   "terLMinus": "0,8",
   "Источник L-": "оценка по чертежу корпуса: L− = DF14-*S-1.25C 5,8 мм − L+ 5 мм; Hirose EDC3-160306-01, https://datasheet.lcsc.com/datasheet/pdf/4227c54f0e95ae3c8c5a14d87fc6a566.pdf?productCode=C185124"
  }
 },
 {
  "fields": {
   "terArticle": "DF14-3032SCFA",
   "terLPlus": "2,9",
   "terLMinus": "2,9",
   "Источник L-": "чертёж корпуса: Hirose EDC3-160306-01 (DF14-*S-1.25C), https://datasheet.lcsc.com/datasheet/pdf/4227c54f0e95ae3c8c5a14d87fc6a566.pdf?productCode=C185124"
  },
  "old": {
   "terArticle": "DF14-3032SCFA",
   "terLPlus": "5",
   "terLMinus": "0,8",
   "Источник L-": "оценка по чертежу корпуса: L− = DF14-*S-1.25C 5,8 мм − L+ 5 мм; Hirose EDC3-160306-01, https://datasheet.lcsc.com/datasheet/pdf/4227c54f0e95ae3c8c5a14d87fc6a566.pdf?productCode=C185124"
  }
 },
 {
  "fields": {
   "terArticle": "2147205000",
   "terLPlus": "1,6",
   "terLMinus": "1,55"
  },
  "old": {
   "terArticle": "2147205000",
   "terLPlus": "5",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "500588000",
   "terLPlus": "2",
   "terLMinus": "1,95"
  },
  "old": {
   "terArticle": "500588000",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "500588001",
   "terLPlus": "2",
   "terLMinus": "1,95"
  },
  "old": {
   "terArticle": "500588001",
   "terLPlus": "5",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "500588020",
   "terLPlus": "2",
   "terLMinus": "1,95"
  },
  "old": {
   "terArticle": "500588020",
   "terLPlus": "5",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "500798000",
   "terLPlus": "2",
   "terLMinus": "1,95"
  },
  "old": {
   "terArticle": "500798000",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "500798001",
   "terLPlus": "2",
   "terLMinus": "1,95"
  },
  "old": {
   "terArticle": "500798001",
   "terLPlus": "5",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "500798020",
   "terLPlus": "2",
   "terLMinus": "1,95"
  },
  "old": {
   "terArticle": "500798020",
   "terLPlus": "5",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "503948051",
   "terLPlus": "3,5",
   "terLMinus": "3,5",
   "Источник L-": "чертёж корпуса: Molex 1511921001 (Milli-Grid 151192), https://datasheet.lcsc.com/datasheet/pdf/045b3c208df0ef4741b42924f908715d.pdf?productCode=C3269195"
  },
  "old": {
   "terArticle": "503948051",
   "terLPlus": "5",
   "terLMinus": "2",
   "Источник L-": "оценка по чертежу корпуса: L− = Milli-Grid 151192 7 мм − L+ 5 мм; Molex 1511921001, https://datasheet.lcsc.com/datasheet/pdf/045b3c208df0ef4741b42924f908715d.pdf?productCode=C3269195; 51110, 151100 — чертежей с размером нет"
  }
 },
 {
  "fields": {
   "terArticle": "503948052",
   "terLPlus": "3,5",
   "terLMinus": "3,5",
   "Источник L-": "чертёж корпуса: Molex 1511921001 (Milli-Grid 151192), https://datasheet.lcsc.com/datasheet/pdf/045b3c208df0ef4741b42924f908715d.pdf?productCode=C3269195"
  },
  "old": {
   "terArticle": "503948052",
   "terLPlus": "5",
   "terLMinus": "2",
   "Источник L-": "оценка по чертежу корпуса: L− = Milli-Grid 151192 7 мм − L+ 5 мм; Molex 1511921001, https://datasheet.lcsc.com/datasheet/pdf/045b3c208df0ef4741b42924f908715d.pdf?productCode=C3269195; 51110, 151100 — чертежей с размером нет"
  }
 },
 {
  "fields": {
   "terArticle": "503948054",
   "terLPlus": "3,5",
   "terLMinus": "3,5",
   "Источник L-": "чертёж корпуса: Molex 1511921001 (Milli-Grid 151192), https://datasheet.lcsc.com/datasheet/pdf/045b3c208df0ef4741b42924f908715d.pdf?productCode=C3269195"
  },
  "old": {
   "terArticle": "503948054",
   "terLPlus": "5",
   "terLMinus": "2",
   "Источник L-": "оценка по чертежу корпуса: L− = Milli-Grid 151192 7 мм − L+ 5 мм; Molex 1511921001, https://datasheet.lcsc.com/datasheet/pdf/045b3c208df0ef4741b42924f908715d.pdf?productCode=C3269195; 51110, 151100 — чертежей с размером нет"
  }
 },
 {
  "fields": {
   "terArticle": "503948055",
   "terLPlus": "3,5",
   "terLMinus": "3,5",
   "Источник L-": "чертёж корпуса: Molex 1511921001 (Milli-Grid 151192), https://datasheet.lcsc.com/datasheet/pdf/045b3c208df0ef4741b42924f908715d.pdf?productCode=C3269195"
  },
  "old": {
   "terArticle": "503948055",
   "terLPlus": "5",
   "terLMinus": "2",
   "Источник L-": "оценка по чертежу корпуса: L− = Milli-Grid 151192 7 мм − L+ 5 мм; Molex 1511921001, https://datasheet.lcsc.com/datasheet/pdf/045b3c208df0ef4741b42924f908715d.pdf?productCode=C3269195; 51110, 151100 — чертежей с размером нет"
  }
 },
 {
  "fields": {
   "terArticle": "503948999",
   "terLPlus": "3,5",
   "terLMinus": "3,5",
   "Источник L-": "чертёж корпуса: Molex 1511921001 (Milli-Grid 151192), https://datasheet.lcsc.com/datasheet/pdf/045b3c208df0ef4741b42924f908715d.pdf?productCode=C3269195"
  },
  "old": {
   "terArticle": "503948999",
   "terLPlus": "5",
   "terLMinus": "2",
   "Источник L-": "оценка по чертежу корпуса: L− = Milli-Grid 151192 7 мм − L+ 5 мм; Molex 1511921001, https://datasheet.lcsc.com/datasheet/pdf/045b3c208df0ef4741b42924f908715d.pdf?productCode=C3269195; 51110, 151100 — чертежей с размером нет"
  }
 },
 {
  "fields": {
   "terArticle": "873968051",
   "terLPlus": "3,5",
   "terLMinus": "3,5",
   "Источник L-": "чертёж корпуса: Molex 1511921001 (Milli-Grid 151192), https://datasheet.lcsc.com/datasheet/pdf/045b3c208df0ef4741b42924f908715d.pdf?productCode=C3269195"
  },
  "old": {
   "terArticle": "873968051",
   "terLPlus": "5",
   "terLMinus": "2",
   "Источник L-": "оценка по чертежу корпуса: L− = Milli-Grid 151192 7 мм − L+ 5 мм; Molex 1511921001, https://datasheet.lcsc.com/datasheet/pdf/045b3c208df0ef4741b42924f908715d.pdf?productCode=C3269195; 51110, 151100 — чертежей с размером нет"
  }
 },
 {
  "fields": {
   "terArticle": "5054311000",
   "terLPlus": "3,05",
   "terLMinus": "3,05",
   "Источник L-": "чертёж корпуса: Molex 5055650002-SD, https://static.chipdip.ru/lib/955/DOC011955731.pdf"
  },
  "old": {
   "terArticle": "5054311000",
   "terLPlus": "5",
   "terLMinus": "1,1",
   "Источник L-": "оценка по чертежу корпуса: L− = Micro-Lock Plus 1.25 505565 6,1 мм − L+ 5 мм; Molex 5055650002-SD, https://static.chipdip.ru/lib/955/DOC011955731.pdf; 505432 — не смотрели"
  }
 },
 {
  "fields": {
   "terArticle": "5054311100",
   "terLPlus": "3,05",
   "terLMinus": "3,05",
   "Источник L-": "чертёж корпуса: Molex 5055650002-SD, https://static.chipdip.ru/lib/955/DOC011955731.pdf"
  },
  "old": {
   "terArticle": "5054311100",
   "terLPlus": "5",
   "terLMinus": "1,1",
   "Источник L-": "оценка по чертежу корпуса: L− = Micro-Lock Plus 1.25 505565 6,1 мм − L+ 5 мм; Molex 5055650002-SD, https://static.chipdip.ru/lib/955/DOC011955731.pdf; 505432 — не смотрели"
  }
 },
 {
  "fields": {
   "terArticle": "502128000",
   "terLPlus": "2,85",
   "terLMinus": "2,85",
   "Источник L-": "чертёж корпуса: Molex 510650000-SD, https://static.chipdip.ru/lib/528/DOC059528857.pdf"
  },
  "old": {
   "terArticle": "502128000",
   "terLPlus": "5",
   "terLMinus": "0,7",
   "Источник L-": "оценка по чертежу корпуса: L− = Micro-Latch 51065 5,7 мм − L+ 5 мм; Molex 510650000-SD, https://static.chipdip.ru/lib/528/DOC059528857.pdf; в 502351 — 6,5 (SD-502351-001); 51089, 51090 — не смотрели"
  }
 },
 {
  "fields": {
   "terArticle": "2140935000",
   "terLPlus": "2,15",
   "terLMinus": "2,15",
   "Источник L-": "чертёж корпуса: Molex 2140920000-SD, https://datasheet.lcsc.com/datasheet/pdf/ec9d5c7cd772cf79382461165b8238fe.pdf?productCode=C3266109"
  },
  "old": {
   "terArticle": "2140935000",
   "terLPlus": "4",
   "terLMinus": "0,3",
   "Источник L-": "оценка по чертежу корпуса: L− = OneBlade 214092 4,3 мм − L+ 4 мм; Molex 2140920000-SD, https://datasheet.lcsc.com/datasheet/pdf/ec9d5c7cd772cf79382461165b8238fe.pdf?productCode=C3266109"
  }
 },
 {
  "fields": {
   "terArticle": "5037650098",
   "terLPlus": "2,7",
   "terLMinus": "2,7",
   "Источник L-": "чертёж корпуса: Molex SD-503764-001, https://static.chipdip.ru/lib/843/DOC011843066.pdf"
  },
  "old": {
   "terArticle": "5037650098",
   "terLPlus": "4",
   "terLMinus": "1,4",
   "Источник L-": "оценка по чертежу корпуса: L− = Pico-Lock 1.0 503764 5,4 мм − L+ 4 мм; Molex SD-503764-001, https://static.chipdip.ru/lib/843/DOC011843066.pdf"
  }
 },
 {
  "fields": {
   "terArticle": "5040520098",
   "terLPlus": "3,2",
   "terLMinus": "3,2",
   "Источник L-": "чертёж корпуса: Molex 5040510000-SD, https://static.chipdip.ru/lib/778/DOC013778807.pdf"
  },
  "old": {
   "terArticle": "5040520098",
   "terLPlus": "5",
   "terLMinus": "1,4",
   "Источник L-": "оценка по чертежу корпуса: L− = Pico-Lock 1.5 504051 6,4 мм − L+ 5 мм; Molex 5040510000-SD, https://static.chipdip.ru/lib/778/DOC013778807.pdf"
  }
 },
 {
  "fields": {
   "terArticle": "874210000",
   "terLPlus": "3,1",
   "terLMinus": "3,1",
   "Источник L-": "чертёж корпуса: Molex 874390000-SD, https://static.chipdip.ru/lib/512/DOC048512109.pdf"
  },
  "old": {
   "terArticle": "874210000",
   "terLPlus": "5",
   "terLMinus": "1,2",
   "Источник L-": "оценка по чертежу корпуса: L− = Pico-SPOX 87439 6,2 мм − L+ 5 мм; Molex 874390000-SD, https://static.chipdip.ru/lib/512/DOC048512109.pdf"
  }
 },
 {
  "fields": {
   "terArticle": "L-KLS1-2.50-T",
   "terLPlus": "4",
   "terLMinus": "3,95"
  },
  "old": {
   "terArticle": "L-KLS1-2.50-T",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "1053002200",
   "terLPlus": "7,85",
   "terLMinus": "7,85"
  },
  "old": {
   "terArticle": "1053002200",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "1053001900",
   "terLPlus": "7,85",
   "terLMinus": "7,85"
  },
  "old": {
   "terArticle": "1053001900",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "1053002100",
   "terLPlus": "7,85",
   "terLMinus": "7,85"
  },
  "old": {
   "terArticle": "1053002100",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "1053002300",
   "terLPlus": "7,85",
   "terLMinus": "7,85"
  },
  "old": {
   "terArticle": "1053002300",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "1053002400",
   "terLPlus": "7,85",
   "terLMinus": "7,85"
  },
  "old": {
   "terArticle": "1053002400",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "1053001100",
   "terLPlus": "7,85",
   "terLMinus": "7,85"
  },
  "old": {
   "terArticle": "1053001100",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "1053001200",
   "terLPlus": "7,85",
   "terLMinus": "7,85"
  },
  "old": {
   "terArticle": "1053001200",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "1053001300",
   "terLPlus": "7,85",
   "terLMinus": "7,85"
  },
  "old": {
   "terArticle": "1053001300",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "1053001400",
   "terLPlus": "7,85",
   "terLMinus": "7,85"
  },
  "old": {
   "terArticle": "1053001400",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "1053001810",
   "terLPlus": "7,85",
   "terLMinus": "7,85"
  },
  "old": {
   "terArticle": "1053001810",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "2014472300",
   "terLPlus": "7,85",
   "terLMinus": "11,05"
  },
  "old": {
   "terArticle": "2014472300",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "2014472800",
   "terLPlus": "7,85",
   "terLMinus": "11,05"
  },
  "old": {
   "terArticle": "2014472800",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "2014472200",
   "terLPlus": "7,85",
   "terLMinus": "11,05"
  },
  "old": {
   "terArticle": "2014472200",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "2014472600",
   "terLPlus": "7,85",
   "terLMinus": "11,05"
  },
  "old": {
   "terArticle": "2014472600",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "2014472400",
   "terLPlus": "7,85",
   "terLMinus": "11,05"
  },
  "old": {
   "terArticle": "2014472400",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "2014471800",
   "terLPlus": "7,85",
   "terLMinus": "11,05"
  },
  "old": {
   "terArticle": "2014471800",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "2014471200",
   "terLPlus": "7,85",
   "terLMinus": "11,05"
  },
  "old": {
   "terArticle": "2014471200",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "2014471300",
   "terLPlus": "7,85",
   "terLMinus": "11,05"
  },
  "old": {
   "terArticle": "2014471300",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "2014471600",
   "terLPlus": "7,85",
   "terLMinus": "11,05"
  },
  "old": {
   "terArticle": "2014471600",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 },
 {
  "fields": {
   "terArticle": "2014471400",
   "terLPlus": "7,85",
   "terLMinus": "11,05"
  },
  "old": {
   "terArticle": "2014471400",
   "terLPlus": "",
   "terLMinus": "",
   "Источник L-": ""
  }
 }
]
''')

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))


def current() -> dict[str, dict]:
    with factory() as uow:
        records = uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=100_000)
    return {str(r.fields.get("terArticle") or ""): dict(r.fields) for r in records}


before = current()
missing = [row["fields"]["terArticle"] for row in ROWS if row["fields"]["terArticle"] not in before]
if missing:
    raise SystemExit(f"ОШИБКА: нет в БД.ТЕР: {missing}")
with factory() as uow:
    report = import_rows("ter", uow, [ImportRowIn(fields=row["fields"], old=row["old"]) for row in ROWS], ACTOR,
                         moscow_now(), dry_run=dry, source="erp").model_dump()
for item in report["refused"]:
    print("отказ:", json.dumps(item, ensure_ascii=False, default=str))
if report["created"]:
    raise SystemExit("ОШИБКА: импорт создал бы строки — ключ не найден")
if not dry:
    after = current()
    for row in ROWS:
        have = after[row["fields"]["terArticle"]]
        print("стало:", row["fields"]["terArticle"], {name: have.get(name, "") for name in FIELDS})
print("ИТОГО", json.dumps({"строк": len(ROWS), "обновится" if dry else "обновлено": len(report["updated"]),
                           "отказ": len(report["refused"]), "создано": len(report["created"])}, ensure_ascii=False),
      "(проверка, ничего не записано)" if dry else "(записано)")

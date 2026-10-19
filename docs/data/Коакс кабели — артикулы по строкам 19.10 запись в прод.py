"""Запись в прод 19.10.2026 — «Коаксиальные кабели» (БД.ПРОВ): склеенные артикулы продавцов разнесены по строкам
(владелец 19.10: «ты разные артикулы кабелей записал в одно, там же разные материалы … выбираем низшую цену, ставим его
как поставщика и заносим то же самое как и в разъёмах»).

Одна строка = один кабель (одни материалы) = один артикул продавца: «Поставщик», «Артикул поставщика», «Цена поставщика,
₽/м» (цена сайта без НДС 22 %, как во всей БД.ПРОВ), «Дата цены», «Ссылка» (новый свой столбец, тем же путём, что
«+ Столбец»). Решения и каждый артикул — «Коакс кабели — артикулы по строкам 19.10.csv» / «.md» (журнал для отката).

  • существующие строки (63) — правкой записи со старым значением (ячейка, которую уже поменяли, вернётся отказом,
    не перезапишется); пустые материалы RG-6 и CNT-400 — только в пустые ячейки;
  • новые строки (61) — загрузкой import_rows("prov") по «Марке» (cable_key, не по ID), затем свои столбцы правкой
    записи со старым "";
  • очищаемые строки (5) — очищены склеенные ячейки продавца (старое значение — то, что прочитано 19.10).
Повторный запуск идемпотентен: уже записанное печатается как «уже так».

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → создано 0, записано 0, отказ 0, остальное «уже так»
"""
import json
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import (ColumnCommand, EditRecordCommand, FieldEdit, add_column,
                                                 custom_columns, edit_record)
from app.modules.references.domain import TechOperationTab, cable_key
from app.modules.references.importers import import_rows
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 19.10.2026", department="System")
LINK = "Ссылка"
PAYLOAD = json.loads(r'''{
 "existing": [
  {
   "mark": "5D-FB",
   "edits": [
    [
     "Поставщик",
     "gsm-repiteri.ru, kroks.ru, vegatel.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 0090; gsm-repiteri.ru: 4632; kroks.ru: 3524; kroks.ru: 3525; kroks.ru: 3521; kroks.ru: 3523; kroks.ru: 3519; kroks.ru: 3518; kroks.ru: 3520; kroks.ru: 2279; kroks.ru: 3522; kroks.ru: 1982; vegatel.ru: R03042; vegatel.ru: R11134; vegatel.ru: R03288",
     "gsm-repiteri.ru: 0090"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/kabel-5d-fb-pvc-chernyy"
    ]
   ]
  },
  {
   "mark": "5D-FB Cu",
   "edits": [
    [
     "Поставщик",
     "gsm-repiteri.ru, kroks.ru, vegatel.ru",
     "kroks.ru"
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 0912; gsm-repiteri.ru: 3947; gsm-repiteri.ru: 4653; kroks.ru: 1372; kroks.ru: 3526; kroks.ru: 3527; kroks.ru: 3528; kroks.ru: 3529; kroks.ru: 2278; vegatel.ru: R13244; vegatel.ru: R03295; vegatel.ru: R11196",
     "kroks.ru: 3527"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cu-pe-al-pvc-dlya-vnutrennej-prokladki/"
    ]
   ]
  },
  {
   "mark": "8D-FB",
   "edits": [
    [
     "Поставщик",
     "gsm-repiteri.ru, kroks.ru, vegatel.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 0091; gsm-repiteri.ru: 2642; gsm-repiteri.ru: 4208; kroks.ru: 1373; kroks.ru: 3532; kroks.ru: 3533; vegatel.ru: R00089; vegatel.ru: R03066; vegatel.ru: R11141",
     "gsm-repiteri.ru: 0091"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/kabel-8d-fb-cca"
    ]
   ]
  },
  {
   "mark": "8D-FB Cu",
   "edits": [
    [
     "Поставщик",
     "gsm-repiteri.ru, vegatel.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 3104; gsm-repiteri.ru: 3359; gsm-repiteri.ru: 0092; gsm-repiteri.ru: 4654; vegatel.ru: R11219",
     "gsm-repiteri.ru: 3104"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-8d-fb-cu-pvc-chernyy"
    ]
   ]
  },
  {
   "mark": "10D-FB",
   "edits": [
    [
     "Поставщик",
     "kroks.ru, gsm-repiteri.ru, vegatel.ru",
     "kroks.ru"
    ],
    [
     "Артикул поставщика",
     "kroks.ru: 2148; kroks.ru: 3530; kroks.ru: 3531; gsm-repiteri.ru: 0088; gsm-repiteri.ru: 3959; gsm-repiteri.ru: 0247; gsm-repiteri.ru: 3949; gsm-repiteri.ru: 4656; vegatel.ru: R07342; vegatel.ru: R03791; vegatel.ru: R11172",
     "kroks.ru: 3530"
    ],
    [
     "Цена поставщика, ₽/м",
     "154,92",
     "262,30"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-10d-fb-cca-pe-tc-pvc-o/"
    ]
   ]
  },
  {
   "mark": "10D-FB PE",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-10d-fb-cca-pe-al-pe-dlya-vneshnej-prokladki/"
    ]
   ]
  },
  {
   "mark": "CNT-400",
   "edits": [
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 3186; gsm-repiteri.ru: 0620",
     "gsm-repiteri.ru: 0620"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/vch-kabel-cnt-400-chernyj"
    ],
    [
     "Материал жилы",
     "",
     "алюминий омеднённый"
    ],
    [
     "Материал экрана",
     "",
     "алюминий, фольга + медь лужёная, оплётка"
    ],
    [
     "Материал оболочки",
     "",
     "ПВХ"
    ]
   ]
  },
  {
   "mark": "GNS-360B",
   "edits": [
    [
     "Артикул поставщика",
     "kroks.ru: 3873; kroks.ru: 3872",
     "kroks.ru: 3873"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-gns-360b-do-18-ggcz-spc-ldptfe-spc-pfa/"
    ]
   ]
  },
  {
   "mark": "LMR-100",
   "edits": [
    [
     "Поставщик",
     "ООО \"Арисмо инжиниринг\", kroks.ru, gsm-repiteri.ru",
     "kroks.ru"
    ],
    [
     "Артикул поставщика",
     "kroks.ru: 1706; kroks.ru: 3547; gsm-repiteri.ru: 3274",
     "kroks.ru: 3547"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-lmr-100-cu-solid-pe-tc-pvc-revo/"
    ]
   ]
  },
  {
   "mark": "LMR-195",
   "edits": [
    [
     "Поставщик",
     "gsm-repiteri.ru, kroks.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 3363; kroks.ru: 3542; kroks.ru: 3543",
     "gsm-repiteri.ru: 3363"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-koaksialnyy-lmr-195"
    ]
   ]
  },
  {
   "mark": "RF 0.81",
   "edits": [
    [
     "Поставщик",
     "gsm-repiteri.ru, kroks.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 4386; kroks.ru: 2597",
     "gsm-repiteri.ru: 4386"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-koaksialnyy-rf0-81"
    ]
   ]
  },
  {
   "mark": "RF 1.13",
   "edits": [
    [
     "Поставщик",
     "gsm-repiteri.ru, kroks.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 3364; kroks.ru: 2598",
     "gsm-repiteri.ru: 3364"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-koaksialnyy-rf1-13"
    ]
   ]
  },
  {
   "mark": "RF 1.37",
   "edits": [
    [
     "Поставщик",
     "CINTY, MEI DUO YUAN KEJI, gsm-repiteri.ru, kroks.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 3366; kroks.ru: 2600",
     "gsm-repiteri.ru: 3366"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-koaksialnyy-rf1-37"
    ]
   ]
  },
  {
   "mark": "RF 1.48",
   "edits": [
    [
     "Поставщик",
     "kroks.ru, gsm-repiteri.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "kroks.ru: 2601; gsm-repiteri.ru: 3365",
     "gsm-repiteri.ru: 3365"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-koaksialnyy-rf1-48"
    ]
   ]
  },
  {
   "mark": "RG-11",
   "edits": [
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 2393; gsm-repiteri.ru: 2114; gsm-repiteri.ru: 4028",
     "gsm-repiteri.ru: 2114"
    ],
    [
     "Цена поставщика, ₽/м",
     "27,05",
     "32,79"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable-75-om/kabel-rg-11-75-om"
    ]
   ]
  },
  {
   "mark": "RG-142",
   "edits": [
    [
     "Поставщик",
     "ООО \"МикроЭМ Технологии\", gsm-repiteri.ru, amel.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 2603; amel.ru: RG-142B/U",
     "gsm-repiteri.ru: 2603"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-rg-142-50-om"
    ]
   ]
  },
  {
   "mark": "RG-174",
   "edits": [
    [
     "Поставщик",
     "ИП Астафьева Юлия Андреевна, gsm-repiteri.ru, kroks.ru, amel.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 1640; kroks.ru: 1769; kroks.ru: 3534; kroks.ru: 3535; amel.ru: RG-174/U",
     "gsm-repiteri.ru: 1640"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/kabel-rg-174-50-om"
    ]
   ]
  },
  {
   "mark": "RG-178",
   "edits": [
    [
     "Поставщик",
     "gsm-repiteri.ru, kroks.ru, amel.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 2604; kroks.ru: 386; kroks.ru: 3545; amel.ru: RG-178B/U",
     "gsm-repiteri.ru: 2604"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-rg-178-50-om"
    ]
   ]
  },
  {
   "mark": "RG-213",
   "edits": [
    [
     "Поставщик",
     "gsm-repiteri.ru, kroks.ru, vegatel.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 1588; kroks.ru: 3544; kroks.ru: 1374; vegatel.ru: R04155",
     "gsm-repiteri.ru: 1588"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/kabel-rg-213-u-black"
    ]
   ]
  },
  {
   "mark": "RG-213 C/U",
   "edits": [
    [
     "Поставщик",
     "baltic-signal.ru, gsm-repiteri.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 2336; gsm-repiteri.ru: 1404",
     "gsm-repiteri.ru: 2336"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-rg-213-c-u-pvc"
    ]
   ]
  },
  {
   "mark": "RG-316",
   "edits": [
    [
     "Поставщик",
     "АО \"ВНИИР-Прогресс\", baltic-signal.ru, gsm-repiteri.ru, kroks.ru, vegatel.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 1951; kroks.ru: 387; kroks.ru: 3546; vegatel.ru: R01406",
     "gsm-repiteri.ru: 1951"
    ],
    [
     "Цена поставщика, ₽/м",
     "89,34",
     "90,16"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/kabel-rg-316-50-om"
    ]
   ]
  },
  {
   "mark": "RG-400",
   "edits": [
    [
     "Поставщик",
     "gsm-repiteri.ru, kroks.ru, amel.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 3277; kroks.ru: 946; amel.ru: RG-400/U",
     "gsm-repiteri.ru: 3277"
    ],
    [
     "Цена поставщика, ₽/м",
     "459,02",
     "532,79"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-rg-400-50-om"
    ]
   ]
  },
  {
   "mark": "RG-402",
   "edits": [
    [
     "Поставщик",
     "kroks.ru, gsm-repiteri.ru, vegatel.ru, amel.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "kroks.ru: 1447; gsm-repiteri.ru: 2605; vegatel.ru: R01413; amel.ru: RG-402",
     "gsm-repiteri.ru: 2605"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-rg-402-50-om"
    ]
   ]
  },
  {
   "mark": "RG-405",
   "edits": [
    [
     "Поставщик",
     "kroks.ru, gsm-repiteri.ru, vegatel.ru, amel.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "kroks.ru: 1450; gsm-repiteri.ru: 2602; vegatel.ru: R01420; amel.ru: RG-405",
     "gsm-repiteri.ru: 2602"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-rg-405-50-om"
    ]
   ]
  },
  {
   "mark": "RG-405SS",
   "edits": [
    [
     "Поставщик",
     "amel.ru, gsm-repiteri.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "amel.ru: RG-405SS; gsm-repiteri.ru: 4619",
     "gsm-repiteri.ru: 4619"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-rg-405-ss"
    ]
   ]
  },
  {
   "mark": "RG-58",
   "edits": [
    [
     "Поставщик",
     "kroks.ru, amel.ru",
     "amel.ru"
    ],
    [
     "Артикул поставщика",
     "kroks.ru: 384; kroks.ru: 3538; kroks.ru: 3536; kroks.ru: 3537; amel.ru: RG-58/U",
     "amel.ru: RG-58/U"
    ],
    [
     "Цена поставщика, ₽/м",
     "73,77",
     ""
    ],
    [
     "Дата цены",
     "15.10.2026",
     ""
    ],
    [
     "Ссылка",
     "",
     "https://www.amel.ru/products/cables_coaxial/kabeli-serii-rg-gibkiy/rg-58-u/"
    ]
   ]
  },
  {
   "mark": "RG-58 A/U",
   "edits": [
    [
     "Поставщик",
     "vegatel.ru, gsm-repiteri.ru",
     "vegatel.ru"
    ],
    [
     "Артикул поставщика",
     "vegatel.ru: R01581; vegatel.ru: R01536; gsm-repiteri.ru: 0448",
     "vegatel.ru: R01581"
    ],
    [
     "Цена поставщика, ₽/м",
     "27,87",
     "48,36"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://www.vegatel.ru/kabel/vegatel-rg-58/kabel-vegatel-rg-58-a-u-cu-gost-re-chernii-buhta-100m"
    ]
   ]
  },
  {
   "mark": "RG-58 C/U",
   "edits": [
    [
     "Поставщик",
     "gsm-repiteri.ru, amel.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 1893; amel.ru: RG-58С/U PVC",
     "gsm-repiteri.ru: 1893"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/kabel-rg-58-c-u-chernyy"
    ]
   ]
  },
  {
   "mark": "RG-6",
   "edits": [
    [
     "Поставщик",
     "gsm-repiteri.ru, kroks.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 1527; kroks.ru: 3539; kroks.ru: 2139; kroks.ru: 3540",
     "gsm-repiteri.ru: 1527"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable-75-om/kabel-rg-6-75-om"
    ],
    [
     "Материал жилы",
     "",
     "сталь омеднённая"
    ],
    [
     "Материал диэлектрика",
     "",
     "ПЭ вспененный"
    ],
    [
     "Материал экрана",
     "",
     "алюминий, фольга + алюминий-магний, оплётка"
    ],
    [
     "Материал оболочки",
     "",
     "ПВХ"
    ]
   ]
  },
  {
   "mark": "SAT-703",
   "edits": [
    [
     "Поставщик",
     "kroks.ru, gsm-repiteri.ru",
     "kroks.ru"
    ],
    [
     "Артикул поставщика",
     "kroks.ru: 3541; kroks.ru: 627; gsm-repiteri.ru: 0735",
     "kroks.ru: 3541"
    ],
    [
     "Цена поставщика, ₽/м",
     "32,79",
     "61,48"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-sat-703-cu-pe-al-pvc/"
    ]
   ]
  },
  {
   "mark": "SR-141-50",
   "edits": [
    [
     "Поставщик",
     "baltic-signal.ru, gsm-repiteri.ru, amel.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 3638; amel.ru: SR-141",
     "gsm-repiteri.ru: 3638"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-rg-402-sr-141-50"
    ]
   ]
  },
  {
   "mark": "TW-FWP142",
   "edits": [
    [
     "Артикул поставщика",
     "kroks.ru: 4073; kroks.ru: 4074; kroks.ru: 4075",
     "kroks.ru: 4073"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-tw-fwp142-do-18-ggcz-spc-ldptfe-spc-fep/"
    ]
   ]
  },
  {
   "mark": "MC12FZ-50",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-koaksialnyy-fider-1-2-mc12fz-50"
    ]
   ]
  },
  {
   "mark": "HCAAY-50-12",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-hengxin-koaksialnyy-fider-1-2-hcaay-50-12"
    ]
   ]
  },
  {
   "mark": "РК50-12-331-С",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/fider-rk50-12-331-c-1-2-r"
    ]
   ]
  },
  {
   "mark": "1/2 Baltic Signal BSF-50-12 LSZH",
   "edits": [
    [
     "Поставщик",
     "baltic-signal.ru, gsm-repiteri.ru",
     "gsm-repiteri.ru"
    ],
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/koaksialnyy-fider-1-2-baltic-signal-bsf-50-12-lszh-fr"
    ]
   ]
  },
  {
   "mark": "РК50-12-331-Снг(С)",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-rk50-12-331-s-koaksialnyy-fider-1-2-hf"
    ]
   ]
  },
  {
   "mark": "HCAAYZ-50-12",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/kabel-hengxin-koaksialnyy-fider-1-2-hcaayz-50-12"
    ]
   ]
  },
  {
   "mark": "LDF4-50A",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-andrew-koaksialnyy-fider-1-2"
    ]
   ]
  },
  {
   "mark": "HPL 50-1/2F",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/kabel-acome-koaksialnyy-fider-1-2-hpl-50-1-2f"
    ]
   ]
  },
  {
   "mark": "VEGATEL CF-1/2",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable-vegatel/cable-vegatel-cf-1-2-gost"
    ]
   ]
  },
  {
   "mark": "RFA 1/2″-50",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-draka-koaksialnyy-fider-1-2"
    ]
   ]
  },
  {
   "mark": "LCF12-50J",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/kabel-lcf-12-50j-koaksialnyj-fider-1-2"
    ]
   ]
  },
  {
   "mark": "HCAHY-50-9",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-kingsignal-koaksialnyy-fider-1-2-sf-hcahy-50-9"
    ]
   ]
  },
  {
   "mark": "MC12SFZ-50",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-koaksialnyy-fider-1-2-sf-mc12sfz-50"
    ]
   ]
  },
  {
   "mark": "HRCAY-50-9",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-koaksialnyy-fider-1-2-sf-hengxin-hrcay-50-9"
    ]
   ]
  },
  {
   "mark": "HRCAYZ-50-9",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-hengxin-koaksialnyy-fider-1-2-sf-hrcayz-50-9"
    ]
   ]
  },
  {
   "mark": "РК50-9-32-С",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-rk50-9-32-s"
    ]
   ]
  },
  {
   "mark": "РК50-9-331-С",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-rk50-9-331-s-1-2-s-koaksialnyy-fider-1-2-sf"
    ]
   ]
  },
  {
   "mark": "1/2 SF Baltic Signal BSF-50-9 LSZH",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/koaksialnyy-fider-1-2sf-baltic-signal-bsf-50-9-lszh-fr"
    ]
   ]
  },
  {
   "mark": "РК50-9-331-Снг(С)",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-rk50-9-331-s-koaksialnyy-fider-1-2-sf"
    ]
   ]
  },
  {
   "mark": "VEGATEL SuperFlex-1/2",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable-vegatel/cable-vegatel-superflex-1-2-gost"
    ]
   ]
  },
  {
   "mark": "SCF12-50J",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-rfs-koaksialnyy-fider-1-2-sf-scf-12-50j"
    ]
   ]
  },
  {
   "mark": "SCF12-50JFN",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-rfs-koaksialnyy-fider-1-2-sf-scf-12-50jfn"
    ]
   ]
  },
  {
   "mark": "HCTAYZ-50-22",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-koaksialnyy-fider-7-8-hctayz-50-22-7-8"
    ]
   ]
  },
  {
   "mark": "РК50-23-391-Снг(С)",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-rk50-23-391-s-koaksialnyy-fider-7-8"
    ]
   ]
  },
  {
   "mark": "MC78FZ-50",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-modern-mc78fz-50"
    ]
   ]
  },
  {
   "mark": "AVA5RK-50FX",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/kabel-commscope-koaksialnyy-fider-7-8-ava5rk-50fx"
    ]
   ]
  },
  {
   "mark": "HCTAYZ-50-23",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-hengxin-koaksialnyy-fider-7-8-hctayz-50-23-7-8-a"
    ]
   ]
  },
  {
   "mark": "HPL 50-7/8 LA",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/kabel-acome-koaksialnyy-fider-7-8-hpl-50-7-8-la"
    ]
   ]
  },
  {
   "mark": "7/8 Baltic Signal BSF-50-23 LSZH",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/koaksialnyy-fider-7-8-baltic-signal-bsf-50-23-lszh-fr"
    ]
   ]
  },
  {
   "mark": "HCTAY-50-23",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-hengxin-koaksialnyy-fider-7-8-hctay-z-50-23-7-8"
    ]
   ]
  },
  {
   "mark": "LCF78-50J",
   "edits": [
    [
     "Дата цены",
     "15.10.2026",
     "19.10.2026"
    ],
    [
     "Ссылка",
     "",
     "https://gsm-repiteri.ru/prodazha/cable/cable-lcf-78-50j-koaksialnyj-fider-7-8"
    ]
   ]
  }
 ],
 "new": [
  {
   "mark": "5D-FB CCA LSZH FR",
   "base": {
    "Марка": "5D-FB CCA LSZH FR",
    "Тип провода": "коаксиальный",
    "Поставщик": "gsm-repiteri.ru",
    "Источник данных": "gsm-repiteri.ru арт. 4632 «Кабель 5D-FB CCA LSZH FR (черный)», https://gsm-repiteri.ru/prodazha/cable/cable-5d-fb-cca-lszh-fr — открыто 19.10.2026: оболочка LSZH, FR; строка выделена 19.10.2026 из «5D-FB» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "7,54"
   },
   "own": {
    "Артикул поставщика": "gsm-repiteri.ru: 4632",
    "Цена поставщика, ₽/м": "61,48",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/cable/cable-5d-fb-cca-lszh-fr",
    "Материал жилы": "алюминий омеднённый",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + алюминий-магний, оплётка",
    "Материал оболочки": "LSZH"
   }
  },
  {
   "mark": "5D-FB CCA PE AL PE",
   "base": {
    "Марка": "5D-FB CCA PE AL PE",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3521 «Кабель коаксиальный 5D-FB CCA PE AL PE (для внешней прокладки)», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cca-pe-al-pe-dlya-vneshnej-prokladki/ — открыто 19.10.2026: для внешней прокладки; строка выделена 19.10.2026 из «5D-FB» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "7,5"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3521",
    "Цена поставщика, ₽/м": "98,36",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cca-pe-al-pe-dlya-vneshnej-prokladki/",
    "Материал жилы": "алюминий омеднённый",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + алюминий, оплётка",
    "Материал оболочки": "ПЭ"
   }
  },
  {
   "mark": "5D-FB CCA PE CCA PVC внутр.",
   "base": {
    "Марка": "5D-FB CCA PE CCA PVC внутр.",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3523 «Кабель коаксиальный 5D-FB CCA PE CCA PVC (для внутренней прокладки)», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cca-pe-cca-pvc-dlya-vnutrennej-prokladki/ — открыто 19.10.2026: для внутренней прокладки; строка выделена 19.10.2026 из «5D-FB» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "7,8"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3523",
    "Цена поставщика, ₽/м": "73,77",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cca-pe-cca-pvc-dlya-vnutrennej-prokladki/",
    "Материал жилы": "алюминий омеднённый",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + алюминий омеднённый, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "5D-FB CCA PE CCA PVC наружн.",
   "base": {
    "Марка": "5D-FB CCA PE CCA PVC наружн.",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 2279 «Кабель коаксиальный 5D-FB CCA PE CCA PVC (для внешней прокладки)», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cca-pe-tc-pvc-dlya-vneshnej-prokladki/ — открыто 19.10.2026: для внешней прокладки (в характеристиках карточки жила «Cu», в названии и описании — CCA; взято CCA); строка выделена 19.10.2026 из «5D-FB» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "7,62"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 2279",
    "Цена поставщика, ₽/м": "98,36",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cca-pe-tc-pvc-dlya-vneshnej-prokladki/",
    "Материал жилы": "алюминий омеднённый",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + алюминий омеднённый, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "5D-FB CCA PE TC LSZH",
   "base": {
    "Марка": "5D-FB CCA PE TC LSZH",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3519 «Кабель коаксиальный 5D-FB CCA PE TC LSZH (для внешней прокладки)», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cca-pe-tc-lszh-dlya-vneshnej-prokladki/ — открыто 19.10.2026: для внешней прокладки; строка выделена 19.10.2026 из «5D-FB» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "7,62"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3519",
    "Цена поставщика, ₽/м": "98,36",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cca-pe-tc-lszh-dlya-vneshnej-prokladki/",
    "Материал жилы": "алюминий омеднённый",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + медь лужёная, оплётка",
    "Материал оболочки": "LSZH"
   }
  },
  {
   "mark": "5D-FB CCA PE TC PE",
   "base": {
    "Марка": "5D-FB CCA PE TC PE",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3518 «Кабель коаксиальный 5D-FB CCA PE TC PE (для внешней прокладки)», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cca-pe-tc-pe-dlya-vneshnej-prokladki/ — открыто 19.10.2026: для внешней прокладки; строка выделена 19.10.2026 из «5D-FB» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "7,4"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3518",
    "Цена поставщика, ₽/м": "73,77",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cca-pe-tc-pe-dlya-vneshnej-prokladki/",
    "Материал жилы": "алюминий омеднённый",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + медь лужёная, оплётка",
    "Материал оболочки": "ПЭ"
   }
  },
  {
   "mark": "5D-FB CCA PE TC PVC наружн.",
   "base": {
    "Марка": "5D-FB CCA PE TC PVC наружн.",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3520 «Кабель коаксиальный 5D-FB CCA PE TC PVC (для внешней прокладки)», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cca-pe-tc-pvc-dlya-vneshnej-prokladki-o/ — открыто 19.10.2026: для внешней прокладки; строка выделена 19.10.2026 из «5D-FB» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "7,5"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3520",
    "Цена поставщика, ₽/м": "98,36",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cca-pe-tc-pvc-dlya-vneshnej-prokladki-o/",
    "Материал жилы": "алюминий омеднённый",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + медь лужёная, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "5D-FB CCA PE TC PVC внутр.",
   "base": {
    "Марка": "5D-FB CCA PE TC PVC внутр.",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3522 «Кабель коаксиальный 5D-FB CCA FPE TC PVC (для внутренней прокладки)», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cca-fpe-tc-pvc-dlya-vnutrennej-prokladki/ — открыто 19.10.2026: для внутренней прокладки; тот же кабель: kroks.ru 1982 — 120 ₽ на сайте; строка выделена 19.10.2026 из «5D-FB» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "7,5"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3522",
    "Цена поставщика, ₽/м": "73,77",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cca-fpe-tc-pvc-dlya-vnutrennej-prokladki/",
    "Материал жилы": "алюминий омеднённый",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + медь лужёная, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "5D-FB Titan",
   "base": {
    "Марка": "5D-FB Titan",
    "Тип провода": "коаксиальный",
    "Поставщик": "vegatel.ru",
    "Источник данных": "vegatel.ru арт. R03042 «Кабель коаксиальный VEGATEL 5D-FB (Titan, черный)», https://www.vegatel.ru/kabel/kabel-5d-fb/kabel-vegatel-5d-fb-titan-chernyj-200m — открыто 19.10.2026: VEGATEL Titan: CCA, FPE, фольга DF + оплётка 85 % (металл оплётки не указан), ПВХ светостабилизированный; тот же кабель: vegatel.ru R11134 — 93 ₽ на сайте; vegatel.ru R03288 — 93 ₽ на сайте; строка выделена 19.10.2026 из «5D-FB» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "7,5"
   },
   "own": {
    "Артикул поставщика": "vegatel.ru: R03042",
    "Цена поставщика, ₽/м": "76,23",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://www.vegatel.ru/kabel/kabel-5d-fb/kabel-vegatel-5d-fb-titan-chernyj-200m",
    "Материал жилы": "алюминий омеднённый",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "5D-FB CU PE AL PE",
   "base": {
    "Марка": "5D-FB CU PE AL PE",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3528 «Кабель коаксиальный 5D-FB CU PE AL PE (для внешней прокладки)», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cu-pe-al-pe-dlya-vneshnej-prokladki/ — открыто 19.10.2026: для внешней прокладки; строка выделена 19.10.2026 из «5D-FB Cu» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "7,9"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3528",
    "Цена поставщика, ₽/м": "106,56",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cu-pe-al-pe-dlya-vneshnej-prokladki/",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + алюминий, оплётка",
    "Материал оболочки": "ПЭ"
   }
  },
  {
   "mark": "5D-FB CU LSZH FR",
   "base": {
    "Марка": "5D-FB CU LSZH FR",
    "Тип провода": "коаксиальный",
    "Поставщик": "gsm-repiteri.ru",
    "Источник данных": "gsm-repiteri.ru арт. 3947 «Кабель 5D-FB CU LSZH FR (черный)», https://gsm-repiteri.ru/prodazha/cable/cable-5d-fb-cu-lszh-fr-chernyy — открыто 19.10.2026: оболочка LSZH, FR; строка выделена 19.10.2026 из «5D-FB Cu» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "7,54"
   },
   "own": {
    "Артикул поставщика": "gsm-repiteri.ru: 3947",
    "Цена поставщика, ₽/м": "122,95",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/cable/cable-5d-fb-cu-lszh-fr-chernyy",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + алюминий-магний, оплётка",
    "Материал оболочки": "LSZH"
   }
  },
  {
   "mark": "5D-FB CU PE TC PVC",
   "base": {
    "Марка": "5D-FB CU PE TC PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3526 «Кабель коаксиальный 5D-FB CU FPE TC PVC (для внутренней прокладки)», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cu-fpe-tc-pvc-dlya-vnutrennej-prokladki/ — открыто 19.10.2026: для внутренней прокладки; тот же кабель: kroks.ru 1372 — 160 ₽ на сайте; строка выделена 19.10.2026 из «5D-FB Cu» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "7,9"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3526",
    "Цена поставщика, ₽/м": "114,75",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cu-fpe-tc-pvc-dlya-vnutrennej-prokladki/",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + медь лужёная, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "5D-FB CU PE TC LSZH",
   "base": {
    "Марка": "5D-FB CU PE TC LSZH",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3529 «Кабель коаксиальный 5D-FB CU PE TC LSZH (для внешней прокладки)», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cu-pe-tc-lszh-dlya-vneshnej-prokladki/ — открыто 19.10.2026: для внешней прокладки; строка выделена 19.10.2026 из «5D-FB Cu» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "7,5"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3529",
    "Цена поставщика, ₽/м": "122,95",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cu-pe-tc-lszh-dlya-vneshnej-prokladki/",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + медь лужёная, оплётка",
    "Материал оболочки": "LSZH"
   }
  },
  {
   "mark": "5D-FB CU PE CCA PVC",
   "base": {
    "Марка": "5D-FB CU PE CCA PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 2278 «Кабель коаксиальный 5D-FB CU PE CCA PVC (для внешней прокладки)», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cu-pe-cca-pvc-dlya-vneshnej-prokladki/ — открыто 19.10.2026: для внешней прокладки; строка выделена 19.10.2026 из «5D-FB Cu» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "7,5"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 2278",
    "Цена поставщика, ₽/м": "106,56",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-5d-fb-cu-pe-cca-pvc-dlya-vneshnej-prokladki/",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + алюминий омеднённый, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "5D-FB Cu ГОСТ",
   "base": {
    "Марка": "5D-FB Cu ГОСТ",
    "Тип провода": "коаксиальный",
    "Поставщик": "gsm-repiteri.ru",
    "Источник данных": "gsm-repiteri.ru арт. 4653 «Кабель VEGATEL 5D-FB Cu (ГОСТ, синий)», https://gsm-repiteri.ru/prodazha/cable/cable-vegatel-5d-fb-cu — открыто 19.10.2026: VEGATEL ГОСТ: медь, FPE, фольга DF + оплётка луженая медь, ПВХ пониженной пожароопасности (нг-LS); тот же кабель: vegatel.ru R13244 — 232 ₽ на сайте; vegatel.ru R03295 — 232 ₽ на сайте; vegatel.ru R11196 — 232 ₽ на сайте; строка выделена 19.10.2026 из «5D-FB Cu» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "7,5"
   },
   "own": {
    "Артикул поставщика": "gsm-repiteri.ru: 4653",
    "Цена поставщика, ₽/м": "180,33",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/cable/cable-vegatel-5d-fb-cu",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + медь лужёная, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "8D-FB CCA LSZH FR",
   "base": {
    "Марка": "8D-FB CCA LSZH FR",
    "Тип провода": "коаксиальный",
    "Поставщик": "gsm-repiteri.ru",
    "Источник данных": "gsm-repiteri.ru арт. 2642 «Кабель 8D-FB CCA LSZH FR (черный)», https://gsm-repiteri.ru/prodazha/cable/cable-8d-fb-cca-lszh-fr — открыто 19.10.2026: оболочка LSZH, FR; строка выделена 19.10.2026 из «8D-FB / 8D-FB CCA» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "10,15"
   },
   "own": {
    "Артикул поставщика": "gsm-repiteri.ru: 2642",
    "Цена поставщика, ₽/м": "122,95",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/cable/cable-8d-fb-cca-lszh-fr",
    "Материал жилы": "алюминий омеднённый",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + алюминий-магний, оплётка",
    "Материал оболочки": "LSZH"
   }
  },
  {
   "mark": "8D-FB CCA PE",
   "base": {
    "Марка": "8D-FB CCA PE",
    "Тип провода": "коаксиальный",
    "Поставщик": "gsm-repiteri.ru",
    "Источник данных": "gsm-repiteri.ru арт. 4208 «Кабель 8D-FB CCA PE (черный)», https://gsm-repiteri.ru/prodazha/cable/cable-8d-fb-cca-pe-chernyy — открыто 19.10.2026: оболочка ПЭ; строка выделена 19.10.2026 из «8D-FB / 8D-FB CCA» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "10,15"
   },
   "own": {
    "Артикул поставщика": "gsm-repiteri.ru: 4208",
    "Цена поставщика, ₽/м": "122,95",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/cable/cable-8d-fb-cca-pe-chernyy",
    "Материал жилы": "алюминий омеднённый",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + алюминий-магний, оплётка",
    "Материал оболочки": "ПЭ"
   }
  },
  {
   "mark": "8D-FB CCA PE TC PVC",
   "base": {
    "Марка": "8D-FB CCA PE TC PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3532 «Кабель коаксиальный 8D-FB CCA PE TC PVC (для внутренней прокладки)», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-8d-fb-cca-pe-tc-pvc-dlya-vnutrennej-prokladki/ — открыто 19.10.2026: оплётка луженая медь; тот же кабель: kroks.ru 1373 — 230 ₽ на сайте; kroks.ru 3533 — 210 ₽ на сайте; строка выделена 19.10.2026 из «8D-FB / 8D-FB CCA» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "10,7"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3532",
    "Цена поставщика, ₽/м": "147,54",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-8d-fb-cca-pe-tc-pvc-dlya-vnutrennej-prokladki/",
    "Материал жилы": "алюминий омеднённый",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + медь лужёная, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "8D-FB ГОСТ",
   "base": {
    "Марка": "8D-FB ГОСТ",
    "Тип провода": "коаксиальный",
    "Поставщик": "vegatel.ru",
    "Источник данных": "vegatel.ru арт. R00089 «Кабель коаксиальный VEGATEL 8D-FB (ГОСТ, черный) бухта 100м», https://www.vegatel.ru/kabel/kabel-8d-fb/kabel-koaksialnyj-vegatel-8d-fb-gost-chernyj — открыто 19.10.2026: VEGATEL ГОСТ: CCA, FPE, фольга DF + оплётка TCCA (по карточке gsm-repiteri того же товара, 4694), ПВХ нг-LS; строка выделена 19.10.2026 из «8D-FB» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "11,1"
   },
   "own": {
    "Артикул поставщика": "vegatel.ru: R00089",
    "Цена поставщика, ₽/м": "178,69",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://www.vegatel.ru/kabel/kabel-8d-fb/kabel-koaksialnyj-vegatel-8d-fb-gost-chernyj",
    "Материал жилы": "алюминий омеднённый",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + алюминий омеднённый лужёный, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "8D-FB Titan",
   "base": {
    "Марка": "8D-FB Titan",
    "Тип провода": "коаксиальный",
    "Поставщик": "vegatel.ru",
    "Источник данных": "vegatel.ru арт. R03066 «Кабель коаксиальный VEGATEL 8D-FB (Titan, черный) бухта 200м», https://www.vegatel.ru/kabel/kabel-8d-fb/kabel-vegatel-8d-fb-titan-chernyj-200m — открыто 19.10.2026: VEGATEL Titan: CCA, FPE, фольга DF + оплётка 90 % (металл не указан), ПВХ; тот же кабель: vegatel.ru R11141 — 172 ₽ на сайте; строка выделена 19.10.2026 из «8D-FB» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "11,1"
   },
   "own": {
    "Артикул поставщика": "vegatel.ru: R03066",
    "Цена поставщика, ₽/м": "140,98",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://www.vegatel.ru/kabel/kabel-8d-fb/kabel-vegatel-8d-fb-titan-chernyj-200m",
    "Материал жилы": "алюминий омеднённый",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "8D-FB CU LSZH FR",
   "base": {
    "Марка": "8D-FB CU LSZH FR",
    "Тип провода": "коаксиальный",
    "Поставщик": "gsm-repiteri.ru",
    "Источник данных": "gsm-repiteri.ru арт. 3359 «Кабель 8D-FB CU LSZH FR (черный)», https://gsm-repiteri.ru/prodazha/cable/cable-8d-fb-cu-lszh-fr-chernyy — открыто 19.10.2026: оболочка LSZH, FR; строка выделена 19.10.2026 из «8D-FB Cu» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "10,30"
   },
   "own": {
    "Артикул поставщика": "gsm-repiteri.ru: 3359",
    "Цена поставщика, ₽/м": "245,90",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/cable/cable-8d-fb-cu-lszh-fr-chernyy",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + алюминий-магний, оплётка",
    "Материал оболочки": "LSZH"
   }
  },
  {
   "mark": "8D-FB CU LSZH",
   "base": {
    "Марка": "8D-FB CU LSZH",
    "Тип провода": "коаксиальный",
    "Поставщик": "gsm-repiteri.ru",
    "Источник данных": "gsm-repiteri.ru арт. 0092 «Кабель 8D-FB CU LSZH (синий)», https://gsm-repiteri.ru/prodazha/cable/cable-8d-fb-cu-lszh — открыто 19.10.2026: оболочка LSZH без FR, экран «фольга и проволочная оплётка» (металл не указан), Ø11,1; строка выделена 19.10.2026 из «8D-FB Cu» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "11,1"
   },
   "own": {
    "Артикул поставщика": "gsm-repiteri.ru: 0092",
    "Цена поставщика, ₽/м": "270,49",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/cable/cable-8d-fb-cu-lszh",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + оплётка",
    "Материал оболочки": "LSZH"
   }
  },
  {
   "mark": "8D-FB Cu ГОСТ",
   "base": {
    "Марка": "8D-FB Cu ГОСТ",
    "Тип провода": "коаксиальный",
    "Поставщик": "gsm-repiteri.ru",
    "Источник данных": "gsm-repiteri.ru арт. 4654 «Кабель VEGATEL 8D-FB Cu (ГОСТ, синий)», https://gsm-repiteri.ru/prodazha/cable/cable-vegatel-8d-fb-cu — открыто 19.10.2026: VEGATEL ГОСТ: медь, FPE, фольга DF + оплётка TCCA, ПВХ нг-LS; тот же кабель: vegatel.ru R11219 — 468 ₽ на сайте; строка выделена 19.10.2026 из «8D-FB Cu» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "11,1"
   },
   "own": {
    "Артикул поставщика": "gsm-repiteri.ru: 4654",
    "Цена поставщика, ₽/м": "360,66",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/cable/cable-vegatel-8d-fb-cu",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + алюминий омеднённый лужёный, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "10D-FB CCA PVC",
   "base": {
    "Марка": "10D-FB CCA PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "gsm-repiteri.ru",
    "Источник данных": "gsm-repiteri.ru арт. 0088 «Кабель 10D-FB CCA PVC (черный)», https://gsm-repiteri.ru/prodazha/cable/kabel-10d-fb-cca — открыто 19.10.2026: оплётка Al-Mg; строка выделена 19.10.2026 из «10D-FB» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "13,0"
   },
   "own": {
    "Артикул поставщика": "gsm-repiteri.ru: 0088",
    "Цена поставщика, ₽/м": "154,92",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/cable/kabel-10d-fb-cca",
    "Материал жилы": "алюминий омеднённый",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + алюминий-магний, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "10D-FB CCA LSZH FR",
   "base": {
    "Марка": "10D-FB CCA LSZH FR",
    "Тип провода": "коаксиальный",
    "Поставщик": "gsm-repiteri.ru",
    "Источник данных": "gsm-repiteri.ru арт. 3959 «Кабель 10D-FB CCA LSZH FR (черный)», https://gsm-repiteri.ru/prodazha/cable/cable-10d-fb-cca-lszh-fr-chernyy — открыто 19.10.2026: оболочка LSZH, FR; строка выделена 19.10.2026 из «10D-FB» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "13,0"
   },
   "own": {
    "Артикул поставщика": "gsm-repiteri.ru: 3959",
    "Цена поставщика, ₽/м": "163,11",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/cable/cable-10d-fb-cca-lszh-fr-chernyy",
    "Материал жилы": "алюминий омеднённый",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + алюминий-магний, оплётка",
    "Материал оболочки": "LSZH"
   }
  },
  {
   "mark": "10D-FB CU PVC",
   "base": {
    "Марка": "10D-FB CU PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "gsm-repiteri.ru",
    "Источник данных": "gsm-repiteri.ru арт. 0247 «Кабель 10D-FB CU PVC (черный)», https://gsm-repiteri.ru/prodazha/cable/kabel-10d-fb-cu-pvc — открыто 19.10.2026: медь BC, FPE, фольга DF + оплётка TC, ПВХ; строка выделена 19.10.2026 из «10D-FB» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "13,1"
   },
   "own": {
    "Артикул поставщика": "gsm-repiteri.ru: 0247",
    "Цена поставщика, ₽/м": "491,80",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/cable/kabel-10d-fb-cu-pvc",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + медь лужёная, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "10D-FB CU LSZH FR",
   "base": {
    "Марка": "10D-FB CU LSZH FR",
    "Тип провода": "коаксиальный",
    "Поставщик": "gsm-repiteri.ru",
    "Источник данных": "gsm-repiteri.ru арт. 3949 «Кабель 10D-FB CU LSZH FR (черный)», https://gsm-repiteri.ru/prodazha/cable/cable-10d-fb-cu-lszh-fr-chernyy — открыто 19.10.2026: оболочка LSZH, FR; строка выделена 19.10.2026 из «10D-FB» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "13,0"
   },
   "own": {
    "Артикул поставщика": "gsm-repiteri.ru: 3949",
    "Цена поставщика, ₽/м": "491,80",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/cable/cable-10d-fb-cu-lszh-fr-chernyy",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + алюминий-магний, оплётка",
    "Материал оболочки": "LSZH"
   }
  },
  {
   "mark": "10D-FB ГОСТ",
   "base": {
    "Марка": "10D-FB ГОСТ",
    "Тип провода": "коаксиальный",
    "Поставщик": "vegatel.ru",
    "Источник данных": "vegatel.ru арт. R07342 «Кабель коаксиальный VEGATEL 10D-FB (ГОСТ)», https://www.vegatel.ru/kabel/kabel-10d-fb/vegatel-10d-fb-gost — открыто 19.10.2026: VEGATEL ГОСТ: CCA, FPE, фольга DF + оплётка 95 % (металл не указан), ПВХ нг-LS; строка выделена 19.10.2026 из «10D-FB» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "13,1"
   },
   "own": {
    "Артикул поставщика": "vegatel.ru: R07342",
    "Цена поставщика, ₽/м": "269,67",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://www.vegatel.ru/kabel/kabel-10d-fb/vegatel-10d-fb-gost",
    "Материал жилы": "алюминий омеднённый",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "10D-FB Titan",
   "base": {
    "Марка": "10D-FB Titan",
    "Тип провода": "коаксиальный",
    "Поставщик": "vegatel.ru",
    "Источник данных": "vegatel.ru арт. R03791 «Кабель коаксиальный VEGATEL 10D-FB (Titan, черный)», https://www.vegatel.ru/kabel/kabel-10d-fb/titan-10d-fb — открыто 19.10.2026: VEGATEL Titan: CCA, FPE, фольга DF + оплётка 95 % (металл не указан), ПВХ светостабилизированный; строка выделена 19.10.2026 из «10D-FB» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "13,1"
   },
   "own": {
    "Артикул поставщика": "vegatel.ru: R03791",
    "Цена поставщика, ₽/м": "222,13",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://www.vegatel.ru/kabel/kabel-10d-fb/titan-10d-fb",
    "Материал жилы": "алюминий омеднённый",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "10D-FB Cu ГОСТ",
   "base": {
    "Марка": "10D-FB Cu ГОСТ",
    "Тип провода": "коаксиальный",
    "Поставщик": "gsm-repiteri.ru",
    "Источник данных": "gsm-repiteri.ru арт. 4656 «Кабель VEGATEL 10D-FB Cu (ГОСТ, синий)», https://gsm-repiteri.ru/prodazha/cable/cable-vegatel-10d-fb-cu — открыто 19.10.2026: VEGATEL ГОСТ: медь BC, FPE, фольга DF + оплётка 95 % (металл не указан), ПВХ нг-LS; тот же кабель: vegatel.ru R11172 — 997 ₽ на сайте; строка выделена 19.10.2026 из «10D-FB» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "13,1"
   },
   "own": {
    "Артикул поставщика": "gsm-repiteri.ru: 4656",
    "Цена поставщика, ₽/м": "778,69",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/cable/cable-vegatel-10d-fb-cu",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "CNT-400 LSZH FR",
   "base": {
    "Марка": "CNT-400 LSZH FR",
    "Тип провода": "коаксиальный",
    "Поставщик": "gsm-repiteri.ru",
    "Источник данных": "gsm-repiteri.ru арт. 3186 «Кабель CNT-400-FR (черный)», https://gsm-repiteri.ru/prodazha/cable/cable-cnt-400-fr-chernyy — открыто 19.10.2026: CNT-400-FR: CCA, вспененный ПЭ, фольга Al + оплётка луженая медь 90 %, безгалогенный огнестойкий полиолефин; строка выделена 19.10.2026 из «CNT-400» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "10,29"
   },
   "own": {
    "Артикул поставщика": "gsm-repiteri.ru: 3186",
    "Цена поставщика, ₽/м": "196,72",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/cable/cable-cnt-400-fr-chernyy",
    "Материал жилы": "алюминий омеднённый",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + медь лужёная, оплётка",
    "Материал оболочки": "LSZH"
   }
  },
  {
   "mark": "LMR-195 CU PE CU PVC",
   "base": {
    "Марка": "LMR-195 CU PE CU PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3542 «Кабель коаксиальный LMR-195 CU PE CU PVC», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-lmr-195-cu-pe-cu-pvc/ — открыто 19.10.2026: оплётка медь, ПВХ; строка выделена 19.10.2026 из «LMR-195» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "5"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3542",
    "Цена поставщика, ₽/м": "90,16",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-lmr-195-cu-pe-cu-pvc/",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + медь, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "LMR-195 CU PE TC PVC",
   "base": {
    "Марка": "LMR-195 CU PE TC PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3543 «Кабель коаксиальный LMR-195 CU PE TC PVC», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-lmr-195-cu-pe-tc-pvc/ — открыто 19.10.2026: оплётка луженая медь, ПВХ; строка выделена 19.10.2026 из «LMR-195» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "5,06"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3543",
    "Цена поставщика, ₽/м": "94,26",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-lmr-195-cu-pe-tc-pvc/",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + медь лужёная, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "RF 0.81 SPC PTFE CU FEP",
   "base": {
    "Марка": "RF 0.81 SPC PTFE CU FEP",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 2597 «Кабель коаксиальный RF0.81 Многожильный SPC PTFE CU FEP», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rf081-mnogozhilnyj-spc-ptfe-cu-fep/ — открыто 19.10.2026: многожильная SPC, ПТФЭ, оплётка медь; строка выделена 19.10.2026 из «RF 0.81» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "1,13"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 2597",
    "Цена поставщика, ₽/м": "122,95",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rf081-mnogozhilnyj-spc-ptfe-cu-fep/",
    "Материал жилы": "медь посеребрённая",
    "Материал диэлектрика": "ПТФЭ",
    "Материал экрана": "медь, оплётка",
    "Материал оболочки": "ФЭП"
   }
  },
  {
   "mark": "RF 1.13 SPC PTFE CU FEP",
   "base": {
    "Марка": "RF 1.13 SPC PTFE CU FEP",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 2598 «Кабель коаксиальный RF1.13 Многожильный SPC PTFE CU FEP», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rf113-mnogozhilnyj-spc-ptfe-cu-fep/ — открыто 19.10.2026: многожильная SPC, ПТФЭ, оплётка медь; строка выделена 19.10.2026 из «RF 1.13» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "1,13"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 2598",
    "Цена поставщика, ₽/м": "98,36",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rf113-mnogozhilnyj-spc-ptfe-cu-fep/",
    "Материал жилы": "медь посеребрённая",
    "Материал диэлектрика": "ПТФЭ",
    "Материал экрана": "медь, оплётка",
    "Материал оболочки": "ФЭП"
   }
  },
  {
   "mark": "RF 1.37 SPC PTFE CU FEP",
   "base": {
    "Марка": "RF 1.37 SPC PTFE CU FEP",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 2600 «Кабель коаксиальный RF1.37 Многожильный SPC PTFE CU FEP», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rf137-mnogozhilnyj-spc-ptfe-cu-fep/ — открыто 19.10.2026: многожильная SPC, ПТФЭ, оплётка медь; строка выделена 19.10.2026 из «RF 1.37» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "1,37"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 2600",
    "Цена поставщика, ₽/м": "131,15",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rf137-mnogozhilnyj-spc-ptfe-cu-fep/",
    "Материал жилы": "медь посеребрённая",
    "Материал диэлектрика": "ПТФЭ",
    "Материал экрана": "медь, оплётка",
    "Материал оболочки": "ФЭП"
   }
  },
  {
   "mark": "RF 1.48 CCS PTFE CU FEP",
   "base": {
    "Марка": "RF 1.48 CCS PTFE CU FEP",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 2601 «Кабель коаксиальный RF1.48 CCS PTFE CU FEP», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rf148-ccs-ptfe-cu-fep/ — открыто 19.10.2026: многожильная CCS, ПТФЭ, оплётка медь; строка выделена 19.10.2026 из «RF 1.48» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "1,48"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 2601",
    "Цена поставщика, ₽/м": "188,52",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rf148-ccs-ptfe-cu-fep/",
    "Материал жилы": "сталь омеднённая",
    "Материал диэлектрика": "ПТФЭ",
    "Материал экрана": "медь, оплётка",
    "Материал оболочки": "ФЭП"
   }
  },
  {
   "mark": "RG-11 с тросом",
   "base": {
    "Марка": "RG-11 с тросом",
    "Тип провода": "коаксиальный",
    "Поставщик": "gsm-repiteri.ru",
    "Источник данных": "gsm-repiteri.ru арт. 4028 «Кабель RG-11 75 Ом, с троссом», https://gsm-repiteri.ru/prodazha/cable/cable-rg-11-75-om-s-trossom — открыто 19.10.2026: CCS 1×1,63, фольга + оплётка CCA, с несущим тросом, наружная прокладка, Ø10,2; строка выделена 19.10.2026 из «RG-11» (владелец: разные материалы — разные строки)",
    "Наружный диаметр, мм": "10,2"
   },
   "own": {
    "Артикул поставщика": "gsm-repiteri.ru: 4028",
    "Цена поставщика, ₽/м": "27,05",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/cable/cable-rg-11-75-om-s-trossom",
    "Материал жилы": "сталь омеднённая",
    "Материал экрана": "алюминий, фольга + алюминий омеднённый, оплётка"
   }
  },
  {
   "mark": "RG-174 CCS PE TC PVC",
   "base": {
    "Марка": "RG-174 CCS PE TC PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 1769 «Кабель коаксиальный RG-174 CCS Solid PE TC PVC», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-174-ccs-solid-pe-tc-pvc/ — открыто 19.10.2026: CCS 7 жил, сплошной ПЭ, оплётка луженая медь, ПВХ — как типовой RG-174/U (АМЭЛ); тот же кабель: amel.ru RG-174/U — цена по запросу; строка выделена 19.10.2026 из «RG-174» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "2,65"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 1769",
    "Цена поставщика, ₽/м": "53,28",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-174-ccs-solid-pe-tc-pvc/",
    "Материал жилы": "сталь омеднённая",
    "Материал диэлектрика": "ПЭ",
    "Материал экрана": "медь лужёная, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "RG-174 CU PE CU PVC",
   "base": {
    "Марка": "RG-174 CU PE CU PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3534 «Кабель коаксиальный RG-174 Многожильный CU Solid PE CU PVC, до 1000 МГц», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-174-mnogozhilnyj-cu-solid-pe-cu-pvc-do-1000-mgcz/ — открыто 19.10.2026: медь 7 жил, ПЭ, оплётка медь; строка выделена 19.10.2026 из «RG-174» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "3"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3534",
    "Цена поставщика, ₽/м": "45,08",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-174-mnogozhilnyj-cu-solid-pe-cu-pvc-do-1000-mgcz/",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ",
    "Материал экрана": "медь, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "RG-174 CU PE TC PVC",
   "base": {
    "Марка": "RG-174 CU PE TC PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3535 «Кабель коаксиальный RG-174 Многожильный CU Solid PE TC PVC, до 1000-2000 МГц», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-174-mnogozhilnyj-cu-solid-pe-tc-pvc-do-1000-2000-mgcz/ — открыто 19.10.2026: медь 7 жил, ПЭ, оплётка луженая медь; строка выделена 19.10.2026 из «RG-174» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "2,9"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3535",
    "Цена поставщика, ₽/м": "49,18",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-174-mnogozhilnyj-cu-solid-pe-tc-pvc-do-1000-2000-mgcz/",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ",
    "Материал экрана": "медь лужёная, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "RG-178 CCS PTFE CU FEP",
   "base": {
    "Марка": "RG-178 CCS PTFE CU FEP",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 386 «Кабель коаксиальный RG-178 B/U CCS PTFE CU FEP», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-178-bu-ccs-ptfe-cu-fep/ — открыто 19.10.2026: CCS многожильная, ПТФЭ, оплётка медь; строка выделена 19.10.2026 из «RG-178» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "1,85"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 386",
    "Цена поставщика, ₽/м": "163,93",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-178-bu-ccs-ptfe-cu-fep/",
    "Материал жилы": "сталь омеднённая",
    "Материал диэлектрика": "ПТФЭ",
    "Материал экрана": "медь, оплётка",
    "Материал оболочки": "ФЭП"
   }
  },
  {
   "mark": "RG-178 CU PTFE CU FEP",
   "base": {
    "Марка": "RG-178 CU PTFE CU FEP",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3545 «Кабель коаксиальный RG-178 B/U Многожильный CU PTFE CU FEP», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-178-bu-mnogozhilnyj-cu-ptfe-cu-fep/ — открыто 19.10.2026: медь многожильная, ПТФЭ, оплётка медь; строка выделена 19.10.2026 из «RG-178» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "1,8"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3545",
    "Цена поставщика, ₽/м": "81,97",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-178-bu-mnogozhilnyj-cu-ptfe-cu-fep/",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПТФЭ",
    "Материал экрана": "медь, оплётка",
    "Материал оболочки": "ФЭП"
   }
  },
  {
   "mark": "RG-213 CU PE CU PVC",
   "base": {
    "Марка": "RG-213 CU PE CU PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3544 «Кабель коаксиальный RG-213 U Многожильный CU Solid PE CU PVC, до 1000 МГц», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-213-u-mnogozhilnyj-cu-solid-pe-cu-pvc-do-1000-mgcz/ — открыто 19.10.2026: медь многожильная, ПЭ, оплётка медь; строка выделена 19.10.2026 из «RG-213» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "10,4"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3544",
    "Цена поставщика, ₽/м": "229,51",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-213-u-mnogozhilnyj-cu-solid-pe-cu-pvc-do-1000-mgcz/",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ",
    "Материал экрана": "медь, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "RG-213 CU PE TC PVC",
   "base": {
    "Марка": "RG-213 CU PE TC PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 1374 «Кабель коаксиальный RG-213 U Многожильный CU Solid PE TC PVC», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-213-u-mnogozhilnyj-cu-solid-pe-tc-pvc/ — открыто 19.10.2026: медь многожильная, ПЭ, оплётка луженая медь; строка выделена 19.10.2026 из «RG-213» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "10,3"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 1374",
    "Цена поставщика, ₽/м": "229,51",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-213-u-mnogozhilnyj-cu-solid-pe-tc-pvc/",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ",
    "Материал экрана": "медь лужёная, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "RG-213 C/U PE",
   "base": {
    "Марка": "RG-213 C/U PE",
    "Тип провода": "коаксиальный",
    "Поставщик": "gsm-repiteri.ru",
    "Источник данных": "gsm-repiteri.ru арт. 1404 «Кабель RG-213 C/U PE», https://gsm-repiteri.ru/prodazha/cable/kabel-rg-213-c-u — открыто 19.10.2026: оболочка ПЭ; строка выделена 19.10.2026 из «RG-213 C/U» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "10,30"
   },
   "own": {
    "Артикул поставщика": "gsm-repiteri.ru: 1404",
    "Цена поставщика, ₽/м": "172,13",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/cable/kabel-rg-213-c-u",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ",
    "Материал экрана": "алюминий, фольга + алюминий омеднённый лужёный, оплётка",
    "Материал оболочки": "ПЭ"
   }
  },
  {
   "mark": "RG-316 CCS PTFE CU FEP",
   "base": {
    "Марка": "RG-316 CCS PTFE CU FEP",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 387 «Кабель коаксиальный RG-316 B/U CCS Solid PTFE CU FEP», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-316-bu-ccs-solid-ptfe-cu-fep/ — открыто 19.10.2026: CCS многожильная, ПТФЭ, оплётка медь; строка выделена 19.10.2026 из «RG-316» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "2,5"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 387",
    "Цена поставщика, ₽/м": "131,15",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-316-bu-ccs-solid-ptfe-cu-fep/",
    "Материал жилы": "сталь омеднённая",
    "Материал диэлектрика": "ПТФЭ",
    "Материал экрана": "медь, оплётка",
    "Материал оболочки": "ФЭП"
   }
  },
  {
   "mark": "RG-316 CU PTFE CU FEP",
   "base": {
    "Марка": "RG-316 CU PTFE CU FEP",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3546 «Кабель коаксиальный RG-316 B/U Многожильный CU Solid PTFE CU FEP», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-316-bu-mnogozhilnyj-cu-solid-ptfe-cu-fep/ — открыто 19.10.2026: медь многожильная, ПТФЭ, оплётка медь; строка выделена 19.10.2026 из «RG-316» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "2,5"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3546",
    "Цена поставщика, ₽/м": "127,05",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-316-bu-mnogozhilnyj-cu-solid-ptfe-cu-fep/",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПТФЭ",
    "Материал экрана": "медь, оплётка",
    "Материал оболочки": "ФЭП"
   }
  },
  {
   "mark": "RG-316 TC PTFE CU FEP",
   "base": {
    "Марка": "RG-316 TC PTFE CU FEP",
    "Тип провода": "коаксиальный",
    "Поставщик": "vegatel.ru",
    "Источник данных": "vegatel.ru арт. R01406 «Кабель коаксиальный VEGATEL RG-316 B/U, бухта 100 м», https://www.vegatel.ru/kabel/kabel-rg-316/kabel-vegatel-rg-316-b-u — открыто 19.10.2026: VEGATEL RG-316 B/U: жила TC (луженая медь), ПТФЭ, оплётка Cu 95 %, ФЭП; строка выделена 19.10.2026 из «RG-316» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "2,5"
   },
   "own": {
    "Артикул поставщика": "vegatel.ru: R01406",
    "Цена поставщика, ₽/м": "89,34",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://www.vegatel.ru/kabel/kabel-rg-316/kabel-vegatel-rg-316-b-u",
    "Материал жилы": "медь лужёная",
    "Материал диэлектрика": "ПТФЭ",
    "Материал экрана": "медь, оплётка",
    "Материал оболочки": "ФЭП"
   }
  },
  {
   "mark": "RG-400 SPC PE SPC FEP",
   "base": {
    "Марка": "RG-400 SPC PE SPC FEP",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 946 «Кабель коаксиальный RG-400 SPC PE SPC FEP», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-400-spc-pe-spc-fep/ — открыто 19.10.2026: kroks: диэлектрик «вспененный полиэтилен» (в названии и описании), оплётка SPC, ФЭП; строка выделена 19.10.2026 из «RG-400» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "5"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 946",
    "Цена поставщика, ₽/м": "459,02",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-400-spc-pe-spc-fep/",
    "Материал жилы": "медь посеребрённая",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "медь посеребрённая, оплётка",
    "Материал оболочки": "ФЭП"
   }
  },
  {
   "mark": "RG-402 SPCW PTFE SPC FEP",
   "base": {
    "Марка": "RG-402 SPCW PTFE SPC FEP",
    "Тип провода": "коаксиальный",
    "Поставщик": "amel.ru",
    "Источник данных": "amel.ru арт. RG-402 «RG-402», https://www.amel.ru/products/cables_coaxial/kabeli-serii-rg-poluzhestkie/rg-402/ — открыто 19.10.2026: АМЭЛ RG-402: SPCW, ПТФЭ, лента SPCS + оплётка SPC 97 %, ФЭП; строка выделена 19.10.2026 из «RG-402» (владелец: разные материалы — разные строки)",
    "Наружный диаметр, мм": "4,14"
   },
   "own": {
    "Артикул поставщика": "amel.ru: RG-402",
    "Ссылка": "https://www.amel.ru/products/cables_coaxial/kabeli-serii-rg-poluzhestkie/rg-402/",
    "Материал жилы": "сталь омеднённая посеребрённая",
    "Материал диэлектрика": "ПТФЭ",
    "Материал экрана": "медь посеребрённая, лента + медь посеребрённая, оплётка",
    "Материал оболочки": "ФЭП"
   }
  },
  {
   "mark": "RG-58 CU PE AL PVC",
   "base": {
    "Марка": "RG-58 CU PE AL PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 384 «Кабель коаксиальный RG-58 CU PE AL PVC», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-58-cu-pe-al-pvc/ — открыто 19.10.2026: вспененный ПЭ, фольга Al + оплётка Al; строка выделена 19.10.2026 из «RG-58» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "5,02"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 384",
    "Цена поставщика, ₽/м": "90,16",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-58-cu-pe-al-pvc/",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + алюминий, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "RG-58 CU PE CCA PVC",
   "base": {
    "Марка": "RG-58 CU PE CCA PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3538 «Кабель коаксиальный RG-58 CU PE CCA PVC», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-58-cu-pe-cca-pvc/ — открыто 19.10.2026: вспененный ПЭ, фольга Al + оплётка CCA; строка выделена 19.10.2026 из «RG-58» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "5"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3538",
    "Цена поставщика, ₽/м": "73,77",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-58-cu-pe-cca-pvc/",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + алюминий омеднённый, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "RG-58 CU PE CU PVC",
   "base": {
    "Марка": "RG-58 CU PE CU PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3536 «Кабель коаксиальный RG-58 CU PE CU PVC», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-58-cu-pe-cu-pvc/ — открыто 19.10.2026: вспененный ПЭ, фольга Al + оплётка медь; строка выделена 19.10.2026 из «RG-58» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "5,02"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3536",
    "Цена поставщика, ₽/м": "90,16",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-58-cu-pe-cu-pvc/",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + медь, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "RG-58 CU PE TC PVC",
   "base": {
    "Марка": "RG-58 CU PE TC PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 3537 «Кабель коаксиальный RG-58 CU PE TC PVC», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-58-cu-pe-tc-pvc/ — открыто 19.10.2026: вспененный ПЭ, фольга Al + оплётка TC; строка выделена 19.10.2026 из «RG-58» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "5"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 3537",
    "Цена поставщика, ₽/м": "90,16",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-58-cu-pe-tc-pvc/",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + медь лужёная, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "RG-58 A/U CU PE CCA PVC",
   "base": {
    "Марка": "RG-58 A/U CU PE CCA PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "gsm-repiteri.ru",
    "Источник данных": "gsm-repiteri.ru арт. 0448 «Кабель RG-58 A/U (черный)», https://gsm-repiteri.ru/prodazha/cable/kabel-rg-58-a-u — открыто 19.10.2026: медь, вспененный ПЭ, фольга Al + оплётка TCCA, ПВХ; строка выделена 19.10.2026 из «RG-58 A/U» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "50",
    "Наружный диаметр, мм": "5,00"
   },
   "own": {
    "Артикул поставщика": "gsm-repiteri.ru: 0448",
    "Цена поставщика, ₽/м": "32,79",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/cable/kabel-rg-58-a-u",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + алюминий омеднённый лужёный, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "RG-58 C/U TC PE TC PVC",
   "base": {
    "Марка": "RG-58 C/U TC PE TC PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "amel.ru",
    "Источник данных": "amel.ru арт. RG-58С/U PVC «RG-58С/U PVC», https://www.amel.ru/products/cables_coaxial/kabeli-serii-rg-gibkiy/rg-58s-u/ — открыто 19.10.2026: АМЭЛ RG-58C/U: жила TPC 19×0,18, ПЭ, оплётка TPC 95 %, ПВХ; строка выделена 19.10.2026 из «RG-58 C/U» (владелец: разные материалы — разные строки)",
    "Наружный диаметр, мм": "4,95"
   },
   "own": {
    "Артикул поставщика": "amel.ru: RG-58С/U PVC",
    "Ссылка": "https://www.amel.ru/products/cables_coaxial/kabeli-serii-rg-gibkiy/rg-58s-u/",
    "Материал жилы": "медь лужёная",
    "Материал диэлектрика": "ПЭ",
    "Материал экрана": "медь лужёная, оплётка 95 %",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "RG-6 CCS PE TC PE",
   "base": {
    "Марка": "RG-6 CCS PE TC PE",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 2139 «Кабель коаксиальный RG-6U CCS PE TC PE», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-6u-ccs-pe-tc-pe/ — открыто 19.10.2026: оплётка луженая медь, оболочка ПЭ; строка выделена 19.10.2026 из «RG-6 / RG-6/U» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "75",
    "Наружный диаметр, мм": "6,8"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 2139",
    "Цена поставщика, ₽/м": "40,98",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-rg-6u-ccs-pe-tc-pe/",
    "Материал жилы": "сталь омеднённая",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + медь лужёная, оплётка",
    "Материал оболочки": "ПЭ"
   }
  },
  {
   "mark": "SAT-703 CU PE TC PVC",
   "base": {
    "Марка": "SAT-703 CU PE TC PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "kroks.ru",
    "Источник данных": "kroks.ru арт. 627 «Кабель коаксиальный SAT-703 CU PE TC PVC», https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-sat-703-cu-pe-tc-pvc/ — открыто 19.10.2026: оплётка луженая медь; строка выделена 19.10.2026 из «SAT-703» (владелец: разные материалы — разные строки)",
    "Волновое сопротивление, Ом": "75",
    "Наружный диаметр, мм": "6,8"
   },
   "own": {
    "Артикул поставщика": "kroks.ru: 627",
    "Цена поставщика, ₽/м": "49,18",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://kroks.ru/shop/cable-rf/kabel-koaksialnyj-sat-703-cu-pe-tc-pvc/",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + медь лужёная, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "SAT-703 CU PE CCA PVC",
   "base": {
    "Марка": "SAT-703 CU PE CCA PVC",
    "Тип провода": "коаксиальный",
    "Поставщик": "gsm-repiteri.ru",
    "Источник данных": "gsm-repiteri.ru арт. 0735 «Кабель Sat-703 75 Ом», https://gsm-repiteri.ru/prodazha/cable-75-om/kabel-sat-703-75-om — открыто 19.10.2026: gsm: медь 0,9, вспененный ПЭ, фольга Al + оплётка луженый омеднённый алюминий 48×0,1, ПВХ; строка выделена 19.10.2026 из «SAT-703» (владелец: разные материалы — разные строки)"
   },
   "own": {
    "Артикул поставщика": "gsm-repiteri.ru: 0735",
    "Цена поставщика, ₽/м": "32,79",
    "Дата цены": "19.10.2026",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/cable-75-om/kabel-sat-703-75-om",
    "Материал жилы": "медь",
    "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + алюминий омеднённый лужёный, оплётка",
    "Материал оболочки": "ПВХ"
   }
  },
  {
   "mark": "SR-141-50 SPCW PTFE TC",
   "base": {
    "Марка": "SR-141-50 SPCW PTFE TC",
    "Тип провода": "коаксиальный",
    "Поставщик": "amel.ru",
    "Источник данных": "amel.ru арт. SR-141 «SR-141», https://www.amel.ru/products/cables_coaxial/kabeli-serii-sr-ekran-mednaya-trubka/sr-141/ — открыто 19.10.2026: АМЭЛ SR-141: SPCW, ПТФЭ, трубка TC (луженая медь), без оболочки; строка выделена 19.10.2026 из «SR-141-50» (владелец: разные материалы — разные строки)"
   },
   "own": {
    "Артикул поставщика": "amel.ru: SR-141",
    "Ссылка": "https://www.amel.ru/products/cables_coaxial/kabeli-serii-sr-ekran-mednaya-trubka/sr-141/",
    "Материал жилы": "сталь омеднённая посеребрённая",
    "Материал диэлектрика": "ПТФЭ",
    "Материал экрана": "медь лужёная, трубка"
   }
  }
 ],
 "cleared": [
  {
   "mark": "1/2″",
   "edits": [
    [
     "Поставщик",
     "gsm-repiteri.ru",
     ""
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 3132; gsm-repiteri.ru: 4150; gsm-repiteri.ru: 3174; gsm-repiteri.ru: 3298; gsm-repiteri.ru: 1978; gsm-repiteri.ru: 1781; gsm-repiteri.ru: 2246; gsm-repiteri.ru: 1585; gsm-repiteri.ru: 4721; gsm-repiteri.ru: 2247; gsm-repiteri.ru: 1327",
     ""
    ],
    [
     "Цена поставщика, ₽/м",
     "172,13",
     ""
    ],
    [
     "Дата цены",
     "15.10.2026",
     ""
    ]
   ],
   "why": "типоразмер фидера; каждый из 11 фидеров уже своей строкой (по модели) — артикулы остаются там"
  },
  {
   "mark": "1/2″ SF",
   "edits": [
    [
     "Поставщик",
     "gsm-repiteri.ru",
     ""
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 3031; gsm-repiteri.ru: 3074; gsm-repiteri.ru: 3314; gsm-repiteri.ru: 1977; gsm-repiteri.ru: 4049; gsm-repiteri.ru: 3838; gsm-repiteri.ru: 3358; gsm-repiteri.ru: 3029; gsm-repiteri.ru: 4722; gsm-repiteri.ru: 2882; gsm-repiteri.ru: 4491",
     ""
    ],
    [
     "Цена поставщика, ₽/м",
     "184,43",
     ""
    ],
    [
     "Дата цены",
     "15.10.2026",
     ""
    ]
   ],
   "why": "типоразмер фидера; каждый из 11 фидеров уже своей строкой (по модели) — артикулы остаются там"
  },
  {
   "mark": "7/8″",
   "edits": [
    [
     "Поставщик",
     "gsm-repiteri.ru",
     ""
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 3842; gsm-repiteri.ru: 2172; gsm-repiteri.ru: 3288; gsm-repiteri.ru: 1904; gsm-repiteri.ru: 3224; gsm-repiteri.ru: 1787; gsm-repiteri.ru: 4289; gsm-repiteri.ru: 2813; gsm-repiteri.ru: 1365",
     ""
    ],
    [
     "Цена поставщика, ₽/м",
     "368,85",
     ""
    ],
    [
     "Дата цены",
     "15.10.2026",
     ""
    ]
   ],
   "why": "типоразмер фидера; каждый из 9 фидеров уже своей строкой (по модели) — артикулы остаются там"
  },
  {
   "mark": "8D-FB CCA",
   "edits": [
    [
     "Поставщик",
     "gsm-repiteri.ru, kroks.ru",
     ""
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 0091; gsm-repiteri.ru: 2642; gsm-repiteri.ru: 4208; kroks.ru: 1373; kroks.ru: 3532; kroks.ru: 3533",
     ""
    ],
    [
     "Цена поставщика, ₽/м",
     "122,95",
     ""
    ],
    [
     "Дата цены",
     "15.10.2026",
     ""
    ]
   ],
   "why": "та же конструкция, что «8D-FB» (жила CCA, оплётка Al-Mg, тот же источник gsm 0091) — кабель отдан строке «8D-FB» (есть закупки); строка остаётся без продавца"
  },
  {
   "mark": "RG-6/U",
   "edits": [
    [
     "Поставщик",
     "etm.ru, gsm-repiteri.ru, kroks.ru",
     ""
    ],
    [
     "Артикул поставщика",
     "gsm-repiteri.ru: 1527; kroks.ru: 3539; kroks.ru: 2139; kroks.ru: 3540",
     ""
    ],
    [
     "Цена поставщика, ₽/м",
     "24,59",
     ""
    ],
    [
     "Дата цены",
     "15.10.2026",
     ""
    ]
   ],
   "why": "строка заведена по etm.ru (жила медь OFC, оплётка медь, оболочка ПЭ) — у kroks/gsm такого нет, все их RG-6U с жилой CCS — отданы «RG-6»"
  }
 ]
}''')

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()


def wires():
    with factory() as uow:
        return {cable_key(r.fields.get("Марка") or r.display_text): r
                for r in uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=100_000)}


records = wires()
missing = [item["mark"] for item in (*PAYLOAD["existing"], *PAYLOAD["cleared"]) if cable_key(item["mark"]) not in records]
if missing:
    print("СТОП — нет строк (ничего не записано):", ", ".join(missing))
    raise SystemExit(1)

with factory() as uow:
    has_link = LINK in custom_columns(TechOperationTab.WIRE, uow)
if not has_link:
    if dry:
        print(f"столбец «{LINK}» будет добавлен (apply)")
    else:
        count = add_column(ColumnCommand(TechOperationTab.WIRE, LINK, ACTOR, now), factory())
        print(f"столбец «{LINK}» добавлен: {count} записей")

total = {"создано строк": 0, "записано ячеек": 0, "уже так": 0, "отказ": 0}
to_create = [row for row in PAYLOAD["new"] if cable_key(row["mark"]) not in records]
if to_create:
    rows = [ImportRowIn(fields=row["base"], old=None) for row in to_create]
    data = import_rows("prov", factory(), rows, ACTOR, now, dry_run=dry, source="erp").model_dump()
    if data["updated"] or data["refused"] or len(data["created"]) != len(to_create):
        print("СТОП — загрузка новых строк не чистая:", {k: data[k] for k in ("created", "updated", "refused")})
        raise SystemExit(1)
    total["создано строк"] = len(data["created"])
    print(f"== новых строк {'будет создано' if dry else 'создано'}: {len(data['created'])}")
    if not dry:
        records = wires()


def cells(mark, edits):
    """edits: [field, old, new] — writes the cells whose value is still the old one, prints the rest."""
    record = records[cable_key(mark)]
    write, refused = [], []
    for field, old, new in edits:
        current = str(record.fields.get(field, "") or "")
        if current == new:
            total["уже так"] += 1
        elif current == old:
            write.append(FieldEdit(field, old, new))
        else:
            refused.append(f"{field}: в ячейке «{current[:80]}», ждали «{old[:80]}»")
    if refused:
        total["отказ"] += len(refused)
        print(f"   отказ «{mark}»: " + "; ".join(refused))
    if not write:
        return
    if not dry:
        try:
            edit_record(EditRecordCommand(record.id, tuple(write), ACTOR, now), factory())
        except Exception as error:  # noqa: BLE001 — one row refused, the rest go on
            total["отказ"] += len(write)
            print(f"   отказ «{mark}»: {error}")
            return
    total["записано ячеек"] += len(write)


for row in PAYLOAD["new"]:
    if cable_key(row["mark"]) not in records:  # dry run: the row appears only by apply, its own cells after it
        total["записано ячеек"] += len(row["own"])
        continue
    base = [[field, "", value] for field, value in row["base"].items() if field != "Марка"]
    cells(row["mark"], base + [[field, "", value] for field, value in row["own"].items()])
for row in PAYLOAD["existing"]:
    cells(row["mark"], row["edits"])
for row in PAYLOAD["cleared"]:
    cells(row["mark"], row["edits"])
print("ИТОГО", total, "(проверка, ничего не записано)" if dry else "(записано)")

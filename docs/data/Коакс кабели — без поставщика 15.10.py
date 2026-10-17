"""Прод 15.10.2026 — «Коаксиальные кабели» без поставщика (владелец: «так же ты не удалил без поставщика»; коаксиальные
кабели — только АМЭЛ, Крокс, gsm-repiteri, «всё остальное можно сносить»).

1. Найдено у поставщика (марка или её стандартное обозначение продаётся у трёх) → новая строка БД.ПРОВ этой марки
   (import_rows по «Марке», old="" у каждой ячейки; свои столбцы «Артикул поставщика», «Цена поставщика, ₽/м», «Дата
   цены» — правкой записи со старым значением ""), строка СПР.КАБ остаётся. Журнал — docs/data «Коакс кабели — найдены
   у поставщиков 15.10.csv».
2. Не найдено → удаляются строка СПР.КАБ и строка БД.ПРОВ марки, даже если на неё ссылаются разъёмы БД.КОАКС (прямое
   указание владельца). Разъёмы и партии склада не трогаются — они перечислены в docs/data «Коакс кабели — удалено без
   поставщика 15.10.csv» (там же все поля и ID для восстановления). Путь удаления — как в «Коакс кабели — удаление
   15.10.py»: `tech_operations.delete` + событие `references.coax_cable_removed`; запись удаляется, только если её поля
   совпадают со снимком (иначе отказ «поменялась»).

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - check < этот файл   → без поставщика: 0

Откат — строки из «Коакс кабели — удалено без поставщика 15.10.csv» или копия базы до записи."""
import json
import os
import re
import sys

from sqlalchemy import text

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import EditRecordCommand, FieldEdit, _audit, cable_catalog, edit_record
from app.modules.references.cables import CableKind
from app.modules.references.domain import BASE_COLUMNS, TechOperationTab, cable_key
from app.modules.references.importers import import_rows
from app.modules.references.load_mirror import ACTOR
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now

sys.stdout.reconfigure(encoding="utf-8")
PAYLOAD = json.loads(r'''{
 "create": [
  {
   "fields": {
    "Марка": "1/2″",
    "Тип провода": "коаксиальный фидерный",
    "Стандарт": "типоразмер фидера 1/2″",
    "Число жил": "1",
    "Волновое сопротивление, Ом": "50",
    "Поставщик": "gsm-repiteri.ru",
    "Ссылка на datasheet": "https://gsm-repiteri.ru/prodazha/cable-vegatel/cable-vegatel-cf-1-2-gost",
    "Источник данных": "по данным продавца (gsm-repiteri.ru, каталог https://gsm-repiteri.ru/prodazha/cable): «1/2″» — типоразмер фидера, не марка одного изготовителя; у продавца 11 фидеров этого типоразмера (каждый — своей строкой БД.ПРОВ): 3132 Кабель Modern Technologies MC12FZ-50 (коаксиальный фидер 1/2); 4150 Кабель Hengxin (коаксиальный фидер 1/2) (HCAAY-50-12); 3174 Кабель РК50-12-331-С (1/2\"R) (коаксиальный фидер 1/2); 3298 Коаксиальный фидер 1/2 Baltic Signal BSF-50-12 LSZH FR; 1978 Кабель РК50-12-331-Снг(С) (1/2\"R HF) (коаксиальный фидер 1/2); 1781 Кабель Hengxin (коаксиальный фидер 1/2) (HCAAYZ-50-12); 2246 Кабель Andrew Heliax LDF4-50A (коаксиальный фидер 1/2); 1585 Кабель Acome (коаксиальный фидер 1/2) (HPL 50-1/2F); 4721 Кабель фидерный VEGATEL CF-1/2 (ГОСТ); 2247 Кабель Draka RFA 1/2\"-50 (коаксиальный фидер 1/2); 1327 Кабель RFS (коаксиальный фидер 1/2) (LCF 12-50J). D1–D3 строки СПР.КАБ «1/2″» (4,8 / 11,3 / 13,8) совпадают с VEGATEL CF-1/2 (4721). Цена — наименьшая (Кабель Modern Technologies MC12FZ-50 (коаксиальный фидер 1/2), 172,13 ₽/м без НДС) — решено агентом — проверить",
    "Артикул поставщика": "gsm-repiteri.ru: 3132; gsm-repiteri.ru: 4150; gsm-repiteri.ru: 3174; gsm-repiteri.ru: 3298; gsm-repiteri.ru: 1978; gsm-repiteri.ru: 1781; gsm-repiteri.ru: 2246; gsm-repiteri.ru: 1585; gsm-repiteri.ru: 4721; gsm-repiteri.ru: 2247; gsm-repiteri.ru: 1327",
    "Цена поставщика, ₽/м": "172,13",
    "Дата цены": "15.10.2026"
   }
  },
  {
   "fields": {
    "Марка": "1/2″ SF",
    "Тип провода": "коаксиальный фидерный",
    "Стандарт": "типоразмер фидера 1/2″ SF",
    "Число жил": "1",
    "Волновое сопротивление, Ом": "50",
    "Поставщик": "gsm-repiteri.ru",
    "Ссылка на datasheet": "https://gsm-repiteri.ru/prodazha/cable/koaksialnyy-fider-1-2sf-baltic-signal-bsf-50-9-lszh-fr",
    "Источник данных": "по данным продавца (gsm-repiteri.ru, каталог https://gsm-repiteri.ru/prodazha/cable): «1/2″ SF» — типоразмер фидера, не марка одного изготовителя; у продавца 11 фидеров этого типоразмера (каждый — своей строкой БД.ПРОВ): 3031 Кабель Kingsignal (коаксиальный фидер 1/2 SF) (HCAHY-50-9); 3074 Кабель Modern MC12SFZ-50 (коаксиальный фидер 1/2 SF); 3314 Кабель (коаксиальный фидер 1/2 SF) Hengxin HRCAY-50-9; 1977 Кабель Hengxin (коаксиальный фидер 1/2 SF) (HRCAYZ-50-9); 4049 Кабель РК50-9-32-С (коаксиальный фидер 1/2 SF); 3838 Кабель РК50-9-331-С (коаксиальный фидер 1/2 SF); 3358 Коаксиальный фидер 1/2SF Baltic Signal BSF-50-9 LSZH FR; 3029 Кабель РК50-9-331-Снг(С) (1/2\"S HF) (коаксиальный фидер 1/2 SF); 4722 Кабель фидерный VEGATEL SuperFlex-1/2 (ГОСТ); 2882 Кабель RFS (коаксиальный фидер 1/2 SF) (SCF 12-50J); 4491 Кабель RFS (коаксиальный фидер 1/2 SF) (SCF 12-50JFN). D1–D3 строки СПР.КАБ «1/2″ SF» (3,6 / 9,3 / 12,3) ближе всего к Baltic Signal BSF-50-9 (3,56 / 9,3 / 12,3). Цена — наименьшая (Кабель Kingsignal (коаксиальный фидер 1/2 SF) (HCAHY-50-9), 184,43 ₽/м без НДС) — решено агентом — проверить",
    "Артикул поставщика": "gsm-repiteri.ru: 3031; gsm-repiteri.ru: 3074; gsm-repiteri.ru: 3314; gsm-repiteri.ru: 1977; gsm-repiteri.ru: 4049; gsm-repiteri.ru: 3838; gsm-repiteri.ru: 3358; gsm-repiteri.ru: 3029; gsm-repiteri.ru: 4722; gsm-repiteri.ru: 2882; gsm-repiteri.ru: 4491",
    "Цена поставщика, ₽/м": "184,43",
    "Дата цены": "15.10.2026"
   }
  },
  {
   "fields": {
    "Марка": "7/8″",
    "Тип провода": "коаксиальный фидерный",
    "Стандарт": "типоразмер фидера 7/8″",
    "Число жил": "1",
    "Волновое сопротивление, Ом": "50",
    "Поставщик": "gsm-repiteri.ru",
    "Ссылка на datasheet": "https://gsm-repiteri.ru/prodazha/cable/cable-koaksialnyy-fider-7-8-hctayz-50-22-7-8",
    "Источник данных": "по данным продавца (gsm-repiteri.ru, каталог https://gsm-repiteri.ru/prodazha/cable): «7/8″» — типоразмер фидера, не марка одного изготовителя; у продавца 9 фидеров этого типоразмера (каждый — своей строкой БД.ПРОВ): 3842 Кабель (коаксиальный фидер 7/8) HCTAYZ-50-22(7/8″); 2172 Кабель РК50-23-391-Снг(С) (7/8\"R HF) (коаксиальный фидер 7/8); 3288 Кабель Modern MC78FZ-50 (фидер HCTAYZ-50-23 7/8\" A); 1904 Кабель Commscope (коаксиальный фидер 7/8) AVA5RK-50FX; 3224 Кабель Hengxin (коаксиальный фидер 7/8) HCTAYZ-50-23(7/8\"A); 1787 Кабель Acome (коаксиальный фидер 7/8) (HPL 50-7/8 LA); 4289 Коаксиальный фидер 7/8 Baltic Signal BSF-50-23 LSZH FR; 2813 Кабель Hengxin (коаксиальный фидер 7/8) HCTAY-50-23(7/8\"A); 1365 Кабель RFS (коаксиальный фидер 7/8) (LCF 78-50J). D1–D3 строки СПР.КАБ «7/8″» (9,1 / 21,5 / 25,2) — по RFS; у продавца размеры есть только у Baltic Signal BSF-50-23 (9 / 22,5 / 24,9). Цена — наименьшая (Кабель (коаксиальный фидер 7/8) HCTAYZ-50-22(7/8″), 368,85 ₽/м без НДС) — решено агентом — проверить",
    "Артикул поставщика": "gsm-repiteri.ru: 3842; gsm-repiteri.ru: 2172; gsm-repiteri.ru: 3288; gsm-repiteri.ru: 1904; gsm-repiteri.ru: 3224; gsm-repiteri.ru: 1787; gsm-repiteri.ru: 4289; gsm-repiteri.ru: 2813; gsm-repiteri.ru: 1365",
    "Цена поставщика, ₽/м": "368,85",
    "Дата цены": "15.10.2026"
   }
  },
  {
   "fields": {
    "Марка": "8D-FB CCA",
    "Тип провода": "коаксиальный",
    "Стандарт": "JIS 8D-FB (вариант CCA)",
    "Жила: проволок × диаметр, мм": "1×2,56 (медненный алюминий, сплошная)",
    "Число жил": "1",
    "Цвета": "чёрный",
    "Наружный диаметр, мм": "10,15",
    "Изоляция": "ПЭ вспененный",
    "Экран": "фольга алюминиевая + оплётка алюминий-магний",
    "Волновое сопротивление, Ом": "50",
    "Темп. от, °C": "-60",
    "Темп. до, °C": "75",
    "Масса, г/м": "115",
    "Поставщик": "gsm-repiteri.ru, kroks.ru",
    "Ссылка на datasheet": "https://gsm-repiteri.ru/prodazha/cable/kabel-8d-fb-cca",
    "Источник данных": "по данным продавца (gsm-repiteri.ru «Кабель 8D-FB CCA PVC (черный)», 0091): характеристики как у строки «8D-FB» (тот же кабель с жилой из омеднённого алюминия); 8D-FB CCA продают gsm-repiteri.ru (0091, 2642, 4208) и kroks.ru (1373, 3532, 3533); строка оставлена отдельной — на «8D-FB CCA» ссылается разъём БД.КОАКС; цена — наименьшая",
    "Артикул поставщика": "gsm-repiteri.ru: 0091; gsm-repiteri.ru: 2642; gsm-repiteri.ru: 4208; kroks.ru: 1373; kroks.ru: 3532; kroks.ru: 3533",
    "Цена поставщика, ₽/м": "122,95",
    "Дата цены": "15.10.2026"
   }
  },
  {
   "fields": {
    "Марка": "RG-6",
    "Тип провода": "коаксиальный",
    "Жила: проволок × диаметр, мм": "1×1,02 (сталь омеднённая, сплошная)",
    "Число жил": "1",
    "Цвета": "белый",
    "Наружный диаметр, мм": "6,8",
    "Изоляция": "ПЭ вспененный / оболочка ПВХ",
    "Экран": "фольга алюминиевая + оплётка алюминий-магний",
    "Волновое сопротивление, Ом": "75",
    "Темп. от, °C": "-60",
    "Темп. до, °C": "70",
    "Масса, г/м": "46",
    "Поставщик": "gsm-repiteri.ru, kroks.ru",
    "Ссылка на datasheet": "https://gsm-repiteri.ru/files/2024/04/cable-rg-6u-specifikacii.pdf",
    "Источник данных": "по данным продавца (gsm-repiteri.ru «Кабель RG-6U 75 Ом», 1527): https://gsm-repiteri.ru/prodazha/cable-75-om/kabel-rg-6-75-om; жила CCS Ø1,02, диэлектрик вспененный ПЭ Ø4,57, экран Ø5,20, оболочка ПВХ Ø6,8, 75±3 Ом, затухание 21,5 дБ/100 м на 1000 МГц, 9,2 кг/200 м; kroks.ru продаёт как «RG-6U» (3539, 2139, 3540); та же марка, что строка «RG-6/U» — оставлена отдельной, на «RG-6» ссылаются 20 разъёмов БД.КОАКС; цена — наименьшая",
    "Артикул поставщика": "gsm-repiteri.ru: 1527; kroks.ru: 3539; kroks.ru: 2139; kroks.ru: 3540",
    "Цена поставщика, ₽/м": "24,59",
    "Дата цены": "15.10.2026"
   }
  },
  {
   "fields": {
    "Марка": "RG-59",
    "Тип провода": "коаксиальный",
    "Жила: проволок × диаметр, мм": "1×0,58 (медь, сплошная)",
    "Число жил": "1",
    "Цвета": "чёрный",
    "Наружный диаметр, мм": "6,1",
    "Изоляция": "ПЭ / оболочка ПВХ",
    "Экран": "оплётка медь",
    "Волновое сопротивление, Ом": "75",
    "Масса, г/м": "40",
    "Поставщик": "gsm-repiteri.ru",
    "Ссылка на datasheet": "https://gsm-repiteri.ru/prodazha/cable-75-om/cable-rg-59-b-u-75-om",
    "Источник данных": "по данным продавца (gsm-repiteri.ru «Кабель RG-59 B/U 75 Ом», 1421): жила медь сплошная Ø0,58, диэлектрик ПЭ, оплётка медь 112 %, оболочка ПВХ Ø6,1, 4 кг/100 м; та же марка, что строка «RG-59 B/U»; у строки СПР.КАБ «RG-59» D1 = 0,81 (у продавца жила 0,58), D2/D3 совпадают с «RG-59 B/U» — D не менялись — решено агентом — проверить",
    "Артикул поставщика": "gsm-repiteri.ru: 1421",
    "Цена поставщика, ₽/м": "27,05",
    "Дата цены": "15.10.2026"
   }
  }
 ],
 "delete": [
  {
   "tab": "kab_coax",
   "id": "7fdce23b-a24e-422a-8b5b-29a44a68d6e1",
   "key": "3C-2V",
   "fields": {
    "Кабель": "3C-2V",
    "D1": "0,5",
    "D2": "3,1",
    "D3": ""
   },
   "reason": "нет в каталогах АМЭЛ, Крокс, gsm-repiteri; разъёмов БД.КОАКС на марку: 2"
  },
  {
   "tab": "kab_coax",
   "id": "7ad74233-8250-4916-b035-78af62b7c719",
   "key": "DG-113",
   "fields": {
    "Кабель": "DG-113",
    "D1": "1,13",
    "D2": "4,80",
    "D3": "5,30"
   },
   "reason": "нет в каталогах АМЭЛ, Крокс, gsm-repiteri; разъёмов БД.КОАКС на марку: 1"
  },
  {
   "tab": "kab_coax",
   "id": "9b3ffb51-dc86-44cb-8902-cdf5b6ace7ca",
   "key": "DX-1000",
   "fields": {
    "Кабель": "DX-1000",
    "D1": "2,6",
    "D2": "7,11",
    "D3": ""
   },
   "reason": "нет в каталогах АМЭЛ, Крокс, gsm-repiteri; разъёмов БД.КОАКС на марку: 4"
  },
  {
   "tab": "kab_coax",
   "id": "95dd8c1c-3795-491b-b890-e837601ed6b3",
   "key": "DX-10A",
   "fields": {
    "Кабель": "DX-10A",
    "D1": "2,6",
    "D2": "7,24",
    "D3": ""
   },
   "reason": "нет в каталогах АМЭЛ, Крокс, gsm-repiteri; разъёмов БД.КОАКС на марку: 4"
  },
  {
   "tab": "kab_coax",
   "id": "b3ef2583-f95a-435d-ac0c-ff3b419efe86",
   "key": "H-1000",
   "fields": {
    "Кабель": "H-1000",
    "D1": "2,62",
    "D2": "7,15",
    "D3": "7,8"
   },
   "reason": "нет в каталогах АМЭЛ, Крокс, gsm-repiteri (аналог по размеру — LMR-400 / MFC-400 UF, не та же марка); разъёмов БД.КОАКС на марку: 4"
  },
  {
   "tab": "kab_coax",
   "id": "01eeaa1e-9884-41e7-a8ae-59c8cf24f0fb",
   "key": "LMR-200",
   "fields": {
    "Кабель": "LMR-200",
    "D1": "1,12",
    "D2": "2,95",
    "D3": "3,66"
   },
   "reason": "нет в каталогах АМЭЛ, Крокс, gsm-repiteri (аналог — АМЭЛ MFC-200 UF («аналог LMR»), отдельная марка в базе); разъёмов БД.КОАКС на марку: 1"
  },
  {
   "tab": "kab_coax",
   "id": "79db5ad9-1dc1-4bb4-aa4b-ea84ec453096",
   "key": "PicoCell 8D/FB",
   "fields": {
    "Кабель": "PicoCell 8D/FB",
    "D1": "2,6",
    "D2": "7,5",
    "D3": ""
   },
   "reason": "нет в каталогах АМЭЛ, Крокс, gsm-repiteri (аналог — 8D-FB (gsm-repiteri.ru, kroks.ru), другой изготовитель); разъёмов БД.КОАКС на марку: 1"
  },
  {
   "tab": "kab_coax",
   "id": "50a46938-1e94-4799-9a25-35a04a6b1105",
   "key": "RF 1.47",
   "fields": {
    "Кабель": "RF 1.47",
    "D1": "0,31",
    "D2": "0,86",
    "D3": "1,3"
   },
   "reason": "нет в каталогах АМЭЛ, Крокс, gsm-repiteri (ближайшая — RF 1.48 (kroks.ru, gsm-repiteri.ru)); разъёмов БД.КОАКС на марку: 0"
  },
  {
   "tab": "kab_coax",
   "id": "68743637-49b7-482d-ad01-f6ef1c4f83a1",
   "key": "RG-141",
   "fields": {
    "Кабель": "RG-141",
    "D1": "0,94",
    "D2": "3",
    "D3": "3,45"
   },
   "reason": "нет в каталогах АМЭЛ, Крокс, gsm-repiteri; разъёмов БД.КОАКС на марку: 1"
  },
  {
   "tab": "kab_coax",
   "id": "ef965ac2-6b75-4d8c-8586-9c42a0bebb96",
   "key": "RG-8",
   "fields": {
    "Кабель": "RG-8",
    "D1": "2,16",
    "D2": "7,24",
    "D3": "8,08"
   },
   "reason": "нет в каталогах АМЭЛ, Крокс, gsm-repiteri (аналог — RG-213 (kroks.ru, gsm-repiteri.ru), жила 2,26 против 2,16); разъёмов БД.КОАКС на марку: 4"
  },
  {
   "tab": "kab_coax",
   "id": "99ec4b29-7c19-4d10-91d3-487bf4c42248",
   "key": "RG-8X",
   "fields": {
    "Кабель": "RG-8X",
    "D1": "1,47",
    "D2": "3,94",
    "D3": ""
   },
   "reason": "нет в каталогах АМЭЛ, Крокс, gsm-repiteri; разъёмов БД.КОАКС на марку: 1"
  },
  {
   "tab": "kab_coax",
   "id": "1683981e-76cd-438c-93d7-16a87a5bb9ea",
   "key": "RP-913B",
   "fields": {
    "Кабель": "RP-913B",
    "D1": "1,13",
    "D2": "4,75",
    "D3": "5,30"
   },
   "reason": "нет в каталогах АМЭЛ, Крокс, gsm-repiteri; разъёмов БД.КОАКС на марку: 1"
  },
  {
   "tab": "kab_coax",
   "id": "0e4c38b2-4201-420b-99d1-75784be8ede3",
   "key": "SAT-50",
   "fields": {
    "Кабель": "SAT-50",
    "D1": "1,00",
    "D2": "4,80",
    "D3": "5,27"
   },
   "reason": "нет в каталогах АМЭЛ, Крокс, gsm-repiteri (аналог — SAT-703 (kroks.ru, gsm-repiteri.ru)); разъёмов БД.КОАКС на марку: 2"
  },
  {
   "tab": "kab_coax",
   "id": "d691edfa-8f50-4275-9011-09c5a79429bf",
   "key": "SAT-752F",
   "fields": {
    "Кабель": "SAT-752F",
    "D1": "1,13",
    "D2": "4,80",
    "D3": "5,30"
   },
   "reason": "нет в каталогах АМЭЛ, Крокс, gsm-repiteri (аналог — SAT-703); разъёмов БД.КОАКС на марку: 1"
  },
  {
   "tab": "kab_coax",
   "id": "dbfab12e-f872-4aca-8fa9-22a99dbecd61",
   "key": "РК50-1,5-21",
   "fields": {
    "Кабель": "РК50-1,5-21",
    "D1": "0,54",
    "D2": "1,5",
    "D3": ""
   },
   "reason": "нет в каталогах АМЭЛ, Крокс, gsm-repiteri; разъёмов БД.КОАКС на марку: 1"
  },
  {
   "tab": "kab_coax",
   "id": "25b3beaf-4b7b-48c2-90e8-4a7821669683",
   "key": "РК50-4-11",
   "fields": {
    "Кабель": "РК50-4-11",
    "D1": "1,37",
    "D2": "4,6",
    "D3": ""
   },
   "reason": "нет в каталогах АМЭЛ, Крокс, gsm-repiteri; разъёмов БД.КОАКС на марку: 2"
  },
  {
   "tab": "prov",
   "id": "61ec645c-eef6-4996-b2c4-b0fea4dd88b1",
   "key": "РК50-2-11",
   "fields": {
    "Марка": "РК50-2-11",
    "Тип провода": "коаксиальный",
    "Стандарт": "ГОСТ 15565-2012",
    "Производители": "",
    "Сечение, мм2": "",
    "AWG": "",
    "Жила: проволок × диаметр, мм": "1×0,67 (медь, сплошная)",
    "Число жил": "1",
    "Цвета": "",
    "Наружный диаметр, мм": "3,2",
    "Изоляция": "ПЭ / оболочка ПВХ",
    "Экран": "оплётка медь 90 %",
    "Волновое сопротивление, Ом": "50",
    "Темп. от, °C": "-50",
    "Темп. до, °C": "70",
    "Напряжение, В": "",
    "Ток, А": "",
    "Масса, г/м": "19,7",
    "Закупок": "1",
    "Цена посл., ₽/м без НДС": "59,8",
    "Поставщик": "",
    "Ссылка на datasheet": "https://paritet.su/catalog/rk_50_2/paraks_rk_50_2_11/",
    "Источник данных": "paritet.su карточка РК50-2-11: жила медь сплошная Ø0,67мм, диэлектрик ПЭ 90% Ø2,25мм, оплётка медная 90%, оболочка ПВХ Ø3,2мм, 50 Ом, затухание 104дБ/мкВ@5-30МГц/100м, ёмкость 9,6пФ/м, -50…+70°C, влажность до 98%@35°C, масса 19,7г/м, ГОСТ 15565-2012, для систем телевещания",
    "Категория": "",
    "Артикул поставщика": "",
    "Цена поставщика, ₽/м": "",
    "Дата цены": ""
   },
   "reason": "нет в каталогах АМЭЛ, Крокс, gsm-repiteri (у АМЭЛ только РК50-2-22); разъёмов БД.КОАКС на марку: 1"
  },
  {
   "tab": "kab_coax",
   "id": "1e9aa66d-2dd6-4ecc-9efd-82c511dcb112",
   "key": "РК50-2-11",
   "fields": {
    "Кабель": "РК50-2-11",
    "D1": "0,67",
    "D2": "2,2",
    "D3": ""
   },
   "reason": "нет в каталогах АМЭЛ, Крокс, gsm-repiteri (у АМЭЛ только РК50-2-22); разъёмов БД.КОАКС на марку: 1"
  },
  {
   "tab": "prov",
   "id": "82052caa-8e87-4a71-bfed-0c39a81bb317",
   "key": "Sucoform 141 Cu FEP",
   "fields": {
    "Марка": "Sucoform 141 Cu FEP",
    "Тип провода": "коаксиальный полужёсткий",
    "Стандарт": "",
    "Производители": "Huber+Suhner SUCOFORM_141_FEP (RG402-типоразмер)",
    "Сечение, мм2": "",
    "AWG": "",
    "Жила: проволок × диаметр, мм": "1×0,94 (посеребр. медь, сплошная)",
    "Число жил": "1",
    "Цвета": "",
    "Наружный диаметр, мм": "",
    "Изоляция": "ПТФЭ",
    "Экран": "оплётка медь лужёная 99 %",
    "Волновое сопротивление, Ом": "50",
    "Темп. от, °C": "",
    "Темп. до, °C": "165",
    "Напряжение, В": "1900",
    "Ток, А": "",
    "Масса, г/м": "40",
    "Закупок": "2",
    "Цена посл., ₽/м без НДС": "0,0",
    "Поставщик": "",
    "Ссылка на datasheet": "https://www.mouser.com/datasheet/2/829/HUBER_2bSUHNER_SUCOFORM_141_FEP_DataSheet-1489508.pdf",
    "Источник данных": "Huber+Suhner SUCOFORM_141_FEP datasheet (Mouser): формуемый полужёсткий коаксиал RG402-размера, жила 0,94мм посеребр.медь, диэлектрик ПТФЭ 2,95мм, оплётка луж.медь 99%, 50 Ом, 165°C, раб.напряжение ≤1,9кВ (исп. 5кВ), масса 4кг/100м=40г/м. Наружный диаметр с FEP-оболочкой (вариант «Cu FEP» покупателя) отдельно не подтверждён — оставлен пустым — решено агентом — проверить. Давальческое — цена 0,0 корректна; бывал давальческим",
    "Категория": "",
    "Артикул поставщика": "",
    "Цена поставщика, ₽/м": "",
    "Дата цены": ""
   },
   "reason": "нет в каталогах АМЭЛ, Крокс, gsm-repiteri (аналог — АМЭЛ SUCOFORM 141 / SF-141 FEP, отдельные марки); разъёмов БД.КОАКС на марку: 0"
  }
 ]
}''')

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply", "check"):
    raise SystemExit("режим: dry | apply | check")
dry = mode != "apply"
engine = build_engine(os.environ["ERP_DATABASE_URL"])
factory = build_references_uow_factory(engine)
now = moscow_now()
SHEET = set(BASE_COLUMNS[TechOperationTab.WIRE])


def tab_list(tab):
    with factory() as uow:
        return uow.tech_operations.list(tab=tab, query="", limit=100_000)


def wires():
    return {cable_key(r.fields.get("Марка") or r.display_text): r for r in tab_list(TechOperationTab.WIRE)}


def no_supplier():
    rows = [r for r in cable_catalog(factory()).rows if r.kind == CableKind.COAX]
    bad = [r for r in rows if not (r.record and str(r.record.fields.get("Поставщик") or "").strip())]
    return len(rows), [r for r in bad if r.record or r.dia], [r for r in bad if not (r.record or r.dia)]


total = {"created": 0, "own_written": 0, "own_refused": 0, "skipped": 0, "deleted": 0, "missing": 0, "refused": 0}
if mode != "check":
    # 1. matches: a БД.ПРОВ row for each D-table mark found at a supplier
    records = wires()
    sheet_rows, own_cells = [], []
    for item in PAYLOAD["create"]:
        mark = item["fields"]["Марка"]
        if cable_key(mark) in records:
            total["skipped"] += 1
            print(f"   пропуск «{mark}»: строка БД.ПРОВ уже есть")
            continue
        sheet = {k: v for k, v in item["fields"].items() if k == "Марка" or k in SHEET}
        own = {k: v for k, v in item["fields"].items() if k != "Марка" and k not in SHEET}
        sheet_rows.append(ImportRowIn(fields=sheet, old={k: "" for k in sheet if k != "Марка"}))
        own_cells.append((mark, own))
    if sheet_rows:
        data = import_rows("prov", factory(), sheet_rows, ACTOR, now, dry_run=dry, source="erp").model_dump()
        total["created"] += len(data["created"])
        for refused in data["refused"]:
            print("   отказ:", refused)
    fresh = wires()
    for mark, own in own_cells:
        record = fresh.get(cable_key(mark))
        if record is None:
            total["own_written"] += len(own)  # dry run: the row is created only by apply
            continue
        stale = [k for k in own if str(record.fields.get(k, "")) != ""]
        if stale:
            total["own_refused"] += len(own)
            print(f"   отказ «{mark}»: в {', '.join(stale)} уже значение — не тронуто")
            continue
        if not dry:
            edit_record(EditRecordCommand(record.id, tuple(FieldEdit(k, "", v) for k, v in own.items()), ACTOR, now), factory())
        total["own_written"] += len(own)

    # 2. deletes: only a record whose fields equal the snapshot; references are listed, never touched
    connectors = tab_list(TechOperationTab.COAX)
    with engine.connect() as conn:
        lots = [dict(r._mapping) for r in conn.execute(text("select warehouse, grn, article, name, inbound, written_off from warehouse_lots"))]
    with factory() as uow:
        for item in PAYLOAD["delete"]:
            tab = TechOperationTab(item["tab"])
            record = next((r for r in uow.tech_operations.list(tab=tab, query="", limit=100_000) if str(r.id) == item["id"]), None)
            if record is None:
                total["missing"] += 1
                print(f"   нет записи {item['tab']} «{item['key']}» ({item['id']})")
                continue
            if dict(record.fields) != item["fields"]:
                total["refused"] += 1
                print(f"   отказ {item['tab']} «{item['key']}»: запись поменялась — не удалена")
                continue
            k = cable_key(item["key"])
            refs = [c.fields.get("Артикул", "") for c in connectors if cable_key(c.fields.get("Провод")) == k]
            pattern = re.compile(r"(?<![a-zа-я0-9])" + r"[\s-]*".join(re.escape(ch) for ch in k) + r"(?![a-z0-9])", re.I)
            lot_refs = [f"{l['warehouse']} {l['grn']} «{l['article']}»" for l in lots if pattern.search(f"{l['article']} | {l['name']}".lower())]
            print(f"   {'удалить' if dry else 'удалено'} {item['tab']} «{item['key']}»: разъёмов {len(refs)} {refs}; партии {lot_refs}")
            if not dry:
                uow.tech_operations.delete(record.id)
                _audit("references.coax_cable_removed", ACTOR, now, {
                    "record_id": str(record.id), "tab": record.tab.value, "display_text": record.display_text,
                    "fields": dict(record.fields), "reason": item["reason"] + " (владелец 15.10.2026: удалить даже со ссылками разъёмов)",
                    "connectors": refs}, uow)
            total["deleted"] += 1
        if not dry:
            uow.commit()

count, bad, cards_only = no_supplier()
print(f"Коаксиальные кабели: строк {count}; без поставщика (БД.ПРОВ / СПР.КАБ): {len(bad)} {[r.cable for r in bad]}")
print(f"   только карточка кабеля (не БД.ПРОВ / СПР.КАБ, не тронуты): {len(cards_only)} {[r.cable for r in cards_only]}")
print("ИТОГО", total, {"dry": "(проверка, ничего не записано)", "check": "(только чтение)", "apply": "(записано)"}[mode])

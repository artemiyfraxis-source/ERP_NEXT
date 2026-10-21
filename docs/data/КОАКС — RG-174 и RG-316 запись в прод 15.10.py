"""Запись в прод 15.10.2026 — БД.КОАКС: разъёмы RG-174 ↔ RG-316 (владелец: «проверить, что все разъёмы, которые идут на
RG-174, идут так же и на RG-316, и наоборот»).

Совместимость в БД.КОАКС — строка на пару «Артикул × Провод» (натуральный ключ coaxArticle + coaxWire), D1–D3 в строке —
кабеля (СПР.КАБ). Скрипт добавляет только новые строки-двойники: копия строки разъёма (артикул, тип, производитель,
L1–L3, пин/экран, карточка) с другим кабелем и D1–D3 этого кабеля из СПР.КАБ; источник совместимости дописан в
«Источник данных». Ничего не переименовывает, не удаляет и не правит существующие строки. Порядок (live-data protocol,
prod-reference-write):

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
      → создано 16, отказов 0
  sudo -n bash erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → все 16 «уже есть»

Строка пропускается, если пара уже есть или строка-образец на сервере изменилась после выгрузки. Откатный журнал —
docs/data/«КОАКС — RG-174 и RG-316 15.10.csv» (+ .md)."""
import json
import os
import sys

from app.database import build_engine, build_references_uow_factory
from app.modules.references.domain import TechOperationTab, cable_key
from app.modules.references.importers import import_rows
from app.modules.references.load_mirror import ACTOR
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now

sys.stdout.reconfigure(encoding="utf-8")
PAYLOAD = json.loads(r'''
[
 {
  "source": {
   "article": "SMB-KW3",
   "cable": "RG-174",
   "fields": {
    "coaxArticle": "SMB-KW3",
    "coaxType": "SMB-female R/A",
    "coaxMfr": "АМЭЛ",
    "coaxWire": "RG-174",
    "Артикул": "SMB-KW3",
    "Тип/Серия": "SMB-female R/A",
    "Провод": "RG-174",
    "Производитель": "АМЭЛ",
    "Программа": "",
    "D1": "0,48",
    "D2": "1,52",
    "D3": "1,9",
    "L1": "2,7",
    "L2": "3,4",
    "L3": "8,6",
    "L+": "",
    "L-": "",
    "Тип пина": "",
    "Тип экрана": "",
    "Артикул поставщика": "amel.ru: SMB-KW3",
    "Источник данных": "L1–L3 примерно: по Amphenol RF, Assembly Instructions C77 (MCX), 919-104P-51AX angle plug (RG-174/188/316) — https://atta.szlcsc.com/upload/public/pdf/source/231023/342-ff58958e80c7a32fa4e602c5a86989e5.pdf — аналог для SMB-female R/A на RG-174"
   }
  },
  "fields": {
   "coaxArticle": "SMB-KW3",
   "coaxType": "SMB-female R/A",
   "coaxMfr": "АМЭЛ",
   "coaxWire": "RG-316",
   "Артикул": "SMB-KW3",
   "Тип/Серия": "SMB-female R/A",
   "Провод": "RG-316",
   "Производитель": "АМЭЛ",
   "D1": "0,5",
   "D2": "1,48",
   "D3": "1,95",
   "L1": "2,7",
   "L2": "3,4",
   "L3": "8,6",
   "Артикул поставщика": "amel.ru: SMB-KW3",
   "Источник данных": "L1–L3 примерно: по Amphenol RF, Assembly Instructions C77 (MCX), 919-104P-51AX angle plug (RG-174/188/316) — https://atta.szlcsc.com/upload/public/pdf/source/231023/342-ff58958e80c7a32fa4e602c5a86989e5.pdf — аналог для SMB-female R/A на RG-316; строка для RG-316 добавлена 15.10.2026 по совместимости (копия строки «SMB-KW3» / RG-174, D1–D3 из СПР.КАБ RG-316): amel.ru SMB-KW3 (код 154A-7502): «Применяемый кабель RG-174/U, RG-188/U, RG-316/U» — https://amel.ru/products/coaxial_connectors/vilki-kabelnye-uglovye/smb-kw3/"
  }
 },
 {
  "source": {
   "article": "SMP-KW3",
   "cable": "RG-174",
   "fields": {
    "coaxArticle": "SMP-KW3",
    "coaxType": "SMP-female R/A",
    "coaxMfr": "АМЭЛ",
    "coaxWire": "RG-174",
    "Артикул": "SMP-KW3",
    "Тип/Серия": "SMP-female R/A",
    "Провод": "RG-174",
    "Производитель": "АМЭЛ",
    "Программа": "",
    "D1": "0,48",
    "D2": "1,52",
    "D3": "1,9",
    "L1": "",
    "L2": "",
    "L3": "",
    "L+": "",
    "L-": "",
    "Тип пина": "",
    "Тип экрана": "",
    "Артикул поставщика": "amel.ru: SMP-KW3"
   }
  },
  "fields": {
   "coaxArticle": "SMP-KW3",
   "coaxType": "SMP-female R/A",
   "coaxMfr": "АМЭЛ",
   "coaxWire": "RG-316",
   "Артикул": "SMP-KW3",
   "Тип/Серия": "SMP-female R/A",
   "Провод": "RG-316",
   "Производитель": "АМЭЛ",
   "D1": "0,5",
   "D2": "1,48",
   "D3": "1,95",
   "Артикул поставщика": "amel.ru: SMP-KW3",
   "Источник данных": "строка для RG-316 добавлена 15.10.2026 по совместимости (копия строки «SMP-KW3» / RG-174, D1–D3 из СПР.КАБ RG-316): amel.ru SMP-KW3 (код 134A-7002): «RG174A/U, RG188A/U, RG316/U» — https://amel.ru/products/coaxial_connectors/rozetki-kabelnye-uglovye-5391/smp-kw3/"
  }
 },
 {
  "source": {
   "article": "MMCX-male R/A RG-174",
   "cable": "RG-174",
   "fields": {
    "coaxArticle": "MMCX-male R/A RG-174",
    "coaxType": "MMCX-male R/A",
    "coaxMfr": "LenoRF",
    "coaxWire": "RG-174",
    "Артикул": "MMCX-male R/A RG-174",
    "Тип/Серия": "MMCX-male R/A",
    "Провод": "RG-174",
    "Производитель": "LenoRF",
    "Программа": "",
    "D1": "0,48",
    "D2": "1,52",
    "D3": "1,9",
    "L1": "0,5",
    "L2": "3",
    "L3": "7,5",
    "L+": "",
    "L-": "",
    "Тип пина": "пайка",
    "Тип экрана": "обжим",
    "Артикул поставщика": ""
   }
  },
  "fields": {
   "coaxArticle": "MMCX-male R/A RG-316",
   "coaxType": "MMCX-male R/A",
   "coaxMfr": "LenoRF",
   "coaxWire": "RG-316",
   "Артикул": "MMCX-male R/A RG-316",
   "Тип/Серия": "MMCX-male R/A",
   "Провод": "RG-316",
   "Производитель": "LenoRF",
   "D1": "0,5",
   "D2": "1,48",
   "D3": "1,95",
   "L1": "0,5",
   "L2": "3",
   "L3": "7,5",
   "Тип пина": "пайка",
   "Тип экрана": "обжим",
   "Источник данных": "строка для RG-316 добавлена 15.10.2026 по совместимости (копия строки «MMCX-male R/A RG-174» / RG-174, D1–D3 из СПР.КАБ RG-316): строки LenoRF названы «<тип> <кабель>» (9 пар RG-174/RG-316 уже есть); угловой обжимной MMCX-male на RG-174/RG-316: kroks.ru 412 — https://kroks.ru/shop/connectors/razemy/rf-connector-mmcx-male-crimp-rg174/ ; gsm-repiteri.ru 13-121L — https://gsm-repiteri.ru/prodazha/razyomi-mcx-mmcx/razyom-13-121l-mmcx-male-obzhimnoy-uglovoy-na-cable-rg-174-rg-316 ; amel.ru MMCX-JW3 — https://amel.ru/products/coaxial_connectors/vilki-kabelnye-uglovye-3215/mmcx-jw3/"
  }
 },
 {
  "source": {
   "article": "F-male RG-316",
   "cable": "RG-316",
   "fields": {
    "coaxArticle": "F-male RG-316",
    "coaxType": "F-male",
    "coaxMfr": "",
    "coaxWire": "RG-316",
    "Артикул": "F-male RG-316",
    "Тип/Серия": "F-male",
    "Провод": "RG-316",
    "Производитель": "",
    "Программа": "",
    "D1": "0,5",
    "D2": "1,48",
    "D3": "1,95",
    "L1": "6,4",
    "L2": "12,7",
    "L3": "",
    "L+": "",
    "L-": "",
    "Тип пина": "обжим",
    "Тип экрана": "обжим",
    "Артикул поставщика": "kroks.ru: 419",
    "Источник данных": "L1–L3 примерно: по C-Tec2 (Liberty AV), спецификация: стандартная разделка F-штекера ¼″ × ¼″ для RG-59/RG-6 — жила 6,4, ещё 6,4 диэлектрика, оплётка отгибается на оболочку — https://shop.ccisolutions.com/StoreFront/jsp/pdf/LIB-CM-RG6L-BNC_specifications.pdf — аналог для F-male на RG-316"
   }
  },
  "fields": {
   "coaxArticle": "F-male RG-316",
   "coaxType": "F-male",
   "coaxWire": "RG-174",
   "Артикул": "F-male RG-316",
   "Тип/Серия": "F-male",
   "Провод": "RG-174",
   "D1": "0,48",
   "D2": "1,52",
   "D3": "1,9",
   "L1": "6,4",
   "L2": "12,7",
   "Тип пина": "обжим",
   "Тип экрана": "обжим",
   "Артикул поставщика": "kroks.ru: 419",
   "Источник данных": "L1–L3 примерно: по C-Tec2 (Liberty AV), спецификация: стандартная разделка F-штекера ¼″ × ¼″ для RG-59/RG-6 — жила 6,4, ещё 6,4 диэлектрика, оплётка отгибается на оболочку — https://shop.ccisolutions.com/StoreFront/jsp/pdf/LIB-CM-RG6L-BNC_specifications.pdf — аналог для F-male на RG-174; строка для RG-174 добавлена 15.10.2026 по совместимости (копия строки «F-male RG-316» / RG-316, D1–D3 из СПР.КАБ RG-174): kroks.ru 419: «Разъем F(male) на кабели RG-174, RG-316, обжимной» — https://kroks.ru/shop/connectors/razemy/rf-f-connectormale-rg174-rg316-crimp/"
  }
 },
 {
  "source": {
   "article": "N-female RG-316",
   "cable": "RG-316",
   "fields": {
    "coaxArticle": "N-female RG-316",
    "coaxType": "N-female",
    "coaxMfr": "",
    "coaxWire": "RG-316",
    "Артикул": "N-female RG-316",
    "Тип/Серия": "N-female",
    "Провод": "RG-316",
    "Производитель": "",
    "Программа": "",
    "D1": "0,5",
    "D2": "1,48",
    "D3": "1,95",
    "L1": "4",
    "L2": "8",
    "L3": "16",
    "L+": "",
    "L-": "",
    "Тип пина": "обжим",
    "Тип экрана": "обжим",
    "Артикул поставщика": "kroks.ru: 656",
    "Источник данных": "L1–L3 примерно: по Coax Connectors Ltd 25-006-B3-AD (N crimp plug, RG-174/316): a 4, b 8, c 16, d 12 — https://coax-connectors.com/wp-content/uploads/2021/08/25-006-B3-AD-REV-B00-1.pdf — аналог для N-female на RG-316"
   }
  },
  "fields": {
   "coaxArticle": "N-female RG-316",
   "coaxType": "N-female",
   "coaxWire": "RG-174",
   "Артикул": "N-female RG-316",
   "Тип/Серия": "N-female",
   "Провод": "RG-174",
   "D1": "0,48",
   "D2": "1,52",
   "D3": "1,9",
   "L1": "4",
   "L2": "8",
   "L3": "16",
   "Тип пина": "обжим",
   "Тип экрана": "обжим",
   "Артикул поставщика": "kroks.ru: 656",
   "Источник данных": "L1–L3 примерно: по Coax Connectors Ltd 25-006-B3-AD (N crimp plug, RG-174/316): a 4, b 8, c 16, d 12 — https://coax-connectors.com/wp-content/uploads/2021/08/25-006-B3-AD-REV-B00-1.pdf — аналог для N-female на RG-174; строка для RG-174 добавлена 15.10.2026 по совместимости (копия строки «N-female RG-316» / RG-316, D1–D3 из СПР.КАБ RG-174): kroks.ru 656: «Разъем N-211L N(female) на кабели RG-174, RG-316, обжимной» — https://kroks.ru/shop/connectors/razemy/rf-connector-n-female-crimp-rg174/"
  }
 },
 {
  "source": {
   "article": "RP-SMA-male R/A RG-316",
   "cable": "RG-316",
   "fields": {
    "coaxArticle": "RP-SMA-male R/A RG-316",
    "coaxType": "RP-SMA-male R/A",
    "coaxMfr": "",
    "coaxWire": "RG-316",
    "Артикул": "RP-SMA-male R/A RG-316",
    "Тип/Серия": "RP-SMA-male R/A",
    "Провод": "RG-316",
    "Производитель": "",
    "Программа": "",
    "D1": "0,5",
    "D2": "1,48",
    "D3": "1,95",
    "L1": "2,6",
    "L2": "5,5",
    "L3": "12",
    "L+": "",
    "L-": "",
    "Тип пина": "обжим",
    "Тип экрана": "обжим",
    "Артикул поставщика": "kroks.ru: 1087",
    "Источник данных": "L1–L3 примерно: по Amphenol RF, Assembly Instructions C53 (SMA), 901-9872 angle plug (RG-174/179/187/188/316) — https://cdn.components.ru/media/PDF/8013478.pdf — аналог для RP-SMA-male R/A на RG-316"
   }
  },
  "fields": {
   "coaxArticle": "RP-SMA-male R/A RG-316",
   "coaxType": "RP-SMA-male R/A",
   "coaxWire": "RG-174",
   "Артикул": "RP-SMA-male R/A RG-316",
   "Тип/Серия": "RP-SMA-male R/A",
   "Провод": "RG-174",
   "D1": "0,48",
   "D2": "1,52",
   "D3": "1,9",
   "L1": "2,6",
   "L2": "5,5",
   "L3": "12",
   "Тип пина": "обжим",
   "Тип экрана": "обжим",
   "Артикул поставщика": "kroks.ru: 1087",
   "Источник данных": "L1–L3 примерно: по Amphenol RF, Assembly Instructions C53 (SMA), 901-9872 angle plug (RG-174/179/187/188/316) — https://cdn.components.ru/media/PDF/8013478.pdf — аналог для RP-SMA-male R/A на RG-174; строка для RG-174 добавлена 15.10.2026 по совместимости (копия строки «RP-SMA-male R/A RG-316» / RG-316, D1–D3 из СПР.КАБ RG-174): kroks.ru 1087: «Разъем RP-SMA(male) на кабели RG-174, RG-316, обжимной, угловой» — https://kroks.ru/shop/connectors/razemy/razem-rp-smamale-rg174-rg316-obzhimnoi-uglovoi/"
  }
 },
 {
  "source": {
   "article": "SMA-Female R/A RG316",
   "cable": "RG-316",
   "fields": {
    "coaxArticle": "SMA-Female R/A RG316",
    "coaxType": "SMA-female R/A",
    "coaxMfr": "",
    "coaxWire": "RG-316",
    "Артикул": "SMA-Female R/A RG316",
    "Тип/Серия": "SMA-female R/A",
    "Провод": "RG-316",
    "Производитель": "",
    "Программа": "",
    "D1": "0,5",
    "D2": "1,48",
    "D3": "1,95",
    "L1": "2,6",
    "L2": "5,5",
    "L3": "12",
    "L+": "",
    "L-": "",
    "Тип пина": "обжим",
    "Тип экрана": "обжим",
    "Артикул поставщика": "kroks.ru: 1106",
    "Источник данных": "L1–L3 примерно: по Amphenol RF, Assembly Instructions C53 (SMA), 901-9872 angle plug (RG-174/179/187/188/316) — https://cdn.components.ru/media/PDF/8013478.pdf — аналог для SMA-female R/A на RG-316"
   }
  },
  "fields": {
   "coaxArticle": "SMA-Female R/A RG316",
   "coaxType": "SMA-female R/A",
   "coaxWire": "RG-174",
   "Артикул": "SMA-Female R/A RG316",
   "Тип/Серия": "SMA-female R/A",
   "Провод": "RG-174",
   "D1": "0,48",
   "D2": "1,52",
   "D3": "1,9",
   "L1": "2,6",
   "L2": "5,5",
   "L3": "12",
   "Тип пина": "обжим",
   "Тип экрана": "обжим",
   "Артикул поставщика": "kroks.ru: 1106",
   "Источник данных": "L1–L3 примерно: по Amphenol RF, Assembly Instructions C53 (SMA), 901-9872 angle plug (RG-174/179/187/188/316) — https://cdn.components.ru/media/PDF/8013478.pdf — аналог для SMA-female R/A на RG-174; строка для RG-174 добавлена 15.10.2026 по совместимости (копия строки «SMA-Female R/A RG316» / RG-316, D1–D3 из СПР.КАБ RG-174): kroks.ru 1106: «Разъем SMA(female) на кабели RG-174, RG-178, RG-316, обжимной, угловой» — https://kroks.ru/shop/connectors/razemy/razem-smafemale-rg174-rg316-obzhimnoi-uglovoi/"
  }
 },
 {
  "source": {
   "article": "SMB-male RG-316",
   "cable": "RG-316",
   "fields": {
    "coaxArticle": "SMB-male RG-316",
    "coaxType": "SMB-male",
    "coaxMfr": "",
    "coaxWire": "RG-316",
    "Артикул": "SMB-male RG-316",
    "Тип/Серия": "SMB-male",
    "Провод": "RG-316",
    "Производитель": "",
    "Программа": "",
    "D1": "0,5",
    "D2": "1,48",
    "D3": "1,95",
    "L1": "3,5",
    "L2": "5",
    "L3": "12,5",
    "L+": "",
    "L-": "",
    "Тип пина": "обжим",
    "Тип экрана": "обжим",
    "Артикул поставщика": "kroks.ru: 831",
    "Источник данных": "L1–L3 примерно: по Coax Connectors Ltd 32-005-D3-AD (SMB crimp plug, RG-174/316): a 3,5, b 7,5, c 12,5, d 9 — https://coax-connectors.com/wp-content/uploads/2021/08/32-005-D3-AD-REV-A01-1.pdf — тот же ряд и кабель, изготовитель другой"
   }
  },
  "fields": {
   "coaxArticle": "SMB-male RG-316",
   "coaxType": "SMB-male",
   "coaxWire": "RG-174",
   "Артикул": "SMB-male RG-316",
   "Тип/Серия": "SMB-male",
   "Провод": "RG-174",
   "D1": "0,48",
   "D2": "1,52",
   "D3": "1,9",
   "L1": "3,5",
   "L2": "5",
   "L3": "12,5",
   "Тип пина": "обжим",
   "Тип экрана": "обжим",
   "Артикул поставщика": "kroks.ru: 831",
   "Источник данных": "L1–L3 примерно: по Coax Connectors Ltd 32-005-D3-AD (SMB crimp plug, RG-174/316): a 3,5, b 7,5, c 12,5, d 9 — https://coax-connectors.com/wp-content/uploads/2021/08/32-005-D3-AD-REV-A01-1.pdf — тот же ряд и кабель, изготовитель другой; строка для RG-174 добавлена 15.10.2026 по совместимости (копия строки «SMB-male RG-316» / RG-316, D1–D3 из СПР.КАБ RG-174): kroks.ru 831: «Разъем SMB(male) на кабели RG-174, RG-316, обжимной» — https://kroks.ru/shop/connectors/razemy/razem-smbmale-rg174-rg316-obzhimnoi/"
  }
 },
 {
  "source": {
   "article": "TNC-Female RG316",
   "cable": "RG-316",
   "fields": {
    "coaxArticle": "TNC-Female RG316",
    "coaxType": "TNC-female bulkhead",
    "coaxMfr": "",
    "coaxWire": "RG-316",
    "Артикул": "TNC-Female RG316",
    "Тип/Серия": "TNC-female bulkhead",
    "Провод": "RG-316",
    "Производитель": "",
    "Программа": "",
    "D1": "0,5",
    "D2": "1,48",
    "D3": "1,95",
    "L1": "4",
    "L2": "6,4",
    "L3": "15,1",
    "L+": "",
    "L-": "",
    "Тип пина": "обжим",
    "Тип экрана": "обжим",
    "Артикул поставщика": "kroks.ru: 1008",
    "Источник данных": "L1–L3 примерно: по Amphenol RF, Assembly Instructions C36 (TNC), 31-2318 bulkhead jack (RG-174/188/316) — https://cdn.components.ru/media/PDF/8022964.pdf — тот же ряд и кабель, изготовитель другой"
   }
  },
  "fields": {
   "coaxArticle": "TNC-Female RG316",
   "coaxType": "TNC-female bulkhead",
   "coaxWire": "RG-174",
   "Артикул": "TNC-Female RG316",
   "Тип/Серия": "TNC-female bulkhead",
   "Провод": "RG-174",
   "D1": "0,48",
   "D2": "1,52",
   "D3": "1,9",
   "L1": "4",
   "L2": "6,4",
   "L3": "15,1",
   "Тип пина": "обжим",
   "Тип экрана": "обжим",
   "Артикул поставщика": "kroks.ru: 1008",
   "Источник данных": "L1–L3 примерно: по Amphenol RF, Assembly Instructions C36 (TNC), 31-2318 bulkhead jack (RG-174/188/316) — https://cdn.components.ru/media/PDF/8022964.pdf — тот же ряд и кабель, изготовитель другой; строка для RG-174 добавлена 15.10.2026 по совместимости (копия строки «TNC-Female RG316» / RG-316, D1–D3 из СПР.КАБ RG-174): kroks.ru 1008: «Разъем T-231L TNC(female) обжимной под кабели RG-174, RG-316, на корпус…» — https://kroks.ru/shop/connectors/razemy/razem-tncfemale-rg174-rg316-paika-na-korpus-germetichnyi-s-rezinovym-uplotnitelem-s-gaikoi-rezba-12-28-s-lyskoi/"
  }
 },
 {
  "source": {
   "article": "Kroks-412",
   "cable": "RG-316",
   "fields": {
    "coaxArticle": "Kroks-412",
    "coaxType": "MMCX-male R/A",
    "coaxMfr": "Kroks",
    "coaxWire": "RG-316",
    "Артикул": "Kroks-412",
    "Тип/Серия": "MMCX-male R/A",
    "Провод": "RG-316",
    "Производитель": "Kroks",
    "Программа": "",
    "D1": "0,5",
    "D2": "1,48",
    "D3": "1,95",
    "L1": "1,3",
    "L2": "2,9",
    "L3": "7,2",
    "L+": "",
    "L-": "",
    "Тип пина": "обжим",
    "Тип экрана": "обжим",
    "Артикул поставщика": "",
    "Источник данных": "L1–L3 примерно: по Amphenol RF, Assembly Instructions C72 (MMCX), 908-43300 angle plug (RG-174/188/316): d 4,32, e 5,97, f 7,24 — https://assets.rs-online.com/v1698848859/Datasheets/90841f9b17d9dc8be1ec91ecb758408f.pdf — тот же ряд и кабель, изготовитель другой"
   }
  },
  "fields": {
   "coaxArticle": "Kroks-412",
   "coaxType": "MMCX-male R/A",
   "coaxMfr": "Kroks",
   "coaxWire": "RG-174",
   "Артикул": "Kroks-412",
   "Тип/Серия": "MMCX-male R/A",
   "Провод": "RG-174",
   "Производитель": "Kroks",
   "D1": "0,48",
   "D2": "1,52",
   "D3": "1,9",
   "L1": "1,3",
   "L2": "2,9",
   "L3": "7,2",
   "Тип пина": "обжим",
   "Тип экрана": "обжим",
   "Источник данных": "L1–L3 примерно: по Amphenol RF, Assembly Instructions C72 (MMCX), 908-43300 angle plug (RG-174/188/316): d 4,32, e 5,97, f 7,24 — https://assets.rs-online.com/v1698848859/Datasheets/90841f9b17d9dc8be1ec91ecb758408f.pdf — тот же ряд и кабель, изготовитель другой; строка для RG-174 добавлена 15.10.2026 по совместимости (копия строки «Kroks-412» / RG-316, D1–D3 из СПР.КАБ RG-174): kroks.ru 412: «Разъем MMCX(male) на кабели RG-174, RG-316, угловой, обжимной» — https://kroks.ru/shop/connectors/razemy/rf-connector-mmcx-male-crimp-rg174/"
  }
 },
 {
  "source": {
   "article": "MCX-JW3",
   "cable": "RG-316",
   "fields": {
    "coaxArticle": "MCX-JW3",
    "coaxType": "MCX-male R/A",
    "coaxMfr": "",
    "coaxWire": "RG-316",
    "Артикул": "MCX-JW3",
    "Тип/Серия": "MCX-male R/A",
    "Провод": "RG-316",
    "Производитель": "",
    "Программа": "",
    "D1": "0,5",
    "D2": "1,48",
    "D3": "1,95",
    "L1": "2,7",
    "L2": "3,4",
    "L3": "8,6",
    "L+": "",
    "L-": "",
    "Тип пина": "обжим",
    "Тип экрана": "обжим",
    "Артикул поставщика": "amel.ru: MCX-JW3",
    "Источник данных": "продавец kroks.ru (было в «Производитель»)"
   }
  },
  "fields": {
   "coaxArticle": "MCX-JW3",
   "coaxType": "MCX-male R/A",
   "coaxWire": "RG-174",
   "Артикул": "MCX-JW3",
   "Тип/Серия": "MCX-male R/A",
   "Провод": "RG-174",
   "D1": "0,48",
   "D2": "1,52",
   "D3": "1,9",
   "L1": "2,7",
   "L2": "3,4",
   "L3": "8,6",
   "Тип пина": "обжим",
   "Тип экрана": "обжим",
   "Артикул поставщика": "amel.ru: MCX-JW3",
   "Источник данных": "продавец kroks.ru (было в «Производитель»); строка для RG-174 добавлена 15.10.2026 по совместимости (копия строки «MCX-JW3» / RG-316, D1–D3 из СПР.КАБ RG-174): amel.ru MCX-JW3 (код 123A-7502): «Применяемый кабель RG-174/U, RG-188/U, RG-316/U» — https://amel.ru/products/coaxial_connectors/vilki-kabelnye-uglovye-3212/mcx-jw3/"
  }
 },
 {
  "source": {
   "article": "MMCX-JW3",
   "cable": "RG-316",
   "fields": {
    "coaxArticle": "MMCX-JW3",
    "coaxType": "MMCX-male R/A",
    "coaxMfr": "",
    "coaxWire": "RG-316",
    "Артикул": "MMCX-JW3",
    "Тип/Серия": "MMCX-male R/A",
    "Провод": "RG-316",
    "Производитель": "",
    "Программа": "",
    "D1": "0,5",
    "D2": "1,48",
    "D3": "1,95",
    "L1": "1,3",
    "L2": "2,9",
    "L3": "7,2",
    "L+": "",
    "L-": "",
    "Тип пина": "обжим",
    "Тип экрана": "обжим",
    "Артикул поставщика": "amel.ru: MMCX-JW3",
    "Источник данных": "L1–L3 примерно: по Amphenol RF, Assembly Instructions C72 (MMCX), 908-43300 angle plug (RG-174/188/316): d 4,32, e 5,97, f 7,24 — https://assets.rs-online.com/v1698848859/Datasheets/90841f9b17d9dc8be1ec91ecb758408f.pdf — тот же ряд и кабель, изготовитель другой"
   }
  },
  "fields": {
   "coaxArticle": "MMCX-JW3",
   "coaxType": "MMCX-male R/A",
   "coaxWire": "RG-174",
   "Артикул": "MMCX-JW3",
   "Тип/Серия": "MMCX-male R/A",
   "Провод": "RG-174",
   "D1": "0,48",
   "D2": "1,52",
   "D3": "1,9",
   "L1": "1,3",
   "L2": "2,9",
   "L3": "7,2",
   "Тип пина": "обжим",
   "Тип экрана": "обжим",
   "Артикул поставщика": "amel.ru: MMCX-JW3",
   "Источник данных": "L1–L3 примерно: по Amphenol RF, Assembly Instructions C72 (MMCX), 908-43300 angle plug (RG-174/188/316): d 4,32, e 5,97, f 7,24 — https://assets.rs-online.com/v1698848859/Datasheets/90841f9b17d9dc8be1ec91ecb758408f.pdf — тот же ряд и кабель, изготовитель другой; строка для RG-174 добавлена 15.10.2026 по совместимости (копия строки «MMCX-JW3» / RG-316, D1–D3 из СПР.КАБ RG-174): amel.ru MMCX-JW3 (код 113A-7502): «Применяемый кабель RG-174/U, RG-188/U, RG-316/U» — https://amel.ru/products/coaxial_connectors/vilki-kabelnye-uglovye-3215/mmcx-jw3/"
  }
 },
 {
  "source": {
   "article": "SMA-JW3",
   "cable": "RG-316",
   "fields": {
    "coaxArticle": "SMA-JW3",
    "coaxType": "SMA-male R/A",
    "coaxMfr": "",
    "coaxWire": "RG-316",
    "Артикул": "SMA-JW3",
    "Тип/Серия": "SMA-male R/A",
    "Провод": "RG-316",
    "Производитель": "",
    "Программа": "",
    "D1": "0,5",
    "D2": "1,48",
    "D3": "1,95",
    "L1": "2,6",
    "L2": "5,5",
    "L3": "12",
    "L+": "",
    "L-": "",
    "Тип пина": "обжим",
    "Тип экрана": "обжим",
    "Артикул поставщика": "amel.ru: SMA-JW3",
    "Источник данных": "продавец kroks.ru (было в «Производитель»); L1–L3 примерно: по Amphenol RF, Assembly Instructions C53 (SMA), 901-9872 angle plug (RG-174/179/187/188/316) — https://cdn.components.ru/media/PDF/8013478.pdf — тот же ряд и кабель, изготовитель другой"
   }
  },
  "fields": {
   "coaxArticle": "SMA-JW3",
   "coaxType": "SMA-male R/A",
   "coaxWire": "RG-174",
   "Артикул": "SMA-JW3",
   "Тип/Серия": "SMA-male R/A",
   "Провод": "RG-174",
   "D1": "0,48",
   "D2": "1,52",
   "D3": "1,9",
   "L1": "2,6",
   "L2": "5,5",
   "L3": "12",
   "Тип пина": "обжим",
   "Тип экрана": "обжим",
   "Артикул поставщика": "amel.ru: SMA-JW3",
   "Источник данных": "продавец kroks.ru (было в «Производитель»); L1–L3 примерно: по Amphenol RF, Assembly Instructions C53 (SMA), 901-9872 angle plug (RG-174/179/187/188/316) — https://cdn.components.ru/media/PDF/8013478.pdf — тот же ряд и кабель, изготовитель другой; строка для RG-174 добавлена 15.10.2026 по совместимости (копия строки «SMA-JW3» / RG-316, D1–D3 из СПР.КАБ RG-174): amel.ru SMA-JW3 (код 183A-7502): «Применяемый кабель RG-174/U, RG-188/U, RG-316/U» — https://amel.ru/products/coaxial_connectors/vilki-kabelnye-uglovye-3224/sma-jw3/"
  }
 },
 {
  "source": {
   "article": "TNC-JW3Y",
   "cable": "RG-316",
   "fields": {
    "coaxArticle": "TNC-JW3Y",
    "coaxType": "TNC-male R/A",
    "coaxMfr": "",
    "coaxWire": "RG-316",
    "Артикул": "TNC-JW3Y",
    "Тип/Серия": "TNC-male R/A",
    "Провод": "RG-316",
    "Производитель": "",
    "Программа": "",
    "D1": "0,5",
    "D2": "1,48",
    "D3": "1,95",
    "L1": "3,2",
    "L2": "8,3",
    "L3": "14,7",
    "L+": "",
    "L-": "",
    "Тип пина": "обжим",
    "Тип экрана": "обжим",
    "Артикул поставщика": "amel.ru: TNC-JW3Y",
    "Источник данных": "L1–L3 примерно: по Amphenol RF, Assembly Instructions C36 (TNC), 31-2381 angle plug (RG-55/142/223) — https://cdn.components.ru/media/PDF/8022964.pdf — аналог для TNC-male R/A на RG-316"
   }
  },
  "fields": {
   "coaxArticle": "TNC-JW3Y",
   "coaxType": "TNC-male R/A",
   "coaxWire": "RG-174",
   "Артикул": "TNC-JW3Y",
   "Тип/Серия": "TNC-male R/A",
   "Провод": "RG-174",
   "D1": "0,48",
   "D2": "1,52",
   "D3": "1,9",
   "L1": "3,2",
   "L2": "8,3",
   "L3": "14,7",
   "Тип пина": "обжим",
   "Тип экрана": "обжим",
   "Артикул поставщика": "amel.ru: TNC-JW3Y",
   "Источник данных": "L1–L3 примерно: по Amphenol RF, Assembly Instructions C36 (TNC), 31-2381 angle plug (RG-55/142/223) — https://cdn.components.ru/media/PDF/8022964.pdf — аналог для TNC-male R/A на RG-174; строка для RG-174 добавлена 15.10.2026 по совместимости (копия строки «TNC-JW3Y» / RG-316, D1–D3 из СПР.КАБ RG-174): amel.ru TNC-JW3Y (код 263B-7501): «Применяемый кабель RG174A/U, RG188A/U, RG316/U» — https://amel.ru/products/coaxial_connectors/vilki-kabelnye-uglovye-5510/tnc-jw3y/"
  }
 },
 {
  "source": {
   "article": "Fakra Z-Female",
   "cable": "RG-316",
   "fields": {
    "coaxArticle": "Fakra Z-Female",
    "coaxType": "Fakra-female",
    "coaxMfr": "",
    "coaxWire": "RG-316",
    "Артикул": "Fakra Z-Female",
    "Тип/Серия": "Fakra-female",
    "Провод": "RG-316",
    "Производитель": "",
    "Программа": "",
    "D1": "0,5",
    "D2": "1,48",
    "D3": "1,95",
    "L1": "3",
    "L2": "4",
    "L3": "9",
    "L+": "",
    "L-": "",
    "Тип пина": "пайка",
    "Тип экрана": "обжим",
    "Артикул поставщика": "",
    "Источник данных": "L1–L3 примерно: по Coax Connectors Ltd 32-054-B3-AD (SMB crimp jack, RG-174/316): a 3, b 5, c 9, d 6 — https://coax-connectors.com/wp-content/uploads/2021/08/32-054-B3-AD-REV-A00-1.pdf — аналог для Fakra-female на RG-316"
   }
  },
  "fields": {
   "coaxArticle": "Fakra Z-Female",
   "coaxType": "Fakra-female",
   "coaxWire": "RG-174",
   "Артикул": "Fakra Z-Female",
   "Тип/Серия": "Fakra-female",
   "Провод": "RG-174",
   "D1": "0,48",
   "D2": "1,52",
   "D3": "1,9",
   "L1": "3",
   "L2": "4",
   "L3": "9",
   "Тип пина": "пайка",
   "Тип экрана": "обжим",
   "Источник данных": "L1–L3 примерно: по Coax Connectors Ltd 32-054-B3-AD (SMB crimp jack, RG-174/316): a 3, b 5, c 9, d 6 — https://coax-connectors.com/wp-content/uploads/2021/08/32-054-B3-AD-REV-A00-1.pdf — аналог для Fakra-female на RG-174; строка для RG-174 добавлена 15.10.2026 по совместимости (копия строки «Fakra Z-Female» / RG-316, D1–D3 из СПР.КАБ RG-174): своего поставщика у строки нет; оба предложения этого типа у gsm-repiteri.ru — на RG-174 и RG-316: 3166 — https://gsm-repiteri.ru/prodazha/razyomi/razyom-fakra-tip-z-water-blue-5021 ; 4335 — https://gsm-repiteri.ru/prodazha/razyomi/razyom-fakra-tip-z-water-blue-5021-female-obzhimnoy-dlya-kabelya-rg-174"
  }
 },
 {
  "source": {
   "article": "RP-SMA-female RG-316",
   "cable": "RG-316",
   "fields": {
    "coaxArticle": "RP-SMA-female RG-316",
    "coaxType": "RP-SMA-female",
    "coaxMfr": "",
    "coaxWire": "RG-316",
    "Артикул": "RP-SMA-female RG-316",
    "Тип/Серия": "RP-SMA-female",
    "Провод": "RG-316",
    "Производитель": "",
    "Программа": "",
    "D1": "0,5",
    "D2": "1,48",
    "D3": "1,95",
    "L1": "2,9",
    "L2": "3,9",
    "L3": "10,5",
    "L+": "",
    "L-": "",
    "Тип пина": "пайка",
    "Тип экрана": "обжим",
    "Артикул поставщика": "",
    "Источник данных": "L1–L3 примерно: по Amphenol RF, Assembly Instructions C53 (SMA), 901-10021-3SF jack (RG-174/188/316) — https://cdn.components.ru/media/PDF/8013478.pdf — аналог для RP-SMA-female на RG-316"
   }
  },
  "fields": {
   "coaxArticle": "RP-SMA-female RG-316",
   "coaxType": "RP-SMA-female",
   "coaxWire": "RG-174",
   "Артикул": "RP-SMA-female RG-316",
   "Тип/Серия": "RP-SMA-female",
   "Провод": "RG-174",
   "D1": "0,48",
   "D2": "1,52",
   "D3": "1,9",
   "L1": "2,9",
   "L2": "3,9",
   "L3": "10,5",
   "Тип пина": "пайка",
   "Тип экрана": "обжим",
   "Источник данных": "L1–L3 примерно: по Amphenol RF, Assembly Instructions C53 (SMA), 901-10021-3SF jack (RG-174/188/316) — https://cdn.components.ru/media/PDF/8013478.pdf — аналог для RP-SMA-female на RG-174; строка для RG-174 добавлена 15.10.2026 по совместимости (копия строки «RP-SMA-female RG-316» / RG-316, D1–D3 из СПР.КАБ RG-174): своего поставщика у строки нет; тот же тип у gsm-repiteri.ru — S-A211L (2745) «RP-SMA-female, обжимной, на кабель RG-174, RG-316» — https://gsm-repiteri.ru/prodazha/razyomi/razyom-s-a211l"
  }
 }
]''')

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()


def pairs():
    with factory() as uow:
        return {(str(r.fields.get("coaxArticle") or "").strip().casefold(), cable_key(r.fields.get("coaxWire"))): r
                for r in uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100_000)}


def key(article, cable):
    return article.strip().casefold(), cable_key(cable)


stored = pairs()
print("строк БД.КОАКС:", len(stored))
rows, skipped = [], 0
for item in PAYLOAD:
    fields, source = item["fields"], item["source"]
    target = key(fields["coaxArticle"], fields["coaxWire"])
    if target in stored:
        print(f"   уже есть «{fields['coaxArticle']}» / {fields['coaxWire']}")
        skipped += 1
        continue
    record = stored.get(key(source["article"], source["cable"]))
    if record is None:
        print(f"   пропуск «{fields['coaxArticle']}»: нет строки-образца «{source['article']}» / {source['cable']}")
        skipped += 1
        continue
    now_fields = {k: str(v) for k, v in record.fields.items() if str(v or "").strip()}
    if now_fields != {k: str(v) for k, v in source["fields"].items() if str(v or "").strip()}:
        print(f"   пропуск «{fields['coaxArticle']}»: строка-образец изменилась после выгрузки")
        skipped += 1
        continue
    rows.append(ImportRowIn(fields=fields))
counts = {"created": 0, "updated": 0, "unchanged": 0, "refused": 0}
if rows:
    data = import_rows("coax", factory(), rows, ACTOR, now, dry_run=dry, source="erp").model_dump()
    counts = {"created": len(data["created"]), "updated": len(data["updated"]), "unchanged": data["unchanged"],
              "refused": len(data["refused"])}
    for item in data["refused"]:
        print("   отказ:", item)
    for item in data["updated"]:
        print("   ВНИМАНИЕ правка:", item)
print("ИТОГО", counts, "пропущено", skipped, "(проверка, ничего не записано)" if dry else "(записано)")
if not dry:
    after = pairs()
    print("строк БД.КОАКС после:", len(after), "было:", len(stored))

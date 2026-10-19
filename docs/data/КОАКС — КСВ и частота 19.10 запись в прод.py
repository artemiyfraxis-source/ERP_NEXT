"""Запись в прод 19.10.2026 — БД.КОАКС: «КСВ» и пустая «Частота до, ГГц» по карточкам поставщиков (АМЭЛ, Kroks,
gsm-repiteri, chipdip), владелец 19.10: «КСВ найти к этим разъёмам». Только пустые ячейки (old = ""), ключ —
Артикул + Кабель. Нужен код #552 (поле «КСВ» карточки). Откатный журнал — «КОАКС — КСВ и частота 19.10.csv».

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash erp.sh backup-now
  ... python - apply < этот файл ;  ... python - dry < этот файл  (→ всё «отказ»: уже записано)"""
import json, os, sys
from app.database import build_engine, build_references_uow_factory
from app.modules.references.importers import COAX_CARD_COLUMNS, import_rows
from app.modules.references.load_mirror import ACTOR
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now

sys.stdout.reconfigure(encoding="utf-8")
ROWS = json.loads(r'''[
 {
  "fields": {
   "coaxArticle": "4.3-10-female для 1/2″",
   "coaxWire": "1/2″",
   "КСВ": "≤1,15"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "4.3-10-male для 1/2″",
   "coaxWire": "1/2″",
   "КСВ": "≤1,15"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "716M-LCF12-C02",
   "coaxWire": "1/2″",
   "КСВ": "≤1,08",
   "Частота до, ГГц": "3,7"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "Andrew 540EZDM",
   "coaxWire": "1/2″",
   "КСВ": "≤1,29"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "Acome C0059X",
   "coaxWire": "1/2″",
   "КСВ": "≤1,1"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-112C/FC-1/2",
   "coaxWire": "1/2″",
   "КСВ": "≤1,1 (0–3 ГГц)"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "NM-1/2",
   "coaxWire": "1/2″",
   "КСВ": "≤1,1 (0–3 ГГц)"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "RFS NM-LCF12-060",
   "coaxWire": "1/2″",
   "КСВ": "1,03"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "Acome C0032X",
   "coaxWire": "1/2″ SF",
   "КСВ": "<1,1"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "DF-1/2SF",
   "coaxWire": "1/2″ SF",
   "КСВ": "≤1,1"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "Acome C0044X",
   "coaxWire": "1/2″ SF",
   "КСВ": "<1,1"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "4.3-10-male для 1/2″ SuperFlex (⌀13,5 мм)",
   "coaxWire": "1/2″ SF",
   "КСВ": "≤1,15"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "4.3-10-female для 1/2″ SuperFlex",
   "coaxWire": "1/2″ SF",
   "КСВ": "≤1,15"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "NM-1/2SF",
   "coaxWire": "1/2″ SF",
   "КСВ": "≤1,1"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N1-211/5D",
   "coaxWire": "5D-FB",
   "Частота до, ГГц": "3"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "Kroks-2950",
   "coaxWire": "5D-FB",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "RP-SMA-male 5D-FB",
   "coaxWire": "5D-FB",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-Female 5D-FB",
   "coaxWire": "5D-FB",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "S-M111C",
   "coaxWire": "5D-FB",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "S-121/5D",
   "coaxWire": "5D-FB",
   "Частота до, ГГц": "3"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "UHF PL259",
   "coaxWire": "5D-FB",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "4.3-10-female для 7/8″",
   "coaxWire": "7/8″",
   "КСВ": "≤1,1"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "4.3-10-male для 7/8″",
   "coaxWire": "7/8″",
   "КСВ": "≤1,15"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "QMA-J240W",
   "coaxWire": "LMR-240",
   "КСВ": "≤1,2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-111B",
   "coaxWire": "LMR-400",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "QMA-KPD7",
   "coaxWire": "5D-FB",
   "КСВ": "≤1,2",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-female RG-141",
   "coaxWire": "RG-141",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-female RG-142",
   "coaxWire": "RG-142",
   "Частота до, ГГц": "11"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-male RG-142",
   "coaxWire": "RG-142",
   "Частота до, ГГц": "11"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "BNC-C174J",
   "coaxWire": "RG-174",
   "Частота до, ГГц": "3"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "Fakra Z-Female",
   "coaxWire": "RG-174",
   "Частота до, ГГц": "6"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "F-111L",
   "coaxWire": "RG-174",
   "КСВ": "<2"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "F-male RG-316",
   "coaxWire": "RG-174",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N1-111L",
   "coaxWire": "RG-174",
   "КСВ": "≤2"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MCX-female RG-174",
   "coaxWire": "RG-174",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MCX-male RG-174",
   "coaxWire": "RG-174",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MCX-JW3",
   "coaxWire": "RG-174",
   "КСВ": "≤1,4",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "Kroks-1440",
   "coaxWire": "RG-174",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MMCX-male RG-174",
   "coaxWire": "RG-174",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "Kroks-412",
   "coaxWire": "RG-174",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MMCX-JW3",
   "coaxWire": "RG-174",
   "КСВ": "≤1,4",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-female RG-316",
   "coaxWire": "RG-174",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "RP-SMA-female RG-316",
   "coaxWire": "RG-174",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "RP-SMA-male R/A RG-316",
   "coaxWire": "RG-174",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-C316F",
   "coaxWire": "RG-174",
   "Частота до, ГГц": "6"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-female RG-174",
   "coaxWire": "RG-174",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-Female R/A RG316",
   "coaxWire": "RG-174",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-male R/A RG-174",
   "coaxWire": "RG-174",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-LC174P",
   "coaxWire": "RG-174",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-JW3",
   "coaxWire": "RG-174",
   "КСВ": "≤1,5",
   "Частота до, ГГц": "8"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMB-KW3",
   "coaxWire": "RG-174",
   "КСВ": "≤1,32",
   "Частота до, ГГц": "4"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMB-male RG-316",
   "coaxWire": "RG-174",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMP-KW3",
   "coaxWire": "RG-174",
   "КСВ": "≤1,26",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "TNC-Female RG316",
   "coaxWire": "RG-174",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "TNC-JW3Y",
   "coaxWire": "RG-174",
   "КСВ": "≤1,4",
   "Частота до, ГГц": "10"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "TNC-C174P",
   "coaxWire": "RG-174",
   "Частота до, ГГц": "4"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MHF1-plug RG-178",
   "coaxWire": "RG-178",
   "Частота до, ГГц": "6"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MMCX-male RG-178",
   "coaxWire": "RG-178",
   "КСВ": "≤1,32",
   "Частота до, ГГц": "4"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MMCX-male R/A RG-178",
   "coaxWire": "RG-178",
   "КСВ": "≤1,4",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-female RG-178",
   "coaxWire": "RG-178",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-male RG-178",
   "coaxWire": "RG-178",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-Male R/A RG178",
   "coaxWire": "RG-178",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "U.FL",
   "coaxWire": "RG-178",
   "Частота до, ГГц": "6"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-112B",
   "coaxWire": "RG-213",
   "Частота до, ГГц": "11"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "UHF-Male U-112B",
   "coaxWire": "RG-213",
   "Частота до, ГГц": "0,3"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-50-2-14/133 NE",
   "coaxWire": "RG-405",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-50-3-9/133 NE",
   "coaxWire": "RG-402",
   "Частота до, ГГц": "11"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-female RG-223",
   "coaxWire": "RG-223",
   "Частота до, ГГц": "11"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-male RG-223",
   "coaxWire": "RG-223",
   "Частота до, ГГц": "11"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "16_N-50-3-27/133_NE",
   "coaxWire": "RG-223",
   "Частота до, ГГц": "11"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-female RG-223",
   "coaxWire": "RG-223",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-male R/A RG-223",
   "coaxWire": "RG-223",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "BNC-C174J",
   "coaxWire": "RG-316",
   "Частота до, ГГц": "3"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-KY3DY",
   "coaxWire": "RG-316D",
   "КСВ": "≤1,4",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-KF2.5DY",
   "coaxWire": "RG-316D",
   "КСВ": "≤1,53",
   "Частота до, ГГц": "8"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "11_SMA-50-2-112/133_NE",
   "coaxWire": "RG-316D",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-ВКУ-1,5.214-2-355-1.М",
   "coaxWire": "RG-316D",
   "КСВ": "≤1,5",
   "Частота до, ГГц": "8"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMP-KW2.5D",
   "coaxWire": "RG-316D",
   "КСВ": "≤1,26",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "Fakra Z-Female",
   "coaxWire": "RG-316",
   "Частота до, ГГц": "6"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "F-111L",
   "coaxWire": "RG-316",
   "КСВ": "<2"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "F-male RG-316",
   "coaxWire": "RG-316",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N1-111L",
   "coaxWire": "RG-316",
   "КСВ": "≤2"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MCX-female RG-316",
   "coaxWire": "RG-316",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MCX-male RG-316",
   "coaxWire": "RG-316",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MCX-JW3",
   "coaxWire": "RG-316",
   "КСВ": "≤1,4",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "Kroks-1440",
   "coaxWire": "RG-316",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MMCX-male RG-316",
   "coaxWire": "RG-316",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "Kroks-412",
   "coaxWire": "RG-316",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MMCX-JW3",
   "coaxWire": "RG-316",
   "КСВ": "≤1,4",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-female RG-316",
   "coaxWire": "RG-316",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "RP-SMA-female RG-316",
   "coaxWire": "RG-316",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "RP-SMA-male R/A RG-316",
   "coaxWire": "RG-316",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-C316F",
   "coaxWire": "RG-316",
   "Частота до, ГГц": "6"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-female RG-316",
   "coaxWire": "RG-316",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-Female R/A RG316",
   "coaxWire": "RG-316",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-JW3",
   "coaxWire": "RG-316",
   "КСВ": "≤1,5",
   "Частота до, ГГц": "8"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-male R/A RG-316",
   "coaxWire": "RG-316",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-LC174P",
   "coaxWire": "RG-316",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMB-KW3",
   "coaxWire": "RG-316",
   "КСВ": "≤1,32",
   "Частота до, ГГц": "4"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMB-male RG-316",
   "coaxWire": "RG-316",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMP-KW3",
   "coaxWire": "RG-316",
   "КСВ": "≤1,26",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "TNC-Female RG316",
   "coaxWire": "RG-316",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "TNC-JW3Y",
   "coaxWire": "RG-316",
   "КСВ": "≤1,4",
   "Частота до, ГГц": "10"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "TNC-C174P",
   "coaxWire": "RG-316",
   "Частота до, ГГц": "4"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-female RG-400",
   "coaxWire": "RG-400",
   "Частота до, ГГц": "11"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-male RG-400",
   "coaxWire": "RG-400",
   "Частота до, ГГц": "11"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-female RG-402",
   "coaxWire": "RG-402",
   "Частота до, ГГц": "11"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-Male RG402 18GHz",
   "coaxWire": "RG-402",
   "КСВ": "≤1,3",
   "Частота до, ГГц": "18"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "11_SMA-50-3-15/111_NE",
   "coaxWire": "RG-402",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-male RG-402",
   "coaxWire": "RG-402",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-50-3-3/111 NH",
   "coaxWire": "RG-402",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-male R/A RG-402",
   "coaxWire": "RG-402",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-Male 141",
   "coaxWire": "RG-402",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "2.92mm-male RG-405",
   "coaxWire": "RG-405",
   "КСВ": "<1,3",
   "Частота до, ГГц": "40"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "MCX-male R/A RG-405",
   "coaxWire": "RG-405",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-female RG-405",
   "coaxWire": "RG-405",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-male RG-405",
   "coaxWire": "RG-405",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-Male flanged",
   "coaxWire": "RG-405",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-Female RG405 18GHz",
   "coaxWire": "RG-405",
   "КСВ": "≤1,3",
   "Частота до, ГГц": "18"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-male RG-405",
   "coaxWire": "RG-405",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-50-2-103/111 NH",
   "coaxWire": "RG-405",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-male R/A RG-405",
   "coaxWire": "RG-405",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-Male RG405 6GHz",
   "coaxWire": "RG-405",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-Male RG405 18GHz",
   "coaxWire": "RG-405",
   "КСВ": "≤1,3",
   "Частота до, ГГц": "18"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMB-Female RG405",
   "coaxWire": "RG-405",
   "КСВ": "≤1,17",
   "Частота до, ГГц": "4"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMP-Female RG405 18GHz",
   "coaxWire": "RG-405",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "19K107-271L5",
   "coaxWire": "RG-405",
   "Частота до, ГГц": "40"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "TNC-Fermale приборно-кабельный",
   "coaxWire": "RG-405",
   "КСВ": "≤1,33",
   "Частота до, ГГц": "10"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "BNC-C58P",
   "coaxWire": "RG-58",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-female RG58",
   "coaxWire": "RG-58",
   "Частота до, ГГц": "11"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-male RG58",
   "coaxWire": "RG-58",
   "Частота до, ГГц": "11"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-C58P",
   "coaxWire": "RG-58",
   "КСВ": "≤1,25",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "S5-121F",
   "coaxWire": "RG-58",
   "Частота до, ГГц": "8"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "RP-SMA-C58J",
   "coaxWire": "RG-58",
   "Частота до, ГГц": "6"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-C58J GOLD",
   "coaxWire": "RG-58",
   "КСВ": "≤1,1 + 0,002·f (f в ГГц)",
   "Частота до, ГГц": "6"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-female RG58",
   "coaxWire": "RG-58",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-Female R/A RG58",
   "coaxWire": "RG-58",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-male RG58",
   "coaxWire": "RG-58",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-male R/A RG58",
   "coaxWire": "RG-58",
   "Частота до, ГГц": "18"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-C58P-GOLD",
   "coaxWire": "RG-58",
   "Частота до, ГГц": "6"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "TNC-Female RG58",
   "coaxWire": "RG-58",
   "Частота до, ГГц": "11"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "TNC-C58P",
   "coaxWire": "RG-58",
   "Частота до, ГГц": "4"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "TNC-Male RG58",
   "coaxWire": "RG-58",
   "Частота до, ГГц": "11"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "BNC-Male RG59",
   "coaxWire": "RG-59",
   "Частота до, ГГц": "3"
  },
  "old": {
   "Частота до, ГГц": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "N-male RG-6",
   "coaxWire": "RG-6",
   "КСВ": "<2 до 6 ГГц"
  },
  "old": {
   "КСВ": ""
  }
 },
 {
  "fields": {
   "coaxArticle": "SMA-ВКП-1,5.21-2-019-2.М",
   "coaxWire": "РК50-1,5-21",
   "КСВ": "≤1,15",
   "Частота до, ГГц": "12"
  },
  "old": {
   "КСВ": "",
   "Частота до, ГГц": ""
  }
 }
]''')
mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
if "КСВ" not in COAX_CARD_COLUMNS:
    raise SystemExit("СТОП: сервер ещё без кода #552 — поле «КСВ» неизвестно")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
call = lambda dry_run: import_rows("coax", factory(), [ImportRowIn(fields=r["fields"], old=r["old"]) for r in ROWS], ACTOR, now,
                                   dry_run=dry_run, source="erp").model_dump()
data = call(True)
if data["created"]:
    raise SystemExit(f"СТОП — строки не найдены, ничего не записано: {data['created'][:5]}")
if mode == "apply":
    data = call(False)
print(f"== {mode}: строк {len(ROWS)} → обновлено {len(data['updated'])}, без изменений {data['unchanged']}, отказ {len(data['refused'])}")
for item in data["refused"][:10]:
    print("   отказ:", item)

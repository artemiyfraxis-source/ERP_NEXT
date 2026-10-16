"""Запись в прод 16.10.2026 — коакс: «HEX обжима, мм» в БД.КОАКС и D1–D3 в СПР.КАБ (владелец 16.10: «ты все решаешь сам
и закрываешь»; значения пишутся как есть, без пометок в базе — «будем на деле проверять»; как получено каждое
значение — только в журнале «Коакс — HEX обжима и D1-D3 16.10.csv» и отчёте .md рядом).

  1. БД.КОАКС «HEX обжима, мм» — пары разъём × кабель с обжимаемой втулкой экрана («Тип экрана» = обжим,
     «Крепление экрана» = обжимной/обжим, или пустые оба, а поставщик в «Наименовании» пишет «обжимной»).
     HEX — типовая матрица по классу втулки (кабель, на который поставщик продаёт разъём). Путь — `_edit_in`
     (правка строки «Базы данных»), old = "" (заполненная ячейка — отказ), ключ — Артикул × cable_key(Провод).
  2. СПР.КАБ D1–D3 — `set_coax_diameters_in` (форма «Коаксиальные кабели»): строки нет — заводится под именем кабеля;
     есть — старые D1–D3 должны совпасть со снимком прода 16.10 (иначе отказ).

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  ... python - apply < этот файл
  ... python - dry   < этот файл   → всё «уже так»
"""
import json
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import (FieldEdit, SetCoaxDiametersCommand, _audit, _edit_in, custom_columns,
                                                 set_coax_diameters_in)
from app.modules.references.cables import diameter_index
from app.modules.references.domain import ReferenceValidationError, StaleRecordError, TechOperationTab, cable_key
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Коакс HEX и D1–D3 16.10.2026", department="System")
REASON_HEX = "HEX матрицы обжима втулки экрана, 16.10.2026 (журнал docs/data «Коакс — HEX обжима и D1-D3 16.10.csv»)"
REASON_D = "D1–D3 коаксиального кабеля, 16.10.2026 (журнал docs/data «Коакс — HEX обжима и D1-D3 16.10.csv»)"
HEX_COLUMN = "HEX обжима, мм"
EMPTY = {"D1": "", "D2": "", "D3": ""}
CELLS = json.loads(r'''{"hex": [
 ["N-111/10D", "10D-FB", "13,51"],
 ["B-111/3C", "3C-2V", "6,48"],
 ["B-211/5D", "5D-FB", "8,23"],
 ["B-111/5D", "5D-FB", "8,23"],
 ["N1-211/5D", "5D-FB", "8,23"],
 ["N1-111/5D", "5D-FB", "8,23"],
 ["M-211/5D", "5D-FB", "8,23"],
 ["M-111/5D", "5D-FB", "8,23"],
 ["N-211/5D", "5D-FB", "8,23"],
 ["N-111/5D", "5D-FB", "8,23"],
 ["N-111C/5D", "5D-FB", "8,23"],
 ["N-121/5D", "5D-FB", "8,23"],
 ["S5-231/5D", "5D-FB", "8,23"],
 ["Kroks-2950", "5D-FB", "8,23"],
 ["S5-111/5D", "5D-FB", "8,23"],
 ["S-A211/5D", "5D-FB", "8,23"],
 ["S-A231/5D", "5D-FB", "8,23"],
 ["RP-SMA-male 5D-FB", "5D-FB", "8,23"],
 ["S-A111/5D", "5D-FB", "8,23"],
 ["S-A121/5D", "5D-FB", "8,23"],
 ["T-A111/5D", "5D-FB", "8,23"],
 ["SMA-Female 5D-FB", "5D-FB", "8,23"],
 ["S-211/5D", "5D-FB", "8,23"],
 ["S-221/5D", "5D-FB", "8,23"],
 ["S-231/5D", "5D-FB", "8,23"],
 ["S-M111C", "5D-FB", "8,23"],
 ["S-111/5D", "5D-FB", "8,23"],
 ["S-121/5D", "5D-FB", "8,23"],
 ["T-211/5D", "5D-FB", "8,23"],
 ["T-111/5D", "5D-FB", "8,23"],
 ["T-121/5D", "5D-FB", "8,23"],
 ["U-211/5D", "5D-FB", "8,23"],
 ["U-111/5D", "5D-FB", "8,23"],
 ["B-211/8D", "8D-FB", "10,9"],
 ["B-111/8D", "8D-FB", "10,9"],
 ["N-211/8D", "8D-FB", "10,9"],
 ["N-111/8D", "8D-FB", "10,9"],
 ["N-111C/8D", "8D-FB", "10,9"],
 ["N-121/8D", "8D-FB", "10,9"],
 ["S5-231/8D", "8D-FB", "10,9"],
 ["S5-111/8D", "8D-FB", "10,9"],
 ["S-A211/8D", "8D-FB", "10,9"],
 ["S-A111/8D", "8D-FB", "10,9"],
 ["S-211/8D", "8D-FB", "10,9"],
 ["S-111/8D", "8D-FB", "10,9"],
 ["T-211/8D", "8D-FB", "10,9"],
 ["T-111/8D", "8D-FB", "10,9"],
 ["T-121/8D", "8D-FB", "10,9"],
 ["U-211/8D", "8D-FB", "10,9"],
 ["U-111/8D", "8D-FB", "10,9"],
 ["U-111B", "8D-FB", "10,9"],
 ["N-111/5D", "CNT-300", "8,23"],
 ["S-A211/5D", "CNT-300", "8,23"],
 ["U-111B", "DX-1000", "10,9"],
 ["U-111B", "DX-10A", "10,9"],
 ["U-111B", "H-1000", "10,9"],
 ["SMA-JWB2", "LMR-100", "3,25"],
 ["N-111/5D", "LMR-300", "8,23"],
 ["S-A211/5D", "LMR-300", "8,23"],
 ["N-111B", "LMR-400", "10,9"],
 ["B-211L", "RG-174", "3,25"],
 ["B-231L", "RG-174", "3,25"],
 ["BNC-C174J", "RG-174", "3,25"],
 ["B-111L", "RG-174", "3,25"],
 ["B-121L", "RG-174", "3,25"],
 ["BNC-C174P", "RG-174", "3,25"],
 ["CRC9 R/A", "RG-174", "3,25"],
 ["Fakra Z-Female", "RG-174", "3,25"],
 ["FAKRA-female (тип C) для RG-174/RG-316", "RG-174", "3,25"],
 ["FAKRA-female (тип D) для RG-174/RG-316", "RG-174", "3,25"],
 ["FAKRA-female (тип Z) для RG-174/RG-316", "RG-174", "3,25"],
 ["FAKRA-female (тип Z) с иглой для RG-174/RG-316", "RG-174", "3,25"],
 ["F-211L", "RG-174", "3,25"],
 ["F-111L", "RG-174", "3,25"],
 ["F-male RG-316", "RG-174", "3,25"],
 ["N1-211L", "RG-174", "3,25"],
 ["N1-111L", "RG-174", "3,25"],
 ["11-211L", "RG-174", "3,25"],
 ["11-121L", "RG-174", "3,25"],
 ["MCX-male RG-174", "RG-174", "3,25"],
 ["MCX-JW3", "RG-174", "3,25"],
 ["13-211L", "RG-174", "3,25"],
 ["13-111L", "RG-174", "3,25"],
 ["Kroks-1440", "RG-174", "3,25"],
 ["MMCX-male RG-174", "RG-174", "3,25"],
 ["13-121L", "RG-174", "3,25"],
 ["Kroks-412", "RG-174", "3,25"],
 ["MMCX-male R/A RG-174", "RG-174", "3,25"],
 ["MMCX-JW3", "RG-174", "3,25"],
 ["N-211L", "RG-174", "3,25"],
 ["N-231L", "RG-174", "3,25"],
 ["N-female RG-316", "RG-174", "3,25"],
 ["N-111L", "RG-174", "3,25"],
 ["S5-231L", "RG-174", "3,25"],
 ["S5-111L", "RG-174", "3,25"],
 ["RP-SMA-female RG-316", "RG-174", "3,25"],
 ["S-A211L", "RG-174", "3,25"],
 ["S-A221L", "RG-174", "3,25"],
 ["S-A231L", "RG-174", "3,25"],
 ["SMA-C174J", "RG-174", "3,25"],
 ["RP-SMA-male R/A RG-316", "RG-174", "3,25"],
 ["S-A111L", "RG-174", "3,25"],
 ["S-A121L", "RG-174", "3,25"],
 ["SMA-female RG-174", "RG-174", "3,25"],
 ["S-231L NGT", "RG-174", "3,25"],
 ["SMA-Female R/A RG316", "RG-174", "3,25"],
 ["S-211L", "RG-174", "3,25"],
 ["S-221L", "RG-174", "3,25"],
 ["S-231L", "RG-174", "3,25"],
 ["S-241L", "RG-174", "3,25"],
 ["SMA-male RG-174", "RG-174", "3,25"],
 ["S-111L NGT", "RG-174", "3,25"],
 ["SMA-male R/A RG-174", "RG-174", "3,25"],
 ["SMA-LC174P", "RG-174", "3,25"],
 ["SMA-JW3", "RG-174", "3,25"],
 ["SMA-C174P", "RG-174", "3,25"],
 ["S-111L", "RG-174", "3,25"],
 ["S-121L", "RG-174", "3,25"],
 ["S1-211L", "RG-174", "3,25"],
 ["S1-111L", "RG-174", "3,25"],
 ["SMB-male RG-316", "RG-174", "3,25"],
 ["TNC-Female RG316", "RG-174", "3,25"],
 ["T-211L", "RG-174", "3,25"],
 ["T-231L", "RG-174", "3,25"],
 ["TNC-JW3Y", "RG-174", "3,25"],
 ["T-111L", "RG-174", "3,25"],
 ["T-121L", "RG-174", "3,25"],
 ["TNC-C174P", "RG-174", "3,25"],
 ["TS9 R/A", "RG-174", "3,25"],
 ["U-111L", "RG-174", "3,25"],
 ["SMA-11_SMA-50-1-4/111_N", "RG-178", "2,67"],
 ["SMA-Male R/A RG178", "RG-178", "2,67"],
 ["B-211B", "RG-213", "10,9"],
 ["B-111B", "RG-213", "10,9"],
 ["N-211B", "RG-213", "10,9"],
 ["N-111B", "RG-213", "10,9"],
 ["N-121B", "RG-213", "10,9"],
 ["S-211B", "RG-213", "10,9"],
 ["S-111B", "RG-213", "10,9"],
 ["T-211B", "RG-213", "10,9"],
 ["T-111B", "RG-213", "10,9"],
 ["U-211B", "RG-213", "10,9"],
 ["U-111B", "RG-213", "10,9"],
 ["UHF-Male U-112B", "RG-213", "10,9"],
 ["N-female RG-223", "RG-223", "5,41"],
 ["N-male RG-223", "RG-223", "5,41"],
 ["16_N-50-3-27/133_NE", "RG-223", "5,41"],
 ["SMA-female RG-223", "RG-223", "5,41"],
 ["SMA-male R/A RG-223", "RG-223", "5,41"],
 ["B-211L", "RG-316", "3,25"],
 ["B-231L", "RG-316", "3,25"],
 ["B-111L", "RG-316", "3,25"],
 ["B-121L", "RG-316", "3,25"],
 ["CRC9", "RG-316", "3,25"],
 ["CRC9 R/A", "RG-316", "3,25"],
 ["N-KY3DY", "RG-316D", "3,83"],
 ["11_SMA-50-2-112/133_NE", "RG-316D", "3,83"],
 ["16_SMA-50-2-106/111_NE", "RG-316D", "3,83"],
 ["Fakra Z-Female", "RG-316", "3,25"],
 ["FAKRA-female (тип C) для RG-174/RG-316", "RG-316", "3,25"],
 ["FAKRA-female (тип D) для RG-174/RG-316", "RG-316", "3,25"],
 ["FAKRA-female (тип Z) для RG-174/RG-316", "RG-316", "3,25"],
 ["FAKRA-female (тип Z) с иглой для RG-174/RG-316", "RG-316", "3,25"],
 ["F-211L", "RG-316", "3,25"],
 ["F-111L", "RG-316", "3,25"],
 ["F-male RG-316", "RG-316", "3,25"],
 ["N1-211L", "RG-316", "3,25"],
 ["N1-111L", "RG-316", "3,25"],
 ["11-211L", "RG-316", "3,25"],
 ["11-121L", "RG-316", "3,25"],
 ["MCX-male RG-316", "RG-316", "3,25"],
 ["MCX-JW3", "RG-316", "3,25"],
 ["13-211L", "RG-316", "3,25"],
 ["13-111L", "RG-316", "3,25"],
 ["Kroks-1440", "RG-316", "3,25"],
 ["MMCX-male RG-316", "RG-316", "3,25"],
 ["13-121L", "RG-316", "3,25"],
 ["Kroks-412", "RG-316", "3,25"],
 ["MMCX-male R/A RG-316", "RG-316", "3,25"],
 ["MMCX-JW3", "RG-316", "3,25"],
 ["N-female RG-316", "RG-316", "3,25"],
 ["N-211L", "RG-316", "3,25"],
 ["N-231L", "RG-316", "3,25"],
 ["N-111L", "RG-316", "3,25"],
 ["S5-231L", "RG-316", "3,25"],
 ["S5-111L", "RG-316", "3,25"],
 ["RP-SMA-female RG-316", "RG-316", "3,25"],
 ["S-A211L", "RG-316", "3,25"],
 ["S-A221L", "RG-316", "3,25"],
 ["S-A231L", "RG-316", "3,25"],
 ["S-A111L", "RG-316", "3,25"],
 ["RP-SMA-male R/A RG-316", "RG-316", "3,25"],
 ["S-A121L", "RG-316", "3,25"],
 ["SMA-C316F", "RG-316", "3,25"],
 ["SMA-Female фланцевый RG316", "RG-316", "3,25"],
 ["SMA-female RG-316", "RG-316", "3,25"],
 ["S-231L NGT", "RG-316", "3,25"],
 ["SMA-Female R/A RG316", "RG-316", "3,25"],
 ["S-211L", "RG-316", "3,25"],
 ["S-221L", "RG-316", "3,25"],
 ["S-231L", "RG-316", "3,25"],
 ["S-241L", "RG-316", "3,25"],
 ["SMA-male RG-316", "RG-316", "3,25"],
 ["S-111L NGT", "RG-316", "3,25"],
 ["SMA-JW3", "RG-316", "3,25"],
 ["SMA-male R/A RG-316", "RG-316", "3,25"],
 ["SMA-C174P", "RG-316", "3,25"],
 ["S-111L", "RG-316", "3,25"],
 ["S-121L", "RG-316", "3,25"],
 ["S1-211L", "RG-316", "3,25"],
 ["SMB-male RG-316", "RG-316", "3,25"],
 ["S1-111L", "RG-316", "3,25"],
 ["TNC-Female RG316", "RG-316", "3,25"],
 ["T-211L", "RG-316", "3,25"],
 ["T-231L", "RG-316", "3,25"],
 ["TNC-JW3Y", "RG-316", "3,25"],
 ["T-111L", "RG-316", "3,25"],
 ["T-121L", "RG-316", "3,25"],
 ["TS9", "RG-316", "3,25"],
 ["TS9 R/A", "RG-316", "3,25"],
 ["U-111L", "RG-316", "3,25"],
 ["N-female RG-400", "RG-400", "5,41"],
 ["N-male RG-400", "RG-400", "5,41"],
 ["B-211F", "RG-58", "5,41"],
 ["B-231F", "RG-58", "5,41"],
 ["B-111F", "RG-58", "5,41"],
 ["B-121F", "RG-58", "5,41"],
 ["BNC-C58P", "RG-58", "5,41"],
 ["FAKRA-female (тип C) для RG-58", "RG-58", "5,41"],
 ["FAKRA-female (тип D) для RG-58", "RG-58", "5,41"],
 ["FAKRA-female (тип Z) для RG-58", "RG-58", "5,41"],
 ["F-211F", "RG-58", "5,41"],
 ["F-111F", "RG-58", "5,41"],
 ["F-121F", "RG-58", "5,41"],
 ["N1-211F", "RG-58", "5,41"],
 ["N1-111F", "RG-58", "5,41"],
 ["11-211F", "RG-58", "5,41"],
 ["11-111F", "RG-58", "5,41"],
 ["M-211F", "RG-58", "5,41"],
 ["M-111F", "RG-58", "5,41"],
 ["13-111F", "RG-58", "5,41"],
 ["N-female RG58", "RG-58", "5,41"],
 ["N-211F", "RG-58", "5,41"],
 ["N-231F", "RG-58", "5,41"],
 ["N-241F", "RG-58", "5,41"],
 ["N-251F", "RG-58", "5,41"],
 ["N-male RG58", "RG-58", "5,41"],
 ["N-111F", "RG-58", "5,41"],
 ["N-121F", "RG-58", "5,41"],
 ["N-C58P", "RG-58", "5,41"],
 ["S5-231F", "RG-58", "5,41"],
 ["S5-111F", "RG-58", "5,41"],
 ["S5-121F", "RG-58", "5,41"],
 ["S-A211F", "RG-58", "5,41"],
 ["S-A221F", "RG-58", "5,41"],
 ["S-A231F", "RG-58", "5,41"],
 ["S-A111F", "RG-58", "5,41"],
 ["RP-SMA-C58J", "RG-58", "5,41"],
 ["RP-SMA-C58P", "RG-58", "5,41"],
 ["S-A121F", "RG-58", "5,41"],
 ["T-A111F", "RG-58", "5,41"],
 ["SMA-C58J GOLD", "RG-58", "5,41"],
 ["SMA-female RG58", "RG-58", "5,41"],
 ["S-231F", "RG-58", "5,41"],
 ["S-231F NGT", "RG-58", "5,41"],
 ["SMA-Female R/A RG58", "RG-58", "5,41"],
 ["S-211F", "RG-58", "5,41"],
 ["S-221F", "RG-58", "5,41"],
 ["S-111F NGT", "RG-58", "5,41"],
 ["SMA-male R/A RG58", "RG-58", "5,41"],
 ["SMA-C58J R/A Gold", "RG-58", "5,41"],
 ["SMA-LC58P", "RG-58", "5,41"],
 ["SMA-C58P", "RG-58", "5,41"],
 ["SMA-C58P-GOLD", "RG-58", "5,41"],
 ["S-111F", "RG-58", "5,41"],
 ["S-121F", "RG-58", "5,41"],
 ["S1-211F", "RG-58", "5,41"],
 ["S1-111F", "RG-58", "5,41"],
 ["TNC-C58J", "RG-58", "5,41"],
 ["T-211F", "RG-58", "5,41"],
 ["T-231F", "RG-58", "5,41"],
 ["TNC-Female RG58", "RG-58", "5,41"],
 ["TNC-C58P", "RG-58", "5,41"],
 ["TNC-Male RG58", "RG-58", "5,41"],
 ["T-111F", "RG-58", "5,41"],
 ["T-121F", "RG-58", "5,41"],
 ["U-211F", "RG-58", "5,41"],
 ["U-231F", "RG-58", "5,41"],
 ["U-111F", "RG-58", "5,41"],
 ["U-121F", "RG-58", "5,41"],
 ["B-111G", "RG-59", "6,48"],
 ["BNC-Male RG59", "RG-59", "6,48"],
 ["F105C", "RG-59", "8,23"],
 ["N1-111/5D", "RG-59", "8,23"],
 ["N-111/5D", "RG-59", "8,23"],
 ["S-A211/5D", "RG-59", "8,23"],
 ["B-111A", "RG-6", "8,23"],
 ["BNC-C6P", "RG-6", "8,23"],
 ["F105C", "RG-6", "8,23"],
 ["F-111A", "RG-6", "8,23"],
 ["S-111A", "RG-6", "8,23"],
 ["T-111A", "RG-6", "8,23"],
 ["N1-111/5D", "RG-6", "8,23"],
 ["N-111/5D", "RG-6", "8,23"],
 ["S-A211/5D", "RG-6", "8,23"],
 ["U-111B", "RG-8", "10,9"],
 ["F105C", "SAT-50", "8,23"],
 ["F105C", "SAT-703", "8,23"],
 ["N1-111/5D", "SAT-703", "8,23"],
 ["N-111/5D", "SAT-703", "8,23"],
 ["S-A211/5D", "SAT-703", "8,23"]
],
"d": [
 ["RG-8", null, {"D1": "2,16", "D2": "7,24", "D3": "7,9"}],
 ["3C-2V", null, {"D1": "0,54", "D2": "3,1", "D3": "3,6"}],
 ["SAT-50", null, {"D1": "1", "D2": "4,8", "D3": "5,3"}],
 ["SAT-752F", null, {"D1": "1,13", "D2": "4,8", "D3": "5,3"}],
 ["nu-Trac TRC-1250", null, {"D1": "13,1", "D2": "33", "D3": "35,8"}],
 ["РК50-4-11", {"D1": "1,37", "D2": "4,6", "D3": ""}, {"D1": "1,37", "D2": "4,6", "D3": "5,8"}],
 ["РК50-1,5-21", {"D1": "0,54", "D2": "1,5", "D3": ""}, {"D1": "0,54", "D2": "1,5", "D3": "1,82"}],
 ["РК50-2-11", {"D1": "0,67", "D2": "2,2", "D3": ""}, {"D1": "0,67", "D2": "2,2", "D3": "2,6"}],
 ["H-1000", {"D1": "2,62", "D2": "7,15", "D3": ""}, {"D1": "2,62", "D2": "7,15", "D3": "8,04"}],
 ["DG-113", {"D1": "1,13", "D2": "4,8", "D3": ""}, {"D1": "1,13", "D2": "4,8", "D3": "5,3"}],
 ["DX-1000", {"D1": "2,6", "D2": "7,11", "D3": ""}, {"D1": "2,6", "D2": "7,11", "D3": "8"}],
 ["DX-10A", {"D1": "2,6", "D2": "7,24", "D3": ""}, {"D1": "2,6", "D2": "7,24", "D3": "8,13"}],
 ["RG-8X", {"D1": "1,42", "D2": "3,94", "D3": ""}, {"D1": "1,42", "D2": "3,94", "D3": "4,56"}],
 ["RP-913B", {"D1": "1,13", "D2": "4,75", "D3": ""}, {"D1": "1,13", "D2": "4,75", "D3": "5,3"}]
]}''')

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
totals: dict[str, int] = {}


def log(table, key, field, old, new, status):
    totals[status.split(" ")[0]] = totals.get(status.split(" ")[0], 0) + 1
    print(f"   {status}: {table} «{key}» {field}: «{old}» → «{new}»")


print(f"== 1. БД.КОАКС «{HEX_COLUMN}» — {len(CELLS['hex'])} пар")
with factory() as uow:
    if HEX_COLUMN not in custom_columns(TechOperationTab.COAX, uow):
        raise SystemExit(f"СТОП: столбца «{HEX_COLUMN}» нет")
    rows = uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100_000)
    for article, wire, value in CELLS["hex"]:
        key = f"{article} × {wire}"
        pair = [r for r in rows if str(r.fields.get("coaxArticle", "")).strip() == article
                and cable_key(r.fields.get("coaxWire")) == cable_key(wire)]
        if len(pair) != 1:
            log("БД.КОАКС", key, HEX_COLUMN, "", value, f"отказ (строк {len(pair)})")
            continue
        record = pair[0]
        current = str(record.fields.get(HEX_COLUMN, "")).strip()
        if current == value:
            log("БД.КОАКС", key, HEX_COLUMN, current, value, "уже так")
            continue
        if current:
            log("БД.КОАКС", key, HEX_COLUMN, current, value, "отказ (ячейка уже заполнена)")
            continue
        try:
            _edit_in(record.id, (FieldEdit(HEX_COLUMN, "", value),), now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            log("БД.КОАКС", key, HEX_COLUMN, "", value, f"отказ ({error})")
            continue
        _audit("references.tech_record_edited", ACTOR, now, {
            "record_id": str(record.id), "tab": record.tab.value, "fields": [HEX_COLUMN], "reason": REASON_HEX}, uow)
        log("БД.КОАКС", key, HEX_COLUMN, "", value, "проверено" if dry else "записано")
    if not dry:
        uow.commit()

print(f"== 2. СПР.КАБ D1–D3 — {len(CELLS['d'])} кабелей")
for cable, old, value in CELLS["d"]:
    with factory() as uow:
        current = diameter_index(uow.tech_operations.list(tab=TechOperationTab.KAB_COAX, query="", limit=100_000)).get(cable_key(cable))
        have = {n: str(current.fields.get(n, "")).strip() for n in EMPTY} if current else dict(EMPTY)
        if have == value:
            log("СПР.КАБ", cable, "D1–D3", have, value, "уже так")
            continue
        if (current is None) != (old is None) or (old is not None and have != old):
            log("СПР.КАБ", cable, "D1–D3", have, value, f"отказ (на проде не как в снимке: {'нет строки' if current is None else have})")
            continue
        try:
            set_coax_diameters_in(SetCoaxDiametersCommand(cable=cable, dia_id=current.id if current else None,
                                                          old=old or dict(EMPTY), value=value, actor=ACTOR, now=now),
                                  uow, {"reason": REASON_D})
        except (StaleRecordError, ReferenceValidationError) as error:
            log("СПР.КАБ", cable, "D1–D3", have, value, f"отказ ({error})")
            continue
        log("СПР.КАБ", cable, "D1–D3", have, value, ("проверено" if dry else "записано") + (" (новая строка)" if current is None else ""))
        if not dry:
            uow.commit()

print("Итого:", ", ".join(f"{name} {count}" for name, count in sorted(totals.items())),
      "(проверка, ничего не записано)" if dry else "(записано)")

"""Запись в прод 25.10.2026 — справочные данные проекта 708К. (Яндекс, 41 КБ): кабель RTK 031 и четыре разъёма
FAKRA / HFM под него по документам изготовителей (владелец 25.10: «делаем техкарты 708К.»; оснастка не определена —
«поставь прочерк»). Только то, что сказано в документах; остальное пусто (список — в журнале CSV).

  1. БД.ПРОВ, список «Коаксиальные кабели»: RTK 031 (Caleb; группа M4 у Rosenberger, аналог Leoni Dacar 302).
     ИСКЛЮЧЕНИЕ из правила «коакс-кабели только АМЭЛ / Kroks / gsm-repiteri» (coax-data-sources) — одобрено
     владельцем 25.10.2026: кабель задан заказчиком, разъёмы Rosenberger группы M4 сделаны под него; поставщика нет.
  2. СПР.КАБ · коаксиал: D1 0,81 (7 × 0,27 → 3 × d, правило 19.10), D2 2,1; D3 в паспорте нет — пусто.
  3. БД.КОАКС × RTK 031: 59K13O-1M4A4, 59K25H-1M4A4, AMK11D-1M4Z5-Z (HFM, 4 канала), 2FA1-NZSP-C30E2.
     Корпуса, уплотнители, крышки (59Z163-003-Z, 59K14B-1M4/50, 59Z163-000/51, 59Z006-T00-Z, AMZ025-000-Z) —
     не кабельные разъёмы, строк не получают (в БД.КОАКС корпусов нет ни одного).

Только создание: import_rows по натуральному ключу (БД.ПРОВ — «Марка», БД.КОАКС — артикул + провод), old = {};
D1–D3 — `set_coax_diameters_in`, только если строки RTK 031 в СПР.КАБ нет. Если ключ уже есть — СТОП, ничего не пишется.

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → создавать нечего, всё уже есть
Откат: удалить 4 строки БД.КОАКС, строку RTK 031 в «Коаксиальных кабелях» и строку RTK 031 в СПР.КАБ (журнал CSV).
"""
import os
import sys

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import SetCoaxDiametersCommand, set_coax_diameters_in
from app.modules.references.cables import diameter_index
from app.modules.references.domain import TechOperationTab, cable_key
from app.modules.references.importers import import_rows
from app.modules.references.load_mirror import ACTOR
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now

sys.stdout.reconfigure(encoding="utf-8")

CABLE = "RTK 031"
CALEB = "https://www.calebcable.com/File/automotive/RTK%20031.pdf"
MA059 = "https://web.archive.org/web/20220630063920/https://products.rosenberger.com/_ocassets/ma/MA_59V059.pdf"
DB13O = "https://products.rosenberger.com/_ocassets/db/59K13O-1M4A4.pdf"
PAGE13O = "https://products.rosenberger.com/radio-frequency/connectors/157636/59k13o-1m4a4-straight-jack"
MA065 = "https://products.rosenberger.com/_ocassets/ma/MA_59V065.pdf"
DB25H = "https://products.rosenberger.com/_ocassets/db/59K25H-1M4A4.pdf"
MAAMV = "https://products.rosenberger.com/_ocassets/ma/MA_AMV003.pdf"
DBAMK = "https://products.rosenberger.com/_ocassets/db/AMK11D-1M4Z5-Y.pdf"
AMP = "https://www.amphenolrf.com/en-us/part/2fa1-nzsp-c30e2/10217/"
TOOL = "Инструмент: — (владелец 25.10.2026: оснастка не определена)."
RL = "КСВ пересчитан из обратных потерь: КСВ = (1+10^(−RL/20))/(1−10^(−RL/20))"

CABLE_ROW = {
    "Марка": CABLE, "Вид кабеля": "coax", "Тип провода": "коаксиальный", "Производители": "Caleb Cable",
    "Жила: проволок × диаметр, мм": "7×0,27", "Материал жилы": "медь", "Материал диэлектрика": "ПЭ вспененный",
    "Материал экрана": "алюминий, фольга + медь лужёная, оплётка", "Материал оболочки": "ПВХ",
    "Наружный диаметр, мм": "3,2", "Волновое сопротивление, Ом": "50", "Погонная ёмкость, пФ/м": "85",
    "Темп. от, °C": "-40", "Темп. до, °C": "105", "Ссылка на datasheet": CALEB,
    "Источник данных": (
        "25.10.2026: ИСКЛЮЧЕНИЕ из правила трёх поставщиков коакс-кабеля (АМЭЛ / Kroks / gsm-repiteri) — одобрено "
        "владельцем 25.10.2026: кабель задан заказчиком проекта 708К. (Яндекс), разъёмы Rosenberger группы M4 "
        "(«M4 / RTK 031» в MA_59V059, «Dacar 302» в MA_59V065 / MA_AMV003) сделаны под него; поставщика и цены нет. "
        f"Паспорт Caleb Cable «Automotive Cables — RTK 031» ({CALEB}): Inner conductor (BC) 7 x 0.27 → Жила, "
        "Материал жилы; Insulator (FPE) dia. 2.1 → Материал диэлектрика (D2 2,1 в СПР.КАБ); Outer conductor 1 "
        "AL/PET/AL + Outer conductor 2 (Tinned Copper) 0.1 x 16 x 7 → Материал экрана; Jacket (PVC) → Материал "
        "оболочки; Outer dia. 3.2±0.1 → Наружный диаметр; Characteristic impedance 50±2; Capacitance at 1 kHz "
        "85±4 nF/km → 85 пФ/м; Max. conductor resistance 48 Ohm/km; Std. attenuation dB/km: 437 (800 MHz), 453 "
        "(900), 605 (1500), 684 (1900), 757 (2300), 797 (2500), 881 (3000); Temperature range -40 °C to 105 °C; "
        "Flame retardancy IEC 60332-1, 60332-3. Аналог — Leoni Dacar 302 (Rosenberger MA_AMV003: «RTK 031 — Leoni "
        "85120380# Dacar 302»). Не дано в паспорте: Ø по экрану (D3), масса, радиусы изгиба, рабочая частота, "
        "стандарт, цвет оболочки (на фото чёрная — не записано)"),
}
DIA = {"D1": "0,81", "D2": "2,1", "D3": ""}

ROWS = [
    {
        "coaxArticle": "59K13O-1M4A4", "coaxWire": CABLE,
        "Артикул": "59K13O-1M4A4", "Провод": CABLE, "Тип/Серия": "FAKRA-female (тип Z)", "Производитель": "Rosenberger",
        "Серия": "Fakra", "Пол": "гнездо", "Полярность": "обычная", "Исполнение": "прямой",
        "L1": "3", "L2": "4,6", "L3": "9,7",
        "Тип пина": "обжим", "Тип экрана": "обжим", "Монтаж": "обжимной", "Центральный контакт": "обжим",
        "Крепление экрана": "обжимной", "HEX обжима, мм": "4,3",
        "Волновое сопротивление, Ом": "50", "Частота до, ГГц": "6",
        "КСВ": "≤1,07 (0–1 ГГц); ≤1,09 (0–3 ГГц); ≤1,22 (0–6 ГГц)",
        "Покрытие": "корпус: никель; центральный контакт: золото",
        "Заглубление контакта, мм": "0,18…0,68",
        "Заглубление контакта, источник": (
            f"Rosenberger MA_59V059 rev w00, л. 5, шаг 6 ({MA059}): «Kontaktrückstandsmaß / interface dimension "
            "0.18 +0.5» — от торца корпуса-вставки D (передний торец внешнего контакта и воронки изолятора) до торца "
            "гнезда центрального контакта, вид X 8:1 → 0,18…0,68 мм; 25.10.2026"),
        "Наименование": ("Разъём 59K13O-1M4A4 (FAKRA-HF, гнездо, прямое, вставка под пластиковый корпус, обжим "
                         "контакта и втулки экрана, 50 Ом; кабель RTK 031)"),
        "Ссылка": PAGE13O,
        "Примечание": (
            "комплект 708К.: прямая FAKRA-розетка в герметичном корпусе 59Z163-003-Z (код Z, Water Blue) с "
            "уплотнителем 59K14B-1M4/50 и крышкой 59Z163-000/51 — детали корпуса, своих строк в БД.КОАКС нет; код Z "
            f"— по корпусу. {TOOL} По MA_59V059: втулку надеть до разделки; обжим контакта cbm 1,34±0,05 × ch "
            "0,88±0,03 (ручной инструмент) или 0,99±0,03 (аппликатор), отрыв >60 Н, концы жилы видны, смещение ≤0,3; "
            "оплётку и фольгу развальцевать (или фольгу срезать); вставить в корпус D до защёлкивания (≤20 Н, "
            "удержание ≥10 Н, проверка тягой ≤5 Н), без вращения; втулку экрана — вплотную к корпусу, зазор "
            "0,1±0,1, HEX 4,3±0,05, отрыв >110 Н; вставку D в пластиковый корпус до 2,05±0,15, затем вторичный "
            "фиксатор"),
        "Источник данных": (
            f"25.10.2026: Rosenberger, Technical Data Sheet 59K13O-1M4A4 rev g00 ({DB13O}): «FAKRA-HF STRAIGHT JACK», "
            "Interface DIN 72594-1, Assembly instruction MA_59V059; Center contact spring bronze, gold over nickel; "
            "Outer contact brass, nickel 3-6 µm; Crimping ferrule copper, nickel → Покрытие; Impedance 50 Ω; "
            "Frequency DC to 6 GHz; Return loss ≥30 dB DC–1 GHz, ≥27 dB DC–3 GHz, ≥20 dB DC–6 GHz → КСВ "
            f"({RL}); Tooling 11W150-000 + insert 11W150-104, 11W161-8M4 (не записано — оснастка не определена); "
            f"Suitable cables RTK 031 → Провод; MA_59V059 rev w00 ({MA059}): л. 1 «59K13O-1M4XX-Y — M4 / RTK 031»; "
            "л. 2 «Strip cable according to drawing/table», строка «M4 / RTK 031: X 9.7±0.2, Y 4.6±0.2, Z 3±0.2»; "
            "по рисунку X — от торца кабеля до среза оболочки, Y — до среза оплётки, Z — открытая жила → L3 9,7, "
            "L2 4,6, L1 3; л. 3 crimp «M4 / RTK 031 Handtool (1.34±0.05) 0.88±0.03 >60N, Crimpapplicator 0.99±0.03» "
            "→ Тип пина обжим; л. 7 «M4 / RTK 031 (4.3±0.05) 4.3±0.05, D-D N.A., >110N» → Тип экрана обжим, HEX 4,3; "
            "л. 8 «interface dimension 2.05±0.15» (вставка в корпус E). Пол — гнездо: центральный контакт с "
            "лепестками (л. 5); Серия, Исполнение, Полярность — по типу. Цена, поставщик — нет"),
    },
    {
        "coaxArticle": "59K25H-1M4A4", "coaxWire": CABLE,
        "Артикул": "59K25H-1M4A4", "Провод": CABLE, "Тип/Серия": "FAKRA-female R/A (тип Z)",
        "Производитель": "Rosenberger",
        "Серия": "Fakra", "Пол": "гнездо", "Полярность": "обычная", "Исполнение": "угловой",
        "L1": "1,7", "L2": "6,4", "L3": "11,6",
        "Тип пина": "обжим", "Тип экрана": "обжим", "Монтаж": "обжимной", "Центральный контакт": "обжим",
        "Крепление экрана": "обжимной", "HEX обжима, мм": "4,3",
        "Волновое сопротивление, Ом": "50", "Частота до, ГГц": "6",
        "КСВ": "≤1,11 (0–1 ГГц); ≤1,13 (0–3 ГГц); ≤1,33 (0–6 ГГц)",
        "Покрытие": "корпус: никель; центральный контакт: золото",
        "Наименование": ("Разъём 59K25H-1M4A4 (FAKRA-HF, гнездо, угловое 360°, вставка под пластиковый корпус, "
                         "обжим пина и втулки экрана, 50 Ом; кабель RTK 031)"),
        "Ссылка": DB25H,
        "Примечание": (
            "комплект 708К.: угловая FAKRA-розетка в поворотном (360°) корпусе 59Z006-T00-Z (код Z) — деталь "
            "корпуса, своей строки нет. Тип пина «обжим» — исключение владельца 25.10.2026 из правила «угловой = "
            "пайка внутри корпуса»: центральный пин — отдельная деталь, обжимается на жиле до установки кабеля в "
            f"угловой корпус. {TOOL} По MA_59V065: втулку надеть до разделки; пин надвинуть на жилу до диэлектрика "
            "и обжать (квадрат 1,1 −0,03), зазор диэлектрик–пин ≤0,3; оплётку развальцевать, вставить кабель до "
            "защёлкивания — кончик контакта виден в окне корпуса; втулку экрана вплотную к корпусу, зазор 0,1 "
            "+0,1/−0,1, HEX 4,3 −0,05; крышку F запрессовать заподлицо; вставку в корпус E до защёлкивания, затем "
            "вторичный фиксатор. Заглубление контакта у углового не задаётся (в инструкции — только «кончик виден»)"),
        "Источник данных": (
            f"25.10.2026: Rosenberger, Technical Data Sheet 59K25H-1M4A4 ({DB25H}): «FAKRA-HF RIGHT ANGLE JACK 360°», "
            "Interface DIN 72594-1, Assembly instruction MA_59V065; Center contact spring bronze, gold; Center "
            "contact 2 brass, gold; Body brass, nickel 2.5-5 µm; Crimping ferrule copper, nickel; Cover alpaca → "
            "Покрытие; Impedance 50 Ω; Frequency DC to 6 GHz; Return loss ≥26 dB DC–1 GHz, ≥24 dB DC–3 GHz, ≥17 dB "
            f"DC–6 GHz → КСВ ({RL}); Tooling 11W150-000 + insert 11W150-504 (не записано — оснастка не определена); "
            f"Suitable cables RTK 031 → Провод; MA_59V065 rev t00 14.02.2025 ({MA065}), л. 1, строка «59K25C-1M4XX-Y* / "
            "59K25H-1M4XX-Y* — Dacar®302, Dacar®302-3, Dacar®302-4: X 11.6±0.2, Y 6.4±0.2, Z 1.7±0.2, Crimpdie "
            "11W150-504, Outerdim. hex 4.3 −0.05, Innerdim. □1.1 −0.03»; по рисунку X — от торца кабеля до среза "
            "оболочки, Y — до среза оплётки, Z — открытая жила → L3 11,6, L2 6,4, L1 1,7; HEX 4,3; л. 2 шаг 3 «Push "
            "center pin "
            "\"C\" on the inner conductor of the cable up to the dielectric and crimp it» → Тип пина обжим "
            "(исключение владельца 25.10 — coax-pin-type); шаг 7 «Slide ferrule \"B\" over the braid up to the "
            "connector body» → Тип экрана обжим; группа M4 = RTK 031 (MA_59V059 л. 1). Цена, поставщик — нет"),
    },
    {
        "coaxArticle": "AMK11D-1M4Z5-Z", "coaxWire": CABLE,
        "Артикул": "AMK11D-1M4Z5-Z", "Провод": CABLE, "Тип/Серия": "HFM-female (тип Z)", "Производитель": "Rosenberger",
        "Серия": "HFM", "Пол": "гнездо", "Полярность": "обычная", "Исполнение": "прямой",
        "Тип пина": "обжим", "Тип экрана": "обжим", "Монтаж": "обжимной", "Центральный контакт": "обжим",
        "Крепление экрана": "обжимной",
        "Волновое сопротивление, Ом": "50", "Частота до, ГГц": "15",
        "КСВ": "≤1,12 (0–3 ГГц); ≤1,22 (3–6 ГГц); ≤1,43 (6–12 ГГц); ≤1,67 (12–15 ГГц)",
        "Покрытие": "внешний контакт: олово; центральный контакт: золото (зона контакта), олово (зона обжима)",
        "Заглубление контакта, мм": "0,90…1,20",
        "Заглубление контакта, источник": (
            f"Rosenberger MA_AMV003 rev c00, л. 7, шаг 7 ({MAAMV}): «Insert prepared cable into jack \"D\" until "
            "interface dimension 1.05±0.15 mm is reached» — вид Z: от переднего торца внешнего контакта гнезда до "
            "торца центрального контакта → 0,90…1,20 мм; 25.10.2026"),
        "Наименование": ("Разъём AMK11D-1M4Z5-Z (HFM — High-Speed FAKRA Mini, гнездо, прямое, корпус на 4 канала "
                         "AMZ025-000-Z + 4 контакта AMK12A-1M4Z5, код Z — Water Blue, 50 Ом; кабель RTK 031)"),
        "Ссылка": DBAMK,
        "Примечание": (
            "комплект 708К. (КБ3–10): 4 канала, на каждый кабель — свой контакт AMK12A-1M4Z5; строка описывает "
            "разделку одного кабеля. L1–L3 и HEX НЕ заполнены: разделка HFM не сводится к трём ступеням от торца "
            "кабеля — размеры MA_AMV003 отсчитываются от торца опорной гильзы, оплётка отворачивается на гильзу, "
            "внешний контакт обжимается тремя зонами (не шестигранник). По MA_AMV003: (1) надрезать и сдвинуть "
            "оболочку a = 13; (2) опорную гильзу обжать на оплётке 3,0±0,05 × 3,05±0,05, остаток перемычки r "
            "0,65±0,1, зазор s 1,75±0,2, длина гильзы (3,2), край оболочки 3,7…4,7 от торца гильзы; (3) калибровочный "
            "рез 9±0,2 от торца гильзы; (4) снять оболочку, оплётку отвернуть на гильзу, от торца гильзы: фольга w "
            "≤4,5, диэлектрик y 5,55±0,2, торец жилы v 7,85±0,2, оплётка не ближе z 2,1±0,2 к жиле (расчётно: "
            "открытая жила v − y = 2,3); (5) обжать контакт cbm 1,28±0,05 × ch 0,9±0,03, отрыв >60 Н, размер x "
            "15,9±0,2; (7) вставить в гнездо D до 1,05±0,15, обжать внешний контакт: по оболочке k 3,7×3,65, "
            "перетяжка t 3,45×3,35, по проводу l 3,8×3,8 (все ±0,05), отрыв >120 Н; калибр Ø3,54 — свободно "
            "вращается, в зоне m не более Ø3,6; (9) гнёзда в корпус до защёлкивания, все вторичные фиксаторы. "
            f"{TOOL}"),
        "Источник данных": (
            f"25.10.2026: Rosenberger, Technical Data Sheet AMK11D-1M4Z5-Y ({DBAMK}): «HFM® High-Speed FAKRA Mini "
            "STRAIGHT JACK w. HOUSING», рисунок: корпус AMZ025-000-Y + 4 × AMK12A-1M4Z5; Interface RN_108-04; "
            "Assembly instruction MA_AMV003; Center contact spring bronze — interface gold, crimp area tin; Outer "
            "contact spring bronze / stainless steel, tin → Покрытие; Impedance 50; Frequency DC to 15 GHz; Return "
            "loss ≥25 dB DC–3 GHz, ≥20 dB 3–6, ≥15 dB 6–12, ≥12 dB 12–15 GHz → КСВ "
            f"({RL}); Temperature -40…+105 °C; Suitable cables Dacar 302, Dacar 302-3, G&G 59998; Coding Z waterblue "
            "sim. 5021 «AMK11D-1M4Z5-Z»; Tooling 11WR024_A / 11WB013_A / 11WS001_A, hand AMW001-SET (не записано — "
            f"оснастка не определена). MA_AMV003 rev c00 23.05.2025 ({MAAMV}): л. 2 «AMK12A-1M4XX — RTK 031 — Leoni "
            "85120380# Dacar 302», «Abisolierlänge a: RTK031 (M4) 13»; л. 3–5, 7, 8 — размеры в «Примечании» → Тип "
            "пина обжим (crimp center contact, RTK 031 (M4) 1.28±0.05 × 0.9±0.03), Тип экрана обжим (crimp outer "
            "conductor k/t/l); Пол — гнездо (лепестковый центральный контакт, л. 7). Цена, поставщик — нет"),
    },
    {
        "coaxArticle": "2FA1-NZSP-C30E2", "coaxWire": CABLE,
        "Артикул": "2FA1-NZSP-C30E2", "Провод": CABLE, "Тип/Серия": "FAKRA-male bulkhead (тип Z)",
        "Производитель": "Amphenol RF",
        "Серия": "Fakra", "Пол": "штекер", "Полярность": "обычная", "Исполнение": "прямой",
        "L1": "2,7", "L2": "8,5", "L3": "13,9",
        "Тип пина": "пайка", "Тип экрана": "обжим", "Монтаж": "обжимной", "Центральный контакт": "пайка",
        "Крепление экрана": "обжимной", "HEX обжима, мм": "4,52",
        "Волновое сопротивление, Ом": "50", "Частота до, ГГц": "6", "КСВ": "≤1,23",
        "Покрытие": "корпус: никель; центральный контакт: золото",
        "Наименование": ("Разъём 2FA1-NZSP-C30E2 (FAKRA, штекер, прямой, на панель с тыльной стороны, IP67, код Z — "
                         "Water Blue, обжим экрана, пайка контакта, 50 Ом; кабель TFC 302 / Dacar 302 / RTK 031)"),
        "Ссылка": AMP,
        "Примечание": (
            "комплект 708К. (КБ28–40). Тип пина «пайка», хотя в названии товара «Crimp» — по чертежу (п. 5C), см. "
            "coax-pin-type. Разделка: открытая жила 2,7; диэлектрик открыт 1,0; фольга НЕ снимается и входит в "
            "разъём — от 3,7 до 8,5 (поэтому L2 = торец → срез оплётки включает диэлектрик 1,0 и фольгу 4,8); "
            "оплётка до 13,9 (L3). Порядок (п. 5): надеть термоусадку, втулку и изолирующую шайбу → припаять жилу к "
            "контакту (bullet) → вставить кабель, фольга внутрь разъёма, оплётка на накатку → обжать втулку HEX "
            ".178″ (4,52) → усадить клеевую термоусадку на втулку: целиком закрыть выступ корпуса, край — в зоне A "
            "(≤1,5 от корпуса). Панель ≤4 мм, отверстие Ø13 +0,2/−0,1 с лыской 11,6 +0,2/0; резьба 1/2-28 "
            f"UNEF-2A, гайка 15,88; длина в сборе 39,12 (REF). Заглубление контакта на чертеже не дано. {TOOL}"),
        "Источник данных": (
            f"25.10.2026: Amphenol RF, страница 2FA1-NZSP-C30E2 ({AMP}); Customer Outline Drawing 2FA1-NXSP-C30E2 rev B "
            "(01-Nov-24), «FAKRA STR PLUG FOR TFC 302 CABLE REAR BULKHEAD, IP67», таблица кодов «Z 2FA1-NZSP-C30E2 "
            "WATER BLUE 5021» → Тип/Серия, Пол, Исполнение; п. 1 BODY zinc alloy (ZAMAK #3) nickel over copper; "
            "CONTACT brass & beryllium copper, gold → Покрытие; п. 2 «IMPEDANCE: 50 OHM; FREQUENCY RANGE: DC-6 GHz; "
            "VSWR (RETURN LOSS): 1.23 MAX» → КСВ ≤1,23; п. 3 «TEMPERATURE RANGE -40 °C TO 100 °C; MEETS IP67; "
            "INTERFACE PER DIN 72594-1»; п. 5 «A. STRIP CABLE PER RECOMMENDED DIMENSIONS … C. SOLDER CABLE CENTER "
            "CONDUCTOR TO BULLET CONTACT. D. INSERT CABLE WITH FOIL ENTERING CONNECTOR AND BRAID OVER KNURL. E. CRIMP "
            "FERRULE OVER BRAID WITH .178″ HEX DIE» → Тип пина пайка, Тип экрана обжим, HEX 4,52 (.178″ × 25,4); "
            "«RECOMMEND CABLE STRIPPING DIMENSIONS»: 2.7 CENTER CONDUCTOR, 1 DIELECTRIC, 8.5 TO BRAID, 13.9 TO "
            "JACKET (от торца кабеля) → L1 2,7, L2 8,5, L3 13,9. Кабель на чертеже — TFC 302; RTK 031 — по "
            "спецификации заказчика 708К. и группе Dacar 302 = RTK 031 (Rosenberger M4). Цена, поставщик — нет"),
    },
]

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()


def run(table, rows, dry_run):
    return import_rows(table, factory(), [ImportRowIn(fields=fields, old={}) for fields in rows], ACTOR, now,
                       dry_run=dry_run, source="erp").model_dump()


def show(name, data):
    print(f"== {name}: создано {len(data['created'])}, обновлено {len(data['updated'])}, "
          f"без изменений {data['unchanged']}, отказ {len(data['refused'])}")
    for item in data["created"]:
        print("   создать/создана:", item)
    for item in data["updated"] + data["refused"]:
        print("   !", item)


def dia_row():
    uow = factory()
    with uow:
        return diameter_index(uow.tech_operations.list(tab=TechOperationTab.KAB_COAX, query="", limit=10_000)).get(
            cable_key(CABLE))


prov, coax, dia = run("prov", [CABLE_ROW], True), run("coax", ROWS, True), dia_row()
show("БД.ПРОВ (проверка)", prov)
show("БД.КОАКС (проверка)", coax)
print("== СПР.КАБ RTK 031:", "уже есть " + str(dia.fields) if dia else f"нет — будет создана {DIA}")
stop = prov["updated"] + prov["refused"] + coax["updated"] + coax["refused"]
if stop:
    raise SystemExit("СТОП: строка с таким ключом уже есть или отказ — ничего не записано")
if mode == "apply":
    if prov["created"]:
        show("БД.ПРОВ записано", run("prov", [CABLE_ROW], False))
    if dia is None:
        uow = factory()
        with uow:
            record = set_coax_diameters_in(SetCoaxDiametersCommand(CABLE, None, {}, DIA, ACTOR, now), uow,
                                           {"reason": "708К. RTK 031 по паспорту Caleb, 25.10.2026"})
            uow.commit()
        print("== СПР.КАБ записано:", record.id, record.fields)
    if coax["created"]:
        show("БД.КОАКС записано", run("coax", ROWS, False))
print("ИТОГО", "(проверка, ничего не записано)" if mode == "dry" else "(записано)")

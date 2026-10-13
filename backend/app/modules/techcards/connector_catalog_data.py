"""Housing catalogues of the connector series beyond the first JST sheets (owner, 12.10.2026: «парсинг не закончен по
сериям разъёмов из БД терминалов»): every housing a maker lists per contact count, the board headers, the crimp
contacts and the documents the values were read from.

Read 12.10.2026 from the makers' own documents: JST catalogue sheets (jst-mfg.com/product/pdf/eng), Connfly drawings
(connfly.com), Molex sales drawings and Hirose / JAE / TE / Samtec catalogues and drawings. molex.com, hirose.com,
te.com and samtec.com do not answer automated requests, so their documents were read from the copies distributors
host unchanged (static.chipdip.ru, datasheet.lcsc.com) — every series names the exact file in `sources`. A count, a
part number or a header the document does not print is not here; a series whose document shows no circuit-1 mark that
can be placed on the wire side keeps «нумерация не сверена» (`conf` "low" on its family).

Each `variants` entry is (title, family id, format, counts) — the format rebuilds the part number exactly as the
document prints it — or (title, family id, (part number, …)) where the numbers do not follow one format. `headers` are
(title, format, counts). `contacts` are the crimp contacts the document lists for the series: (part number, wire).
The families here are drawn by `connector_housings.housing_of` like the rest (`CATALOG_FAMILIES` is appended to
`FAMILIES`); `erp_only` — not in SUPPLYMAP TcHousing.gs, so the warehouse name autofill skips them.
"""

from __future__ import annotations

from typing import Any

WTB, WTW, BTB = "Провод — плата", "Провод — провод", "Плата — плата"
AUTO, ROUND, POWER, IFACE = "Автомобильные", "Цилиндрические", "Силовые", "Интерфейсные"
PLUG, LUGS, RF = "Питание и штекеры", "Наконечники и клеммы", "ВЧ"
TOP, SIDE = "Вилка на плату, вертикальная", "Вилка на плату, угловая"
TOP_SMT, SIDE_SMT = "Вилка на плату SMD, вертикальная", "Вилка на плату SMD, угловая"
IVORY, BLACK = "#efe6cf", "#2b2b2b"

JST_PDF = "https://www.jst-mfg.com/product/pdf/eng/{}.pdf"
CONNFLY_PDF = "https://www.connfly.com/static/upload/file/pdf/{}.pdf"
LCSC = "https://datasheet.lcsc.com/datasheet/pdf/{}.pdf?productCode={}"
CHIPDIP = "https://static.chipdip.ru/lib/{}.pdf"


def _counts(first: int, last: int, *extra: int, step: int = 1) -> tuple[int, ...]:
    return tuple(range(first, last + 1, step)) + extra


def _even(first: int, last: int, *extra: int) -> tuple[int, ...]:
    return _counts(first, last, *extra, step=2)


def _family(identifier: str, title: str, pattern: str, pitch: float | None, *, rows: int = 1, start: str = "TL",
            order: str = "row", latch: str = "none", color: str = IVORY, conf: str = "low", note: str = "",
            src: str = "", **extra: Any) -> dict[str, Any]:
    family = {"id": identifier, "title": title, "pattern": pattern, "pitch": pitch, "rows": rows, "order": order,
              "start": start, "latch": latch, "color": color, "erp_only": True, "conf": conf, **extra}
    if note:
        family["note"] = note
    if src:
        family["src"] = src
    return family


UNCHECKED = "метки контакта 1 со стороны проводов на листе производителя нет — сверить по детали"
NO_DRAWING = "чертёж производителя не получен — нумерация не сверена, сверить по детали"
DF1B_SRC = "Hirose DF1B, каталог 01.07.2020, {}"


def _te_table(base: str, tens: dict[str, int]) -> dict[str, int]:
    """TE «N-NNNNNN-D» part numbers: the prefix gives the tens of the contact count, the last digit the units."""
    out = {}
    for prefix, ten in tens.items():
        for unit in range(10):
            count = ten * 10 + unit
            if 2 <= count:
                out[f"{prefix}{base}-{unit}"] = count
    return out


# TE C-353908 (Mini CT): white 353908-2 … 2-353908-0, blue 6-353908-2 … 8-353908-0 — 2 to 20 positions.
MINI_CT_TABLE = {key: count for key, count in _te_table("353908", {"": 0, "1-": 1, "2-": 2, "6-": 0, "7-": 1, "8-": 2}).items()
                 if count <= 20}
# TE C-770849 (SL-156): natural 770849-2 … 2-770849-4 (2 to 24 positions); 3- … 8-770849-2 — coloured 2-position.
SL156_TABLE = {key: count for key, count in _te_table("770849", {"": 0, "1-": 1, "2-": 2}).items() if count <= 24}
SL156_TABLE.update({f"{prefix}-770849-2": 2 for prefix in "345678"})


CATALOG_FAMILIES: tuple[dict[str, Any], ...] = (
    # JST EL — eEL «Housing»: 2, 3, 4 in one row, 4 (04N) and 6 in two rows, 9, 12, 15 in three rows («Contact position
    # location numbers» prints the grid, but not from which side it is seen).
    _family("jst-el", "JST EL", r"^EL[RP]-0*(\d+)VF?$", 4.5, rows_by={2: 1, 3: 1, 4: 1, 6: 2, 9: 3, 12: 3, 15: 3},
            latch="lever", note="раскладка по листу JST eEL; с какой стороны показаны номера, лист не пишет — сверить по детали"),
    _family("jst-el-n", "JST EL (4 конт. в 2 ряда)", r"^EL[RP]-04NVF?$", 4.5, rows=2, fixed=4, latch="lever",
            note="раскладка по листу JST eEL; с какой стороны показаны номера, лист не пишет — сверить по детали"),
    _family("jst-gvh", "JST GVH", r"^GVHR-0*(\d+)V-S$", 1.25, latch="ramp",
            note="лист JST eGVH не показывает метку контакта 1 со стороны проводов"),
    _family("jst-gvh-sf", "JST GVH (боковой ввод)", r"^GVHRSF-0*(\d+)V-S$", 1.25, latch="ramp",
            note="на листе JST eGVH метка контакта 1 есть у корпуса с боковым вводом; сторону проводов лист не показывает"),
    # SUH — eSUH «Housing» is drawn as eSH: the wire end with the protrusions at the top, «Circuit No.1» ▽ on the left
    # of that face -> from the wire side, ▽ face up, contact 1 on the RIGHT (as SH).
    _family("jst-suh", "JST SUH", r"^SUHR-0*(\d+)V-S", 0.8, start="TR", latch="mark", conf="high",
            src="JST eSUH, Housing", note="по чертежу JST eSUH: метка ▽ контакта 1 — со стороны проводов ▽ сверху, контакт 1 справа"),
    _family("jst-lbt", "JST LBT (2 силовых + сигнальные)", r"^LBTAR-0*(\d+)V-2K-K", 1.0, latch="ramp",
            note="2 силовых контакта по краям и сигнальные с шагом 1,0 — рисунок условный; метка контакта 1 на лицевой грани (JST eLBT)"),
    _family("jst-pud", "JST PUD (2 ряда)", r"^PUDP-0*(\d+)V-S$", 2.0, rows=2, order="col", latch="ramp", note=UNCHECKED),
    _family("jst-pad", "JST PAD (2 ряда)", r"^PADP-0*(\d+)V-1-S$", 2.0, rows=2, order="col", latch="lever", note=UNCHECKED),
    _family("jst-pnd", "JST PND (2 ряда)", r"^PNDP-0*(\d+)V-Z$", 2.0, rows=2, order="col", latch="lever", note=UNCHECKED),
    _family("jst-sqs", "JST SQS (пиропатрон)", r"^SQSR-0*(\d+)H-1[ABC]-[KMD]$", None, color=BLACK,
            note="лист JST eSQS номеров контактов не показывает"),
    _family("jst-zpd", "JST ZPD (2 ряда)", r"^ZPDR-0*(\d+)V-(?:S|1-Z)$", 1.5, rows=2, order="col", latch="ramp", note=UNCHECKED),
    _family("jst-nshd", "JST NSHD (2 ряда)", r"^NSHDR-0*(\d+)V-(?:Z|[12]-Z)$", 1.0, rows=2, order="col", latch="ramp", note=UNCHECKED),
    # SHJ — eSHJ «Socket housing»: front view (wire end up) has «Mark of No. 1 circuit» ▽ at the RIGHT of the face ->
    # from the wire side, ▽ face up, contact 1 on the LEFT.
    _family("jst-shj", "JST SHJ", r"^SHJP-0*(\d+)V-S", 1.0, latch="mark", conf="high", src="JST eSHJ, Socket housing",
            note="по чертежу JST eSHJ: метка ▽ контакта 1 справа на лицевой грани; со стороны проводов ▽ сверху — контакт 1 слева"),
    # RA — eRA «Housing»: the front face carries ▽ and the moulded odd numbers 1, 3, 5 from the left, the wire end is up
    # (the mating face with the square entries is the view below) -> from the wire side, that face up: 1 top-RIGHT,
    # 2 under it.
    _family("jst-ra", "JST RA (2 ряда)", r"^RA-(\d{2})11$", 2.54, rows=2, order="col", start="TR", latch="mark", color=BLACK,
            conf="high", src="JST eRA, Housing",
            note="по чертежу JST eRA: на лицевой грани ▽ и нечётные номера 1, 3, 5; со стороны проводов этой гранью вверх — контакт 1 справа вверху"),
    # Hirose
    _family("hirose-df13-2r", "Hirose DF13 (2 ряда)", r"^DF13-(\d+)DS-1[.,]25C", 1.25, rows=2, order="col", latch="ramp",
            note=UNCHECKED),
    # DF19 — catalogue 01.01.2024 «Single Row Socket (Cable Type)»: the face view has the cable end up (p. 2 «Cable
    # Type»: the cable leaves the socket away from the vertical header) and the «Contact No. 1 Indicator» ▽ at the right
    # of the mating edge; the view under it is the mating end. From the wire side, ▽ face up: contact 1 on the LEFT.
    # DF19G repeats the ▽ on the shell face over the same contact, so the ▽ stays over contact 1 either way up.
    _family("hirose-df19", "Hirose DF19", r"^DF19G?-(\d+)S-1C", 1.0, latch="mark", conf="high",
            src="Hirose DF19, каталог 01.01.2024, Single Row Socket (Cable Type)",
            note="по каталогу Hirose DF19: метка ▽ контакта 1 на грани у стыковочного торца; со стороны проводов ▽ сверху — контакт 1 слева"),
    _family("hirose-df20", "Hirose DF20 (2 ряда)", r"^DF20A-(\d+)DS-1C", 1.0, rows=2, order="col", latch="ramp", note=UNCHECKED),
    _family("hirose-df61", "Hirose DF61", r"^DF61-(\d+)S-2[.,]2C", 2.2, latch="lever", note=UNCHECKED),
    # DF1B — catalogue 01.07.2020 (third angle). The sockets' front view has the mating end UP: the top view shows the
    # square pin entries with lead-in chamfers and the anti-mis-insertion bosses, the in-line socket's lock hook sits
    # near that end and engages the plug's lever inside its 7 mm shroud. The wire end is the bottom view, where the
    # in-line sockets and plugs carry the moulded contact numbers. From the wire side, with the face that carries the
    # «Contact No1. mark» (front face) up, the bottom view keeps left and right: contact 1 on the LEFT.
    _family("hirose-df1b", "Hirose DF1B (гнездовой корпус)", r"^DF1B-(\d+)S-2[.,]5RC?$", 2.5, latch="mark", conf="high",
            src=DF1B_SRC.format("Single Row Socket"),
            note="по каталогу Hirose DF1B: метка ① контакта 1 на лицевой грани у торца проводов; со стороны проводов этой гранью вверх — контакт 1 слева"),
    # DS: «1» on the front face over the first column; the mating DF1BZ-*DP header numbers No. 1 / No. 2 across the rows
    # of one column (and DES prints 1, 3, 5 … over 2, 4, 6 …), so 2 is under 1.
    _family("hirose-df1b-2r", "Hirose DF1B (гнездовой корпус, 2 ряда)", r"^DF1B-(\d+)DS-2[.,]5RC$", 2.5, rows=2,
            order="col", latch="mark", conf="high", src=DF1B_SRC.format("Double Row Socket + Double Row Straight Pin Header"),
            note="по каталогу Hirose DF1B: «1» на лицевой грани, у ответной вилки № 1 и № 2 в одном столбце; со стороны проводов этой гранью вверх — 1 слева вверху, 2 под ним"),
    # ES: the wire-end view prints 1, 2, 3 … n from the left along the front-face edge; the lock hook is on the back face.
    _family("hirose-df1b-es", "Hirose DF1B (гнездовой корпус на провод)", r"^DF1B-(\d+)ES-2[.,]5RC$", 2.5,
            latch="mark", conf="high", src=DF1B_SRC.format("Single Row In-line Socket (Full Lock Type)"),
            note="по каталогу Hirose DF1B: цифры 1…n на торце проводов у лицевой грани (защёлка на обратной); лицевой гранью вверх — контакт 1 слева"),
    _family("hirose-df1b-des", "Hirose DF1B (гнездовой корпус на провод, 2 ряда)", r"^DF1B-(\d+)DES-2[.,]5RC$", 2.5,
            rows=2, order="col", latch="mark", conf="high", src=DF1B_SRC.format("Double Row In-line Socket (Full Lock Type)"),
            note="по каталогу Hirose DF1B: на торце проводов 1, 3, 5 … у лицевой грани с меткой ▽, 2, 4, 6 … под ними; ▽ сверху — 1 слева вверху"),
    # EP (full lock): the wire-end view prints 1, 2, 3 … from the left along the face with the lock lever.
    _family("hirose-df1b-p", "Hirose DF1B (штыревой корпус)", r"^DF1B-(\d+)EP-2[.,]5RC$", 2.5, latch="lever", conf="high",
            src=DF1B_SRC.format("Single Row In-line Plug (Full Lock Type)"),
            note="по каталогу Hirose DF1B: цифры 1…n на торце проводов у грани с рычагом замка; рычагом вверх — контакт 1 слева"),
    _family("hirose-df1b-p2r", "Hirose DF1B (штыревой корпус, 2 ряда)", r"^DF1B-(\d+)DEP-2[.,]5RC$", 2.5, rows=2,
            order="col", latch="lever",
            note="в каталоге Hirose DF1B у корпуса DEP на торце проводов только метка поляризации, номеров нет — сверить по детали"),
    # DF1BA (simple lock, panel mount) numbers the other way: the wire-end view prints 1, 2, 3 from the RIGHT (DEP:
    # 1, 3, 5 over 2, 4, 6) along the front face that carries the «Contact No1. mark».
    _family("hirose-df1ba-p", "Hirose DF1BA (штыревой корпус, простой замок)", r"^DF1BA-(\d+)EP-2[.,]5RC$", 2.5,
            start="TR", latch="mark", conf="high", src=DF1B_SRC.format("Single Row In-line Plug (Simple Lock Type)"),
            note="по каталогу Hirose DF1B: у DF1BA цифры 1, 2, 3 справа на торце проводов у лицевой грани; лицевой гранью вверх — контакт 1 справа"),
    _family("hirose-df1ba-p2r", "Hirose DF1BA (штыревой корпус, простой замок, 2 ряда)", r"^DF1BA-(\d+)DEP-2[.,]5RC$",
            2.5, rows=2, order="col", start="TR", latch="mark", conf="high",
            src=DF1B_SRC.format("Double Row In-line Plug (Simple Lock Type)"),
            note="по каталогу Hirose DF1B: у DF1BA на торце проводов 1, 3, 5 справа у грани с меткой контакта 1, 2, 4, 6 под ними — 1 справа вверху"),
    # JAE
    _family("jae-fis", "JAE FI-S", r"^FI-S(\d+)S$", 1.25, latch="ramp", color="#b9c0c6", note=UNCHECKED),
    _family("jae-fir", "JAE FI-R", r"^FI-RE(\d+)HL$", 0.5, latch="ramp", color="#b9c0c6", note=UNCHECKED),
    # Connfly (drawings C-DS10xx: the female housing is drawn without a circuit-1 mark that can be placed)
    _family("connfly-ds1070", "Connfly DS1070", r"^I-DS1070-SC[A-Z](\d{2})$", 2.54, latch="ramp", note=UNCHECKED),
    _family("connfly-ds1072", "Connfly DS1072", r"^I-DS1072-SC[A-Z]0(\d{2})$", 3.96, latch="ramp", color="#e6e8ea", note=UNCHECKED),
    _family("connfly-ds1073", "Connfly DS1073", r"^I-DS1073-SC[A-Z]0(\d{2})$", 3.96, latch="lever", note=UNCHECKED),
    _family("connfly-ds1074", "Connfly DS1074", r"^I-DS1074-SC[A-Z]0(\d{2})$", 5.08, latch="ramp", color="#e6e8ea", note=UNCHECKED),
    # Samtec ISD2 — «positions per row», two rows: ISD2-05-D = 10 contacts.
    _family("samtec-isd2", "Samtec ISD2 (2 ряда)", r"^ISD2-(\d{2})-D-[MS]", 2.0, rows=2, order="col", count_mul=2,
            latch="lever", color=BLACK, note="на листе Samtec метка ▽ контакта 1 есть, со стороны проводов не сверена"),
    # Molex — sales drawings (SD) read from the copies at static.chipdip.ru / datasheet.lcsc.com (13.10.2026, third
    # angle). A view placed directly above / below the front view keeps left and right; a rear view placed beside the
    # side view mirrors them; an isometric view shows the outside. Which end takes the wires: the end with the larger
    # crimp cavities and the moulded part / cavity numbers; the mating end has the small pin entries (or the towers of
    # the Fit families). Every family below names the drawing and the view its contact 1 is read from.
    _family("molex-minifit-jr-bk", "Molex Mini-Fit Jr. (чёрный)", r"^39-?03-?9(\d{2})2$", 4.2, rows=2, start="BR",
            latch="lever", color=BLACK, conf="high", src="Molex SD-5557-003",
            note="по чертежу Molex SD-5557-003: защёлка сверху, контакт 1 внизу справа, ряд 1…n дальше от защёлки",
            body={"extra": 5.4, "h": 11.6, "len": 19.6, "lock_w": 5.4, "lock_h": 2.8, "win": (3.4, 3.4)}),
    # Nano-Fit 105307 — the front view (latch face, towers of the mating end up) has «CIRCUIT 1» at its right on the
    # wire-end edge; the bottom view (wire end) keeps it there -> latch up, contact 1 on the RIGHT.
    _family("molex-nanofit-1r", "Molex Nano-Fit", r"^105307-?[12]2(\d{2})$", 2.5, start="TR", latch="lever", color=BLACK,
            color_by=((r"^105307-?2", IVORY),), conf="high", src="Molex SD-105307-100",
            note="по чертежу Molex SD-105307-100: метка CIRCUIT 1 справа на грани защёлки у торца проводов; защёлкой вверх — контакт 1 справа"),
    # Nano-Fit 105308 — the isometric view of the mating face, latch up: CKT 1 top-left, CKT 2 right of it, LAST CKT
    # bottom-right -> from the wire side, latch up: 1 top-RIGHT, the latch row first, both rows right to left.
    _family("molex-nanofit", "Molex Nano-Fit (2 ряда)", r"^105308-?[12]2(\d{2})$", 2.5, rows=2, start="TR", latch="lever",
            color=BLACK, color_by=((r"^105308-?2", IVORY),), conf="high", src="Molex SD-105308-100",
            note="по чертежу Molex SD-105308-100: со стыковки защёлкой вверх CKT 1 слева вверху; со стороны проводов — 1 справа вверху, ряд 1…n у защёлки"),
    # Nano-Fit plugs — the bottom view (wire end, latch catch up) marks CKT 1 at the left, LAST CKT at the right (2 rows:
    # CKT 2 right of CKT 1) — the mirror of the receptacles, as a mated pair must be.
    _family("molex-nanofit-plug-1r", "Molex Nano-Fit (вилка)", r"^200277-?[12]1(\d{2})$", 2.5, latch="lever", color=BLACK,
            color_by=((r"^200277-?2", IVORY),), conf="high", src="Molex 2002771000-SD",
            note="по чертежу Molex 2002771000-SD: на торце проводов упором защёлки вверх CKT 1 слева"),
    _family("molex-nanofit-plug", "Molex Nano-Fit (вилка, 2 ряда)", r"^201444-?[12]1(\d{2})$", 2.5, rows=2, latch="lever",
            color=BLACK, color_by=((r"^201444-?2", IVORY),), conf="high", src="Molex 2014441000-SD",
            note="по чертежу Molex 2014441000-SD: на торце проводов упором защёлки вверх CKT 1 слева вверху, CKT 2 справа от него"),
    _family("molex-microfit-plus", "Molex Micro-Fit+ (2 ряда)", r"^206461-?(\d{2})\d{2}$", 3.0, rows=2, latch="lever",
            color=BLACK, note="на чертеже Molex 2064610000-SD номеров контактов нет — сверить по детали"),
    # Mini-Fit TPA2 172708 — the wire-end views («before TPA assembly», latch up) mould 8 7 6 5 over 4 3 2 1: contact 1
    # bottom-right, as Mini-Fit Jr.
    _family("molex-minifit-tpa2", "Molex Mini-Fit TPA2 (2 ряда)", r"^172708-?0[0-2](\d{2})$", 4.2, rows=2, start="BR",
            latch="lever", color=BLACK, conf="high", src="Molex SD-172708-0000",
            note="по чертежу Molex SD-172708-0000: на торце проводов защёлкой вверх цифры 8 7 6 5 над 4 3 2 1 — контакт 1 внизу справа"),
    # 200453 — the wire-end view (latch block with «200453» up) moulds 4 3 2 1, FIRST CKT ID at the right.
    _family("molex-minifit-tpa2-1r", "Molex Mini-Fit TPA2", r"^200453-?10(\d{2})$", 4.2, start="TR", latch="lever",
            color=BLACK, conf="high", src="Molex 2004531000",
            note="по чертежу Molex 2004531000: на торце проводов защёлкой вверх цифры 4 3 2 1 — контакт 1 справа"),
    # Mini-Fit Max 215913 — the isometric view of the wire end, latch up, has «1» (FIRST CKT) on the right end wall.
    _family("molex-minifit-max-1r", "Molex Mini-Fit Max", r"^215913-?10(\d{2})$", 4.2, start="TR", latch="lever",
            color=BLACK, conf="high", src="Molex 2159130000-SD",
            note="по чертежу Molex 2159130000-SD: на торце проводов защёлкой вверх «1» на правой стенке — контакт 1 справа"),
    # ULTRA-FIT — the wire-end views (moulded «1», «172256» / «172258», latch up) put CKT 1 at the left (2 rows: CKT 2
    # right of it, LAST CKT bottom-right); the mating views below the front views mirror it.
    _family("molex-ultrafit-1r", "Molex ULTRA-FIT", r"^172256-?[12][01](\d{2})$", 3.5, latch="lever", color=BLACK,
            conf="high", src="Molex SD-172256-0001",
            note="по чертежу Molex SD-172256-0001: на торце проводов защёлкой вверх «1» и CKT 1 слева"),
    _family("molex-ultrafit", "Molex ULTRA-FIT (2 ряда)", r"^172258-?[34]1(\d{2})$", 3.5, rows=2, latch="lever", color=BLACK,
            color_by=((r"^172258-?4", IVORY),), conf="high", src="Molex 1722580003",
            note="по чертежу Molex 1722580003: на торце проводов защёлкой вверх CKT 1 слева вверху, ряд 1…n у защёлки"),
    # Pico-Lock — the front view is the flat face with the lance windows and the «CIRCUIT No1 INDICATOR MARK» ▽ over the
    # right cavity at the wire end; the top view (wire end) has CIRCUIT 1 at the right with that face down -> ▽ face
    # up, contact 1 on the LEFT. The locks are arms at both ends, so the ▽ is the face cue.
    _family("molex-picolock-20", "Molex Pico-Lock 2,0", r"^20534102(\d{2})$", 2.0, latch="mark", color=BLACK, conf="high",
            src="Molex 2053410000-SD", note="по чертежу Molex 2053410000-SD: метка ▽ над контактом 1; ▽ сверху — контакт 1 слева"),
    _family("molex-picolock-15", "Molex Pico-Lock 1,5", r"^504051(\d{2})01$", 1.5, latch="mark", conf="high",
            src="Molex 5040510000-SD", note="по чертежу Molex 5040510000-SD: метка ▽ над контактом 1; ▽ сверху — контакт 1 слева"),
    _family("molex-picolock-10", "Molex Pico-Lock 1,0", r"^503764(\d{2})01$", 1.0, latch="mark", conf="high",
            src="Molex SD-503764-001", note="по чертежу Molex SD-503764-001: метка ▽ над контактом 1; ▽ сверху — контакт 1 слева"),
    # Micro-Lock Plus 2.0 — the top view is the wire end (crimp cavities, CIRCUIT 1 MARK on the end wall), the lock
    # face at its bottom edge, CIRCUIT 1 at the right -> lock up, contact 1 on the LEFT.
    _family("molex-microlock-20", "Molex Micro-Lock Plus 2,0", r"^505570(\d{2})0[01]$", 2.0, latch="ramp",
            color_by=((r"1$", BLACK),), conf="high", src="Molex 5055700001",
            note="по чертежу Molex 5055700001: на торце проводов замок снизу, CIRCUIT 1 справа; замком вверх — контакт 1 слева"),
    # 505565, 214526 — the rear view (wire end, lock up) has the ▷ CKT. NO.1 MARK and CKT. NO.1 at the left; the plug
    # 214525 numbers its mating face CIRCUIT 1 … N from the left, so its wire end, lock up, has contact 1 on the RIGHT
    # (it mates with 214526 face to face).
    _family("molex-microlock-125", "Molex Micro-Lock Plus 1,25", r"^505565(\d{2})0\d$", 1.25, latch="ramp", conf="high",
            src="Molex 5055650002-SD", note="по чертежу Molex 5055650002-SD: на торце проводов замком вверх метка ▷ и CKT. NO.1 слева"),
    _family("molex-microlock-125-p", "Molex Micro-Lock Plus 1,25 (провод — провод)", r"^2145252(\d{2})\d$", 1.25,
            start="TR", latch="ramp", conf="high", src="Molex 2145250000-SD",
            note="по чертежу Molex 2145250000-SD: со стыковки замком вверх CIRCUIT 1 слева; со стороны проводов — контакт 1 справа"),
    _family("molex-microlock-125-s", "Molex Micro-Lock Plus 1,25 (провод — провод, гнёзда)", r"^214526(\d{2})0\d$", 1.25,
            latch="ramp", conf="high", src="Molex 2145260000-SD",
            note="по чертежу Molex 2145260000-SD: на торце проводов замком вверх метка ▷ и CIRCUIT 1 слева"),
    # Micro-Latch 51065 — the top view (wire end) has «1» and CIRCUIT 1 at the left with the slotted face at the bottom;
    # the lock ramp is on the opposite face -> ramp up, contact 1 on the LEFT. 502351 — rear view (wire end), lock up,
    # «1» at the left.
    _family("molex-microlatch", "Molex Micro-Latch", r"^51065-?(\d{2})0[01]$", 2.0, latch="ramp", conf="high",
            src="Molex 510650000-SD", note="по чертежу Molex 510650000-SD: на торце проводов защёлкой вверх «1» слева"),
    _family("molex-microlatch-502351", "Molex 502351 (под контакты Micro-Latch)", r"^502351-?(\d{2})0[01]$", 2.0,
            latch="ramp", color_by=((r"1$", BLACK),), conf="high", src="Molex SD-502351-001",
            note="по чертежу Molex SD-502351-001: на торце проводов замком вверх «1» слева"),
    # Mini-SPOX 5264 — the catalogue top view is the wire end (round wire entries) with CIRCUIT 1 at the left and the
    # face with the lance windows at its bottom (friction lock, no latch) -> windows down, contact 1 on the LEFT.
    _family("molex-minispox", "Molex Mini-SPOX", r"^50-?37-?5(\d{2})3$", 2.5, latch="windows", conf="high",
            src="Molex 5264-N, каталог MX01 B-102",
            note="по каталогу Molex 5264-N: на торце проводов окнами фиксаторов вниз CIRCUIT 1 слева"),
    # Pico-SPOX 87439 — the bottom view has the «MARK FOR CIRCUIT1» at the right under the mating-end view; the ramp
    # bump is on the opposite (top) face -> from the wire side, ramp up, contact 1 on the LEFT.
    _family("molex-picospox", "Molex Pico-SPOX", r"^87439-?(\d{2})0[01]$", 1.5, latch="ramp", color_by=((r"1$", BLACK),),
            conf="high", src="Molex 874390000-SD",
            note="по чертежу Molex 874390000-SD: метка «1» на нижней грани; со стороны проводов выступом защёлки вверх — контакт 1 слева"),
    # Micro-One, OneBlade, Zero-Hachi, CLIK-Mate 2.0 — the end view with the moulded marks (wire end), lock up, has
    # CIRCUIT 1 at the left.
    _family("molex-microone", "Molex Micro-One", r"^2127242(\d{2})[01]$", 2.0, latch="lever",
            color_by=((r"1$", BLACK),), conf="high", src="Molex 2127240000-SD",
            note="по чертежу Molex 2127240000-SD: на торце проводов замком вверх CIRCUIT 1 слева"),
    _family("molex-oneblade", "Molex OneBlade", r"^21409220(\d)0$", 1.0, latch="ramp", color=BLACK, conf="high",
            src="Molex 2140920000-SD", note="по чертежу Molex 2140920000-SD: на торце проводов защёлками вверх CIRCUIT 1 слева"),
    _family("molex-zerohachi", "Molex Zero-Hachi", r"^2147192(\d{2})0$", 0.8, latch="mark", color=BLACK, conf="high",
            src="Molex 2147190000-SD",
            note="по чертежу Molex 2147190000-SD: метка контакта 1 на грани с окнами фиксаторов; этой гранью вверх — контакт 1 слева"),
    # KK 254 2695 — the face with the windows has «1» at its wire-end corner on the left (mating end up); the bottom view
    # keeps it -> windows up, contact 1 LEFT; the ramp is on the opposite face -> ramp up, contact 1 on the RIGHT.
    _family("molex-kk254", "Molex KK 254", r"^22-?01-?[23](\d{2})[17]$", 2.54, start="TR", latch="ramp", conf="high",
            src="Molex 26950000-SD (SD-2695)",
            note="по чертежу Molex 26950000-SD: «1» на грани с окнами; со стороны проводов рампой защёлки вверх — контакт 1 справа"),
    # C-Grid III — the top view (mating end, pin lead-ins) has CCT. No. 1 at the right, the face with the polarising
    # buttons and «90123» at its bottom; the bottom view (wire end) keeps it -> buttons up, contact 1 on the RIGHT.
    _family("molex-cgrid3-1r", "Molex C-Grid III", r"^90123-?0[12](\d{2})$", 2.54, start="TR", color=BLACK, conf="high",
            src="Molex SD-90123",
            note="по чертежу Molex SD-90123: со стороны проводов гранью с кнопками поляризации и надписью 90123 вверх — контакт 1 справа"),
    # Milli-Grid 151192 — the front view (mating end) has CKT 1 top-left, CKT 2 under it, CKT 3 right of 1, the face with
    # the ▽ 1st CKT mark and the latch up -> from the wire side 1 top-RIGHT, 2 under it.
    _family("molex-milligrid-151192", "Molex Milli-Grid (151192)", r"^151192-?00(\d{2})$", 2.0, rows=2, order="col",
            start="TR", latch="mark", color=BLACK, conf="high", src="Molex 1511921001",
            note="по чертежу Molex 1511921001: метка ▽ 1st CKT на грани с защёлкой; ▽ сверху — 1 справа вверху, 2 под ним"),
    _family("molex-clikmate-20", "Molex CLIK-Mate 2,0", r"^502439-?(\d{2})00$", 2.0, latch="ramp", conf="high",
            src="Molex 5024391500", note="по чертежу Molex 5024391500: на торце проводов замком вверх «1» и метка ▷ слева"),
    # Series of БД.ТЕР without a drawing the ERP could read (molex.com did not answer, 13.10.2026): the housing part
    # numbers are those Molex lists (its part sheets, or the LCSC / Heilind pages that quote them); the count comes
    # from the part number, the numbering stays unchecked.
    _family("molex-kk396", "Molex KK 396 (41695)", r"^(?:00)?09-?50-?8(\d{2})\d$", 3.96, latch="ramp", note=NO_DRAWING),
    _family("molex-minilock", "Molex Mini-Lock", r"^0?51163-?(\d{2})00$", 2.5, latch="lever", note=NO_DRAWING),
    _family("molex-sl-1r", "Molex SL (1 ряд)", r"^0?50-?57-?94(\d{2})$", 2.54, latch="ramp", color=BLACK, note=NO_DRAWING),
    _family("molex-igrid", "Molex iGrid (2 ряда)", r"^501646-?(\d{2})0[01]$", 2.0, rows=2, order="col", latch="lever",
            color_by=((r"1$", BLACK),), note=NO_DRAWING),
    _family("molex-boardin", "Molex Board-in", r"^0?35023-?00(\d{2})$", 2.0, note=NO_DRAWING),
    # TE Mini CT — C-353908: the top view is the wire end; «CIRCUIT No.1» is at its right with the +AMP face (the lock
    # ramp) at the bottom -> from the wire side, lock up, contact 1 on the LEFT.
    _family("te-minict", "TE AMP Mini CT", r"^((?:\d-)?353908-\d)$", 1.5, latch="ramp", table=MINI_CT_TABLE, conf="high",
            src="TE C-353908", note="по чертежу TE C-353908: защёлка сверху, контакт 1 слева",
            color_by=((r"^[678]-", "#3b6fb6"),)),
    # TE SL-156 — C-770849: the front view is the mating face (square post entries) with 1 … n moulded left to right,
    # the ramp face up -> from the wire side, ramp up, contact 1 on the RIGHT.
    _family("te-sl156", "TE SL-156", r"^((?:\d-)?770849-\d)$", 3.96, start="TR", latch="ramp", table=SL156_TABLE,
            conf="high", src="TE C-770849",
            note="по чертежу TE C-770849: со стороны стыковки 1…n слева направо, защёлкой вверх; со стороны проводов — контакт 1 справа"),
)

CATALOG_FAMILY_SAMPLES: dict[str, str] = {
    "jst-el": "ELR-03V", "jst-el-n": "ELR-04NV", "jst-gvh": "GVHR-03V-S", "jst-gvh-sf": "GVHRSF-04V-S", "jst-suh": "SUHR-04V-S", "jst-lbt": "LBTAR-05V-2K-K",
    "jst-pud": "PUDP-10V-S", "jst-pad": "PADP-10V-1-S", "jst-pnd": "PNDP-10V-Z", "jst-sqs": "SQSR-02H-1A-K",
    "jst-zpd": "ZPDR-10V-S", "jst-nshd": "NSHDR-16V-Z", "jst-shj": "SHJP-06V-S", "jst-ra": "RA-1011",
    "hirose-df13-2r": "DF13-10DS-1.25C", "hirose-df19": "DF19-8S-1C", "hirose-df20": "DF20A-10DS-1C", "hirose-df61": "DF61-2S-2.2C",
    "hirose-df1b": "DF1B-4S-2.5R", "hirose-df1b-2r": "DF1B-18DS-2.5RC", "hirose-df1b-es": "DF1B-4ES-2.5RC",
    "hirose-df1b-des": "DF1B-10DES-2.5RC", "hirose-df1b-p": "DF1B-5EP-2.5RC",
    "hirose-df1b-p2r": "DF1B-10DEP-2.5RC", "hirose-df1ba-p": "DF1BA-5EP-2.5RC", "hirose-df1ba-p2r": "DF1BA-10DEP-2.5RC",
    "jae-fis": "FI-S10S", "jae-fir": "FI-RE31HL",
    "connfly-ds1070": "I-DS1070-SCV04", "connfly-ds1072": "I-DS1072-SCT004", "connfly-ds1073": "I-DS1073-SCW004",
    "connfly-ds1074": "I-DS1074-SCT004", "samtec-isd2": "ISD2-05-D-M", "te-minict": "353908-4", "te-sl156": "770849-4",
    "molex-minifit-jr-bk": "39-03-9082", "molex-nanofit-1r": "105307-1204", "molex-nanofit": "105308-1208",
    "molex-nanofit-plug-1r": "200277-1104", "molex-nanofit-plug": "201444-1108", "molex-microfit-plus": "206461-0800",
    "molex-minifit-tpa2": "172708-0008", "molex-minifit-tpa2-1r": "200453-1004", "molex-minifit-max-1r": "215913-1004",
    "molex-ultrafit-1r": "172256-1104", "molex-ultrafit": "172258-3108", "molex-picolock-20": "2053410204",
    "molex-picolock-15": "5040510401", "molex-picolock-10": "5037640401", "molex-microlock-20": "5055700600",
    "molex-microlock-125": "5055650600", "molex-microlock-125-p": "2145252060", "molex-microlock-125-s": "2145260600",
    "molex-microlatch": "510650600", "molex-microlatch-502351": "502351-0600", "molex-picospox": "87439-0400", "molex-minispox": "50-37-5043", "molex-microone": "2127242040", "molex-oneblade": "2140922040",
    "molex-zerohachi": "2147192080", "molex-kk254": "22-01-2041", "molex-cgrid3-1r": "90123-0106",
    "molex-milligrid-151192": "1511920010", "molex-clikmate-20": "502439-0400",
    # Housings Molex lists: 09-50-8051 — part sheet 0009508051 (KK 3.96 mm, friction ramp, 5 circuits); 51163-0400 — part
    # sheet 0511630400 (Mini-Lock, 4 circuits); 50-57-9404 — SL 70066, 4 positions (LCSC C293342, which links the Molex
    # drawing 050579404_sd); 501646-1000 — iGrid dual row, 10 circuits, natural (Heilind 501646-1000); 35023-0004 —
    # Board-in crimp housing, 4 circuits (Molex part 350230004).
    "molex-kk396": "09-50-8051", "molex-minilock": "51163-0400", "molex-sl-1r": "50-57-9404",
    "molex-igrid": "501646-1000", "molex-boardin": "35023-0004",
}


def _jst(identifier: str, title: str, sheet: str, category: str, terminals: tuple[str, ...], variants: tuple, headers: tuple,
         contacts: tuple, note: str = "", range_checked: bool = True) -> dict[str, Any]:
    return {"id": identifier, "title": title, "maker": "JST", "category": category, "terminals": terminals,
            "variants": variants, "headers": headers, "contacts": contacts, "note": note,
            "datasheet": JST_PDF.format(sheet), "range_checked": range_checked, "range_src": f"JST {sheet}",
            "sources": ((f"JST {sheet}", JST_PDF.format(sheet)),)}


def _series(identifier: str, title: str, maker: str, category: str, terminals: tuple[str, ...], variants: tuple,
            sources: tuple[tuple[str, str], ...], *, headers: tuple = (), contacts: tuple = (), note: str = "",
            range_checked: bool = True, range_src: str = "") -> dict[str, Any]:
    return {"id": identifier, "title": title, "maker": maker, "category": category, "terminals": terminals,
            "variants": variants, "headers": headers, "contacts": contacts, "note": note,
            "datasheet": sources[0][1], "range_checked": range_checked, "range_src": range_src or sources[0][0],
            "sources": sources}


def _cd(doc: str) -> str:
    """static.chipdip.ru copy of a maker document by its number: DOC043115887 → lib/115/DOC043115887.pdf."""
    return CHIPDIP.format(f"{doc[3:6]}/DOC{doc}")


MINIFIT_SD = ("Molex SD-5557-003", _cd("011947843"))
MOLEX_NOTE = "нумерация по чертежам Molex со стороны проводов не сверена"

MOLEX_SERIES: tuple[dict[str, Any], ...] = (
    _series("molex-picoblade", "Molex PicoBlade", "Molex", WTB, ("51021", "51047"),
            (("Розетка 51021", "molex-picoblade", "51021-{:02d}00", _counts(2, 15)),
             ("Вилка провод — провод 51047", "molex-picoblade-plug", "51047-{:02d}00", _counts(2, 10))),
            (("Molex PicoBlade 51021, лист каталога", _cd("047405434")), ("Molex 510470000-SD", _cd("031503632"))),
            contacts=(("500798000", "гнездо, AWG 28–26"), ("500588000", "гнездо, AWG 32–28"), ("501258000", "штырь, AWG 28–26"))),
    _series("molex-minifit-jr", "Molex Mini-Fit Jr.", "Molex", WTB, ("5557", "Mini-Fit Jr."),
            (("Розетка 5557, натуральная, UL94V-2", "molex-minifit-jr", "39-01-2{:02d}0", _even(2, 24)),
             ("Розетка 5557, натуральная, UL94V-0", "molex-minifit-jr", "39-01-2{:02d}5", _even(2, 24)),
             ("Розетка 5557, чёрная, UL94V-0", "molex-minifit-jr-bk", "39-03-9{:02d}2", _even(2, 24))),
            (MINIFIT_SD,), note="ответная вилка 5559, вилки на плату 5566, 5569; гнёзда 5556"),
    _series("molex-minifit-plus", "Molex Mini-Fit Plus", "Molex", WTB, ("Mini-Fit Plus", "Mini-Fit Plus Grounding Terminal"),
            (("Розетка Mini-Fit Jr. 5557, натуральная, UL94V-2", "molex-minifit-jr", "39-01-2{:02d}0", _even(2, 24)),),
            (MINIFIT_SD,), note="контакты Mini-Fit Plus — в корпуса Mini-Fit Jr. 5557 / 5559 (Series в «Терминалы»)"),
    _series("molex-microfit", "Molex Micro-Fit 3.0", "Molex", WTB, ("43025", "43645", "43020", "43640"),
            (("Розетка, 2 ряда 43025", "molex-microfit", "43025-{:02d}00", _even(2, 24)),
             ("Розетка, 1 ряд 43645", "molex-microfit-1r", "43645-{:02d}00", _counts(2, 12)),
             ("Вилка, 2 ряда 43020", "molex-microfit-plug", "43020-{:02d}00", _even(2, 24)),
             ("Вилка, 1 ряд, на панель 43640", "molex-microfit-plug-1r", "43640-{:02d}08", (2, 3, 4, 6, 7))),
            (("Molex SDA-43025", _cd("055327843")), ("Molex SDA-43645", _cd("011221865")),
             ("Molex 43020, лист каталога", _cd("047506990")), ("Molex SD-43640-001", _cd("011955153"))),
            note="гнёзда 43030, 46235, штыри 43031; 1-рядные вилки 43640 без крепления на панель на чертеже SD-43640-001 не перечислены"),
    _series("molex-nanofit", "Molex Nano-Fit", "Molex", WTB, ("Nano-Fit",),
            (("Розетка, 1 ряд, чёрная", "molex-nanofit-1r", "105307-12{:02d}", _counts(2, 8)),
             ("Розетка, 1 ряд, натуральная", "molex-nanofit-1r", "105307-22{:02d}", _counts(2, 8)),
             ("Розетка, 2 ряда, чёрная", "molex-nanofit", "105308-12{:02d}", _even(4, 16)),
             ("Розетка, 2 ряда, натуральная", "molex-nanofit", "105308-22{:02d}", _even(4, 16)),
             ("Вилка провод — провод, 1 ряд, чёрная", "molex-nanofit-plug-1r", "200277-11{:02d}", _counts(2, 8)),
             ("Вилка провод — провод, 1 ряд, натуральная", "molex-nanofit-plug-1r", "200277-21{:02d}", _counts(2, 8)),
             ("Вилка провод — провод, 2 ряда, чёрная", "molex-nanofit-plug", "201444-11{:02d}", _even(4, 16)),
             ("Вилка провод — провод, 2 ряда, натуральная", "molex-nanofit-plug", "201444-21{:02d}", _even(4, 16))),
            (("Molex SD-105307-100", _cd("043115887")), ("Molex SD-105308-100", _cd("056441323")),
             ("Molex 2002771000-SD", _cd("011158288")), ("Molex 2014441000-SD", _cd("012237572"))),
            note="вилки на плату 105309, 105310, 105311–105314; гнёзда 105300, штыри 201447, TPA 105325"),
    _series("molex-microfit-plus", "Molex Micro-Fit+", "Molex", WTB, ("Micro-Fit+", "Micro-Fit+ PCIe"),
            (("Розетка, 2 ряда, чёрная", "molex-microfit-plus", "206461-{:02d}00", _even(2, 24)),),
            (("Molex 2064610000-SD", _cd("034869295")),),
            note="гнёзда 206460; цвета и ключи: -xx01 натуральная, -xx02 красная, -xx04 жёлтая; PCIe 219114 (12 + 4 конт.) не рисуется",
            range_checked=False),
    _series("molex-minifit-tpa2", "Molex Mini-Fit TPA2", "Molex", WTB, ("Mini-Fit TPA2",),
            (("Розетка, 2 ряда", "molex-minifit-tpa2", "172708-00{:02d}", _even(2, 24)),
             ("Розетка, 1 ряд", "molex-minifit-tpa2-1r", "200453-10{:02d}", _counts(2, 6))),
            (("Molex SD-172708-0000", _cd("011963338")), ("Molex 2004531000", _cd("021539216"))),
            note="гнёзда 172718, 202988; TPA 172709"),
    _series("molex-minifit-max", "Molex Mini-Fit Max", "Molex", WTB, ("Mini-Fit Max",),
            (("Розетка, 1 ряд", "molex-minifit-max-1r", "215913-10{:02d}", _counts(2, 6)),),
            (("Molex 2159130000-SD", LCSC.format("9d1df9cd2818e0156c9da353e2c9fc8d", "C17540385")),),
            note="2-рядные корпуса 212514 — чертёж не найден", range_checked=False),
    _series("molex-ultrafit", "Molex ULTRA-FIT", "Molex", WTB, ("ULTRA-FIT",),
            (("Розетка, 1 ряд, с TPA", "molex-ultrafit-1r", "172256-11{:02d}", _counts(2, 8)),
             ("Розетка, 1 ряд, без TPA", "molex-ultrafit-1r", "172256-10{:02d}", _counts(2, 8)),
             ("Розетка, 2 ряда, чёрная", "molex-ultrafit", "172258-31{:02d}", _even(4, 16)),
             ("Розетка, 2 ряда, натуральная", "molex-ultrafit", "172258-41{:02d}", _even(4, 16))),
            (("Molex SD-172256-0001", _cd("034869138")), ("Molex 1722580003", _cd("011945469"))),
            note="гнёзда 172253"),
    _series("molex-picolock", "Molex Pico-Lock", "Molex", WTB, ("Pico-Lock",),
            (("Корпус шаг 2,0", "molex-picolock-20", "20534102{:02d}", _counts(2, 6)),
             ("Корпус шаг 1,5", "molex-picolock-15", "504051{:02d}01", _counts(2, 12)),
             ("Корпус шаг 1,0", "molex-picolock-10", "503764{:02d}01", _counts(2, 6))),
            (("Molex 2053410000-SD", _cd("011297753")), ("Molex 5040510000-SD", _cd("013778807")),
             ("Molex SD-503764-001", _cd("011843066"))),
            contacts=(("2053425028", "шаг 2,0, AWG 22–20"),), note="контакты шага 1,5 — 504052, шага 1,0 — 503765"),
    _series("molex-microlock-plus", "Molex Micro-Lock Plus", "Molex", WTB, ("Micro-Lock Plus",),
            (("Корпус шаг 2,0, натуральный", "molex-microlock-20", "505570{:02d}00", _counts(2, 16)),
             ("Корпус шаг 2,0, чёрный", "molex-microlock-20", "505570{:02d}01", _counts(2, 16)),
             ("Корпус шаг 1,25", "molex-microlock-125", "505565{:02d}00", _counts(2, 16)),
             ("Провод — провод 1,25, штыревой", "molex-microlock-125-p", "2145252{:02d}0", _counts(2, 9)),
             ("Провод — провод 1,25, гнездовой", "molex-microlock-125-s", "214526{:02d}00", _counts(2, 9))),
            (("Molex 5055700001", _cd("021798243")), ("Molex 5055650002-SD", _cd("011955731")),
             ("Molex 2145250000-SD", _cd("052941559")), ("Molex 2145260000-SD", _cd("036844049"))),
            contacts=(("5054311000", "шаг 1,25, AWG 30–26"), ("2145241000", "штырь 1,25, AWG 30–26")),
            note="корпуса 213719, 505432 — чертежи не найдены", range_checked=False),
    _series("molex-microlatch", "Molex Micro-Latch", "Molex", WTB, ("Micro-Latch",),
            (("Корпус 51065", "molex-microlatch", "51065{:02d}00", _counts(2, 15)),
             ("Корпус 502351, натуральный", "molex-microlatch-502351", "502351-{:02d}00", _counts(2, 15)),
             ("Корпус 502351, чёрный", "molex-microlatch-502351", "502351-{:02d}01", _counts(2, 15))),
            (("Molex 510650000-SD", _cd("059528857")), ("Molex SD-502351-001", _cd("041645867"))),
            contacts=(("502128000", "AWG 30–24"),), note="корпус 502351 принимает контакты 50212, 50372, 56161, 560085"),
    _series("molex-minispox", "Molex Mini-SPOX", "Molex", WTB, ("Mini-SPOX",),
            (("Корпус 5264", "molex-minispox", "50-37-5{:02d}3", _counts(2, 15)),),
            (("Molex Mini-SPOX 5264, лист каталога", _cd("047474155")),),
            note="контакты 5263"),
    _series("molex-picospox","Molex Pico-SPOX", "Molex", WTB, ("Pico-SPOX",),
            (("Корпус, натуральный", "molex-picospox", "87439-{:02d}00", _counts(2, 15)),
             ("Корпус, чёрный", "molex-picospox", "87439-{:02d}01", _counts(2, 15))),
            (("Molex 874390000-SD", _cd("048512109")),),
            contacts=(("874210000", "AWG 26–24"),), note="контакты 87421-000* (AWG 26–24) и 87421-010* (AWG 30–26)"),
    _series("molex-microone", "Molex Micro-One", "Molex", WTB, ("Micro-One",),
            (("Корпус с TPA, натуральный", "molex-microone", "2127242{:02d}0", _counts(2, 8)),
             ("Корпус с TPA, чёрный", "molex-microone", "2127242{:02d}1", _counts(2, 8))),
            (("Molex 2127240000-SD", LCSC.format("f6f28c29addd5298c2ea9815d12feaef", "C3268230")),),
            contacts=(("561349001", "AWG 24–22"),),
            note="корпуса 205979 — чертёж не найден", range_checked=False),
    _series("molex-oneblade", "Molex OneBlade", "Molex", WTB, ("OneBlade",),
            (("Корпус", "molex-oneblade", "21409220{}0", _counts(2, 8)),),
            (("Molex 2140920000-SD", LCSC.format("ec9d5c7cd772cf79382461165b8238fe", "C3266109")),),
            contacts=(("2140935000", "AWG 30–28"),)),
    _series("molex-zerohachi", "Molex Zero-Hachi", "Molex", WTB, ("Zero-Hachi",),
            (("Корпус", "molex-zerohachi", "2147192{:02d}0", _counts(2, 20)),),
            (("Molex 2147190000-SD", LCSC.format("c7076ff0722df1ed5ce187d7673c4af7", "C7603638")),),
            contacts=(("2147205001", "AWG 32–30"),)),
    _series("molex-kk254", "Molex KK 254", "Molex", WTB, ("KK 254",),
            (("Корпус 2695", "molex-kk254", "22-01-2{:02d}1", _counts(2, 16)),
             ("Корпус 2695-R", "molex-kk254", "22-01-2{:02d}7", _counts(2, 16)),
             ("Корпус 2695-RP", "molex-kk254", "22-01-3{:02d}7", _counts(2, 16))),
            (("Molex SD-2695", _cd("011879936")),),
            note="гнёзда 2759, 6459, 41572, 4809, 8088"),
    _series("molex-cgrid3", "Molex C-Grid III", "Molex", WTB, ("C-Grid III",),
            (("Корпус, 1 ряд, 2 кнопки поляризации", "molex-cgrid3-1r", "90123-01{:02d}", _counts(2, 32)),
             ("Корпус, 1 ряд, 4 кнопки поляризации", "molex-cgrid3-1r", "90123-02{:02d}", _counts(2, 32))),
            (("Molex SD-90123", _cd("035237016")),),
            note="2-рядные корпуса 90142, 90156, 90160 — чертежи не найдены", range_checked=False),
    _series("molex-milligrid", "Molex Milli-Grid", "Molex", WTB, ("51110", "Milli-Grid"),
            (("Корпус 51110, 2 ряда", "molex-milligrid", "51110-{:02d}50", _even(4, 32)),
             ("Корпус 51110 с ключами", "molex-milligrid", ("51110-0460", "51110-0660", "51110-0860", "51110-1060", "51110-1260",
                                                             *(f"51110-{n:02d}51" for n in _even(14, 32)))),
             ("Корпус 151192, 2 ряда", "molex-milligrid-151192", "151192-00{:02d}", _even(4, 50))),
            (("Molex Milli-Grid 51110, лист каталога MX07", _cd("047287477")),
             ("Molex 1511921001", LCSC.format("045b3c208df0ef4741b42924f908715d", "C3269195"))),
            contacts=(("50394-8051", "AWG 30–26"), ("50394-8052", "AWG 30–26")),
            note="корпуса 151100, 151098 — чертежи не найдены", range_checked=False),
    _series("molex-clikmate", "Molex CLIK-Mate", "Molex", WTB, ("502578", "CLIK-Mate"),
            (("Корпус шаг 2,0 (502439)", "molex-clikmate-20", "502439-{:02d}00", _counts(2, 10, 12, 13, 14, 15)),),
            (("Molex 5024391500", _cd("031162073")),),
            contacts=(("5024380000", "шаг 2,0, AWG 26–22"),),
            note="корпуса 502578, 503149, 502380 — чертежи не найдены", range_checked=False),
    _series("molex-picoclasp", "Molex Pico-Clasp", "Molex", WTB, ("501189", "501330"),
            (("Корпус, 1 ряд (501330)", "molex-picoclasp-1r", "501330-{:02d}00", _counts(2, 15)),),
            (("Molex Pico-Clasp 501330, лист каталога", _cd("033381093")),),
            note="2-рядные корпуса 501189, 501939 — чертежи не найдены", range_checked=False),
    # Molex part sheets (static.chipdip.ru copies): each confirms one housing; the series tables and the sales drawings
    # (SD-41695-001, 511630400_sd) were not reachable, so the range and the numbering stay unchecked.
    _series("molex-kk396", "Molex KK 396", "Molex", WTB, ("KK 396 (legacy KK 156)", "KK 396/508"),
            (("Корпус 41695, без защёлки", "molex-kk396", ("09-50-8020",)),
             ("Корпус 41695, с фрикционной защёлкой", "molex-kk396", ("09-50-8051",))),
            (("Molex 0009508020, лист детали", _cd("011657093")), ("Molex 0009508051, лист детали", _cd("029580511"))),
            note="известны только корпуса из листов деталей; корпуса 2139 (09-50-3…) — лист не найден; контакты 2478, 2578, 6838",
            range_checked=False),
    _series("molex-minilock", "Molex Mini-Lock", "Molex", WTB, ("Mini-Lock",),
            (("Корпус 51163, натуральный", "molex-minilock", ("51163-0400",)),),
            (("Molex 0511630400, лист детали", _cd("012341229")),),
            note="известен только корпус из листа детали (4 конт.)", range_checked=False),
)

DF1B_DOC = ("Hirose DF1B, каталог 01.07.2020", LCSC.format("ba434fb92ec248c0cb9579d7a1b767a6", "C392028"))
DF13_DOC = ("Hirose DF13, каталог", LCSC.format("64333dc9655d4e31b6b072799d4297c9", "C88348"))
JAE_FI_DOC = ("JAE, обзор Board-to-Cable v2.2", LCSC.format("9e3ff33ecc0cd871bc0b1e9ff5103b8b", "C570476"))

CATALOG_SERIES: tuple[dict[str, Any], ...] = (
    # --- JST catalogue sheets ---------------------------------------------------------------------------------------
    _jst("jst-el", "JST EL", "eEL", WTW, ("EL",),
         (("Розетка ELR (под штыри SLM)", "jst-el", "ELR-{:02d}V", (2, 3, 4, 6, 9, 12, 15)),
          ("Розетка ELR, 4 конт. в 2 ряда", "jst-el-n", ("ELR-04NV", "ELR-04NVF")),
          ("Розетка ELR только на весу (F)", "jst-el", ("ELR-02VF",)),
          ("Вилка ELP (под гнёзда SLF)", "jst-el", "ELP-{:02d}V", (2, 3, 4, 6, 9, 12, 15)),
          ("Вилка ELP, 4 конт. в 2 ряда", "jst-el-n", ("ELP-04NV",))),
         (), (("SLM-01T-P1.3E", "штырь, AWG 26–20"), ("SLF-01T-P1.3E", "гнездо, AWG 26–20"),
              ("SLM-41T-P1.3E", "штырь, AWG 20–16"), ("SLF-41T-P1.3E", "гнездо, AWG 20–16"),
              ("SLM-42T-P1.3E", "штырь, 2 провода"), ("SLF-42T-P1.3E", "гнездо, 2 провода")),
         note="провод — провод: розетка ELR под штыри, вилка ELP под гнёзда"),
    _jst("jst-gvh", "JST GVH", "eGVH", WTB, ("GVH",),
         (("Корпус с боковым вводом (SF)", "jst-gvh-sf", "GVHRSF-{:02d}V-S", _counts(2, 15)),
          ("Корпус обычный", "jst-gvh", ("GVHR-03V-S",))),
         ((TOP_SMT, "BM{:02d}B-GVHS-TB", _counts(2, 15), "jst-gvh-sf"), (SIDE_SMT, "SM{:02d}B-GVHS-TB", (3,), "jst-gvh")),
         (("SGVH-002T-P0.2", "AWG 30–26"), ("SGVHSF-002T-0.2", "AWG 30–26, боковой ввод"))),
    _jst("jst-suh", "JST SUH", "eSUH", WTB, ("SUH", "SUR"),
         (("С выступами (-B)", "jst-suh", "SUHR-{:02d}V-S-B", _counts(2, 6, 8, 10, 12, 14, 16, 17, 20)),
          ("Без выступов", "jst-suh", "SUHR-{:02d}V-S", _counts(2, 6, 10, 12, 14, 16, 20))),
         ((TOP_SMT, "BM{:02d}B-SURS-TF", _counts(2, 6, 8, 10, 12, 14, 16, 17, 20)),
          (SIDE_SMT, "SM{:02d}B-SURS-TF", _counts(2, 6, 8, 10, 12, 14, 16, 17, 20))),
         (("SSUH-003T-P0.15", "AWG 32–28"),)),
    _jst("jst-lbt", "JST LBT", "eLBT", WTB, ("LBT",),
         (("Корпус A: 2 силовых + 1, 3, 5, 7 сигнальных", "jst-lbt", "LBTAR-{:02d}V-2K-K", (3, 5, 7, 9)),),
         ((SIDE_SMT, "SM{:02d}B-LBTAKS-TD-N2T-K", (3, 5, 7, 9)),),
         (("SLBTAD-01T-M0.5", "силовой, AWG 24–20"), ("SSH-003T-P0.2-H", "сигнальный, AWG 28")),
         note="число контактов — силовые и сигнальные вместе"),
    _jst("jst-pud", "JST PUD", "ePUD", WTB, ("PUD",),
         (("Корпус, 2 ряда", "jst-pud", "PUDP-{:02d}V-S", _even(8, 40)),),
         ((TOP, "B{:02d}B-PUDSS", _even(8, 40)), (TOP + ", с бобышкой", "B{:02d}B-PUDSS-1", _even(8, 40)),
          (SIDE, "S{:02d}B-PUDSS-1", _even(8, 40))),
         (("SPUD-002T-P0.5", "AWG 28–24"), ("SPUD-001T-P0.5", "AWG 26–22"))),
    _jst("jst-pad", "JST PAD", "ePAD", WTB, ("PAD", "PADP"),
         (("Корпус, 2 ряда", "jst-pad", "PADP-{:02d}V-1-S", (10, 12, 14, 16, 20, 22, 24, 40)),),
         ((TOP, "B{:02d}B-PADSS-F", (10, 12, 14, 16, 20, 22, 24)), (TOP + ", с бобышкой", "B{:02d}B-PADSS-1F", (10, 12, 14, 16, 20, 22, 24, 40)),
          (SIDE, "S{:02d}B-PADSS-1", (10, 12, 14, 16, 20, 24))),
         (("SPH-002T-P0.5L", "AWG 28–24"), ("SPH-001T-P0.5L", "AWG 26–22"))),
    _jst("jst-pnd", "JST PND", "ePND", WTB, ("PND", "PNDP"),
         (("Корпус, 2 ряда", "jst-pnd", "PNDP-{:02d}V-Z", _even(8, 42)),),
         ((TOP + ", с бобышкой", "B{:02d}B-PNDZS-1", _even(8, 42)), (TOP, "B{:02d}B-PNDZS", (8, 10, 12, 20, 22, 32))),
         (("SPND-002T-C0.5", "AWG 28–24"), ("SPND-001T-C0.5", "AWG 26–22"))),
    _jst("jst-sqs", "JST SQS", "eSQS", AUTO, ("SQS",),
         (("Корпус, ключ A, чёрный", "jst-sqs", ("SQSR-02H-1A-K",)), ("Корпус, ключ B, зелёный", "jst-sqs", ("SQSR-02H-1B-M",)),
          ("Корпус, ключ C, оранжевый", "jst-sqs", ("SQSR-02H-1C-D",))),
         (), (("SQS-A03GF-P-P", "0,3–0,5 мм²"), ("SQS-A03GI-P-P", "0,3–0,5 мм²")),
         note="разъём пиропатрона подушки безопасности; CPA SQSC-02H-1A-Y / SQSC-02H-2A-Y"),
    _jst("jst-zpd", "JST ZPD", "eZPD", WTB, ("ZPD",),
         (("Корпус, 2 ряда", "jst-zpd", "ZPDR-{:02d}V-S", _even(10, 30)), ("Корпус, 2 ряда (32–40)", "jst-zpd", "ZPDR-{:02d}V-1-Z", _even(32, 40))),
         ((TOP_SMT, "BM{:02d}B-ZPDSS-TF", _even(10, 30)), (TOP_SMT, "BM{:02d}B-ZPDZS-TF", _even(32, 40)),
          (SIDE_SMT, "SM{:02d}B-ZPDSS-TF", _even(10, 30)), (SIDE_SMT, "SM{:02d}B-ZPDZS-TF", _even(32, 40))),
         (("SZPD-002T-P0.3", "AWG 28–24"),)),
    _jst("jst-nshd", "JST NSHD", "eNSHD", WTB, ("NSHD",),
         (("Корпус, 2 ряда", "jst-nshd", ("NSHDR-16V-Z", "NSHDR-20V-Z", "NSHDR-24V-Z", "NSHDR-28V-Z", "NSHDR-32V-1-Z", "NSHDR-40V-2-Z")),),
         ((TOP_SMT, "BM{:02d}B-NSHDZS-TFT", (16, 20, 24, 28)), (TOP_SMT, "BM{:02d}B-NSHDZS-1-TFT", (32, 40)),
          (SIDE_SMT, "SM{:02d}B-NSHDZS-TF", (16, 20, 24, 28)), (SIDE_SMT, "SM{:02d}B-NSHDZS-1-TF", (32, 40))),
         (("SNSHD-003T-P0.2", "AWG 32–28"),)),
    _jst("jst-shj", "JST SHJ", "eSHJ", WTB, ("SHJ",),
         (("Корпус", "jst-shj", "SHJP-{:02d}V-S(HF)", (2, 6)),),
         ((TOP_SMT, "BM{:02d}B-SHJS-TB(HF)", (6,)), (SIDE_SMT, "SM{:02d}B-SHJH-TF(HF)", (2, 6))),
         (("SSHJ-003T-P0.2", "AWG 32–28"),)),
    _series("jst-ra", "JST RA", "JST", WTB, ("RA",),
            (("Корпус, 2 ряда", "jst-ra", "RA-{:02d}11", (10, 16, 20, 26, 30, 34, 40, 50, 60)),),
            (("JST eRA (копия листа на LCSC)", LCSC.format("68d757359fc6485c9fc0b479f24e115d", "C566594")),),
            headers=((TOP, "RA-H{:02d}1TD-1190", (10, 16, 20, 26, 30, 34, 40, 50, 60)),
                     (SIDE, "RA-H{:02d}1SD-1190", (10, 16, 20, 26, 34, 40, 50, 60))),
            contacts=(("RA-SC1290", "AWG 26–22"),), note="вилки на плату по MIL-C-83503", range_src="JST eRA"),
    # --- Hirose -----------------------------------------------------------------------------------------------------
    _series("hirose-df1b", "Hirose DF1B", "Hirose", WTB, ("DF1B", "DF1"),
            (("Гнездовой корпус, 1 ряд", "hirose-df1b", "DF1B-{}S-2.5R", _counts(2, 16, 18, 20)),
             ("Гнездовой корпус, 2 ряда", "hirose-df1b-2r", "DF1B-{}DS-2.5RC", _even(4, 30, 34, 36, 40)),
             ("Гнездовой корпус на провод (полный замок), 1 ряд", "hirose-df1b-es", "DF1B-{}ES-2.5RC", _counts(2, 13)),
             ("Гнездовой корпус на провод (полный замок), 2 ряда", "hirose-df1b-des", "DF1B-{}DES-2.5RC", _even(10, 34)),
             ("Штыревой корпус (полный замок), 1 ряд", "hirose-df1b-p", "DF1B-{}EP-2.5RC", _counts(2, 13)),
             ("Штыревой корпус (полный замок), 2 ряда", "hirose-df1b-p2r", "DF1B-{}DEP-2.5RC", _even(10, 34)),
             ("Штыревой корпус (простой замок), 1 ряд", "hirose-df1ba-p", "DF1BA-{}EP-2.5RC", _counts(2, 10, 12)),
             ("Штыревой корпус (простой замок), 2 ряда", "hirose-df1ba-p2r", "DF1BA-{}DEP-2.5RC", _even(4, 30, 34))),
            (DF1B_DOC,),
            headers=((TOP, "DF1BZ-{}P-2.5DSA", _counts(2, 16, 18, 20), "hirose-df1b"),
                     (SIDE, "DF1BZ-{}P-2.5DS", _counts(2, 16, 18, 20), "hirose-df1b"),
                     (TOP, "DF1BZ-{}DP-2.5DSA", _even(4, 30, 34, 36, 40), "hirose-df1b-2r"),
                     (SIDE, "DF1BZ-{}DP-2.5DS", _even(4, 30, 34, 36, 40), "hirose-df1b-2r")),
            contacts=(("DF1B-2022SCF", "гнездо, AWG 22–20"), ("DF1B-2022SCFA", "гнездо, AWG 22–20, золото"),
                      ("DF1B-2428SCF", "гнездо, AWG 28–24"), ("DF1B-2428SCFA", "гнездо, AWG 28–24, золото"),
                      ("DF1B-30SCF", "гнездо, AWG 30"), ("DF1B-30SCFA", "гнездо, AWG 30, золото"),
                      ("DF1B-2022PCF", "штырь, AWG 22–20"), ("DF1B-2022PCFB", "штырь, AWG 22–20, золото"),
                      ("DF1B-2428PCF", "штырь, AWG 28–24"), ("DF1-PD2428SCFB", "штырь, AWG 28–24, золото"),
                      ("DF1B-30PCF", "штырь, AWG 30"), ("DF1B-30PCFB", "штырь, AWG 30, золото")),
            note="гнёзда — в корпуса S, DS, ES, DES; штыри — в EP, DEP"),
    _series("hirose-df11", "Hirose DF11", "Hirose", WTB, ("DF11", "DF11EP"),
            (("Гнездовой корпус, 2 ряда", "hirose-df11", "DF11-{}DS-2C", _even(4, 32)),),
            (("Hirose DF11, каталог 01.06.2020", LCSC.format("22dd23afaeccfeedcfc5a20a3c317420", "C185144")),),
            headers=((TOP, "DF11-{}DP-2DSA", _even(4, 32)), (TOP_SMT, "DF11C-{}DP-2V", _even(4, 32, 40))),
            contacts=(("DF11-22SCF", "AWG 22"), ("DF11-2428SCF", "AWG 28–24"), ("DF11-30SCF", "AWG 30")),
            range_src="Hirose DF11, каталог"),
    _series("hirose-df13", "Hirose DF13", "Hirose", WTB, ("DF13",),
            (("Корпус, 1 ряд", "hirose-df13", "DF13-{}S-1.25C", _counts(2, 15)),
             ("Корпус, 2 ряда", "hirose-df13-2r", "DF13-{}DS-1.25C", (10, 20, 30, 40))),
            (DF13_DOC,),
            headers=((TOP, "DF13-{}P-1.25DSA", _counts(2, 15), "hirose-df13"),
                     (SIDE, "DF13A-{}P-1.25H", _counts(2, 12, 14, 15), "hirose-df13"),
                     (TOP_SMT, "DF13C-{}P-1.25V", _counts(2, 12, 14, 15), "hirose-df13")),
            contacts=(("DF13-2630SCF", "AWG 30–26"), ("DF13-3032SCF", "AWG 32–30")), range_src="Hirose DF13, каталог"),
    _series("hirose-df14", "Hirose DF14", "Hirose", WTB, ("DF14",),
            (("Корпус", "hirose-df14", "DF14-{}S-1.25C", _counts(2, 10, 15, 20, 25, 30)),),
            (("Hirose EDC3-160306-01 (DF14-*S-1.25C)", LCSC.format("4227c54f0e95ae3c8c5a14d87fc6a566", "C185124")),),
            range_src="Hirose EDC3-160306-01"),
    _series("hirose-df19", "Hirose DF19", "Hirose", WTB, ("DF19",),
            (("Корпус", "hirose-df19", "DF19-{}S-1C", (8, 14, 20, 30)),
             ("Корпус с экраном (EMI)", "hirose-df19", "DF19G-{}S-1C(06)", (8, 14, 20, 30))),
            (("Hirose DF19, каталог 01.01.2024", LCSC.format("d0e6797f2ca3f8a48e9b5ce30acf807e", "C3268449")),),
            headers=((TOP_SMT, "DF19-{}P-1V(52)", (14, 20)), (SIDE_SMT, "DF19G-{}P-1H(52)", (8, 14, 20, 30))),
            contacts=(("DF19A-2830SCFA", "AWG 30–28"), ("DF19A-3032SCFA", "AWG 32–30"), ("DF19A-36SCFA", "AWG 36")),
            range_src="Hirose DF19, каталог"),
    _series("hirose-df20", "Hirose DF20", "Hirose", WTB, ("DF20",),
            (("Корпус, 2 ряда", "hirose-df20", "DF20A-{}DS-1C", (10, 20, 30, 40, 50)),),
            (("Hirose DF20, каталог", LCSC.format("58c284d870df321ba7391c5da3bf96c1", "C5378701")),),
            headers=((TOP_SMT, "DF20EF-{}DP-1V(52)", (10, 20, 30, 40, 50)), (TOP_SMT + ", с бобышкой", "DF20EG-{}DP-1V(52)", (10, 20, 30, 40, 50)),
                     (SIDE_SMT, "DF20F-{}DP-1H(52)", (10, 20, 30, 40))),
            contacts=(("DF20F-2830SCFA", "AWG 30–28"), ("DF20F-3032SCFA", "AWG 32–30")), range_src="Hirose DF20, каталог"),
    _series("hirose-df61", "Hirose DF61", "Hirose", WTB, ("DF61",),
            (("Корпус (11 — красный, 12, 13 — другие цвета)", "hirose-df61", ("DF61-2S-2.2C",)),),
            (("Hirose DF61, каталог", LCSC.format("557db64e6055e99ed1eff0a53c923865", "C531061")),),
            headers=((TOP, "DF61-{}P-2.2V", (2,)),),
            contacts=(("DF61-2226SCF", "AWG 26–22"), ("DF61-2628SCF", "AWG 28–26")),
            note="в каталоге только 2-контактный корпус; суффикс (11)/(12)/(13) — цвет", range_src="Hirose DF61, каталог"),
    # --- JAE --------------------------------------------------------------------------------------------------------
    _series("jae-fix", "JAE FI-X", "JAE", WTB, ("FI-X",),
            (("Кабельная вилка", "jae-fix", ("FI-X30H", "FI-X30HL")),), (JAE_FI_DOC,),
            contacts=(("FI-XC3A-1-15000", "AWG 30–36"),), note="только 30 контактов; L — с защёлками", range_src="JAE, обзор v2.2"),
    _series("jae-fir", "JAE FI-R", "JAE", WTB, ("FI-R",),
            (("Кабельная вилка с экраном, под провод", "jae-fir", "FI-RE{}HL", (21, 31, 41, 51)),),
            (("JAE FI-R, MB-0153-4", LCSC.format("c8110807488e21f4fc506aaa43b04ed7", "C570465")), JAE_FI_DOC),
            headers=((SIDE_SMT, "FI-RE{}S-HF", (21, 31, 41, 51)), (TOP_SMT, "FI-RE{}S-VF", (21, 31, 41, 51))),
            contacts=(("FI-RC3-1A-1E-15000", "верхний ряд"), ("FI-RC3-1B-1E-15000", "нижний ряд")), range_src="JAE FI-R, MB-0153-4"),
    _series("jae-fis", "JAE FI-S", "JAE", WTB, ("FI-S",),
            (("Кабельная вилка, 1 ряд", "jae-fis", "FI-S{}S", _counts(2, 6, 8, 10, 15, 20, 25, 30)),),
            (JAE_FI_DOC, ("JAE FI, каталог", LCSC.format("ac03f7cea7680ba50396b3199cf5220b", "C570469"))),
            contacts=(("FI-C3-A1-15000", "AWG 32–28"),), range_src="JAE, обзор v2.2"),
    # --- Connfly ----------------------------------------------------------------------------------------------------
    _series("connfly-ds1070", "Connfly DS1070", "Connfly", WTB, ("I-DS1070", "DS1070"),
            (("Корпус, слоновая кость", "connfly-ds1070", "I-DS1070-SCV{:02d}", _counts(2, 20)),),
            (("Connfly C-DS1070-XXXXXX-D", CONNFLY_PDF.format("DS1070")),),
            contacts=(("T-DS1070-SC600", "AWG 28–22"),), note="шаг 2,54, тип 2510"),
    _series("connfly-ds1072", "Connfly DS1072", "Connfly", WTB, ("I-DS1072", "DS1072"),
            (("Корпус, прозрачный", "connfly-ds1072", "I-DS1072-SCT0{:02d}", _counts(2, 13)),),
            (("Connfly C-DS1072-XXXXX-D", CONNFLY_PDF.format("DS1072")),),
            contacts=(("T-DS1072-SC600", "AWG 24–18"),)),
    _series("connfly-ds1073", "Connfly DS1073 (серия MF)", "Connfly", WTB, ("I-DS1073", "DS1073"),
            (("Корпус, белый", "connfly-ds1073", "I-DS1073-SCW0{:02d}", _counts(2, 13)),),
            (("Connfly C-DS1073-XXXXX-E", CONNFLY_PDF.format("DS1073")),),
            contacts=(("T-DS1073-SC600", "AWG 22–18"),), note="шаг 3,96, тип VH"),
    _series("connfly-ds1074", "Connfly DS1074", "Connfly", WTB, ("T-DS1074", "DS1074"),
            (("Корпус, прозрачный", "connfly-ds1074", "I-DS1074-SCT0{:02d}", _counts(2, 10)),),
            (("Connfly C-DS1074-XXXXX-D", CONNFLY_PDF.format("DS1074")),),
            contacts=(("T-DS1074-SC600", "AWG 24–18"),)),
    _series("connfly-ds1069", "Connfly DS1069 (аналог JST XH)", "Connfly", WTB, ("I-DS1069",),
            (("Корпус, белый", "connfly-ds1069", "I-DS1069-SCW0{:02d}", _counts(2, 20)),),
            (("Connfly C-DS1069-XXXXX-E", CONNFLY_PDF.format("DS1069")),),
            contacts=(("T-DS1069-SC600", "AWG 28–22"),)),
    _series("connfly-ds1066", "Connfly DS1066 (шаг 2,0)", "Connfly", WTB, ("I-DS1066",),
            (("Корпус, белый", "connfly-ds1066", "I-DS1066-SCW0{:02d}", _counts(2, 15)),
             ("Корпус, оранжевый", "connfly-ds1066", "I-DS1066-SCE0{:02d}", _counts(2, 15))),
            (("Connfly C-DS1066-XXXXX-C", CONNFLY_PDF.format("DS1066")),),
            contacts=(("T-DS1066-SC600", "AWG 30–24"),), note="тип PH; на складе — I-DS1066-SCW02 / SCW03"),
    _series("connfly-ds1071", "Connfly DS1071 (Dupont 2,54)", "Connfly", WTB, ("I-DS1071-SCB01", "I-DS1071-SCB02", "BLD2, BLS2"),
            (("Корпус, 1 ряд", "connfly-ds1071", "I-DS1071-SCB01X{}", _counts(1, 40)),
             ("Корпус, 2 ряда", "connfly-ds1071", "I-DS1071-SCB02X{}", _counts(2, 40))),
            (("Connfly C-DS1071-XXXXX-B", CONNFLY_PDF.format("DS1071")),),
            contacts=(("T-DS1071-SC600", "AWG 28–22"),),
            note="номер корпуса по чертежу: ряды × контактов в ряду (1×1…1×40, 2×2…2×40)"),
    # --- Samtec, TE -------------------------------------------------------------------------------------------------
    _series("samtec-isd2", "Samtec ISD2", "Samtec", WTB, ("ISD2, ITD2", "ISD2"),
            (("Корпус с защёлкой (M)", "samtec-isd2", "ISD2-{:02d}-D-M", (5, 7, 10, 15, 20, 25, 30)),
             ("Корпус с винтами (S)", "samtec-isd2", "ISD2-{:02d}-D-S", (5, 7, 10, 15, 20, 25, 30))),
            (("Samtec F-221 (2 мм Tiger Eye)", LCSC.format("847b1e07e7b1800b2b4711a473424829", "C17268036")),),
            contacts=(("CC81L-2426-01-L", "AWG 26–24"), ("CC81L-2830-01-L", "AWG 30–28")),
            note="в номере — контактов в ряду, рядов 2 (ISD2-05-D — 10 конт.); ответная вилка T2M"),
    _series("te-minict", "TE AMP Mini CT", "TE", WTB, ("AMP Mini CT", "Mini CT"),
            (("Корпус, белый", "te-minict", ("353908-2", "353908-3", "353908-4", "353908-5", "353908-6", "353908-7", "353908-8",
                                             "353908-9", *(f"1-353908-{unit}" for unit in range(10)), "2-353908-0")),
             ("Корпус, синий", "te-minict", ("6-353908-2", "6-353908-3", "6-353908-4", "6-353908-5", "6-353908-6", "6-353908-7",
                                             "6-353908-8", "6-353908-9", *(f"7-353908-{unit}" for unit in range(10)), "8-353908-0"))),
            (("TE C-353908 (корпус)", LCSC.format("f8428b09358c7046279d183a3699019d", "C392607")),
             ("TE C-353907 (контакт)", LCSC.format("be9f945df3d94195a01e9f7b04dc7ade", "C392604"))),
            contacts=(("353907-1", "AWG 28–24"),), range_src="TE C-353908"),
    _series("te-sl156", "TE SL-156", "TE", WTB, ("SL-156 Connector Contacts", "SL-156"),
            (("Корпус, натуральный", "te-sl156", ("770849-2", "770849-3", "770849-4", "770849-5", "770849-6", "770849-7", "770849-8",
                                                  "770849-9", *(f"1-770849-{unit}" for unit in range(10)),
                                                  *(f"2-770849-{unit}" for unit in range(5)))),
             ("Корпус 2 конт., цветной", "te-sl156", tuple(f"{prefix}-770849-2" for prefix in "345678"))),
            (("TE C-770849 (корпус)", LCSC.format("6dfe50de0ef7803f2ab7cae28e1480bd", "C201364")),
             ("TE C-350980 (контакт)", LCSC.format("1f98bf588eacc1ea238cf5be8ffbb384", "C188440"))),
            contacts=(("350980-1", "AWG 24–18"), ("3-350980-1", "AWG 24–18")),
            note="цветные 2-контактные: 3- чёрный, 4- серый, 5- красный, 6- синий, 7- коричневый, 8- жёлтый",
            range_src="TE C-770849"),
)

# Molex after the rest of the catalogue: the gallery order comes from `connector_series._order`, not from here.
CATALOG_SERIES = CATALOG_SERIES + MOLEX_SERIES

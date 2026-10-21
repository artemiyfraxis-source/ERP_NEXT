"""Спецификация жгута (ГОСТ 2.106, форма 1 / 1а), А4; первый лист — основная надпись формы 2, далее — 2а."""
import eskd
from eskd import TA, line, rect, text
from harness_data import BASE, BRAID, BRAID_M, CONNECTORS, E4, MARK_TUBE, MARKED, NAME, SB, SPLICES, TUBES, connector_groups, contact_counts, ref_range, wire_kinds

COLS = [6, 6, 8, 70, 63, 10, 22]
HEADER = ["Формат", "Зона", "Поз.", "Обозначение", "Наименование", "Кол.", "Приме-\nчание"]
ROW_H, HEADER_H, TOP = 8, 15, 292
BOTTOM_FIRST, BOTTOM_NEXT = 45, 20  # над основной надписью формы 2 (40 мм) / 2а (15 мм)


def metres(m):
    return f"{m:.3f}".replace(".", ",")


def spec_parts():
    """Прочие изделия: (наименование, кол., примечание) — позиции 1…N."""
    parts = [(f"{c.kind} {c.housing} {c.maker}", len(refs), ref_range(refs)) for refs, c in connector_groups()]
    parts += [(f"Контакт {contact}", n, ref_range(refs)) for contact, (n, refs) in contact_counts().items()]
    parts.append(("Гильза соединительная", len(SPLICES), "при обжиме"))
    return parts


def wire_positions():
    """Позиция спецификации провода по (сечение, цвет)."""
    first = len(spec_parts()) + 1
    return {(mm2, color): pos for pos, (mm2, color, _) in enumerate(wire_kinds(), first)}


def spec_rows():
    """Строки: (формат, поз., обозначение, наименование, кол., примечание); str — раздел; None — пустая."""
    items = ["Документация",
             ("A3", "", SB, "Сборочный чертеж", "", ""),
             ("A3", "", E4, "Схема электрическая", "", ""),
             ("", "", "", "соединений", "", ""),
             "Прочие изделия"]
    parts = spec_parts()
    items += [("", str(pos), "", name, str(qty), note) for pos, (name, qty, note) in enumerate(parts, 1)]
    materials = [(f"Провод НВ4 {mm2} {color}", mm / 1000) for mm2, color, mm in wire_kinds()]
    materials += [(f"Трубка {tube} мм, черная", m) for tube, m in TUBES]
    materials.append((BRAID, BRAID_M))
    items.append("Материалы")
    items += [("", str(pos), "", name, metres(m), "м") for pos, (name, m) in enumerate(materials, len(parts) + 1)]
    items += [("", str(pos), "", f"Маркировка {CONNECTORS[ref].mark} {MARK_TUBE}", "1", ref)
              for pos, ref in enumerate(MARKED, len(parts) + len(materials) + 1)]
    rows = []
    for item in items:
        rows += [None, item, None] if isinstance(item, str) else [item]
    return rows[1:]


def page(rows, bottom):
    """Графы и строки спецификации от верха рамки до bottom."""
    x0 = 20
    rect(x0, bottom, x0 + 185, TOP, "05_ТАБЛИЦЫ", lw=50)
    line((x0, TOP - HEADER_H), (x0 + 185, TOP - HEADER_H), "05_ТАБЛИЦЫ", lw=50)
    offs = [x0 + sum(COLS[:i]) for i in range(len(COLS))]
    for x, w, caption in zip(offs, COLS, HEADER):
        if x > x0:
            line((x, bottom), (x, TOP), "05_ТАБЛИЦЫ", lw=50)
        if w < 10:
            text(caption, (x + w / 2, TOP - HEADER_H / 2), h=2.5, rot=90, width=0.8)
        else:
            lines = caption.split("\n")
            for k, s in enumerate(lines):
                text(s, (x + w / 2, TOP - HEADER_H / 2 + (len(lines) - 1) * 2.25 - k * 4.5), h=2.5 if w < 25 else 3.5)
    yy = TOP - HEADER_H - ROW_H
    while yy > bottom:
        line((x0, yy), (x0 + 185, yy), "05_ТАБЛИЦЫ", lw=eskd.THIN)
        yy -= ROW_H
    for r, row in enumerate(rows):
        yc = TOP - HEADER_H - ROW_H * r - ROW_H / 2
        if isinstance(row, str):  # раздел — по центру графы «Наименование», подчеркнут
            cx = offs[4] + COLS[4] / 2
            text(row, (cx, yc))
            line((cx - len(row) * 1.25, yc - 2.5), (cx + len(row) * 1.25, yc - 2.5), "04_ТЕКСТ", lw=eskd.THIN)
        elif row:
            fmt, pos, desig, name, qty, note = row
            for col, val in [(0, fmt), (2, pos), (5, qty)]:
                if val:
                    text(val, (offs[col] + COLS[col] / 2, yc), h=2.5 if col == 0 else 3.5,
                         width=0.6 if len(val) > 3 else 0.8)
            for col, val in [(3, desig), (4, name), (6, note)]:
                if val:
                    text(val, (offs[col] + 1.5, yc), align=TA.MIDDLE_LEFT,
                         width=0.7 if len(val) > COLS[col] / 2.25 else 0.8)


def spec_sheets():
    rows = spec_rows()
    first = int((TOP - HEADER_H - BOTTOM_FIRST) // ROW_H)
    nxt = int((TOP - HEADER_H - BOTTOM_NEXT) // ROW_H)
    chunks = [rows[:first]]
    rest = rows[first:]
    while rest:
        chunks.append(rest[:nxt])
        rest = rest[nxt:]
    docs = []
    for i, chunk in enumerate(chunks, 1):
        doc = eskd.new_sheet()
        eskd.frame(210, 297, None)  # на А4 место графы 26 занимает шапка спецификации
        page(chunk, BOTTOM_FIRST if i == 1 else BOTTOM_NEXT)
        if i == 1:
            eskd.title_form2(20, 5, BASE, NAME, len(chunks))
        else:
            eskd.title_form2a(20, 5, BASE, i)
        docs.append(doc)
    return docs

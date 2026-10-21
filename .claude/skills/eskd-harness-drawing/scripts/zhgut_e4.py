"""Схема электрическая соединений Э4 (А3) — вид как схема Э4 в ERP: соединители таблицами,
провода своим цветом, сростки точками. ГОСТ 2.701/2.702 (схема, перечень элементов), 2.710.
"""
import ezdxf

import eskd
from eskd import TA, line, rect, table, text
from harness_data import (CONNECTORS, DEVIATION, E4, NAME, SB, SPLICES, STRIP_MM, TOLERANCE, free_end_wires,
                          pin_wire, unused_pins_text)

ROW = 5
COLORS = {"красный": (220, 30, 30), "черный": (20, 20, 20), "белый": (150, 150, 150), "синий": (30, 90, 220),
          "зеленый": (20, 150, 50), "желтый": (215, 175, 0)}
LEFT_COLS = [("Цвет", 8), ("Сечение", 13), ("Марка", 31), ("Терминал", 25), ("Цепь", 9), ("№", 6)]
TABLE_W = sum(w for _, w in LEFT_COLS)
pins = {}  # «X1:1» → точка подключения провода на таблице соединителя


def connector_table(ref, x0, row1_y, left):
    """Соединитель таблицей: шапка «X1  «5»», графы, строки контактов, внизу тип корпуса."""
    c = CONNECTORS[ref]
    cols = LEFT_COLS if left else LEFT_COLS[::-1]
    top = row1_y + ROW / 2 + ROW + 6
    bottom = row1_y - (c.pins - 1) * ROW - ROW / 2 - ROW
    rect(x0, bottom, x0 + TABLE_W, top, "02_ЭЛЕМЕНТЫ")
    text(ref, (x0 + 2, top - 3), align=TA.MIDDLE_LEFT)
    line((x0, top - 6), (x0 + TABLE_W, top - 6), "02_ЭЛЕМЕНТЫ")
    for yy in [top - 6 - ROW] + [row1_y - ROW / 2 - k * ROW for k in range(c.pins)]:
        line((x0, yy), (x0 + TABLE_W, yy), "02_ЭЛЕМЕНТЫ", lw=None if yy == bottom + ROW else eskd.THIN)
    x = x0
    for title, w in cols:
        if x > x0:
            line((x, bottom + ROW), (x, top - 6), "02_ЭЛЕМЕНТЫ", lw=eskd.THIN)
        text(title, (x + w / 2, top - 6 - ROW / 2), h=2.5, width=0.8)
        for k in range(c.pins):
            yy = row1_y - k * ROW
            wire_ = pin_wire(f"{ref}:{k + 1}")
            if title == "№":
                text(str(k + 1), (x + w / 2, yy), h=2.5)
            elif wire_:
                mm2, color = wire_[3], wire_[4]
                if title == "Цвет":
                    hatch = eskd.msp.add_hatch(dxfattribs={"layer": "02_ЭЛЕМЕНТЫ"})
                    hatch.rgb = COLORS[color]
                    hatch.paths.add_polyline_path([(x + 2, yy - 1.5), (x + w - 2, yy - 1.5), (x + w - 2, yy + 1.5),
                                                   (x + 2, yy + 1.5)], is_closed=True)
                else:
                    value = {"Сечение": f"{mm2} мм²", "Марка": f"НВ4 {mm2} {color}", "Терминал": c.contact}
                    if title in value:
                        text(value[title], (x + 1, yy), h=2.5, align=TA.MIDDLE_LEFT, width=0.8)
        x += w
    text(f"{c.maker} {c.housing}", (x0 + 2, bottom + ROW / 2), h=2.5, align=TA.MIDDLE_LEFT)
    for k in range(c.pins):
        pins[f"{ref}:{k + 1}"] = (x0 + TABLE_W if left else x0, row1_y - k * ROW)


def wire(num, points, color, labels=(0, 1)):
    """Провод — ломаная своего цвета; номер над участком у соединителя."""
    eskd.msp.add_lwpolyline(points, dxfattribs={"layer": "01_ПРОВОДА", "true_color": ezdxf.rgb2int(COLORS[color])})
    for end in labels:
        a, b = (points[0], points[1]) if end == 0 else (points[-1], points[-2])
        text(str(num), (a[0] + 3 if b[0] > a[0] else a[0] - 3, a[1] + 0.8), h=2.5, align=TA.BOTTOM_CENTER)


def free_end(num, p):
    """Свободный конец — хвост с поперечной чертой и номером провода."""
    line((p[0], p[1] - 1.2), (p[0], p[1] + 1.2), "01_ПРОВОДА")
    text(str(num), (p[0] + 3, p[1]), h=2.5, align=TA.MIDDLE_LEFT)


def splice(ref, p, color, label_pos):
    """Сросток — залитая точка цвета провода."""
    hatch = eskd.msp.add_hatch(dxfattribs={"layer": "01_ПРОВОДА"})
    hatch.rgb = COLORS[color]
    hatch.paths.add_edge_path().add_arc(p, 1.1, 0, 360)
    text(ref, label_pos, h=2.5)


def e4_sheet1():
    doc = eskd.new_sheet()
    eskd.frame(420, 297, E4)
    for ref, x0, row1_y in [("X1", 25, 255), ("X2", 25, 225), ("X3", 25, 197), ("X4", 25, 170),
                            ("X5", 300, 272), ("X6", 300, 232)]:
        connector_table(ref, x0, row1_y, x0 < 200)
    P = pins
    red, blk, wht, blu = "красный", "черный", "белый", "синий"

    # С1 — красный: X1:1 → X5:1, X5:2 и свободный конец
    c1 = (190, P["X1:1"][1])
    wire(1, [P["X1:1"], c1], red, (0,))
    wire(2, [c1, (c1[0], P["X5:1"][1]), P["X5:1"]], red, (1,))
    wire(3, [c1, (215, c1[1]), (215, P["X5:2"][1]), P["X5:2"]], red, (1,))
    wire(4, [c1, (c1[0], 244), (205, 244)], red, ())
    free_end(4, (205, 244))
    splice("С1", c1, red, (c1[0] - 4, c1[1] + 2.5))

    # С2 — черный: X1:2 → X5:3, X6:2 и свободный конец
    c2 = (235, P["X1:2"][1])
    wire(5, [P["X1:2"], c2], blk, (0,))
    wire(6, [c2, (c2[0], P["X5:3"][1]), P["X5:3"]], blk, (1,))
    wire(7, [c2, (265, c2[1]), (265, P["X6:2"][1]), P["X6:2"]], blk, (1,))
    wire(8, [c2, (c2[0], 238), (250, 238)], blk, ())
    free_end(8, (250, 238))
    splice("С2", c2, blk, (c2[0] - 4, c2[1] + 2.5))

    # С3, С4 — провода от X2 и X3 сходятся в сросток, от него к свободному концу — отдельный провод
    end_x = 200
    c3, c4 = (150, P["X2:1"][1]), (170, P["X2:2"][1])
    wire(9, [P["X2:1"], c3], wht, (0,))
    wire(10, [P["X3:1"], (c3[0], P["X3:1"][1]), c3], wht, (0,))
    wire(11, [c3, (end_x, c3[1])], wht, ())
    free_end(11, (end_x, c3[1]))
    wire(12, [P["X2:2"], c4], blu, (0,))
    wire(13, [P["X3:2"], (c4[0], P["X3:2"][1]), c4], blu, (0,))
    wire(14, [c4, (end_x, c4[1])], blu, ())
    free_end(14, (end_x, c4[1]))
    splice("С3", c3, wht, (c3[0] - 4, c3[1] + 2.5))
    splice("С4", c4, blu, (c4[0] + 4, c4[1] - 2.5))

    # X4 → X6 напрямую
    for num, src, dst, ch, color in [(15, "X4:2", "X6:5", 290, wht), (16, "X4:3", "X6:4", 285, "зеленый"),
                                     (17, "X4:4", "X6:3", 280, "желтый")]:
        wire(num, [P[src], (ch, P[src][1]), (ch, P[dst][1]), P[dst]], color)

    elements = [(ref, f"{c.kind} {c.housing} {c.maker}", "1", "") for ref, c in CONNECTORS.items()]
    elements.append((f"{SPLICES[0]}…{SPLICES[-1]}", "Сросток", str(len(SPLICES)), ""))
    table(230, 140, [24, 106, 10, 45], ["Поз.\nобозн.", "Наименование", "Кол.", "Примечание"], elements, "llcl")

    tt = [
        "1. С1–С4 — сростки (точки соединения проводов);",
        f"    выполнение — по {SB}, таблица 1.",
        f"2. Свободные концы проводов № {', '.join(map(str, free_end_wires()))} (номера проводов по схеме)",
        f"    зачистить на {STRIP_MM} мм и облудить.",
        f"3. Длины проводов — по сборочному чертежу {SB}.",
        f"    Предельные отклонения длин {TOLERANCE} мм.",
        f"4. {DEVIATION}",
        f"5. Не используются: {unused_pins_text()}.",
    ]
    for i, s in enumerate(tt):
        text(s, (25, 140 - i * 6.3), align=TA.MIDDLE_LEFT)
    eskd.title_form1(230, 5, E4, (NAME, "Схема электрическая", "соединений"), 1, 1, scale="—")  # схема без масштаба
    return doc

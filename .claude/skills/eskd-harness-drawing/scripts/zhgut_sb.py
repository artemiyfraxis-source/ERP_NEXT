"""Сборочный чертеж жгута (ГОСТ 2.414), А3, без масштаба.

Вид: слева ветви в оплетке сходятся в термоусадочную трубку (ствол), справа — ветви к X5, X6 и отвод
свободных концов. Сращивания и маркировка — таблицами 1 и 2.
"""
import eskd
from eskd import TA, hdim, line, pline, rect, table, text
from harness_data import (BRAID, CONNECTORS, DEVIATION, E4, L_FREE, L_TRUNK, L_X5, L_X6, L_XP, MARK_LEN, MARK_METHOD,
                          MARK_TUBE, MARKED, NAME, SB, SPLICE_METHOD, SPLICE_TUBE_MM, SPLICES, STRIP_MM, TOLERANCE,
                          TUBE_END_2PIN, TUBE_SPLICE, TUBE_TRUNK, FREE, MARK_DIST, WIRES, end_tube, END_TUBE_MM, TWIST_PITCH)
from zhgut_spec import wire_positions

# Изображение строится в условных единицах и вписывается в поле над техническими требованиями:
# K — увеличение, (CX, CY) — центр изображения на листе.
K, CX, CY = 1.5, 217, 207


def P(x, y):
    return CX + (x - 183) * K, CY + (y - 237) * K


def connector(ref, x_rear, y, hgt, left):
    """Корпус соединителя; задний торец (выход проводов) — x_rear."""
    x_out = x_rear - 10 if left else x_rear + 10
    rect(*P(min(x_rear, x_out), y - hgt / 2), *P(max(x_rear, x_out), y + hgt / 2), "02_ЭЛЕМЕНТЫ")
    x, yy = P(x_out, y)
    if left:
        text(ref, (x - 3, yy), h=5, align=TA.MIDDLE_RIGHT)
    else:
        text(ref, (x + 3, yy), h=5, align=TA.MIDDLE_LEFT)


def braid(points):
    """Ветвь в оплетке — основная толстая линия."""
    pline([P(*p) for p in points], "01_ЖГУТ")


def branch(points):
    """Ветвь — одна линия на все провода ветви; провода и контакты — на схеме Э4."""
    pline([P(*p) for p in points], "01_ПРОВОДА")


def tube(x1, x2, y, half):
    """Трубка поверх ветви: от x1 до x2, половина высоты half."""
    rect(*P(x1, y - half), *P(x2, y + half), "02_ЭЛЕМЕНТЫ")


def leader(starts, shelf, caption, to_left=False):
    """Линии-выноски с точками на изделии, сходятся к полке с надписью."""
    hx, hy = P(*shelf)
    for start in starts:
        sx, sy = P(*start)
        eskd.msp.add_circle((sx, sy), 0.6, dxfattribs={"layer": "03_РАЗМЕРЫ"})
        line((sx, sy), (hx, hy), "03_РАЗМЕРЫ")
    end = hx - len(caption) * 2.6 if to_left else hx + len(caption) * 2.6
    line((hx, hy), (end, hy), "03_РАЗМЕРЫ")
    text(caption, ((hx + end) / 2, hy + 1), align=TA.BOTTOM_CENTER)


def sb_sheet():
    doc = eskd.new_sheet()
    eskd.frame(420, 297, SB)

    y0 = 235
    A, B = (160, y0), (175, y0)  # ствол А–Б — термоусадочная трубка; сростки С1–С4 — в Б
    left = [("X1", 265, 6), ("X2", 245, 6), ("X3", 225, 6), ("X4", 205, 9)]
    for ref, y, hgt in left:
        connector(ref, 100, y, hgt, True)
        braid([(100, y), (125, y), A])
        tube(96, 105, y, hgt / 2 + 0.6)  # ТТК поверх корпуса соединителя с заходом на провода
        tube(108, 112, y, 1.6)      # маркировка

    # Ствол — жгут в термоусадочной трубке (контур трубки поверх жгута)
    braid([A, B])
    rect(*P(A[0], y0 - 2.5), *P(B[0], y0 + 2.5), "02_ЭЛЕМЕНТЫ")

    # Справа — ветви к X5, к X6 и отвод свободных концов
    xs1_rear, xs1_y, xs2_rear = 250, y0 + 20, 260
    branch([B, (185, xs1_y), (xs1_rear, xs1_y)])
    branch([B, (xs2_rear, y0)])
    connector("X5", xs1_rear, xs1_y, 7, False)
    connector("X6", xs2_rear, y0, 9, False)

    free_x, free_y = 200, y0 - 18
    branch([B, (185, free_y), (free_x, free_y)])
    line(P(free_x, free_y - 1), P(free_x, free_y + 1), "01_ПРОВОДА")
    x, y = P(free_x, free_y)
    text(f"Своб. концы от {SPLICES[0]}–{SPLICES[-1]}", (x + 3, y), align=TA.MIDDLE_LEFT)
    x, y = P(*B)
    text("Б", (x - 1.5, y - 5), align=TA.TOP_RIGHT)

    leader([(167, y0 - 2.5)], (150, 186), f"Трубка {TUBE_TRUNK}", to_left=True)
    leader([(121, 265), (121, 245)], (118, 255), BRAID, to_left=True)
    leader([(121, 225), (121, 205)], (118, 215), BRAID, to_left=True)
    for i, (ref, y, hgt) in enumerate(left, 1):  # индексы маркировки М1–М4 над маркировочными трубками
        x, yy = P(110, y + 1.6)
        text(f"М{i}", (x, yy + 1), align=TA.BOTTOM_CENTER)
    leader([(175, y0 + 2.5)], (172, 246), f"{SPLICES[0]}–{SPLICES[-1]}", to_left=True)
    leader([(102, 205 - 5.1)], (99, 186), f"Трубка {end_tube('X4')}", to_left=True)
    leader([(102, 265 + 3.6)], (104, 272), f"Трубка {TUBE_END_2PIN}, 3 места")

    # Слева — от переднего (левого) торца соединителей, общая выносная; справа — до заднего торца
    top_y = left[0][1] + left[0][2] / 2
    line(P(90, left[-1][1] - left[-1][2] / 2), P(90, top_y), "03_РАЗМЕРЫ")
    dim_top = P(0, 279)[1]
    hdim(P(90, top_y), P(*A), dim_top, L_XP)
    hdim(P(*A), P(*B), dim_top, L_TRUNK)
    hdim(P(*B), P(xs1_rear, xs1_y + 3.5), dim_top, L_X5)
    hdim(P(*B), P(free_x, free_y), P(0, 208)[1], L_FREE)
    hdim(P(*B), P(xs2_rear, y0 - 4.5), P(0, 200)[1], L_X6)

    tt = [
        "1. Длины ответвлений указаны от центра разветвления: до переднего торца",
        "    соединителей X1–X4, до заднего торца X5, X6 и до концов проводов отвода.",
        f"    Предельные отклонения длин {TOLERANCE} мм.",
        f"2. Электрический монтаж — по схеме {E4}.",
        "3. Сращивание проводов — по таблице 1. Провода X2 (белый и синий)",
        f"    и X3 (белый и синий) перед сращиванием свить попарно, шаг свивки {TWIST_PITCH} мм.",
        "    * Допускается пайка, ленточный обжим или ультразвуковая сварка.",
        f"4. Свободные концы проводов отвода зачистить на {STRIP_MM} мм и облудить.",
        f"5. {DEVIATION}",
        f"6. Ветви X1–X4 — в оплетке {BRAID[8:]}. Поверх корпуса соединителей со стороны",
        "    проводов усадить трубку с заходом на оплетку:",
        f"    на X1–X3 — {TUBE_END_2PIN}, на X4 — {TUBE_TRUNK}, L = {END_TUBE_MM} мм.",
        "7. Маркировку выполнить поверх оплетки в местах М1–М4 по таблице 2.",
    ]
    for i, s in enumerate(tt):
        text(s, (232, 135 - i * 5.2), align=TA.MIDDLE_LEFT)

    pos = wire_positions()

    def splice_wires_text(splice):
        """«17 → X1:1; 15 → X5:1, X5:2, своб.» — провод по спецификации и его второй конец."""
        groups = {}
        for _, a, b, mm2, color, *_rest in WIRES:
            if splice in (a, b):
                other = b if a == splice else a
                groups.setdefault(pos[(mm2, color)], []).append("своб." if other == FREE else other)
        return "; ".join(f"{p} → {', '.join(ends)}" for p, ends in groups.items())

    splices = [(s, splice_wires_text(s), SPLICE_METHOD, f"{TUBE_SPLICE}, L = {SPLICE_TUBE_MM}") for s in SPLICES]
    table(25, 118, [18, 76, 51, 40], ["Индекс", "Провод (поз. спец.)\n→ второй конец", "Способ", "Изоляция"],
          splices, "clll", header_h=10, title="Таблица 1 — Сращивания (в разветвлении Б)")
    marks = [(f"М{i}", CONNECTORS[ref].mark.strip("«»"), MARK_DIST, MARK_METHOD, f"Трубка {MARK_TUBE}, L = {MARK_LEN} мм")
             for i, ref in enumerate(MARKED, 1)]
    table(25, 65, [18, 16, 30, 40, 81], ["Индекс", "Текст", "От корпуса,\nмм", "Способ", "Материал"], marks, "ccccl",
          header_h=10, title="Таблица 2 — Маркировка")
    eskd.title_form1(230, 5, SB, (NAME, "Сборочный чертеж"), 1, 1, scale="—")  # без масштаба
    return doc

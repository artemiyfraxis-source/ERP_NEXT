"""Оформление по ЕСКД (ezdxf): лист, рамка, основные надписи форм 1, 2, 2а, таблицы, размеры.

ГОСТ 2.104 (основные надписи), 2.301/2.303/2.304 (форматы, линии, шрифт), 2.307 (размеры).
"""
import ezdxf
from ezdxf.enums import TextEntityAlignment as TA

from harness_data import DEVELOPER, ORGANIZATION

THICK, THIN = 70, 25
LAYERS = [("00_РАМКА", 70), ("00_РАМКА_ТОНК", 25), ("01_ЖГУТ", 100), ("01_ПРОВОДА", 50), ("02_ЭЛЕМЕНТЫ", 50),
          ("03_РАЗМЕРЫ", 25), ("04_ТЕКСТ", 25), ("05_ТАБЛИЦЫ", 25)]
DIM = {"dimtxt": 3.5, "dimasz": 3.0, "dimexe": 2.0, "dimexo": 0.0, "dimgap": 1.0, "dimtad": 1,
       "dimtih": 0, "dimtoh": 0, "dimdec": 0, "dimtxsty": "GOST", "dimlwd": 25, "dimlwe": 25}
msp = None  # пространство модели текущего листа


def new_sheet():
    global msp
    doc = ezdxf.new("R2010", setup=True, units=ezdxf.units.MM)
    doc.header["$LWDISPLAY"] = 1
    doc.styles.add("GOST", font="isocpeur.ttf")  # чертежный шрифт с кириллицей
    for name, lw in LAYERS:
        doc.layers.add(name, color=7, lineweight=lw)
    msp = doc.modelspace()
    return doc


def _attrs(layer, lw):
    return {"layer": layer, **({"lineweight": lw} if lw else {})}


def line(p1, p2, layer="00_РАМКА", lw=None):
    msp.add_line(p1, p2, dxfattribs=_attrs(layer, lw))


def pline(points, layer):
    msp.add_lwpolyline(points, dxfattribs={"layer": layer})


def rect(x1, y1, x2, y2, layer="00_РАМКА", lw=None):
    msp.add_lwpolyline([(x1, y1), (x2, y1), (x2, y2), (x1, y2)], close=True, dxfattribs=_attrs(layer, lw))


def text(s, p, h=3.5, align=TA.MIDDLE_CENTER, rot=0, width=0.9):
    msp.add_text(s, height=h, rotation=rot,
                 dxfattribs={"layer": "04_ТЕКСТ", "style": "GOST", "width": width}).set_placement(p, align=align)


def hdim(p1, p2, y, value):
    """Горизонтальный размер; чертеж без масштаба — значение задается явно."""
    msp.add_linear_dim(base=(p1[0], y), p1=p1, p2=p2, text=str(value), dimstyle="Standard",
                       override=DIM, dxfattribs={"layer": "03_РАЗМЕРЫ"}).render()


def frame(w, h, graph26):
    """Формат, рамка, графы 19–23 на поле подшивки, графа 26, «Копировал» / «Формат»."""
    rect(0, 0, w, h, "00_РАМКА_ТОНК")
    rect(20, 5, w - 5, h - 5)
    text("Копировал", (w - 120, 2.5), h=2.5)
    text(f"Формат {'А3' if w > 300 else 'А4'}", (w - 30, 2.5), h=2.5)
    y = 5
    for caption, hgt in [("Инв. № подл.", 25), ("Подп. и дата", 35), ("Взам. инв. №", 25),
                         ("Инв. № дубл.", 25), ("Подп. и дата", 35)]:
        rect(8, y, 20, y + hgt)
        line((13, y), (13, y + hgt))
        text(caption, (10.5, y + hgt / 2), h=2.5, rot=90)
        y += hgt
    if graph26:
        rect(20, h - 19, 90, h - 5)
        text(graph26, (55, h - 12), rot=180)


def _lines(x0, y0, segments):
    for a, b, lw in segments:
        line((x0 + a[0], y0 + a[1]), (x0 + b[0], y0 + b[1]), lw=lw)


def _revisions(x0, y0, rows_top, header_y, signed):
    """Левая часть основной надписи (65 мм): строки по 5 мм, графы изменений и подписей."""
    for yy in range(5, rows_top, 5):
        line((x0, y0 + yy), (x0 + 65, y0 + yy), lw=THICK if yy in (header_y, header_y + 5) else THIN)
    line((x0 + 7, y0 + header_y), (x0 + 7, y0 + rows_top), lw=THICK)
    for xx in (17, 40, 55, 65):
        line((x0 + xx, y0), (x0 + xx, y0 + rows_top), lw=THICK)
    for s, xx in [("Изм.", 3.5), ("Лист", 12), ("№ докум.", 28.5), ("Подп.", 47.5), ("Дата", 60)]:
        text(s, (x0 + xx, y0 + header_y + 2.5), h=2.5, width=0.8)
    for s, yy in signed:
        text(s, (x0 + 1, y0 + yy), h=2.5, align=TA.MIDDLE_LEFT, width=0.8)
    text(DEVELOPER, (x0 + 18, y0 + header_y - 2.5), align=TA.MIDDLE_LEFT, width=0.8)


def title_form1(x0, y0, designation, name, sheet, sheets, scale=""):
    """Основная надпись по форме 1 (чертежи и схемы), 185×55. name — строки наименования."""
    rect(x0, y0, x0 + 185, y0 + 55)
    _revisions(x0, y0, 55, 30, [("Разраб.", 27.5), ("Пров.", 22.5), ("Т.контр.", 17.5),
                                ("Н.контр.", 7.5), ("Утв.", 2.5)])
    _lines(x0, y0, [((65, 40), (185, 40), THICK), ((65, 15), (185, 15), THICK), ((135, 0), (135, 40), THICK),
                    ((135, 35), (185, 35), THIN), ((135, 20), (185, 20), THICK), ((150, 20), (150, 40), THICK),
                    ((167, 20), (167, 40), THICK), ((140, 20), (140, 35), THIN), ((145, 20), (145, 35), THIN),
                    ((155, 15), (155, 20), THICK)])
    text(designation, (x0 + 125, y0 + 47.5), h=7)
    text(name[0], (x0 + 100, y0 + 33), h=5)
    for k, s in enumerate(name[1:]):
        text(s, (x0 + 100, y0 + 25 - k * 5))
    for s, xx in [("Лит.", 142.5), ("Масса", 158.5), ("Масштаб", 176)]:
        text(s, (x0 + xx, y0 + 37.5), h=2.5)
    if scale:
        text(scale, (x0 + 176, y0 + 27.5))
    text(f"Лист  {sheet}" if sheets > 1 else "Лист", (x0 + 136, y0 + 17.5), h=2.5, align=TA.MIDDLE_LEFT)
    text(f"Листов  {sheets}", (x0 + 156, y0 + 17.5), h=2.5, align=TA.MIDDLE_LEFT)
    text(ORGANIZATION, (x0 + 160, y0 + 7.5), h=5)


def title_form2(x0, y0, designation, name, sheets):
    """Основная надпись по форме 2 (первый лист текстовых документов), 185×40."""
    rect(x0, y0, x0 + 185, y0 + 40)
    _revisions(x0, y0, 40, 25, [("Разраб.", 22.5), ("Пров.", 17.5), ("Н.контр.", 7.5), ("Утв.", 2.5)])
    _lines(x0, y0, [((65, 25), (185, 25), THICK), ((135, 0), (135, 25), THICK), ((135, 20), (185, 20), THIN),
                    ((135, 15), (185, 15), THICK), ((150, 15), (150, 25), THICK), ((165, 15), (165, 25), THICK),
                    ((140, 15), (140, 20), THIN), ((145, 15), (145, 20), THIN)])
    text(designation, (x0 + 125, y0 + 32.5), h=7)
    text(name, (x0 + 100, y0 + 12.5), h=5)
    for s, xx in [("Лит.", 142.5), ("Лист", 157.5), ("Листов", 175)]:
        text(s, (x0 + xx, y0 + 22.5), h=2.5)
    text(str(sheets), (x0 + 175, y0 + 17.5), h=2.5)
    text(ORGANIZATION, (x0 + 160, y0 + 7.5), h=5)


def table(x0, top, cols, header, rows, aligns, header_h=15, row_h=8, title=""):
    """Таблица: шапка (строки через \\n), строки 8 мм; aligns — 'l' / 'c' по графам."""
    if title:
        text(title, (x0, top + 3), align=TA.BOTTOM_LEFT)
    width = sum(cols)
    bottom = top - header_h - row_h * len(rows)
    rect(x0, bottom, x0 + width, top, "05_ТАБЛИЦЫ", lw=50)
    line((x0, top - header_h), (x0 + width, top - header_h), "05_ТАБЛИЦЫ", lw=50)
    for i in range(1, len(rows)):
        yy = top - header_h - row_h * i
        line((x0, yy), (x0 + width, yy), "05_ТАБЛИЦЫ", lw=THIN)
    x = x0
    for i, w in enumerate(cols):
        if i:
            line((x, bottom), (x, top), "05_ТАБЛИЦЫ", lw=50)
        lines = header[i].split("\n")
        for k, s in enumerate(lines):
            text(s, (x + w / 2, top - header_h / 2 + (len(lines) - 1) * 2.25 - k * 4.5))
        for r, row in enumerate(rows):
            if row[i]:
                yy = top - header_h - row_h * r - row_h / 2
                if aligns[i] == "l":
                    text(row[i], (x + 1.5, yy), align=TA.MIDDLE_LEFT)
                else:
                    text(row[i], (x + w / 2, yy))
        x += w
    return bottom


def title_form2a(x0, y0, designation, sheet):
    """Основная надпись по форме 2а (последующие листы), 185×15."""
    rect(x0, y0, x0 + 185, y0 + 15)
    line((x0, y0 + 5), (x0 + 65, y0 + 5), lw=THICK)
    line((x0, y0 + 10), (x0 + 65, y0 + 10), lw=THIN)
    for xx in (7, 17, 40, 55, 65, 175):
        line((x0 + xx, y0), (x0 + xx, y0 + 15), lw=THICK)
    line((x0 + 175, y0 + 8), (x0 + 185, y0 + 8), lw=THICK)
    for s, xx in [("Изм.", 3.5), ("Лист", 12), ("№ докум.", 28.5), ("Подп.", 47.5), ("Дата", 60)]:
        text(s, (x0 + xx, y0 + 2.5), h=2.5, width=0.8)
    text(designation, (x0 + 120, y0 + 7.5), h=7)
    text("Лист", (x0 + 180, y0 + 11.5), h=2.5, width=0.8)
    text(str(sheet), (x0 + 180, y0 + 4))

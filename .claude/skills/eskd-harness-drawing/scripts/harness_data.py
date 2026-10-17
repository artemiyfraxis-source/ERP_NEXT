"""Данные жгута — единственный источник для сборочного чертежа, схемы Э4 и спецификации."""
from typing import NamedTuple

BASE = "ЮТНВ.713К.4КБ1"  # внутренний номер изделия; документы — BASE + код документа
NAME = "Жгут НВ4"
SB, E4 = f"{BASE} СБ", f"{BASE} Э4"
DEVELOPER, ORGANIZATION = "Васильев", "Fraxis"
STRIP_MM = "2–3"      # зачистка свободных концов под лужение, мм
TOLERANCE = "+10/−5"  # предельные отклонения длин, мм

# Ответвления, мм: от центра разветвления до заднего торца корпуса соединителя или до концов проводов
L_XP = 120                                         # X1–X4 — до разветвления А
L_TRUNK, L_X5, L_X6, L_FREE = 30, 150, 170, 50   # ствол А–Б; от Б: X5, X6, отвод свободных концов
L_TO_B = L_XP + L_TRUNK                            # от левых соединителей до сростков С1–С4 в Б


class Connector(NamedTuple):
    mark: str     # куда подключается («5» …); пусто — нет
    kind: str
    housing: str
    maker: str
    contact: str
    pins: int


CONNECTORS = {
    "X1": Connector("«5»", "Вилка", "SMR-02V-B", "JST", "SYM-001T-P0.6", 2),
    "X2": Connector("«I3»", "Вилка", "SMR-02V-B", "JST", "SYM-001T-P0.6", 2),
    "X3": Connector("«C»", "Розетка", "SMP-02V-BC", "JST", "SHF-001T-0.8BS", 2),
    "X4": Connector("«A6»", "Вилка", "SMR-04V-B", "JST", "SYM-001T-P0.6", 4),
    "X5": Connector("", "Розетка", "I-DS1071-SCB01X3", "Connfly", "T-DS1071-SC600", 3),
    "X6": Connector("", "Розетка", "I-DS1071-SCB02X3", "Connfly", "T-DS1071-SC600", 6),
}
MARKED = ["X1", "X2", "X3", "X4"]  # маркировка: трубка MARK_TUBE, надпись — адрес подключения (Connector.mark)
MARK_TUBE, MARK_LEN, MARK_DIST = "4/2 мм", "10–15", "50"  # MARK_DIST — от корпуса соединителя, мм
BRAID = "Оплетка XP-3"
BRAID_PIECE_MM = 110  # отрезок оплетки на ветвь (резка по техкарте; на чертеже ветвь — от разъема до А)
BRAID_M = len(MARKED) * BRAID_PIECE_MM / 1000  # оплетка на ветвях X1–X4
SPLICES = ["С1", "С2", "С3", "С4"]  # сростки — точки соединения проводов
FREE = "своб. конец"
# Термоусадочные трубки (из состава ERP), м; ТТК 6/2 не используется
# ТТК 3/1 — сростки; ТТК 8/2 — хвостовики 2-контактных соединителей; ТТК 12/4 — ствол и хвостовик X4
TUBE_SPLICE, TUBE_END_2PIN, TUBE_TRUNK = "ТТК 3/1", "ТТК 8/2", "ТТК 12/4"
SPLICE_TUBE_MM = 10  # изоляция одного сростка
END_TUBE_MM = 15     # трубка поверх корпуса соединителя X1–X4
TWIST_PITCH = "20"   # шаг свивки пар X2, X3, мм
SPLICE_METHOD = "Опрессовка гильзой*"  # * допускается пайка или ленточный обжим
MARK_METHOD = "ТУТ марк."              # термоусадочная маркировочная трубка
TUBES = [(TUBE_SPLICE, len(SPLICES) * SPLICE_TUBE_MM / 1000), (TUBE_END_2PIN, 3 * END_TUBE_MM / 1000),
         (TUBE_TRUNK, (END_TUBE_MM + 30) / 1000)]
DEVIATION = "Опрессовка 0,5 мм² в контакт SYM-001T-P0.6 (X1) — согласованное отклонение."

# (номер, откуда, куда, сечение, цвет, длина реза по КД, примечание)
WIRES = [
    (1, "X1:1", "С1", "0,5", "красный", L_TO_B, ""),
    (2, "С1", "X5:1", "0,2", "красный", L_X5, ""),
    (3, "С1", "X5:2", "0,2", "красный", L_X5, ""),
    (4, "С1", FREE, "0,2", "красный", L_FREE, ""),
    (5, "X1:2", "С2", "0,5", "черный", L_TO_B, ""),
    (6, "С2", "X5:3", "0,2", "черный", L_X5, ""),
    (7, "С2", "X6:2", "0,2", "черный", L_X6, ""),
    (8, "С2", FREE, "0,2", "черный", L_FREE, ""),
    (9, "X2:1", "С3", "0,12", "белый", L_TO_B, ""),
    (10, "X3:1", "С3", "0,12", "белый", L_TO_B, ""),
    (11, "С3", FREE, "0,12", "белый", L_FREE, ""),
    (12, "X2:2", "С4", "0,12", "синий", L_TO_B, ""),
    (13, "X3:2", "С4", "0,12", "синий", L_TO_B, ""),
    (14, "С4", FREE, "0,12", "синий", L_FREE, ""),
    (15, "X4:2", "X6:5", "0,2", "белый", L_TO_B + L_X6, ""),
    (16, "X4:3", "X6:4", "0,2", "зеленый", L_TO_B + L_X6, ""),
    (17, "X4:4", "X6:3", "0,2", "желтый", L_TO_B + L_X6, ""),
]


def pin_wire(pin):
    return next((w for w in WIRES if pin in (w[1], w[2])), None)


def free_pins(ref):
    return [k for k in range(1, CONNECTORS[ref].pins + 1) if pin_wire(f"{ref}:{k}") is None]


def free_end_wires():
    return [w[0] for w in WIRES if w[2] == FREE]


def ref_range(refs):
    """Подряд идущие номера — диапазоном: X1, X2, X3 → «X1–X3»; X5, X6 → «X5, X6»."""
    nums = [int(r[1:]) for r in refs]
    if len(refs) >= 3 and nums == list(range(nums[0], nums[0] + len(nums))):
        return f"{refs[0]}–{refs[-1]}"
    return ", ".join(refs)


def connector_groups():
    """Одинаковые соединители — одной строкой: [(refs, Connector)]."""
    groups = {}
    for ref, c in CONNECTORS.items():
        groups.setdefault((c.kind, c.housing, c.maker), []).append(ref)
    return [(refs, CONNECTORS[refs[0]]) for refs in groups.values()]


def contact_counts():
    """Контакт → (количество занятых, соединители)."""
    counts = {}
    for ref, c in CONNECTORS.items():
        used = c.pins - len(free_pins(ref))
        n, refs = counts.get(c.contact, (0, []))
        counts[c.contact] = (n + used, refs + [ref])
    return counts


def wire_kinds():
    """Марки проводов по возрастанию сечения: [(сечение, цвет, суммарная длина мм)]."""
    kinds = sorted({(w[3], w[4]) for w in WIRES}, key=lambda k: (float(k[0].replace(",", ".")), k[1]))
    return [(mm2, color, sum(w[5] for w in WIRES if (w[3], w[4]) == (mm2, color))) for mm2, color in kinds]


def marking_lines(per_line=4):
    """Строки ТТ о маркировке: «X1 — «5»; X2 — «I3»;» — по per_line на строку."""
    items = [f"{ref} — {CONNECTORS[ref].mark}" for ref in MARKED]
    chunks = [items[i:i + per_line] for i in range(0, len(items), per_line)]
    return ["    " + "; ".join(c) + ("." if k == len(chunks) - 1 else ";") for k, c in enumerate(chunks)]


def end_tube(ref):
    """Трубка на хвостовике соединителя."""
    return TUBE_TRUNK if CONNECTORS[ref].pins > 2 else TUBE_END_2PIN


def splice_wires(splice):
    """Номера проводов, сходящихся в сростке."""
    return [w[0] for w in WIRES if splice in (w[1], w[2])]


def unused_pins_text():
    """«контакт 1 X4, контакты 1, 6 X6»."""
    parts = []
    for ref in CONNECTORS:
        pins = free_pins(ref)
        if pins:
            parts.append(f"{'контакт' if len(pins) == 1 else 'контакты'} {', '.join(map(str, pins))} {ref}")
    return ", ".join(parts)

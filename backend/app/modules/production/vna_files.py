"""Разбор файла векторного анализатора цепей (ВАЦ, VNA): частота → КСВН.

ОТК меряет коаксиальные сборки на ВАЦ (у владельца Arinst VNA-DL 1–8800 МГц, калибровочный набор SMA). Формат выгрузки
Arinst пока неизвестен, поэтому разбор принимает то, что выгружают такие приборы:
- Touchstone `.s1p` (и первая пара S11 из `.s2p`): строка опций `# Hz|kHz|MHz|GHz S RI|MA|DB R 50`, комментарии `!`,
  без строки опций — `# GHz S MA R 50` по стандарту;
- CSV/TXT: разделитель `;`, `,`, табуляция или пробелы, десятичная запятая, необязательная строка заголовка.
  Единица частоты — из заголовка (Гц/кГц/МГц/ГГц, Hz/kHz/MHz/GHz) или по диапазону значений; столбец значения —
  КСВ/VSWR, S11 в дБ или Return loss (обратные потери, дБ — положительные значат |Γ| = 10^(−RL/20)), действительная и
  мнимая части Γ, либо линейный модуль |Γ|.
Результат — точки (частота, Гц; КСВН), по возрастанию частоты. Непонятный файл — VnaFileError с русским текстом.
Модуль чистый: ни базы, ни сети.
"""

from __future__ import annotations

import math
import re
from typing import Sequence

from .domain import ProductionValidationError

# |Γ| ≥ 1 (обрыв, КЗ, погрешность калибровки) даёт бесконечный КСВН; хранится этот потолок.
VSWR_CEILING = 999.0
MAX_POINTS = 200_000

VswrPoint = tuple[float, float]


class VnaFileError(ProductionValidationError):
    """Файл ВАЦ не разобран; текст — для человека."""


_UNITS = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}
# Порядок важен: «ггц» и «мгц» содержат «гц».
_HEADER_UNITS = (
    (re.compile(r"ghz|ггц"), 1e9),
    (re.compile(r"mhz|мгц"), 1e6),
    (re.compile(r"khz|кгц"), 1e3),
    (re.compile(r"hz|гц"), 1.0),
)
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


def vswr_from_gamma(gamma: float) -> float:
    """КСВН из модуля коэффициента отражения; |Γ| ≥ 1 — потолок VSWR_CEILING."""
    if not math.isfinite(gamma) or gamma < 0:
        raise VnaFileError("В файле отрицательный или нечисловой модуль коэффициента отражения")
    if gamma >= 1:
        return VSWR_CEILING
    return min((1 + gamma) / (1 - gamma), VSWR_CEILING)


def parse_vna_file(content: bytes, file_name: str = "") -> list[VswrPoint]:
    """Точки (Гц, КСВН) из выгрузки ВАЦ, по возрастанию частоты."""
    text = _decode(content)
    lines = text.splitlines()
    name = str(file_name or "").strip().lower()
    touchstone = re.search(r"\.s\d+p$", name) is not None or any(
        line.strip().startswith("#") and _is_option_line(line) for line in lines
    )
    points = _touchstone(lines) if touchstone else _table(lines)
    if len(points) < 2:
        raise VnaFileError("В файле меньше двух точек измерения")
    if len(points) > MAX_POINTS:
        raise VnaFileError(f"В файле больше {MAX_POINTS} точек — это не выгрузка одного замера")
    return sorted(points, key=lambda point: point[0])


# Шапка выгрузки Arinst VNA-DL: «!Start (Hz):  1000000.000000», «!Points:  1000», «!Bandwidth (Hz):  1000».
_HEADER_FIELD = re.compile(r"^[!#;/\s]*([A-Za-zА-Яа-яЁё ]+?)\s*(?:\(([^)]*)\))?\s*[:=]\s*([-+0-9.,eE]+)\s*$")
_HEADER_KEYS = {
    "start": "start_hz", "stop": "stop_hz", "points": "points", "bandwidth": "if_bandwidth_hz",
    "if bandwidth": "if_bandwidth_hz", "ifbw": "if_bandwidth_hz", "rbw": "if_bandwidth_hz",
}


def vna_header(content: bytes) -> dict[str, float]:
    """Параметры развёртки из шапки файла, что нашлись: start_hz, stop_hz, points, if_bandwidth_hz (для паспорта).
    Ничего не нашлось или файл не читается — пустой словарь: шапка необязательна."""
    try:
        text = _decode(content)
    except VnaFileError:
        return {}
    found: dict[str, float] = {}
    for raw in text.splitlines()[:50]:
        match = _HEADER_FIELD.match(raw.strip())
        if match is None:
            continue
        key = _HEADER_KEYS.get(match.group(1).strip().lower())
        value = _number(match.group(3), decimal_comma=True)
        if key is None or value is None or key in found:
            continue
        unit = (match.group(2) or "").strip().lower()
        if key != "points":
            value *= _header_unit(unit) or 1.0
        found[key] = value
    return found


def _decode(content: bytes) -> str:
    if not content:
        raise VnaFileError("Файл пустой")
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return content.decode("utf-16")
        except UnicodeDecodeError as error:
            raise VnaFileError("Файл не читается как текст — нужен .s1p, .csv или .txt") from error
    if b"\x00" in content:
        raise VnaFileError("Файл не текстовый — нужен .s1p, .csv или .txt из программы ВАЦ")
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise VnaFileError("Файл не читается как текст — нужен .s1p, .csv или .txt")


def _number(token: str, decimal_comma: bool) -> float | None:
    clean = token.strip().strip('"').strip("'").replace(" ", "").replace(" ", "")
    if decimal_comma:
        clean = clean.replace(",", ".")
    if not _NUMBER.match(clean):
        return None
    value = float(clean)
    return value if math.isfinite(value) else None


# --- Touchstone ---------------------------------------------------------------------------------------------------


def _is_option_line(line: str) -> bool:
    tokens = line.strip().lstrip("#").lower().split()
    return any(token in _UNITS or token in ("s", "y", "z", "h", "g", "ri", "ma", "db", "r") for token in tokens)


def _touchstone(lines: Sequence[str]) -> list[VswrPoint]:
    unit, parameter, fmt = 1e9, "s", "ma"
    seen_options = False
    points: list[VswrPoint] = []
    for number, raw in enumerate(lines, start=1):
        line = raw.split("!", 1)[0].strip()
        if not line:
            continue
        if line.startswith("#"):
            if seen_options:
                continue  # Touchstone: действует первая строка опций
            seen_options = True
            tokens = line[1:].lower().split()
            index = 0
            while index < len(tokens):
                token = tokens[index]
                if token in _UNITS:
                    unit = _UNITS[token]
                elif token in ("s", "y", "z", "h", "g"):
                    parameter = token
                elif token in ("ri", "ma", "db"):
                    fmt = token
                elif token == "r":
                    index += 1  # волновое сопротивление: КСВН от него не зависит
                else:
                    raise VnaFileError(f"Строка опций Touchstone не понята: «{raw.strip()}»")
                index += 1
            if parameter != "s":
                raise VnaFileError("В файле Touchstone не S-параметры — КСВН считается по S11")
            continue
        if line.startswith("["):
            raise VnaFileError("Touchstone 2.0 с ключевыми словами [..] не поддерживается — выгрузите версию 1 (.s1p)")
        values = [_number(token, decimal_comma=False) for token in line.split()]
        if any(value is None for value in values):
            raise VnaFileError(f"Строка {number} файла Touchstone не числовая: «{raw.strip()[:60]}»")
        if len(values) < 3:
            continue  # продолжение строки многопортового файла (шумовые параметры и т.п.) — S11 в первой строке
        frequency, first, second = values[0], values[1], values[2]
        points.append((frequency * unit, vswr_from_gamma(_gamma(fmt, first, second))))
    return points


def _gamma(fmt: str, first: float, second: float) -> float:
    if fmt == "ri":
        return math.hypot(first, second)
    if fmt == "db":
        return 10 ** (first / 20)
    return first


# --- CSV / TXT ----------------------------------------------------------------------------------------------------


def _split(line: str, separator: str | None) -> list[str]:
    return line.split() if separator is None else [cell.strip() for cell in line.split(separator)]


def _numeric_row(line: str, separator: str | None) -> list[float] | None:
    cells = [cell for cell in _split(line, separator)]
    if separator is not None:
        while cells and not cells[-1]:
            cells.pop()  # «1;1,2;» — пустая ячейка в конце строки
    if len(cells) < 2:
        return None
    values = [_number(cell, decimal_comma=separator != ",") for cell in cells]
    if any(value is None for value in values):
        return None
    return values  # type: ignore[return-value]


def _table(lines: Sequence[str]) -> list[VswrPoint]:
    content = [line for line in lines if line.strip() and not line.lstrip().startswith(("!", "//"))]
    separator, rows, header = _detect(content)
    columns = len(rows[0])
    labels = [label.lower() for label in header]
    if labels and len(labels) != columns:
        # «Частота, МГц  КСВН» через пробелы режется не по столбцам: для пары столбцов подписью служит вся строка.
        labels = [" ".join(labels)] * 2 if columns == 2 else []
    frequency_column = _frequency_column(labels)
    unit = _header_unit(labels[frequency_column] if frequency_column < len(labels) else "")
    if unit is None:
        unit = _unit_by_range(max(row[frequency_column] for row in rows))
    gammas = _gammas(rows, labels, frequency_column)
    return [(row[frequency_column] * unit, vswr_from_gamma(gamma)) for row, gamma in zip(rows, gammas)]


def _detect(lines: Sequence[str]) -> tuple[str | None, list[list[float]], list[str]]:
    """Разделитель, числовые строки и строка заголовка над ними (пустая, если её нет)."""
    for separator in (";", "\t", None, ","):
        if separator is not None and not any(separator in line for line in lines):
            continue
        rows: list[list[float]] = []
        first_row = None
        for index, line in enumerate(lines):
            row = _numeric_row(line, separator)
            if row is None:
                continue
            if first_row is None:
                first_row = index
            rows.append(row)
        if len(rows) >= 2 and len(rows) * 2 >= len(lines) - (first_row or 0):
            widths: dict[int, int] = {}
            for row in rows:
                widths[len(row)] = widths.get(len(row), 0) + 1
            width = max(widths, key=lambda key: widths[key])
            rows = [row for row in rows if len(row) == width]
            header: list[str] = []
            if first_row:
                candidate = _split(lines[first_row - 1], separator)
                if len(candidate) >= 2:
                    header = candidate
            if len(rows) >= 2:
                return separator, rows, header
    raise VnaFileError(
        "Файл не похож на выгрузку ВАЦ: нужны столбцы «частота» и «КСВ» (или S11 дБ, Return loss, Re/Im) — "
        "числа через «;», «,» или табуляцию",
    )


def _frequency_column(labels: Sequence[str]) -> int:
    for index, label in enumerate(labels):
        if re.search(r"freq|част|^f\b|^f\s*[\(\[,]", label) or _header_unit(label) is not None and not _value_label(label):
            return index
    return 0


def _header_unit(label: str) -> float | None:
    for pattern, factor in _HEADER_UNITS:
        if pattern.search(label):
            return factor
    return None


def _value_label(label: str) -> bool:
    return bool(re.search(r"vswr|swr|ксв|s11|s21|return|rl\b|отраж|потер|db|дб|re\b|real|im\b|imag|mag|действ|мним", label))


def _unit_by_range(maximum: float) -> float:
    """Без единицы в заголовке: больше 10⁶ — Гц, от 100 — МГц, иначе ГГц (ВАЦ работают до десятков ГГц)."""
    if maximum > 1e6:
        return 1.0
    if maximum >= 100:
        return 1e6
    return 1e9


def _kind(label: str) -> str | None:
    if re.search(r"vswr|swr|ксв", label):
        return "vswr"
    if re.search(r"return|\brl\b|отраж|потер", label):
        return "rl"
    if re.search(r"db|дб", label):
        return "db"
    if re.search(r"\bre\b|real|действ", label):
        return "re"
    if re.search(r"\bim\b|imag|мним", label):
        return "im"
    if re.search(r"phase|фаз|angle|угол|deg|град", label):
        return "phase"
    if re.search(r"mag|модул|\|", label):
        return "mag"
    return None


def _gammas(rows: Sequence[Sequence[float]], labels: Sequence[str], frequency_column: int) -> list[float]:
    kinds = {index: _kind(label) for index, label in enumerate(labels) if index != frequency_column}
    by_kind: dict[str, int] = {}
    for index, kind in kinds.items():
        if kind is not None and kind not in by_kind:
            by_kind[kind] = index
    if "vswr" in by_kind:
        column = by_kind["vswr"]
        values = [row[column] for row in rows]
        if min(values) < 1:
            raise VnaFileError("В столбце КСВ есть значения меньше 1 — это не КСВН")
        return [(value - 1) / (value + 1) for value in values]
    if "rl" in by_kind or "db" in by_kind:
        return _decibels([row[by_kind.get("rl", by_kind.get("db"))] for row in rows])
    if "re" in by_kind and "im" in by_kind:
        return [math.hypot(row[by_kind["re"]], row[by_kind["im"]]) for row in rows]
    if "mag" in by_kind:
        return [row[by_kind["mag"]] for row in rows]
    return _guess([row for row in rows], frequency_column)


def _decibels(values: Sequence[float]) -> list[float]:
    """S11 в дБ отрицательный, Return loss положительный: знак столбца — по большинству значений."""
    negative = sum(1 for value in values if value < 0)
    if negative * 2 >= len(values):
        return [10 ** (value / 20) for value in values]
    return [10 ** (-value / 20) for value in values]


def _guess(rows: Sequence[Sequence[float]], frequency_column: int) -> list[float]:
    """Столбцы без подписей: КСВ (все ≥ 1), S11 дБ (есть отрицательные), Re/Im (два столбца в пределах ±1)."""
    others = [index for index in range(len(rows[0])) if index != frequency_column]
    first = [row[others[0]] for row in rows]
    if len(others) >= 2:
        second = [row[others[1]] for row in rows]
        if all(abs(value) <= 1.05 for value in first + second) and any(value < 0 for value in first + second):
            return [math.hypot(a, b) for a, b in zip(first, second)]
    if any(value < 0 for value in first):
        return _decibels(first)
    if min(first) >= 1:
        return [(value - 1) / (value + 1) for value in first]
    if max(first) <= 1:
        return list(first)
    raise VnaFileError(
        "Не понять, что во втором столбце: добавьте строку заголовка — «Частота, МГц;КСВ», «S11, дБ» или «Return loss»",
    )

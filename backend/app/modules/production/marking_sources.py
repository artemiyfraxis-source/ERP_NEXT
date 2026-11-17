"""Expand structured ETK marks using the production task's existing serial range."""

from decimal import Decimal, InvalidOperation
import re

MAX_LABELS = 500
TOKEN = re.compile(r"\{№(?::([1-6]))?\}")


def expand_serial(text: str, serial: int) -> str:
    return TOKEN.sub(lambda match: str(serial).zfill(int(match[1] or 0)), text)


def labels(document, task, serial_range, selected=()):
    """Stable keys select inscriptions, never browser-provided text/quantity/serials."""
    if serial_range is None:
        raise ValueError("Номера задания освобождены — печать недоступна")
    marks = []
    for op_index, operation in enumerate(document.get("operations") or []):
        for mark_index, mark in enumerate((operation.get("fields") or {}).get("marks") or []):
            key = f"{task.id}:{op_index}:{mark_index}"
            if selected and key not in selected:
                continue
            text = str(mark.get("text") or "")
            if not text.strip():
                continue
            try:
                count = Decimal(str(mark.get("qty", "1")).replace(",", "."))
            except InvalidOperation as error:
                raise ValueError(f"Неверное количество надписей: «{text}»") from error
            if not count.is_finite() or count < 0 or count != count.to_integral_value() or count > MAX_LABELS:
                raise ValueError(f"Нужно целое количество этикеток от 0 до {MAX_LABELS}: «{text}»")
            if count == 0:
                continue
            marks.append((key, mark, text, int(count)))
    # Match markLabelList: numbered inscriptions first, then fixed inscriptions.
    marks.sort(key=lambda item: not bool(TOKEN.search(item[2])))
    result = []
    first, last = serial_range
    for key, mark, text, count in marks:
        numbered = bool(TOKEN.search(text))
        quantity = count if numbered else count * task.quantity
        size = (last - first + 1) * count if numbered else quantity
        if sum(item["quantity"] for item in result) + size > MAX_LABELS:
            raise ValueError(f"За один раз — не более {MAX_LABELS} этикеток")
        for serial in range(first, last + 1) if numbered else (None,):
            result.append({"key": key, "task_id": str(task.id), "text": expand_serial(text, serial) if numbered else text,
                           "material": str(mark.get("material") or ""), "quantity": quantity, "serial": serial})
    return result


def check_materials(rows):
    materials = {str(row["material"]).strip().casefold() for row in rows}
    if len(materials) > 1:
        raise ValueError("Выберите совместимый материал: разделите надписи разных материалов на отдельные партии")
    if any(re.search(r"термоусад|трубк|\bтут\b|tube|sleeve", value) for value in materials):
        raise ValueError("Надписи для трубок нельзя печатать на этикетках — выберите совместимый материал")

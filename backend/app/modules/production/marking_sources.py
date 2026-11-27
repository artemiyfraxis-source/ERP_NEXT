"""Expand structured ETK marks using the production task's existing serial range."""

from decimal import Decimal, InvalidOperation
import re

from .traceability import clean_designation, default_designation, factory_project

MAX_LABELS = 500
TOKEN = re.compile(r"\{№(?::([1-6]))?\}")


def expand_serial(text: str, serial: int) -> str:
    return TOKEN.sub(lambda match: str(serial).zfill(int(match[1] or 0)), text)


def serial_designation(document, task):
    """«Изделие» заводского номера FRX.<проект>.<изделие>.<k> (владелец 27.11) из ЭТК задания: обозначение технолога
    в операции маркировки (`fields.serial.product` / `.designation`), иначе из шаблона `FRX.<проект>.<изделие>.{№}`
    (`fields.serial.template` или надпись с {№}), иначе «КБ<n>» позиции. Ничего — None: заводской номер не выдаётся."""
    project = factory_project(task.project)
    templates = []
    for operation in (document or {}).get("operations") or []:
        fields = operation.get("fields") or {}
        serial = fields.get("serial") if isinstance(fields.get("serial"), dict) else {}
        for key in ("product", "designation"):
            if (own := clean_designation(serial.get(key))) is not None:
                return own
        templates.append(serial.get("template"))
        templates.extend(mark.get("text") for mark in fields.get("marks") or () if isinstance(mark, dict))
    for text in templates:
        if (own := _template_designation(text, project)) is not None:
            return own
    return default_designation(task.item)


def _template_designation(text, project):
    """<изделие> of a label template FRX.<project>.<изделие>.{№}: the project may hold dots, so it is matched against
    the task's project; the rest up to «.{№}» is the designation."""
    clean = re.sub(r"\s+", "", str(text or ""))
    if not project or not clean[:4].upper() == "FRX." or not clean.endswith(".{№}"):
        return None
    body = clean[4:-4]
    for index, char in enumerate(body):
        if char == "." and factory_project(body[:index]) == project:
            return clean_designation(body[index + 1:])
    return None


def labels(document, task, serial_range, selected=(), interleave=False):
    """Stable keys select inscriptions, never browser-provided text/quantity/serials.

    interleave («чередовать по изделию», owner 18.11): the machine hands labels out one by one and the operator
    assembles each item at once, so every item gets its own set in ETK order — fixed marks, then numbered ones —
    before the next item: XS1, A1 №1, XS1, A1 №2 … A fixed row then carries the item's serial it belongs to."""
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
    first, last = serial_range
    if interleave and any(TOKEN.search(text) for _key, _mark, text, _count in marks):
        total = sum(count for *_rest, count in marks) * (last - first + 1)
        if total > MAX_LABELS:
            raise ValueError(f"За один раз — не более {MAX_LABELS} этикеток")
        ordered = sorted(marks, key=lambda item: bool(TOKEN.search(item[2])))  # fixed first, ETK order kept
        return [{"key": key, "task_id": str(task.id), "text": expand_serial(text, serial),
                 "material": str(mark.get("material") or ""), "quantity": count, "serial": serial}
                for serial in range(first, last + 1) for key, mark, text, count in ordered]
    # Match markLabelList: numbered inscriptions first, then fixed inscriptions.
    marks.sort(key=lambda item: not bool(TOKEN.search(item[2])))
    result = []
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


_PARTS = re.compile(r"(\d+)")


def natural_key(text: str):
    """«X2» before «X10»: digit runs compare as numbers, the rest case-insensitively."""
    return [(0, int(part), "") if part.isdigit() else (1, 0, part.casefold()) for part in _PARTS.split(text) if part]


def manual_labels(lines, order="listed", numbering=None):
    """«Свой текст» of a marking printer card (owner 20.11): one label per line × its copies, in the order typed or by
    number. A line with {№} becomes one label per number of `numbering` (first, last). These are not product serials:
    nothing is issued, a row's `serial` stays empty."""
    rows = []
    for index, (text, copies) in enumerate(lines):
        text = str(text).strip()
        if not text:
            raise ValueError(f"Пустая строка №{index + 1} — удалите её или впишите надпись")
        if TOKEN.search(text):
            if numbering is None:
                raise ValueError(f"В строке «{text}» есть {{№}} — укажите номера «с» и «по»")
            first, last = numbering
            numbers = range(first, last + 1)
        else:
            numbers = (None,)
        for value in numbers:
            rows.append({"key": f"manual:{index}" + (f":{value}" if value is not None else ""),
                         "text": expand_serial(text, value) if value is not None else text, "material": "",
                         "quantity": int(copies), "serial": None})
            if sum(row["quantity"] for row in rows) > MAX_LABELS:
                raise ValueError(f"За один раз — не более {MAX_LABELS} этикеток")
    if order == "number":
        rows.sort(key=lambda row: natural_key(row["text"]))
    return rows

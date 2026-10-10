"""№ проекта для сравнения: один ключ на всю ERP (SUPPLYMAP `projNoNorm_`).

Модули хранят этот ключ рядом со своим текстом проекта (колонки `*project_key`), чтобы читать один проект по индексу,
а не всю таблицу с фильтром в Python. Совпадение — точное по ключу: «719К.2» не цепляет «719К.21».
"""

from __future__ import annotations

import re

_HOMOGLYPHS = str.maketrans("KCEOPAHTMBXY", "КСЕОРАНТМВХУ")


def normalize_project_number(value: object) -> str:
    """Пробелы прочь, верхний регистр, латинские двойники → кириллица, «2000К» ≡ «2000К.1»."""
    normalized = re.sub(r"\s+", "", str(value or "").strip().upper()).translate(_HOMOGLYPHS)
    if re.fullmatch(r"[1-9]\d*(?:К[А-Я]?|МФ)(?:\.[А-Я]{1,3})?\.?", normalized):
        return normalized.rstrip(".") + ".1"
    return normalized


def project_from_purpose(value: object) -> str:
    """Проект из НАЗНАЧЕНИЯ/ЦЕЛИ («708К.4 · КБ1 · Жгут» → «708К.4»; «708К.4.012» → «708К.4»)."""
    purpose = re.sub(r"[\s·,;:\-—]*КБ\s*\d+[\s\S]*$", "", str(value or "").strip(), flags=re.I)
    token = re.split(r"[\s·,;]+", purpose)[0] if purpose else ""
    token = re.sub(r"\.\d{3,}$", "", token)
    return normalize_project_number(token) if token else ""


def digit_stem(key: str) -> str:
    """Цифры ключа, которые обязательно стоят в исходном тексте подряд: начальные цифры номера (нормализация их не
    меняет, а «.1» дописывает только после них), иначе самая длинная серия цифр. Пусто — у ключа нет цифр.
    Для предварительного отбора `LIKE '%<цифры>%'` по тексту без колонки ключа; точное сравнение — после."""
    leading = re.match(r"\d+", key)
    if leading:
        return leading.group(0)
    return max(re.findall(r"\d+", key), key=len, default="")

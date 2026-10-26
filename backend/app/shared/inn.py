"""A Russian ИНН: its check digits, and our own company's ИНН (ООО «ЭЗП «ФРАКСИС»: never a seller).

Shared by purchasing (the supplier directory, the УПД registry) and the document reader (`app.shared.doc_reader`), so
both check a seller the same way. Pure."""

from __future__ import annotations

OWN_INNS: frozenset[str] = frozenset({"5040191181"})


def inn_valid(inn: str) -> bool:
    """10 digits of an organisation or 12 of an individual entrepreneur, with the right check digits."""
    if not inn.isascii() or not inn.isdigit():
        return False
    digits = [int(char) for char in inn]

    def check(weights: list[int]) -> int:
        return sum(w * d for w, d in zip(weights, digits)) % 11 % 10

    if len(digits) == 10:
        return check([2, 4, 10, 3, 5, 9, 4, 6, 8]) == digits[9]
    if len(digits) == 12:
        return check([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == digits[10] and check([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == digits[11]
    return False

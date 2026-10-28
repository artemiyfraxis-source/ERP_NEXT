"""A Russian ИНН: its check digits, and our own company's ИНН (ООО «ЭЗП «ФРАКСИС»: never a seller).

Shared by purchasing (the supplier directory, the УПД registry) and the document reader (`app.shared.doc_reader`), so
both check a seller the same way; a misread ИНН is put right (`checked_inn`) the same way by the warehouse, the
registry and «Принять по УПД». Pure."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

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


# ---- a misread ИНН (owner 27.10.2026: the model read «5256004951» and «5256084951» off scans of «5256084961») -------

_INN_IN_TEXT = re.compile(r"(?<!\d)(\d{12}|\d{10})(?!\d)")

FIXED_BY_DOCUMENT = "ИНН {who} исправлен по тексту документа: было {read}"
FIXED_BY_DIRECTORY = "ИНН исправлен по справочнику: было {read}"
NOT_VALID = "ИНН {who} не прошёл проверку: {read}"


def inns_in_text(text: str | None) -> list[str]:
    """The valid ИНН of a text (a text layer, tesseract's page), each once, in order; ours left out."""
    found = (inn for inn in _INN_IN_TEXT.findall(str(text or "")) if inn_valid(inn) and inn not in OWN_INNS)
    return list(dict.fromkeys(found))


def inn_slip(read: str, inn: str) -> int:
    """How far a read ИНН is from `inn`: the digits that differ (an adjacent transposition counts as one slip);
    another length — never close."""
    if len(read) != len(inn):
        return 1 << 10
    wrong = [index for index, (a, b) in enumerate(zip(read, inn)) if a != b]
    if len(wrong) == 2 and wrong[1] == wrong[0] + 1 and read[wrong[0]] == inn[wrong[1]] and read[wrong[1]] == inn[wrong[0]]:
        return 1
    return len(wrong)


def nearest_inn(read: str, candidates: Iterable[str]) -> str:
    """The one valid candidate a misread ИНН was read off: one digit (or one adjacent transposition) away; none — two
    digits away. Two candidates at the same distance — none: never a guess."""
    valid = {inn for inn in candidates if inn and inn_valid(inn) and inn != read}
    for slips in (1, 2):
        near = [inn for inn in valid if inn_slip(read, inn) == slips]
        if near:
            return near[0] if len(near) == 1 else ""
    return ""


@dataclass(frozen=True)
class InnCheck:
    inn: str  # what to use: as read when valid, the corrected one; «» — kept out of matching (or not read)
    read: str  # as read
    remark: str = ""  # «» — valid as read (or not read at all)

    @property
    def corrected(self) -> bool:
        return bool(self.inn) and self.inn != self.read


def checked_inn(read: str | None, *, text: Iterable[str] = (), known: Iterable[str] = (),
                who: str = "продавца") -> InnCheck:
    """A read ИНН that fails its check digits is never used as is: the valid ИНН of the same document's text (a text
    layer, tesseract) it is a slip of; else the one known ИНН it is a slip of (`known` — the directory's ИНН of the
    suppliers named as the seller is); else nothing (match by the name) and a remark. A valid ИНН is never changed."""
    digits = "".join(char for char in str(read or "") if char.isdigit())
    if not digits or inn_valid(digits):
        return InnCheck(digits, digits)
    if found := nearest_inn(digits, text):
        return InnCheck(found, digits, FIXED_BY_DOCUMENT.format(who=who, read=digits))
    if found := nearest_inn(digits, known):
        return InnCheck(found, digits, FIXED_BY_DIRECTORY.format(read=digits))
    return InnCheck("", digits, NOT_VALID.format(who=who, read=digits))

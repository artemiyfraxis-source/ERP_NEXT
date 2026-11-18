"""Deliberate per-item norm assignment for coax adapters and test cables (owner 18.11.2026).

The operator never enters a norm; the passport, the label and the VNA tool read only `RfDetails.norm_id`. This module
checks an assignment against the catalog (`norm_problem`, run on every save) and proposes one for review
(`propose`): maker spec → ГОСТ/ОСТ/ТУ → IEC → ESCC → MIL-PRF-55339, in the order of `rf-norms.json` `assignment`.
A proposal is only text for a reviewed CSV; nothing here writes.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .instrument_norms import CatalogNorm, _model_text, catalog
from .rf_interfaces import NORM_ROLES, RfDetails

# Type-norm levels in assignment order; makers come first and MIL sheets last.
TYPE_LEVELS = ("tu", "iec", "escc")
_ANGLE = re.compile(r"углов|right[\s-]*angle|(?<!\d)90\s*°", re.I)
LEVEL_LABELS = {"maker": "изготовитель", "tu": "ТУ", "iec": "IEC", "escc": "ESCC", "mil": "MIL-PRF-55339"}


def _series(rf: RfDetails) -> tuple[str, ...]:
    return tuple(sorted(port.series for port in rf.interfaces))


def _ends(rf: RfDetails) -> tuple[str, ...]:
    return tuple(sorted(f"{port.series} {port.center}" for port in rf.interfaces))


def pair_text(rf: RfDetails) -> str:
    return " – ".join(f"{port.series} {port.body}" for port in rf.interfaces)


def fits(entry: CatalogNorm, rf: RfDetails, *, angled: bool | None = None) -> bool:
    if _series(rf) not in entry.port_pairs or (entry.ends and _ends(rf) not in entry.ends):
        return False
    return angled is None or entry.angle is None or entry.angle == angled


def norm_problem(details: RfDetails) -> str:
    """Why `details.norm_id` may not be stored, or "" when it may (empty id is always allowed)."""
    if not details.norm_id:
        return ""
    entry = catalog().get(details.norm_id)
    if entry is None:
        return f"Норма {details.norm_id} отсутствует в каталоге норм rf-norms.json"
    if details.role not in NORM_ROLES:
        return "Норма назначается только переходу или тестовому кабелю"
    if len(details.interfaces) != 2:
        return "Для назначения нормы укажите в карточке оба ВЧ-интерфейса"
    if any(port.impedance_ohm is not None and port.impedance_ohm != 50 for port in details.interfaces):
        return "Нормы каталога — для тракта 50 Ом"
    if not fits(entry, details):
        return f"Норма {entry.id} ({entry.norm.title}) не подходит к паре {pair_text(details)}"
    return ""


def kept_norm(before: RfDetails, after: RfDetails) -> str:
    """A card edit keeps the assigned norm while it still fits; a changed pair or role clears it (the audit shows it)."""
    if not before.norm_id:
        return ""
    candidate = RfDetails(after.interfaces, after.role, norm_id=before.norm_id) if after.role in NORM_ROLES else None
    return before.norm_id if candidate is not None and not norm_problem(candidate) else ""


def limits_text(entry: CatalogNorm) -> str:
    norm = entry.norm
    loss = norm.loss.limit_text if norm.loss else "вносимые потери не нормированы"
    return f"{norm.limit_text}; {loss}"


@dataclass(frozen=True)
class Proposal:
    norm_id: str
    reason: str

    @property
    def entry(self) -> CatalogNorm | None:
        return catalog().get(self.norm_id) if self.norm_id else None


def _found(token: str, text: str) -> bool:
    return bool(re.search(rf"(?<![A-ZА-Я0-9]){re.escape(token.upper())}", text))


def _maker(name: str, model: str, manufacturer: str):
    """Maker groups of the item's maker (a ТУ binds only its maker), with whether the group's article (if any) is named."""
    text = _model_text(f"{name} {manufacturer}", model)
    for entry in catalog().values():
        if entry.level == "maker" and any(_found(token, text) for token in entry.holder.get("matches", [entry.holder["match"]])):
            article = (entry.group or {}).get("article")
            yield entry, article is None or _found(article, text)


def propose(name: str, model: str, manufacturer: str, rf: RfDetails) -> Proposal | None:
    """The norm to assign, or an empty id with the reason; None for items that carry no norm (not adapter/cable)."""
    if rf.role not in NORM_ROLES:
        return None
    if len(rf.interfaces) != 2:
        return Proposal("", "в карточке нет двух ВЧ-интерфейсов")
    foreign = sorted({port.impedance_ohm for port in rf.interfaces if port.impedance_ohm not in (None, 50)})
    if foreign:
        return Proposal("", f"{foreign[0]:g} Ом: нормы каталога — для тракта 50 Ом")
    series = _series(rf)
    unknown_ohm = any(port.impedance_ohm is None for port in rf.interfaces)
    if any(port.series == "RP-SMA" for port in rf.interfaces):
        return Proposal("", "нет нормы на RP-SMA: первоисточника нет, к SMA не приравнивается")
    if unknown_ohm and "BNC" in series:
        return Proposal("", "сопротивление в карточке не указано, а BNC бывает 50 и 75 Ом — сначала уточнить карточку")
    note = "; сопротивление в карточке не указано — принято 50 Ом" if unknown_ohm else ""
    makers = list(_maker(name, model, manufacturer))
    for entry, named in makers:
        if named and fits(entry, rf):
            return Proposal(entry.id, f"изготовитель: {entry.holder['title']}, {entry.norm.title}{note}")
    if rf.role == "test_cable":
        known = next((entry.holder["note"] for entry, _ in makers if entry.holder.get("note")), "")
        return Proposal("", "кабельная сборка: паспорта изготовителя с нормой КСВН и затухания на это изделие в каталоге нет"
                            + (f" ({known})" if known else ""))
    angled = bool(_ANGLE.search(name))
    for level in TYPE_LEVELS:
        for entry in catalog().values():
            if entry.level == level and fits(entry, rf, angled=angled):
                return Proposal(entry.id, f"{LEVEL_LABELS[level]}: {entry.norm.reference}, {entry.norm.title}{note}")
    for entry in catalog().values():
        if entry.level == "mil" and fits(entry, rf):
            return Proposal(entry.id, f"MIL: {entry.norm.reference}, {entry.norm.title} — нормы изготовителя, ТУ, IEC "
                                      f"или ESCC на это сочетание нет{note}")
    gendered = " – ".join(f"{port.series} {port.body}" for port in rf.interfaces)
    if any(entry.level == "mil" and _series(rf) in entry.port_pairs for entry in catalog().values()):
        return Proposal("", f"в каталоге нет листа MIL-PRF-55339 на сочетание родов {gendered} "
                            f"(rf-norms.json, mil_adapters_note)")
    return Proposal("", f"в каталоге нет нормы на пару {' – '.join(series)}")

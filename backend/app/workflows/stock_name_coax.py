"""Full stock name of a «Коаксиальный соединитель» (owner 27.11.2026).

The owner-approved house style stays: «SMA-male RG-58», «SMA-female R/A RG-174/RG-316», «SMA-female bulkhead LMR-100»
(male / female by the centre contact). On top of what `infer_article_identity` knows, the builder reads the БД.КОАКС
record of the article (type, cable, execution, mounting) and adds only what tells articles apart: R/A, bulkhead /
flange, PCB, cable. Mounting («обжим» / «пайка» / «прижим») and maker go to `attrs` — `resolve_collisions` adds them
when two articles still share a name. Unknown gender never gets a guess: `complete=False` and the lot goes to review.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
import re

from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.techcards.connector_housings import normalize_article
from app.workflows.stock_name_types import LotHints, StockName
from app.workflows.warehouse_article import (
    _article_key, _field, _rg_list, _without_supplier, infer_article_identity,
)

COAX_TYPE = "Коаксиальный соединитель"

_TYPE = re.compile(r"^(?P<family>.+?)-(?P<sex>male|female)\b\s*(?P<tail>.*)$", re.IGNORECASE)
_ANGLE_TEXT = re.compile(r"(?<![A-Za-z0-9_])R/A(?![A-Za-z0-9_])|углов", re.IGNORECASE)
_BULKHEAD_TEXT = re.compile(r"bulkhead|flange|фланец|фланц|панел", re.IGNORECASE)
_LMR_TEXT = re.compile(r"(?<![A-Za-z])LMR[\s-]?(\d+[A-Z]?)(?![A-Za-z0-9])", re.IGNORECASE)
_PCB_TEXT = re.compile(r"на плату|\bPCB\b", re.IGNORECASE)
_CABLE = re.compile(r"^(?:RG-\d+[A-Z]?|LMR-\d+[A-Z]?)$")
_GENDER_WORD = {"штекер": "male", "гнездо": "female"}
_MOUNTS = (
    ("обжим", "обжим"), ("напаив", "пайка"), ("пайк", "пайка"), ("прижим", "прижим"),
)


def _mount_of(value: str) -> str:
    text = value.lower()
    return next((word for key, word in _MOUNTS if key in text), "")


def _wire(value: str) -> str:
    return re.sub(r"^(RG|LMR)\s*-?\s*(\d+)", lambda m: f"{m[1].upper()}-{m[2]}", value.strip(), flags=re.IGNORECASE)


def _parse_identity_name(name: str) -> tuple[str, str, bool, str]:
    """«SMA-female R/A RG-174» → (family, sex, angle, cable); empty parts when the name does not say."""
    found = _TYPE.match(name or "")
    if not found:
        return "", "", False, ""
    tail = found["tail"]
    angle = tail.startswith("R/A")
    cable = tail[3:].strip() if angle else tail.strip()
    return found["family"], found["sex"].lower(), angle, cable


def _identity_parts(text: str) -> tuple[str, str, bool, str, str]:
    """Family, sex, angle, cable and source of the code / description in `text`, without any reference records."""
    if not text.strip():
        return "", "", False, "", ""
    identity = infer_article_identity(text, ())
    if identity is None or identity.item_type != COAX_TYPE:
        return "", "", False, "", ""
    family, sex, angle, cable = _parse_identity_name(identity.name)
    return family, sex, angle, cable, identity.source


def _matching_fields(article: str, records: Sequence[TechOperationRecord]) -> list[dict[str, str]]:
    key = _article_key(normalize_article(_without_supplier(article)))
    if not key:
        return []
    return [
        item.fields for item in records
        if item.tab is TechOperationTab.COAX
        and _article_key(_field(item.fields, "coaxArticle", "Артикул", "article")) == key
    ]


def _most_common(values: Sequence[str]) -> str:
    return Counter(value for value in values if value).most_common(1)[0][0] if any(values) else ""


def _record_cable(fields: Sequence[dict[str, str]]) -> str:
    """One БД.КОАКС record per cable the article fits. All RG/LMR wires join «RG-174/RG-316»; a single other wire
    («5D-FB») stays as it is; a mix of unrelated wire marks (synonyms of one cable) is not guessed."""
    wires = list(dict.fromkeys(_wire(_field(item, "coaxWire", "Провод", "wire")) for item in fields))
    wires = [wire for wire in wires if wire]
    standard = [wire for wire in wires if _CABLE.match(wire)]
    if standard and len(standard) == len(wires) and len(standard) <= 3:
        return "/".join(standard)
    return wires[0] if len(wires) == 1 else ""


def coax_name(hints: LotHints, records: Sequence[TechOperationRecord]) -> StockName | None:
    if hints.item_type != COAX_TYPE:
        return None
    article = hints.article
    fields = _matching_fields(article, records)
    text = " ".join(part for part in (article, hints.upd_name, hints.description) if part)

    # Family and sex: the reference record first, then the article's code, then the receipt wording, then the old name.
    rec_type = _most_common([_field(item, "coaxType", "Тип/Серия", "type") for item in fields])
    family, sex, tail = "", "", ""
    if rec_type:
        found = _TYPE.match(rec_type)
        if found:
            family, sex, tail = found["family"], found["sex"].lower(), found["tail"].strip()
        else:
            family = rec_type
    if not sex:
        sex = _GENDER_WORD.get(_most_common([_field(item, "Пол") for item in fields]).lower(), "")
    if not family:
        family = _most_common([_field(item, "Серия") for item in fields])

    parts = [_identity_parts(article)] + [_identity_parts(value) for value in (hints.upd_name, hints.description, hints.name)]
    code_family, code_sex, code_angle, code_cable, code_source = parts[0]
    for part_family, part_sex, _angle, _cable, _source in parts:
        family = family or part_family
        if not sex and part_family == family:
            sex = part_sex
    source = "БД.КОАКС" if fields else (code_source or ("разбор описания" if any(p[0] for p in parts) else ""))

    angle = (
        "R/A" in tail or code_angle
        or any(_field(item, "Исполнение") == "угловой" for item in fields)
        or bool(_ANGLE_TEXT.search(text))
    )
    # The old name counts only for «bulkhead» / «flange»: a word nobody types by accident (R/A of an old name may be wrong).
    bulkhead = bool(re.search("bulkhead|flange", tail, re.IGNORECASE)) or bool(
        _BULKHEAD_TEXT.search(f"{text} {hints.name}")
    )
    pcb = bool(_PCB_TEXT.search(text))
    rest = " ".join(word for word in tail.split() if word not in {"R/A"} and not re.fullmatch(r"bulkhead", word, re.IGNORECASE))

    # Cable: the article's own code or words win over the reference (one record per cable the article fits).
    # A supplier-code letter («S-231L» → RG-174) is only a hint: the reference's cable list beats it.
    text_cable = _rg_list(" ".join((hints.upd_name, hints.description)))
    if not text_cable:
        lmr = _LMR_TEXT.search(f"{hints.upd_name} {hints.description} {hints.name}")
        text_cable = f"LMR-{lmr[1].upper()}" if lmr else ""
    record_cable = _record_cable(fields)
    cable = text_cable or (
        (record_cable or code_cable) if code_source == "код поставщика" else (code_cable or record_cable)
    ) or next((part[3] for part in parts[1:] if part[3]), "")

    maker = (hints.manufacturer or _most_common([_field(item, "coaxMfr", "Производитель") for item in fields])).strip()
    cable_records = [item for item in fields if _wire(_field(item, "coaxWire", "Провод", "wire")) in cable.split("/")]
    mounts = {
        _mount_of(_field(item, "Монтаж", "Тип экрана", "Крепление экрана")) for item in (cable_records or fields)
    } - {""}
    mount = next(iter(mounts)) if len(mounts) == 1 else _mount_of(text) if not mounts else ""

    reverse = family.upper().startswith("RP-") or any("RP" in _field(item, "Полярность") for item in fields)
    if reverse and not family.upper().startswith("RP-"):
        family = f"RP-{family}"

    name_parts = [f"{family}-{sex}" if family and sex else family]
    if angle:
        name_parts.append("R/A")
    if bulkhead:
        name_parts.append("bulkhead" if not re.search("flange", tail, re.IGNORECASE) else "flange")
    if rest:
        name_parts.append(rest)
    if pcb:
        name_parts.append("на плату")
    if cable:
        name_parts.append(cable)
    name = " ".join(part for part in name_parts if part)

    variants = [word for word, on in (("R/A", angle), ("bulkhead", bulkhead), ("на плату", pcb)) if on]
    attrs = {
        "family": family, "variant": " ".join(variants + ([rest] if rest else [])),
        "gender": {"male": "pin", "female": "socket"}.get(sex, ""), "pins": "",
        "execution": " ".join(variants), "cable": cable, "mount": mount, "maker": maker, "section": "", "size": "",
    }
    if re.search("переходник", text, re.IGNORECASE):
        return StockName(name or hints.name or article, False, ("переходник: два соединителя",), source, attrs)
    if not family:
        return StockName(hints.name or article, False, ("серия",), source, attrs)
    if fields and sex and code_sex and sex != code_sex:
        return StockName(name, False, ("пол: БД.КОАКС и код артикула расходятся",), source, attrs)
    if not sex:
        return StockName(name, False, ("RP-пол",) if reverse else ("пол",), source, attrs)
    return StockName(name, True, (), source, attrs)

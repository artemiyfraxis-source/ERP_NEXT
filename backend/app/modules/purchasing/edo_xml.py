"""EDO documents in the tax-service (ФНС) XML formats, as Diadoc and other operators hand them out (ported from
Roller_Buyer domain/edo-xml.ts and edo-files.ts).

Read: УПД / счёт-фактура ON_NSCHFDOPPR (5.01 and 5.03 attribute names) and the correction invoice ON_NKORSCHFDOPPR
(header only). An upload is one XML or a ZIP of them (Diadoc «Скачать → документооборот целиком»): signatures, cards and
PDFs inside are left out. The XML is untrusted: it is parsed by defusedxml with DTDs refused, archives are checked by
their declared sizes before anything is inflated, and marking codes are dropped from the tree right after parsing, so
they cannot leak into anything built from it. Pure: bytes in, documents out — no database, no files."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

MB = 1024 * 1024
# A Diadoc export of half a year is ~10 MB zipped; the limits stop a ZIP bomb long before memory does.
LIMITS = {"zip_bytes": 20 * MB, "entries": 500, "unzipped_bytes": 100 * MB, "xml_bytes": 20 * MB}

EDO_KINDS = ("УПД", "Счёт-фактура", "Корректировочный СФ")

_MARKING = frozenset({"НомСредИдентТов", "КИЗ", "НомУпак", "ИдентТрансУпак"})
_KINDS = {"СЧФ": "Счёт-фактура", "СЧФДОП": "УПД", "ДОП": "УПД"}
_CORRECTION_FUNCS = frozenset({"КСЧФ", "КСЧФДИС", "ДИС"})
_KNOWN_VERSIONS = frozenset({"5.01", "5.02", "5.03"})
_BUYER_TITLES = frozenset({"ON_NSCHFDOPPOK", "ON_NKORSCHFDOPPOK", "DP_TOVTORGPOK", "DP_REZRUZAK"})
_OTHER_FORMATS = {"DP_TOVTORGPR": "ТОРГ-12", "DP_REZRUISP": "акт", "ON_SCHFDOPPR": "УПД до 2019 года"}
# Same name test as the Google parser (google/upd-parser/UpdCore.js, updIsServiceLine_).
_SERVICE_NAME = re.compile(
    r"доставк|упаковк|транспортн|перевозк|экспедир|курьер|сдэк|почтов|услуг[аи]\s+по|погрузк|разгрузк", re.IGNORECASE,
)
_ADVANCE_NAME = re.compile(r"предоплат|аванс|предварительн\S* оплат", re.IGNORECASE)
_CALC_RATE = re.compile(r"^[0-9]+\s*/\s*[0-9]+$")
_PREFIX = re.compile(r"^([A-Z]{2}_[A-Z0-9]+)(?:_|\.|$)", re.IGNORECASE | re.ASCII)
_DOTTED_DATE = re.compile(r"^([0-9]{2})\.([0-9]{2})\.([0-9]{4})$")
_DOTTED_TIME = re.compile(r"^([0-9]{2})\.([0-9]{2})\.([0-9]{2})$")
_DECIMAL = re.compile(r"^(-?)([0-9]+)(?:\.([0-9]+))?$")
_PLAIN = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")
_NOT_DIGIT = re.compile(r"[^0-9]")
_POSITION = re.compile(r": line [0-9]+, column [0-9]+$")
_DOCUMENT_ENTRY = re.compile(r"^(ON|DP)_.+\.xml$", re.IGNORECASE)
_ENCODING = re.compile(r"""^\s*<\?xml[^>]*?\sencoding\s*=\s*["']([\w.:-]+)["']""", re.IGNORECASE)


@dataclass(frozen=True)
class EdoHeader:
    number: str
    date: str | None  # ISO; None when the document has none or an impossible one
    seller_name: str
    seller_inn: str
    seller_kpp: str
    buyer_name: str
    buyer_inn: str
    basis: str  # every ОснПер as «<name> № <number> от <ДД.ММ.ГГГГ>», joined by «; »
    payment_doc: str  # every СвПРД as «№ <number> от <ДД.ММ.ГГГГ>», joined by «, »
    total_net: int | None  # kopecks; for a correction — the signed difference: increase minus decrease
    total_vat: int | None
    total_gross: int | None


@dataclass(frozen=True)
class EdoLine:
    seq: int
    name: str
    article: str  # column А «код товара» (КодТов); АртикулТов only when the code is missing
    product_type_code: str
    unit_code: str
    unit: str
    quantity: Decimal | None
    price: Decimal | None  # rubles per unit without VAT, as in the document
    net: int | None  # kopecks
    excise: str
    vat_rate: str  # as in the document: «22%», «20/120», «без НДС»
    vat: int | None  # kopecks; 0 for «без НДС»
    gross: int | None
    country_code: str
    country: str
    declaration: str
    is_service: bool


@dataclass(frozen=True)
class EdoDoc:
    id_file: str
    format: str
    version: str
    func: str
    kind: str
    formed_at: str | None  # when the seller formed the file, «2026-04-28T09:04:49»: the later resend wins
    header: EdoHeader
    lines: list[EdoLine]
    issues: list[str]


@dataclass(frozen=True)
class Unsupported:
    """Not a document this reader takes, in words for the buyer."""

    reason: str


@dataclass(frozen=True)
class EdoXmlFile:
    name: str  # for messages only, never a filesystem path
    xml: str
    content: bytes  # the file as it came: kept as the original document


class UploadRejected(ValueError):
    pass


def edo_document_id(id_file: str) -> str:
    """The registry key: derived from the file's own ИдФайл (unique per document) and short enough for a URL — an
    ИдФайл runs to 160 characters."""
    return f"edo-{hashlib.sha1(id_file.encode('utf-8')).hexdigest()[:32]}"


# ---- tree helpers ----------------------------------------------------------------------------------------------

_EMPTY = fromstring("<_/>")


def _local(tag: Any) -> str:
    text = str(tag)
    return text.rsplit("}", 1)[-1]


def _kids(element: Any, name: str) -> list[Any]:
    return [child for child in element if _local(child.tag) == name]


def _node(element: Any, name: str) -> Any:
    kids = _kids(element, name)
    return kids[0] if kids else _EMPTY


def _has(element: Any, name: str) -> bool:
    return any(_local(child.tag) == name for child in element)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _attr(element: Any, name: str) -> str:
    return _clean(element.get(name))


def _first(element: Any, *names: str) -> str:
    return next((value for value in (_attr(element, name) for name in names) if value), "")


def _text(element: Any) -> str:
    return _clean(element.text)


def _uniq(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _rub(kopecks: int) -> str:
    return f"{Decimal(kopecks) / 100:.2f}"


def edo_date_to_iso(raw: str) -> str | None:
    """«07.07.2025» → «2025-07-07»; None when it is not a calendar date."""
    match = _DOTTED_DATE.match(raw)
    if not match:
        return None
    try:
        return date(int(match[3]), int(match[2]), int(match[1])).isoformat()
    except ValueError:
        return None


def _decimal_to_kop(raw: str) -> int | None | bool:
    """Exact decimal string → kopecks, rounding half away from zero; None for an empty one; False when not a number."""
    if raw == "":
        return None
    match = _DECIMAL.match(raw)
    if not match:
        return False
    frac = (match[3] or "").ljust(3, "0")
    kopecks = int(match[2]) * 100 + int(frac[:2]) + (1 if int(frac[2]) >= 5 else 0)
    return -kopecks if match[1] else kopecks


class _Numbers:
    """Number readers that note an unreadable value in the document's issues instead of guessing."""

    def __init__(self, issues: list[str], where: str = ""):
        self.issues = issues
        self.where = where

    def _bad(self, what: str, raw: str) -> None:
        self.issues.append(f"{self.where}не число в «{what}»: {raw}")

    def money(self, raw: str, what: str) -> int | None:
        kopecks = _decimal_to_kop(raw)
        if kopecks is False:
            self._bad(what, raw)
            return None
        return kopecks  # type: ignore[return-value]

    def plain(self, raw: str, what: str) -> Decimal | None:
        if raw == "":
            return None
        if _PLAIN.match(raw):
            return Decimal(raw)
        self._bad(what, raw)
        return None


def edo_doc_ref(name: str, number: str, day: str) -> str:
    """«<name> № <number> от <date>» without repeating what the name already says: 1С often writes the whole
    reference into the name («Счет 25К/23-20 от 09.04.2024») or ends it with «№»."""
    text = _clean(name)
    if number and not re.search(rf"(?:^|[\W_]){re.escape(number)}(?:$|[\W_])", text):
        text = f"{text} {number}" if text.endswith("№") else f"{text} № {number}" if text else f"№ {number}"
    if day and day not in text:
        text = f"{text} от {day}" if text else f"от {day}"
    return text


def _prefix(name: str) -> str | None:
    match = _PREFIX.match(name)
    return match[1].upper() if match else None


def _unsupported_format(prefix: str | None) -> str:
    if not prefix:
        return "не документ ЭДО в формате ФНС"
    if prefix in _BUYER_TITLES:
        return f"{prefix} — ответ покупателя, данные берутся из документа продавца"
    what = _OTHER_FORMATS.get(prefix)
    if prefix.startswith("DP_") and not what:
        return f"{prefix} — служебный файл ЭДО, не документ"
    return f"формат {prefix}{f' ({what})' if what else ''} пока не поддерживается"


def _party(element: Any) -> tuple[str, str, str]:
    """(name, inn, kpp) of СвПрод / СвПокуп."""
    ident = _node(element, "ИдСв")

    def fio(node: Any) -> str:
        return " ".join(part for part in (_attr(node, "Фамилия"), _attr(node, "Имя"), _attr(node, "Отчество")) if part)

    def digits(text: str) -> str:
        return _NOT_DIGIT.sub("", text)

    if _has(ident, "СвЮЛУч"):
        org = _node(ident, "СвЮЛУч")
        return _attr(org, "НаимОрг"), digits(_attr(org, "ИННЮЛ")), digits(_attr(org, "КПП"))
    if _has(ident, "СвИП"):
        person = _node(ident, "СвИП")
        name = fio(_node(person, "ФИО"))
        return (f"ИП {name}" if name else ""), digits(_attr(person, "ИННФЛ")), ""
    if _has(ident, "СвФЛУч"):
        person = _node(ident, "СвФЛУч")
        return fio(_node(person, "ФИО")), digits(_attr(person, "ИННФЛ")), ""
    return _attr(_node(ident, "СвИнНеУч"), "НаимОрг"), "", ""


def _vat_amount(element: Any, numbers: _Numbers) -> int | None:
    """СумНал / СумНалВсего: an amount, «без НДС» (0) or «-» (None)."""
    if _has(element, "СумНал"):
        return numbers.money(_text(_node(element, "СумНал")), "СумНал")
    if _has(element, "БезНДС"):
        return 0
    return None


def _is_advance(func: str, info: Any, lines: list[EdoLine]) -> bool:
    """Advance invoices (на предоплату) are tax documents only: they must not close an invoice."""
    if func != "СЧФ":
        return False
    kind_1c = next((text for text in _kids(info, "ТекстИнф") if _attr(text, "Идентиф") == "ВидСчетаФактуры"), None)
    if kind_1c is not None and re.search(r"аванс", _attr(kind_1c, "Значен"), re.IGNORECASE):
        return True
    return bool(lines) and all(_CALC_RATE.match(line.vat_rate) or _ADVANCE_NAME.search(line.name) for line in lines)


def _sum_check(issues: list[str], label: str, values: list[int | None], total: int | None) -> None:
    """XML amounts are exact: the lines must add up to the document total to the kopeck."""
    if total is None or not values or any(value is None for value in values):
        return
    found = sum(value for value in values if value is not None)
    if found != total:
        issues.append(f"{label} {_rub(found)} ≠ итог документа {_rub(total)}")


def _line(element: Any, index: int, issues: list[str]) -> EdoLine:
    raw_seq = _attr(element, "НомСтр")
    seq = int(raw_seq) if raw_seq.isascii() and raw_seq.isdigit() and int(raw_seq) > 0 else index + 1
    numbers = _Numbers(issues, f"строка {seq}: ")
    name = _attr(element, "НаимТов")
    extra = _node(element, "ДопСведТов")
    # 5.03: СвДТ / НомерДТ; 5.01: СвТД / НомерТД.
    customs = [*_kids(element, "СвДТ"), *_kids(element, "СвТД")]
    traced = [_attr(node, "НомТовПрослеж") for node in _kids(extra, "СведПрослеж")]
    tax = _node(element, "СумНал")
    excise = _node(element, "Акциз")
    work_kind = _attr(extra, "ПрТовРаб")
    return EdoLine(
        seq=seq,
        name=name,
        article=_attr(extra, "КодТов") or _attr(extra, "АртикулТов"),
        product_type_code=_attr(extra, "КодВидТов"),
        unit_code=_attr(element, "ОКЕИ_Тов"),
        unit=_attr(element, "НаимЕдИзм") or _attr(extra, "НаимЕдИзм"),
        quantity=numbers.plain(_attr(element, "КолТов"), "КолТов"),
        price=numbers.plain(_attr(element, "ЦенаТов"), "ЦенаТов"),
        net=numbers.money(_attr(element, "СтТовБезНДС"), "СтТовБезНДС"),
        excise=_text(_node(excise, "СумАкциз")) or _text(_node(excise, "БезАкциз")),
        vat_rate=_attr(element, "НалСт") or ("без НДС" if _has(tax, "БезНДС") else ""),
        vat=_vat_amount(tax, numbers),
        gross=numbers.money(_attr(element, "СтТовУчНал"), "СтТовУчНал"),
        country_code=", ".join(_uniq([_attr(node, "КодПроисх") for node in customs])),
        # 5.01 keeps the country in an attribute, 5.03 in child elements.
        country=_attr(extra, "КрНаимСтрПр") or ", ".join(_uniq([_text(node) for node in _kids(extra, "КрНаимСтрПр")])),
        declaration=", ".join(_uniq([_first(node, "НомерДТ", "НомерТД") for node in customs])) or ", ".join(_uniq(traced)),
        # ПрТовРаб: 1 имущество, 2 работа, 3 услуга, 4 имущественные права, 5 иное. Without it — the name.
        is_service=work_kind in ("2", "3") if work_kind else bool(_SERVICE_NAME.search(name)),
    )


def _drop_marking(root: Any) -> None:
    for parent in list(root.iter()):
        for child in list(parent):
            if _local(child.tag) in _MARKING:
                parent.remove(child)


def parse_edo_xml(xml: str, file_name: str) -> EdoDoc | Unsupported:
    source = xml[1:] if xml.startswith("﻿") else xml
    if re.search(r"<!DOCTYPE", source, re.IGNORECASE):
        return Unsupported("XML с DOCTYPE не принимается")
    try:
        root = fromstring(source, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException:
        return Unsupported("XML с DOCTYPE не принимается")
    except ParseError as error:
        line = error.position[0] if getattr(error, "position", None) else 0
        return Unsupported(f"XML повреждён: {_POSITION.sub('', str(error))} (строка {line})")
    _drop_marking(root)
    if _local(root.tag) != "Файл":
        return Unsupported("не документ ЭДО в формате ФНС")
    id_file = _attr(root, "ИдФайл")
    prefix = _prefix(id_file) or _prefix(re.split(r"[\\/]", file_name)[-1])
    if prefix not in ("ON_NSCHFDOPPR", "ON_NKORSCHFDOPPR"):
        return Unsupported(_unsupported_format(prefix))
    if not id_file:
        return Unsupported("в файле нет ИдФайл — не документ ЭДО")

    version = _attr(root, "ВерсФорм")
    doc = _node(root, "Документ")
    func = _attr(doc, "Функция")
    correction = prefix == "ON_NKORSCHFDOPPR"
    head = _node(doc, "СвКСчФ" if correction else "СвСчФакт")
    if head is _EMPTY or (not head.attrib and len(head) == 0):
        return Unsupported(f"нет сведений о документе ({'СвКСчФ' if correction else 'СвСчФакт'})")

    issues: list[str] = []
    numbers = _Numbers(issues)
    if version not in _KNOWN_VERSIONS:
        issues.append(f"версия формата {version or 'не указана'} не проверялась")
    if correction:
        kind = "Корректировочный СФ"
        if func not in _CORRECTION_FUNCS:
            issues.append(f"функция документа «{func}» не распознана")
    else:
        kind = _KINDS.get(func, "УПД")
        if func not in _KINDS:
            issues.append(f"функция документа «{func}» не распознана — считаю УПД")

    raw_date = _first(head, "ДатаДок", "ДатаСчФ", "ДатаКСчФ")
    day = edo_date_to_iso(raw_date)
    if raw_date and not day:
        issues.append(f"дата документа не распознана: {raw_date}")
    currency = _attr(head, "КодОКВ") or _attr(_node(head, "ДенИзм"), "КодОКВ")
    if currency and currency != "643":
        issues.append(f"валюта документа — код {currency}, суммы не в рублях")
    fix = next((_node(head, name) for name in ("ИспрДок", "ИспрСчФ", "ИспрКСчФ") if _has(head, name)), _EMPTY)
    fix_number = _first(fix, "НомИспр", "НомИспрСчФ", "НомИспрКСчФ")
    if fix_number:
        fix_ref = edo_doc_ref("исправление", fix_number, _first(fix, "ДатаИспр", "ДатаИспрСчФ", "ДатаИспрКСчФ"))
        issues.append(f"исправленный документ: {fix_ref}")

    seller_name, seller_inn, seller_kpp = _party(_node(head, "СвПрод"))
    buyer_name, buyer_inn, _ = _party(_node(head, "СвПокуп"))
    payment_doc = ", ".join(
        ref for ref in (edo_doc_ref("", _attr(p, "НомерПРД"), _attr(p, "ДатаПРД")) for p in _kids(head, "СвПРД")) if ref
    )
    lines: list[EdoLine] = []
    if correction:
        # The corrected invoices stand in for the basis: the correction belongs to their chain.
        basis = "; ".join(ref for ref in (
            edo_doc_ref("Счёт-фактура", _first(s, "НомерСчФ", "НомерДок", "РеквНомерДок"), _first(s, "ДатаСчФ", "ДатаДок", "РеквДатаДок"))
            for s in _kids(head, "СчФ")
        ) if ref)
        table = _node(doc, "ТаблКСчФ")
        up, down = _node(table, "ВсегоУвел"), _node(table, "ВсегоУм")

        def diff(a: int | None, b: int | None) -> int | None:
            return None if a is None and b is None else (a or 0) - (b or 0)

        def total(node: Any, name: str) -> int | None:
            return numbers.money(_attr(node, name), name)

        def vat(node: Any) -> int | None:
            return _vat_amount(_node(node, "СумНал") if _has(node, "СумНал") else _node(node, "СумНалВсего"), numbers)

        total_net = diff(total(up, "СтТовБезНДСВсего"), total(down, "СтТовБезНДСВсего"))
        total_vat = diff(vat(up), vat(down))
        total_gross = diff(total(up, "СтТовУчНалВсего"), total(down, "СтТовУчНалВсего"))
    else:
        transfer = _node(_node(doc, "СвПродПер"), "СвПер")
        basis = "; ".join(ref for ref in (
            edo_doc_ref(_first(o, "РеквНаимДок", "НаимОсн"), _first(o, "РеквНомерДок", "НомОсн"), _first(o, "РеквДатаДок", "ДатаОсн"))
            for o in _kids(transfer, "ОснПер")
        ) if ref)
        table = _node(doc, "ТаблСчФакт")
        lines = [_line(item, index, issues) for index, item in enumerate(_kids(table, "СведТов"))]
        totals = _node(table, "ВсегоОпл")
        total_net = numbers.money(_attr(totals, "СтТовБезНДСВсего"), "СтТовБезНДСВсего")
        total_vat = _vat_amount(_node(totals, "СумНалВсего"), numbers)
        total_gross = numbers.money(_attr(totals, "СтТовУчНалВсего"), "СтТовУчНалВсего")
        if _is_advance(func, _node(head, "ИнфПолФХЖ1"), lines):
            return Unsupported("авансовый счёт-фактура (на предоплату) — не закрывающий документ")
        if not lines:
            issues.append("нет ни одной позиции")
        _sum_check(issues, "сумма строк", [line.gross for line in lines], total_gross)
        _sum_check(issues, "сумма строк без НДС", [line.net for line in lines], total_net)
        _sum_check(issues, "НДС по строкам", [line.vat for line in lines], total_vat)

    number = _first(head, "НомерДок", "НомерСчФ", "НомерКСчФ")
    if not number:
        issues.append("нет номера документа")
    if not raw_date:
        issues.append("нет даты документа")
    if not seller_name:
        issues.append("нет продавца")
    if not seller_inn:
        issues.append("нет ИНН продавца")

    formed_day = edo_date_to_iso(_attr(doc, "ДатаИнфПр"))
    formed_time = _DOTTED_TIME.match(_attr(doc, "ВремИнфПр"))
    formed_at = f"{formed_day}T{':'.join(formed_time.groups()) if formed_time else '00:00:00'}" if formed_day else None
    header = EdoHeader(
        number=number, date=day, seller_name=seller_name, seller_inn=seller_inn, seller_kpp=seller_kpp,
        buyer_name=buyer_name, buyer_inn=buyer_inn, basis=basis, payment_doc=payment_doc,
        total_net=total_net, total_vat=total_vat, total_gross=total_gross,
    )
    return EdoDoc(
        id_file=id_file, format=prefix, version=version, func=func, kind=kind, formed_at=formed_at, header=header,
        lines=lines, issues=issues,
    )


# ---- uploads ---------------------------------------------------------------------------------------------------

def decode_edo_xml(content: bytes) -> str:
    """ФНС XML is windows-1251 by its declaration; a BOM or the declaration decides, utf-8 otherwise."""
    if content[:3] == b"\xef\xbb\xbf":
        return content[3:].decode("utf-8", errors="replace")
    if content[:2] == b"\xff\xfe":
        return content[2:].decode("utf-16-le", errors="replace")
    if content[:2] == b"\xfe\xff":
        return content[2:].decode("utf-16-be", errors="replace")
    head = content[:200].decode("latin-1")
    match = _ENCODING.match(head)
    label = match[1] if match else "utf-8"
    try:
        return content.decode(label)
    except LookupError:
        # An unknown label: ФНС formats are windows-1251.
        return content.decode("cp1251", errors="replace")
    except UnicodeDecodeError:
        # Declared (or assumed) utf-8, but the bytes are not: the usual culprit is windows-1251.
        return content.decode("cp1251", errors="replace")


def _base_name(path: str) -> str:
    return re.split(r"[\\/]", path)[-1]


def _unpack_zip(name: str, content: bytes) -> list[EdoXmlFile]:
    if len(content) > LIMITS["zip_bytes"]:
        raise UploadRejected(f"архив «{name}» больше 20 МБ")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        entries = archive.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError) as error:
        raise UploadRejected(f"архив «{name}» не читается: {error}") from None
    # Sizes come from the archive's own headers and are checked before anything is inflated; the read below stops at
    # the declared size, so a lying header cannot make an entry bigger.
    if len(entries) > LIMITS["entries"]:
        raise UploadRejected(f"в архиве «{name}» больше 500 файлов")
    if sum(entry.file_size for entry in entries) > LIMITS["unzipped_bytes"]:
        raise UploadRejected(f"архив «{name}» распаковывается больше чем в 100 МБ")
    found: list[EdoXmlFile] = []
    for entry in entries:
        base = _base_name(entry.filename)
        if entry.is_dir() or not _DOCUMENT_ENTRY.match(base):
            continue
        if entry.file_size > LIMITS["xml_bytes"]:
            raise UploadRejected(f"«{base}» в архиве «{name}» больше 20 МБ")
        try:
            with archive.open(entry) as stream:
                data = stream.read(entry.file_size + 1)
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, ValueError, OSError, EOFError) as error:
            raise UploadRejected(f"архив «{name}» не читается: {error}") from None
        if len(data) > entry.file_size:
            raise UploadRejected(f"архив «{name}» повреждён: «{base}» больше заявленного размера")
        found.append(EdoXmlFile(base, decode_edo_xml(data), data))
    if not found:
        raise UploadRejected(f"в архиве «{name}» нет XML-документов ЭДО (файлов ON_… или DP_…)")
    return found


def unpack_edo_upload(name: str, content: bytes) -> list[EdoXmlFile]:
    """One uploaded file → the EDO XMLs in it: the file itself, or the documents of a ZIP archive. Raises
    UploadRejected with the reason in words."""
    is_zip = content[:2] == b"PK" and content[2:3] in (b"\x03", b"\x05")
    if is_zip or name.lower().endswith(".zip"):
        return _unpack_zip(name, content)
    if not name.lower().endswith(".xml"):
        raise UploadRejected(f"«{name}»: нужен XML или ZIP из ЭДО")
    if len(content) > LIMITS["xml_bytes"]:
        raise UploadRejected(f"файл «{name}» больше 20 МБ")
    return [EdoXmlFile(name, decode_edo_xml(content), content)]


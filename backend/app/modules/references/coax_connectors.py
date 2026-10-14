"""The 311 coax connectors of gsm-repiteri.ru (`backend/data/cables/coax-connectors-gsm.json`, checked 12.10.2026)
→ the body of `POST /api/references/import/coax` (owner 13.10.2026: load them into БД.КОАКС through the import API).

    python -m app.modules.references.coax_connectors --export backend/data/cables/coax-connectors-gsm.import.json

One row per connector and cable (the table's key is the article + «Провод»; a connector the site gives for RG-174 and
RG-316 is two rows, as БД.КОАКС keeps SMA-C174P). How a site entry becomes rows:

* article — the site's; an entry without one is named by what tells it apart: «<тип> [угловой] [монтаж, кроме
  обжимного] [с иглой] [на корпус] для <кабели>» — «FAKRA-female (тип C) для RG-174/RG-316» (owner 14.10.2026: «а
  зачем мне номера?»); the site number stays in «Примечание» and names it only when two entries would share a name;
  two entries with one article and cable: the angled one becomes «<артикул> R/A» (БД.КОАКС names angled connectors
  so), the same connector on two pages is one row with the second page in «Примечание»;
* a pair БД.КОАКС already has (the mirror `backend/data/reference-mirror/БД.КОАКС.tsv`, matched as the import
  matches: article without case, cable by `cable_key`) gets the card columns only — its type, maker, «Тип пина» /
  «Тип экрана» and lengths are the technologist's and are not re-sent;
* a new pair gets its type, maker, «Тип пина» / «Тип экрана» in the base's words (пайка / обжим / пайка; anything
  else — цанга, прижим, накрутка — is left empty, as the generator then asks) and the card;
* card columns (`importers.COAX_CARD_COLUMNS`): series, gender, polarity, angle, mount, the site's words for the pin
  and the shield, impedance, max frequency, body material, plating, size, the site's name, link, note, source.
  Empty values are left out (null-spec: the load neither clears nor guesses).

The import never deletes; a second apply changes nothing. Dimensions D1–D3 belong to the cable (СПР.КАБ), lengths
L1–L3 / L± are not on the site — they stay for the technologist."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import cable_key
from .importers import COAX_CARD_COLUMNS
from .technology import (
    TECH_SNAPSHOT_SCHEMA_VERSION, import_supplymap_snapshot, parse_mirror_rows, sheet_snapshot_records,
)

DATA = Path(__file__).resolve().parents[3] / "data"
GSM_FILE = DATA / "cables" / "coax-connectors-gsm.json"
EXPORT_FILE = DATA / "cables" / "coax-connectors-gsm.import.json"
MIRROR_COAX = DATA / "reference-mirror" / "БД.КОАКС.tsv"
GSM_VERSION = 1

GENDER = {"male": "штекер", "female": "гнездо"}
POLARITY = {"normal": "обычная", "RP": "обратная (RP)"}
_SITE_NUMBER = re.compile(r"номер на сайте:\s*(\d+)")
_SHIELD_CRIMP = re.compile(r"^обжим")
_SHIELD_SOLDER = re.compile(r"(^|:\s*)пайка$")


class GsmFileError(ValueError):
    pass


@dataclass(frozen=True)
class CoaxExport:
    rows: list[dict[str, Any]]
    connectors: int  # site entries read
    merged: list[str]  # entries folded into an earlier row (the same connector on another page)
    existing: int  # rows whose pair БД.КОАКС already has (card columns only)


def _text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def _number(value: object) -> str:
    """6.0 → «6», 2.5 → «2,5» (the base writes decimals with a comma)."""
    if value is None or value == "":
        return ""
    number = float(value)
    return (str(int(number)) if number.is_integer() else f"{number:g}").replace(".", ",")


def pin_type(pin: object) -> str:
    """«Тип пина» in the base's words: only «пайка» is one; цанга / «не требуется» stay empty."""
    return "пайка" if _text(pin).casefold() == "пайка" else ""


def shield_type(shield: object) -> str:
    """«Тип экрана» in the base's words (обжим / пайка); прижим, накрутка, компрессия stay empty."""
    text = _text(shield).casefold()
    if _SHIELD_CRIMP.search(text):
        return "обжим"
    if _SHIELD_SOLDER.search(text):
        return "пайка"
    return ""


def site_number(entry: Mapping[str, Any]) -> str:
    match = _SITE_NUMBER.search(_text(entry.get("note")))
    return match.group(1) if match else ""


def _article(entry: Mapping[str, Any]) -> str:
    article = _text(entry.get("article"))
    if article:
        return article
    kind = _text(entry.get("type_name")) or _text(entry.get("series"))
    if not kind:
        return _text(entry.get("full_name"))
    name, mount = _text(entry.get("full_name")).casefold(), _text(entry.get("mount"))
    cables = "/".join(_text(cable) for cable in entry.get("cable") or [] if _text(cable))
    parts = [kind, "угловой" if _text(entry.get("angle")) == "угловой" else "",
             mount if mount and mount != "обжимной" else "", "с иглой" if "игл" in name else "",
             "на корпус" if "на корпус" in name else "", f"для {cables}" if cables else ""]
    return " ".join(part for part in parts if part)


def _numbered(entry: Mapping[str, Any]) -> str:
    """The name of a second, different site entry that would get the same descriptive name: with its site number."""
    number = site_number(entry)
    return f"{_article(entry)} №{number}" if number else _article(entry)


def _card(entry: Mapping[str, Any], checked_on: str, source: str) -> dict[str, str]:
    dims = entry.get("dims_mm") or {}
    card = {
        "Серия": _text(entry.get("series")),
        "Пол": GENDER.get(_text(entry.get("gender")), ""),
        "Полярность": POLARITY.get(_text(entry.get("polarity")), ""),
        "Исполнение": _text(entry.get("angle")),
        "Монтаж": _text(entry.get("mount")),
        "Центральный контакт": _text(entry.get("pin")),
        "Крепление экрана": _text(entry.get("shield")),
        "Волновое сопротивление, Ом": _number(entry.get("impedance_ohm")),
        "Частота до, ГГц": _number(entry.get("max_freq_ghz")),
        "Материал корпуса": _text(entry.get("material_body")),
        "Покрытие": _text(entry.get("plating")),
        "Габариты": _text(dims.get("размер_как_на_сайте") if isinstance(dims, Mapping) else ""),
        "Наименование": _text(entry.get("full_name")),
        "Ссылка": _text(entry.get("url")),
        "Примечание": _text(entry.get("note")),
        "Источник данных": f"{source}, {_date(checked_on)}",
    }
    return {name: value for name, value in card.items() if value}


def _date(iso: str) -> str:
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", _text(iso))
    return f"{match.group(3)}.{match.group(2)}.{match.group(1)}" if match else _text(iso)


def mirror_pairs(path: Path = MIRROR_COAX) -> set[tuple[str, str]]:
    """The (article, cable) pairs of БД.КОАКС as the import matches them."""
    headers, records = sheet_snapshot_records("coax", parse_mirror_rows(path.read_text(encoding="utf-8")))
    snapshot = import_supplymap_snapshot({"meta": {
        "schemaVersion": TECH_SNAPSHOT_SCHEMA_VERSION, "columnHeadersByTab": {"coax": headers},
    }, "records": records})
    return {pair_key(record.fields.get("coaxArticle"), record.fields.get("coaxWire")) for record in snapshot.records}


def pair_key(article: object, cable: object) -> tuple[str, str]:
    return _text(article).casefold(), cable_key(_text(cable))


def gsm_import_rows(document: Mapping[str, Any], existing: Iterable[tuple[str, str]] = ()) -> CoaxExport:
    """The site file → rows for `POST /api/references/import/coax` (see the module doc)."""
    if not isinstance(document, Mapping) or document.get("version") != GSM_VERSION \
            or not isinstance(document.get("connectors"), list):
        raise GsmFileError(f"Не файл разъёмов gsm-repiteri.ru версии {GSM_VERSION}")
    known = set(existing)
    checked_on, source = _text(document.get("checked_on")), _text(document.get("source")) or "gsm-repiteri.ru"
    entries: Sequence[Mapping[str, Any]] = document["connectors"]
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    angle_of: dict[tuple[str, str], str] = {}
    page_of: dict[str, str] = {}  # a descriptive name (no site article) → the page that took it
    merged: list[str] = []
    for entry in entries:
        name = _article(entry)
        if not _text(entry.get("article")):
            if page_of.setdefault(name, _text(entry.get("url"))) != _text(entry.get("url")):
                name = _numbered(entry)
        cables = [_text(cable) for cable in entry.get("cable") or [] if _text(cable)]
        angle = _text(entry.get("angle"))
        if angle == "угловой" and any(angle_of.get(pair_key(name, cable), angle) != angle for cable in cables):
            name = f"{name} R/A"  # the straight one took the article: the angled one is named as БД.КОАКС names them
        for cable in cables:
            key = pair_key(name, cable)
            if key in rows:
                fields = rows[key]["fields"]
                also = f"также на сайте: {_text(entry.get('url'))}"
                if also not in fields.get("Примечание", ""):
                    fields["Примечание"] = "; ".join(part for part in (fields.get("Примечание", ""), also) if part)
                merged.append(f"{name} / {cable}")
                continue
            fields: dict[str, Any] = {"coaxArticle": name, "coaxWire": cable}
            if key not in known:
                fields.update({
                    "coaxType": _text(entry.get("type_name")),
                    "coaxMfr": _text(entry.get("manufacturer")),
                    "Тип пина": pin_type(entry.get("pin")),
                    "Тип экрана": shield_type(entry.get("shield")),
                })
            fields.update(_card(entry, checked_on, source))
            rows[key] = {"fields": {column: value for column, value in fields.items() if value}}
            angle_of[key] = angle
    existing_rows = sum(1 for key in rows if key in known)
    return CoaxExport(rows=list(rows.values()), connectors=len(entries), merged=merged, existing=existing_rows)


def build_export(gsm: Path = GSM_FILE, mirror: Path = MIRROR_COAX) -> CoaxExport:
    try:
        document = json.loads(gsm.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise GsmFileError(f"{gsm.name}: {error}") from error
    return gsm_import_rows(document, mirror_pairs(mirror))


def export_body(export: CoaxExport) -> str:
    return json.dumps({"rows": export.rows}, ensure_ascii=False, indent=1) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Тело загрузки коаксиальных разъёмов gsm-repiteri.ru в БД.КОАКС "
                                                 "(POST /api/references/import/coax)")
    parser.add_argument("--export", default=str(EXPORT_FILE), metavar="FILE", help="куда записать тело загрузки")
    parser.add_argument("--source", default=str(GSM_FILE), help="файл разъёмов (coax-connectors-gsm.json)")
    parser.add_argument("--mirror", default=str(MIRROR_COAX), help="БД.КОАКС.tsv — какие пары уже есть в базе")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        export = build_export(Path(args.source), Path(args.mirror))
    except (GsmFileError, OSError) as error:
        print(f"Файл не читается: {error}", file=sys.stderr)
        return 2
    Path(args.export).write_text(export_body(export), encoding="utf-8", newline="")
    print(f"Разъёмов на сайте {export.connectors} → строк БД.КОАКС {len(export.rows)} (разъём × кабель), "
          f"из них уже в базе {export.existing} (только карточка), склеено повторов {len(export.merged)} → {args.export}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

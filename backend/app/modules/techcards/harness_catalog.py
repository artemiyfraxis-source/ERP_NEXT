"""Каталог редактора жгута из НАШИХ справочников (H-T4).

Редактор жгута (TECHMAP-GRAPHER, порт в `frontend/src/harness/`) читает справочники как опубликованные снимки
`ReferenceCatalogSnapshot`: по снимку на источник (`technology-wires`, `technology-terminals`, …), в каждом записи
`{recordId, entityType, sourceKey, payload, sourceLocation}`. Здесь чистое отображение баз ERP в эту форму:

- БД.ТЕР → `terminal` (L+ / L− — поправки длины резки, зачистка, сечения, Ø изоляции);
- БД.ПРОВ → `wire` (одна жила) и `cable` (жил больше одной);
- СПР.КАБ (коаксиал) → `coax-cable` со слоями D1–D3;
- БД.КОАКС → `coax-termination` со слоями D/L разделки: L — разъёма, D — только кабеля из СПР.КАБ (владелец 16.10.2026);
- БД.МАТ (термоусадка, оплётка, лента, гофра, кембрик) → `protective-covering`;
- семейства корпусов разъёмов (`connector_housings`) → `connector-family`, правила аналогов → `connector-alias`.

Наши базы — единственный источник: образцовый каталог TECHMAP-GRAPHER сюда не попадает. Идентичность стабильна:
`recordId` = sha256(тип + ключ), `sha256` снимка — от его записей, `snapshotId` — UUID5 от sha256, так что те же
данные дают тот же снимок и привязки документа к нему не «плывут» от перезагрузки справочника.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID, uuid5

from .connector_housings import ALIAS_RULES, ALIASES, CATALOG_SAMPLES, FAMILIES, housing_of
from .harness.validate import ReferenceBook

CONTRACT_VERSION = 1
SOURCE_KIND = "erp-references"
SNAPSHOT_NAMESPACE = UUID("5f0c8a0e-6a51-4a8e-9d55-3c1f2a7b9e40")

WIRES = "technology-wires"
TERMINALS = "technology-terminals"
COAX_CABLES = "technology-coax-cables"
COAX_TERMINATIONS = "technology-coax-terminations"
DATABASE = "technology-database"
CONNECTORS = "technology-connectors"

# Tabs of the references module (`TechOperationTab` values) the catalog reads.
TABS = ("ter", "prov", "kab_coax", "coax", "mat")

SHEETS = {"ter": "БД.ТЕР", "prov": "БД.ПРОВ", "kab_coax": "СПР.КАБ", "coax": "БД.КОАКС", "mat": "БД.МАТ",
          "connectors": "База разъёмов ERP"}

# БД.МАТ «Группа» → covering kind of the editor (None: a covering without its own drawing style). Other groups
# (стяжка, хомут, гильза, клей, припой, феррит, бирка…) are fasteners and consumables, not coverings.
COVERING_GROUPS: dict[str, str | None] = {
    "тут": "heat-shrink", "ттк": "heat-shrink", "оплётка": "braid", "оплетка": "braid", "плетёнка": "braid",
    "плетенка": "braid", "лента": "tape", "гофра": None, "кембрик": None,
}

_NUMBER = re.compile(r"^[+\-−]?\d+(?:[.,]\d+)?$")


def number(value: object) -> float | None:
    """A sheet cell as a number: decimal comma, spaces and «мм» allowed; anything else (—, «нет», text) → None."""
    text = re.sub(r"[\s\u00a0]", "", str(value or "")).removesuffix("мм").removesuffix("mm")
    if not _NUMBER.match(text):
        return None
    return float(text.replace(",", ".").replace("−", "-"))


# Cells that say «no value» in words: treated as empty, not as a bad number.
NO_VALUE = frozenset({"-", "—", "–", "нет", "нет данных", "не найдено", "н/д", "⚠ нет данных"})


def _text(fields: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(fields.get(key) or "").strip()
        if value:
            return value
    return ""


def number_or_text(fields: Mapping[str, Any], *keys: str) -> float | str | None:
    raw = _text(fields, *keys)
    if not raw or raw.casefold() in NO_VALUE:
        return None
    value = number(raw)
    return raw if value is None else value


def cable_key(value: object) -> str:
    """The ETK generator's cable key: «RG-316», «RG 316» and «rg316» are one cable."""
    return re.sub(r"[\s-]", "", str(value or "").lower())


def _payload(**values: Any) -> dict[str, Any]:
    """Drops empty values: the editor treats a missing key as «не заполнено»."""
    return {key: value for key, value in values.items() if value not in (None, "", [], {})}


class _Collector:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.diagnostics: list[dict[str, Any]] = []

    def record(self, entity_type: str, source_key: str, payload: dict[str, Any], sheet: str) -> None:
        self.records.append({
            "recordId": hashlib.sha256(f"{entity_type}\n{source_key}".encode()).hexdigest(),
            "entityType": entity_type, "sourceKey": source_key, "payload": payload, "sourceLocation": sheet,
        })

    def warn(self, code: str, message: str, *, entity_type: str | None, source_key: str | None = None,
             field: str | None = None, sheet: str | None = None, severity: str = "warning") -> None:
        self.diagnostics.append({
            "diagnosticId": f"{code}:{len(self.diagnostics) + 1}", "severity": severity, "code": code,
            "message": message, "entityType": entity_type, "sourceKey": source_key, "field": field,
            "sourceLocation": sheet,
        })


def _measure(collector: _Collector, fields: Mapping[str, Any], column: str, *, entity_type: str, key: str,
             sheet: str, aliases: tuple[str, ...] = ()) -> float | None:
    """A numeric column; a filled cell that is not a number is reported, not guessed."""
    raw = _text(fields, *aliases, column)
    if not raw or raw.casefold() in NO_VALUE:
        return None
    value = number(raw)
    if value is None:
        collector.warn("value-not-number", f"«{column}» = «{raw}» — не число, значение не передано в редактор",
                       entity_type=entity_type, source_key=key, field=column, sheet=sheet)
    return value


def _terminals(rows: Iterable[Mapping[str, Any]], out: _Collector) -> None:
    sheet = SHEETS["ter"]
    seen: set[str] = set()
    for fields in rows:
        reel = _text(fields, "terArticle", "Артикул контакта (REEL)")
        bag = _text(fields, "Артикул контакта (BAG)")
        key = reel or bag
        if not key:
            out.warn("terminal-article-missing", "Строка БД.ТЕР без артикула контакта — в редактор не передана",
                     entity_type="terminal", sheet=sheet)
            continue
        if key in seen:
            out.warn("terminal-duplicate", f"Артикул «{key}» повторяется в БД.ТЕР — передана первая строка",
                     entity_type="terminal", source_key=key, sheet=sheet)
            continue
        seen.add(key)

        def measure(column: str, *aliases: str) -> float | None:
            return _measure(out, fields, column, entity_type="terminal", key=key, sheet=sheet, aliases=aliases)

        plus = measure("L+", "terLPlus")
        minus = measure("L-", "terLMinus")
        out.record("terminal", key, _payload(
            reelArticle=reel, bagArticle=bag,
            manufacturer=_text(fields, "terManufacturer", "Производитель"),
            series=_text(fields, "terSeries", "Series"),
            productName=_text(fields, "terComponent", "Product Name"),
            connectorType=_text(fields, "Тип разъема"),
            contactType=_text(fields, "terType", "Тип контакта"),
            applicator=_text(fields, "terApplicator", "Аппликатор"),
            # Shown, not computed with: a range («3,00 - 3,50») or a double pitch («3 / 2») stays as written.
            pitchMm=number_or_text(fields, "terStep", "Шаг разьема"),
            stripLengthMm=number_or_text(fields, "terStrip", "Длина зачистки, мм"),
            # L+ adds material to the cut length, L− takes it away; the editor keeps the sign by meaning.
            lengthPlusMm=None if plus is None else abs(plus),
            lengthMinusMm=None if minus is None else abs(minus),
            # Crimp heights and pull force are often ranges per AWG («22 AWG: 0,66 - 0,72») — kept as text.
            crimpHeight=_text(fields, "terCrimpHeight", "Высота обжима проводника , мм"),
            insulationCrimpHeight=_text(fields, "Высота обжима изоляции, мм"),
            pullForce=_text(fields, "terPullForceMin", "Усилие обрыва контакта, N"),
            awgFrom=measure("От AWG", "awgFrom"), awgTo=measure("До AWG", "awgTo"),
            sectionFromMm2=measure("От мм2", "mm2From"), sectionToMm2=measure("До мм2", "mm2To"),
            insulationDiameterFromMm=measure("Диаметр изоляции от, мм"),
            insulationDiameterToMm=measure("Диаметр изоляции до, мм"),
            material=_text(fields, "Материал контакта"),
            datasheetUrl=_text(fields, "Ссылка на DATASHEET на сайте производителя"),
        ), sheet)


def _cores(value: str) -> int | None:
    match = re.match(r"^\s*(\d+)", value)
    return int(match.group(1)) if match else None


def _wires(rows: Iterable[Mapping[str, Any]], out: _Collector) -> None:
    sheet = SHEETS["prov"]
    seen: set[str] = set()
    for fields in rows:
        mark = _text(fields, "Марка")
        if not mark:
            continue
        if mark in seen:
            out.warn("wire-duplicate", f"Марка «{mark}» повторяется в БД.ПРОВ — передана первая строка",
                     entity_type="wire", source_key=mark, sheet=sheet)
            continue
        seen.add(mark)
        cores = _cores(_text(fields, "Число жил"))
        entity = "cable" if cores is not None and cores > 1 else "wire"
        section = _text(fields, "Сечение, мм2")
        awg = _text(fields, "AWG")
        size = section or (f"{awg} AWG" if awg else "")
        out.record(entity, mark, _payload(
            **{"Марка": mark},
            wireType=_text(fields, "Тип провода"),
            manufacturer=_text(fields, "Производители", "Стандарт / производитель"),
            # Multi-core: the editor shows «4x0,35» from Core + «Сечение C»; a single wire shows its section alone.
            sectionMm2=size if entity == "wire" else "",
            Core=str(cores) if entity == "cable" else "",
            **{"Сечение C": size if entity == "cable" else ""},
            awg=awg, conductor=_text(fields, "Жила: проволок × диаметр, мм"),
            colors=_text(fields, "Цвета"),
            outerDiameterMm=_measure(out, fields, "Наружный диаметр, мм", entity_type=entity, key=mark, sheet=sheet),
            insulation=_text(fields, "Материал оболочки"), shield=_text(fields, "Экран"),
            impedanceOhm=_measure(out, fields, "Волновое сопротивление, Ом", entity_type=entity, key=mark, sheet=sheet),
        ), sheet)


def _coax_layers(fields: Mapping[str, Any], out: _Collector, *, entity: str, key: str, sheet: str,
                 lengths: bool, cable: Mapping[int, float] | None = None) -> tuple[list[dict[str, Any]], bool]:
    """Layers of a coax cable (its own D1–D3) or of a connector end (`lengths`): the end's L1–L3 with the D1–D3 of
    its cable (`cable`, from СПР.КАБ) only — a connector row's D is never read (owner 16.10.2026)."""
    layers: list[dict[str, Any]] = []
    filled = False
    for index in (1, 2, 3):
        if lengths:
            diameter = (cable or {}).get(index)
            filled = filled or diameter is not None
        else:
            diameter = _measure(out, fields, f"D{index}", entity_type=entity, key=key, sheet=sheet)
        length = _measure(out, fields, f"L{index}", entity_type=entity, key=key, sheet=sheet) if lengths else None
        if diameter is None and length is None:
            continue
        layer: dict[str, Any] = {"index": index, "diameterMm": diameter}
        if lengths:
            layer["stripLengthMm"] = length
        layers.append(layer)
    return layers, filled


def _coax_cables(rows: Iterable[Mapping[str, Any]], out: _Collector) -> dict[str, dict[int, float]]:
    """СПР.КАБ coax table → `coax-cable` records; returns the D1–D3 by cable key for БД.КОАКС."""
    sheet = SHEETS["kab_coax"]
    diameters: dict[str, dict[int, float]] = {}
    for fields in rows:
        name = _text(fields, "Кабель")
        if not name or cable_key(name) in diameters:
            continue
        layers, _ = _coax_layers(fields, out, entity="coax-cable", key=name, sheet=sheet, lengths=False)
        diameters[cable_key(name)] = {layer["index"]: layer["diameterMm"] for layer in layers
                                      if layer["diameterMm"] is not None}
        outer = max((layer["diameterMm"] for layer in layers if layer["diameterMm"] is not None), default=None)
        out.record("coax-cable", name, _payload(name=name, layers=layers, outerDiameterMm=outer), sheet)
    return diameters


def _coax_terminations(rows: Iterable[Mapping[str, Any]], out: _Collector,
                       cable_diameters: Mapping[str, Mapping[int, float]]) -> None:
    sheet = SHEETS["coax"]
    rows = [fields for fields in rows if _text(fields, "coaxArticle", "Артикул")]
    articles = Counter(_text(fields, "coaxArticle", "Артикул") for fields in rows)
    keys: Counter[str] = Counter()
    for fields in rows:
        article = _text(fields, "coaxArticle", "Артикул")
        cable = _text(fields, "coaxWire", "Провод")
        # The same connector article serves several cables (SMA-C174P: RG-174 and RG-316) — the cable tells them apart.
        key = article if articles[article] == 1 or not cable else f"{article} / {cable}"
        keys[key] += 1
        if keys[key] > 1:
            key = f"{key} ({keys[key]})"
        layers, filled = _coax_layers(fields, out, entity="coax-termination", key=key, sheet=sheet, lengths=True,
                                      cable=cable_diameters.get(cable_key(cable)))
        out.record("coax-termination", key, _payload(
            article=article, connectorType=_text(fields, "coaxType", "Тип/Серия"),
            manufacturer=_text(fields, "coaxMfr", "Производитель"), cable=cable,
            pinType=_text(fields, "Тип пина"), shieldType=_text(fields, "Тип экрана"),
            lengthPlusMm=_measure(out, fields, "L+", entity_type="coax-termination", key=key, sheet=sheet),
            lengthMinusMm=_measure(out, fields, "L-", entity_type="coax-termination", key=key, sheet=sheet),
            diametersFrom="СПР.КАБ" if filled else "",
            layers=layers,
        ), sheet)


def _coverings(rows: Iterable[Mapping[str, Any]], out: _Collector) -> None:
    sheet = SHEETS["mat"]
    seen: set[str] = set()
    for fields in rows:
        name = _text(fields, "Наименование")
        group = _text(fields, "Группа")
        if not name or group.casefold() not in COVERING_GROUPS or name in seen:
            continue
        seen.add(name)

        def measure(column: str) -> float | None:
            return _measure(out, fields, column, entity_type="protective-covering", key=name, sheet=sheet)

        before, after = measure("Размер до, мм"), measure("Размер после, мм")
        size = "/".join(_text(fields, column) for column in ("Размер до, мм", "Размер после, мм")
                        if _text(fields, column))
        out.record("protective-covering", name, _payload(
            name=name, group=group, coveringKind=COVERING_GROUPS[group.casefold()],
            manufacturer=_text(fields, "Производитель"), series=_text(fields, "Серия"),
            size=f"{size} мм" if size else "", diameterBeforeMm=before, diameterAfterMm=after,
            shrinkRatio=_text(fields, "Коэф. усадки"), color=_text(fields, "Цвет"),
            adhesive=_text(fields, "Клеевой слой"), material=_text(fields, "Материал"),
            unit=_text(fields, "Ед. изм."),
        ), sheet)


def _js_replacement(python: str) -> str:
    return re.sub(r"\\(\d)", r"$\1", python)


def _connectors(out: _Collector) -> None:
    """Families of connector housings the ERP draws (база разъёмов) with their article patterns, so the editor can
    tell a part number the base knows from one it does not."""
    sheet = SHEETS["connectors"]
    titles = Counter(family["title"] for family in FAMILIES)
    for family in FAMILIES:
        sample = CATALOG_SAMPLES.get(family["id"], "")
        housing = housing_of(sample) if sample else None
        key = family["title"] if titles[family["title"]] == 1 else f"{family['title']} ({family['id']})"
        out.record("connector-family", key, _payload(
            familyId=family["id"], name=family["title"], sampleArticle=sample,
            contactCount=housing.get("n") if housing else None,
            pitchMm=family.get("pitch"), rows=housing.get("rows") if housing else family.get("rows"),
            latch=family.get("latch"),
            # Matched case-insensitively against the article normalised like `normalize_article`.
            pattern=family["pattern"], confidence=family.get("conf"), note=family.get("note"),
        ), sheet)
    for article, alias in ALIASES.items():
        out.record("connector-alias", article, _payload(
            pattern=f"^{re.escape(article)}$", replacement=alias,
        ), sheet)
    for pattern, replacement in ALIAS_RULES:
        out.record("connector-alias", pattern, _payload(pattern=pattern, replacement=_js_replacement(replacement)),
                   sheet)


def _snapshot(source_id: str, records: list[dict[str, Any]], diagnostics: list[dict[str, Any]],
              captured_utc: str) -> dict[str, Any]:
    records = sorted(records, key=lambda record: (record["entityType"], record["sourceKey"]))
    body = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    sha = hashlib.sha256(f"{CONTRACT_VERSION}\n{source_id}\n{body}".encode()).hexdigest()
    return {
        "snapshotId": str(uuid5(SNAPSHOT_NAMESPACE, sha)), "sourceId": source_id, "contractVersion": CONTRACT_VERSION,
        "capturedUtc": captured_utc, "sourceKind": SOURCE_KIND, "versionFingerprint": sha[:16], "sourceUri": None,
        "sha256": sha, "records": records, "diagnostics": diagnostics,
    }


def build_harness_catalog(tables: Mapping[str, Iterable[Mapping[str, Any]]], *, captured_utc: str) -> dict[str, Any]:
    """References (tab → record fields, as the references module stores them) → the editor's catalog snapshots."""
    terminals, wires, kab, coax, mat, connectors = (_Collector() for _ in range(6))
    _terminals(tables.get("ter", ()), terminals)
    _wires(tables.get("prov", ()), wires)
    diameters = _coax_cables(tables.get("kab_coax", ()), kab)
    _coax_terminations(tables.get("coax", ()), coax, diameters)
    _coverings(tables.get("mat", ()), mat)
    _connectors(connectors)
    snapshots = [
        _snapshot(WIRES, wires.records, wires.diagnostics, captured_utc),
        _snapshot(TERMINALS, terminals.records, terminals.diagnostics, captured_utc),
        _snapshot(COAX_CABLES, kab.records, kab.diagnostics, captured_utc),
        _snapshot(COAX_TERMINATIONS, coax.records, coax.diagnostics, captured_utc),
        # The universal base of the editor: wires, cables, coax cables and the coverings of its «Защита» tab.
        _snapshot(DATABASE, wires.records + kab.records + mat.records,
                  wires.diagnostics + kab.diagnostics + mat.diagnostics, captured_utc),
        _snapshot(CONNECTORS, connectors.records, connectors.diagnostics, captured_utc),
    ]
    return {
        "contractVersion": CONTRACT_VERSION, "capturedUtc": captured_utc, "snapshots": snapshots,
        "counts": {snapshot["sourceId"]: len(snapshot["records"]) for snapshot in snapshots},
    }


def reference_book(tables: Mapping[str, Iterable[Mapping[str, Any]]]) -> ReferenceBook:
    """The keys a drawing may reference, from the same records the editor's pickers offer: wire / cable marks
    (БД.ПРОВ, СПР.КАБ), terminal articles REEL and BAG (БД.ТЕР), covering names (БД.МАТ), coax strip keys (БД.КОАКС)."""
    keys: dict[str, set[str]] = {"wire": set(), "terminal": set(), "covering": set(), "strip": set()}
    kinds = {"wire": "wire", "cable": "wire", "coax-cable": "wire", "terminal": "terminal",
             "protective-covering": "covering", "coax-termination": "strip"}
    for snapshot in build_harness_catalog(tables, captured_utc="")["snapshots"]:
        for record in snapshot["records"]:
            kind = kinds.get(record["entityType"])
            if kind is None:
                continue
            keys[kind].add(record["sourceKey"])
            if kind == "terminal":
                keys[kind].update(str(record["payload"].get(name) or "") for name in ("reelArticle", "bagArticle"))
    return ReferenceBook(
        wires=frozenset(keys["wire"]), terminals=frozenset(keys["terminal"] - {""}),
        coverings=frozenset(keys["covering"]), strips=frozenset(keys["strip"]),
    )

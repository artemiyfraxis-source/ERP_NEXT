"""Project kitset card — SUPPLYMAP TcKitset.gs («Проекты», ключ erptechmap).

One card per calculation: project number, customer, every assembly (КБ) with its run and composition.
The calculation brings the structure, a person brings the numbers: «на 1 шт», «% брака», the purchased
name, the link and the chosen warehouse lot survive «Обновить из расчёта» (key КБ + артикул).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
import re
from typing import Any, Iterable
from uuid import UUID

from app.modules.requests.domain import unit_for_type

from .etk_parts import kit_article


# Брак по умолчанию для метражных позиций (провод, кабель) — 5 %: столько же зашито в BOM ERP («БРАК, ДОЛЯ» = 0,05).
# Штучным — 0 (технолог Василий, 04.10: «пока даём без брака, лучше пусть походят донабирать, чем возвраты делать»):
# запас технолог ставит руками 0, 1, 2, 3…
SCRAP_DEFAULT = Decimal("5")
PIECE_SCRAP_DEFAULT = Decimal("0")
# Счётные единицы: половинки штуки не бывает, партия округляется вверх до целого.
PIECE_UNITS = frozenset({"шт"})
_HOMOGLYPHS = str.maketrans("KCEOPAHTMBXY", "КСЕОРАНТМВХУ")
_PROJECT_RE = re.compile(
    r"^(?:[1-9]\d*(?:К[А-Я]?|МФ)(?:\.(?:[А-Я]{1,3}|[1-9]\d*))*\.?|П\d{4}-[1-9]\d*(?:\.[1-9]\d*)?)$"
)


class KitsetValidationError(ValueError):
    pass


class KitsetNotFound(KitsetValidationError):
    """«Карточка набора не найдена» — not a failure: the tile outlived the card (SUPPLYMAP `gone`)."""


def kit_number(value: object) -> Decimal:
    """`tcKitNum_`: «1,56», «1.56» and empty all read; anything else is 0."""
    text = re.sub(r"\s", "", str(value if value is not None else "")).replace(",", ".", 1)
    match = re.match(r"^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?", text, flags=re.I)
    if not match:
        return Decimal("0")
    try:
        number = Decimal(match[0])
    except InvalidOperation:
        return Decimal("0")
    return number if number.is_finite() else Decimal("0")


def batch_quantity(per: object, run: object, scrap_pct: object, unit: str) -> Decimal:
    """`tcKitBatchQty_`: расход на штуку × тираж × (1 + брак%), округление до сотых; штучное (`PIECE_UNITS`) —
    затем вверх до целого (Василий, 04.10: «округлять шт, половинки быть не может»). Метры остаются до сотых.
    Тот же расчёт на экране — `kitBatch` в ProjectsPage.tsx."""
    quantity = kit_number(per) * kit_number(run) * (1 + kit_number(scrap_pct) / 100)
    quantity = quantity.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if unit in PIECE_UNITS:
        return quantity.quantize(Decimal("1"), rounding=ROUND_CEILING)
    return quantity


def default_scrap(item_type: str) -> Decimal:
    """«% брака» новой строки набора: штучным 0, метражным `SCRAP_DEFAULT`. Сохранённое значение не трогается."""
    return PIECE_SCRAP_DEFAULT if unit_for_type(item_type) in PIECE_UNITS else SCRAP_DEFAULT


def project_number_valid(value: object) -> bool:
    """`projNoValid_` on the raw string: a Latin «K» is refused on purpose, so the number gets typed right."""
    return bool(_PROJECT_RE.match(re.sub(r"\s+", "", str(value or "").strip().upper())))


def project_key(value: object) -> str:
    """`projNoNorm_`: spaces out, upper case, Latin homoglyphs → Cyrillic, «2000К» ≡ «2000К.1»."""
    normalized = re.sub(r"\s+", "", str(value or "").strip().upper()).translate(_HOMOGLYPHS)
    return re.sub(r"^([1-9]\d*(?:К[А-Я]?|МФ)(?:\.[А-Я]{1,3})?)\.?$", r"\1.1", normalized)


def item_id(project: object, index: object) -> str:
    """`tcKitItemId_`: артикул изделия = номер проекта + индекс сборки («708К.4» + «КБ1» → «708К.4КБ1»)."""
    return str(project or "").strip() + str(index or "").strip().upper()


@dataclass(frozen=True)
class KitLot:
    """СКЛАД/ГРН/ПАКЕТ: the concrete warehouse lot a technologist picked for the line (owner 02.09.2026)."""

    lot_id: UUID | None
    warehouse: str
    grn: str
    package: str = ""


@dataclass(frozen=True)
class KitComponent:
    article: str
    type: str = ""
    name: str = ""
    link: str = ""
    per_unit: Decimal = Decimal("1")
    scrap_pct: Decimal | None = None  # не задан → `default_scrap(type)`: штучным 0, метражным 5 %
    lot: KitLot | None = None

    def __post_init__(self) -> None:
        if self.scrap_pct is None:
            object.__setattr__(self, "scrap_pct", default_scrap(self.type))

    @property
    def unit(self) -> str:
        """шт / м по типу — как в импорте из расчёта (`unit_for_type`)."""
        return unit_for_type(self.type)

    def batch(self, run: Decimal) -> Decimal:
        return batch_quantity(self.per_unit, run, self.scrap_pct, self.unit)


@dataclass(frozen=True)
class KitAssembly:
    index: str
    name: str
    run: Decimal
    components: tuple[KitComponent, ...] = ()
    # Строки листа КБn расчёта для генератора (`tcCalcOps_`: длины резки, лужение), прочитанные при заведении или
    # «Обновить из расчёта». None — ещё не читались (карточка старше), тогда генератор прочитает лист сам и запомнит.
    calc_ops: tuple[dict[str, str], ...] | None = None


@dataclass(frozen=True)
class CalcKitset:
    """What one calculation says: `tcReadCalcKitset_` result."""

    project: str
    customer: str
    calc_source: str
    assemblies: tuple[KitAssembly, ...]


@dataclass(frozen=True)
class KitDocument:
    """КД — attached to the assembly (КБ), not to a spec line: a drawing is issued for the product (TcKd.gs).

    A register row as on the SUPPLYMAP sheet «КД» (№ ПРОЕКТА, КБ, ИМЯ ФАЙЛА, ЗАГРУЖЕНО, КТО); the file itself
    lives in the ERP database (owner, 29.09: off Google Drive). `file_id` / `url` are the historical Drive
    reference of a row attached while КД lived on Drive — read-only; such a row has no bytes (`size` is None)
    until the one-time import (`import_kd_from_drive`) brings them over."""

    id: UUID
    project: str
    item_index: str
    file_name: str
    content_type: str
    size: int | None
    uploaded_at: datetime
    uploaded_by: str
    file_id: str = ""
    url: str = ""

    @property
    def stored(self) -> bool:
        """The bytes are in ERP (downloadable); otherwise only the archived Drive link is left."""
        return self.size is not None


@dataclass(frozen=True)
class Kitset:
    project: str
    customer: str
    calc_source: str
    assemblies: tuple[KitAssembly, ...]
    updated_at: datetime
    updated_by: str
    created_at: datetime
    version: int = 1

    @property
    def lines(self) -> int:
        """Rows of «Наборы»: an assembly without composition still takes one row."""
        return sum(max(1, len(assembly.components)) for assembly in self.assemblies)

    def assembly(self, index: str) -> KitAssembly | None:
        wanted = str(index or "").strip().upper()
        return next((item for item in self.assemblies if item.index.upper() == wanted), None)

    def with_edits(self, edits: Iterable[dict[str, Any]], *, who: str, now: datetime) -> tuple[Kitset, int]:
        """`tcKitsetSaveEdits`: [{index, art, per, scrap, name?, link?, lot?}] — structure is not touched.

        name/link/lot None = unchanged; lot {} or "" clears the binding (the person changed their mind).
        """
        wanted: dict[tuple[str, str], dict[str, Any]] = {}
        for edit in edits:
            key = (str(edit.get("index") or "").strip(), str(edit.get("art") or "").strip())
            wanted[key] = edit
        updated = 0
        assemblies = []
        for assembly in self.assemblies:
            components = []
            for component in assembly.components:
                edit = wanted.get((assembly.index, component.article))
                if edit is None:
                    components.append(component)
                    continue
                changed = replace(
                    component,
                    per_unit=kit_number(edit.get("per")),
                    scrap_pct=kit_number(edit.get("scrap")),
                )
                if edit.get("name") is not None:
                    changed = replace(changed, name=str(edit["name"]))
                if edit.get("link") is not None:
                    changed = replace(changed, link=str(edit["link"]))
                if "lot" in edit and edit["lot"] is not None:
                    changed = replace(changed, lot=_lot(edit["lot"]))
                components.append(changed)
                updated += 1
            assemblies.append(replace(assembly, components=tuple(components)))
        return replace(
            self, assemblies=tuple(assemblies), updated_at=now, updated_by=who, version=self.version + 1,
        ), updated


def _lot(value: Any) -> KitLot | None:
    if not value:
        return None
    if not isinstance(value, dict):
        raise KitsetValidationError("Партия склада передана не в том виде")
    grn = str(value.get("grn") or "").strip()
    if not grn:
        return None
    lot_id = value.get("lot_id")
    return KitLot(
        lot_id=UUID(str(lot_id)) if lot_id else None,
        warehouse=str(value.get("warehouse") or "").strip(),
        grn=grn,
        package=str(value.get("package") or "").strip(),
    )


def write_from_calc(
    data: CalcKitset, current: Kitset | None, *, who: str, now: datetime,
) -> Kitset:
    """`tcKitsetWrite_(keepEdits=true)`: rows rebuilt from the calculation, a person's numbers kept.

    Kept per КБ + артикул: на 1 шт и % брака always; наименование and ссылка when the person filled them
    (введённое сильнее расчёта); the lot binding (расчёт про склад ничего не знает).
    """
    kept: dict[tuple[str, str], KitComponent] = {}
    if current is not None:
        for assembly in current.assemblies:
            for component in assembly.components:
                kept[(assembly.index, component.article)] = component
    assemblies = []
    for assembly in data.assemblies:
        components = []
        for component in assembly.components:
            keep = kept.get((assembly.index, component.article))
            if keep is None:
                components.append(component)
                continue
            components.append(replace(
                component,
                per_unit=keep.per_unit,
                scrap_pct=keep.scrap_pct,
                name=keep.name or component.name,
                link=keep.link or component.link,
                lot=keep.lot,
            ))
        ops = assembly.calc_ops
        if ops is None and current is not None:
            previous = current.assembly(assembly.index)
            ops = previous.calc_ops if previous is not None else None
        assemblies.append(replace(assembly, components=tuple(components), calc_ops=ops))
    return Kitset(
        project=data.project,
        customer=data.customer,
        calc_source=data.calc_source,
        assemblies=tuple(assemblies),
        updated_at=now,
        updated_by=who,
        created_at=current.created_at if current is not None else now,
        version=current.version + 1 if current is not None else 1,
    )


def manual_kitset(
    *, project: str, customer: str, assemblies: Iterable[dict[str, Any]], who: str, now: datetime,
    current: Kitset | None = None,
) -> Kitset:
    """A card without a calculation file: the same rows typed in (index, name, run, composition)."""
    parsed = []
    for item in assemblies:
        index = str(item.get("index") or "").strip().upper()
        if not index:
            continue
        parsed.append(KitAssembly(
            index=index,
            name=str(item.get("name") or "").strip(),
            run=kit_number(item.get("run")),
            components=tuple(
                KitComponent(
                    article=str(component.get("art") or "").strip(),
                    type=str(component.get("type") or "").strip(),
                    name=str(component.get("name") or "").strip(),
                    link=str(component.get("link") or "").strip(),
                    per_unit=kit_number(component.get("per")) or Decimal("1"),
                    scrap_pct=(
                        kit_number(component.get("scrap")) if component.get("scrap") not in (None, "") else None
                    ),
                )
                for component in item.get("components") or []
                if str(component.get("art") or "").strip()
            ),
        ))
    if not parsed:
        raise KitsetValidationError("Нужна хотя бы одна сборка с индексом")
    return write_from_calc(
        CalcKitset(project=project, customer=customer, calc_source="", assemblies=tuple(parsed)),
        current, who=who, now=now,
    )


# ── Сортировка списка (kitSortList_) ──────────────────────────────────────


def sort_kitsets(rows: list[dict[str, Any]], by: str, statuses: list[str]) -> list[dict[str, Any]]:
    """Portal list order: fresh first (default), old first, by status, by number, by customer."""

    def updated(row: dict[str, Any]) -> float:
        value = row.get("updated_at")
        return value.timestamp() if isinstance(value, datetime) else 0.0

    def rank(row: dict[str, Any]) -> int:
        return statuses.index(row["status"]) if row.get("status") in statuses else 999

    if by == "upd_asc":
        return sorted(rows, key=updated)
    if by == "status":
        return sorted(rows, key=lambda row: (rank(row), -updated(row)))
    if by == "no":
        return sorted(rows, key=lambda row: _natural(row["project"]))
    if by == "customer":
        return sorted(rows, key=lambda row: (str(row.get("customer") or "").casefold(), -updated(row)))
    return sorted(rows, key=updated, reverse=True)


def _natural(value: str) -> list:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", str(value))]


# ── Снабжение проекта (tcKitsetSupply_) ───────────────────────────────────


@dataclass(frozen=True)
class SupplyRow:
    """One row of the purchase chain tied to the project: where it is and in which status."""

    article: str
    where: str  # «Запрос» · «Закупка» · «Приёмка»
    status: str
    source_id: str = ""
    received: bool = False
    # A row without an article (a manual request) is matched by its name within the project.
    name: str = ""
    # What the row still brings to the project (a request not yet in procurement, the live procurement line not yet
    # accepted): «В закупке» of the position. Accepted goods are counted on the shelf or in the kit instead.
    open_quantity: Decimal = Decimal("0")


@dataclass(frozen=True)
class SupplyStock:
    """A quantity of the item the project already has: a free warehouse lot, or the project's kit lines."""

    article: str
    name: str
    quantity: Decimal


@dataclass(frozen=True)
class SupplyLine:
    article: str
    name: str
    kbs: tuple[str, ...]
    need: Decimal
    grn: str
    stage: str
    received: bool
    rows: tuple[SupplyRow, ...] = field(default_factory=tuple)
    item_type: str = ""  # of the first spec line with this article: the unit follows it (шт / м)
    ordered: Decimal = Decimal("0")  # «В закупке»: open requests and procurement lines of the project
    free: Decimal = Decimal("0")  # «Свободно»: free stock (with the project's own reserves) under this item
    kitted: Decimal = Decimal("0")  # reserved or issued to the project's kits

    @property
    def short(self) -> Decimal:
        """«Не хватает»: need − free stock − in procurement − already in the kits, never below zero."""
        return max(self.need - self.free - self.ordered - self.kitted, Decimal("0"))


_SUPPLY_RANK = {"Запрос": 1, "Закупка": 2, "Приёмка": 3}


def _article_key(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _supply_match(lines: dict[str, dict[str, Any]], article: object, name: object) -> str | None:
    """The spec position an item belongs to. With an article: the КД article, then the article actually used
    (`kit_article`) — a description is never taken for an article. Without one (a manual request): its name
    against the position's article or name."""
    art = _article_key(article)
    if art:
        if art in lines:
            return art
        return next((key for key, line in lines.items() if art in line["used"]), None)
    label = _article_key(name)
    if not label:
        return None
    if label in lines:
        return label
    return next((key for key, line in lines.items() if label in line["names"] or label in line["used"]), None)


def supply_lines(
    kitset: Kitset, rows: Iterable[SupplyRow], stock: Iterable[SupplyStock] = (),
    kitted: Iterable[SupplyStock] = (),
) -> tuple[SupplyLine, ...]:
    """Positions of the spec against purchasing, keyed by the КД article (the one key calc and purchase share).

    A row replaced in the calc (`kit_article`: the wire in «Наименование» differs from the КД one) is bought as the
    item actually used, so purchases under either article count for the position. A row without an article counts
    by its name. Each row, lot and kit line counts for one position only.
    Stage = the LATEST row of the position (one bought, another still requested → it is on its way);
    no rows → «со склада» if the spec has a ГРН or the line is already kitted (reserved/issued), else «не заведено».
    """
    lines: dict[str, dict[str, Any]] = {}
    for assembly in kitset.assemblies:
        for component in assembly.components:
            key = _article_key(component.article)
            if not key:
                continue
            line = lines.get(key)
            if line is None:
                line = lines[key] = {
                    "article": component.article, "name": component.name, "type": component.type, "kbs": [],
                    "need": Decimal("0"), "grn": "", "rows": (), "used": set(), "names": set(),
                    "free": Decimal("0"), "kitted": Decimal("0"),
                }
            used = _article_key(kit_article(component.article, component.name)["art"])
            if used != key:
                line["used"].add(used)
            if _article_key(component.name):
                line["names"].add(_article_key(component.name))
            if assembly.index and assembly.index not in line["kbs"]:
                line["kbs"].append(assembly.index)
            line["need"] = (line["need"] + component.batch(assembly.run)).quantize(Decimal("0.01"))
            if component.lot and component.lot.grn and not line["grn"]:
                line["grn"] = component.lot.grn
    for row in rows:
        key = _supply_match(lines, row.article, row.name)
        if key is not None:
            lines[key]["rows"] += (row,)
    for items, field_name in ((stock, "free"), (kitted, "kitted")):
        for item in items:
            key = _supply_match(lines, item.article, item.name)
            if key is not None and item.quantity > 0:
                lines[key][field_name] += item.quantity
    result = []
    for line in lines.values():
        found = line["rows"]
        best = None
        for row in found:
            if best is None or _SUPPLY_RANK[row.where] > _SUPPLY_RANK[best.where]:
                best = row
        if best is not None:
            stage = best.where + (f" · {best.status}" if best.status else "")
        else:
            stage = "со склада" if line["grn"] or line["kitted"] > 0 else "не заведено"
        result.append(SupplyLine(
            article=line["article"], name=line["name"], kbs=tuple(line["kbs"]), need=line["need"],
            grn=line["grn"], stage=stage, received=any(row.where == "Приёмка" and row.received for row in found),
            rows=found, item_type=line["type"],
            ordered=sum((row.open_quantity for row in found), Decimal("0")),
            free=line["free"], kitted=line["kitted"],
        ))
    return tuple(result)


# ── Отправить на комплектацию (issueKitsetTasks_) ─────────────────────────


@dataclass(frozen=True)
class KitIssueItem:
    name: str
    article: str
    need: Decimal
    lot: KitLot | None


@dataclass(frozen=True)
class KitIssueTask:
    index: str
    purpose: str
    items: tuple[KitIssueItem, ...]
    waiting: tuple[str, ...]


def issue_tasks(kitset: Kitset, available_lots: set[tuple[str, str]]) -> tuple[KitIssueTask, ...]:
    """One fulfillment request per assembly with composition (owner 01.09.2026): the kit of one КБ goes to the
    shop without waiting for the rest. Name = the purchased name, else the КД article. A line without a lot,
    or with a lot no longer on the shelf, still goes and waits for delivery — a missing balance is «ждём
    поставки», not a refusal. `available_lots` = {(grn, package)} of lots with a balance."""
    tasks = []
    for assembly in kitset.assemblies:
        if not assembly.components:
            continue
        items, waiting = [], []
        for component in assembly.components:
            name = component.name.strip() or component.article.strip()
            need = component.batch(assembly.run)
            if not name or need <= 0:
                continue
            lot = component.lot
            items.append(KitIssueItem(name=name, article=component.article.strip(), need=need, lot=lot))
            if lot is None:
                waiting.append(f"{name}: ГРН не выбран ({_display(need)})")
            elif (lot.grn, lot.package) not in available_lots:
                waiting.append(f"{name}: ГРН {lot.grn} не найден на складе ({_display(need)})")
        purpose = f"{kitset.project} · {assembly.index}" + (f" · {assembly.name}" if assembly.name else "")
        tasks.append(KitIssueTask(index=assembly.index, purpose=purpose, items=tuple(items), waiting=tuple(waiting)))
    return tuple(tasks)


def _display(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text

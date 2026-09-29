"""Project kitset card — SUPPLYMAP TcKitset.gs («Проекты», ключ erptechmap).

One card per calculation: project number, customer, every assembly (КБ) with its run and composition.
The calculation brings the structure, a person brings the numbers: «на 1 шт», «% брака», the purchased
name, the link and the chosen warehouse lot survive «Обновить из расчёта» (key КБ + артикул).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
import re
from typing import Any, Iterable
from uuid import UUID


# Брак по умолчанию — 5 %: столько же зашито в BOM ERP («БРАК, ДОЛЯ» = 0,05), набор не должен с ним расходиться.
SCRAP_DEFAULT = Decimal("5")
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


def batch_quantity(per: object, run: object, scrap_pct: object) -> Decimal:
    """`tcKitBatchQty_`: расход на штуку × тираж × (1 + брак%), округление до сотых."""
    quantity = kit_number(per) * kit_number(run) * (1 + kit_number(scrap_pct) / 100)
    return quantity.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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
    scrap_pct: Decimal = SCRAP_DEFAULT
    lot: KitLot | None = None

    def batch(self, run: Decimal) -> Decimal:
        return batch_quantity(self.per_unit, run, self.scrap_pct)


@dataclass(frozen=True)
class KitAssembly:
    index: str
    name: str
    run: Decimal
    components: tuple[KitComponent, ...] = ()


@dataclass(frozen=True)
class CalcKitset:
    """What one calculation says: `tcReadCalcKitset_` result."""

    project: str
    customer: str
    calc_source: str
    assemblies: tuple[KitAssembly, ...]


@dataclass(frozen=True)
class KitDocument:
    """КД — attached to the assembly (КБ), not to a spec line: a drawing is issued for the product (TcKd.gs)."""

    id: UUID
    project: str
    item_index: str
    file_name: str
    content_type: str
    size: int
    uploaded_at: datetime
    uploaded_by: str


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
        assemblies.append(replace(assembly, components=tuple(components)))
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
                        kit_number(component.get("scrap"))
                        if component.get("scrap") not in (None, "") else SCRAP_DEFAULT
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


_SUPPLY_RANK = {"Запрос": 1, "Закупка": 2, "Приёмка": 3}


def _article_key(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def supply_lines(kitset: Kitset, rows: Iterable[SupplyRow]) -> tuple[SupplyLine, ...]:
    """Positions of the spec against purchasing, keyed by the КД article (the one key calc and purchase share).

    Stage = the LATEST row of the position (one bought, another still requested → it is on its way);
    no rows → «со склада» if the spec has a ГРН, else «не заведено».
    """
    by_article: dict[str, list[SupplyRow]] = {}
    for row in rows:
        key = _article_key(row.article)
        if key:
            by_article.setdefault(key, []).append(row)
    lines: dict[str, dict[str, Any]] = {}
    for assembly in kitset.assemblies:
        for component in assembly.components:
            key = _article_key(component.article)
            if not key:
                continue
            line = lines.get(key)
            if line is None:
                line = lines[key] = {
                    "article": component.article, "name": component.name, "kbs": [],
                    "need": Decimal("0"), "grn": "", "rows": tuple(by_article.get(key, ())),
                }
            if assembly.index and assembly.index not in line["kbs"]:
                line["kbs"].append(assembly.index)
            line["need"] = (line["need"] + component.batch(assembly.run)).quantize(Decimal("0.01"))
            if component.lot and component.lot.grn and not line["grn"]:
                line["grn"] = component.lot.grn
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
            stage = "со склада" if line["grn"] else "не заведено"
        result.append(SupplyLine(
            article=line["article"], name=line["name"], kbs=tuple(line["kbs"]), need=line["need"],
            grn=line["grn"], stage=stage, received=any(row.where == "Приёмка" and row.received for row in found),
            rows=found,
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

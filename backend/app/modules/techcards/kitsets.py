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
from urllib.parse import urlsplit
from uuid import UUID

from app.modules.requests.domain import unit_for_type

from .etk_parts import kit_article
from .harness.schema import HARNESS_CONTENT_TYPE, HARNESS_UNLINKED_CONTENT_TYPE


# Брак по умолчанию для метражных позиций (провод, кабель) — 5 %: столько же зашито в BOM ERP («БРАК, ДОЛЯ» = 0,05).
# Штучным — 0 (технолог Василий, 04.10: «пока даём без брака, лучше пусть походят донабирать, чем возвраты делать»):
# запас технолог ставит руками 0, 1, 2, 3…
SCRAP_DEFAULT = Decimal("5")
PIECE_SCRAP_DEFAULT = Decimal("0")
# «Выдаём вровень» (владелец, 10.10.2026): запас на брак пока НЕ добавляется к «на партию» — ни в выдачу, ни в резерв
# ГРН, ни в потребность снабжения; нехватку довыдают отдельной заявкой («Довыдача»). «% брака» строк хранится и
# правится как прежде — вернуть запас = True здесь и `KIT_SCRAP_APPLIED` в ProjectsPage.tsx.
SCRAP_APPLIED = False
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
    # Строку добавил технолог в генераторе (владелец 21.10.2026), а не расчёт: «Обновить из расчёта» её не стирает,
    # убрать её можно тем же путём, каким добавили.
    manual: bool = False

    def __post_init__(self) -> None:
        if self.scrap_pct is None:
            object.__setattr__(self, "scrap_pct", default_scrap(self.type))

    @property
    def unit(self) -> str:
        """шт / м по типу — как в импорте из расчёта (`unit_for_type`)."""
        return unit_for_type(self.type)

    def batch(self, run: Decimal) -> Decimal:
        """«На партию»: расход × тираж, без запаса на брак, пока `SCRAP_APPLIED` выключен."""
        return batch_quantity(self.per_unit, run, self.scrap_pct if SCRAP_APPLIED else Decimal("0"), self.unit)


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


DOCUMENT_KINDS = {HARNESS_CONTENT_TYPE: "harness", HARNESS_UNLINKED_CONTENT_TYPE: "harness_unlinked"}


def document_kind(document: KitDocument) -> str:
    """«harness» — the harness drawing edited in ERP; «harness_unlinked» — such a drawing unlinked from its position;
    «file» — an attached file."""
    return DOCUMENT_KINDS.get((document.content_type or "").split(";")[0].strip(), "file")


def document_json(document: KitDocument) -> dict[str, Any]:
    """A row of the «КД» register as `tcKdByAssembly_` hands it to the card. `stored` — the bytes are in ERP (the
    card links the download); otherwise `url` is the archived Drive link of a row from before the move, read-only.
    `kind` — «harness» for the harness drawing (opened in the drawing editor), «file» otherwise."""
    return {
        "id": str(document.id), "index": document.item_index, "name": document.file_name,
        "size": document.size, "stored": document.stored, "url": document.url or None,
        "uploaded_at": document.uploaded_at.isoformat(), "who": document.uploaded_by,
        "kind": document_kind(document),
    }


FOLDER_URL_MAX = 1024


def clean_folder_url(value: object) -> str:
    """Ссылка на папку проекта: пусто — убрать; иначе http(s)-адрес не длиннее FOLDER_URL_MAX."""
    url = str(value or "").strip()
    if not url:
        return ""
    if len(url) > FOLDER_URL_MAX:
        raise KitsetValidationError(f"Ссылка длиннее {FOLDER_URL_MAX} символов.")
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc or any(ch.isspace() for ch in url):
        raise KitsetValidationError("Введите ссылку, начинающуюся с http:// или https://")
    return url


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
    folder_url: str = ""

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

    def with_component(self, index: str, component: KitComponent, *, who: str, now: datetime) -> Kitset:
        """Технолог добавил комплектующее в состав КБ: строка встаёт в конец (позиции строк расчёта не сдвигаются) и
        дальше идёт как любая строка набора — в «Проекты», резерв ГРН и заявку на комплектацию."""
        assembly = self._assembly_or_fail(index)
        article = component.article.strip()
        if not article:
            raise KitsetValidationError("Укажите артикул комплектующего.")
        if component.per_unit <= 0:
            raise KitsetValidationError("Количество на изделие должно быть больше нуля.")
        if any(item.article == article for item in assembly.components):
            raise KitsetValidationError(f"В {assembly.index} уже есть «{article}» — его количество правится в «Спецификации» проекта.")
        added = replace(component, article=article, name=component.name.strip(), type=component.type.strip(), manual=True)
        return self._with_assembly(replace(assembly, components=(*assembly.components, added)), who=who, now=now)

    def without_component(self, index: str, article: str, *, who: str, now: datetime) -> Kitset:
        """Убрать строку, добавленную технологом; строка расчёта убирается только в самом расчёте."""
        assembly = self._assembly_or_fail(index)
        wanted = str(article or "").strip()
        found = next((item for item in assembly.components if item.article == wanted), None)
        if found is None:
            raise KitsetValidationError(f"В {assembly.index} нет «{wanted}».")
        if not found.manual:
            raise KitsetValidationError(f"«{wanted}» пришёл из расчёта — уберите его в расчёте и обновите проект.")
        rest = tuple(item for item in assembly.components if item is not found)
        return self._with_assembly(replace(assembly, components=rest), who=who, now=now)

    def _assembly_or_fail(self, index: str) -> KitAssembly:
        assembly = self.assembly(index)
        if assembly is None:
            raise KitsetValidationError(f"В проекте «{self.project}» нет сборки «{index}».")
        return assembly

    def _with_assembly(self, changed: KitAssembly, *, who: str, now: datetime) -> Kitset:
        assemblies = tuple(changed if item.index == changed.index else item for item in self.assemblies)
        return replace(self, assemblies=assemblies, updated_at=now, updated_by=who, version=self.version + 1)


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
    (введённое сильнее расчёта); the lot binding (расчёт про склад ничего не знает). Rows the technologist added
    (`manual`) stay at the end of their КБ; once the calculation lists the same article, its row takes over.
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
        previous = current.assembly(assembly.index) if current is not None else None
        listed = {component.article for component in components}
        components += [item for item in (previous.components if previous else ()) if item.manual and item.article not in listed]
        ops = assembly.calc_ops
        if ops is None and previous is not None:
            ops = previous.calc_ops
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
        folder_url=current.folder_url if current is not None else "",
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
    """A quantity of the item the project already has: a free warehouse lot, the project's kit lines, or what its
    procurement marked «Есть на складе, без ГРН». `off_book` — a kit line issued «есть, не заведено» (no lot): it
    takes from that same unbooked stock, so it uses up the procurement's «без ГРН» instead of adding to it."""

    article: str
    name: str
    quantity: Decimal
    off_book: bool = False
    issued: Decimal = Decimal("0")  # a kit line: of `quantity`, issued (less what came back good) — the rest is reserved


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
    issued: Decimal = Decimal("0")  # of `kitted`, already issued from the warehouse (less what came back good)
    # «Без ГРН»: procurement covered it «Есть на складе, без ГРН» — on the shelf, in no lot, reserved nowhere; less
    # what the project's kits already issued «есть, не заведено» (the same unbooked goods, never counted twice).
    off_book: Decimal = Decimal("0")
    link: str = ""  # the first supplier link of the position in the spec
    # Where the shown «Наименование» comes from (`app.workflows.item_names`): ref | stock | spec | site | "" (unknown),
    # and the tooltip that says so (or why no name was found).
    name_source: str = ""
    name_hint: str = ""

    @property
    def short(self) -> Decimal:
        """«Не хватает»: need − free stock − in procurement − already in the kits − on the shelf without a ГРН, never
        below zero."""
        return max(self.need - self.free - self.ordered - self.kitted - self.off_book, Decimal("0"))


@dataclass(frozen=True)
class SupplyState:
    """One word of «Стадия» on «Снабжение», its tone and a tooltip with the numbers behind it."""

    key: str  # short | issued | kitting | procurement | received | outside | stock | none
    label: str
    tone: str  # ok | warn | danger | info | neutral
    hint: str


_PROCUREMENT_PLACES = ("Закупка", "Приёмка")


def supply_state(line: SupplyLine) -> SupplyState:
    """The position's state in one word (owner, 12.10.2026: «если заведено без кнопки в закупку — значит это куплено вне
    системы»), first that holds:

    - «Не хватает N» — need − free − without ГРН − in procurement − in the kits is still above zero (the checkbox);
    - «Выдано в производство» — the project's kits already issued the whole need from the warehouse (owner 17.10.2026:
      «почему пишется в комплектации, если оно уже выдано»);
    - «В комплектации» — the project's kits hold the whole need, part of it still reserved on the shelf;
    - «В закупке» — an ERP procurement line of the position is still open;
    - «Принято на склад» — procurement brought it and receiving accepted it;
    - «Куплено вне системы» — only purchase requests, none handed to procurement («В закупку» never pressed);
    - «Со склада» — free stock (or the stock procurement marked «без ГРН») covers it, no purchase in ERP.
    """
    def n(value: Decimal) -> str:
        return _display(value).replace(".", ",")

    numbers = (
        f"Нужно {n(line.need)} · свободно {n(line.free)} · без ГРН {n(line.off_book)} · в закупке {n(line.ordered)}"
        f" · в комплектации {n(line.kitted)}"
    )
    if line.need <= 0:
        return SupplyState("none", "Нет потребности", "neutral", numbers)
    if line.short > 0:
        return SupplyState(
            "short", f"Не хватает {n(line.short)}", "danger", f"{numbers} → не хватает {n(line.short)}",
        )
    if line.issued >= line.need:
        return SupplyState(
            "issued", "Выдано в производство", "ok",
            f"Выдано со склада по комплектации проекта: {n(line.issued)} из {n(line.need)} · {numbers}",
        )
    if line.kitted >= line.need:
        return SupplyState(
            "kitting", "В комплектации", "ok",
            f"По комплектации проекта: выдано {n(line.issued)}, в резерве {n(line.kitted - line.issued)} из {n(line.need)}"
            f" · {numbers}",
        )
    procurement = [row for row in line.rows if row.where in _PROCUREMENT_PLACES]
    rows_text = "; ".join(f"{row.source_id} · {row.where} · {row.status}" for row in line.rows if row.source_id)
    open_lines = [row for row in procurement if not row.received and not row.status.endswith("без ГРН")]
    if open_lines:
        return SupplyState("procurement", "В закупке", "warn", f"В закупке {n(line.ordered)}: {rows_text} · {numbers}")
    if any(row.received for row in procurement):
        return SupplyState("received", "Принято на склад", "ok", f"Принято по закупке: {rows_text} · {numbers}")
    if line.rows and not procurement:
        return SupplyState(
            "outside", "Куплено вне системы", "neutral",
            f"Заявка без «В закупку» — закупки в ERP нет: {rows_text} · {numbers}",
        )
    if line.off_book > 0 and line.free < line.need:
        return SupplyState(
            "stock", "Со склада", "info", f"Закупка отметила «Есть на складе, без ГРН»: {n(line.off_book)} · {numbers}",
        )
    return SupplyState("stock", "Со склада", "info", f"Свободно на складе {n(line.free)} из нужных {n(line.need)} · {numbers}")


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
    kitted: Iterable[SupplyStock] = (), off_book: Iterable[SupplyStock] = (), absent: Iterable[SupplyStock] = (),
) -> tuple[SupplyLine, ...]:
    """Positions of the spec against purchasing, keyed by the КД article (the one key calc and purchase share).

    A row replaced in the calc (`kit_article`: the wire in «Наименование» differs from the КД one) is bought as the
    item actually used, so purchases under either article count for the position. A row without an article counts
    by its name. Each row, lot and kit line counts for one position only. `off_book` — procurement covers «Есть на
    складе, без ГРН» (no lot holds them, so they are in no `stock`): they count for the position less the kit lines
    issued «есть, не заведено» of it; bound to a ГРН, a cover leaves them and is counted with its lot instead.
    `absent` — kit lines the storekeeper marked «нет на складе» (owner 12.10.2026): the shelf does not have what the
    books show, so «Свободно» of the position drops by them (never below zero) and the need shows as «Не хватает».
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
                    "link": component.link.strip(),
                    "need": Decimal("0"), "grn": "", "rows": (), "used": set(), "names": set(),
                    "free": Decimal("0"), "kitted": Decimal("0"), "off_book": Decimal("0"),
                    "kitted_off_book": Decimal("0"), "absent": Decimal("0"), "issued": Decimal("0"),
                }
            used = _article_key(kit_article(component.article, component.name)["art"])
            if used != key:
                line["used"].add(used)
            if _article_key(component.name):
                line["names"].add(_article_key(component.name))
            if not line["link"] and component.link.strip():
                line["link"] = component.link.strip()
            if assembly.index and assembly.index not in line["kbs"]:
                line["kbs"].append(assembly.index)
            line["need"] = (line["need"] + component.batch(assembly.run)).quantize(Decimal("0.01"))
            if component.lot and component.lot.grn and not line["grn"]:
                line["grn"] = component.lot.grn
    for row in rows:
        key = _supply_match(lines, row.article, row.name)
        if key is not None:
            lines[key]["rows"] += (row,)
    for items, field_name in ((stock, "free"), (kitted, "kitted"), (off_book, "off_book"), (absent, "absent")):
        for item in items:
            key = _supply_match(lines, item.article, item.name)
            if key is not None and item.quantity > 0:
                lines[key][field_name] += item.quantity
                if field_name == "kitted" and item.off_book:
                    lines[key]["kitted_off_book"] += item.quantity
                if field_name == "kitted":
                    lines[key]["issued"] += min(item.issued, item.quantity)
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
            free=max(line["free"] - line["absent"], Decimal("0")), kitted=line["kitted"], issued=line["issued"],
            off_book=max(line["off_book"] - line["kitted_off_book"], Decimal("0")), link=line["link"],
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

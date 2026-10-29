"""Project kitset card across modules — SUPPLYMAP TcKitset.gs, TcKd.gs, Issue.gs `portalIssueFromKitset`.

The kitset itself lives in Techcards. Creating it also registers the project for the production plan
(`tcKitsetSyncErp_`), queues finished goods per КБ (`shipQueueForProject_`, ADR-0016), remembers the shipping
addressee and the deadline. None of those may fail the creation: the card is needed for composition and
techcards, the side effects report their own errors (`erpError`, `shipError`, …) as SUPPLYMAP does.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
import logging
from typing import Any, Iterable, Protocol
from uuid import UUID, uuid4

from app.modules.fulfillment.application import (
    BindLotCommand,
    CreateFulfillmentCommand,
    DeleteFulfillmentCommand,
    UnitOfWork as FulfillmentUnitOfWork,
    bind_lot,
    create_fulfillment,
    delete_fulfillment,
    free_for_project,
    kit_key,
    kit_requests_by_kb,
    project_reserve_by_lot,
)
from app.modules.fulfillment.domain import (
    DuplicateKitRequest, FulfillmentLine, FulfillmentRequest, FulfillmentStatus, FulfillmentValidationError,
)
from app.modules.procurement.application import (
    ConcurrentProcurementUpdate,
    RemoveRequestLinesCommand,
    RequestLines,
    UnitOfWork as ProcurementUnitOfWork,
    remove_untouched_request_lines,
)
from app.modules.procurement.domain import ProcurementLine
from app.workflows.item_names import ReferenceNames, StockNames, reference_records, resolve_name
from app.modules.production.domain import ProductionTask
from app.modules.production.traceability import ProductionBox
from app.modules.production.plan import (
    kit_stage_key,
    KIT_STAGES,
    MARKER_FIELDS,
    PLAN_KINDS,
    PLAN_MARK_FLAGS,
    PLAN_STAGE_AUTO,
    TC_REVIEW_VALUES,
    legacy_stage_refusal,
    ActiveBlocker,
    KitStageNow,
    PlanEvent,
    PlanProjectSnapshot,
    PROJECT_TERMINALS,
    StagePlan,
    kit_label,
    project_terminal,
    ProjectStage,
    normalize_project_number,
    plan_stage_change,
    project_from_purpose,
    project_stage,
    project_stage_now,
    stage_plan_message,
    summarize_journal,
    summarize_kit_marks,
)
from app.modules.production.plan_application import (
    AddPlanEventCommand,
    ChangeStageCommand,
    DeleteProjectPlanEventsCommand,
    PlanUnitOfWork,
    ReplacePlanProjectsCommand,
    add_plan_event,
    change_stage,
    delete_project_plan_events,
    replace_plan_projects,
)
from app.modules.requests.application import (
    CalcSheetError,
    CalcSpreadsheetReader,
    CreateRequestsCommand,
    UnitOfWork as RequestsUnitOfWork,
    create_requests,
    extract_from_calc,
)
from app.modules.requests.domain import (
    DEFAULT_CATEGORY,
    DELIVERY_POINTS,
    CalcImport,
    CalcImportItem,
    Request,
    RequestDraft,
    RequestStatus,
    ValidationError as RequestValidationError,
    build_import_drafts,
    parse_calc_number,
    project_reference_valid,
    spreadsheet_id_from_url,
    unit_for_type,
)
from app.modules.shipping.application import (
    DropProjectCommand,
    QueueProjectCommand,
    SetProjectAddresseeCommand,
    UnitOfWork as ShippingUnitOfWork,
    drop_project,
    queue_project,
    set_project_addressee,
)
from app.modules.shipping.domain import FinishedGood, ProjectAssembly
from app.modules.techcards.application import UnitOfWork as TechcardsUnitOfWork
from app.workflows import stage_cascade
from app.modules.techcards.etk_parts import kit_article
from app.modules.techcards.kitset_calc import PROJECT_SHEET, SPEC_SHEET, parse_calc_sheets
from app.modules.techcards.kitsets import (
    CalcKitset,
    KitAssembly,
    KitDocument,
    Kitset,
    KitsetNotFound,
    KitsetValidationError,
    SupplyLine,
    SupplyRow,
    SupplyStock,
    issue_tasks,
    item_id,
    project_key,
    project_number_valid,
    supply_lines,
    write_from_calc,
)
from app.modules.techcards.project_cards import ProjectTechcard, TechcardStatus
from app.shared.events import AuditEvent
from app.shared.file_types import stored_content_type
from app.shared.identity import Actor
from app.workflows.production_plan import issued_runs, kit_stage_facts, project_kits


# Терминальные статусы реестра перекрывают вычисленный этап — решение человека (ERP_PROJ_TERMINALS_).
TERMINAL_STATUSES = PROJECT_TERMINALS
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class LoadKitsetCommand:
    data: CalcKitset
    number: str
    deadline: date | None
    ship: dict[str, str]
    actor: Actor
    now: datetime


@dataclass
class LoadKitsetResult:
    project: str
    assemblies: int
    lines: int
    erp: dict[str, Any] | None = None
    erp_error: str = ""
    ship: dict[str, Any] | None = None
    ship_error: str = ""
    ship_to_error: str = ""
    deadline_error: str = ""


def _audit(uow, project: str, event_type: str, actor: Actor, now: datetime, details: dict[str, str]) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=actor.id, event_type=event_type, actor=actor, occurred_at=now,
        details={"project": project, **details},
    ))


_log = logging.getLogger(__name__)


class CalcSheetsReader(Protocol):
    """Google Sheets port: whole named sheets as display values plus the file title (see `google_sheets`)."""

    def read_sheets(self, spreadsheet_id: str, names: tuple[str, ...]) -> tuple[str, dict[str, list[list[str]]]]: ...


def read_calc_by_link(url: str, reader: Callable[[], CalcSheetsReader]):
    """`tcReadCalcKitset_(calcUrl)`: the calculation by its Google Sheets link — ЗКЗ and СПЯ as the sheet shows
    them; the file title refines the project number; the link is kept as the card's «РАСЧЁТ»."""
    link = str(url or "").strip()
    spreadsheet_id = spreadsheet_id_from_url(link)
    if not spreadsheet_id:
        raise KitsetValidationError(f"Не похоже на ссылку Google Таблицы: {link}")
    title, sheets = reader().read_sheets(spreadsheet_id, (PROJECT_SHEET, SPEC_SHEET))
    data = parse_calc_sheets(sheets, title, link)
    ops = read_calc_ops(link, [assembly.index for assembly in data.assemblies], reader)
    if ops is None:
        return data
    return replace(data, assemblies=tuple(
        replace(assembly, calc_ops=tuple(ops.get(assembly.index, []))) for assembly in data.assemblies
    ))


def read_calc_ops(
    calc_source: str, indexes: list[str], reader: Callable[[], CalcSheetsReader],
) -> dict[str, list[dict[str, str]]] | None:
    """Листы КБn расчёта одним чтением (`tcCalcOps_` на все сборки) → {КБn: строки}; лист, которого нет, — пусто.
    None — прочитать не удалось: генератор тогда не ждёт, а карточка помнит, что листы ещё не читались."""
    source = str(calc_source or "")
    spreadsheet_id = spreadsheet_id_from_url(source) or source
    names = tuple(dict.fromkeys(str(index or "") for index in indexes if index))
    if not spreadsheet_id or not names:
        return {}
    try:
        _title, sheets = reader().read_sheets(spreadsheet_id, names)
    except Exception as error:  # noqa: BLE001 — SUPPLYMAP logs a WARN and returns []
        _log.warning("tcCalcOps_: %s", error)
        return None
    return {name: calc_ops_from_grid(sheets.get(name) or []) for name in names}


def assembly_calc_ops(
    kitset: Kitset, assembly: KitAssembly, reader: Callable[[], CalcSheetsReader],
    techcards_uow: Callable[[], TechcardsUnitOfWork],
) -> list[dict[str, str]]:
    """Строки КБn для генератора: из базы, если уже прочитаны (заведение / «Обновить из расчёта»), иначе — с листа
    расчёта один раз, и результат запоминается в карточке: следующее открытие генератора Google не ждёт."""
    if assembly.calc_ops is not None:
        return [dict(row) for row in assembly.calc_ops]
    ops = read_calc_ops(kitset.calc_source, [assembly.index], reader)
    if ops is None:
        return []
    rows = ops.get(assembly.index, [])
    try:
        with techcards_uow() as uow:
            current = uow.kitsets.get(kitset.project)
            if current is not None and current.assembly(assembly.index) is not None:
                uow.kitsets.save(replace(current, version=current.version + 1, assemblies=tuple(
                    replace(item, calc_ops=tuple(rows)) if item.index == assembly.index else item
                    for item in current.assemblies
                )))
                uow.commit()
    except Exception as error:  # noqa: BLE001 — не запомнили: прочитаем в следующий раз, генератор уже получил строки
        _log.warning("calc_ops cache %s %s: %s", kitset.project, assembly.index, error)
    return rows


def calc_ops_by_link(calc_source: str, index: str, reader: Callable[[], CalcSheetsReader]) -> list[dict[str, str]]:
    """`tcCalcOps_(calcUrl, index)`: rows of the calculation's КБn sheet for the generator — from row 5, columns
    A..H as the sheet shows them: {code: B, comp: C, n: D, l: E («L», м), req: H («Требования к задаче»)}, rows
    without a code dropped. The estimator writes cut lengths and tinning there. A read failure must not stop the
    generator: empty list, as in SUPPLYMAP."""
    source = str(calc_source or "")
    spreadsheet_id = spreadsheet_id_from_url(source) or source
    name = str(index or "")
    if not spreadsheet_id or not name:
        return []
    ops = read_calc_ops(source, [name], reader)
    return (ops or {}).get(name, [])


def calc_ops_from_grid(grid: list[list[str]]) -> list[dict[str, str]]:
    """The КБn sheet grid → generator rows (see `calc_ops_by_link`)."""
    if len(grid) < 5:
        return []

    def clean(row: list[str], column: int) -> str:
        return " ".join(str(row[column] if column < len(row) else "").split())

    rows = [{"code": clean(row, 1), "comp": clean(row, 2), "n": clean(row, 3), "l": clean(row, 4), "req": clean(row, 7)}
            for row in grid[4:]]
    return [row for row in rows if row["code"]]


def bom_names(
    kitset: Kitset,
    references_uow: Callable[[], Any] | None,
    fulfillment_uow: Callable[[], FulfillmentUnitOfWork] | None,
) -> tuple[ReferenceNames, StockNames]:
    """The resolvers behind `title` of a generator row — the same ones «Снабжение» names its positions with."""
    lots, _held = _stock_lots(kitset.project, fulfillment_uow)
    return ReferenceNames(reference_records(references_uow)), StockNames(lots)


def generator_bom(
    kitset: Kitset, assembly: KitAssembly, calc_ops: list[dict[str, str]],
    names: tuple[ReferenceNames, StockNames] | None = None,
) -> dict[str, Any]:
    """`tcLoadBomFromKit`: состав сборки для генератора — из сохранённой карточки, с правками оператора.
    Аналог по КД → оригинал к применению (kitUseArt_), заменённый в расчёте провод → провод из «Наименования»
    (`kit_article`): генератор подбирает контакт и сечение по тому, что применяется; артикул КД — в `kd`.
    Состав читается из карточки при каждом открытии, так что правка расчёта доходит до генератора без пересборки.
    `calc_ops` — строки листа КБn расчёта (`calc_ops_by_link`). Тот же ответ читает пакетная генерация.
    `names` (`bom_names`) — `title`: название строки «как в КД» (справочник ERP → спецификация → склад → очищенный
    заголовок сайта) для показа; `name` остаётся прежним — по нему генератор узнаёт деталь (провод, ТУТ, колодка…)."""
    components = []
    for position, component in enumerate(assembly.components):
        # Строку расчёта в ERP не удаляют, а обнуляют (владелец 25.10: «расчёт не правим, правим СПЯ в ERP»):
        # расход 0 — строка не применяется, в генератор она не идёт; номера kit-N остальных строк не сдвигаются.
        if not component.per_unit:
            continue
        # Строку технолога вводят артикулом к применению: её название — не замена провода сметчиком.
        use = {"art": component.article, "kd": ""} if component.manual else kit_article(component.article, component.name)
        name = use["art"] if use["kd"] else (component.name or component.article)
        shown = resolve_name(
            component.article, component.name, "" if component.manual else component.link,
            names[0] if names else None, names[1] if names else None,
        ) if names else None
        components.append({
            "id": f"kit-{position}", "type": component.type, "name": name,
            **({"title": shown.name or name, "title_source": shown.source, "title_hint": shown.hint} if shown else {}),
            "art": use["art"], "kd": use["kd"], "qty": kit_number_payload(component.per_unit),
            "scrap": kit_number_payload(component.scrap_pct / 100),
            **({"manual": True} if component.manual else {}),
        })
    return {
        "project": kitset.project, "index": assembly.index, "name": assembly.name,
        "run": kit_number_payload(assembly.run), "part_qty": kit_number_payload(assembly.run or Decimal("1")),
        "calc_source": kitset.calc_source, "components": components, "calc_ops": calc_ops,
    }


def calc_sheets_reader(state: Any) -> CalcSheetsReader:
    """Tests put a fake on ``app.state.kitset_sheets_reader``; otherwise the service-account adapter from env
    (the same key «Из расчёта» in «Заявках» uses). Raises `CalcSheetError` (kind ``config``) without a key."""
    reader = getattr(state, "kitset_sheets_reader", None)
    if reader is not None:
        return reader
    from app.modules.requests.google_sheets import reader_from_env

    return reader_from_env()


def load_kitset(
    command: LoadKitsetCommand,
    techcards_uow: Callable[[], TechcardsUnitOfWork],
    production_uow: Callable[[], PlanUnitOfWork] | None,
    shipping_uow: Callable[[], ShippingUnitOfWork] | None,
    enqueue_names: Callable[[str], None] | None = None,
) -> LoadKitsetResult:
    """`tcKitsetLoadFromCalc`: create or refresh the card. `number` overrides the calculation's number: typed
    at creation, and passed by «Обновить из расчёта» so the refresh does not open a second card under the
    cell's shorter number."""
    data = command.data
    number = str(command.number or "").strip()
    if number:
        if not project_number_valid(number):
            raise KitsetValidationError(
                f"Неверный № проекта «{number}» — нужны цифры и кириллическая «К», например 720К.1.2"
            )
        data = replace(data, project=number)
    with techcards_uow() as uow:
        current = uow.kitsets.get(data.project)
        if current is not None:
            data = replace(data, project=current.project)
        kitset = write_from_calc(data, current, who=command.actor.name, now=command.now)
        uow.kitsets.save(kitset)
        _audit(uow, kitset.project, "techcards.kitset_loaded", command.actor, command.now, {
            "assemblies": str(len(kitset.assemblies)), "lines": str(kitset.lines),
        })
        uow.commit()
    result = LoadKitsetResult(project=kitset.project, assemblies=len(kitset.assemblies), lines=kitset.lines)

    # Проект — в реестр ERP: одна сущность, а не параллельный список. Существующий не трогаем.
    if production_uow is not None:
        try:
            result.erp = register_project(kitset, command.actor, command.now, production_uow)
        except Exception as error:  # noqa: BLE001 — реестр не роняет заведение (SUPPLYMAP res.erpError)
            result.erp_error = str(error)
    # Очередь на отгрузку встаёт САМА — по строке на каждую КБ (ADR-0016).
    if shipping_uow is not None:
        try:
            queued = queue_project(QueueProjectCommand(
                project=kitset.project, customer=kitset.customer,
                assemblies=tuple(
                    ProjectAssembly(
                        item_index=assembly.index, name=assembly.name, run=assembly.run,
                        article=item_id(kitset.project, assembly.index),
                    )
                    for assembly in kitset.assemblies
                ),
                actor=command.actor, now=command.now,
            ), shipping_uow())
            result.ship = {"added": queued.added, "updated": queued.updated, "skipped": list(queued.skipped)}
        except Exception as error:  # noqa: BLE001
            result.ship_error = str(error)
        fields = {key: value for key, value in (command.ship or {}).items() if str(value or "").strip()}
        if fields:
            try:
                set_project_addressee(SetProjectAddresseeCommand(
                    project=kitset.project, fields=fields, expected_version=None,
                    actor=command.actor, now=command.now,
                ), shipping_uow())
            except Exception as error:  # noqa: BLE001
                result.ship_to_error = str(error)
    # ДЛ — после реестра: запись журнала требует, чтобы проект там уже был.
    if command.deadline is not None and production_uow is not None:
        try:
            add_plan_event(AddPlanEventCommand(
                project=kitset.project, kind="deadline", blocker="", text="при заведении",
                deadline=command.deadline, known_projects=(kitset.project,),
                actor=command.actor, now=command.now,
            ), production_uow())
        except Exception as error:  # noqa: BLE001
            result.deadline_error = str(error)
    # tcKitNamesEnqueue_: пустые наименования добирает фон (kitset_names_tick) по ссылкам из расчёта.
    if enqueue_names is not None:
        try:
            enqueue_names(kitset.project)
        except Exception:  # noqa: BLE001 — очередь имён не роняет заведение
            logging.exception("tcKitNamesEnqueue_:%s", kitset.project)
    return result


@dataclass(frozen=True)
class KitsetRequestsCommand:
    """«Сразу создать заявки на закупку» при заведении проекта: те же заявки, что «Оформить заявку из расчёта»
    (ЗКП расчёта → `importRequests_`), с настройками формы по умолчанию — категория «Общепром», документ по
    качеству «Нет», план. дата поступления = дедлайн закупки."""

    project: str
    url: str
    purpose: str
    delivery_point: str
    procurement_deadline: date
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class KitsetRequestsResult:
    created: int = 0
    existing: int = 0
    skipped: tuple[str, ...] = ()


def requests_calc_reader(state: Any) -> CalcSpreadsheetReader:
    """The ЗКП reader of «Из расчёта» in «Заявках»: a test fake on ``app.state.calc_spreadsheet_reader``,
    otherwise the service-account adapter from env."""
    reader = getattr(state, "calc_spreadsheet_reader", None)
    if reader is not None:
        return reader
    from app.modules.requests.google_sheets import reader_from_env

    return reader_from_env()


def project_request_count(project: str, requests: Iterable[Request]) -> int:
    """Requests already filed for the project — by project reference, purpose or `project.NNN` designation."""
    canon = normalize_project_number(project)
    count = 0
    for request in requests:
        targets = (
            normalize_project_number(request.project_reference) if request.project_reference else "",
            project_from_purpose(request.purpose),
            project_from_purpose(request.designation),
        )
        count += canon in targets
    return count


def create_kitset_requests(
    command: KitsetRequestsCommand,
    requests_uow: Callable[[], RequestsUnitOfWork],
    reader: Callable[[], CalcSpreadsheetReader],
) -> KitsetRequestsResult:
    """A project that already has requests gets none: re-creating it never doubles the purchase. Rows without a
    readable quantity are left for the manual form and named in the result."""
    with requests_uow() as uow:
        existing = project_request_count(command.project, uow.requests.recent(10_000))
    if existing:
        return KitsetRequestsResult(existing=existing)
    extract = extract_from_calc(command.url, reader())
    items, skipped = [], []
    for item in extract.items:
        quantity = parse_calc_number(item.quantity)
        if quantity is None or quantity <= 0:
            skipped.append(item.name)
            continue
        items.append(CalcImportItem(
            item_type=item.item_type, name=item.name, quantity=quantity, unit=item.unit,
            quality_document="Нет", planned_date=command.procurement_deadline, category="Общепром",
            link=item.link or None, price=parse_calc_number(item.price),
            price_with_vat=parse_calc_number(item.price_with_vat),
        ))
    if not items:
        raise RequestValidationError("В листе ЗКП нет позиций с количеством")
    drafts = build_import_drafts(CalcImport(
        project=command.project, purpose=command.purpose, delivery_point=command.delivery_point,
        procurement_deadline=command.procurement_deadline, items=tuple(items),
    ))
    created = create_requests(CreateRequestsCommand(
        creation_key=f"kitset-requests:{normalize_project_number(command.project)}",
        drafts=tuple(drafts), actor=command.actor, now=command.now,
        designation_base=command.project.strip(), source="import", to_procurement=True,
    ), requests_uow())
    return KitsetRequestsResult(created=len(created), skipped=tuple(skipped))


def register_project(
    kitset: Kitset, actor: Actor, now: datetime, production_uow: Callable[[], PlanUnitOfWork],
) -> dict[str, Any]:
    """`tcKitsetSyncErp_`: a missing project enters the registry; customer is required there, «—» if the
    calculation has none. КБ позиции the plan reads from the kitset itself (build_production_plan)."""
    canon = normalize_project_number(kitset.project)
    with production_uow() as uow:
        projects = list(uow.plan_projects.list())
    if any(normalize_project_number(item.project) == canon for item in projects):
        return {"project": False, "positions": len(kitset.assemblies)}
    replace_plan_projects(ReplacePlanProjectsCommand(
        projects=(*projects, PlanProjectSnapshot(
            project=kitset.project, customer=kitset.customer or "—", opened_on=now.date(),
        )),
        actor=actor, now=now,
    ), production_uow())
    return {"project": True, "positions": len(kitset.assemblies)}


# ── Статус проекта (portalKitsetList / tcKitsetGet) ─────────────────────


@dataclass(frozen=True)
class KitsetFacts:
    cards: tuple[ProjectTechcard, ...] = ()
    tasks: tuple[ProductionTask, ...] = ()
    fulfillment: tuple[FulfillmentRequest, ...] = ()
    goods: tuple[FinishedGood, ...] = ()
    events: tuple[PlanEvent, ...] = ()
    projects: tuple[PlanProjectSnapshot, ...] = ()
    # Production boxes and the ids of the boxes ОТК inspected: the ladder's «ОТК» stage.
    boxes: tuple[ProductionBox, ...] = ()
    inspected: frozenset[UUID] = frozenset()
    # Ids of the tasks with a work session: the ladder's «В работе».
    worked: frozenset[UUID] = frozenset()
    # КБ runs (kit_stage_key → pieces, `issued_runs`): «ОТК» only once the whole run is made.
    runs: dict[str, int] = field(default_factory=dict)


def _qc_facts(uow, tasks=()) -> tuple[tuple[ProductionBox, ...], frozenset[UUID], frozenset[UUID]]:
    """Boxes, ОТК-inspected box ids and ids of ``tasks`` someone worked on (the ladder's «В работе») through the
    production unit of work; a store without them reads empty."""
    boxes, qc = getattr(uow, "boxes", None), getattr(uow, "qc", None)
    worked_of = getattr(getattr(uow, "production", None), "worked", None)
    worked = frozenset(worked_of([task.id for task in tasks])) if callable(worked_of) and tasks else frozenset()
    if boxes is None or qc is None:
        return (), frozenset(), worked
    return tuple(boxes.list(limit=100_000)), frozenset(qc.inspected_box_ids()), worked


def kitset_facts(techcards, production=None, fulfillment=None, shipping=None) -> KitsetFacts:
    """Facts for `kitset_stages` from the modules' unit-of-work factories; a missing optional module reads empty."""
    def read(factory, reader, default=()):
        if factory is None:
            return default
        with factory() as uow:
            return reader(uow)

    def read_production(uow):
        listed = tuple(uow.production.list(limit=10_000))
        return listed, tuple(uow.plan_events.list()), tuple(uow.plan_projects.list()), _qc_facts(uow, listed)

    tasks, events, projects, (boxes, inspected, worked) = read(production, read_production, ((), (), (), ((), frozenset(), frozenset())))
    cards, kitsets = read(techcards, lambda uow: (
        tuple(uow.techcards.list()), tuple(uow.kitsets.list()) if hasattr(uow, "kitsets") else (),
    ), ((), ()))
    return KitsetFacts(
        cards=cards,
        tasks=tasks,
        fulfillment=read(fulfillment, lambda uow: tuple(uow.fulfillment.list(limit=10_000))),
        goods=read(shipping, lambda uow: tuple(uow.goods.list(limit=10_000))),
        events=events,
        projects=projects,
        boxes=boxes,
        inspected=inspected,
        worked=worked,
        runs=issued_runs(cards, kitsets),
    )


def _stage_marks(facts: KitsetFacts, events: Iterable[PlanEvent]):
    """Stage facts and marks of the ladder; `kit_stage_facts` rebuilds a mark for an issued card — the stages a manual
    pick jumped (`KitMark.skipped`) are carried over from the journal."""
    raw = summarize_kit_marks(events)
    stage_facts, marks = kit_stage_facts(
        cards=facts.cards, tasks=facts.tasks, fulfillment=facts.fulfillment, goods=facts.goods, marks=raw,
        boxes=facts.boxes, inspected=facts.inspected, worked=facts.worked, runs=facts.runs,
    )
    marks = {key: replace(mark, skipped=raw[key].skipped) if key in raw else mark for key, mark in marks.items()}
    return stage_facts, marks


def kitset_stages(kitsets: Iterable[Kitset], facts: KitsetFacts) -> dict[str, tuple[ProjectStage, str]]:
    """{project: (stage, active blocker type)} — the same ladder and the same КБ (`project_kits`: the kitset's
    assemblies plus the project's techcard КБ) the production plan uses."""
    stage_facts, marks = _stage_marks(facts, facts.events)
    journal = summarize_journal(facts.events)
    snapshots = {normalize_project_number(item.project): item for item in facts.projects}
    result = {}
    for kitset in kitsets:
        canon = normalize_project_number(kitset.project)
        stage = project_stage(
            kitset.project, project_kits(kitset.project, (assembly.index for assembly in kitset.assemblies), facts.cards),
            stage_facts, project_terminal(snapshots.get(canon), journal.get(canon)), marks,
        )
        entry = journal.get(canon)
        blocker = entry.blocker.type if entry is not None and entry.blocker is not None else ""
        result[kitset.project] = (stage, blocker)
    return result


# Владелец 26.10: «почему на доске мастера до сих пор есть отгруженные». КБ с этими этапами (или проект в этих
# статусах / «Отменён») к запуску не предлагаются — ни на доске мастера, ни в автоплане.
CLOSED_KB_STAGES = ("Принято складом", "Упаковано", "Отгружено")


def closed_kb_keys(techcards, production=None, fulfillment=None, shipping=None) -> frozenset[str]:
    """Ключи `kit_stage_key` КБ, которые запускать уже нечего: этап КБ «Принято складом» / «Упаковано» / «Отгружено»,
    либо проект в одном из этих статусов или в терминальном («Отменён»). Тот же этап, что на экране проекта."""
    if techcards is None:
        return frozenset()
    with techcards() as uow:
        kitsets = tuple(uow.kitsets.list()) if hasattr(uow, "kitsets") else ()
    if not kitsets:
        return frozenset()
    stages = kitset_stages(kitsets, kitset_facts(techcards, production, fulfillment, shipping))
    closed = (*CLOSED_KB_STAGES, *TERMINAL_STATUSES)
    keys: set[str] = set()
    for project, (stage, _blocker) in stages.items():
        for kit in stage.kits:
            if stage.status in closed or kit.status in closed:
                keys.add(kit_stage_key(project, kit.item_index))
    return frozenset(keys)


PROJECT_BLOCK = "block"
PROJECT_UNBLOCK = "unblock"
PROJECT_STATUS = "status"
# Отметка «ТК готова» / «КБ набрана» проекта (владелец 29.10): ручной переключатель да / нет / авто.
PROJECT_MARKER = "marker"


# ── Ручная смена этапа: одна служба за всеми выборами этапа (карточка, КБ, «Производственный план») ──────────

@dataclass(frozen=True)
class ProjectStageState:
    """The stages of one project right now, read for a manual change or its preview."""

    project: str
    kits: tuple[KitStageNow, ...]
    terminal: str = ""
    blocker: ActiveBlocker | None = None
    # The project's last journal sequence at read time: the change is refused if the journal moved since.
    sequence: int = 0
    # What a forward move may close or must keep (`stage_cascade`): tasks, kit requests, reserves, goods.
    cascade: stage_cascade.CascadeFacts = field(default_factory=stage_cascade.CascadeFacts)


def project_stage_state(
    project: str, item_indexes: Iterable[str], facts: KitsetFacts, *, sequence: int = 0, kb: str = "",
    cascade: stage_cascade.CascadeFacts | None = None,
) -> ProjectStageState:
    """КБ of the project — the kitset's assemblies plus the КБ of its techcards (as the production plan lists them),
    plus ``kb`` when a single КБ is picked — with their stages now. ``cascade`` — the project's closable items read
    in one session (sessions, techcard reserves); without it they come from ``facts`` (no work sessions, no
    techcard reserves)."""
    canon = normalize_project_number(project)
    indexes = [*project_kits(project, item_indexes, facts.cards), *([kb] if str(kb or "").strip() else [])]
    events = tuple(event for event in facts.events if normalize_project_number(event.project) == canon)
    stage_facts, marks = _stage_marks(facts, events)
    journal = summarize_journal(events).get(canon)
    snapshot = next((item for item in facts.projects if normalize_project_number(item.project) == canon), None)
    kits = project_stage_now(project, indexes, stage_facts, marks)
    if cascade is None:
        cascade = stage_cascade.CascadeFacts(tasks=facts.tasks, requests=facts.fulfillment, goods=facts.goods)
    return ProjectStageState(
        project=canon, kits=kits, terminal=project_terminal(snapshot, journal),
        blocker=journal.blocker if journal is not None else None, sequence=sequence, cascade=cascade,
    )


def load_project_stage_state(
    project: str, *, production, techcards=None, fulfillment=None, shipping=None,
    item_indexes: Iterable[str] | None = None, kb: str = "",
) -> ProjectStageState:
    """Reads the stages of a project from the modules (``item_indexes`` None — the kitset's assemblies, if any).
    The journal version is read first, so a row added while the facts are read refuses the change."""
    canon = normalize_project_number(project)
    with production() as uow:
        sequence = uow.plan_events.last_sequence(canon)
    if item_indexes is None:
        kitset = None
        if techcards is not None:
            with techcards() as uow:
                kitsets = getattr(uow, "kitsets", None)
                kitset = kitsets.get(project) if kitsets is not None else None
        item_indexes = [assembly.index for assembly in kitset.assemblies] if kitset is not None else []
    facts = kitset_facts(techcards, production, fulfillment, shipping)
    cascade = stage_cascade.load_cascade_facts(stage_cascade.cascade_uow_factory(production), project)
    return project_stage_state(project, item_indexes, facts, sequence=sequence, kb=kb, cascade=cascade)


def stage_change_payload(
    state: ProjectStageState, plan: StagePlan, cascade: stage_cascade.StageCascade | None = None,
) -> dict[str, Any]:
    """Preview (and the applied change) for the screens: per КБ from → to, skipped stages in ladder order, what the
    move closes (`close`) and keeps with the reason (`keep`), totals; `preview_token` — the version the apply is
    checked against. ``cascade`` — what an apply actually closed (the preview plans it over ``state``)."""
    if cascade is None:
        cascade = stage_cascade.plan_cascade(state.project, plan, state.cascade)
    changed = {change.item_index: change for change in plan.changes}
    kept = {item.item_index: item for item in plan.kept}
    kbs = []
    for kit in state.kits:
        closing = stage_cascade.kb_payload(cascade, kit.item_index)
        if kit.item_index in changed:
            change = changed[kit.item_index]
            of_kb = cascade.of(kit.item_index)
            kbs.append({
                "kb": kit.item_index, "label": kit_label(kit.item_index), "from": change.from_stage,
                "to": change.to_stage, "skipped": list(change.skipped), "change": True, "note": "",
                "warnings": of_kb.warnings() if of_kb is not None else [], **closing,
            })
        elif kit.item_index in kept:
            item = kept[kit.item_index]
            kbs.append({
                "kb": kit.item_index, "label": kit_label(kit.item_index), "from": item.stage, "to": item.stage,
                "skipped": [], "change": False, "note": item.note, "warnings": [], **closing,
            })
    warnings = [cascade.note] if cascade.note else []
    if state.terminal and not plan.error:
        warnings.append(f"Проект закрыт («{state.terminal}») — смена этапа вернёт его в работу.")
    if state.blocker is not None:
        warnings.append(
            f"Активный блокер «{state.blocker.type}»" + (f": {state.blocker.text}" if state.blocker.text else "")
            + " — смена этапа его не снимает."
        )
    return {
        "project": state.project, "value": plan.value, "kb": plan.kb, "direction": plan.direction,
        "reason_required": plan.reason_required, "error": plan.error, "message": stage_plan_message(plan),
        "terminal": state.terminal, "blocker": state.blocker.type if state.blocker is not None else "",
        "kbs": kbs, "warnings": warnings,
        **stage_cascade.cascade_payload(cascade),
        "preview_token": stage_cascade.cascade_token(state.project, state.sequence, plan, cascade),
    }


@dataclass(frozen=True)
class ChangeProjectStageCommand:
    """Ручная смена этапа: ``kb`` "" — весь проект (без КБ — запись с пустым КБ), иначе одна КБ; ``value`` — этап
    лестницы или «авто». Назад — только с ``reason``. ``preview_token`` — из предпросмотра: изменилось с тех пор
    что-то из закрываемого — отказ 409; пусто — без сверки с предпросмотром (старый экран)."""

    project: str
    value: str
    actor: Actor
    now: datetime
    kb: str = ""
    reason: str = ""
    preview_token: str = ""


@dataclass(frozen=True)
class ProjectStageChange:
    events: tuple[PlanEvent, ...]
    plan: StagePlan
    state: ProjectStageState
    # What the change closed (None — journal rows only, no shared session).
    cascade: stage_cascade.StageCascade | None = None

    def payload(self) -> dict[str, Any]:
        return stage_change_payload(self.state, self.plan, self.cascade)


def preview_project_stage(state: ProjectStageState, value: str, kb: str = "") -> dict[str, Any]:
    return stage_change_payload(state, plan_stage_change(state.kits, value, kb))


def change_project_stage(
    command: ChangeProjectStageCommand, state: ProjectStageState, uow_factory: Callable[[], PlanUnitOfWork],
) -> ProjectStageChange:
    """The one stage service: plans the pick over ``state`` (read just before) and writes it — with everything a
    forward move closes (`stage_cascade`) — in one transaction. A stage pick on a closed project first returns it to
    work (as before)."""
    plan = plan_stage_change(state.kits, command.value, command.kb)
    change = ChangeStageCommand(
        project=command.project, plan=plan, actor=command.actor, now=command.now, reason=command.reason,
        reopen_from=state.terminal, expected_sequence=state.sequence,
    )
    cascade_factory = stage_cascade.cascade_uow_factory(uow_factory)
    if cascade_factory is None:
        return ProjectStageChange(change_stage(change, uow_factory()), plan, state)
    events, cascade = stage_cascade.apply_stage_change(
        change, cascade_factory(), preview_token=str(command.preview_token or "").strip(),
    )
    return ProjectStageChange(events, plan, state, cascade)


@dataclass(frozen=True)
class ChangeProjectStatusCommand:
    """Смена статуса проекта одним действием — из карточки, плитки и «Производственного плана».

    ``action``: "status" — ``value`` этап лестницы (поднимает отставшие КБ; назад — с причиной ``text``), «авто»
    (снять ручные этапы) или Отказ/Слился/Отменён; "block" — блокер ``value`` (СТОП, ОЖИД ИНФ, ОЖИД КОМПЛ) с причиной
    ``text``; "unblock" — снять блокер; "marker" — отметка ``field`` (tc_ready — «ТК готова», kit_picked — «КБ
    набрана») = ``value`` да / нет / авто (авто — снова по факту).
    """

    project: str
    item_indexes: tuple[str, ...]
    action: str
    value: str
    actor: Actor
    now: datetime
    text: str = ""
    preview_token: str = ""
    field: str = ""


def change_project_status(
    command: ChangeProjectStatusCommand, uow_factory: Callable[[], PlanUnitOfWork], *,
    techcards=None, fulfillment=None, shipping=None,
) -> list[PlanEvent] | ProjectStageChange:
    """Единая точка смены статуса проекта: блокер и закрытие — записью журнала, этап — через `change_project_stage`
    (возвращает `ProjectStageChange`).

    «Отгружено» по факту отгрузки (ГП отгружено ≥ партии) ставится само и перекрывает ручной этап — вызывать эту
    функцию из отгрузки не нужно.
    """
    project, value, known = command.project, str(command.value or "").strip(), (command.project,)

    def add(kind: str, *, event_value: str = "", blocker: str = "", text: str = "", field: str = "") -> PlanEvent:
        return add_plan_event(AddPlanEventCommand(
            project=project, kind=kind, blocker=blocker, text=text, deadline=None, known_projects=known,
            actor=command.actor, now=command.now, value=event_value, field=field,
        ), uow_factory())

    if command.action == PROJECT_MARKER:
        field = str(command.field or "").strip()
        if field not in MARKER_FIELDS:
            raise KitsetValidationError("Отметка — tc_ready («ТК готова») или kit_picked («КБ набрана»).")
        if value not in (*PLAN_MARK_FLAGS, PLAN_STAGE_AUTO):
            raise KitsetValidationError(f"Отметка «{MARKER_FIELDS[field]}» — да, нет или авто.")
        return [add("mark", event_value=value, field=field, text=command.text)]
    if command.action == PROJECT_BLOCK:
        return [add("block", blocker=value, text=command.text)]
    if command.action == PROJECT_UNBLOCK:
        return [add("unblock", text=command.text)]
    if command.action != PROJECT_STATUS:
        raise KitsetValidationError("Неизвестное действие со статусом проекта.")
    if value in PROJECT_TERMINALS:
        return [add("status", event_value=value, text=command.text)]
    if legacy_stage_refusal(value):
        raise KitsetValidationError(legacy_stage_refusal(value))
    if value not in (*KIT_STAGES, PLAN_STAGE_AUTO):
        raise KitsetValidationError(f"Неизвестный статус проекта: {value}")
    state = load_project_stage_state(
        project, production=uow_factory, techcards=techcards, fulfillment=fulfillment, shipping=shipping,
        item_indexes=command.item_indexes,
    )
    return change_project_stage(ChangeProjectStageCommand(
        project=project, value=value, actor=command.actor, now=command.now, reason=command.text,
        preview_token=command.preview_token,
    ), state, uow_factory)


def kitset_statuses() -> list[str]:
    return [*KIT_STAGES, *TERMINAL_STATUSES]


def cards_state(project: str, cards: Iterable[ProjectTechcard]) -> dict[str, dict[str, Any]]:
    """`tcKitsetCardsState_`: per КБ — card id, issued or not, operations count, revision."""
    canon = project_key(project)
    state = {}
    for card in cards:
        if project_key(card.project) != canon:
            continue
        operations = (card.document or {}).get("operations") or []
        state[card.item_index.strip().upper()] = {
            "card_id": str(card.id), "status": card.status.value, "issued": card.status is TechcardStatus.ISSUED,
            "stages": len(operations), "revision": card.revision,
        }
    return state


# ── Снабжение (portalKitsetSupply) ──────────────────────────────────────


_RECEIVING_STATUSES = {"В пути", "Карантин", "Готово"}


def _project_chain(
    project: str, requests: Iterable[Request], procurement: Iterable[ProcurementLine],
) -> tuple[dict[UUID, Request], list[ProcurementLine]]:
    """The project's requests (by id) and procurement lines: by the request's project reference (or purpose), a
    line by its request or its own project reference."""
    canon = normalize_project_number(project)
    by_request: dict[UUID, Request] = {}
    for request in requests:
        target = (
            normalize_project_number(request.project_reference)
            if request.project_reference else project_from_purpose(request.purpose)
        )
        if target == canon:
            by_request[request.id] = request
    lines = []
    for line in procurement:
        target = normalize_project_number(line.project_reference) if line.project_reference else ""
        if line.source_request_id in by_request or target == canon:
            lines.append(line)
    return by_request, lines


def _line_article(line: ProcurementLine, request: Request | None) -> str:
    # Артикул мог остаться только в заявке — тянем его вперёд по связи строки с заявкой.
    return str(line.design_article or "").strip() or (str(request.design_article or "").strip() if request else "")


def supply_rows(
    project: str, requests: Iterable[Request], procurement: Iterable[ProcurementLine],
    lotless: Iterable[UUID] = (),
) -> list[SupplyRow]:
    """The purchase chain of the project: Запрос → Закупка → Приёмка. SUPPLYMAP ties it by ЦЕЛЬ (project number)
    and АРТИКУЛ КД; here by the request's project reference (or purpose) and design article. A procurement line
    in transit, quarantine or done is at acceptance; «Готово» means it was accepted with a ГРН.

    `open_quantity` («В закупке»): a live request with no procurement line yet brings its КОЛ-ВО; once handed over,
    only the newest live revision of its line brings what is still to buy (КОЛ-ВО − «Со склада») until it is
    accepted. Accepted goods lie on the shelf or sit in the kit — counted there, not twice.

    `lotless` — ids of lines holding a cover «Есть на складе, без ГРН»: such a line closed from stock (unpaid
    «Готово») was never accepted — it stays in «Закупка» as «Готово · без ГРН», not «Принято на склад»."""
    by_request, lines = _project_chain(project, requests, procurement)
    without_lot = set(lotless)
    handed = {line.source_request_id for line in lines}
    newest: dict[UUID, ProcurementLine] = {}
    for line in lines:
        if line.status.value == "Отменено":
            continue
        current = newest.get(line.source_request_id)
        if current is None or (line.created_at, line.revision) > (current.created_at, current.revision):
            newest[line.source_request_id] = line
    rows = []
    for request in by_request.values():
        live = request.status not in (RequestStatus.CANCELLED, RequestStatus.CLOSED) and request.id not in handed
        rows.append(SupplyRow(
            article=str(request.design_article or "").strip(), where="Запрос", status=request.status.value,
            source_id=request.legacy_id or str(request.id), name=request.name,
            open_quantity=request.quantity if live else Decimal("0"),
        ))
    for line in lines:
        status = line.status.value
        if status == "Отменено":
            continue
        request = by_request.get(line.source_request_id)
        article = _line_article(line, request)
        where = "Приёмка" if status in _RECEIVING_STATUSES else "Закупка"
        live = newest.get(line.source_request_id) is line and status != "Готово"
        received = status == "Готово"
        if received and line.paid_at is None and line.id in without_lot:
            where, status, received = "Закупка", f"{status} · без ГРН", False
        rows.append(SupplyRow(
            article=article, where=where, status=status, source_id=line.legacy_id or str(line.id),
            received=received, name=line.calculated_name or (request.name if request else ""),
            open_quantity=line.to_buy if live else Decimal("0"),
        ))
    return rows


def supply_off_book(
    project: str, requests: Iterable[Request], procurement: Iterable[ProcurementLine],
    procurement_uow: Callable[[], ProcurementUnitOfWork] | None,
) -> list[tuple[UUID, SupplyStock]]:
    """«Без ГРН»: the project's procurement covers «Есть на складе, без ГРН» — on the shelf, in no lot, so neither
    «Свободно» nor «В закупке» (the line's КОЛ-ВО − «Со склада») has them. Cancelled lines bring nothing.
    → (line id, the quantity under the line's article and name). No factory or no port (test fakes) → []."""
    if procurement_uow is None:
        return []
    by_request, lines = _project_chain(project, requests, procurement)
    by_id = {line.id: line for line in lines if line.status.value != "Отменено"}
    if not by_id:
        return []
    with procurement_uow() as uow:
        covers = getattr(uow, "stock_covers", None)
        found = list(covers.active_without_lot(list(by_id))) if covers is not None else []
    result = []
    for cover in found:
        line = by_id.get(cover.line_id)
        if line is None or cover.lot_id is not None or cover.quantity <= 0:
            continue
        request = by_request.get(line.source_request_id)
        result.append((line.id, SupplyStock(
            article=_line_article(line, request), name=line.calculated_name or (request.name if request else ""),
            quantity=cover.quantity,
        )))
    return result


def supply_stock(project: str, fulfillment_uow: Callable[[], FulfillmentUnitOfWork] | None) -> list[SupplyStock]:
    """«Свободно»: what a kit of the project can take from each lot (ДОСТУПНО + the project's own reserves on it —
    «Со склада» covers and techcard reserves, the same number the spec's lot picker shows)."""
    return _free_stock(*_stock_lots(project, fulfillment_uow))


def _stock_lots(project: str, fulfillment_uow: Callable[[], FulfillmentUnitOfWork] | None) -> tuple[list, dict]:
    """Every lot with a balance and the project's own reserves on them; no factory → nothing."""
    if fulfillment_uow is None:
        return [], {}
    with fulfillment_uow() as uow:
        lots = list(uow.warehouse.list(limit=10_000, query="", include_zero=False))
        held = project_reserve_by_lot(uow, lots, project)
    return lots, held


def _free_stock(lots: list, held: dict) -> list[SupplyStock]:
    stock = []
    for lot in lots:
        free = free_for_project(lot, held)
        if free <= 0:
            continue
        # A lot accepted without an article carries its name there: then it is matched by name, like a manual request.
        article = "" if _same_text(lot.article, lot.name) else lot.article
        stock.append(SupplyStock(article=article, name=lot.name, quantity=free))
    return stock


def supply_kitting(project: str, requests: Iterable[FulfillmentRequest]) -> list[SupplyStock]:
    """Already in the project's kits: reserved on a lot or issued (less what came back good) — that need is covered
    and leaves the shelf, so it is counted here and not as free stock."""
    canon = normalize_project_number(project)
    kitted = []
    for request in requests:
        if request.status is FulfillmentStatus.CANCELLED or project_from_purpose(request.purpose) != canon:
            continue
        for line in request.lines:
            issued = max(line.issued - line.returned_good, Decimal("0"))
            quantity = line.reserved + issued
            if quantity > 0:
                kitted.append(SupplyStock(
                    article=line.article, name=line.name, quantity=quantity, off_book=line.off_book, issued=issued,
                ))
    return kitted


def supply_absent(project: str, requests: Iterable[FulfillmentRequest]) -> list[SupplyStock]:
    """Kit lines of the project marked «нет на складе»: the storekeeper found nothing on the shelf, so that much of the
    book stock is not there for the project — `supply_lines` takes it off «Свободно» and the position becomes short."""
    canon = normalize_project_number(project)
    return [
        SupplyStock(article=line.article, name=line.name, quantity=line.requirement)
        for request in requests
        if request.status is not FulfillmentStatus.CANCELLED and project_from_purpose(request.purpose) == canon
        for line in request.lines
        if line.absent
    ]


def _same_text(left: object, right: object) -> bool:
    return " ".join(str(left or "").split()).casefold() == " ".join(str(right or "").split()).casefold()


def project_supply(
    kitset: Kitset,
    requests_uow: Callable[[], RequestsUnitOfWork] | None,
    procurement_uow: Callable[[], ProcurementUnitOfWork] | None,
    fulfillment_uow: Callable[[], FulfillmentUnitOfWork] | None,
    references_uow: Callable[[], Any] | None = None,
) -> tuple[tuple[SupplyLine, ...], tuple[Request, ...]]:
    """The «Снабжение» tab: every spec position with Нужно / Свободно / Без ГРН / В закупке / Не хватает, its stage
    and the name it shows (`item_names.resolve_name`: references, the spec, the warehouse, the supplier site).
    A module without a factory reads empty. Also returns the project's requests (the order reads their point)."""
    def read(factory, reader):
        if factory is None:
            return ()
        with factory() as uow:
            return tuple(reader(uow))

    # Только этот проект, по индексам ключа № проекта (U06): «первые 10 000» всей ERP теряли старые записи проекта.
    # Отбор шире, чем нужно цепочке (заявка — по № проекта, цели или обозначению), сужают `_project_chain` и др.
    project = kitset.project
    requests = read(requests_uow, lambda uow: uow.requests.for_project(project))
    procurement = read(procurement_uow, lambda uow: uow.procurement.for_project(
        project, tuple(request.id for request in requests),
    ))
    kits = read(fulfillment_uow, lambda uow: uow.fulfillment.for_project(project))
    off_book = supply_off_book(kitset.project, requests, procurement, procurement_uow)
    rows = supply_rows(kitset.project, requests, procurement, lotless=[line_id for line_id, _item in off_book])
    lots, held = _stock_lots(kitset.project, fulfillment_uow)
    lines = supply_lines(
        kitset, rows, _free_stock(lots, held), supply_kitting(kitset.project, kits),
        off_book=[item for _line_id, item in off_book], absent=supply_absent(kitset.project, kits),
    )
    references = ReferenceNames(reference_records(references_uow))
    stock_names = StockNames(lots)
    named = []
    for line in lines:
        shown = resolve_name(line.article, line.name, line.link, references, stock_names)
        named.append(replace(line, name=shown.name, name_source=shown.source, name_hint=shown.hint))
    return tuple(named), requests


# ── Заказать недостающее (Снабжение → заявки на закупку) ────────────────


ORDER_PURPOSE = "Обеспечение производства"
ORDER_ITEM_TYPE = "Комплектующие"


@dataclass(frozen=True)
class OrderMissingCommand:
    """«Заказать недостающее»: `articles` — the picked spec positions (КД article), empty — every position short.
    `deadline` / `delivery_point` override the project's own (its deadline, the point of its latest request)."""

    project: str
    articles: tuple[str, ...]
    actor: Actor
    now: datetime
    deadline: date | None = None
    delivery_point: str = ""


@dataclass
class OrderMissingResult:
    created: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    @property
    def message(self) -> str:
        if not self.created and not self.skipped:
            return "Не хватающих позиций нет — заказывать нечего."
        text = f"Заявки созданы: {len(self.created)}"
        if self.skipped:
            reasons = list(dict.fromkeys(item["kind"] for item in self.skipped))
            text += f", пропущено {len(self.skipped)} ({', '.join(reasons)})"
        return text + "."


def _skip_reason(line: SupplyLine) -> tuple[str, str]:
    """(kind, reason) of a picked position with nothing to order."""
    if line.need <= 0:
        return "нет потребности", "нет потребности"
    if line.ordered > 0:
        return "уже в закупке", f"уже в закупке: {kit_number_payload(line.ordered)}"
    if line.kitted > 0:
        return "уже в комплектации", f"уже в комплектации: {kit_number_payload(line.kitted)}"
    if line.off_book > 0:
        return "есть на складе без ГРН", f"на складе без ГРН: {kit_number_payload(line.off_book)}"
    return "хватает на складе", f"свободно на складе: {kit_number_payload(line.free)}"


def project_deadline(project: str, production_uow: Callable[[], PlanUnitOfWork] | None) -> date | None:
    """The project's deadline as its card shows it: the journal's latest ДЛ, else the registry's."""
    if production_uow is None:
        return None
    canon = normalize_project_number(project)
    with production_uow() as uow:
        journal = summarize_journal(uow.plan_events.list(project=canon)).get(canon)
        snapshot = next(
            (item for item in uow.plan_projects.list() if normalize_project_number(item.project) == canon), None,
        )
    return (journal.deadline if journal is not None else None) or (snapshot.deadline if snapshot else None)


def order_missing(
    command: OrderMissingCommand,
    techcards_uow: Callable[[], TechcardsUnitOfWork],
    requests_uow: Callable[[], RequestsUnitOfWork],
    *,
    procurement_uow: Callable[[], ProcurementUnitOfWork] | None = None,
    fulfillment_uow: Callable[[], FulfillmentUnitOfWork] | None = None,
    production_uow: Callable[[], PlanUnitOfWork] | None = None,
    references_uow: Callable[[], Any] | None = None,
) -> OrderMissingResult:
    """Purchase requests for what the project lacks: per position Нужно − Свободно − В закупке − в комплектации −
    на складе без ГРН.

    Idempotent by construction: the requests it files count «В закупке» at once, so a repeat finds nothing short;
    the creation key (project + what is ordered + the chain it saw) also turns a double click into one batch.
    The project's earlier requests do not block it — they are subtracted."""
    with techcards_uow() as uow:
        kitset = uow.kitsets.get(command.project)
    if kitset is None:
        raise KitsetNotFound(f"Карточка набора «{command.project}» не найдена")
    if not project_reference_valid(kitset.project):
        raise KitsetValidationError(
            f"Номер проекта «{kitset.project}» не в формате 591К.3 — заявку к нему не привязать"
        )
    lines, requests = project_supply(kitset, requests_uow, procurement_uow, fulfillment_uow, references_uow)
    by_key = {_supply_key(line.article): line for line in lines}
    wanted = [_supply_key(article) for article in command.articles if _supply_key(article)]
    result = OrderMissingResult()
    picked: list[SupplyLine] = []
    for key in dict.fromkeys(wanted) if wanted else by_key:
        line = by_key.get(key)
        if line is None:
            result.skipped.append({"art": key, "kind": "нет в спецификации", "reason": "нет в спецификации"})
        elif line.short > 0:
            picked.append(line)
        elif wanted:
            kind, reason = _skip_reason(line)
            result.skipped.append({"art": line.article, "kind": kind, "reason": reason})
        elif line.need > 0:
            # Без выбора пропущенными считаются только покрытые позиции — чтобы баннер сказал, почему их нет.
            kind, reason = _skip_reason(line)
            result.skipped.append({"art": line.article, "kind": kind, "reason": reason})
    if not picked:
        return result
    deadline = command.deadline or project_deadline(kitset.project, production_uow)
    if deadline is None:
        raise KitsetValidationError("У проекта нет дедлайна — укажи срок закупки")
    canon = normalize_project_number(kitset.project)
    point = command.delivery_point.strip() or next((
        request.delivery_point for request in sorted(requests, key=lambda item: item.created_at, reverse=True)
        if (normalize_project_number(request.project_reference) if request.project_reference
            else project_from_purpose(request.purpose)) == canon and request.delivery_point
    ), DELIVERY_POINTS[0])
    drafts = []
    for line in picked:
        item_type = line.item_type.strip() or ORDER_ITEM_TYPE
        drafts.append(RequestDraft(
            purpose=ORDER_PURPOSE, delivery_point=point, name=line.name.strip() or line.article,
            item_type=item_type, quantity=line.short, unit=unit_for_type(item_type), quality_document="Нет",
            procurement_deadline=deadline, planned_date=deadline, project_reference=kitset.project,
            category=DEFAULT_CATEGORY, design_article=line.article,
            comment=f"{kitset.project} · {', '.join(line.kbs)}" if line.kbs else kitset.project,
        ))
    seen = sorted(f"{row.source_id}:{row.status}" for line in lines for row in line.rows)
    digest = hashlib.sha256(json.dumps(
        [canon, seen, [(draft.design_article, str(draft.quantity)) for draft in drafts]], ensure_ascii=False,
    ).encode()).hexdigest()[:32]
    created = create_requests(CreateRequestsCommand(
        creation_key=f"kitset-missing:{canon}:{digest}", drafts=tuple(drafts), actor=command.actor,
        now=command.now, designation_base=kitset.project.strip(), source="kitset-missing", to_procurement=True,
    ), requests_uow())
    result.created = [{
        "art": request.design_article or "", "name": request.name, "quantity": kit_number_payload(request.quantity),
        "unit": request.unit, "legacy_id": request.legacy_id,
    } for request in created]
    return result


def _supply_key(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


# ── Отправить на комплектацию (portalIssueFromKitset) ────────────────────


@dataclass
class IssueFromKitsetResult:
    created: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    # КБ that already have a live request (any status but «Отменена»): {"index", "no", "status"} — not sent again.
    already: list[dict[str, str]] = field(default_factory=list)

    @property
    def message(self) -> str:
        waiting = sum(1 for item in self.created if item["shortfall"])
        return (
            f"Заявок создано: {len(self.created)}"
            + (f", из них ждут поставки: {waiting}" if waiting else "")
            + (f"; уже отправлены: {_sent_list(self.already)}" if self.already else "")
            + (f"; пропущено сборок без состава: {len(self.skipped)}" if self.skipped else "")
            + "."
        )


def _sent_list(already: list[dict[str, str]]) -> str:
    return ", ".join(f"{item['index']} ({item['no']})" for item in already)


def kitset_issue_state(
    kitset: Kitset, fulfillment_uow: Callable[[], FulfillmentUnitOfWork],
) -> list[dict[str, Any]]:
    """What «Отправить на комплектацию» would do, per assembly: its live kit request (number and status) if the КБ
    was sent already and not cancelled, whether it has composition to send."""
    with fulfillment_uow() as uow:
        live = kit_requests_by_kb(uow, kitset.project)
    rows = []
    for assembly in kitset.assemblies:
        key = kit_key(f"{kitset.project} · {assembly.index}")
        request = live.get(key[1]) if key is not None else None
        rows.append({
            "index": assembly.index, "name": assembly.name, "has_items": bool(assembly.components),
            "request": {"id": str(request.id), "no": request.number, "status": request.status.value}
            if request is not None else None,
        })
    return rows


def issue_from_kitset(
    kitset: Kitset, deadline: date | None, actor: Actor, now: datetime,
    fulfillment_uow: Callable[[], FulfillmentUnitOfWork],
) -> IssueFromKitsetResult:
    """One request per assembly: lines with a picked lot are reserved right away (the lot binding splits the need
    when the lot holds less), the rest wait for delivery — the storekeeper binds them after acceptance."""
    if deadline is None:
        raise KitsetValidationError("Укажи дедлайн.")
    if not any(assembly.components for assembly in kitset.assemblies):
        raise KitsetValidationError(f"В наборе «{kitset.project}» нет ни одной сборки с составом.")
    lots: dict[tuple[str, str], Any] = {}
    with fulfillment_uow() as uow:
        # One live request per КБ (prod 180К.11: a second press doubled КБ1 and its reserve): a КБ sent already and
        # not cancelled is skipped and named; the database's partial UNIQUE stops a press racing this one.
        live = kit_requests_by_kb(uow, kitset.project)
        for assembly in kitset.assemblies:
            for component in assembly.components:
                lot = component.lot
                if lot is None:
                    continue
                stock = uow.warehouse.get(lot.lot_id) if lot.lot_id else None
                if stock is None:
                    stock = next((
                        item for item in uow.warehouse.list(limit=50, query=lot.grn)
                        if item.grn == lot.grn and (item.package or "") == lot.package
                    ), None)
                # the project's own «Со склада» reserve counts as free for its kit (bind_lot takes it first)
                if stock is not None and free_for_project(stock, project_reserve_by_lot(uow, [stock], kitset.project)) > 0:
                    lots[(lot.grn, lot.package)] = stock
    result = IssueFromKitsetResult()
    tasks = []
    for task in issue_tasks(kitset, set(lots)):
        key = kit_key(task.purpose)
        sent = live.get(key[1]) if key is not None else None
        if sent is not None:
            result.already.append({"index": task.index, "no": sent.number, "status": sent.status.value})
        elif not task.items:
            result.skipped.append(task.index)
        else:
            tasks.append(task)
    if not tasks and result.already:
        raise DuplicateKitRequest(f"Все КБ проекта {kitset.project} уже отправлены: {_sent_list(result.already)}.")
    for task in tasks:
        lines = []
        bindings = []
        for item in task.items:
            stock = lots.get((item.lot.grn, item.lot.package)) if item.lot else None
            line = FulfillmentLine.create(
                article=item.article, name=item.name, unit=stock.unit if stock is not None else "шт",
                requirement=item.need,
            )
            lines.append(line)
            if stock is not None:
                bindings.append((line.id, stock.id))
        request = create_fulfillment(CreateFulfillmentCommand(
            idempotency_key=f"kitset:{kitset.project}:{task.index}:{uuid4()}",
            purpose=task.purpose, deadline=deadline, lines=tuple(lines), actor=actor, now=now,
        ), fulfillment_uow())
        shortfall = list(task.waiting)
        for line_id, lot_id in bindings:
            try:
                request = bind_lot(BindLotCommand(
                    request_id=request.id, expected_version=request.version, line_id=line_id,
                    lot_id=lot_id, actor=actor, now=now,
                ), fulfillment_uow())
            except (FulfillmentValidationError, KeyError) as error:
                shortfall.append(str(error))
        result.created.append({
            "id": str(request.id), "no": request.number, "index": task.index, "shortfall": shortfall,
        })
    return result


# ── Удаление проекта каскадом (tcKitsetDelete) ──────────────────────────


def delete_kitset(
    project: str, actor: Actor, now: datetime,
    techcards_uow: Callable[[], TechcardsUnitOfWork],
    production_uow: Callable[[], PlanUnitOfWork] | None,
    shipping_uow: Callable[[], ShippingUnitOfWork] | None,
    fulfillment_uow: Callable[[], FulfillmentUnitOfWork] | None = None,
    requests_uow: Callable[[], RequestsUnitOfWork] | None = None,
    procurement_uow: Callable[[], ProcurementUnitOfWork] | None = None,
) -> dict[str, Any]:
    """The card goes everywhere at once: composition and spec, the registry row, issued techcards with their
    revisions, finished-goods rows without movement, and the project's tails that never moved
    (`purge_project_tails`: the project's whole journal in the production plan — blockers, deadlines, «Проверка ТК»,
    «Отметка плана», so a project created again under the number starts clean; tasks without work, kit requests
    without issue, purchase requests with their procurement lines while nothing was ordered, invoiced or paid).
    Anything with movement stays and is named in ``tails.kept``; ``journal.deleted`` counts the removed journal
    rows. КД stays too, as in
    `tcKitsetDelete`: its register rows (with the files) outlive the card and show up again when the project is
    loaded from the calculation anew."""
    project = str(project or "").strip()
    if not project:
        raise KitsetValidationError("Не указан номер проекта")
    with techcards_uow() as uow:
        kitset = uow.kitsets.get(project)
        if kitset is None:
            raise KitsetNotFound(f"Карточка набора «{project}» не найдена")
        lines = kitset.lines
        uow.kitsets.delete(kitset.project)
        canon = project_key(kitset.project)
        cards = revisions = 0
        for name in {card.project for card in uow.techcards.list() if project_key(card.project) == canon}:
            dropped = uow.techcards.delete_project(name)
            cards += dropped[0]
            revisions += dropped[1]
        _audit(uow, kitset.project, "techcards.kitset_deleted", actor, now, {
            "lines": str(lines), "cards": str(cards),
        })
        uow.commit()
    erp: dict[str, int] = {}
    if production_uow is not None:
        canon_plan = normalize_project_number(kitset.project)
        with production_uow() as uow:
            projects = list(uow.plan_projects.list())
        kept = [item for item in projects if normalize_project_number(item.project) != canon_plan]
        if len(kept) != len(projects):
            replace_plan_projects(ReplacePlanProjectsCommand(projects=tuple(kept), actor=actor, now=now), production_uow())
            erp["Проекты"] = len(projects) - len(kept)
    gp = {"deleted": 0, "kept": 0}
    if shipping_uow is not None:
        dropped = drop_project(DropProjectCommand(project=kitset.project, actor=actor, now=now), shipping_uow())
        gp = {"deleted": dropped.deleted, "kept": dropped.kept}
    # Хвосты без движения — отличие от tcKitsetDelete (решено агентом 04.10 — проверить): карточка уже удалена, поэтому
    # сбой здесь не валит удаление, а возвращается текстом; добить можно командой purge_project_tails.
    tails: dict[str, Any] | None = None
    tails_error = ""
    journal = {"deleted": 0}
    if fulfillment_uow is not None or production_uow is not None or requests_uow is not None:
        try:
            purged = purge_project_tails(
                kitset.project, actor, now, fulfillment_uow=fulfillment_uow, production_uow=production_uow,
                requests_uow=requests_uow, procurement_uow=procurement_uow,
            )
            tails = purged.payload()
            journal = {"deleted": purged.count(purged.deleted, TAIL_JOURNAL)}
        except Exception as error:  # noqa: BLE001 — карточка уже снесена, ответ должен дойти
            _log.exception("Хвосты проекта %s не удалены", kitset.project)
            tails_error = f"Журнал, задания, запросы на комплектацию и заявки проекта не удалены: {error}"
    return {
        "project": kitset.project, "deleted": lines, "erp": erp,
        "cards": {"cards": cards, "revisions": revisions}, "gp": gp,
        "journal": journal, "tails": tails, "tails_error": tails_error,
    }


# ── Хвосты проекта без движения (отличие от tcKitsetDelete, решено агентом 04.10 — проверить) ──


TAIL_FULFILLMENT = "fulfillment"
TAIL_PRODUCTION = "production"
TAIL_REQUESTS = "requests"
TAIL_PROCUREMENT = "procurement"
TAIL_JOURNAL = "journal"
TAIL_SECTIONS = (TAIL_FULFILLMENT, TAIL_PRODUCTION, TAIL_REQUESTS, TAIL_PROCUREMENT, TAIL_JOURNAL)
_TAIL_NOUNS = {
    TAIL_FULFILLMENT: ("запрос на комплектацию", "запроса на комплектацию", "запросов на комплектацию"),
    TAIL_PRODUCTION: ("производственное задание", "производственных задания", "производственных заданий"),
    TAIL_REQUESTS: ("заявка на закупку", "заявки на закупку", "заявок на закупку"),
    TAIL_PROCUREMENT: ("строка закупки", "строки закупки", "строк закупки"),
    TAIL_JOURNAL: ("запись журнала проекта", "записи журнала проекта", "записей журнала проекта"),
}
# Заявка, по которой уже везут или привезли, остаётся при любых строках закупки.
_REQUEST_DELIVERED = frozenset({RequestStatus.IN_TRANSIT, RequestStatus.CLOSED})
# Собранный, но не выданный комплект физически лежит в коробке: удалить его молча — потерять след на полке.
_PICKED = frozenset({FulfillmentStatus.COMPLETE, FulfillmentStatus.READY})


def _plural(count: int, forms: tuple[str, str, str]) -> str:
    tail = count % 100
    if 11 <= tail <= 14:
        form = forms[2]
    else:
        form = forms[0] if count % 10 == 1 else forms[1] if 2 <= count % 10 <= 4 else forms[2]
    return f"{count} {form}"


@dataclass(frozen=True)
class TailEntry:
    section: str
    label: str
    reason: str = ""


@dataclass
class ProjectTails:
    """What a project's purge removed (or would remove, on a dry run) and what stayed with a reason."""

    project: str
    applied: bool
    deleted: list[TailEntry] = field(default_factory=list)
    kept: list[TailEntry] = field(default_factory=list)
    freed: int = 0

    def count(self, entries: list[TailEntry], section: str) -> int:
        return sum(1 for entry in entries if entry.section == section)

    @property
    def message(self) -> str:
        removed = [
            _plural(self.count(self.deleted, section), _TAIL_NOUNS[section])
            for section in TAIL_SECTIONS if self.count(self.deleted, section)
        ]
        verb = "Удалено" if self.applied else "Будет удалено"
        text = f"{verb}: {', '.join(removed)}" if removed else (
            "Удалять нечего: журнала, заданий, запросов на комплектацию и заявок без движения нет"
        )
        if self.freed:
            text += f"; резерв снят по {self.freed} {'строке' if self.freed % 10 == 1 and self.freed % 100 != 11 else 'строкам'}"
        groups: dict[tuple[str, str], list[str]] = {}
        for entry in self.kept:
            groups.setdefault((entry.section, entry.reason), []).append(entry.label)
        if groups:
            text += ". Осталось: " + "; ".join(
                f"{_plural(len(labels), _TAIL_NOUNS[section])} — {reason} ({', '.join(labels)})"
                for (section, reason), labels in groups.items()
            )
        return text + "."

    def payload(self) -> dict[str, Any]:
        return {
            **{section: {"deleted": self.count(self.deleted, section), "kept": self.count(self.kept, section)}
               for section in TAIL_SECTIONS},
            "freed": self.freed,
            "kept": [{"section": entry.section, "label": entry.label, "reason": entry.reason} for entry in self.kept],
            "message": self.message,
        }


def _pages(read: Callable[[UUID | None], list], size: int = 500) -> list:
    items: list = []
    after = None
    while True:
        page = list(read(after))
        items.extend(page)
        if len(page) < size:
            return items
        after = page[-1].id


def _journal_label(event: PlanEvent) -> str:
    """One journal row as the card's «Журнал» shows it: «30.09.2026 · Проверка ТК · КБ1 · проверена»."""
    text = " ".join(str(event.text or "").split())
    parts = (
        f"{event.occurred_at:%d.%m.%Y}", event.kind, event.item_index, event.value,
        text if len(text) <= 60 else text[:59] + "…",
    )
    return " · ".join(part for part in parts if part)


def request_matches_project(canon: str, request: Request) -> bool:
    """Заявка проекта — по № проекта, по цели или по обозначению «проект.NNN», как `project_request_count`."""
    return canon in (
        normalize_project_number(request.project_reference) if request.project_reference else "",
        project_from_purpose(request.purpose),
        project_from_purpose(request.designation),
    )


def purge_project_tails(
    project: str, actor: Actor, now: datetime, *,
    fulfillment_uow: Callable[[], FulfillmentUnitOfWork] | None = None,
    production_uow: Callable[[], Any] | None = None,
    requests_uow: Callable[[], RequestsUnitOfWork] | None = None,
    procurement_uow: Callable[[], ProcurementUnitOfWork] | None = None,
    apply: bool = True,
) -> ProjectTails:
    """Tails of a deleted project that never moved go; anything with movement stays and is named with a reason.

    The project's journal in the production plan goes whole — blockers, deadlines, comments, «Проверка ТК»,
    «Отметка плана» — so a project created again under the same number does not show the old one (owner 04.10).
    Production tasks without recorded work (no box, session, output, defect, «взял/закончил») and not handed to a
    site whole; kit requests with nothing issued and not picked — their reserve goes back to the lot; purchase
    requests together with all their procurement lines while nothing moved there (`removal_blocker`: no supplier
    order, invoice, КП money, payment, receipt or «Со склада» reserve — owner 04.10), or still «Новый» with no
    line at all. The project is matched exactly after normalisation
    (`normalize_project_number` / `project_from_purpose`), so «719К.2» never touches «719К.21». The same code path
    serves the cascade of `delete_kitset` and the `purge_project_tails` command (``apply=False`` — dry run)."""
    canon = normalize_project_number(project)
    if not canon:
        raise KitsetValidationError("Не указан номер проекта")
    tails = ProjectTails(project=str(project).strip(), applied=apply)
    busy_requests: set[UUID] = set()

    if production_uow is not None:
        # Журнал — первым: от него зависит, покажет ли заново заведённый проект старую историю; остальное может сорваться.
        events = delete_project_plan_events(
            DeleteProjectPlanEventsCommand(project=canon, actor=actor, now=now, apply=apply), production_uow(),
        )
        tails.deleted.extend(TailEntry(TAIL_JOURNAL, _journal_label(event)) for event in events)
        with production_uow() as uow:
            tasks = sorted((task for task in _pages(lambda after: uow.production.list(limit=500, after_id=after))
                            if normalize_project_number(task.project) == canon), key=lambda task: task.number)
            worked = uow.production.worked([task.id for task in tasks]) if tasks else set()
            for task in tasks:
                label = f"{task.number} · {task.item}"
                if task.id in worked:
                    reason = "отмечена работа"
                elif task.handed_to:
                    reason = f"отдано в {task.handed_to}, там работу в ERP не отмечают"
                else:
                    tails.deleted.append(TailEntry(TAIL_PRODUCTION, label))
                    if apply:
                        uow.production.delete(task.id)
                        uow.audit.add(AuditEvent(
                            id=uuid4(), aggregate_id=task.id, event_type="production.task_deleted", actor=actor,
                            occurred_at=now, details={"project": task.project, "number": task.number,
                                                      "item": task.item, "reason": "project_deleted"},
                        ))
                    continue
                tails.kept.append(TailEntry(TAIL_PRODUCTION, label, reason))
                if task.fulfillment_request_id is not None:
                    busy_requests.add(task.fulfillment_request_id)
            if apply and tasks:
                uow.commit()

    if fulfillment_uow is not None:
        with fulfillment_uow() as uow:
            requests = [request for request in _pages(lambda after: uow.fulfillment.list(limit=500, after_id=after))
                        if project_from_purpose(request.purpose) == canon]
        for request in requests:
            label = f"{request.number} · {request.purpose}"
            if any(line.issued > 0 for line in request.lines):
                reason = "по нему уже выдано со склада"
            elif request.status in _PICKED:
                reason = "комплект уже собран, разберите его в «Комплектации»"
            elif request.id in busy_requests:
                reason = "к нему привязано задание с отмеченной работой"
            else:
                if not apply:
                    tails.deleted.append(TailEntry(TAIL_FULFILLMENT, label))
                    tails.freed += sum(1 for line in request.lines if line.lot is not None and line.reserved > 0)
                    continue
                try:
                    deleted = delete_fulfillment(DeleteFulfillmentCommand(request.id, actor, now), fulfillment_uow())
                except (ValueError, KeyError) as error:
                    reason = f"не удалось удалить: {error}"
                else:
                    tails.deleted.append(TailEntry(TAIL_FULFILLMENT, label))
                    tails.freed += deleted.freed
                    continue
            tails.kept.append(TailEntry(TAIL_FULFILLMENT, label, reason))

    if requests_uow is not None:
        _purge_requests(canon, tails, actor, now, requests_uow, procurement_uow, apply)
    return tails


def _request_blocker(request: Request, lines: RequestLines | None, procurement_seen: bool) -> str | None:
    """Why a purchase request of a deleted project stays; None — it goes (with `lines`, if any)."""
    if request.status in _REQUEST_DELIVERED:
        return f"уже в закупке («{request.status.value}»)"
    if lines is not None:
        return f"в закупке {lines.blocker}" if lines.blocker else None
    if request.status is RequestStatus.NEW:
        return None
    if procurement_seen and request.status is RequestStatus.IN_WORK:
        # Передана, но строка ещё не создана (событие в очереди): удалить заявку сейчас — строка придёт без неё.
        return "передана в закупку, строки закупки ещё нет — удалите позже"
    return f"уже в закупке («{request.status.value}»)"


def _purge_requests(
    canon: str, tails: ProjectTails, actor: Actor, now: datetime,
    requests_uow: Callable[[], RequestsUnitOfWork],
    procurement_uow: Callable[[], ProcurementUnitOfWork] | None,
    apply: bool,
) -> None:
    """Purchase requests go with their procurement lines: the lines first (one procurement transaction, re-checked
    there), then the requests whose version did not move meanwhile (a re-transfer keeps its request)."""
    with requests_uow() as uow:
        requests = sorted((request for request in uow.requests.recent(1_000_000)
                           if request_matches_project(canon, request)), key=lambda request: request.legacy_id)
    if not requests:
        return

    def lines_of(ids: list[UUID], write: bool) -> dict[UUID, RequestLines]:
        return remove_untouched_request_lines(RemoveRequestLinesCommand(
            request_ids=tuple(ids), actor=actor, now=now, project=tails.project, apply=write,
        ), procurement_uow())

    found = lines_of([request.id for request in requests], False) if procurement_uow is not None else {}
    going: list[Request] = []
    for request in requests:
        reason = _request_blocker(request, found.get(request.id), procurement_uow is not None)
        if reason is None:
            going.append(request)
        else:
            tails.kept.append(TailEntry(TAIL_REQUESTS, f"{request.legacy_id} · {request.name}", reason))
    if not going:
        return
    removed = found
    raced = False
    with_lines = [request.id for request in going if request.id in found]
    if apply and with_lines:
        try:
            removed = lines_of(with_lines, True)
        except ConcurrentProcurementUpdate:
            raced = True
    with requests_uow() as uow:
        for request in going:
            label = f"{request.legacy_id} · {request.name}"
            if raced and request.id in found:
                tails.kept.append(TailEntry(TAIL_REQUESTS, label, "строки закупки изменили во время удаления — повторите"))
                continue
            lines = removed.get(request.id) if request.id in found else None
            if lines is not None and lines.blocker:
                tails.kept.append(TailEntry(TAIL_REQUESTS, label, f"в закупке {lines.blocker}"))
                continue
            tails.deleted.extend(TailEntry(TAIL_PROCUREMENT, line) for line in (lines.lines if lines else ()))
            if apply:
                current = uow.requests.get(request.id)
                if current is None:
                    continue
                if current.version != request.version:
                    tails.kept.append(TailEntry(TAIL_REQUESTS, label, "заявку изменили во время удаления — повторите"))
                    continue
            tails.deleted.append(TailEntry(TAIL_REQUESTS, label))
            if apply:
                uow.requests.delete(request.id)
                uow.audit.add(AuditEvent(
                    id=uuid4(), aggregate_id=request.id, event_type="request.deleted", actor=actor,
                    occurred_at=now, details={"project": tails.project, "legacy_id": request.legacy_id,
                                              "designation": request.designation, "reason": "project_deleted"},
                ))
        if apply:
            uow.commit()


# ── КД (TcKd.gs) ────────────────────────────────────────────────────────


def attach_document(
    project: str, index: str, file_name: str, content_type: str, content: bytes, actor: Actor, now: datetime,
    techcards_uow: Callable[[], TechcardsUnitOfWork], audit_extra: dict[str, str] | None = None,
) -> KitDocument:
    """`tcKdAdd`: the file name carries the product id in front («708К.4КБ1 — …») so it is recognised outside
    the card too — in search, mail, print. The bytes live in the ERP database: the owner's decision of 29.09 is
    to leave Google Drive (SUPPLYMAP TcKd.gs kept them in «Проекты/<проект>/КД»)."""
    index = str(index or "").strip().upper()
    if not index:
        raise KitsetValidationError("Не указана сборка")
    if not content:
        raise KitsetValidationError("Файл не передан")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise KitsetValidationError("Файл КД больше 20 МБ")
    with techcards_uow() as uow:
        kitset = uow.kitsets.get(project)
        if kitset is None:
            raise KitsetNotFound(f"Карточка набора «{project}» не найдена")
        if kitset.assembly(index) is None:
            raise KitsetValidationError(f"В наборе «{kitset.project}» нет сборки {index}")
        document = KitDocument(
            id=uuid4(), project=kitset.project, item_index=index,
            file_name=f"{item_id(kitset.project, index)} — {str(file_name or '').strip() or 'КД'}",
            content_type=stored_content_type(content),
            size=len(content), uploaded_at=now, uploaded_by=actor.name,
        )
        uow.kitsets.add_document(document, content)
        _audit(uow, kitset.project, "techcards.kitset_document_added", actor, now, {
            "item_index": index, "file": document.file_name, "size": str(document.size), **(audit_extra or {}),
        })
        uow.commit()
    return document


def detach_document(
    project: str, document_id: UUID, actor: Actor, now: datetime,
    techcards_uow: Callable[[], TechcardsUnitOfWork],
) -> int:
    """`tcKdDelete`: the register row goes with its bytes. A historical row (only a Drive link, from before the
    move to the database) goes the same way — nothing on Drive is touched."""
    with techcards_uow() as uow:
        kitset = uow.kitsets.get(project)
        if kitset is None:
            raise KitsetNotFound(f"Карточка набора «{project}» не найдена")
        document = uow.kitsets.document(kitset.project, document_id)
        if document is None:
            return 0
        deleted = uow.kitsets.delete_document(kitset.project, document_id)
        _audit(uow, kitset.project, "techcards.kitset_document_removed", actor, now, {
            "item_index": document.item_index, "file": document.file_name,
        })
        uow.commit()
    return deleted


def kit_number_payload(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


__all__ = [
    "LoadKitsetCommand", "LoadKitsetResult", "KitsetFacts", "IssueFromKitsetResult", "PLAN_KINDS",
    "ChangeProjectStatusCommand", "change_project_status",
    "ChangeProjectStageCommand", "ProjectStageChange", "ProjectStageState", "change_project_stage",
    "load_project_stage_state", "preview_project_stage", "project_stage_state", "stage_change_payload",
    "TC_REVIEW_VALUES", "attach_document", "calc_ops_from_grid", "cards_state", "delete_kitset", "detach_document",
    "assembly_calc_ops", "bom_names", "generator_bom", "issue_from_kitset", "kitset_issue_state", "read_calc_ops", "kit_number_payload", "kitset_facts", "kitset_stages", "closed_kb_keys", "CLOSED_KB_STAGES", "kitset_statuses", "load_kitset", "register_project", "supply_lines",
    "supply_rows", "ProjectTails", "TailEntry", "TAIL_JOURNAL", "TAIL_SECTIONS", "purge_project_tails", "request_matches_project", "KitsetRequestsCommand", "KitsetRequestsResult", "create_kitset_requests", "requests_calc_reader",
]

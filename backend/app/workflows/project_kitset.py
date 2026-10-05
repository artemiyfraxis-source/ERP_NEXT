"""Project kitset card across modules — SUPPLYMAP TcKitset.gs, TcKd.gs, Issue.gs `portalIssueFromKitset`.

The kitset itself lives in Techcards. Creating it also registers the project for the production plan
(`tcKitsetSyncErp_`), queues finished goods per КБ (`shipQueueForProject_`, ADR-0016), remembers the shipping
addressee and the deadline. None of those may fail the creation: the card is needed for composition and
techcards, the side effects report their own errors (`erpError`, `shipError`, …) as SUPPLYMAP does.
"""

from __future__ import annotations

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
    project_reserve_by_lot,
)
from app.modules.fulfillment.domain import FulfillmentLine, FulfillmentRequest, FulfillmentStatus, FulfillmentValidationError
from app.modules.procurement.application import (
    ConcurrentProcurementUpdate,
    RemoveRequestLinesCommand,
    RequestLines,
    UnitOfWork as ProcurementUnitOfWork,
    remove_untouched_request_lines,
)
from app.modules.procurement.domain import ProcurementLine
from app.modules.production.domain import ProductionTask
from app.modules.production.plan import (
    KIT_STAGES,
    PLAN_KINDS,
    PLAN_STAGE_AUTO,
    TC_REVIEW_VALUES,
    PlanEvent,
    PlanProjectSnapshot,
    PROJECT_TERMINALS,
    project_terminal,
    ProjectStage,
    normalize_project_number,
    project_from_purpose,
    project_stage,
    summarize_journal,
    summarize_kit_marks,
)
from app.modules.production.plan_application import (
    AddPlanEventCommand,
    DeleteProjectPlanEventsCommand,
    PlanUnitOfWork,
    ReplacePlanProjectsCommand,
    add_plan_event,
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
    CalcImport,
    CalcImportItem,
    Request,
    RequestStatus,
    ValidationError as RequestValidationError,
    build_import_drafts,
    parse_calc_number,
    spreadsheet_id_from_url,
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
from app.modules.techcards.etk_parts import kit_article
from app.modules.techcards.kitset_calc import PROJECT_SHEET, SPEC_SHEET, parse_calc_sheets
from app.modules.techcards.kitsets import (
    CalcKitset,
    KitAssembly,
    KitDocument,
    Kitset,
    KitsetNotFound,
    KitsetValidationError,
    SupplyRow,
    issue_tasks,
    item_id,
    project_key,
    project_number_valid,
    supply_lines,
    write_from_calc,
)
from app.modules.techcards.project_cards import ProjectTechcard, TechcardStatus
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.workflows.production_plan import kit_stage_facts


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


def generator_bom(kitset: Kitset, assembly: KitAssembly, calc_ops: list[dict[str, str]]) -> dict[str, Any]:
    """`tcLoadBomFromKit`: состав сборки для генератора — из сохранённой карточки, с правками оператора.
    Аналог по КД → оригинал к применению (kitUseArt_), заменённый в расчёте провод → провод из «Наименования»
    (`kit_article`): генератор подбирает контакт и сечение по тому, что применяется; артикул КД — в `kd`.
    Состав читается из карточки при каждом открытии, так что правка расчёта доходит до генератора без пересборки.
    `calc_ops` — строки листа КБn расчёта (`calc_ops_by_link`). Тот же ответ читает пакетная генерация."""
    components = []
    for position, component in enumerate(assembly.components):
        use = kit_article(component.article, component.name)
        components.append({
            "id": f"kit-{position}", "type": component.type,
            "name": use["art"] if use["kd"] else (component.name or component.article),
            "art": use["art"], "kd": use["kd"], "qty": kit_number_payload(component.per_unit or Decimal("1")),
            "scrap": kit_number_payload(component.scrap_pct / 100),
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
        designation_base=command.project.strip(), source="import",
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


def kitset_facts(techcards, production=None, fulfillment=None, shipping=None) -> KitsetFacts:
    """Facts for `kitset_stages` from the modules' unit-of-work factories; a missing optional module reads empty."""
    def read(factory, reader, default=()):
        if factory is None:
            return default
        with factory() as uow:
            return reader(uow)

    tasks, events, projects = read(production, lambda uow: (
        tuple(uow.production.list(limit=10_000)), tuple(uow.plan_events.list()), tuple(uow.plan_projects.list()),
    ), ((), (), ()))
    return KitsetFacts(
        cards=read(techcards, lambda uow: tuple(uow.techcards.list())),
        tasks=tasks,
        fulfillment=read(fulfillment, lambda uow: tuple(uow.fulfillment.list(limit=10_000))),
        goods=read(shipping, lambda uow: tuple(uow.goods.list(limit=10_000))),
        events=events,
        projects=projects,
    )


def kitset_stages(kitsets: Iterable[Kitset], facts: KitsetFacts) -> dict[str, tuple[ProjectStage, str]]:
    """{project: (stage, active blocker type)} — the same ladder the production plan uses."""
    marks = summarize_kit_marks(facts.events)
    stage_facts, marks = kit_stage_facts(
        cards=facts.cards, tasks=facts.tasks, fulfillment=facts.fulfillment, goods=facts.goods, marks=marks,
    )
    journal = summarize_journal(facts.events)
    snapshots = {normalize_project_number(item.project): item for item in facts.projects}
    result = {}
    for kitset in kitsets:
        canon = normalize_project_number(kitset.project)
        stage = project_stage(
            kitset.project, [assembly.index for assembly in kitset.assemblies],
            stage_facts, project_terminal(snapshots.get(canon), journal.get(canon)), marks,
        )
        entry = journal.get(canon)
        blocker = entry.blocker.type if entry is not None and entry.blocker is not None else ""
        result[kitset.project] = (stage, blocker)
    return result


PROJECT_BLOCK = "block"
PROJECT_UNBLOCK = "unblock"
PROJECT_STATUS = "status"


@dataclass(frozen=True)
class ChangeProjectStatusCommand:
    """Смена статуса проекта одним действием — из карточки, плитки и «Производственного плана».

    ``action``: "status" — ``value`` этап лестницы (на все КБ), «авто» (снять ручные этапы) или
    Отказ/Слился/Отменён; "block" — блокер ``value`` (ХОЛД, СТОП…) с причиной ``text``; "unblock" — снять блокер.
    """

    project: str
    item_indexes: tuple[str, ...]
    action: str
    value: str
    actor: Actor
    now: datetime
    text: str = ""


def change_project_status(
    command: ChangeProjectStatusCommand, uow_factory: Callable[[], PlanUnitOfWork],
) -> list[PlanEvent]:
    """Единая точка смены статуса проекта: пишет записи журнала проекта, как «Производственный план» SUPPLYMAP.

    Этап лестницы у закрытого проекта сначала возвращает его в работу. «Отгружено» по факту отгрузки (ГП отгружено ≥
    партии) ставится само и перекрывает ручной этап — вызывать эту функцию из отгрузки не нужно.
    """
    project, value, known = command.project, str(command.value or "").strip(), (command.project,)

    def add(kind: str, *, item_index: str = "", event_value: str = "", blocker: str = "", text: str = "") -> PlanEvent:
        return add_plan_event(AddPlanEventCommand(
            project=project, kind=kind, blocker=blocker, text=text, deadline=None, known_projects=known,
            actor=command.actor, now=command.now, item_index=item_index, value=event_value,
        ), uow_factory())

    if command.action == PROJECT_BLOCK:
        return [add("block", blocker=value, text=command.text)]
    if command.action == PROJECT_UNBLOCK:
        return [add("unblock", text=command.text)]
    if command.action != PROJECT_STATUS:
        raise KitsetValidationError("Неизвестное действие со статусом проекта.")
    if value in PROJECT_TERMINALS:
        return [add("status", event_value=value, text=command.text)]
    if value not in (*KIT_STAGES, PLAN_STAGE_AUTO):
        raise KitsetValidationError(f"Неизвестный статус проекта: {value}")
    canon = normalize_project_number(project)
    with uow_factory() as uow:
        snapshot = next((item for item in uow.plan_projects.list() if normalize_project_number(item.project) == canon), None)
        journal = summarize_journal(uow.plan_events.list(project=canon)).get(canon)
    events = [add("status", event_value=PLAN_STAGE_AUTO)] if project_terminal(snapshot, journal) else []
    # Без КБ этап ставится на весь проект (запись с пустым КБ), как в «Производственном плане».
    for index in command.item_indexes or ("",):
        events.append(add("stage", item_index=index, event_value=value))
    return events


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


def supply_rows(
    project: str, requests: Iterable[Request], procurement: Iterable[ProcurementLine],
) -> list[SupplyRow]:
    """The purchase chain of the project: Запрос → Закупка → Приёмка. SUPPLYMAP ties it by ЦЕЛЬ (project number)
    and АРТИКУЛ КД; here by the request's project reference (or purpose) and design article. A procurement line
    in transit, quarantine or done is at acceptance; «Готово» means it was accepted with a ГРН."""
    canon = normalize_project_number(project)
    by_request: dict[UUID, str] = {}
    rows = []
    for request in requests:
        target = (
            normalize_project_number(request.project_reference)
            if request.project_reference else project_from_purpose(request.purpose)
        )
        if target != canon:
            continue
        article = str(request.design_article or "").strip()
        by_request[request.id] = article
        rows.append(SupplyRow(article=article, where="Запрос", status=request.status.value,
                              source_id=request.legacy_id or str(request.id)))
    for line in procurement:
        target = normalize_project_number(line.project_reference) if line.project_reference else ""
        if line.source_request_id not in by_request and target != canon:
            continue
        # Артикул мог остаться только в заявке — тянем его вперёд по связи строки с заявкой.
        article = str(line.design_article or "").strip() or by_request.get(line.source_request_id, "")
        status = line.status.value
        if status == "Отменено":
            continue
        where = "Приёмка" if status in _RECEIVING_STATUSES else "Закупка"
        rows.append(SupplyRow(
            article=article, where=where, status=status, source_id=line.legacy_id or str(line.id),
            received=status == "Готово",
        ))
    return rows


# ── Отправить на комплектацию (portalIssueFromKitset) ────────────────────


@dataclass
class IssueFromKitsetResult:
    created: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        waiting = sum(1 for item in self.created if item["shortfall"])
        return (
            f"Заявок создано: {len(self.created)}"
            + (f", из них ждут поставки: {waiting}" if waiting else "")
            + (f"; пропущено сборок без состава: {len(self.skipped)}" if self.skipped else "")
            + "."
        )


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
    for task in issue_tasks(kitset, set(lots)):
        if not task.items:
            result.skipped.append(task.index)
            continue
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
    techcards_uow: Callable[[], TechcardsUnitOfWork],
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
            content_type=str(content_type or "").strip() or "application/octet-stream",
            size=len(content), uploaded_at=now, uploaded_by=actor.name,
        )
        uow.kitsets.add_document(document, content)
        _audit(uow, kitset.project, "techcards.kitset_document_added", actor, now, {
            "item_index": index, "file": document.file_name, "size": str(document.size),
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
    "TC_REVIEW_VALUES", "attach_document", "calc_ops_from_grid", "cards_state", "delete_kitset", "detach_document",
    "assembly_calc_ops", "generator_bom", "issue_from_kitset", "read_calc_ops", "kit_number_payload", "kitset_facts", "kitset_stages", "kitset_statuses", "load_kitset", "register_project", "supply_lines",
    "supply_rows", "ProjectTails", "TailEntry", "TAIL_JOURNAL", "TAIL_SECTIONS", "purge_project_tails", "request_matches_project", "KitsetRequestsCommand", "KitsetRequestsResult", "create_kitset_requests", "requests_calc_reader",
]

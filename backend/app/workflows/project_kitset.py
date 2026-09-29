"""Project kitset card across modules — SUPPLYMAP TcKitset.gs, TcKd.gs, Issue.gs `portalIssueFromKitset`.

The kitset itself lives in Techcards. Creating it also registers the project for the production plan
(`tcKitsetSyncErp_`), queues finished goods per КБ (`shipQueueForProject_`, ADR-0016), remembers the shipping
addressee and the deadline. None of those may fail the creation: the card is needed for composition and
techcards, the side effects report their own errors (`erpError`, `shipError`, …) as SUPPLYMAP does.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Protocol
from uuid import UUID, uuid4

from app.modules.fulfillment.application import (
    BindLotCommand,
    CreateFulfillmentCommand,
    UnitOfWork as FulfillmentUnitOfWork,
    bind_lot,
    create_fulfillment,
)
from app.modules.fulfillment.domain import FulfillmentLine, FulfillmentRequest, FulfillmentValidationError
from app.modules.procurement.domain import ProcurementLine
from app.modules.production.domain import ProductionTask
from app.modules.production.plan import (
    KIT_STAGES,
    PLAN_KINDS,
    TC_REVIEW_VALUES,
    PlanEvent,
    PlanProjectSnapshot,
    ProjectStage,
    normalize_project_number,
    project_from_purpose,
    project_stage,
    summarize_journal,
    summarize_kit_marks,
)
from app.modules.production.plan_application import (
    AddPlanEventCommand,
    PlanUnitOfWork,
    ReplacePlanProjectsCommand,
    add_plan_event,
    replace_plan_projects,
)
from app.modules.requests.application import CalcSheetError
from app.modules.requests.domain import Request, spreadsheet_id_from_url
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
from app.modules.techcards.kitset_calc import PROJECT_SHEET, SPEC_SHEET, parse_calc_sheets
from app.modules.techcards.kitsets import (
    CalcKitset,
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
TERMINAL_STATUSES = ("Отказ", "Слился", "Отменён")
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
    return parse_calc_sheets(sheets, title, link)


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
    return result


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


def kitset_stages(kitsets: Iterable[Kitset], facts: KitsetFacts) -> dict[str, tuple[ProjectStage, str]]:
    """{project: (stage, active blocker type)} — the same ladder the production plan uses."""
    marks = summarize_kit_marks(facts.events)
    stage_facts, marks = kit_stage_facts(
        cards=facts.cards, tasks=facts.tasks, fulfillment=facts.fulfillment, goods=facts.goods, marks=marks,
    )
    journal = summarize_journal(facts.events)
    terminals = {
        normalize_project_number(item.project): item.status
        for item in facts.projects if item.terminal and item.status
    }
    result = {}
    for kitset in kitsets:
        canon = normalize_project_number(kitset.project)
        stage = project_stage(
            kitset.project, [assembly.index for assembly in kitset.assemblies],
            stage_facts, terminals.get(canon, ""), marks,
        )
        entry = journal.get(canon)
        blocker = entry.blocker.type if entry is not None and entry.blocker is not None else ""
        result[kitset.project] = (stage, blocker)
    return result


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
                if stock is not None and stock.available > 0:
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
) -> dict[str, Any]:
    """The card goes everywhere at once: composition and spec, KD, the registry row, issued techcards with their
    revisions, finished-goods rows without movement. Left alone: shop tasks (people's work), purchase requests,
    fulfillment requests and finished goods that already moved — those belong to their own sections."""
    project = str(project or "").strip()
    if not project:
        raise KitsetValidationError("Не указан номер проекта")
    with techcards_uow() as uow:
        kitset = uow.kitsets.get(project)
        if kitset is None:
            raise KitsetNotFound(f"Карточка набора «{project}» не найдена")
        lines = kitset.lines
        documents = uow.kitsets.delete_documents(kitset.project)
        uow.kitsets.delete(kitset.project)
        canon = project_key(kitset.project)
        cards = revisions = 0
        for name in {card.project for card in uow.techcards.list() if project_key(card.project) == canon}:
            dropped = uow.techcards.delete_project(name)
            cards += dropped[0]
            revisions += dropped[1]
        _audit(uow, kitset.project, "techcards.kitset_deleted", actor, now, {
            "lines": str(lines), "cards": str(cards), "documents": str(documents),
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
    return {
        "project": kitset.project, "deleted": lines, "erp": erp,
        "cards": {"cards": cards, "revisions": revisions}, "kd": documents, "gp": gp,
    }


# ── КД (TcKd.gs) ────────────────────────────────────────────────────────


def attach_document(
    project: str, index: str, file_name: str, content_type: str, content: bytes, actor: Actor, now: datetime,
    techcards_uow: Callable[[], TechcardsUnitOfWork],
) -> KitDocument:
    """`tcKdAdd`: the file name carries the product id in front («708К.4КБ1 — …») — it is recognised outside
    the card too. Files live in ERP itself: there is no Drive after Google."""
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
            "item_index": index, "file": document.file_name,
        })
        uow.commit()
    return document


def kit_number_payload(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


__all__ = [
    "LoadKitsetCommand", "LoadKitsetResult", "KitsetFacts", "IssueFromKitsetResult", "PLAN_KINDS",
    "TC_REVIEW_VALUES", "attach_document", "cards_state", "delete_kitset", "issue_from_kitset",
    "kit_number_payload", "kitset_stages", "kitset_statuses", "load_kitset", "register_project", "supply_lines",
    "supply_rows",
]

"""HTTP: machine speed — «норма vs факт» per setup and norm suggestions (plan machine-speed-history S2). Rules and
formulas: production/machine_speed.py; the history: production/machine_history.py.

- `GET /api/production/machine-speed?equipment_id&since&until` (the «Станки» view right) → `{since, until, can_accept,
  machines, rows}`: per machine and setup key — runs, pieces, running time, fact and norm s/pc (the БД.ОП CYCLOGRAM for
  the setup's length), setup fact / norm, stops by reason and, once per БД.ОП row (the runs of every machine in the
  report pooled; shown on the setup with the most pieces), a suggestion of new cyclogram cells. since / until — Moscow
  days, inclusive; default the last 30 days (31 days with today); at most MAX_DAYS days. `machines` — the ОБ.019 machine
  links (the machines that write the history).
- `GET /api/production/machine-speed/runs?equipment_id&setup_key&since&until` → `{runs}`: the segments of that setup that
  made pieces (those counted in «Прогонов»).
- `POST /api/production/machine-speed/norm-suggestions/accept` (the «Справочник» edit right) `{record_id, cells:
  [{field, old, new}], equipment_id, setup_key, since, until}` → only «Скорость проката» / «Скорость работы
  инструмента», each exactly as the suggestion recomputed now for that row and period (the machine's report, else the
  all-machines one) — otherwise, or with no suggestion, 409 «Предложение изменилось — обновите отчёт». The cells and the
  recomputed «Время Чел» / «Время машины» / «Время операции» (frontend opFields.withCycleTimes) are written through
  references edit_record in one transaction with the audit `production.machine_norm_accepted` (evidence of the
  period); an old value that no longer matches → 409, nothing written.
  The edit is a БД.ОП write, so it queues the ETK refresh (workflows/etk_refresh, `techcard_refresh_requests`): with
  ETK_AUTO_REVISION=1 (production) every issued ETK with that operation gets a new revision by itself. Each suggestion
  says so in `etk_note`.

Operation of a setup: a cutting run's ETK operation (the frozen recipe's wire cut code; with no БД.ОП row of that code
the norm comes from the operation's `linearTiming`), otherwise the БД.ОП row CUT_WIRE+{n}strip+{m}mark of the setup.
History rows are streamed (`yield_per`), never loaded whole."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.modules.production import machine_runs as runs
from app.modules.production import machine_speed as speed
from app.modules.production import machines as rules
from app.modules.production.api import production_uow_factory
from app.modules.production.autoplan_api import references_uow_factory
from app.modules.production.cutting_infrastructure import CuttingGroupRow
from app.modules.production.machine_history import MachineSpeedRow
from app.modules.production.machine_writes import MachineWriteRow
from app.modules.production.manual_jobs import ManualJobRow
from app.modules.references.application import EditRecordCommand, FieldEdit, edit_record
from app.modules.references.domain import ReferenceValidationError, StaleRecordError, TechOperationTab
from app.shared.clock import MOSCOW, moscow_now
from app.shared.events import AuditEvent
from app.shared.http_auth import Principal, references_edit_principal

from .machines_api import Reader, _equipment, _inventory, _name

router = APIRouter(prefix="/api/production/machine-speed", tags=["production"])
Factory = Annotated[Callable, Depends(production_uow_factory)]
References = Annotated[Callable[[], Any] | None, Depends(references_uow_factory)]
Now = Annotated[datetime, Depends(moscow_now)]
# The «Справочник» edit right — the same gate as a БД.ОП cell edit (a refs:write token passes it, as it passes that).
Editor = Annotated[Principal, Depends(references_edit_principal)]

DEFAULT_DAYS = 30
# Two months, inclusive: a running ОБ.019 writes up to ~14k rows a shift, so a period is at most ~1–2 million rows
# streamed through Python.
MAX_DAYS = 62
STREAM = 5000
WIRE_CUTS = ("cutWire", "cutWireStrip")
ACCEPTABLE = ("roll", "tool")
CHANGED = "Предложение изменилось — обновите отчёт"
ETK_NOTE = "Выпущенные ЭТК с операцией {code} получат новую ревизию автоматически"
COLUMNS = (MachineSpeedRow.id, MachineSpeedRow.equipment_id, MachineSpeedRow.recorded_at, MachineSpeedRow.boot,
           MachineSpeedRow.counter_total, MachineSpeedRow.state_code, MachineSpeedRow.alarm_code,
           MachineSpeedRow.run_write_id, MachineSpeedRow.run_kind, MachineSpeedRow.setup_key, MachineSpeedRow.reason)


def _period(since: date | None, until: date | None, now: datetime) -> tuple[date, date, datetime, datetime]:
    until = until or runs.aware(now).astimezone(MOSCOW).date()
    since = since or until - timedelta(days=DEFAULT_DAYS)
    if since > until:
        raise HTTPException(status_code=422, detail="Начало периода позже конца")
    if (until - since).days + 1 > MAX_DAYS:
        raise HTTPException(status_code=422, detail=f"Период — не больше {MAX_DAYS} дней")
    return since, until, datetime.combine(since, time(), MOSCOW), datetime.combine(until + timedelta(days=1), time(), MOSCOW)


def _window(query, equipment_id: UUID | None, start: datetime, end: datetime):
    query = query.where(MachineSpeedRow.recorded_at >= start, MachineSpeedRow.recorded_at < end)
    return query if equipment_id is None else query.where(MachineSpeedRow.equipment_id == equipment_id)


def _stream(session, equipment_id: UUID | None, start: datetime, end: datetime):
    """The window's rows, each machine's in time order, fetched STREAM at a time."""
    query = _window(select(*COLUMNS), equipment_id, start, end).order_by(
        MachineSpeedRow.equipment_id, MachineSpeedRow.recorded_at, MachineSpeedRow.id)
    return session.execute(query.execution_options(yield_per=STREAM))


def _operations(refs: Callable[[], Any] | None) -> dict[str, Any]:
    """БД.ОП by operation code (the first row of a code wins, as techcards _operation_norms); {} — no references."""
    if refs is None:
        return {}
    with refs() as uow:
        records = uow.tech_operations.list(tab=TechOperationTab.OPERATION, query="", limit=100_000)
    found: dict[str, Any] = {}
    for record in records:
        code = _code(record)
        if code and code not in found:
            found[code] = record
    return found


def _code(record) -> str:
    return str(record.fields.get("opNumber") or record.fields.get("Номер") or "").strip()


class _Setup:
    """What a setup key's runs say about it: the operation code, the ETK timing and the wire as written."""

    def __init__(self, key: str):
        self.key, self.code, self.timing, self.wire, self.from_run = key, speed.setup_code(key), None, "", False


def _setups(session, equipment_id: UUID | None, start: datetime, end: datetime,
            only: str | None = None) -> dict[tuple[UUID, str], _Setup]:
    """The window's setups (one grouped query, not the rows) with what their runs name."""
    query = _window(select(MachineSpeedRow.equipment_id, MachineSpeedRow.setup_key, MachineSpeedRow.run_write_id),
                    equipment_id, start, end)
    if only is not None:
        query = query.where(MachineSpeedRow.setup_key == only)
    query = query.group_by(MachineSpeedRow.equipment_id, MachineSpeedRow.setup_key, MachineSpeedRow.run_write_id)
    found: dict[tuple[UUID, str], _Setup] = {}
    writes: dict[UUID, list[_Setup]] = defaultdict(list)
    for machine, key, write_id in session.execute(query):
        setup = found.setdefault((machine, key), _Setup(key))
        if write_id is not None and setup not in writes[write_id]:
            writes[write_id].append(setup)
    if not writes:
        return found
    for write in session.scalars(select(MachineWriteRow).where(MachineWriteRow.id.in_(list(writes)))
                                 .order_by(MachineWriteRow.created_at)):
        run = runs.info(write) or {}
        try:
            if run.get("kind") == "cutting":
                group = session.get(CuttingGroupRow, UUID(str(run.get("group_id"))))
                recipe = (group.recipe or {}) if group else {}
                op = next((item for item in recipe.get("operations") or [] if item.get("type") in WIRE_CUTS), None)
                for setup in writes[write.id]:
                    if op is not None and not setup.from_run:  # the first cutting run of the setup names it
                        setup.code = str(op.get("code") or setup.code).strip()
                        setup.timing = (op.get("parameters") or {}).get("linearTiming")
                        setup.from_run = True
                    setup.wire = setup.wire or str(recipe.get("cable") or "")
            elif run.get("kind") == "manual":
                job = session.get(ManualJobRow, UUID(str(run.get("job_id"))))
                for setup in writes[write.id]:
                    setup.wire = setup.wire or (job.wire if job else "")
        except ValueError:
            continue
    return found


def _norm(setup: _Setup, record) -> tuple[Decimal | None, Decimal | None]:
    length = speed.length_mm(setup.key)
    if record is not None:
        return speed.piece_seconds(record.fields, length), speed.prep_seconds(record.fields)
    return speed.timing_seconds(setup.timing, length), None


def _machines(session) -> tuple[list[dict], dict[UUID, dict]]:
    """The ОБ.019 links — the machines that write the speed history."""
    equipment = _equipment(session)
    machines = []
    for link in session.scalars(select(rules.MachineLinkRow).where(rules.MachineLinkRow.kind == rules.KIND_OB019)
                                .order_by(rules.MachineLinkRow.inventory_number)):
        unit = next(iter(equipment.get(_inventory(link.inventory_number), [])), None)
        machines.append({"equipment_id": str(link.equipment_id), "inventory_number": link.inventory_number,
                         "name": _name(unit)})
    return machines, {UUID(item["equipment_id"]): item for item in machines}


def _can_accept(principal: Principal) -> bool:
    try:
        references_edit_principal(principal)
    except HTTPException:
        return False
    return True


def _stops(stops: dict[str, float]) -> list[dict]:
    return [{"reason": reason, "label": speed.STOP_LABELS[reason], "seconds": round(seconds, 1)}
            for reason, seconds in stops.items()]


def _analysis(session, refs: Callable[[], Any] | None, equipment_id: UUID | None, start: datetime, end: datetime,
              only: str | None = None):
    setups = _setups(session, equipment_id, start, end, only)
    records = _operations(refs)
    norms = {key: _norm(setup, records.get(setup.code)) for key, setup in setups.items()}
    found = speed.segments(_stream(session, equipment_id, start, end), {key: value[0] for key, value in norms.items()},
                           only=only)
    return setups, records, norms, found


def report(session, refs: Callable[[], Any] | None, equipment_id: UUID | None, since: date, until: date,
           start: datetime, end: datetime) -> tuple[list[dict], list[dict]]:
    """→ (machines, rows) of the contract."""
    machines, by_id = _machines(session)
    setups, records, norms, found = _analysis(session, refs, equipment_id, start, end)
    rows: list[dict] = []
    groups: dict[str, list[tuple[speed.SetupFact, dict]]] = defaultdict(list)
    for fact in speed.by_setup(found):
        key = (fact.equipment_id, fact.setup_key)
        setup, (norm, prep) = setups[key], norms[key]
        record = records.get(setup.code)
        parsed = speed.parse_setup(fact.setup_key)
        ratio = speed.ratio(fact.fact, norm)
        row = {
            "equipment_id": str(fact.equipment_id),
            "inventory_number": by_id.get(fact.equipment_id, {}).get("inventory_number", ""),
            "setup_key": fact.setup_key,
            "setup": {"wire": setup.wire or parsed["wire"], "length_mm": parsed["length_mm"],
                      "strip_front_mm": parsed["strip_front_mm"], "strip_back_mm": parsed["strip_back_mm"],
                      "pull": parsed["pull"], "marking": speed.marking_text(parsed["marking"])},
            "operation": {"code": setup.code,
                          "name": str(record.fields.get("opName") or record.fields.get("Название") or "") if record else "",
                          "record_id": str(record.id) if record else None},
            "runs": len(fact.runs), "pieces": fact.pieces, "running_seconds": round(fact.running_seconds, 1),
            "fact_seconds_per_piece": speed.seconds_text(fact.fact),
            "norm_seconds_per_piece": speed.seconds_text(norm),
            "ratio": speed.seconds_text(ratio),
            "setup_fact_seconds": speed.seconds_text(fact.setup_fact(prep), "1"),
            "setup_norm_seconds": speed.seconds_text(prep, "1"),
            "stops": _stops(fact.stops), "suggestion": None,
        }
        rows.append(row)
        if record is not None:
            groups[setup.code].append((fact, row))
    for code, items in groups.items():
        record = records[code]
        points, ratios = [], []
        for fact, _row in items:
            norm = norms[(fact.equipment_id, fact.setup_key)][0]
            length = speed.length_mm(fact.setup_key)
            if norm is None or length is None:
                continue
            for run in fact.runs:
                points.append(speed.Point(length, run.pieces, run.fact))
                ratios.append(run.fact / float(norm))
        found_suggestion = speed.suggest(record.fields, points, ratios)
        if found_suggestion is None:
            continue
        target = max(items, key=lambda item: item[0].pieces)[1]
        target["suggestion"] = {
            "record_id": str(record.id), "operation_code": code, "cells": found_suggestion["cells"],
            "evidence": {"pieces": sum(point.pieces for point in points), "runs": len(points),
                         "lengths": found_suggestion["lengths"], "fit": found_suggestion["fit"],
                         "since": since.isoformat(), "until": until.isoformat()},
            "note": found_suggestion["note"], "etk_note": ETK_NOTE.format(code=code),
        }
    rows.sort(key=lambda item: (item["inventory_number"], -item["pieces"], item["setup_key"]))
    return machines, rows


@router.get("")
def machine_speed(principal: Reader, factory: Factory, refs: References, now: Now,
                  equipment_id: UUID | None = None, since: date | None = None, until: date | None = None) -> dict:
    since, until, start, end = _period(since, until, now)
    with factory() as uow:
        machines, rows = report(uow.session, refs, equipment_id, since, until, start, end)
    return {"since": since.isoformat(), "until": until.isoformat(), "can_accept": _can_accept(principal),
            "machines": machines, "rows": rows}


@router.get("/runs")
def machine_speed_runs(principal: Reader, factory: Factory, refs: References, now: Now,
                       equipment_id: UUID, setup_key: str = Query(min_length=1, max_length=300),
                       since: date | None = None, until: date | None = None) -> dict:
    since, until, start, end = _period(since, until, now)
    with factory() as uow:
        _, _, norms, found = _analysis(uow.session, refs, equipment_id, start, end, only=setup_key)
    prep = norms.get((equipment_id, setup_key), (None, None))[1]
    return {"runs": [{
        "started_at": item.started_at.isoformat(), "ended_at": item.ended_at.isoformat(), "kind": item.run_kind,
        "pieces": item.pieces, "running_seconds": round(item.running_seconds, 1),
        "fact_seconds_per_piece": speed.seconds_text(item.fact),
        "setup_fact_seconds": speed.seconds_text(item.setup_fact(prep), "1"),
    } for item in found if item.pieces > 0]}


class CellPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(min_length=1, max_length=200)
    old: str = Field(default="", max_length=200)
    new: str = Field(min_length=1, max_length=20)


class AcceptPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: UUID
    cells: list[CellPayload] = Field(min_length=1, max_length=len(ACCEPTABLE))
    equipment_id: UUID
    setup_key: str = Field(min_length=1, max_length=300)
    since: date
    until: date


class _WithAudit:
    """The references unit of work whose commit also adds the production audit event: the edit and its journal entry
    are one transaction."""

    def __init__(self, inner, event: Callable[[], AuditEvent]):
        self.inner, self.event = inner, event

    def __enter__(self):
        self.inner.__enter__()
        return self

    def __exit__(self, *details):
        return self.inner.__exit__(*details)

    def __getattr__(self, name: str):
        return getattr(self.inner, name)

    def commit(self) -> None:
        self.inner.audit.add(self.event())
        self.inner.commit()


def _edits(record, cells: list[CellPayload], suggestion: dict) -> list[FieldEdit]:
    """The cells as sent — only «Скорость проката» / «Скорость работы инструмента», each with the suggestion's new value
    — plus the times the sheet's formulas recompute from them."""
    keys = speed.cycle_keys(record.fields)
    allowed = {keys[cell] for cell in ACCEPTABLE if cell in keys}
    suggested = {cell["field"]: cell["new"] for cell in suggestion["cells"]}
    fields = dict(record.fields)
    edits: list[FieldEdit] = []
    for item in cells:
        if item.field not in allowed:
            raise HTTPException(status_code=422, detail=f"«{item.field}» — не скорость проката и не ход инструмента")
        value = item.new.strip().replace(".", ",")
        if any(edit.field == item.field for edit in edits):
            raise HTTPException(status_code=422, detail=f"«{item.field}» — дважды")
        if suggested.get(item.field) != value:
            raise HTTPException(status_code=409, detail=CHANGED)
        edits.append(FieldEdit(item.field, item.old, value))
        fields[item.field] = value
    sent = {edit.field for edit in edits}
    if sent != set(suggested):
        # A joint fit «a + L / v» is valid only whole: half of it (roll without tool, or the reverse) is not the suggestion.
        raise HTTPException(status_code=409, detail=CHANGED)
    recomputed = speed.with_cycle_times(fields)
    edits += [FieldEdit(key, str(record.fields.get(key) or ""), value) for key, value in recomputed.items()
              if key not in sent and value != record.fields.get(key, "")]
    return edits


def _suggestion(session, refs, record_id: str, equipment_id: UUID, since: date, until: date, start: datetime,
                end: datetime) -> tuple[dict | None, dict | None]:
    """(the suggestion for the row now — the machine's report, else the all-machines one; the machine's setup row)."""
    _, rows = report(session, refs, equipment_id, since, until, start, end)
    found = next((row["suggestion"] for row in rows if row["suggestion"] and row["suggestion"]["record_id"] == record_id),
                 None)
    if found is None:
        _, everything = report(session, refs, None, since, until, start, end)
        found = next((row["suggestion"] for row in everything
                      if row["suggestion"] and row["suggestion"]["record_id"] == record_id), None)
    return found, rows


@router.post("/norm-suggestions/accept")
def accept_suggestion(payload: AcceptPayload, principal: Editor, factory: Factory, refs: References, now: Now) -> dict:
    if refs is None:
        raise HTTPException(status_code=503, detail="Справочник не настроен")
    since, until, start, end = _period(payload.since, payload.until, now)
    with refs() as uow:
        record = uow.tech_operations.get(payload.record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Строка БД.ОП не найдена")
    if record.tab != TechOperationTab.OPERATION:
        raise HTTPException(status_code=422, detail="Запись — не операция БД.ОП")
    with factory() as production:
        suggestion, rows = _suggestion(production.session, refs, str(record.id), payload.equipment_id, since, until,
                                       start, end)
    if suggestion is None:
        raise HTTPException(status_code=409, detail=CHANGED)
    edits = _edits(record, payload.cells, suggestion)
    code = _code(record)
    shown = next((row for row in rows or [] if row["setup_key"] == payload.setup_key), None)
    evidence = dict(suggestion["evidence"])
    if shown is not None:
        evidence.update({"fact_seconds_per_piece": shown["fact_seconds_per_piece"],
                         "norm_seconds_per_piece": shown["norm_seconds_per_piece"]})
    inventory = (shown or {}).get("inventory_number") or ""
    written = [{"field": edit.field, "old": edit.old, "new": edit.value} for edit in edits]
    summary = "; ".join(f"{item['field']}: {item['old'] or '—'} → {item['new']}" for item in written)

    def event() -> AuditEvent:
        return AuditEvent(
            id=uuid4(), aggregate_id=payload.equipment_id, event_type="production.machine_norm_accepted",
            actor=principal.actor, occurred_at=now,
            details={"text": f"Норма {code} по факту станка {inventory}: {summary}"[:500], "record_id": str(record.id),
                     "operation_code": code, "equipment_id": str(payload.equipment_id), "setup_key": payload.setup_key,
                     "cells": written, "evidence": evidence, "note": suggestion["note"]})

    try:
        edit_record(EditRecordCommand(record_id=record.id, edits=tuple(edits), actor=principal.actor, now=now),
                    _WithAudit(refs(), event))
    except StaleRecordError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ReferenceValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"record_id": str(record.id), "written": written}

"""HTTP: ОБ.019 settings written from ERP (plan «Станки», slice 4; owner 19.11.2026). Rules: production/machine_writes.py.

The machine card («Настройки» part):
- `GET  /api/production/machines/card/{ОБ}/control` (view) → may it be written now and why not, the last writes with
  their read-back result, the machine's stored corrections.
- `POST /api/production/machines/card/{ОБ}/write` (edit) `{values: {key: value}, confirm_state}` → a queued write of
  the card's writable fields (job, mode, quantities, wire and strip lengths, marking texts with zero-filled tails).
- `GET  /api/production/machines/card/{ОБ}/transfer[?group_id=…&length_mm=…|?wire=…&length_mm=…]` (view) → the ETK
  cutting jobs of open groups and the hook-up wires `{label, conductor_mm, has_recipe}`; with a job: what «Передать на
  станок» writes — ETK nominal + ERP corrections except length; with a wire and no job (`group_id: null`):
  the wire's newest recipe, else the reference fallback + the correction. A recipe value is final (no offset on top).
- `POST /api/production/machines/card/{ОБ}/transfer` (edit) `{group_id, length_mm, confirm_state, expected_source,
  expected_active_run_id}` → that write. The group preview supplies `expected_source` {group_id, version, length_mm,
  plan_hash} and `expected_active_run_id` (UUID or null). Both are compared under machine/group locks; a stale or
  missing source, or a changed active run → 409: refresh the preview and ask the operator to confirm again.
- `POST /api/production/machines/card/{ОБ}/correction` (edit) — after a trial Слава says which case it is (owner 19.11):
  `machine` «станок режет не так» → the machine's stored offset of that parameter (ETK unchanged); `etk` «размер в ЭТК
  неверный» → a new revision of every issued ETK of the job with reason «правка наладчика: …», the technologist is
  notified (not asked); `both`. Each correction is journaled on the machine. `measured` without `offset` → the server
  computes the offset from the machine's live value (offset = на станке − получилось); `seen` — the live value the card
  showed: 409 when the machine holds another one now, 409 while a parameter write is open on the machine; response
  `{offset, etk, written, measured}`. Length instead requires measured + seen and queues D658 / D600.6, returning
  the queued `command`; signed D54 is exposed by control.length_correction and command read-back fields.
`confirm_state` is the machine state the person confirmed in the dialog: a different state now → 409.

The command runner on the machine PC (`tools/machine-agent/ob019-writer.ps1`, token `machine:report`, PC bound):
- `POST /api/production/machines/agent/writes/next` `{inventory, machine_id}` → `{command: null | {id, kind: "write",
  blocks, bits: [{addr, bit, value}], states}}`; `POST …/agent/writes/{id}/result` `{inventory, machine_id, ok, readback: {"32": 32500}, error}`.
- Machine commands (Стоп / Сброс / Пуск / Сброс аварии, workflows/machine_control_api.py) ride the same queue: `next`
  gives `{id, kind: "control", action, deadline_ms}` — Stop first; the result adds `outcome` (done / failed / expired /
  unknown), `reason` (a code, production/machine_control.py REASONS), `detail` (journal only) and `before` / `after`
  `{"766": state, "782": alarm}` read on the PC."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from hashlib import sha256
import json
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.modules.equipment_setup.recipe_infrastructure import SqlAlchemyRecipes
from app.modules.equipment_setup.recipes import (OB019_SPEED_PARAMETERS, EtkNominal, latest_for_wire, ob019_proposal, recipe_holds, recipe_wire,
                                                 wire_key)
from app.modules.production import machine_control
from app.modules.production import machine_runs as runs
from app.modules.production import manual_jobs as manual
from app.modules.production import machine_writes as commands
from app.modules.production import machines as rules
from app.modules.production.api import production_uow_factory
from app.modules.production.cutting_infrastructure import CuttingGroupRow, CuttingMemberRow
from app.modules.production.infrastructure import ProductionTaskRow
from app.modules.techcards.application import EditTechcardOperationCommand, edit_project_operation
from app.modules.techcards.project_cards import TechcardValidationError, operation_key
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.http_auth import Principal, require_section, section_allowed
from app.shared.identity import Actor
from app.shared.notifications import TECHNOLOGIST_AUDIENCE, notification_requested

from .machines_api import Bridge, Reader, _inventory
from .ob019_marking import group_laser_mark, marking_plan
from .ob019_setup import GROUP_STATES, WIRE_CUTS, _conductor, _wires, group_choices, group_nominal, wire_label

router = APIRouter(prefix="/api/production/machines", tags=["production"])
Factory = Annotated[Callable, Depends(production_uow_factory)]
Now = Annotated[datetime, Depends(moscow_now)]
# The setup technician and the master write: Задания or Производство on edit (named gate of the access contract).
machines_edit_principal = require_section(("production", "myops"), "edit",
                                          detail="Записывать на станок вам не разрешено.")
Writer = Annotated[Principal, Depends(machines_edit_principal)]

WIRE_KEYS = commands.OFFSET_KEYS
ETK_SIDES = {"strip_front_mm": "a", "strip_back_mm": "b"}
CASES = {"machine": "станок режет не так", "etk": "размер в ЭТК неверный", "both": "станок и размер в ЭТК"}
_LATIN = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                  ("a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                   "u", "f", "kh", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya")))


def latin(text: str) -> str:
    """The machine stores ASCII only: «ПВАМ 0,35» → «PVAM 0,35» for its job and product lines."""
    out = []
    for char in " ".join(str(text or "").split()):
        low = char.lower()
        if low in _LATIN:
            mapped = _LATIN[low]
            out.append(mapped.upper() if char != low else mapped)
        elif 32 <= ord(char) <= 126:
            out.append(char)
    return "".join(out)[: rules.TEXT_CHARS]


# --- payloads ---

class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WritePayload(Payload):
    values: dict[str, str | int | float] = Field(min_length=1, max_length=len(rules.OB019_FIELDS))
    confirm_state: int | None = None


class ExpectedSource(Payload):
    group_id: UUID
    version: int = Field(ge=1)
    length_mm: str = Field(min_length=1, max_length=20)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class TransferPayload(Payload):
    group_id: UUID
    length_mm: str = Field(min_length=1, max_length=20)
    confirm_state: int | None = None
    expected_source: ExpectedSource | None = None
    expected_active_run_id: UUID | None = None


class CorrectionPayload(Payload):
    parameter: Literal[WIRE_KEYS]  # type: ignore[valid-type]
    case: Literal["machine", "etk", "both"]
    offset: str | None = Field(default=None, max_length=20)  # the machine's new total offset, мм
    etk_value: str | None = Field(default=None, max_length=20)  # the right ETK size, мм
    group_id: UUID | None = None  # the cutting job whose ETK is corrected
    measured: str | None = Field(default=None, max_length=20)
    # The machine value the card's preview showed («На станке 325,00…»): the server refuses when the machine holds
    # another one now, so the offset is never computed from a value the person did not see (review 19.11.2026).
    seen: str | None = Field(default=None, max_length=20)
    note: str = Field(default="", max_length=200)


class AgentPayload(Payload):
    inventory: str = Field(min_length=1, max_length=64)
    machine_id: str = Field(min_length=1, max_length=80)
    # The command runner names its version since 1.2.0; none = 1.1.0 (no switch bits, no other HMI pages).
    runner_version: str = Field(default="", max_length=32)


class ResultPayload(AgentPayload):
    ok: bool
    correction_pulsed: bool = False
    readback: dict[str, int] = Field(default_factory=dict, max_length=200)
    error: str = Field(default="", max_length=500)
    # Machine commands only (kind "control").
    outcome: Literal["done", "failed", "expired", "unknown"] | None = None
    reason: str = Field(default="", max_length=32)
    detail: str = Field(default="", max_length=500)
    before: dict[str, int] | None = None
    after: dict[str, int] | None = None
    # «Закрыть / Открыть пульт на ПК» (runner 1.4.0): graceful / forced / none / started.
    method: str = Field(default="", max_length=16)


# --- helpers ---

def _decimal(value: object, label: str) -> Decimal:
    text = str(value if value is not None else "").strip().replace(",", ".").replace(" ", "")
    try:
        number = Decimal(text)
    except ArithmeticError:
        raise HTTPException(422, f"{label}: нужно число") from None
    if not number.is_finite():
        raise HTTPException(422, f"{label}: нужно число")
    return number


def _shown(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(commands.STEP), "f").replace(".", ",")


def _link(session, inventory: str) -> rules.MachineLinkRow:
    link = session.scalar(select(rules.MachineLinkRow).where(rules.MachineLinkRow.inventory_number == _inventory(inventory)))
    if link is None:
        raise HTTPException(404, f"{inventory}: станок не подключён к ERP")
    if link.kind != rules.KIND_OB019:
        raise HTTPException(409, f"{inventory}: запись с ERP есть только у ОБ.019")
    return link


def locked_link(session, inventory: str) -> rules.MachineLinkRow:
    """Serialize loads even when the machine has no run row yet; hold until commit/rollback.

    SQLite's write transaction must precede the first read (FOR UPDATE is ignored there).
    The shared production UoW may already have started BEGIN IMMEDIATE for resource claims.
    """
    if session.get_bind().dialect.name == "sqlite":
        connection = session.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
    link = session.scalar(select(rules.MachineLinkRow).where(
        rules.MachineLinkRow.inventory_number == _inventory(inventory))
        .with_for_update().execution_options(populate_existing=True))
    if link is None or link.kind != rules.KIND_OB019:
        return _link(session, inventory)
    # Remaining pieces in the confirmed plan and the absolute PLC target must use the same counter.
    # Telemetry normally updates only this row, so the link lock alone cannot keep that baseline stable.
    session.scalar(select(rules.MachineSnapshotRow).where(
        rules.MachineSnapshotRow.equipment_id == link.equipment_id)
        .with_for_update().execution_options(populate_existing=True))
    return link


def active_run_id(session, equipment_id: UUID, *, lock: bool = False) -> str | None:
    row = runs.current(session, equipment_id, for_update=lock)
    return str(row.id) if runs.is_open(row) else None


def confirm_active_run(session, equipment_id: UUID, expected: UUID | None, job: dict) -> None:
    row = runs.current(session, equipment_id, for_update=True)
    active = str(row.id) if runs.is_open(row) else None
    if active != (str(expected) if expected is not None else None):
        raise HTTPException(409, "Задание на станке изменилось — обновите карточку и подтвердите загрузку ещё раз")
    if active is not None and not runs.same_job(runs.info(row), job):
        raise HTTPException(409, f"На станке идёт задание {runs.info(row)['label']} — сначала завершите или прервите его")


def _confirm(session, link: rules.MachineLinkRow, state: int | None) -> None:
    snapshot = session.get(rules.MachineSnapshotRow, link.equipment_id)
    current = rules.snapshot_words(snapshot).get(766)
    if state is not None and current != state:
        text = rules.OB019_STATES.get(current, (f"код {current}", ""))[0] if current is not None else "неизвестно"
        raise HTTPException(409, f"Состояние станка изменилось: {text} — проверьте и подтвердите ещё раз")


def _journal(uow, equipment_id: UUID, event: str, actor, now: datetime, text: str, **details: str) -> None:
    uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=equipment_id, event_type=f"production.machine_{event}",
                             actor=actor, occurred_at=now, details={"text": text[:500], **details}))


def _closed(uow, rows: list[commands.MachineWriteRow], actor, now: datetime) -> None:
    for row in rows:
        if row.purpose == commands.CONTROL:
            text = commands.CONTROL_TEXTS[row.status][0]
            _journal(uow, row.equipment_id, "command_finished", actor, now, f"{row.summary}: {text} — {row.error}",
                     command_id=str(row.id), command=row.command or "", status=row.status)
        else:
            _journal(uow, row.equipment_id, "write_failed", actor, now,
                     (f"Результат поправки неизвестен — проверьте станок: {row.summary} — {row.error}"
                      if row.status == "unknown" else f"Не записано: {row.summary} — {row.error}"),
                     command_id=str(row.id))


def _enqueue(uow, link, principal: Principal, purpose: str, writes, summary: str, now: datetime,
             run: dict | None = None) -> dict:
    """Queue a write; `run` (production/machine_runs.py) makes it the start of a job run from the card."""
    try:
        # «Тираж» N is pieces in this job: written as the target D22 = D649 + N (production/machine_writes.py).
        writes, quantity = commands.quantity_as_target(
            list(writes), uow.session.get(rules.MachineSnapshotRow, link.equipment_id), now)
    except commands.MachineCommandRefused as error:
        raise HTTPException(409, str(error)) from error
    except rules.MachineWriteInvalid as error:
        raise HTTPException(422, str(error)) from error
    if quantity is not None:
        summary = (f"{summary}; тираж {quantity['pieces']} шт: счётчик станка {quantity['counter']} → "
                   f"цель {quantity['target']}")
    if run is not None:
        if quantity is None:
            raise HTTPException(422, "Укажите тираж — штук в этом задании: без него станок не остановится на нужном числе")
        settle_run(uow, link.equipment_id, now)
        run = runs.with_quantity(runs.supersede(uow.session, link.equipment_id, run, now, principal.actor.name), quantity)
    try:
        row, expired = commands.enqueue(uow.session, equipment_id=link.equipment_id, purpose=purpose, writes=writes,
                                        summary=summary, actor_id=principal.actor.id, actor_name=principal.actor.name,
                                        now=now)
    except commands.MachineCommandRefused as error:
        raise HTTPException(409, str(error)) from error
    except rules.MachineWriteInvalid as error:
        raise HTTPException(422, str(error)) from error
    _closed(uow, expired, principal.actor, now)
    if run is not None:
        runs.tag(row, run)
    elif purpose == "settings":
        # ERP's own edit of the open job's length / order / product: the run follows it (machine_runs.follow).
        runs.follow(uow.session, link.equipment_id, {item.key: item.value for item in writes})
    changes = "; ".join(f"{item['label']} {item['before'] or '—'} → {item['after'] or '—'}"
                        for item in row.payload["fields"])
    _journal(uow, link.equipment_id, "write_queued", principal.actor, now, f"{summary}: {changes}",
             command_id=str(row.id))
    uow.commit()
    return commands.command_view(row)


def _offset_rows(session, equipment_id: UUID) -> list[dict]:
    stored = commands.offsets(session, equipment_id)
    rows = []
    for key in WIRE_KEYS:
        row = stored.get(key)
        rows.append({"key": key, "label": rules.OB019_FIELDS[key].label, "offset": _shown(commands.offset_value(row)),
                     "legacy": key == "length_mm",
                     "reason": row.reason if row else "", "updated_by": row.updated_by_name if row else "",
                     "updated_at": commands.moment(row.updated_at) if row else None, "etk": key in ETK_SIDES})
    return rows


# --- the card ---

@router.get("/card/{inventory}/control")
def control(inventory: str, principal: Reader, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        session = uow.session
        link = session.scalar(select(rules.MachineLinkRow).where(
            rules.MachineLinkRow.inventory_number == _inventory(inventory)))
        if link is None or link.kind != rules.KIND_OB019:
            return {"problem": "Станок не подключён к ERP" if link is None else "Запись с ERP есть только у ОБ.019",
                    "state": None, "commands": [], "offsets": [], "can_edit": False}
        snapshot = session.get(rules.MachineSnapshotRow, link.equipment_id)
        rows = commands.latest(session, link.equipment_id, now)
        correction_value = commands.length_correction_value(rules.snapshot_words(snapshot))
        if rules.link_status(snapshot, now) != "ok":
            correction_value = None
        else:
            confirmed = next((row for row in rows if row.payload.get(commands.LENGTH_CORRECTION)
                              and row.status == "done" and row.finished_at is not None
                              and rules.since(snapshot.reported_at, now) >= rules.since(row.finished_at, now)), None)
            if confirmed is not None:
                correction_value = commands.length_correction_value(confirmed.readback or {})
        settle_run(uow, link.equipment_id, now)
        result = {
            "problem": commands.idle_problem(link, snapshot, now),
            "state": rules.snapshot_words(snapshot).get(766),
            "commands": [commands.command_view(row) for row in rows],
            "offsets": _offset_rows(session, link.equipment_id),
            "length_correction": {"register": "D54", "value": _shown(correction_value),
                                  "legacy_offset": _shown(commands.offset_value(
                                      commands.offsets(session, link.equipment_id).get("length_mm")))},
            "can_edit": _can_edit(principal),
            "can_control": can_control(principal) and can_stop(principal),
            # «Стоп» and every other command: Производство / Задания only (ОТК reads the card, never commands).
            "can_stop": can_stop(principal),
            # The runner on the PC is older than 1.2.0: the other HMI pages and switches are not written yet.
            "runner_outdated": commands.runner_outdated(link.equipment_id, now),
            "runner_keys": sorted(key for key, field in rules.OB019_FIELDS.items() if commands.field_needs_runner(field)),
        }
        uow.commit()
    return result


def can_stop(principal: Principal) -> bool:
    return not principal.service and section_allowed(principal, ("production", "myops"), "view")


def can_control(principal: Principal) -> bool:
    """Сброс / Пуск / Сброс аварии: «Управление станками» on edit (Стоп needs only the card's view)."""
    return not principal.service and section_allowed(principal, (machine_control.SECTION,), "edit")


def _can_edit(principal: Principal) -> bool:
    try:
        machines_edit_principal(principal)
    except HTTPException:
        return False
    return True


@router.post("/card/{inventory}/write")
def write(inventory: str, payload: WritePayload, principal: Writer, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        link = _link(uow.session, inventory)
        _confirm(uow.session, link, payload.confirm_state)
        writes = [commands.FieldWrite(key, value) for key, value in payload.values.items()]
        return _enqueue(uow, link, principal, "settings", writes, "Запись настроек с карточки", now)


def _group(session, group_id: UUID) -> CuttingGroupRow:
    group = session.get(CuttingGroupRow, group_id)
    if group is None:
        raise HTTPException(404, "Группа резки не найдена")
    return group


def _quantity(session, group_id: UUID, length: Decimal) -> int:
    total = Decimal(0)
    for member in session.scalars(select(CuttingMemberRow).where(CuttingMemberRow.group_id == group_id,
                                                                 CuttingMemberRow.state == "active")):
        cut = (member.row_snapshot or {}).get("cut_length_mm")
        try:
            same = cut not in (None, "") and Decimal(str(cut).replace(",", ".")) == length
        except ArithmeticError:
            same = False
        if same:
            total += Decimal(member.blank_quantity)
    return int(total.to_integral_value(rounding=ROUND_CEILING))


def _length(text: str) -> Decimal:
    length = _decimal(text, "Длина провода")
    if length <= 0:
        raise HTTPException(422, "Длина провода: нужно положительное число")
    return length


def _versions(session, link: rules.MachineLinkRow):
    return SqlAlchemyRecipes(session).list_versions(link.equipment_id, limit=200, offset=0)


def _plan_rows(session, link: rules.MachineLinkRow, nominal: EtkNominal, length: Decimal,
               recipe_keys: tuple[str, ...]) -> tuple[list[dict], list[str], list[str]]:
    """Base + ERP offset, except length (the PLC holds its correction in D54). Recipe keys take the newest recipe;
    a recipe value is final (what the machine held when it cut right — the offset is not added twice, owner 19.11);
    the offset goes only on top of the ETK nominal / reference fallback. → (rows, missing labels, blocking labels)."""
    etk = {item.key: item for item in ob019_proposal(None, nominal, length)}
    recipe = latest_for_wire(_versions(session, link), nominal.wire)
    reference = {item.key: item for item in ob019_proposal(recipe, nominal, length)}
    stored = commands.offsets(session, link.equipment_id)
    words = rules.snapshot_words(session.get(rules.MachineSnapshotRow, link.equipment_id))
    rows, missing = [], []
    speed_keys = tuple(item.key for item in OB019_SPEED_PARAMETERS if recipe_holds(recipe, item.key))
    for key in (*WIRE_KEYS, *speed_keys):
        base = reference[key] if key in recipe_keys else etk[key]
        final = key in recipe_keys and recipe_holds(recipe, key)
        offset = None if final else Decimal(0) if key == "length_mm" else commands.offset_value(stored.get(key))
        value = None if base.value is None else (base.value if final else max(base.value + offset, Decimal(0)))
        if value is None:
            missing.append(rules.OB019_FIELDS[key].label)
        live = rules.field_value(key, words)
        rows.append({"key": key, "label": rules.OB019_FIELDS[key].label, "unit": rules.OB019_FIELDS[key].unit, "nominal": _shown(base.value),
                     "source": base.source, "offset": _shown(offset), "value": _shown(value),
                     "live": None if live is None else _shown(Decimal(str(live)))})
    blocking = [rules.OB019_FIELDS[key].label for key in ("length_mm", "strip_front_mm", "strip_back_mm")
                if rules.OB019_FIELDS[key].label in missing]
    return rows, missing, blocking


# The ETK gives length and strip of a job; cut diameter and knife back-off are the machine's own (Слава's эталон).
JOB_RECIPE_KEYS = ("diameter_mm", "knife_back_mm", *(item.key for item in OB019_SPEED_PARAMETERS))
# Without a job everything but the length comes from the wire's recipe, else the reference fallback.
WIRE_RECIPE_KEYS = tuple(key for key in WIRE_KEYS if key != "length_mm") + tuple(item.key for item in OB019_SPEED_PARAMETERS)


def _transfer_plan(session, link: rules.MachineLinkRow, group_id: UUID, length_text: str,
                   now: datetime | None = None) -> dict:
    """ETK nominal of the job + this machine's corrections → what «Передать на станок» writes. The same job written
    again after part of it was cut asks only for the rest (`planned` − `done`)."""
    length = _length(length_text)
    group = _group(session, group_id)
    nominal = group_nominal(group.recipe or {})
    if nominal is None:
        raise HTTPException(422, "В группе нет резки провода")
    nominal = EtkNominal(nominal.wire, length, nominal.strip_front_mm, nominal.strip_back_mm, nominal.partial_front,
                         nominal.partial_back, _conductor(_wires(session), nominal.wire))
    rows, missing, blocking = _plan_rows(session, link, nominal, length, JOB_RECIPE_KEYS)
    planned = _quantity(session, group.id, length)
    done = runs.done_before(session, link.equipment_id, runs.cutting(group.id, length, "", None), now) if now else 0
    quantity = max(planned - done, 0)
    job = {"total_set": quantity, "order_quantity": quantity, "order_number": latin(group.number),
           "product": latin(f"{nominal.wire} L{_shown(length)}")}
    return {"group_id": str(group.id), "version": group.version, "number": group.number,
            "wire": nominal.wire, "length_mm": _shown(length),
            "quantity": quantity, "planned": planned, "done": done, "rows": rows, "job": job, "missing": missing,
            "blocking": blocking,
            "marking": marking_plan(group_laser_mark(group.recipe or {}), length, latin)}


def _expected_source(plan: dict) -> dict:
    # Live PLC values only describe the comparison column; they are not values to write. Hash every other
    # part of the preview, including quantities, offsets, recipe provenance and laser marking.
    shown = {**plan, "rows": [{key: value for key, value in row.items() if key != "live"}
                             for row in plan["rows"]]}
    digest = sha256(json.dumps(shown, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return {"group_id": plan["group_id"], "version": plan["version"],
            "length_mm": runs.length_key(plan["length_mm"]), "plan_hash": digest}


def confirmed_transfer_plan(session, link, group_id: UUID, length_mm: str,
                            expected: ExpectedSource | None, now: datetime) -> dict:
    """Caller holds the machine link lock; group editors use the same group row lock."""
    if expected is None:
        raise HTTPException(409, "Подтвердите актуальный план загрузки задания на станок")
    reason = "Группа резки или план загрузки изменились — обновите карточку и подтвердите загрузку ещё раз"
    if expected.group_id != group_id or runs.length_key(expected.length_mm) != runs.length_key(length_mm):
        raise HTTPException(409, reason)
    group = session.scalar(select(CuttingGroupRow).where(CuttingGroupRow.id == group_id)
                           .with_for_update().execution_options(populate_existing=True))
    if group is None or group.version != expected.version or group.state not in GROUP_STATES:
        raise HTTPException(409, reason)
    if _quantity(session, group_id, _length(length_mm)) < 1:
        raise HTTPException(409, reason)
    try:
        plan = _transfer_plan(session, link, group_id, length_mm, now)
    except HTTPException as error:
        if error.status_code in (404, 422):
            raise HTTPException(409, reason) from error
        raise
    if _expected_source(plan) != {**expected.model_dump(mode="json"),
                                   "length_mm": runs.length_key(expected.length_mm)}:
        raise HTTPException(409, reason)
    return plan


def _wire_plan(session, link: rules.MachineLinkRow, wire_text: str, length_text: str) -> dict:
    """A wire picked by hand, no ETK job: the wire's newest recipe on this machine, else the reference fallback + the
    machine's offset; the length is the one entered."""
    wire = wire_label(wire_text)
    if not wire:
        raise HTTPException(422, "Выберите провод")
    length = _length(length_text)
    nominal = EtkNominal(wire, length, conductor_mm=_conductor(_wires(session), wire))
    rows, missing, blocking = _plan_rows(session, link, nominal, length, WIRE_RECIPE_KEYS)
    job = {"total_set": None, "order_quantity": None, "order_number": "", "product": latin(f"{wire} L{_shown(length)}")}
    return {"group_id": None, "number": None, "wire": wire, "length_mm": _shown(length), "quantity": None,
            "rows": rows, "job": job, "missing": missing, "blocking": blocking, "marking": None}


def _wire_choices(session, link: rules.MachineLinkRow) -> list[dict]:
    """Hook-up wires of the reference (Ø жилы) and the wires with a recipe on this machine, by label."""
    wires = _wires(session)
    recorded: dict[str, str] = {}
    for version in _versions(session, link):
        label = recipe_wire(version)
        if label and wire_key(label) not in recorded:
            recorded[wire_key(label)] = label
    names = {wire_key(label): label for label in wires} | recorded
    return [{"label": label, "conductor_mm": _plain(_conductor(wires, label)), "has_recipe": key in recorded}
            for key, label in sorted(names.items(), key=lambda item: item[1])]


def _plain(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f").replace(".", ",")


@router.get("/card/{inventory}/transfer")
def transfer_preview(inventory: str, _principal: Reader, factory: Factory, now: Now,
                     group_id: UUID | None = None, length_mm: Annotated[str, Query(max_length=20)] = "",
                     wire: Annotated[str, Query(max_length=200)] = "") -> dict:
    with factory() as uow:
        session = uow.session
        link = _link(session, inventory)
        if group_id is not None:
            plan = _transfer_plan(session, link, group_id, length_mm, now)
            return {**plan, "expected_source": _expected_source(plan),
                    "expected_active_run_id": active_run_id(session, link.equipment_id)}
        if wire.strip():
            return _wire_plan(session, link, wire, length_mm)
        groups = list(session.scalars(select(CuttingGroupRow).where(CuttingGroupRow.state.in_(GROUP_STATES))
                                      .order_by(CuttingGroupRow.created_at.desc()).limit(100)))
        members = list(session.scalars(select(CuttingMemberRow).where(
            CuttingMemberRow.state == "active", CuttingMemberRow.group_id.in_([group.id for group in groups])))) \
            if groups else []
        return {"choices": group_choices(groups, members), "wires": _wire_choices(session, link)}


@router.post("/card/{inventory}/transfer")
def transfer(inventory: str, payload: TransferPayload, principal: Writer, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        session = uow.session
        link = locked_link(session, inventory)
        _confirm(session, link, payload.confirm_state)
        confirm_active_run(session, link.equipment_id, payload.expected_active_run_id,
                           runs.cutting(payload.group_id, payload.length_mm, "", None))
        refuse_while_manual(session, link.equipment_id)
        plan = confirmed_transfer_plan(session, link, payload.group_id, payload.length_mm, payload.expected_source, now)
        if plan["blocking"]:
            raise HTTPException(422, "В ЭТК не задано: " + ", ".join(plan["blocking"]))
        refuse_when_done(plan)
        writes = [commands.FieldWrite(row["key"], row["value"]) for row in plan["rows"] if row["value"] is not None]
        writes += [commands.FieldWrite(key, value) for key, value in plan["job"].items() if value not in ("", None)]
        current = runs.current(session, link.equipment_id)
        new_job = not (runs.is_open(current) and runs.same_job(runs.info(current),
                                                            runs.cutting(payload.group_id, payload.length_mm, "", None)))
        writes += marking_writes(plan, new_job=new_job)
        return _enqueue(uow, link, principal, "transfer", writes, transfer_summary(plan), now,
                        run=cutting_run(plan, payload.length_mm))


def marking_writes(plan: dict, *, new_job: bool = False) -> list[commands.FieldWrite]:
    """The ETK laser marking of a job plan (workflows/ob019_marking.py) — written with the job, 422 when it cannot be."""
    marking = plan.get("marking")
    if marking is None and new_job:
        marking = marking_plan({"mode": "none"}, Decimal(0), latin)
    if marking is None:
        return []
    if marking["blocking"]:
        raise HTTPException(422, "Маркировка: " + "; ".join(marking["blocking"]))
    # Cleared texts are real writes (zero-filled), unlike the job's empty strings dropped above.
    return [commands.FieldWrite(row["key"], row["value"]) for row in marking["rows"]]


def transfer_summary(plan: dict) -> str:
    return f"Передать на станок: группа {plan['number']}, {plan['wire']}, {plan['length_mm']} мм"


def cutting_run(plan: dict, length_mm: str, values: dict | None = None) -> dict:
    """The run of a job plan; `values` — what the card writes instead of the plan (the drawing edited by hand). The run
    expects the machine to keep the job's length, order and product as written (production/machine_runs.start_blocker)."""
    rows = {row["key"]: row["value"] for row in plan["rows"]}
    identity = {"length_mm": rows.get("length_mm"), "order_number": plan["job"].get("order_number"),
                "product": plan["job"].get("product"), **(values or {})}
    return {**runs.cutting(UUID(plan["group_id"]), length_mm,
                           f"{plan['number']} · {plan['wire']} · {plan['length_mm']} мм", plan["quantity"]),
            "expect": runs.expect(identity)}


def refuse_when_done(plan: dict) -> None:
    if plan["quantity"] < 1:
        raise HTTPException(422, f"По станку уже сделано {plan['done']} из {plan['planned']} — завершите задание")


def refuse_while_manual(session, equipment_id: UUID) -> None:
    """A cutting job is not written while a manual job runs on the machine: the counter would mix both (plan S2)."""
    job = manual.running(session, equipment_id)
    if job is not None:
        raise HTTPException(409, f"На станке идёт ручная работа {manual.number_text(job.number)} — "
                                 "завершите или отмените её")


# --- corrections after a trial ---

def _etk_cards(session, group_id: UUID) -> list[UUID]:
    task_ids = set(session.scalars(select(CuttingMemberRow.task_id).where(CuttingMemberRow.group_id == group_id,
                                                                          CuttingMemberRow.state == "active")))
    if not task_ids:
        return []
    return sorted({card for card in session.scalars(select(ProductionTaskRow.techcard_id).where(
        ProductionTaskRow.id.in_(task_ids))) if card is not None}, key=str)


def _strip_operation(document: dict) -> dict:
    found = [op for op in (document or {}).get("operations") or []
             if op.get("type") in WIRE_CUTS and isinstance((op.get("fields") or {}).get("strip"), dict)]
    if len(found) != 1:
        raise TechcardValidationError("В ЭТК не одна операция резки провода с разделкой — правку делает технолог")
    return found[0]


def _correct_etk(request: Request, *, card_ids: list[UUID], parameter: str, value: Decimal, note: str,
                 principal: Principal, inventory: str, equipment_id: UUID, now: datetime) -> list[dict]:
    factory = getattr(request.app.state, "techcards_uow_factory", None)
    if not callable(factory):
        raise HTTPException(503, "База ЭТК не настроена")
    side = ETK_SIDES[parameter]
    label = rules.OB019_FIELDS[parameter].label.lower()
    done = []
    for card_id in card_ids:
        with factory() as uow:
            card = uow.techcards.get(card_id)
        if card is None:
            continue
        try:
            operation = _strip_operation(card.document)
        except TechcardValidationError as error:
            raise HTTPException(409, f"ЭТК {card.project}: {error}") from error
        strip = dict(operation["fields"]["strip"])
        if not strip.get(side):
            raise HTTPException(409, f"ЭТК {card.project}: эта сторона провода не разделывается")
        old = str(strip.get("len" + side.upper()) or "")
        if Decimal(old.replace(",", ".") or "0") == value:
            continue
        strip["len" + side.upper()] = format(value.normalize(), "f")
        edited = {**operation, "fields": {**operation["fields"], "strip": strip}}
        reason = f"правка наладчика: {label} {old or '—'} → {_shown(value)} мм ({inventory})" + (f"; {note}" if note else "")
        notice = notification_requested(
            key=f"machine-etk:{card.id}:{card.revision + 1}", now=now, title="Правка ЭТК наладчиком",
            body=f"{principal.actor.name}: ЭТК {card.project}, {reason}", kind="etk_setup_correction",
            reference=str(card.id), audiences=(TECHNOLOGIST_AUDIENCE,))
        journal = AuditEvent(id=uuid4(), aggregate_id=equipment_id, event_type="production.machine_etk_corrected",
                             actor=principal.actor, occurred_at=now,
                             details={"text": f"ЭТК {card.project}: {reason}"[:500], "card_id": str(card.id)})
        try:
            updated = edit_project_operation(EditTechcardOperationCommand(
                card_id=card.id, expected_version=card.version, actor=principal.actor, now=now,
                operation_key=operation_key(operation), operation=edited, reason=reason, notice=notice,
                journal=(journal,)), factory())
        except TechcardValidationError as error:
            raise HTTPException(409, f"ЭТК {card.project}: {error}") from error
        done.append({"card_id": str(updated.id), "project": updated.project, "revision": updated.revision})
    return done


STALE_LIVE = "Нет свежих данных со станка — повторите, когда станок на связи"
LIVE_CHANGED = "Значение на станке изменилось после пробы — проверьте и повторите"
WRITE_OPEN = "Идёт запись на станок — подождите"


def _live_value(session, link: rules.MachineLinkRow, key: str, now: datetime) -> Decimal | None:
    """The parameter's value on the machine now (latest snapshot, link «На связи»), мм; None — no fresh reading."""
    snapshot = session.get(rules.MachineSnapshotRow, link.equipment_id)
    if rules.link_status(snapshot, now) != "ok":
        return None
    value = rules.field_value(key, rules.snapshot_words(snapshot))
    return None if value is None else Decimal(str(value)).quantize(commands.STEP)


@router.post("/card/{inventory}/correction")
def correction(inventory: str, payload: CorrectionPayload, request: Request, principal: Writer, factory: Factory,
               now: Now) -> dict:
    """«Правка после пробы». With `measured` and no `offset` the server computes the machine's new total offset from
    what the machine holds now: it cuts with a constant error e = M − W, the wanted value is W' = N − e = N + (W − M),
    and W' = N + offset' → offset' = W − M — independent of the nominal and of the old offset, so a hand edit on the
    panel or a repeated trial without a new transfer give the right offset, not a doubled one. An explicit `offset`
    (manual entry) wins."""
    label = rules.OB019_FIELDS[payload.parameter].label
    note = " ".join(payload.note.split())
    got: Decimal | None = None
    if payload.measured not in (None, ""):
        got = _decimal(payload.measured, "Получилось")
        if got < 0 or got != got.quantize(commands.STEP):
            raise HTTPException(422, "Получилось: число ≥ 0 с точностью 0,01 мм")
        got = got.quantize(commands.STEP)
    measured = f", получилось {_shown(got)} мм" if got is not None else ""
    result: dict = {"offset": None, "etk": [], "written": None, "measured": _shown(got)}
    with factory() as uow:
        session = uow.session
        link = _link(session, inventory)
        equipment_id = link.equipment_id
        written = _live_value(session, link, payload.parameter, now)
        result["written"] = _shown(written)
        card_ids: list[UUID] = []
        if payload.case in ("etk", "both"):
            if payload.parameter not in ETK_SIDES:
                raise HTTPException(422, "С карточки станка в ЭТК правится только зачистка начала и конца; длину и "
                                         "остальное правит технолог")
            if payload.group_id is None or payload.etk_value in (None, ""):
                raise HTTPException(422, "Укажите задание резки и верный размер ЭТК")
            _group(session, payload.group_id)
            card_ids = _etk_cards(session, payload.group_id)
            if not card_ids:
                raise HTTPException(409, "У задания нет выданной ЭТК")
        if payload.case in ("machine", "both"):
            if payload.parameter == "length_mm":
                if payload.offset not in (None, ""):
                    raise HTTPException(422, "Для поправки длины введите измеренную длину, а не поправку ERP")
                if got is None or payload.seen in (None, ""):
                    raise HTTPException(422, "Укажите измеренную длину и показанную длину на станке")
                try:
                    row, expired = commands.enqueue_length_correction(
                        session, equipment_id=equipment_id, measured=got, seen=_decimal(payload.seen, "На станке"),
                        actor_id=principal.actor.id, actor_name=principal.actor.name, now=now)
                except rules.MachineWriteInvalid as error:
                    raise HTTPException(422, str(error)) from error
                except commands.MachineCommandRefused as error:
                    raise HTTPException(409, str(error)) from error
                _closed(uow, expired, principal.actor, now)
                _journal(uow, equipment_id, "write_queued", principal.actor, now, row.summary,
                         command_id=str(row.id))
                result["command"] = commands.command_view(row)
                uow.commit()
                return result
            if payload.offset not in (None, ""):
                offset = _decimal(payload.offset, "Поправка станка")
                reason = f"{CASES[payload.case]}{measured}" + (f"; {note}" if note else "")
            elif got is None:
                raise HTTPException(422, "Укажите, сколько получилось, или поправку станка")
            elif written is None:
                raise HTTPException(422, STALE_LIVE)
            elif commands.open_write(session, equipment_id, now) is not None:
                raise HTTPException(409, WRITE_OPEN)
            elif payload.seen not in (None, "") and _decimal(payload.seen, "На станке").quantize(commands.STEP) != written:
                raise HTTPException(409, LIVE_CHANGED)
            else:
                offset = written - got
                reason = f"{CASES[payload.case]}, на станке {_shown(written)}{measured}" + (f"; {note}" if note else "")
            try:
                old, new = commands.set_offset(session, equipment_id=equipment_id, parameter=payload.parameter,
                                               offset=offset, reason=reason, actor_name=principal.actor.name, now=now)
            except rules.MachineWriteInvalid as error:
                raise HTTPException(422, str(error)) from error
            except commands.MachineCommandRefused as error:
                raise HTTPException(409, str(error)) from error
            _journal(uow, equipment_id, "offset_set", principal.actor, now,
                     f"Поправка станка «{label}»: {_shown(old)} → {_shown(new)} мм — {reason}", parameter=payload.parameter)
            result["offset"] = _shown(new)
        uow.commit()
    if card_ids:
        value = _decimal(payload.etk_value, "Размер ЭТК")
        if value < 0 or value != value.quantize(commands.STEP):
            raise HTTPException(422, "Размер ЭТК: число ≥ 0 с точностью 0,01 мм")
        result["etk"] = _correct_etk(request, card_ids=card_ids, parameter=payload.parameter, value=value,
                                     note=f"{CASES[payload.case]}{measured}" + (f"; {note}" if note else ""),
                                     principal=principal, inventory=link.inventory_number, equipment_id=equipment_id,
                                     now=now)
    return result


# --- the command runner on the machine PC ---

# The run check below is the system's, not the person's whose request happened to trigger it (a viewer opening the card).
SETTLE_ACTOR = Actor(id=UUID(int=0), name="ERP · проверка станка", department="Система")


def settle_run(uow, equipment_id: UUID, now: datetime) -> None:
    """The machine's run whose counter check waits for a fresh snapshot (production/machine_runs.py `pending`) is
    checked now — on every card, control and command request, without anyone pressing anything; the journal names
    the system as its author."""
    row = runs.current(uow.session, equipment_id)
    if runs.pending(row):
        _run_verified(uow, row, SETTLE_ACTOR, now)


def _run_verified(uow, row: commands.MachineWriteRow, actor, now: datetime) -> None:
    """A job run's write read back right: its counter start; a manual job is counted by the machine from now. Nothing
    while the check still waits for a snapshot after the write (it settles on a later read: `settle_run`)."""
    run = runs.verified(uow.session, row, now)
    if run is None:
        return
    start = run.get("counter_start")
    check = run.get("check", "ok")
    job = None
    if run["kind"] == "manual" and start is not None and check == "ok":
        params = {item["key"]: item["after"] for item in row.payload.get("fields", [])}
        try:
            # The write itself is verified whatever happens to the job meanwhile: a job changed or closed in the same
            # moment stays counted by hand, never turns the write into «агент не сообщил результат».
            with uow.session.begin_nested():
                job = manual.attach_plc(uow.session, UUID(run["job_id"]), write_id=row.id, counter_start=start,
                                        params=params)
        except (manual.ManualJobConflict, IntegrityError):
            job = None
        if job is not None:
            uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=job.id, event_type="production.manual_job_counted",
                                     actor=actor, occurred_at=now, details={
                                         "text": f"{manual.number_text(job.number)}: записано на станок и проверено, "
                                                 f"счёт по станку с {start}", "number": manual.number_text(job.number),
                                         "equipment_id": str(job.equipment_id)}))
    text = f"Задание на станке: {run.get('label', '')} — " + (
        runs.MOVED if check == "moved" else runs.UNREAD if check == "unknown"
        else f"счётчик станка {start}, цель {run['target']}" if run.get("target") is not None
        else f"счётчик станка {start}" if start is not None else "нет свежих данных станка, счёт вручную")
    _journal(uow, row.equipment_id, "run_verified", actor, now, text, command_id=str(row.id))


def _agent_link(session, bridge: Principal, payload: AgentPayload) -> rules.MachineLinkRow:
    link = session.scalar(select(rules.MachineLinkRow).where(
        rules.MachineLinkRow.inventory_number == _inventory(payload.inventory)))
    if link is None or link.kind != rules.KIND_OB019 or link.token_id != bridge.actor.id \
            or link.machine_id != " ".join(payload.machine_id.split()):
        raise HTTPException(403, f"Этот ПК или токен не привязан к {payload.inventory}")
    return link


@router.post("/agent/writes/next")
def next_command(payload: AgentPayload, bridge: Bridge, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        link = _agent_link(uow.session, bridge, payload)
        row, expired = commands.claim(uow.session, link.equipment_id, now, payload.runner_version)
        _closed(uow, expired, bridge.actor, now)
        uow.commit()
        if row is None:
            return {"command": None}
        if row.purpose == commands.CONTROL:
            return {"command": {"id": str(row.id), "kind": "control", "action": row.command,
                                "deadline_ms": machine_control.deadline_ms(row, now),
                                "force": bool(row.payload.get("force"))}}
        if row.payload.get(commands.LENGTH_CORRECTION):
            return {"command": {"id": str(row.id), "kind": "length_correction", "blocks": row.payload["blocks"],
                                "bits": [], "seen": row.payload["seen"],
                                "deadline_ms": max(0, int((commands.COMMAND_TTL - rules.since(row.created_at, now))
                                                          .total_seconds() * 1000)),
                                "states": list(commands.IDLE_STATES)}}
        return {"command": {"id": str(row.id), "kind": "write", "blocks": row.payload["blocks"],
                            "bits": commands.expected_bits(row), "states": list(commands.IDLE_STATES)}}


@router.post("/agent/writes/{command_id}/result")
def command_result(command_id: UUID, payload: ResultPayload, bridge: Bridge, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        link = _agent_link(uow.session, bridge, payload)
        before = uow.session.get(commands.MachineWriteRow, command_id)
        if before is not None and before.equipment_id == link.equipment_id and before.purpose == commands.CONTROL:
            try:
                recorded = machine_control.finish(uow.session, before, outcome=payload.outcome or "failed",
                                                  reason=payload.reason or "unknown_command", before=payload.before,
                                                  after=payload.after, now=now, method=payload.method or None)
            except rules.MachineWriteInvalid as error:
                raise HTTPException(422, str(error)) from error
            if recorded:
                text = commands.CONTROL_TEXTS[before.status][0]
                states = before.readback or {}
                method = {"forced": " — закрыт принудительно", "none": " — пульт не был открыт"}.get(payload.method, "")
                _journal(uow, link.equipment_id, "command_finished", bridge.actor, now,
                         f"{before.summary}: {text}" + (f" — {before.error}" if before.error else "") + method,
                         command_id=str(before.id), command=before.command or "", status=before.status,
                         reason=payload.reason, method=payload.method, detail=" ".join(payload.detail.split())[:300],
                         state_before=str((states.get("before") or {}).get("766", "")),
                         state_after=str((states.get("after") or {}).get("766", "")),
                         alarm_before=str((states.get("before") or {}).get("782", "")),
                         alarm_after=str((states.get("after") or {}).get("782", "")))
            uow.commit()
            if not recorded:
                raise HTTPException(409, "Результат команды уже записан")
            return {"status": before.status, "error": before.error}
        was_taken = before is not None and before.status == "taken"
        try:
            row = commands.finish(uow.session, equipment_id=link.equipment_id, command_id=command_id, ok=payload.ok,
                                  readback=payload.readback, error=payload.error, now=now,
                                  correction_pulsed=payload.correction_pulsed, before=payload.before,
                                  outcome=payload.outcome)
        except KeyError as error:
            raise HTTPException(404, str(error.args[0])) from error
        if was_taken:
            if row.status == "done":
                if row.payload.get(commands.LENGTH_CORRECTION):
                    old = commands.length_correction_value(row.payload["seen"])
                    new = commands.length_correction_value(row.readback or {})
                    _journal(uow, link.equipment_id, "length_corrected", bridge.actor, now,
                             f"Поправка станка: D54 = {_shown(old)} → {_shown(new)} мм",
                             command_id=str(row.id), before=_shown(old), after=_shown(new), register="D54")
                _journal(uow, link.equipment_id, "write_done", bridge.actor, now,
                         f"Записано и проверено: {row.summary}", command_id=str(row.id))
                _run_verified(uow, row, bridge.actor, now)
            else:
                _journal(uow, link.equipment_id, "write_failed", bridge.actor, now,
                         (f"Результат поправки неизвестен — проверьте станок: {row.summary} — {row.error}"
                          if row.status == "unknown" else f"Не записано: {row.summary} — {row.error}"),
                         command_id=str(row.id))
        uow.commit()
        return {"status": row.status, "error": row.error}


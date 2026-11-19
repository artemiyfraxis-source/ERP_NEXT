"""HTTP: ОБ.019 settings written from ERP (plan «Станки», slice 4; owner 19.11.2026). Rules: production/machine_writes.py.

The machine card («Настройки» part):
- `GET  /api/production/machines/card/{ОБ}/control` (view) → may it be written now and why not, the last writes with
  their read-back result, the machine's stored corrections.
- `POST /api/production/machines/card/{ОБ}/write` (edit) `{values: {key: value}, confirm_state}` → a queued write of
  the card's writable fields (job, mode, quantities, wire and strip lengths, marking texts with zero-filled tails).
- `GET  /api/production/machines/card/{ОБ}/transfer[?group_id=…&length_mm=…]` (view) → the ETK cutting jobs of open
  groups; with a job: what «Передать на станок» writes — ETK nominal + the machine's correction per parameter.
- `POST /api/production/machines/card/{ОБ}/transfer` (edit) `{group_id, length_mm, confirm_state}` → that write.
- `POST /api/production/machines/card/{ОБ}/correction` (edit) — after a trial Слава says which case it is (owner 19.11):
  `machine` «станок режет не так» → the machine's stored offset of that parameter (ETK unchanged); `etk` «размер в ЭТК
  неверный» → a new revision of every issued ETK of the job with reason «правка наладчика: …», the technologist is
  notified (not asked); `both`. Each correction is journaled on the machine.
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
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.modules.equipment_setup.recipe_infrastructure import SqlAlchemyRecipes
from app.modules.equipment_setup.recipes import EtkNominal, latest_for_wire, ob019_proposal
from app.modules.production import machine_control
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
from app.shared.notifications import TECHNOLOGIST_AUDIENCE, notification_requested

from .machines_api import Bridge, Reader, _inventory
from .ob019_setup import GROUP_STATES, WIRE_CUTS, _conductor, _wires, group_choices, group_nominal

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


class TransferPayload(Payload):
    group_id: UUID
    length_mm: str = Field(min_length=1, max_length=20)
    confirm_state: int | None = None


class CorrectionPayload(Payload):
    parameter: Literal[WIRE_KEYS]  # type: ignore[valid-type]
    case: Literal["machine", "etk", "both"]
    offset: str | None = Field(default=None, max_length=20)  # the machine's new total offset, мм
    etk_value: str | None = Field(default=None, max_length=20)  # the right ETK size, мм
    group_id: UUID | None = None  # the cutting job whose ETK is corrected
    measured: str | None = Field(default=None, max_length=20)
    note: str = Field(default="", max_length=200)


class AgentPayload(Payload):
    inventory: str = Field(min_length=1, max_length=64)
    machine_id: str = Field(min_length=1, max_length=80)
    # The command runner names its version since 1.2.0; none = 1.1.0 (no switch bits, no other HMI pages).
    runner_version: str = Field(default="", max_length=32)


class ResultPayload(AgentPayload):
    ok: bool
    readback: dict[str, int] = Field(default_factory=dict, max_length=200)
    error: str = Field(default="", max_length=500)
    # Machine commands only (kind "control").
    outcome: Literal["done", "failed", "expired", "unknown"] | None = None
    reason: str = Field(default="", max_length=32)
    detail: str = Field(default="", max_length=500)
    before: dict[str, int] | None = None
    after: dict[str, int] | None = None


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
            _journal(uow, row.equipment_id, "write_failed", actor, now, f"Не записано: {row.summary} — {row.error}",
                     command_id=str(row.id))


def _enqueue(uow, link, principal: Principal, purpose: str, writes, summary: str, now: datetime) -> dict:
    try:
        row, expired = commands.enqueue(uow.session, equipment_id=link.equipment_id, purpose=purpose, writes=writes,
                                        summary=summary, actor_id=principal.actor.id, actor_name=principal.actor.name,
                                        now=now)
    except commands.MachineCommandRefused as error:
        raise HTTPException(409, str(error)) from error
    except rules.MachineWriteInvalid as error:
        raise HTTPException(422, str(error)) from error
    _closed(uow, expired, principal.actor, now)
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
        result = {
            "problem": commands.idle_problem(link, snapshot, now),
            "state": rules.snapshot_words(snapshot).get(766),
            "commands": [commands.command_view(row) for row in rows],
            "offsets": _offset_rows(session, link.equipment_id),
            "can_edit": _can_edit(principal),
            "can_control": can_control(principal),
            # The runner on the PC is older than 1.2.0: the other HMI pages and switches are not written yet.
            "runner_outdated": commands.runner_outdated(link.equipment_id, now),
            "runner_keys": sorted(key for key, field in rules.OB019_FIELDS.items() if commands.field_needs_runner(field)),
        }
        uow.commit()
    return result


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


def _transfer_plan(session, link: rules.MachineLinkRow, group_id: UUID, length_text: str) -> dict:
    """ETK nominal of the job + this machine's corrections → what «Передать на станок» writes."""
    length = _decimal(length_text, "Длина провода")
    if length <= 0:
        raise HTTPException(422, "Длина провода: нужно положительное число")
    group = _group(session, group_id)
    nominal = group_nominal(group.recipe or {})
    if nominal is None:
        raise HTTPException(422, "В группе нет резки провода")
    wires = _wires(session)
    nominal = EtkNominal(nominal.wire, length, nominal.strip_front_mm, nominal.strip_back_mm, nominal.partial_front,
                         nominal.partial_back, _conductor(wires, nominal.wire))
    etk = {item.key: item for item in ob019_proposal(None, nominal, length)}
    recipe = latest_for_wire(SqlAlchemyRecipes(session).list_versions(link.equipment_id, limit=200, offset=0),
                             nominal.wire)
    reference = {item.key: item for item in ob019_proposal(recipe, nominal, length)}
    stored = commands.offsets(session, link.equipment_id)
    words = rules.snapshot_words(session.get(rules.MachineSnapshotRow, link.equipment_id))
    rows, missing = [], []
    for key in WIRE_KEYS:
        # The ETK gives length and strip; cut diameter and knife back-off are the machine's own (Слава's эталон).
        base = etk[key] if key in ("length_mm", "strip_front_mm", "pull_front_mm", "strip_back_mm", "pull_back_mm") \
            else reference[key]
        offset = commands.offset_value(stored.get(key))
        value = None if base.value is None else max(base.value + offset, Decimal(0))
        if value is None:
            missing.append(rules.OB019_FIELDS[key].label)
        rows.append({"key": key, "label": rules.OB019_FIELDS[key].label, "unit": "мм", "nominal": _shown(base.value),
                     "source": base.source, "offset": _shown(offset), "value": _shown(value),
                     "live": None if rules.field_value(key, words) is None
                     else _shown(Decimal(str(rules.field_value(key, words))))})
    quantity = _quantity(session, group.id, length)
    job = {"total_set": quantity, "order_quantity": quantity, "order_number": latin(group.number),
           "product": latin(f"{nominal.wire} L{_shown(length)}")}
    return {"group_id": str(group.id), "number": group.number, "wire": nominal.wire, "length_mm": _shown(length),
            "quantity": quantity, "rows": rows, "job": job, "missing": missing,
            "blocking": [label for key, label in ((key, rules.OB019_FIELDS[key].label)
                                                  for key in ("length_mm", "strip_front_mm", "strip_back_mm"))
                         if label in missing]}


@router.get("/card/{inventory}/transfer")
def transfer_preview(inventory: str, _principal: Reader, factory: Factory,
                     group_id: UUID | None = None, length_mm: Annotated[str, Query(max_length=20)] = "") -> dict:
    with factory() as uow:
        session = uow.session
        link = _link(session, inventory)
        if group_id is None:
            groups = list(session.scalars(select(CuttingGroupRow).where(CuttingGroupRow.state.in_(GROUP_STATES))
                                          .order_by(CuttingGroupRow.created_at.desc()).limit(100)))
            members = list(session.scalars(select(CuttingMemberRow).where(
                CuttingMemberRow.state == "active", CuttingMemberRow.group_id.in_([group.id for group in groups])))) \
                if groups else []
            return {"choices": group_choices(groups, members)}
        return _transfer_plan(session, link, group_id, length_mm)


@router.post("/card/{inventory}/transfer")
def transfer(inventory: str, payload: TransferPayload, principal: Writer, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        session = uow.session
        link = _link(session, inventory)
        _confirm(session, link, payload.confirm_state)
        plan = _transfer_plan(session, link, payload.group_id, payload.length_mm)
        if plan["blocking"]:
            raise HTTPException(422, "В ЭТК не задано: " + ", ".join(plan["blocking"]))
        writes = [commands.FieldWrite(row["key"], row["value"]) for row in plan["rows"] if row["value"] is not None]
        writes += [commands.FieldWrite(key, value) for key, value in plan["job"].items() if value not in ("", None)]
        summary = f"Передать на станок: группа {plan['number']}, {plan['wire']}, {plan['length_mm']} мм"
        return _enqueue(uow, link, principal, "transfer", writes, summary, now)


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


@router.post("/card/{inventory}/correction")
def correction(inventory: str, payload: CorrectionPayload, request: Request, principal: Writer, factory: Factory,
               now: Now) -> dict:
    label = rules.OB019_FIELDS[payload.parameter].label
    note = " ".join(payload.note.split())
    measured = f", получилось {payload.measured} мм" if payload.measured else ""
    result: dict = {"offset": None, "etk": []}
    with factory() as uow:
        session = uow.session
        link = _link(session, inventory)
        equipment_id = link.equipment_id
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
            if payload.offset in (None, ""):
                raise HTTPException(422, "Укажите поправку станка")
            offset = _decimal(payload.offset, "Поправка станка")
            reason = f"{CASES[payload.case]}{measured}" + (f"; {note}" if note else "")
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
                                "deadline_ms": machine_control.deadline_ms(row, now)}}
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
                                                  after=payload.after, now=now)
            except rules.MachineWriteInvalid as error:
                raise HTTPException(422, str(error)) from error
            if recorded:
                text = commands.CONTROL_TEXTS[before.status][0]
                states = before.readback or {}
                _journal(uow, link.equipment_id, "command_finished", bridge.actor, now,
                         f"{before.summary}: {text}" + (f" — {before.error}" if before.error else ""),
                         command_id=str(before.id), command=before.command or "", status=before.status,
                         reason=payload.reason, detail=" ".join(payload.detail.split())[:300],
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
                                  readback=payload.readback, error=payload.error, now=now)
        except KeyError as error:
            raise HTTPException(404, str(error.args[0])) from error
        if was_taken:
            if row.status == "done":
                _journal(uow, link.equipment_id, "write_done", bridge.actor, now,
                         f"Записано и проверено: {row.summary}", command_id=str(row.id))
            else:
                _journal(uow, link.equipment_id, "write_failed", bridge.actor, now,
                         f"Не записано: {row.summary} — {row.error}", command_id=str(row.id))
        uow.commit()
        return {"status": row.status, "error": row.error}


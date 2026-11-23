"""HTTP: «Выполнение задания» on the ОБ.019 card (owner 19.11.2026, plan «Станки вне проекта», S2). Rules:
production/machine_runs.py; the write itself is the card's write queue (machine_writes_api.py), the start — the card's
«Пуск» (machine_control_api.py).

- `GET  /api/production/machines/card/{ОБ}/run` (view) → `{run, manual, link_ok, problem, can_edit, can_control}`: the
  machine's newest run with live progress (done of planned from T.Finish), the manual job running on it.
- `POST /api/production/machines/card/{ОБ}/run` (edit) `{kind: "cutting", group_id, length_mm, values?, confirm_state,
  expected_source, expected_active_run_id}` or `{kind: "manual", job_id, values?, confirm_state,
  expected_active_run_id}` → the queued write that starts a run. Cutting uses the source guard from the transfer
  preview; both kinds compare the active run UUID/null under the machine lock (409 → refresh and confirm again).
  Cutting without `values`
  writes the ETK plan (the same words as «Передать на станок»); with `values` — those (the drawing edited by hand)
  plus the ETK marking (422 «Маркировка: …» when it is blocked, nothing written).
  A manual job writes `values` plus its own order number «R-000001», product line and planned quantity.
- `POST /api/production/machines/card/{ОБ}/run/close` (edit) `{run_id}` for a cutting run → closed with the machine
  count (the hand-in in «Мои задания» is pre-filled with it, the operator confirms there); for a manual run also
  `{version, good, defect, defect_reason, material, material_note}` → the manual job is finished (`count_source` plc, or
  «Вручную» with a mark: `counter_reset` when the counter went back, `counter_unread` when it was not read). Refused
  while the machine cuts (103/104/106): «Стоп» first.
- `GET  /api/production/machines/runs/cutting/{group_id}` (view) → `{runs}`: closed machine runs of the group not handed
  in yet (no output of that length after the run closed), summed per length — «Сдать группу» pre-fills from them."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.modules.production import machine_runs as runs
from app.modules.production import machine_writes as commands
from app.modules.production import machines as rules
from app.modules.production import manual_jobs as manual
from app.modules.production.api import production_uow_factory
from app.modules.production.cutting_infrastructure import CuttingMemberRow
from app.modules.production.infrastructure import ProductionOutputRow
from app.modules.references.infrastructure import EquipmentMachineRow
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.http_auth import Principal

from .machine_writes_api import (_can_edit, _confirm, _enqueue, _journal, _link, can_control,
                                 confirm_active_run, confirmed_transfer_plan, ExpectedSource, locked_link,
                                 cutting_run, latin, machines_edit_principal, marking_writes, refuse_when_done,
                                 refuse_while_manual, settle_run, transfer_summary)
from .machines_api import Reader
from .manual_jobs_api import view as manual_view

router = APIRouter(prefix="/api/production/machines", tags=["production"])
Factory = Annotated[Callable, Depends(production_uow_factory)]
Now = Annotated[datetime, Depends(moscow_now)]
Writer = Annotated[Principal, Depends(machines_edit_principal)]


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunPayload(Payload):
    kind: Literal["cutting", "manual"]
    group_id: UUID | None = None
    length_mm: str = Field(default="", max_length=20)
    job_id: UUID | None = None
    values: dict[str, str | int | float] = Field(default_factory=dict, max_length=len(rules.OB019_FIELDS))
    confirm_state: int | None = None
    expected_source: ExpectedSource | None = None
    expected_active_run_id: UUID | None = None


class ClosePayload(Payload):
    run_id: UUID
    # A manual run finishes its manual job.
    version: int | None = Field(default=None, ge=1)
    good: int | None = Field(default=None, ge=0, le=manual.LIMIT)
    defect: int = Field(default=0, ge=0, le=manual.LIMIT)
    defect_reason: str = Field(default="", max_length=300)
    material: Literal["pending", "not_issued", "issued"] = "pending"
    material_note: str = Field(default="", max_length=300)


def _live(session, equipment_id: UUID, now: datetime) -> tuple[dict[int, int], bool, str]:
    snapshot = session.get(rules.MachineSnapshotRow, equipment_id)
    code = rules.link_status(snapshot, now)
    return rules.snapshot_words(snapshot), code == "ok", "" if code == "ok" else rules.LINK_TEXTS[code][0]


def _manual_view(session, job: manual.ManualJobRow | None) -> dict | None:
    return None if job is None else manual_view(job, session.get(EquipmentMachineRow, job.equipment_id))


@router.get("/card/{inventory}/run")
def run_state(inventory: str, principal: Reader, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        session = uow.session
        link = _link(session, inventory)
        commands.latest(session, link.equipment_id, now, limit=1)  # what timed out meanwhile shows as such
        settle_run(uow, link.equipment_id, now)
        words, link_ok, problem = _live(session, link.equipment_id, now)
        row = runs.current(session, link.equipment_id)
        result = {"run": runs.view(row, words, link_ok) if row is not None else None,
                  "expected_active_run_id": str(row.id) if runs.is_open(row) else None,
                  "manual": _manual_view(session, manual.running(session, link.equipment_id)),
                  "link_ok": link_ok, "problem": problem, "can_edit": _can_edit(principal),
                  "can_control": can_control(principal)}
        uow.commit()
    return result


def _whole(value: object) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


@router.post("/card/{inventory}/run")
def start_run(inventory: str, payload: RunPayload, principal: Writer, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        session = uow.session
        link = locked_link(session, inventory)
        _confirm(session, link, payload.confirm_state)
        job = (runs.cutting(payload.group_id, payload.length_mm, "", None) if payload.kind == "cutting"
               else runs.manual(payload.job_id, "", None))
        confirm_active_run(session, link.equipment_id, payload.expected_active_run_id, job)
        if payload.kind == "cutting":
            if payload.group_id is None or not payload.length_mm.strip():
                raise HTTPException(422, "Выберите задание резки")
            refuse_while_manual(session, link.equipment_id)
            plan = confirmed_transfer_plan(session, link, payload.group_id, payload.length_mm,
                                           payload.expected_source, now)
            if payload.values:
                # A size missing in the ETK comes with the values the first time (a re-send of the open run of this job,
                # e.g. «Тираж» changed, keeps what is on the machine); the ETK marking goes with them as without (a
                # blocked marking refuses the whole job, nothing is written). A marking value typed wins.
                typed = {field.label for key in payload.values if (field := rules.OB019_FIELDS.get(key)) is not None}
                last = runs.current(session, link.equipment_id)
                again = runs.is_open(last) and runs.same_job(runs.info(last), runs.cutting(payload.group_id,
                                                                                           payload.length_mm, "", None))
                # The open run of this job written again (e.g. «Тираж» only): its length, order and product stay as the
                # run wrote them (a length typed for a job the ETK lacked keeps its guard, the plan's is not written
                # back over the operator's) — unless typed now.
                kept = (runs.info(last) or {}).get("expect") if again else None
                missing = [] if again else [label for label in plan["blocking"] if label not in typed]
                if missing:
                    raise HTTPException(422, "В ЭТК не задано: " + ", ".join(missing))
                writes = [commands.FieldWrite(key, value) for key, value in payload.values.items()]
                # The job's length, order and product as the plan has them unless typed: what the run expects on the
                # machine before «Пуск» (production/machine_runs.start_blocker).
                identity = {row["key"]: row["value"] for row in plan["rows"] if row["key"] == "length_mm"} | {
                    key: plan["job"].get(key) for key in ("order_number", "product")}
                writes += [commands.FieldWrite(key, value) for key, value in identity.items()
                           if kept is None and key not in payload.values and value not in ("", None)]
                writes += [item for item in marking_writes(plan) if item.key not in payload.values]
                run = cutting_run({**plan, "quantity": _whole(payload.values.get("total_set")) or plan["quantity"]},
                                  payload.length_mm, payload.values)
                if kept is not None:
                    run["expect"] = {**kept, **runs.expect(payload.values)}
            else:
                if plan["blocking"]:
                    raise HTTPException(422, "В ЭТК не задано: " + ", ".join(plan["blocking"]))
                refuse_when_done(plan)
                writes = [commands.FieldWrite(row["key"], row["value"]) for row in plan["rows"] if row["value"] is not None]
                writes += [commands.FieldWrite(key, value) for key, value in plan["job"].items() if value not in ("", None)]
                writes += marking_writes(plan)
                run = cutting_run(plan, payload.length_mm)
            return _enqueue(uow, link, principal, "transfer", writes, transfer_summary(plan), now, run=run)
        job = session.get(manual.ManualJobRow, payload.job_id) if payload.job_id else None
        if job is None or job.equipment_id != link.equipment_id:
            raise HTTPException(404, "Ручная работа этого станка не найдена")
        if job.status != "running":
            raise HTTPException(409, f"Ручная работа {manual.number_text(job.number)} уже "
                                     f"{manual.STATUS_TEXTS[job.status][0].lower()}")
        values: dict[str, object] = dict(payload.values)
        if job.length_mm is not None:
            values.setdefault("length_mm", format(job.length_mm, "f"))
        if job.quantity_planned:
            # Written again after part of it was made: only the rest (the run keeps counting from its first start).
            done = runs.done_before(session, link.equipment_id, runs.manual(job.id, "", None), now)
            values.setdefault("total_set", max(job.quantity_planned - done, 0))
        values.setdefault("order_number", manual.plc_number(job.number))
        product = " ".join(part for part in (job.wire, f"L{format(job.length_mm.normalize(), 'f')}"
                                             if job.length_mm is not None else "") if part)
        if product:
            values.setdefault("product", latin(product))
        writes = [commands.FieldWrite(key, value) for key, value in values.items()]
        label = " · ".join(part for part in (manual.number_text(job.number), job.wire,
                                             f"{format(job.length_mm.normalize(), 'f').replace('.', ',')} мм"
                                             if job.length_mm is not None else "") if part)
        run = {**runs.manual(job.id, label, _whole(values.get("total_set"))), "expect": runs.expect(values)}
        return _enqueue(uow, link, principal, "transfer", writes, f"Передать на станок: ручная работа {label}", now,
                        run=run)


@router.post("/card/{inventory}/run/close")
def close_run(inventory: str, payload: ClosePayload, principal: Writer, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        session = uow.session
        link = locked_link(session, inventory)
        settle_run(uow, link.equipment_id, now)
        row = runs.current(session, link.equipment_id, for_update=True)  # a parallel close or re-send waits here
        if row is None or row.id != payload.run_id or not runs.is_open(row):
            raise HTTPException(409, "Это задание на станке уже завершено или сменилось — обновите карточку")
        if row.status in commands.OPEN:
            raise HTTPException(409, "Задание ещё записывается на станок — дождитесь результата")
        if runs.pending(row):
            raise HTTPException(409, "Запись задания проверяется по счётчику станка — повторите через секунду")
        if (runs.info(row) or {}).get("kind") == "manual" and (payload.version is None or payload.good is None):
            raise HTTPException(422, "Укажите годные и брак")
        words, link_ok, _problem = _live(session, link.equipment_id, now)
        if runs.cutting_now(words, link_ok):
            raise HTTPException(409, runs.CUTTING)
        run = runs.close(row, words, link_ok, now, principal.actor.name)
        count = run["machine_count"]
        counted = (f"по станку {count} шт" if count is not None else "счётчик станка сброшен — вручную" if run["reset"]
                   else "счётчик станка не прочитан — вручную")
        job = None
        if run["kind"] == "manual":
            result = manual.Finish(payload.good, payload.defect, payload.defect_reason, payload.material,
                                   payload.material_note, machine_count=count)
            try:
                job = manual.finish(session, UUID(run["job_id"]), payload.version, result,
                                    actor_name=principal.actor.name, now=now, counter_reset=run["reset"],
                                    counter_unread=run["unread"])
            except KeyError as error:
                raise HTTPException(404, str(error.args[0])) from error
            except manual.ManualJobConflict as error:
                raise HTTPException(409, str(error)) from error
            except manual.ManualJobInvalid as error:
                raise HTTPException(422, str(error)) from error
            defect = f", брак {job.defect_quantity} ({job.defect_reason})" if job.defect_quantity else ""
            uow.audit.add(AuditEvent(
                id=uuid4(), aggregate_id=job.id, event_type="production.manual_job_finished", actor=principal.actor,
                occurred_at=now, details={"text": f"Завершена {manual.number_text(job.number)} с карточки станка: "
                                                  f"годных {job.good_quantity}{defect}; {counted}",
                                          "number": manual.number_text(job.number),
                                          "equipment_id": str(job.equipment_id)}))
        _journal(uow, link.equipment_id, "run_closed", principal.actor, now,
                 f"Задание завершено: {run.get('label', '')} — {counted}", command_id=str(row.id))
        result = {"run": runs.view(row, words, link_ok), "manual": _manual_view(session, job)}
        uow.commit()
    return result


@router.get("/runs/cutting/{group_id}")
def cutting_runs(group_id: UUID, _principal: Reader, factory: Factory) -> dict:
    """Machine runs of a cutting group closed after the last hand-in of their cut length — summed per length (a job
    cut in several runs, by one machine or more, is handed in at once)."""
    with factory() as uow:
        session = uow.session
        members = list(session.scalars(select(CuttingMemberRow).where(CuttingMemberRow.group_id == group_id)))
        operations: dict[str, list[UUID]] = {}
        for member in members:
            operations.setdefault(runs.length_key((member.row_snapshot or {}).get("cut_length_mm")), []) \
                .append(member.operation_id)
        handed: dict[str, datetime] = {}
        for length, ids in operations.items():
            last = session.scalar(select(func.max(ProductionOutputRow.finished_at))
                                  .where(ProductionOutputRow.operation_id.in_(ids)))
            if last is not None:
                handed[length] = last
        links = {link.equipment_id: link.inventory_number for link in session.scalars(select(rules.MachineLinkRow))}
        found: dict[str, dict] = {}
        for row in session.scalars(select(commands.MachineWriteRow).where(commands.MachineWriteRow.purpose == "transfer")
                                   .order_by(commands.MachineWriteRow.created_at.desc()).limit(500)):
            run = runs.info(row)
            if (run is None or run["kind"] != "cutting" or run.get("group_id") != str(group_id)
                    or not run.get("closed_at") or run.get("continued") or run.get("check") in runs.VOID_CHECKS):
                continue
            closed = datetime.fromisoformat(run["closed_at"])
            last = handed.get(run["length_mm"])
            if last is not None and (last.replace(tzinfo=closed.tzinfo) if last.tzinfo is None else last) >= closed:
                continue  # handed in after this run closed
            entry = found.setdefault(run["length_mm"], {
                "run_id": str(row.id), "inventory": "", "length_mm": run["length_mm"], "count": None, "runs": 0,
                "unknown": False, "closed_at": run["closed_at"], "closed_by": run.get("closed_by", "")})
            entry["runs"] += 1
            inventory = links.get(row.equipment_id, "")
            if inventory and inventory not in entry["inventory"].split(", "):
                entry["inventory"] = ", ".join(part for part in (entry["inventory"], inventory) if part)
            if run.get("machine_count") is None:
                entry["unknown"] = True
            else:
                entry["count"] = (entry["count"] or 0) + run["machine_count"]
    return {"runs": list(found.values())}

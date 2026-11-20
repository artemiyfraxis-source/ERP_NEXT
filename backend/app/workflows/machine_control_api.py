"""HTTP: ОБ.019 machine commands from the card (plan «Станки», slice 3). Rules: production/machine_control.py.

- `POST /api/production/machines/card/{ОБ}/command` `{command, confirm_state?, hands_clear?}` → the queued command
  (the same view as the card's writes in `GET …/control`, which also says `can_control`).
  `stop` — the card's gate only (Производство or Задания, view), no confirmation. `reset` / `start` / `alarm_clear` —
  also «Управление станками» on edit (403), `hands_clear: true` and the D766 state the card saw (409 when it changed,
  when the state does not allow the command — Пуск with the feed rollers lifted included — or another command is open).
  Since 19.11.2026 the card sends `hands_clear` with every press, no dialog: the journal records it as «одним нажатием
  с карточки» (`auto_confirm`), not as a person's confirmation; the field name stays for the API contract. `start`
  only for an open job run whose «Тираж» is on the machine (production/machine_runs.py `start_blocker`, 409 otherwise).
  `hmi_close` / `hmi_open` («Закрыть / Открыть пульт на ПК», 20.11.2026) — «Управление станками» on edit, `hands_clear`
  (the card asked its own confirmation), no state to confirm: with the panel open ERP reads no state; `force: true`
  (hmi_close only, 422 otherwise) — «Закрыть принудительно». The refusal rule is production/machine_control.py
  `hmi_problem` (409 with the reason, e.g. «Станок в цикле — …»).
The runner on the machine PC takes them through `…/agent/writes/next` (workflows/machine_writes_api.py)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.modules.production import machine_control
from app.modules.production import machine_runs as runs
from app.modules.production import machine_writes as commands
from app.modules.production import machines as rules
from app.modules.production.api import production_uow_factory
from app.shared.clock import moscow_now

from .machine_writes_api import _closed, _journal, _link, can_control, settle_run
from .machines_api import Commander

router = APIRouter(prefix="/api/production/machines", tags=["production"])
Factory = Annotated[Callable, Depends(production_uow_factory)]
Now = Annotated[datetime, Depends(moscow_now)]


class CommandPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: Literal["stop", "reset", "start", "alarm_clear", "hmi_close", "hmi_open"]
    confirm_state: int | None = Field(default=None, ge=0, le=rules.WORD_MAX)
    hands_clear: bool = False
    force: bool = False


@router.post("/card/{inventory}/command")
def send_command(inventory: str, payload: CommandPayload, principal: Commander, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        link = _link(uow.session, inventory)
        if payload.command == "start" and can_control(principal):  # without the right: 403 from the request below
            # «Пуск» from ERP only for an open checked run whose target D22 is on the machine (production/machine_runs).
            settle_run(uow, link.equipment_id, now)
            blocker = runs.start_blocker(uow.session, link.equipment_id, now)
            if blocker:
                raise HTTPException(409, blocker)
        try:
            row, closed = machine_control.request(
                uow.session, equipment_id=link.equipment_id, command=payload.command, actor_id=principal.actor.id,
                actor_name=principal.actor.name, can_control=can_control(principal), state_seen=payload.confirm_state,
                hands_clear=payload.hands_clear, now=now, force=payload.force)
        except machine_control.CommandRefused as error:
            uow.commit()  # what expired meanwhile stays expired
            raise HTTPException(error.status, str(error)) from error
        _closed(uow, closed, principal.actor, now)
        state = row.payload.get("state_seen")
        state_text = rules.OB019_STATES.get(state, (f"код {state}", ""))[0] if state is not None else "—"
        how = ", одним нажатием с карточки" if payload.hands_clear else ""
        if row.command in machine_control.HMI_COMMANDS:
            how = ", подтверждено на карточке"
            if state is not None and rules.link_status(
                    uow.session.get(rules.MachineSnapshotRow, link.equipment_id), now) != "ok":
                how = " (до открытия пульта)" + how
        _journal(uow, link.equipment_id, "command_requested", principal.actor, now,
                 f"{row.summary}: станок «{state_text}»" + how,
                 command_id=str(row.id), command=row.command or "", state_seen="" if state is None else str(state),
                 auto_confirm="1" if payload.hands_clear and row.command not in machine_control.HMI_COMMANDS else "",
                 force="1" if row.payload.get("force") else "")
        view = commands.command_view(row)
        uow.commit()
    return view
